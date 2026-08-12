"""ADR 0003 residual sweep — F2 shared-workspace dropper: autonomy-authorship PROVENANCE.

The workspace is SHARED read-write between the autonomy-earning CONTAINED run and the UNCONTAINED
human maintenance run (`cwd=workspace`, full FS reach by design). So a NOT-human-approved
(autonomous) action can DROP an executable file a human later approves and runs uncontained
(`sh ./build.sh`) — contained.py:31-38 named this open axis. We do NOT fence it (the human keeps
full reach on purpose); we FLAG it: an autonomous write_file / contained run_command that authors a
workspace file is recorded, and when a human run_command references such a file the seam surfaces a
⚠ in the approval preview + an audit tag on the Decision. ADVISORY only — never a deny; a
human-approved (re-)write CLEARS the taint. Porous by construction (argv-token match).

Pins: the pure recognizer + normalizer; the session manifest note/clear; autonomous write_file AND
contained run_command recording; the human-approval clear; the HELD-preview surfacing (flagged, NOT
denied); the human maintenance run is deliberately NOT tracked.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from salienceos.verifier.observers import observed_write_set, snapshot_tree

from collaborator import provenance
from collaborator.contained import SHELL_CONTAINED_AUTONOMY_CAP
from collaborator.contained import _CODEFENCE_VERIFIED_SENTINEL as _TOKEN
from collaborator.governance import HELD, RAN, govern_action
from collaborator.loop import approve
from collaborator.policycaps import mint, workspace_subject
from collaborator.session import Session
from collaborator.toolcall import ToolIntent
from collaborator.tools import ACT_THEN_REPORT, PROPOSE_FIRST

_CAPS = ("fs.read:project", "fs.write:project", "shell.exec")
# Stand the F-6 code-protection floor down (patched True) so an autonomous run_command CAN earn
# autonomy in these cross-platform tests; the signed contained-autonomy cap is granted separately.
_CODE_UP = patch("collaborator.governance.code_protection_available", return_value=True)


def _contained_dropper(rel, content="#!/bin/sh\necho pwn\n"):
    """A wrap_contained side-effect that DROPS `rel` into the workspace, then emits the contained
    guard's POSITIVE proof token (so the executor whitelists code_protected exactly as the real
    guard would). Returns (run_argv, isolated=unshare_net, protected=True)."""
    def _side(argv, workspace, *, roots_with_witness=None, unshare_net=True):
        target = str(Path(workspace) / rel)
        script = ("import sys,pathlib;"
                  "p=pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True);"
                  "p.write_text(sys.argv[2]); sys.stderr.write(sys.argv[3] + chr(10))")
        return [sys.executable, "-c", script, target, content, _TOKEN], unshare_net, True
    return _side


def _uncontained_dropper(rel, content="x\n"):
    """A wrap_no_network side-effect (human path) that DROPS `rel`. Returns (run_argv, isolated=False)."""
    def _side(argv):
        target = str(Path(rel))  # cwd is the workspace at run time
        script = ("import sys,pathlib;"
                  "p=pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True);"
                  "p.write_text(sys.argv[2])")
        return [sys.executable, "-c", script, target, content], False
    return _side


def _signed_autonomy(tmp):
    """A SIGNED session carrying shell.contained_autonomy with run_command at act_then_report — the
    only way an autonomous run_command earns autonomy (mutable capabilities never can, F5)."""
    key = b"caps-key"
    signed = mint(("shell.exec", SHELL_CONTAINED_AUTONOMY_CAP), {"run_command": ACT_THEN_REPORT},
                  "admin", workspace_subject(tmp), key)
    return Session(workspace=tmp, policy_caps=signed, caps_key=key,
                   leash_overrides={"run_command": ACT_THEN_REPORT})


class RecognizerUnit(unittest.TestCase):
    def test_matches_common_script_invocations(self):
        authored = {"build.sh", "sub/deploy.sh"}
        cases = {
            "sh ./build.sh": "build.sh",
            "bash build.sh": "build.sh",
            "./build.sh --flag": "build.sh",
            'sh "./build.sh"': "build.sh",       # quoted
            "sh sub/deploy.sh": "sub/deploy.sh",
            "sh /ws/build.sh": "build.sh",         # absolute path INTO the workspace
        }
        for cmd, want in cases.items():
            with self.subTest(cmd=cmd):
                self.assertEqual(provenance.references_autonomous_file(cmd, authored, "/ws"), want)

    def test_list_argv_form_matches(self):
        self.assertEqual(
            provenance.references_autonomous_file(["sh", "./build.sh"], {"build.sh"}, "/ws"), "build.sh")

    def test_no_match_for_unrelated_or_untracked(self):
        authored = {"build.sh"}
        for cmd in ("echo hi", "python setup.py", "cat notes.txt", "ls -la", "sh other.sh"):
            with self.subTest(cmd=cmd):
                self.assertEqual(provenance.references_autonomous_file(cmd, authored, "/ws"), "")

    def test_empty_manifest_is_never_a_match(self):
        self.assertEqual(provenance.references_autonomous_file("sh build.sh", set(), "/ws"), "")

    def test_flags_are_skipped_not_matched(self):
        # a token that begins with '-' is an option, never a path — even if a file were named "-x"
        self.assertEqual(provenance.references_autonomous_file("rm -build.sh", {"build.sh"}, "/ws"), "")

    def test_total_on_malformed_command(self):
        # unbalanced quote -> shlex raises -> falls back, never raises out; a non-str element too
        self.assertEqual(provenance.references_autonomous_file('sh "build.sh', {"build.sh"}, "/ws"), "")
        self.assertEqual(provenance.references_autonomous_file(object(), {"build.sh"}, "/ws"), "")

    def test_norm_rel_normalizes_and_drops_escaping(self):
        self.assertEqual(provenance.norm_rel("./build.sh"), "build.sh")
        self.assertEqual(provenance.norm_rel("sub\\b.sh"), "sub/b.sh")
        self.assertEqual(provenance.norm_rel("a/./b.sh"), "a/b.sh")
        for bad in ("", ".", "..", "../escape.sh", None):
            self.assertEqual(provenance.norm_rel(bad), "")


class ManifestState(unittest.TestCase):
    def test_note_normalizes_and_clear_discards(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            s.note_autonomous_authorship(["./a.sh", "sub\\b.sh", "../escape.sh", "", None])
            self.assertIn("a.sh", s._autonomous_authored)
            self.assertIn("sub/b.sh", s._autonomous_authored)
            self.assertNotIn("../escape.sh", s._autonomous_authored)   # escaping path dropped
            self.assertNotIn("", s._autonomous_authored)
            s.clear_autonomous_authorship(["./a.sh"])                  # clear normalizes too
            self.assertNotIn("a.sh", s._autonomous_authored)
            self.assertIn("sub/b.sh", s._autonomous_authored)

    def test_fresh_session_manifest_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(Session(workspace=tmp)._autonomous_authored, set())


class AutonomousWriteFileTracking(unittest.TestCase):
    def test_autonomous_write_records_then_human_run_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, capabilities=_CAPS)   # write_file default leash = act_then_report
            d = govern_action(s, ToolIntent("write_file", {"path": "drop.sh", "content": "echo hi"},
                                            "structured"))
            self.assertEqual(d.status, RAN)                  # autonomous (human_gated=False) verified write
            self.assertIn("drop.sh", s._autonomous_authored)
            held = govern_action(s, ToolIntent("run_command", {"command": "sh drop.sh"}, "structured"))
            self.assertEqual(held.status, HELD)              # ADVISORY — held for the human, not denied
            self.assertEqual(held.provenance_touch, "drop.sh")
            self.assertEqual(held.preview.get("autonomous_authored"), "drop.sh")
            self.assertIn("autonomy-authored file referenced", held.summary())

    def test_human_approved_write_clears_the_taint(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, capabilities=_CAPS,
                        leash_overrides={"write_file": PROPOSE_FIRST})
            s.note_autonomous_authorship(["drop.sh"])         # seed: an autonomous run authored it
            held = govern_action(s, ToolIntent("write_file", {"path": "drop.sh", "content": "reviewed"},
                                               "structured"))
            self.assertEqual(held.status, HELD)
            ran = approve(s, held)                             # the human vets these bytes
            self.assertEqual(ran.status, RAN)
            self.assertNotIn("drop.sh", s._autonomous_authored)   # taint cleared

    def test_untracked_file_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, capabilities=_CAPS,
                        leash_overrides={"run_command": PROPOSE_FIRST})
            held = govern_action(s, ToolIntent("run_command", {"command": "sh other.sh"}, "structured"))
            self.assertEqual(held.status, HELD)
            self.assertEqual(held.provenance_touch, "")
            self.assertNotIn("autonomous_authored", held.preview)


class ContainedRunTracking(unittest.TestCase):
    def test_contained_autonomous_run_records_dropped_file(self):
        with tempfile.TemporaryDirectory() as tmp, _CODE_UP, \
                patch("collaborator.governance.netns_available", return_value=True), \
                patch("collaborator.tools.wrap_contained", side_effect=_contained_dropper("dropped.sh")):
            s = _signed_autonomy(tmp)
            d = govern_action(s, ToolIntent("run_command", {"command": ["true"]}, "structured"))
            self.assertEqual(d.status, RAN)
            self.assertTrue(d.code_protected)                 # ran the REAL contained-autonomous path
            self.assertIn("dropped.sh", s._autonomous_authored)
            self.assertTrue((Path(tmp) / "dropped.sh").exists())

    def test_recorded_drop_then_flags_a_later_human_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _CODE_UP, patch("collaborator.governance.netns_available", return_value=True), \
                    patch("collaborator.tools.wrap_contained",
                          side_effect=_contained_dropper("build.sh")):
                s = _signed_autonomy(tmp)
                self.assertEqual(govern_action(
                    s, ToolIntent("run_command", {"command": ["true"]}, "structured")).status, RAN)
            # a LATER human run_command referencing the autonomously-dropped file is flagged (not denied).
            # leash=PROPOSE_FIRST is HOST authority forcing the HELD (human-hand) path deterministically —
            # without it, this autonomy-capable session AUTO-RUNS the shell on a bwrap-capable host (CI) but
            # HELDs on a host without code protection (Windows dev): a host-divergent test otherwise.
            held = govern_action(s, ToolIntent("run_command", {"command": "sh ./build.sh"}, "structured"),
                                 leash=PROPOSE_FIRST)
            self.assertEqual(held.status, HELD)
            self.assertEqual(held.provenance_touch, "build.sh")

    def test_directory_creations_are_not_recorded_as_files(self):
        # the executor makes workspace/.sandbox-home (a dir) before the run; a dir is never a runnable
        # file, so it must NOT enter the manifest — only the dropped FILE does.
        with tempfile.TemporaryDirectory() as tmp, _CODE_UP, \
                patch("collaborator.governance.netns_available", return_value=True), \
                patch("collaborator.tools.wrap_contained", side_effect=_contained_dropper("dropped.sh")):
            s = _signed_autonomy(tmp)
            govern_action(s, ToolIntent("run_command", {"command": ["true"]}, "structured"))
            self.assertNotIn(".sandbox-home", s._autonomous_authored)
            self.assertEqual(s._autonomous_authored, {"dropped.sh"})


class SnapshotRobustness(unittest.TestCase):
    """External panel (5/5): snapshot_tree must not HANG or ABORT on a special / unreadable file — a
    rw workspace lets an autonomous run drop a FIFO, and a blocking read would hang the govern loop."""

    def test_regular_file_still_hashes(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.txt").write_text("hi")
            snap = snapshot_tree(d)
            self.assertNotIn(snap["a.txt"], ("special", "unreadable", "dir"))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO requires os.mkfifo (POSIX)")
    def test_fifo_is_marked_special_and_does_not_hang(self):
        with tempfile.TemporaryDirectory() as d:
            os.mkfifo(str(Path(d) / "pipe"))       # a reader with no writer would block forever
            t0 = time.time()
            snap = snapshot_tree(d)                 # must return promptly, never read the FIFO
            self.assertLess(time.time() - t0, 5.0)
            self.assertEqual(snap["pipe"], "special")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO requires os.mkfifo (POSIX)")
    def test_dropped_fifo_is_recorded_via_diff_without_hanging(self):
        with tempfile.TemporaryDirectory() as d:
            pre = snapshot_tree(d)
            os.mkfifo(str(Path(d) / "pipe"))
            post = snapshot_tree(d)
            self.assertIn("pipe", observed_write_set(pre, post))   # visible as a change, not a hang


class _FakeOutcome:
    def __init__(self, cleared):
        self.cleared = cleared


class RecordingHonesty(unittest.TestCase):
    def test_autonomous_write_recorded_even_when_verification_does_not_clear(self):
        # External panel (opus CRITICAL): a written-but-unverified drop still leaves runnable bytes on
        # disk, so record on execution.result.ok (child reached disk), not only on full verification.
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, capabilities=_CAPS)
            with patch("collaborator.governance.govern", return_value=_FakeOutcome(False)):
                d = govern_action(s, ToolIntent("write_file", {"path": "drop.sh", "content": "x"},
                                                "structured"))
            self.assertNotEqual(d.status, RAN)            # verification did NOT clear...
            self.assertIn("drop.sh", s._autonomous_authored)   # ...but the on-disk drop is still tracked

    def test_snapshot_failure_marks_tracking_incomplete_and_surfaces_it(self):
        # External panel (5/5): a snapshot failure must NOT silently under-record — surface DEGRADED
        # tracking so a missing ⚠ isn't read as "human-authored".
        with tempfile.TemporaryDirectory() as tmp, _CODE_UP, \
                patch("collaborator.governance.netns_available", return_value=True), \
                patch("collaborator.tools.wrap_contained", side_effect=_contained_dropper("dropped.sh")), \
                patch("collaborator.governance.snapshot_tree", side_effect=OSError("boom")):
            s = _signed_autonomy(tmp)
            d = govern_action(s, ToolIntent("run_command", {"command": ["true"]}, "structured"))
            self.assertEqual(d.status, RAN)                        # the run is NEVER blocked by a snapshot fail
            self.assertTrue(s._autonomous_tracking_incomplete)     # ...but the gap is recorded honestly
        # and a later human (HELD) run preview surfaces the degraded posture. leash=PROPOSE_FIRST forces
        # the human-hand path deterministically (this autonomy-capable session would AUTO-RUN on a
        # bwrap-capable host / CI, leaving preview=None — a host-divergent test otherwise).
        held = govern_action(s, ToolIntent("run_command", {"command": "sh whatever.sh"}, "structured"),
                             leash=PROPOSE_FIRST)
        self.assertEqual(held.status, HELD)
        self.assertTrue(held.preview.get("provenance_tracking_incomplete"))


class ClearOnHumanRun(unittest.TestCase):
    def test_human_approved_run_of_autonomous_file_clears_its_taint(self):
        # External panel (3 vendors): a conscious human accept of the EXACT bytes (approving `sh f`)
        # should drop the taint so an unchanged file does not nag on every future approval.
        with tempfile.TemporaryDirectory() as tmp, \
                patch("collaborator.maintain.maintenance_available", return_value=False), \
                patch("collaborator.tools.wrap_no_network", side_effect=_uncontained_dropper("noop.sh")):
            s = Session(workspace=tmp, capabilities=_CAPS,
                        leash_overrides={"run_command": PROPOSE_FIRST})
            s.note_autonomous_authorship(["build.sh"])
            held = govern_action(s, ToolIntent("run_command", {"command": "sh build.sh"}, "structured"))
            self.assertEqual(held.status, HELD)
            self.assertEqual(held.provenance_touch, "build.sh")     # flagged at the hand
            ran = approve(s, held)                                  # the human consciously accepts it
            self.assertEqual(ran.status, RAN)
            self.assertNotIn("build.sh", s._autonomous_authored)    # taint cleared -> no future nag
            # ...and a later reference is no longer flagged (until an autonomous re-write re-taints)
            held2 = govern_action(s, ToolIntent("run_command", {"command": "sh build.sh"}, "structured"))
            self.assertEqual(held2.provenance_touch, "")


class HumanMaintenanceNotTracked(unittest.TestCase):
    def test_human_run_writes_are_not_tracked(self):
        # A human-approved (human_gated) maintenance run is trusted + UNCONTAINED by design; the F2
        # snapshot fires ONLY for the autonomous contained path, so a human run's writes are NOT recorded.
        with tempfile.TemporaryDirectory() as tmp, \
                patch("collaborator.maintain.maintenance_available", return_value=False), \
                patch("collaborator.tools.wrap_no_network",
                      side_effect=_uncontained_dropper("mfile.sh")):
            s = Session(workspace=tmp, capabilities=_CAPS,
                        leash_overrides={"run_command": PROPOSE_FIRST})
            held = govern_action(s, ToolIntent("run_command", {"command": ["true"]}, "structured"))
            self.assertEqual(held.status, HELD)
            ran = approve(s, held)
            self.assertEqual(ran.status, RAN)
            self.assertTrue((Path(tmp) / "mfile.sh").exists())    # the human run DID write it...
            self.assertEqual(s._autonomous_authored, set())        # ...but it is NOT tracked


if __name__ == "__main__":
    unittest.main()
