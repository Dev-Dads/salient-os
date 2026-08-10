"""ADR 0003 revisit #1a + MINOR-B — non-Linux run_command parity and the approved==executed seal.

Two independent additions to the run_command govern slot, both pinned here:

* B1 — the ISOLATION-EARNS-AUTONOMY floor. run_command's raw network reach is isolated (netns) only
  on Linux with verified netns; where isolation is unavailable an act_then_report shell floors to a
  human hand unless a signed, default-deny ``shell.raw_network`` opt-in accepts raw reach. This floor
  is ORTHOGONAL to (and today BEHAVIOR-MASKED by) the F-6 Harm A code floor, which withholds ALL
  run_command autonomy while ``code_protection_available()`` is False. So every B1 test PATCHES
  ``code_protection_available``→True to stand the code floor down and put ONLY the network floor
  under test — the pre-wired second axis, exercised in isolation.

* MINOR-B — the by-reference-mutation (TOCTOU) seal, extended from net_post to held run_command /
  write_file: approval is bound to the EXACT args the human saw; a payload mutated after origination
  is DENIED, a decision with no seal fails CLOSED.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collaborator import netns
from collaborator.governance import DENIED, HELD, RAN, govern_action
from collaborator.loop import approve
from collaborator.policycaps import mint, workspace_subject
from collaborator.session import Session
from collaborator.toolcall import ToolIntent
from collaborator.tools import ACT_THEN_REPORT, PROPOSE_FIRST, SEALED_TOOLS, held_action_seal, toolset

_CAPS = ("fs.read:project", "fs.write:project", "shell.exec")
# code protection is deferred (False in this build); patch it True so the CODE floor stands down and
# only the NETWORK floor (B1) governs the outcome under test.
_CODE_UP = patch("collaborator.governance.code_protection_available", return_value=True)


def _session(tmp, *, caps=_CAPS, **kw):
    return Session(workspace=tmp, capabilities=caps, **kw)


class IsolationEarnsAutonomyFloor(unittest.TestCase):
    """B1 in ISOLATION (code floor patched down): only the network-isolation floor is under test."""

    def test_atr_shell_floored_off_linux_without_optin(self):
        # No verified netns AND no shell.raw_network opt-in -> an act_then_report run_command is
        # WITHHELD to a human hand (HELD/propose_first), never auto-run with raw reach.
        with tempfile.TemporaryDirectory() as tmp, _CODE_UP, \
                patch("collaborator.governance.netns_available", return_value=False):
            s = _session(tmp, leash_overrides={"run_command": ACT_THEN_REPORT})
            d = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            self.assertEqual(d.status, HELD)
            self.assertEqual(d.leash, PROPOSE_FIRST)

    def test_atr_shell_runs_with_SIGNED_raw_network_optin(self):
        # The SIGNED, default-deny opt-in ACCEPTS raw reach on this host -> the floor stands down and
        # the shell may auto-run (act_then_report). The isolation flag stays HONESTLY False (real
        # netns is unavailable on this dev host — the executor never claims isolation it lacks).
        with tempfile.TemporaryDirectory() as tmp, _CODE_UP, \
                patch("collaborator.governance.netns_available", return_value=False):
            key = b"caps-key"
            signed = mint(("shell.exec", netns.SHELL_RAW_NETWORK_CAP),
                          {"run_command": ACT_THEN_REPORT}, "admin", workspace_subject(tmp), key)
            s = Session(workspace=tmp, policy_caps=signed, caps_key=key,
                        leash_overrides={"run_command": ACT_THEN_REPORT})
            d = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            self.assertEqual(d.status, RAN)
            self.assertEqual(d.leash, ACT_THEN_REPORT)
            self.assertIs(d.network_isolated, False)   # honest — no raw-reach claim of isolation

    def test_UNSIGNED_raw_network_optin_does_NOT_stand_the_floor_down(self):
        # red-team F1: the raw-reach opt-in is the "run raw unattended" signal, so — like the emission
        # auto-lift (F5) — it must rest on a SIGNED grant, never mutable session.capabilities. Listing
        # shell.raw_network in an UNSIGNED session's caps must NOT lift autonomy: still HELD.
        with tempfile.TemporaryDirectory() as tmp, _CODE_UP, \
                patch("collaborator.governance.netns_available", return_value=False):
            s = _session(tmp, caps=(*_CAPS, netns.SHELL_RAW_NETWORK_CAP),
                         leash_overrides={"run_command": ACT_THEN_REPORT})
            d = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            self.assertEqual(d.status, HELD)
            self.assertEqual(d.leash, PROPOSE_FIRST)

    def test_atr_shell_runs_when_isolation_available(self):
        # Verified netns available AND the executor genuinely isolates -> autonomy is NOT withheld and
        # the shell auto-runs. On this non-Linux dev host real netns is absent, so wrap_no_network is
        # patched to simulate a host that DOES isolate (the Linux IsolationProof tests exercise the
        # real thing); with the isolation actually achieved, require_isolation is satisfied.
        def _isolated(argv):
            return [str(a) for a in argv], True
        with tempfile.TemporaryDirectory() as tmp, _CODE_UP, \
                patch("collaborator.governance.netns_available", return_value=True), \
                patch("collaborator.tools.wrap_no_network", side_effect=_isolated):
            s = _session(tmp, leash_overrides={"run_command": ACT_THEN_REPORT})
            d = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            self.assertEqual(d.status, RAN)
            self.assertEqual(d.leash, ACT_THEN_REPORT)
            self.assertIs(d.network_isolated, True)

    def test_default_leash_shell_is_held_regardless(self):
        # run_command's default leash is already propose_first, so a shell without a loosening
        # override is HELD whether or not isolation is available — the floor only ever TIGHTENS.
        with tempfile.TemporaryDirectory() as tmp, _CODE_UP, \
                patch("collaborator.governance.netns_available", return_value=True):
            s = _session(tmp)
            d = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            self.assertEqual(d.status, HELD)


class RawNetworkPreview(unittest.TestCase):
    """The HELD preview honestly surfaces raw network reach — LIVE off-Linux (not masked)."""

    def test_held_preview_shows_raw_network_off_linux(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch("collaborator.governance.netns_available", return_value=False):
            s = _session(tmp)  # default propose_first -> HELD
            d = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            self.assertEqual(d.status, HELD)
            self.assertIs(d.preview.get("raw_network"), True)

    def test_held_preview_omits_raw_network_when_isolation_available(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch("collaborator.governance.netns_available", return_value=True):
            s = _session(tmp)
            d = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            self.assertEqual(d.status, HELD)
            self.assertNotIn("raw_network", d.preview)


class HeldActionSeal(unittest.TestCase):
    """MINOR-B — the seal function itself: injective, tool-scoped, "" where nothing is consequential."""

    def test_seal_is_empty_for_non_sealed_tools(self):
        self.assertEqual(held_action_seal("read_file", {"path": "a"}), "")
        self.assertEqual(held_action_seal("web_fetch", {"url": "https://x.example/"}), "")

    def test_seal_changes_with_argv_and_content(self):
        a = held_action_seal("run_command", {"command": ["echo", "hi"]})
        b = held_action_seal("run_command", {"command": ["echo", "bye"]})
        self.assertNotEqual(a, b)
        self.assertTrue(a)
        w = held_action_seal("write_file", {"path": "a.txt", "content": "x"})
        w2 = held_action_seal("write_file", {"path": "a.txt", "content": "y"})
        self.assertNotEqual(w, w2)

    def test_str_and_list_command_seal_distinctly(self):
        # a string command _exec_command shlex-splits vs. a pre-split argv EXECUTE the same here but
        # are different consequential inputs — the type tag keeps them from colliding.
        self.assertNotEqual(held_action_seal("run_command", {"command": "echo hi"}),
                            held_action_seal("run_command", {"command": ["echo", "hi"]}))


class MinorBApprovalSeal(unittest.TestCase):
    """MINOR-B at the approval boundary — approved == executed for held run_command / write_file."""

    def test_mutated_run_command_denied_at_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)  # run_command default propose_first -> HELD
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            self.assertEqual(held.status, HELD)
            self.assertTrue(held.seal)
            held.args["command"] = ["echo", "bye"]        # by-reference mutation after the human saw it
            d = approve(s, held)
            self.assertEqual(d.status, DENIED)
            self.assertIn("seal mismatch", d.reason)
            self.assertFalse(held.consumed)               # retryable, not burned

    def test_mutated_write_file_denied_at_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp, leash_overrides={"write_file": PROPOSE_FIRST})
            held = govern_action(s, ToolIntent(
                "write_file", {"path": "note.txt", "content": "approved"}, "structured"))
            self.assertEqual(held.status, HELD)
            self.assertTrue(held.seal)
            held.args["content"] = "swapped-in after approval"   # mutate content, path still resolves
            d = approve(s, held)
            self.assertEqual(d.status, DENIED)
            self.assertIn("seal mismatch", d.reason)
            self.assertFalse(held.consumed)
            self.assertFalse((Path(tmp) / "note.txt").exists())  # nothing was written

    def test_unmutated_run_command_approves_and_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            d = approve(s, held)                          # seal matches -> runs
            self.assertEqual(d.status, RAN)

    def test_unmutated_write_file_approves_and_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp, leash_overrides={"write_file": PROPOSE_FIRST})
            held = govern_action(s, ToolIntent(
                "write_file", {"path": "note.txt", "content": "hello"}, "structured"))
            d = approve(s, held)
            self.assertEqual(d.status, RAN)
            self.assertEqual((Path(tmp) / "note.txt").read_text(encoding="utf-8"), "hello")

    def test_missing_seal_fails_closed(self):
        # A held run_command whose seal was stripped (a decision not minted through the seam, or
        # tampered) must not run something unbound — approve() DENIES rather than executing.
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            held.seal = ""                                # strip the integrity binding
            d = approve(s, held)
            self.assertEqual(d.status, DENIED)
            self.assertIn("fail closed", d.reason)
            self.assertFalse(held.consumed)


class RedTeamFixes(unittest.TestCase):
    """Regressions for the internal-panel findings folded into this PR (reproduce-before-accept)."""

    def test_held_command_is_frozen_immutable(self):
        # F2: the held payload is FROZEN — the command is an immutable tuple, so an in-place aliasing
        # mutation can't swap argv between the human's view/approval and the run.
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            self.assertIsInstance(held.args["command"], tuple)
            with self.assertRaises(TypeError):
                held.args["command"][0] = "rm"          # frozen -> cannot mutate an element

    def test_in_window_list_mutation_cannot_change_execution(self):
        # F2 (rt-b1-floor repro): a holder that mutates the command in-place during the approve window
        # (simulated via the real in-chain names_code_root call site) can no longer change what runs —
        # the frozen tuple isn't a list, so the mutation never fires and the approved argv executes.
        import collaborator.governance as G
        real = G.names_code_root
        def mutate_then_call(command):
            if isinstance(command, list) and "hi" in command:
                command[:] = ["echo", "PWNED"]
            return real(command)
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            with patch.object(G, "names_code_root", side_effect=mutate_then_call):
                d = approve(s, held)
            self.assertEqual(d.status, RAN)
            self.assertIn("hi", (d.result.output if d.result else ""))
            self.assertNotIn("PWNED", (d.result.output if d.result else ""))

    def test_hostile_str_is_evaluated_once_at_freeze(self):
        # rt-minorb-seal F1 deterministic repro: an element whose __str__ mutates a sibling on a later
        # call cannot make seal-time and run-time disagree — freeze calls str() ONCE at hold and runs
        # the frozen result.
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            cmd = [sys.executable, "-c", "print('CLEAN')", None]
            class Sneak:
                def __init__(self): self.n = 0
                def __str__(self):
                    self.n += 1
                    if self.n >= 2:
                        cmd[2] = "print('SWAPPED')"   # rewrite a sibling on any later str()
                    return ""
            cmd[3] = Sneak()
            held = govern_action(s, ToolIntent("run_command", {"command": cmd}, "structured"))
            d = approve(s, held)
            self.assertEqual(d.status, RAN)
            self.assertIn("CLEAN", (d.result.output if d.result else ""))
            self.assertNotIn("SWAPPED", (d.result.output if d.result else ""))

    def test_missing_binary_fails_closed_not_raises(self):
        # F6b: a held run_command whose binary doesn't exist FAILS honestly (no exception escapes
        # approve(), a Decision is returned, the action is not lost to a raise).
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            held = govern_action(s, ToolIntent(
                "run_command", {"command": ["definitely-not-a-real-binary-zzz"]}, "structured"))
            d = approve(s, held)                       # must not raise
            self.assertEqual(d.status, "failed")
            self.assertIn("command error", d.reason)

    def test_unbalanced_quote_command_fails_closed_not_raises(self):
        # F6b: a string command that shlex cannot split (ValueError) FAILS, never raises.
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            held = govern_action(s, ToolIntent("run_command", {"command": 'echo "unbalanced'}, "structured"))
            d = approve(s, held)                       # must not raise
            self.assertEqual(d.status, "failed")
            self.assertIn("command error", d.reason)

    def test_sealed_tools_is_the_single_source(self):
        # rt-minorb-seal F3: held_action_seal seals EXACTLY the tools in SEALED_TOOLS — no drift
        # between the seal-minting set and the approve-time verification set.
        probe = {"command": ["x"], "path": "p", "content": "c", "url": "https://h.example/"}
        sealed = frozenset(n for n in toolset() if held_action_seal(n, probe))
        self.assertEqual(sealed, SEALED_TOOLS)

    def test_wildcard_eq_seal_cannot_spoof_a_match(self):
        # rt-minorb-seal F5: a hostile object whose __eq__ returns True for anything must NOT satisfy
        # the seal check — compare_digest + the isinstance(str) guard fail it closed.
        class AnyEq:
            def __eq__(self, other): return True
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            held.seal = AnyEq()
            held.args["command"] = ["echo", "PWNED"]
            d = approve(s, held)
            self.assertEqual(d.status, DENIED)
            self.assertFalse(held.consumed)

    def test_exec_code_floor_belt_denies_autonomous_unprotected_shell(self):
        # N2: the CODE floor is re-asserted at execution too — a direct autonomous (not human_gated)
        # run_command reaching execute_and_verify while code protection is unavailable is DENIED,
        # symmetric with the network floor and the floor doing all the work today.
        from collaborator.governance import execute_and_verify
        from collaborator.tools import get_tool
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            d = execute_and_verify(s, get_tool("run_command"), held.directive, held.action_id,
                                   dict(held.args), leash=ACT_THEN_REPORT, human_gated=False)
            self.assertEqual(d.status, DENIED)
            self.assertIn("code protection unavailable", d.reason)

    def test_exec_network_floor_refuses_autonomous_unisolated_shell(self):
        # F3 (real belief<->behaviour binding): with code protection available, a direct autonomous
        # run_command with no verified netns and no signed opt-in is REFUSED BY THE EXECUTOR (it cannot
        # isolate) -> FAILED, bound to the ACTUAL isolation result, not the govern-time belief.
        from collaborator.governance import execute_and_verify
        from collaborator.tools import get_tool
        with tempfile.TemporaryDirectory() as tmp, _CODE_UP:
            s = _session(tmp)   # unsigned -> no opt-in
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            d = execute_and_verify(s, get_tool("run_command"), held.directive, held.action_id,
                                   dict(held.args), leash=ACT_THEN_REPORT, human_gated=False)
            self.assertEqual(d.status, "failed")
            self.assertIn("isolation required", (d.result.error if d.result else ""))
            self.assertIs(d.network_isolated, False)

    def test_exec_belt_not_keyed_on_leash_string(self):
        # N1: the execution belt keys on `not human_gated` (an autonomous execution), NOT the leash
        # string — a propose_first leash with human_gated=False can't slip past the isolation refusal.
        from collaborator.governance import execute_and_verify
        from collaborator.tools import get_tool
        with tempfile.TemporaryDirectory() as tmp, _CODE_UP:
            s = _session(tmp)
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            d = execute_and_verify(s, get_tool("run_command"), held.directive, held.action_id,
                                   dict(held.args), leash=PROPOSE_FIRST, human_gated=False)
            self.assertEqual(d.status, "failed")            # still refused, not run raw

    def test_human_approved_shell_unaffected_by_exec_belt(self):
        # A human-approved held shell is the human's call -> the execution belt does not refuse it.
        with tempfile.TemporaryDirectory() as tmp, \
                patch("collaborator.governance.netns_available", return_value=False):
            s = _session(tmp)
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            d = approve(s, held)
            self.assertEqual(d.status, RAN)

    def test_writefile_list_content_is_frozen_to_str(self):
        # N3: a JSON tool call can supply a LIST content (a shared mutable). freeze_args coerces
        # path/content to str EXACTLY as _exec_write will, so the seal binds what actually lands and an
        # in-window mutation of the caller's list can't reach the disk.
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp, leash_overrides={"write_file": PROPOSE_FIRST})
            content = ["approved"]
            held = govern_action(s, ToolIntent(
                "write_file", {"path": "note.txt", "content": content}, "structured"))
            self.assertIsInstance(held.args["content"], str)      # frozen to str at hold
            content[:] = ["PWNED"]                                 # mutate the caller's original list
            d = approve(s, held)
            self.assertEqual(d.status, RAN)
            self.assertEqual((Path(tmp) / "note.txt").read_text(encoding="utf-8"), "['approved']")

    def test_non_ascii_seal_denies_and_does_not_raise(self):
        # F5b: a tampered NON-ASCII seal must DENY (fail closed), never raise out of approve() (a real
        # seal is always an ASCII hexdigest; the .isascii() guard fails a non-ascii one closed).
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            held.seal = "café-not-a-real-seal-\U0001f600"
            d = approve(s, held)                                  # must not raise
            self.assertEqual(d.status, DENIED)
            self.assertFalse(held.consumed)

    def test_surrogate_seal_denies_and_does_not_raise(self):
        # F5b residual (rt-minorb-seal): a lone-surrogate tampered seal must DENY, never raise a
        # UnicodeEncodeError — the .isascii() guard catches it before any encode.
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            held.seal = "\ud800deadbeef"                          # lone surrogate
            d = approve(s, held)                                  # must not raise
            self.assertEqual(d.status, DENIED)
            self.assertFalse(held.consumed)

    def test_str_subclass_content_is_frozen_to_plain_str(self):
        # rt-minorb-seal residual: a str SUBCLASS with a drifting __str__ in content must be coerced
        # to a plain str at freeze (str() evaluated once), so seal and disk can never diverge.
        class DriftStr(str):
            _n = [0]
            def __str__(self):
                self._n[0] += 1
                return "DRIFTED" if self._n[0] >= 3 else "approved"
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp, leash_overrides={"write_file": PROPOSE_FIRST})
            held = govern_action(s, ToolIntent(
                "write_file", {"path": "n.txt", "content": DriftStr("approved")}, "structured"))
            self.assertIs(type(held.args["content"]), str)       # plain str, not the drifting subclass
            d = approve(s, held)
            self.assertEqual(d.status, RAN)
            self.assertEqual(held.args["content"], (Path(tmp) / "n.txt").read_text(encoding="utf-8"))


class ExternalPanelFixes(unittest.TestCase):
    """Regressions for the 5-vendor certification panel's convergent finding — approve() must be a
    SELF-CONTAINED approved==executed boundary, not reliant on govern_action having frozen/sealed."""

    def test_approve_re_freezes_unfrozen_args(self):
        # gemini (HIGH): a HELD decision whose args are a shared MUTABLE (not frozen by govern_action)
        # must still be safe — approve() re-freezes, so an in-window mutation can't change execution.
        import collaborator.governance as G
        real = G.names_code_root
        def mutate(cmd):
            if isinstance(cmd, list) and "APPROVED" in cmd:
                cmd[:] = ["echo", "PWNED"]
            return real(cmd)
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp)
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            held.args = {"command": ["echo", "APPROVED"]}        # simulate UNFROZEN args at approve
            held.seal = held_action_seal("run_command", held.args)
            with patch.object(G, "names_code_root", side_effect=mutate):
                d = approve(s, held)
            self.assertEqual(d.status, RAN)
            self.assertIn("APPROVED", (d.result.output if d.result else ""))
            self.assertNotIn("PWNED", (d.result.output if d.result else ""))

    def test_tool_rebind_downgrade_is_denied(self):
        # grok (MEDIUM): a Decision.tool rebound to a non-sealed tool while keeping a leftover seal is
        # DENIED — a write_file hold cannot be downgraded to a read_file of the same path.
        with tempfile.TemporaryDirectory() as tmp:
            s = _session(tmp, leash_overrides={"write_file": PROPOSE_FIRST})
            (Path(tmp) / "secret.txt").write_text("SECRET", encoding="utf-8")
            held = govern_action(s, ToolIntent(
                "write_file", {"path": "secret.txt", "content": "overwrite"}, "structured"))
            held.tool = "read_file"                              # rebind to downgrade write -> read
            d = approve(s, held)
            self.assertEqual(d.status, DENIED)                   # leftover write_file seal can't match

    def test_seal_binds_tool_identity(self):
        # the seal includes the tool name, so identical args seal differently per tool — a rebind
        # between two SEALED tools cannot replay one's sealed args as the other's action.
        common = {"command": ["x"], "path": "x", "content": "x"}
        a = held_action_seal("run_command", common)
        b = held_action_seal("write_file", common)
        self.assertTrue(a and b)
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    sys.exit(unittest.main())
