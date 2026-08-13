"""The loop we own: parses intents, governs each, and feeds HONEST results back —
the model's narration never stands in for what actually happened."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collaborator.governance import DENIED, HELD, RAN
from collaborator.loop import run_turn
from collaborator.model_client import ScriptedClient
from collaborator.policycaps import mint, workspace_subject
from collaborator.session import Session


def _call(name, args):
    return {"content": None, "tool_calls": [
        {"id": "1", "function": {"name": name, "arguments": json.dumps(args)}}]}


class LoopBasics(unittest.TestCase):
    def test_write_then_final_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            client = ScriptedClient([
                _call("write_file", {"path": "out.txt", "content": "hi"}),
                {"content": "All done — wrote the file."},
            ])
            r = run_turn(s, client, "please write out.txt")
            self.assertEqual(len(r.decisions), 1)
            self.assertEqual(r.decisions[0].status, RAN)
            self.assertEqual((Path(tmp) / "out.txt").read_text(), "hi")
            self.assertEqual(r.reply, "All done — wrote the file.")

    def test_content_embedded_call_executes(self):
        # The box gap: model emits the call as plain text; the loop still runs it.
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            client = ScriptedClient([
                {"content": '<tool_call>{"name":"write_file","arguments":{"path":"o.txt","content":"z"}}</tool_call>'},
                {"content": "done"},
            ])
            r = run_turn(s, client, "write it")
            self.assertEqual(r.decisions[0].status, RAN)
            self.assertEqual((Path(tmp) / "o.txt").read_text(), "z")


class HonestFeedback(unittest.TestCase):
    def test_denied_action_fed_back_as_denied_not_success(self):
        # Model requests shell (not granted) and NARRATES success; the loop must feed
        # the authoritative DENIED result back, not the model's claim.
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)  # no shell.exec
            client = ScriptedClient([
                _call("run_command", {"command": ["echo", "hi"]}),
                {"content": "I ran the command successfully!"},  # a lie
            ])
            r = run_turn(s, client, "run echo")
            self.assertEqual(r.decisions[0].status, DENIED)
            fed = [m for m in r.history if m["role"] == "user" and "TOOL RESULTS" in m["content"]]
            self.assertTrue(fed)
            self.assertIn("DENIED", fed[-1]["content"])
            self.assertIn("authoritative", fed[-1]["content"])


class Bounds(unittest.TestCase):
    def test_max_iterations_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            # A model that always asks for another tool call.
            always = ScriptedClient([_call("write_file", {"path": f"f{i}.txt", "content": "x"})
                                     for i in range(50)])
            r = run_turn(s, always, "loop forever", max_iterations=3)
            self.assertEqual(r.stopped, "max_iterations")
            self.assertEqual(len(r.decisions), 3)

    def test_ambiguous_is_surfaced_not_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            client = ScriptedClient([
                {"tool_calls": [{"function": {"name": "write_file", "arguments": "{bad json"}}]},
                {"content": "ok"},
            ])
            r = run_turn(s, client, "do it")
            self.assertEqual(r.decisions, [])       # nothing governed/run
            self.assertTrue(r.ambiguous)            # surfaced instead


class NetPostF1ModelCannotSelfOriginate(unittest.TestCase):
    """red-team F1 through the REAL loop: a model-emitted net_post — even with the FULL autonomous
    grant (signed net.post.auto + net_post act_then_report leash-cap + a live credential) — is
    HELD, because run_turn never passes the keyword leash that emit() carries. Only the host entry
    point (emit) can direct an autonomous emission; the model can never self-originate one."""

    def test_model_emitted_net_post_with_full_auto_grant_is_held(self):
        def _no_post(url, body, **kw):
            raise AssertionError("egress.post must NOT be reached — a model emission is always gated")

        with tempfile.TemporaryDirectory() as tmp:
            signed = mint(("net.post:api.example", "net.post.auto:api.example"),
                          {"net_post": "act_then_report"}, "admin", workspace_subject(tmp), b"k")
            s = Session(workspace=tmp, policy_caps=signed, caps_key=b"k")
            s.egress_credentials = {"api.example": "Bearer sk-live"}
            client = ScriptedClient([
                _call("net_post", {"url": "https://api.example/v1/x", "body": "secrets"}),
                {"content": "done"},
            ])
            with mock.patch("collaborator.egress.post", _no_post):
                r = run_turn(s, client, "post this")
            np = [d for d in r.decisions if d.tool == "net_post"]
            self.assertEqual(len(np), 1)
            self.assertEqual(np[0].status, HELD)
            self.assertEqual(np[0].leash, "propose_first")
            self.assertEqual(r.stopped, "held")


EMPTY = {"content": "", "tool_calls": None}  # a reasoning-only completion: no content, no call


class _EmptyAtLowTempClient:
    """Emulates the live failure mode: a DETERMINISTIC empty streak at greedy temperature
    (a same-temp retry never escapes it), which breaks only when the temperature is raised.
    Acts once called at temperature >= 0.5, then answers plainly."""

    def __init__(self, act_msg) -> None:
        self._act = act_msg
        self._acted = False
        self.temps: list = []

    def complete(self, messages, tools=None, temperature=None) -> dict:
        self.temps.append(temperature)
        if self._acted:
            return {"content": "done"}
        if temperature is not None and temperature >= 0.5:
            self._acted = True
            return self._act
        return {"content": "", "tool_calls": None}  # empty at greedy / default temp


class EmptyCompletionIsNotDone(unittest.TestCase):
    """Live-found (gpt-oss:120b, 2026-08-13): the model intermittently ends a turn after
    only its private reasoning channel — empty content, no tool call, finish_reason=stop —
    DETERMINISTICALLY at greedy temperature. The loop must treat that as SILENCE (retry with
    an escalating temperature to escape the streak), never as a finished 'final' turn."""

    def test_retries_past_empty_then_acts(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            client = ScriptedClient([
                EMPTY, EMPTY,                                   # two reasoning-only no-ops
                _call("write_file", {"path": "out.txt", "content": "hi"}),  # then it acts
                {"content": "done"},
            ])
            r = run_turn(s, client, "write out.txt")
            self.assertEqual(len(r.decisions), 1)
            self.assertEqual(r.decisions[0].status, RAN)
            self.assertEqual((Path(tmp) / "out.txt").read_text(), "hi")
            self.assertEqual(r.stopped, "final")
            # loop hygiene: the discarded empties are NEVER appended as blank assistant turns
            blanks = [m for m in r.history
                      if m.get("role") == "assistant" and not (m.get("content") or "").strip()]
            self.assertEqual(blanks, [])
            # the first attempt uses the client's own temperature; retries escalate it
            self.assertIsNone(client.temps[0])
            self.assertGreaterEqual(client.temps[1], 0.5)

    def test_retry_escalates_temperature_to_escape_a_deterministic_empty_streak(self):
        # A plain same-temperature retry would loop forever here; only the raised temperature
        # gets an action out of the model.
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            client = _EmptyAtLowTempClient(_call("write_file", {"path": "o.txt", "content": "x"}))
            r = run_turn(s, client, "write o.txt")
            self.assertEqual(r.stopped, "final")
            self.assertEqual((Path(tmp) / "o.txt").read_text(), "x")
            self.assertIsNone(client.temps[0])                 # first shot: model's own temp
            self.assertTrue(any(t is not None and t >= 0.5 for t in client.temps))  # escalated

    def test_all_empty_surfaces_error_never_silent_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            client = ScriptedClient([EMPTY, EMPTY, EMPTY])     # empty through the whole budget
            r = run_turn(s, client, "do something", empty_retries=2)   # 3 attempts
            self.assertEqual(r.stopped, "empty")               # NOT "final"
            self.assertEqual(r.decisions, [])
            self.assertFalse((Path(tmp) / "out.txt").exists())
            self.assertIn("empty response", r.reply)           # honest, not a fake success
            self.assertEqual(len(client.seen), 3)              # exactly the retry budget, no more

    def test_legit_final_answer_is_not_retried(self):
        # A real answer with no tool call is a valid, immediate 'final' — never re-rolled.
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            client = ScriptedClient([{"content": "The answer is 42."}])
            r = run_turn(s, client, "what is the answer?")
            self.assertEqual(r.stopped, "final")
            self.assertEqual(r.reply, "The answer is 42.")
            self.assertEqual(len(client.seen), 1)              # one call, no wasted retries
            self.assertEqual(client.temps, [None])             # used the client's own temperature

    def test_empty_retries_zero_is_single_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            client = ScriptedClient([EMPTY])
            r = run_turn(s, client, "do something", empty_retries=0)
            self.assertEqual(r.stopped, "empty")
            self.assertEqual(len(client.seen), 1)


TRUNC = {"content": "", "tool_calls": None, "finish_reason": "length"}  # output hit the cap


class TruncatedTurnGrowsBudgetAndRetries(unittest.TestCase):
    """A large tool call clipped at the token cap (finish_reason == "length") must not be
    accepted as-is: the loop grows max_tokens and retries so the call can complete."""

    def test_truncation_retries_with_a_larger_budget_then_acts(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            client = ScriptedClient([
                TRUNC,                                                       # clipped
                _call("write_file", {"path": "big.py", "content": "ok"}),   # completes on retry
                {"content": "done"},
            ])
            r = run_turn(s, client, "write a big file")
            self.assertEqual(len(r.decisions), 1)
            self.assertEqual(r.decisions[0].status, RAN)
            self.assertEqual((Path(tmp) / "big.py").read_text(), "ok")
            self.assertEqual(r.stopped, "final")
            # the truncation retry asked for a LARGER budget than the first (default) call
            self.assertIsNone(client.max_tokens_seen[0])                    # first: client default
            self.assertIsNotNone(client.max_tokens_seen[1])                 # retry: an explicit, grown cap
            self.assertGreater(client.max_tokens_seen[1], 16384)

    def test_persistent_truncation_surfaces_ambiguous_never_silently_lost(self):
        # every attempt clips a <tool_call> mid-JSON; it must be surfaced, not vanish
        clipped = {"content": '<tool_call>{"name":"write_file","arguments":{"path":"a","content":"aaaa',
                   "tool_calls": None, "finish_reason": "length"}
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            client = ScriptedClient([clipped, clipped, clipped, clipped])
            r = run_turn(s, client, "write a huge file", empty_retries=3)
            self.assertEqual(r.decisions, [])              # nothing ran (it never completed)...
            self.assertTrue(r.ambiguous)                   # ...but the clipped call was SURFACED
            self.assertFalse((Path(tmp) / "a").exists())   # and no partial write happened


if __name__ == "__main__":
    unittest.main()
