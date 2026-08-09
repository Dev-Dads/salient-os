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


if __name__ == "__main__":
    unittest.main()
