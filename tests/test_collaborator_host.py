"""The Host (② Stage A): one presence that owns loop+propose+view+ledger on a serial
worker thread. Auto-records, tracks a task lifecycle, resumes held actions, fires an
idle proposal — and stays consistent under a concurrent reader. All deterministic via
ScriptedClient; every wait is bounded."""

import json
import tempfile
import threading
import time
import unittest

from collaborator.governance import DENIED, HELD, RAN
from collaborator.host import (
    AWAITING_APPROVAL,
    CANCELLED,
    DONE,
    FAILED,
    PAUSED,
    RUNNING,
    Collaborator,
)
from collaborator.model_client import ScriptedClient
from collaborator.session import Session


def _call(name, args):
    return {"content": None, "tool_calls": [
        {"id": "1", "function": {"name": name, "arguments": json.dumps(args)}}]}


def _propose_msg(name, args, *, confidence=0.9, rationale="useful"):
    return {"content": json.dumps({"propose": True, "confidence": confidence,
                                   "rationale": rationale,
                                   "action": {"name": name, "arguments": args}})}


def _wait(host, task_id, states, timeout=5.0):
    """Poll until the task is in one of `states` (a set), or fail. Bounded."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = host.get_task(task_id)
        if last and last["state"] in states:
            return last
        time.sleep(0.01)
    raise AssertionError(f"task {task_id} never reached {states}; last={last}")


class HostLifecycle(unittest.TestCase):
    def _host(self, caps=("fs.read:project", "fs.write:project", "shell.exec"), **kw):
        self.tmp = tempfile.mkdtemp()
        s = Session(workspace=self.tmp, capabilities=caps)
        h = Collaborator(s, kw.pop("doer", None) or ScriptedClient([]), **kw).start()
        self.addCleanup(h.stop)
        return h, s

    def test_submit_runs_to_done_and_records(self):
        from pathlib import Path
        doer = ScriptedClient([_call("write_file", {"path": "o.txt", "content": "hi"}),
                               {"content": "all done"}])
        h, _ = self._host(doer=doer)
        tid = h.submit("write o.txt")
        t = _wait(h, tid, {DONE})
        self.assertEqual(t["decisions"], 1)
        self.assertEqual((Path(self.tmp) / "o.txt").read_text(), "hi")
        self.assertEqual(h.snapshot()["counts"]["ran"], 1)

    def test_held_then_approve_resumes_to_done(self):
        # run_command defaults to propose_first -> HELD; approve -> worker runs it -> resume.
        doer = ScriptedClient([_call("run_command", {"command": ["echo", "hi"]}),
                               {"content": "finished after approval"}])
        h, _ = self._host(doer=doer)
        tid = h.submit("run echo")
        t = _wait(h, tid, {AWAITING_APPROVAL})
        self.assertEqual(len(t["held"]), 1)
        self.assertTrue(h.approve(tid))
        t = _wait(h, tid, {DONE})
        # the held action ran on approval and was recorded
        self.assertEqual(h.snapshot()["counts"]["ran"], 1)

    def test_approve_denied_stays_awaiting(self):
        doer = ScriptedClient([_call("run_command", {"command": ["echo", "hi"]}),
                               {"content": "done"}])
        h, s = self._host(doer=doer)
        tid = h.submit("run echo")
        _wait(h, tid, {AWAITING_APPROVAL})
        s.capabilities = ()  # revoke shell.exec between hold and approve (TOCTOU)
        self.assertTrue(h.approve(tid))
        # approval re-gated -> DENIED -> nothing ran -> stays awaiting (re-approvable)
        t = _wait(h, tid, {AWAITING_APPROVAL})
        self.assertEqual(t["state"], AWAITING_APPROVAL)
        self.assertTrue(any(d.status == DENIED for d in h._tasks[tid].decisions))

    def test_pause_holds_next_action_then_resume_runs(self):
        from pathlib import Path
        # The write is scripted twice: paused on the first attempt, re-issued after resume
        # (a real model, told "resumed, continue," re-emits the step the pause blocked).
        doer = ScriptedClient([_call("write_file", {"path": "p.txt", "content": "x"}),
                               _call("write_file", {"path": "p.txt", "content": "x"}),
                               {"content": "done"}])
        h, _ = self._host(doer=doer)
        h.pause()
        tid = h.submit("write p.txt")
        t = _wait(h, tid, {PAUSED})
        self.assertFalse((Path(self.tmp) / "p.txt").exists())  # nothing ran while paused
        h.resume()
        _wait(h, tid, {DONE})
        self.assertEqual((Path(self.tmp) / "p.txt").read_text(), "x")

    def test_decline_cancels_a_held_task(self):
        doer = ScriptedClient([_call("run_command", {"command": ["echo", "hi"]}), {"content": "d"}])
        h, _ = self._host(doer=doer)
        tid = h.submit("run echo")
        _wait(h, tid, {AWAITING_APPROVAL})
        self.assertTrue(h.decline(tid))
        self.assertEqual(h.get_task(tid)["state"], CANCELLED)

    def test_partial_deny_keeps_the_denied_held_action_retryable(self):
        # panel grok F1: a turn holding TWO actions where one is later DENIED must NOT silently
        # drop the denied one — it stays in `held`, re-approvable; the approvable one still runs.
        from pathlib import Path
        from collaborator.tools import PROPOSE_FIRST
        both = {"content": None, "tool_calls": [
            {"id": "a", "function": {"name": "write_file",
                                     "arguments": json.dumps({"path": "w.txt", "content": "x"})}},
            {"id": "b", "function": {"name": "run_command",
                                     "arguments": json.dumps({"command": ["echo", "hi"]})}}]}
        doer = ScriptedClient([both, {"content": "done"}])
        h, s = self._host(doer=doer)
        h.set_leash("write_file", PROPOSE_FIRST)  # so the write HOLDS too
        tid = h.submit("write and run")
        t = _wait(h, tid, {AWAITING_APPROVAL})
        self.assertEqual(len(t["held"]), 2)
        s.capabilities = ("fs.write:project",)  # revoke shell.exec -> run_command will DENY
        h.approve(tid)
        # write ran; run_command denied but NOT dropped -> stays awaiting + re-approvable
        t = _wait(h, tid, {AWAITING_APPROVAL})
        self.assertEqual((Path(self.tmp) / "w.txt").read_text(), "x")
        held = h._tasks[tid].held
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0].tool, "run_command")

    def test_should_not_propose_while_busy_or_running(self):
        # refutes "ProposeJob can run while a turn is mid-flight": the trigger is gated.
        h, s = self._host(caps=("fs.write:project",))
        s.proactivity = "eager"
        h._idle_seconds = 0.0
        h._propose_cooldown = 0.0
        with h._lock:
            self.assertTrue(h._should_propose())          # idle + eager -> would fire
            h._worker_busy = True
            self.assertFalse(h._should_propose())          # ...but not while the worker is busy
            h._worker_busy = False
            h._tasks["x"] = type("T", (), {"state": RUNNING})()
            self.assertFalse(h._should_propose())          # ...nor while a task is RUNNING

    def test_empty_completion_task_is_FAILED_not_done(self):
        # a persistently empty model -> stopped="empty" -> honest FAILED, never a fake DONE
        doer = ScriptedClient([{"content": "", "tool_calls": None}] * 6)
        h, _ = self._host(doer=doer)
        tid = h.submit("do something")
        t = _wait(h, tid, {FAILED})
        self.assertEqual(t["state"], FAILED)


class HostProposeTrigger(unittest.TestCase):
    def test_idle_trigger_surfaces_a_proposal(self):
        tmp = tempfile.mkdtemp()
        s = Session(workspace=tmp, capabilities=("fs.write:project",), proactivity="eager")
        proposer = ScriptedClient([_propose_msg("write_file", {"path": "idea.txt", "content": "hi"})])
        h = Collaborator(s, ScriptedClient([]), proposer_client=proposer,
                         idle_seconds=0.0, propose_cooldown=0.0, tick_seconds=0.02).start()
        self.addCleanup(h.stop)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not h.snapshot()["proposals"]:
            time.sleep(0.02)
        self.assertTrue(h.snapshot()["proposals"], "no proposal surfaced on idle")

    def test_proactivity_off_surfaces_nothing(self):
        tmp = tempfile.mkdtemp()
        s = Session(workspace=tmp, capabilities=("fs.write:project",), proactivity="off")
        proposer = ScriptedClient([_propose_msg("write_file", {"path": "idea.txt", "content": "hi"})])
        h = Collaborator(s, ScriptedClient([]), proposer_client=proposer,
                         idle_seconds=0.0, propose_cooldown=0.0, tick_seconds=0.02).start()
        self.addCleanup(h.stop)
        time.sleep(0.3)  # give the ticker several chances to (not) fire
        self.assertEqual(h.snapshot()["proposals"], [])


class HostControls(unittest.TestCase):
    def test_veto_proposal(self):
        from collaborator.propose import PROPOSED, VETOED
        tmp = tempfile.mkdtemp()
        s = Session(workspace=tmp, capabilities=("fs.write:project",), proactivity="eager")
        proposer = ScriptedClient([_propose_msg("write_file", {"path": "i.txt", "content": "x"})])
        h = Collaborator(s, ScriptedClient([]), proposer_client=proposer,
                         idle_seconds=0.0, propose_cooldown=0.0, tick_seconds=0.02).start()
        self.addCleanup(h.stop)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not h._proposals:
            time.sleep(0.02)
        pid = next(iter(h._proposals))
        self.assertTrue(h.veto(pid))
        self.assertEqual(h._proposals[pid].status, VETOED)

    def test_set_leash_reflects_in_snapshot(self):
        from collaborator.tools import NOTIFY_ONLY
        tmp = tempfile.mkdtemp()
        s = Session(workspace=tmp, capabilities=("fs.write:project",))
        h = Collaborator(s, ScriptedClient([])).start()
        self.addCleanup(h.stop)
        self.assertTrue(h.set_leash("write_file", NOTIFY_ONLY))
        self.assertEqual(h.snapshot()["leashes"]["write_file"], NOTIFY_ONLY)


class HostConcurrency(unittest.TestCase):
    def test_snapshot_never_crashes_while_a_turn_runs(self):
        # A slow client so a turn is in flight while we hammer snapshot() from another thread.
        class Slow(ScriptedClient):
            def complete(self, messages, tools=None, temperature=None, max_tokens=None):
                time.sleep(0.005)
                return super().complete(messages, tools=tools, temperature=temperature,
                                        max_tokens=max_tokens)
        tmp = tempfile.mkdtemp()
        s = Session(workspace=tmp, capabilities=("fs.write:project",))
        seq = []
        for i in range(3):  # < max_iterations (6) so the turn reaches its final answer -> DONE
            seq += [_call("write_file", {"path": f"f{i}.txt", "content": "x"})]
        seq += [{"content": "done"}]
        h = Collaborator(s, Slow(seq)).start()
        self.addCleanup(h.stop)

        errors = []
        stop = threading.Event()

        def hammer():
            while not stop.is_set():
                try:
                    snap = h.snapshot()
                    # counts must be internally consistent: ran never exceeds governed
                    self.assertLessEqual(snap["counts"]["ran"], snap["counts"]["governed"])
                except Exception as e:  # noqa: BLE001
                    errors.append(e)
                    return

        # daemon readers + stop-in-finally so a failed wait can never hang the process
        readers = [threading.Thread(target=hammer, daemon=True) for _ in range(4)]
        for r in readers:
            r.start()
        try:
            tid = h.submit("write three files")
            _wait(h, tid, {DONE}, timeout=10.0)
        finally:
            stop.set()
            for r in readers:
                r.join(timeout=2.0)
        self.assertEqual(errors, [], f"snapshot() crashed under concurrency: {errors[:1]}")
        self.assertEqual(h.snapshot()["counts"]["ran"], 3)


if __name__ == "__main__":
    unittest.main()
