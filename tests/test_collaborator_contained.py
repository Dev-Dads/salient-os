"""PR 1b — bwrap contained execution ("protection earns autonomy").

Cross-platform, hermetic tests of the wrapper's CONSTRUCTION + honest fallback (the argv template, the
in-child guard, the sentinels, the off-Linux/no-bwrap no-op), plus a Linux ``@skipUnless`` LIVE proof
that a real contained child genuinely has the code roots read-only and no $HOME/secrets in view. The
construction tests exercise the pure builders directly so they run everywhere (real bwrap is Linux-only
and absent on this dev host / some CI); the live proof is where the guarantee is actually checked.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from collaborator import codefence, contained


class HonestFallback(unittest.TestCase):
    """Where containment is unavailable (non-Linux, no bwrap), the wrapper NO-OPs and reports honestly —
    the seam then withholds autonomy (today's behaviour), never a fake fence."""

    def test_off_linux_wrap_is_a_noop_not_a_fake_fence(self):
        if sys.platform == "linux":
            self.skipTest("this asserts the NON-Linux fallback")
        argv, isolated, protected = contained.wrap_contained(["echo", "hi"], "/tmp/x")
        self.assertEqual(argv, ["echo", "hi"])   # unchanged
        self.assertFalse(isolated)
        self.assertFalse(protected)              # -> the executor REFUSES an autonomous run (never fakes)

    def test_off_linux_probe_is_false(self):
        if sys.platform == "linux":
            self.skipTest("this asserts the NON-Linux fallback")
        contained._reset_probe_cache_for_tests()
        self.assertFalse(contained.containment_available())
        self.assertFalse(codefence.code_protection_available())   # delegates to the probe

    def test_empty_roots_cannot_claim_protection(self):
        # No code roots to protect -> containment is meaningless -> not protected (fail closed), on any OS.
        argv, isolated, protected = contained.wrap_contained(["echo", "hi"], "/tmp/x", roots_with_witness=())
        self.assertFalse(protected)


class Sentinels(unittest.TestCase):
    def test_protection_unverified_only_on_exit_45_with_sentinel(self):
        self.assertTrue(contained.protection_unverified(45, "x SALIENT_CODEFENCE_UNVERIFIED y"))
        self.assertFalse(contained.protection_unverified(0, "SALIENT_CODEFENCE_UNVERIFIED"))   # wrong rc
        self.assertFalse(contained.protection_unverified(45, "clean"))                          # no sentinel
        self.assertFalse(contained.protection_unverified(44, "SALIENT_CODEFENCE_UNVERIFIED"))   # netns exit

    def test_setup_failed_only_on_bwrap_prefixed_stderr(self):
        self.assertTrue(contained.setup_failed(1, "bwrap: No permissions to create new namespace"))
        self.assertFalse(contained.setup_failed(0, "bwrap: whatever"))   # rc 0 -> the payload ran fine
        self.assertFalse(contained.setup_failed(1, "some other error"))  # not a bwrap setup failure

    def test_signed_cap_constant(self):
        self.assertEqual(contained.SHELL_CONTAINED_AUTONOMY_CAP, "shell.contained_autonomy")


class GuardScript(unittest.TestCase):
    """The in-child verification script — the crux of verified-not-trusted."""

    _PAIRS = ((Path("/x/collaborator"), Path("/x/collaborator/codefence.py")),
              (Path("/x/salienceos"), Path("/x/salienceos/__init__.py")))

    def test_code_half_checks_present_unwritable_and_ro_mount(self):
        g = contained._guarded_script(None, self._PAIRS, check_net=False)
        self.assertIn("SALIENT_CODEFENCE_UNVERIFIED", g)
        self.assertIn("exit 45", g)
        self.assertIn(">>", g)                       # the append-must-fail write probe
        self.assertIn("mountinfo", g)                # the structural ro check
        self.assertIn("collaborator/codefence.py", g)
        self.assertIn('exec "$@"', g)
        # no net half when check_net is False
        self.assertNotIn("SALIENT_NETNS_UNVERIFIED", g)
        self.assertNotIn("exit 44", g)

    def test_net_half_present_when_check_net(self):
        g = contained._guarded_script(12345, self._PAIRS, check_net=True)
        self.assertIn("12345", g)                    # the parent netns inode baked in
        self.assertIn("SALIENT_NETNS_UNVERIFIED", g)
        self.assertIn("exit 44", g)


class ArgvTemplate(unittest.TestCase):
    """The bwrap argv is deny-by-default, hardened, and never leaks $HOME/secrets."""

    _PAIRS = ((Path("/x/collaborator"), Path("/x/collaborator/codefence.py")),)

    def _argv(self, *, unshare_net):
        return contained._bwrap_argv("/ws", self._PAIRS, unshare_net=unshare_net,
                                     parent_ino=1, inner="GUARD")

    def test_hardening_flags_present(self):
        a = " ".join(self._argv(unshare_net=True))
        for flag in ("--unshare-user", "--disable-userns", "--assert-userns-disabled",
                     "--cap-drop", "ALL", "--die-with-parent", "--new-session", "--clearenv"):
            self.assertIn(flag, a)
        self.assertNotIn("--unshare-all", a)         # its -try variant fails OPEN — never used

    def test_code_root_ro_and_workspace_rw(self):
        a = self._argv(unshare_net=True)
        # the protected root is ro-bound at its identity path; the workspace is rw-bound. _bwrap_argv
        # binds str(root) — derive the expected token the same way so the test is host-agnostic (str(Path)
        # renders backslashes on Windows dev hosts, forward slashes on the Linux path that actually runs it).
        root = str(self._PAIRS[0][0])
        self.assertIn("--ro-bind", a)
        j = a.index(root)                    # the code root appears as an identity ro-bind: --ro-bind R R
        self.assertEqual(a[j - 1], "--ro-bind")
        self.assertEqual(a[j + 1], root)
        i = a.index("--bind")
        self.assertEqual(a[i + 1], "/ws")
        self.assertEqual(a[i + 2], "/ws")

    def test_deny_by_default_never_binds_home_or_root_or_run(self):
        a = " ".join(self._argv(unshare_net=True))
        # $HOME is SET to a workspace-local dir but the host home / root / docker-socket dir are NEVER bound
        self.assertNotIn("--bind /root", a)
        self.assertNotIn("--ro-bind /root", a)
        self.assertNotIn("/var/run", a)
        self.assertNotIn("--bind / ", a)             # never bind the host root
        self.assertIn(".sandbox-home", a)            # HOME points inside the fenced workspace

    def test_unshare_net_toggles_with_flag(self):
        self.assertIn("--unshare-net", self._argv(unshare_net=True))
        self.assertNotIn("--unshare-net", self._argv(unshare_net=False))


class Witness(unittest.TestCase):
    def test_witness_pairs_are_real_files_inside_their_roots(self):
        pairs = codefence.protected_roots_with_witness()
        self.assertTrue(pairs)                        # both packages resolve on a normal checkout
        for root, witness in pairs:
            self.assertTrue(witness.is_file())
            self.assertEqual(witness.parent, root)
            self.assertIn(root, codefence.PROTECTED_ROOTS)


@unittest.skipUnless(contained.containment_available(), "bwrap containment unavailable on this host")
class ContainmentProofLinux(unittest.TestCase):
    """LIVE: a real contained child genuinely has the code roots read-only and no secrets in view."""

    def test_code_root_is_read_only_inside_the_sandbox(self):
        root = codefence.PROTECTED_ROOTS[0]
        target = (root / "__contained_probe__.tmp").as_posix()
        with tempfile.TemporaryDirectory() as ws:
            argv, _iso, protected = contained.wrap_contained(
                [contained._SH_BIN, "-c", f'echo x > "{target}" 2>/dev/null; echo rc=$?'], ws)
            self.assertTrue(protected)
            r = subprocess.run(argv, capture_output=True, timeout=30, text=True)
            self.assertEqual(r.returncode, 0)                       # guard passed + payload ran
            self.assertNotIn("rc=0", r.stdout)                      # the write to the code root FAILED (ro)
            self.assertFalse((root / "__contained_probe__.tmp").exists())  # nothing written on the host

    def test_no_home_or_shadow_in_view(self):
        with tempfile.TemporaryDirectory() as ws:
            argv, _iso, protected = contained.wrap_contained(
                [contained._SH_BIN, "-c",
                 'cat /etc/shadow 2>/dev/null; ls /root 2>/dev/null; echo DONE'], ws)
            self.assertTrue(protected)
            r = subprocess.run(argv, capture_output=True, timeout=30, text=True)
            self.assertIn("DONE", r.stdout)
            self.assertNotIn("root:", r.stdout)                     # /etc/shadow masked with /dev/null (empty)


if __name__ == "__main__":
    sys.exit(unittest.main())
