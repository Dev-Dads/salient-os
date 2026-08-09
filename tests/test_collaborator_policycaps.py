"""③ Signed PolicyCaps: authority bound to a verified grant. Under a grant, the mutable
config and the Step-2 controls can only TIGHTEN, never widen; tamper / strip / wrong
subject / absent key / an unlisted tool all fail closed. The panel's real findings are
each pinned by a test here.
"""

import tempfile
import unittest
from pathlib import Path

from collaborator.governance import DENIED, HELD, NOTIFIED, RAN, govern_action
from collaborator.loop import approve
from collaborator.policycaps import (
    PolicyCaps,
    SignedPolicyCaps,
    apply_cap,
    granted_capabilities,
    leash_cap,
    mint,
    verify,
    workspace_subject,
)
from collaborator.session import Session
from collaborator.toolcall import ToolIntent
from collaborator.view import JudgmentLedger, JudgmentView, set_leash

CAPS_KEY = b"authority-caps-key"


def _wi(path="a.txt", content="x"):
    return ToolIntent("write_file", {"path": path, "content": content}, "structured")


def _rc():
    return ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured")


def _granted(tmp, capabilities, leash_caps, key=CAPS_KEY, subject=None):
    subj = subject if subject is not None else workspace_subject(tmp)
    caps = mint(capabilities, leash_caps, "admin", subj, key)
    return Session(workspace=tmp, policy_caps=caps, caps_key=key)


class SignVerify(unittest.TestCase):
    def test_roundtrip_and_failures(self):
        caps = mint(("fs.write:project",), {"run_command": "propose_first"}, "admin", "/ws", CAPS_KEY)
        self.assertTrue(verify(caps, CAPS_KEY, "/ws"))
        self.assertFalse(verify(caps, b"wrong-key", "/ws"))
        self.assertFalse(verify(caps, CAPS_KEY, "/other"))     # subject mismatch
        self.assertFalse(verify(None, CAPS_KEY))
        self.assertFalse(verify(caps, None, "/ws"))            # absent key
        tampered = SignedPolicyCaps(
            PolicyCaps(("fs.write:project", "shell.exec"), (), "admin", "/ws"), caps.signature)
        self.assertFalse(verify(tampered, CAPS_KEY, "/ws"))    # edited caps, stale sig

    def test_canonical_is_deterministic_and_binding(self):
        a = mint(("b", "a"), {"write_file": "act_then_report"}, "admin", "/ws", CAPS_KEY)
        b = mint(("a", "b"), {"write_file": "act_then_report"}, "admin", "/ws", CAPS_KEY)
        self.assertEqual(a.signature, b.signature)             # order-independent
        c = mint(("a", "b", "shell.exec"), {"write_file": "act_then_report"}, "admin", "/ws", CAPS_KEY)
        self.assertNotEqual(a.signature, c.signature)          # different grant -> different sig

    def test_verify_is_total(self):
        self.assertFalse(verify("not-a-signed-caps", CAPS_KEY, "/ws"))  # never raises


class NoWiden(unittest.TestCase):
    def test_mutable_capabilities_cannot_widen(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _granted(tmp, ("fs.read:project", "fs.write:project"), {"write_file": "act_then_report"})
            s.capabilities = ("fs.read:project", "fs.write:project", "shell.exec")  # try to widen
            self.assertEqual(govern_action(s, _rc()).status, DENIED)  # grant is authoritative

    def test_view_setleash_cannot_loosen_past_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _granted(tmp, ("fs.read:project", "fs.write:project", "shell.exec"),
                         {"run_command": "propose_first", "write_file": "act_then_report"})
            set_leash(s, "run_command", "act_then_report")     # try to loosen via the view
            self.assertEqual(govern_action(s, _rc()).status, HELD)  # capped to propose_first

    def test_tighten_within_grant_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _granted(tmp, ("fs.read:project", "fs.write:project", "shell.exec"),
                         {"run_command": "propose_first", "write_file": "act_then_report"})
            set_leash(s, "run_command", "notify_only")         # stricter than cap -> allowed
            self.assertEqual(govern_action(s, _rc()).status, NOTIFIED)


class FailClosed(unittest.TestCase):
    def test_unlisted_tool_defaults_to_strictest(self):
        # granted shell.exec but NO run_command leash cap -> run_command is notify_only,
        # never runnable even at an act_then_report override (silence != looseness).
        with tempfile.TemporaryDirectory() as tmp:
            s = _granted(tmp, ("fs.read:project", "fs.write:project", "shell.exec"),
                         {"write_file": "act_then_report"})
            set_leash(s, "run_command", "act_then_report")
            self.assertEqual(govern_action(s, _rc()).status, NOTIFIED)

    def test_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _granted(tmp, ("fs.read:project", "fs.write:project"), {"write_file": "act_then_report"})
            s.policy_caps = SignedPolicyCaps(
                PolicyCaps(("fs.write:project", "shell.exec"), (("write_file", "act_then_report"),),
                           "admin", workspace_subject(tmp)),
                s.policy_caps.signature)  # widen caps, keep stale signature
            self.assertEqual(govern_action(s, _wi()).status, DENIED)  # even the write is denied

    def test_stripping_the_grant_fails_closed(self):
        # sticky enforcement: a session built WITH a grant does not revert to legacy when
        # the grant is nulled at runtime (the panel's consensus HIGH).
        with tempfile.TemporaryDirectory() as tmp:
            s = _granted(tmp, ("fs.read:project", "fs.write:project"), {"write_file": "act_then_report"})
            self.assertEqual(govern_action(s, _wi("ok.txt")).status, RAN)
            s.policy_caps = None
            self.assertEqual(govern_action(s, _wi("no.txt")).status, DENIED)
            self.assertFalse((Path(tmp) / "no.txt").exists())

    def test_absent_key_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _granted(tmp, ("fs.write:project",), {"write_file": "act_then_report"})
            s.caps_key = None
            self.assertEqual(govern_action(s, _wi()).status, DENIED)

    def test_replay_onto_another_workspace_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _granted(tmp, ("fs.write:project",), {"write_file": "act_then_report"},
                         subject="/some/other/workspace")  # minted for a different subject
            self.assertEqual(govern_action(s, _wi()).status, DENIED)


class ApprovePathReGate(unittest.TestCase):
    def test_stripping_between_hold_and_approve_denies(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _granted(tmp, ("fs.read:project", "fs.write:project"), {"write_file": "propose_first"})
            d = govern_action(s, _wi("held.txt"))
            self.assertEqual(d.status, HELD)
            s.policy_caps = None                       # revoke while held
            self.assertEqual(approve(s, d).status, DENIED)   # re-gate sources from the grant
            self.assertFalse((Path(tmp) / "held.txt").exists())


class ValidGrantGrants(unittest.TestCase):
    def test_grant_confers_capability_and_runnable_leash(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp, capabilities=())  # no plain caps at all
            s.policy_caps = mint(("fs.write:project",), {"write_file": "act_then_report"},
                                 "admin", workspace_subject(tmp), CAPS_KEY)
            s.caps_key = CAPS_KEY
            s.enforce_caps = True
            d = govern_action(s, _wi("g.txt"))
            self.assertEqual(d.status, RAN)            # the signed grant confers fs.write
            self.assertTrue((Path(tmp) / "g.txt").exists())


class ViewShowsEffective(unittest.TestCase):
    def test_snapshot_shows_granted_not_mutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _granted(tmp, ("fs.read:project", "fs.write:project"),
                         {"write_file": "act_then_report"})
            s.capabilities = ("fs.read:project", "fs.write:project", "shell.exec")  # widen mutable
            set_leash(s, "run_command", "act_then_report")
            snap = JudgmentView(s, JudgmentLedger()).snapshot()
            self.assertNotIn("shell.exec", snap["capabilities"])   # effective, not mutable
            self.assertEqual(snap["leashes"]["run_command"], "notify_only")  # capped (unlisted)


class Legacy(unittest.TestCase):
    def test_no_grant_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)  # constructed with NO grant
            self.assertFalse(s.enforce_caps)
            self.assertEqual(granted_capabilities(s), s.capabilities)
            self.assertIsNone(leash_cap(s, "run_command"))
            self.assertEqual(govern_action(s, _wi()).status, RAN)


class ApplyCap(unittest.TestCase):
    def test_stricter_wins(self):
        self.assertEqual(apply_cap("act_then_report", "propose_first"), "propose_first")
        self.assertEqual(apply_cap("notify_only", "propose_first"), "notify_only")
        self.assertEqual(apply_cap("act_then_report", None), "act_then_report")

    def test_unknown_fails_closed_never_returned_verbatim(self):
        # red-team F0: an unrecognised value on EITHER side must resolve to NOTIFY_ONLY, never be
        # returned as-is (the old code returned the unknown string, which then ran because it
        # matched neither `== PROPOSE_FIRST` nor `== NOTIFY_ONLY` downstream).
        self.assertEqual(apply_cap("act_then_report", "bogus"), "notify_only")
        self.assertEqual(apply_cap("propose-first", "notify_only"), "notify_only")  # hyphen typo
        self.assertEqual(apply_cap("bogus", None), "notify_only")


if __name__ == "__main__":
    unittest.main()
