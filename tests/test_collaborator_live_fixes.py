"""Regressions found by the ④ live task-scale run (a real model driving the loop
through a multi-step job), guarding two bugs that single-action unit fixtures and
Linux-only CI both missed:

1. NEWLINE / EXACT-BYTES — the mutating write must put on disk exactly the UTF-8
   bytes we hashed. The old write_text path translated "\\n"->"\\r\\n" on Windows,
   so the disk bytes diverged from the artifact hash and EVERY multi-line write
   false-failed verification (invisible on Linux CI). The invariant: a governed
   multi-line write clears, and the file is byte-for-byte the content.

2. PAUSE-ON-HELD — a propose-first action means "wait for my yes." The loop must
   PAUSE and hand the held action back, not call the model again — which just spins
   it re-proposing the same call until max_iterations (observed live: 6 wasted
   iterations before this fix).
"""

import tempfile
import unittest
from pathlib import Path

from collaborator.governance import HELD, RAN, govern_action
from collaborator.loop import run_turn
from collaborator.model_client import ScriptedClient
from collaborator.session import Session
from collaborator.tools import _exec_write
from collaborator.toolcall import ToolIntent
from salienceos.verifier.signing import sha256_bytes

_MULTILINE = "def f():\n    return 42\n\nprint(f())\n"  # has \n — the trigger


class ExactBytesWrite(unittest.TestCase):
    def test_tool_writes_exact_utf8_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex = _exec_write(tmp, {"path": "m.py", "content": _MULTILINE})
            disk = (Path(tmp) / "m.py").read_bytes()
            self.assertEqual(disk, _MULTILINE.encode("utf-8"))  # no newline translation
            # the claimed artifact hash matches the real disk bytes on every platform
            self.assertEqual(ex.artifact_hashes["m.py"], sha256_bytes(disk))

    def test_multiline_write_clears_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            d = govern_action(
                s, ToolIntent("write_file", {"path": "m.py", "content": _MULTILINE}, "structured"))
            self.assertEqual(d.status, RAN)
            self.assertTrue(d.cleared, "a multi-line write must clear the artifact check")
            self.assertEqual((Path(tmp) / "m.py").read_bytes(), _MULTILINE.encode("utf-8"))


def _cmd_msg():
    # a well-formed run_command call (structured shape) the parser will run
    return {"content": None,
            "tool_calls": [{"name": "run_command", "arguments": {"command": ["echo", "hi"]}}]}


class PauseOnHeld(unittest.TestCase):
    def test_loop_pauses_on_held_and_does_not_spin(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp,
                        capabilities=("fs.read:project", "fs.write:project", "shell.exec"))
            # queue TWO commands: if the loop kept going it would consume the second.
            client = ScriptedClient([_cmd_msg(), _cmd_msg()])
            res = run_turn(s, client, "run it", max_iterations=6)
            self.assertEqual(res.stopped, "held")
            self.assertEqual(len(res.decisions), 1)
            self.assertEqual(res.decisions[0].status, HELD)
            # the model was called exactly once — the loop did NOT spin re-proposing.
            self.assertEqual(len(client.seen), 1)

    def test_act_then_report_write_does_not_pause(self):
        # a non-held action must not trip the pause: the loop runs it and continues
        # to the model's next (final) message.
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            write = {"content": None,
                     "tool_calls": [{"name": "write_file",
                                     "arguments": {"path": "a.txt", "content": "hi"}}]}
            done = {"content": "all done.", "tool_calls": None}
            res = run_turn(s, ScriptedClient([write, done]), "make a file", max_iterations=6)
            self.assertEqual(res.stopped, "final")
            self.assertEqual(res.decisions[0].status, RAN)
            self.assertEqual(res.reply, "all done.")


if __name__ == "__main__":
    unittest.main()
