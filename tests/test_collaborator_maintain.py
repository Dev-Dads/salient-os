"""ADR 0003 revisit #1b — the MAINTENANCE SANDBOX (deputy seal) for a human/opted-in run_command.

Cross-platform, hermetic tests of the wrapper's CONSTRUCTION + honest fallback (the bwrap argv, the
in-child guard, the sentinels, the off-Linux/no-bwrap no-op), plus a Linux ``@skipUnless`` LIVE proof
that a real maintenance child (1) keeps FULL filesystem reach, (2) has the deputy sockets masked, and
(3) — the crux — CANNOT `umount` the mask to reveal the real socket, because ALL caps are dropped. The
naive unprivileged-netns mask is defeatable in one `umount` (proven on a live host); this is the
un-removable version.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from collaborator import maintain


class HonestFallback(unittest.TestCase):
    """Where the sandbox is unavailable (non-Linux, no bwrap), the wrapper NO-OPs and reports honestly —
    the caller then falls back to the routeless netns wrap, never a fake seal."""

    def test_off_linux_wrap_is_a_noop(self):
        if sys.platform == "linux":
            self.skipTest("this asserts the NON-Linux fallback")
        argv, isolated, sandboxed = maintain.wrap_maintenance(["echo", "hi"], "/tmp/x")
        self.assertEqual(argv, ["echo", "hi"])   # unchanged
        self.assertFalse(isolated)
        self.assertFalse(sandboxed)              # -> the caller falls back to wrap_no_network

    def test_off_linux_probe_is_false(self):
        if sys.platform == "linux":
            self.skipTest("this asserts the NON-Linux fallback")
        maintain._reset_probe_cache_for_tests()
        self.assertFalse(maintain.maintenance_available())

    def test_empty_argv_is_a_noop(self):
        argv, isolated, sandboxed = maintain.wrap_maintenance([], "/tmp/x")
        self.assertEqual(argv, [])
        self.assertFalse(sandboxed)


class Sentinels(unittest.TestCase):
    def test_verified_ok_is_the_positive_whitelist_and_rc_independent(self):
        # The AUTHORITATIVE signal: True iff the guard's positive token is present — regardless of the
        # payload's exit code (a genuinely-sandboxed maintenance command may fail), and absence never counts.
        self.assertTrue(maintain.verified_ok(0, "x SALIENT_MAINT_VERIFIED"))
        self.assertTrue(maintain.verified_ok(1, "SALIENT_MAINT_VERIFIED\ncommand failed"))
        self.assertFalse(maintain.verified_ok(0, "no token here"))            # no proof => not verified
        self.assertFalse(maintain.verified_ok(47, "SALIENT_MAINT_CAPS_PRESENT"))  # guard tripped

    def test_maintenance_unverified_only_on_a_guard_trip(self):
        self.assertTrue(maintain.maintenance_unverified(44, "SALIENT_NETNS_UNVERIFIED"))       # netns not fresh
        self.assertTrue(maintain.maintenance_unverified(47, "SALIENT_MAINT_CAPS_PRESENT"))     # caps present
        self.assertTrue(maintain.maintenance_unverified(46, "SALIENT_MAINT_MASK_UNVERIFIED"))  # deputy unmasked
        self.assertFalse(maintain.maintenance_unverified(0, "SALIENT_MAINT_VERIFIED"))         # a clean run
        self.assertFalse(maintain.maintenance_unverified(47, "unrelated"))                     # code alone isn't enough


class GuardScript(unittest.TestCase):
    """The in-child verification script — verified-not-trusted: net fresh, caps ALL dropped, deputies masked."""

    def test_guard_checks_net_caps_and_masks_then_positive_token(self):
        g = maintain._guarded_script(12345, ("/run/docker.sock", "/run/dbus/system_bus_socket"))
        # NET half (reuses netns's inode check + sentinel/exit-44)
        self.assertIn("12345", g)
        self.assertIn("SALIENT_NETNS_UNVERIFIED", g)
        self.assertIn("exit 44", g)
        # NO-ESCALATION half — all four cap sets zero AND NoNewPrivs=1 (the umount- + setuid-defense)
        for tok in ("CapEff", "CapPrm", "CapBnd", "CapAmb", "NoNewPrivs"):
            self.assertIn(tok, g)
        self.assertIn("SALIENT_MAINT_CAPS_PRESENT", g)
        self.assertIn("exit 47", g)
        # MASK half — each present deputy must be a char device (/dev/null)
        self.assertIn("/run/docker.sock", g)
        self.assertIn("/run/dbus/system_bus_socket", g)
        self.assertIn("SALIENT_MAINT_MASK_UNVERIFIED", g)
        self.assertIn("exit 46", g)
        # POSITIVE token AFTER every check, immediately before exec (a tripped check exits earlier)
        self.assertIn("SALIENT_MAINT_VERIFIED", g)
        self.assertLess(g.index("SALIENT_MAINT_VERIFIED"), g.index('exec "$@"'))
        self.assertGreater(g.index("SALIENT_MAINT_VERIFIED"), g.index("CapEff"))

    def test_caps_check_present_even_with_no_deputies(self):
        # Even on a host with no deputy sockets, caps MUST be verified dropped (else the seal is a lie
        # everywhere the sandbox reports available).
        g = maintain._guarded_script(12345, ())
        self.assertIn("SALIENT_MAINT_CAPS_PRESENT", g)
        self.assertNotIn("SALIENT_MAINT_MASK_UNVERIFIED", g)   # no deputies -> no mask loop
        self.assertTrue(g.rstrip().endswith('exec "$@"'))

    def test_present_deputy_sockets_is_shell_safe(self):
        for p in maintain._present_deputy_sockets():
            self.assertFalse(any(c in p for c in '\n\r\t "\'\\$`;|&<>*?()[]{}'), p)


class ArgvTemplate(unittest.TestCase):
    """The bwrap argv: full read-write host view (maintenance reach) + un-removable deputy masks + cap-drop."""

    def _argv(self, *, unshare_net=True, deputies=("/run/docker.sock",)):
        return maintain._bwrap_argv("/ws", deputies, unshare_net=unshare_net, inner="GUARD")

    def test_hardening_and_uid_flags(self):
        a = " ".join(self._argv())
        for flag in ("--unshare-user", "--disable-userns", "--assert-userns-disabled",
                     "--cap-drop", "ALL", "--die-with-parent", "--new-session", "--unshare-net"):
            self.assertIn(flag, a)
        self.assertNotIn("--unshare-all", a)          # its -try variant fails OPEN — never used

    def test_full_fs_view_and_fresh_dev_proc(self):
        a = self._argv()
        i = a.index("--bind")
        self.assertEqual(a[i + 1:i + 3], ["/", "/"])   # FULL read-write host view (maintenance keeps reach)
        j = a.index("--dev")
        self.assertEqual(a[j + 1], "/dev")             # fresh /dev (host /dev bind leaves /dev/null unwritable)
        self.assertIn("--proc", a)
        # appears-as-root parity with the mapped-root netns path
        u = a.index("--uid")
        self.assertEqual(a[u + 1], "0")

    def test_deputy_sockets_masked_after_the_full_bind(self):
        a = self._argv(deputies=("/run/docker.sock",))
        # the deputy is masked with an identity ro-bind of /dev/null
        k = a.index("/run/docker.sock")
        self.assertEqual(a[k - 2:k], ["--ro-bind", "/dev/null"])
        # the mask (--ro-bind /dev/null /run/docker.sock) comes AFTER the full --bind / / so it overrides it
        self.assertLess(a.index("--bind"), k)
        # chdir into the workspace
        c = a.index("--chdir")
        self.assertEqual(a[c + 1], "/ws")

    def test_unshare_net_toggles_with_flag(self):
        self.assertIn("--unshare-net", self._argv(unshare_net=True))
        self.assertNotIn("--unshare-net", self._argv(unshare_net=False))


class ExecCommandWiring(unittest.TestCase):
    """The human run_command path prefers the maintenance sandbox and falls back honestly."""

    def test_human_path_uses_maintenance_sandbox_when_available(self):
        from collaborator import tools
        with mock.patch("collaborator.maintain.maintenance_available", return_value=True) as avail, \
             mock.patch("collaborator.maintain.wrap_maintenance",
                        return_value=(["/usr/bin/bwrap", "true"], True, True)) as wrap, \
             mock.patch("collaborator.tools.run_supervised") as rs:
            rs.return_value = type("R", (), {"returncode": 0, "stdout": b"", "stderr": b"x SALIENT_MAINT_VERIFIED"})()
            with tempfile.TemporaryDirectory() as d:
                ex = tools._exec_command(d, {"command": ["echo", "hi"]})
        avail.assert_called_once()
        wrap.assert_called_once()
        self.assertTrue(ex.network_isolated)           # whitelisted on the positive token
        self.assertFalse(ex.code_protected)            # maintenance sandbox does NOT protect code (full rw)

    def test_human_path_falls_back_to_netns_when_sandbox_unavailable(self):
        from collaborator import tools
        with mock.patch("collaborator.maintain.maintenance_available", return_value=False), \
             mock.patch("collaborator.tools.wrap_no_network",
                        return_value=(["echo", "hi"], False)) as wnn, \
             mock.patch("collaborator.tools.run_supervised") as rs:
            rs.return_value = type("R", (), {"returncode": 0, "stdout": b"hi", "stderr": b""})()
            with tempfile.TemporaryDirectory() as d:
                ex = tools._exec_command(d, {"command": ["echo", "hi"]})
        wnn.assert_called_once()                       # fell back to the certified routeless netns wrap
        self.assertFalse(ex.network_isolated)          # honest: this fake host provides no isolation

    def test_sandbox_run_without_token_is_not_isolated(self):
        # If the guard trips (no positive token), the command did not run and network_isolated is corrected.
        from collaborator import tools
        with mock.patch("collaborator.maintain.maintenance_available", return_value=True), \
             mock.patch("collaborator.maintain.wrap_maintenance",
                        return_value=(["/usr/bin/bwrap", "true"], True, True)), \
             mock.patch("collaborator.tools.run_supervised") as rs:
            rs.return_value = type("R", (), {"returncode": 47, "stdout": b"", "stderr": b"SALIENT_MAINT_CAPS_PRESENT"})()
            with tempfile.TemporaryDirectory() as d:
                ex = tools._exec_command(d, {"command": ["echo", "hi"]})
        self.assertFalse(ex.network_isolated)          # no token => not verified => honest False


@unittest.skipUnless(maintain.maintenance_available(), "bwrap maintenance sandbox unavailable on this host")
class MaintenanceSandboxProofLinux(unittest.TestCase):
    """LIVE (Linux CI + Sparky): a real maintenance child keeps full-FS reach, masks the deputies, and —
    the crux — CANNOT umount the mask (caps dropped), while the naive netns mask can (proven separately)."""

    def _run(self, inner_argv, workspace, deputies=None):
        dep = tuple(deputies) if deputies is not None else maintain._DEPUTY_SOCKETS
        with mock.patch.object(maintain, "_DEPUTY_SOCKETS", dep):
            argv, isolated, sandboxed = maintain.wrap_maintenance(inner_argv, workspace)
            self.assertTrue(sandboxed)
            return subprocess.run(argv, capture_output=True, timeout=40, text=True)

    def _sh(self, script, workspace, deputies=None):
        return self._run([maintain.contained._SH_BIN, "-c", script], workspace, deputies)

    def test_deputy_mask_survives_an_umount_attempt(self):
        import socket as _socket
        if not hasattr(_socket, "AF_UNIX"):
            self.skipTest("no AF_UNIX")
        with tempfile.TemporaryDirectory() as d:
            sock = os.path.join(d, "docker.sock")
            srv = _socket.socket(_socket.AF_UNIX)
            try:
                srv.bind(sock)
                script = (f'umount "{sock}" 2>/dev/null; '
                          f'if [ -S "{sock}" ]; then echo BYPASSED; else echo HELD; fi')
                r = self._sh(script, d, deputies=[sock])
            finally:
                srv.close()
        self.assertTrue(maintain.verified_ok(r.returncode, r.stderr))   # the sandbox verified
        self.assertIn("HELD", r.stdout)                                 # the mask survived the umount (cap-drop)
        self.assertNotIn("BYPASSED", r.stdout)

    def test_caps_are_all_dropped_inside(self):
        with tempfile.TemporaryDirectory() as d:
            r = self._sh('grep -E "CapEff|CapPrm" /proc/self/status', d, deputies=[])
        self.assertTrue(maintain.verified_ok(r.returncode, r.stderr))
        self.assertRegex(r.stdout, r"CapEff:\s+0+\b")                   # effective: no CAP_SYS_ADMIN to umount
        self.assertRegex(r.stdout, r"CapPrm:\s+0+\b")                   # permitted: nothing raiseable into effective

    def test_full_filesystem_reach_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            # the workspace is writable AND an arbitrary host file stays readable (holes-in-open-view, not a fence)
            r = self._sh('echo w > ./probe && cat ./probe && cat /etc/hostname >/dev/null && echo FS_OK', d, deputies=[])
        self.assertTrue(maintain.verified_ok(r.returncode, r.stderr))
        self.assertIn("w", r.stdout)
        self.assertIn("FS_OK", r.stdout)

    def test_setuid_is_defanged_by_no_new_privs(self):
        # Panel finding (4 vendors CRITICAL, refuted): setuid-root binaries ARE visible in the full-/ view,
        # but NoNewPrivs=1 makes the kernel ignore setuid/fcap on execve, so none can regain CAP_SYS_ADMIN.
        with tempfile.TemporaryDirectory() as d:
            r = self._sh('grep NoNewPrivs /proc/self/status', d, deputies=[])
        self.assertTrue(maintain.verified_ok(r.returncode, r.stderr))
        self.assertRegex(r.stdout, r"NoNewPrivs:\s+1")

    def test_host_proc_root_cannot_reach_a_masked_socket(self):
        # Panel finding (grok CRITICAL, refuted): reach the UNMASKED host socket via /proc/<host-pid>/root.
        # Blocked because the sandbox is a CHILD userns and cannot ptrace a parent-userns process -> EACCES.
        import socket as _socket
        if not hasattr(_socket, "AF_UNIX"):
            self.skipTest("no AF_UNIX")
        with tempfile.TemporaryDirectory() as d:
            sock = os.path.join(d, "docker.sock")
            srv = _socket.socket(_socket.AF_UNIX)
            try:
                srv.bind(sock)
                srv.listen(1)
                ppid = os.getpid()   # the test process — a host process in the PARENT userns
                script = (
                    "import socket\n"
                    "ok=False\n"
                    f"for pid in ('{ppid}','1'):\n"
                    "  try:\n"
                    "    c=socket.socket(socket.AF_UNIX); c.settimeout(3)\n"
                    f"    c.connect('/proc/%s/root{sock}' % pid); ok=True; c.close()\n"
                    "  except OSError: pass\n"
                    "print('REACHED' if ok else 'BLOCKED')\n")
                r = self._run([sys.executable, "-c", script], d, deputies=[sock])
            finally:
                srv.close()
        self.assertTrue(maintain.verified_ok(r.returncode, r.stderr))
        self.assertIn("BLOCKED", r.stdout)          # the host socket is unreachable via /proc/pid/root
        self.assertNotIn("REACHED", r.stdout)

    def test_network_is_routeless(self):
        code = ('import socket,sys\n'
                'try:\n'
                ' socket.create_connection(("1.1.1.1",443),timeout=5); print("REACHED")\n'
                'except OSError: print("BLOCKED")\n')
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(maintain, "_DEPUTY_SOCKETS", ()):
                argv, isolated, sandboxed = maintain.wrap_maintenance([sys.executable, "-c", code], d)
                r = subprocess.run(argv, capture_output=True, timeout=40, text=True)
        self.assertTrue(maintain.verified_ok(r.returncode, r.stderr))
        self.assertIn("BLOCKED", r.stdout)                             # routeless netns: egress.py stays sole IP path
        self.assertNotIn("REACHED", r.stdout)


if __name__ == "__main__":
    sys.exit(unittest.main())
