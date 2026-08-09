"""ADR 0003 revisit #1: run_command runs network-isolated so egress.py is the sole path off
the machine. The argv wrap + honest flag are testable everywhere; the actual "no external
egress" PROOF is OS-gated to Linux (runs in ubuntu CI, skips on the Windows dev host).
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

from collaborator.netns import isolation_unverified, netns_available, wrap_no_network
from collaborator.tools import _exec_command


class WrapComposition(unittest.TestCase):
    def test_wrap_when_available(self):
        from collaborator import netns
        with mock.patch("collaborator.netns.netns_available", return_value=True):
            argv2, isolated = wrap_no_network(["echo", "hi there"])
        self.assertTrue(isolated)
        self.assertEqual(argv2[0], netns._UNSHARE_BIN)  # absolute path, not the bare name
        self.assertEqual(argv2[1:4], ["--map-root-user", "--net", "--"])
        # the original tokens survive intact as trailing argv — exec "$@" does not re-split them,
        # so a command with spaces stays one argv element (no shell-injection via the wrapper).
        self.assertEqual(argv2[-2:], ["echo", "hi there"])

    def test_passthrough_when_unavailable(self):
        with mock.patch("collaborator.netns.netns_available", return_value=False):
            argv2, isolated = wrap_no_network(["echo", "hi"])
        self.assertFalse(isolated)
        self.assertEqual(argv2, ["echo", "hi"])

    def test_empty_argv_never_isolated(self):
        with mock.patch("collaborator.netns.netns_available", return_value=True):
            self.assertEqual(wrap_no_network([]), ([], False))

    def test_unshare_binary_is_absolute(self):
        # Red-team F1: a bare `unshare` name would be PATH-resolved at run time and could be
        # shadowed by a planted binary → egress while falsely reporting isolated. argv[0] must be
        # an absolute path so subprocess runs it via execv with no PATH lookup.
        from collaborator import netns
        self.assertTrue(os.path.isabs(netns._UNSHARE_BIN), netns._UNSHARE_BIN)  # never a bare name
        with mock.patch("collaborator.netns.netns_available", return_value=True):
            argv2, _ = wrap_no_network(["curl", "https://evil.example"])
        self.assertEqual(argv2[0], netns._UNSHARE_BIN)
        self.assertNotEqual(argv2[0], "unshare")  # never the bare, shadowable name
        self.assertTrue(os.path.isabs(netns._SH_BIN), netns._SH_BIN)  # sh absolute too (parity)
        self.assertIn(netns._SH_BIN, argv2)


class IsolationUnverified(unittest.TestCase):
    """Red-team synthesis: network_isolated must be VERIFIED, not trust that unshare isolated.
    The per-run guard fails closed with a sentinel; the caller then flags isolated=False."""

    def test_sentinel_trips(self):
        self.assertTrue(isolation_unverified(44, "SALIENT_NETNS_UNVERIFIED\n"))
        self.assertTrue(isolation_unverified(44, b"...SALIENT_NETNS_UNVERIFIED..."))

    def test_normal_exit_or_other_error_does_not(self):
        self.assertFalse(isolation_unverified(0, ""))
        self.assertFalse(isolation_unverified(44, "some other error"))  # code alone isn't enough
        self.assertFalse(isolation_unverified(1, "SALIENT_NETNS_UNVERIFIED"))  # sentinel alone isn't


class AvailabilityAndFlag(unittest.TestCase):
    def test_non_linux_is_never_available(self):
        if sys.platform != "linux":
            self.assertFalse(netns_available())

    def test_exec_command_flag_is_honest(self):
        # The reported flag must match reality — never a silent claim of isolation.
        with tempfile.TemporaryDirectory() as d:
            ex = _exec_command(d, {"command": [sys.executable, "-c", "pass"]})
        self.assertEqual(ex.network_isolated, netns_available())


@unittest.skipUnless(netns_available(), "netns unavailable (non-Linux or user namespaces off)")
class IsolationProof(unittest.TestCase):
    """Runs only where netns is real (Linux CI). Proves run_command has NO external egress."""

    def test_external_connect_fails_inside_netns(self):
        # A child reaching a public IP literal (no DNS) must fail closed — a fresh net namespace
        # has only loopback and no route out, so the connect is unreachable.
        code = ("import socket,sys\n"
                "try:\n"
                "    socket.create_connection(('1.1.1.1',443),timeout=5)\n"
                "    print('REACHED'); sys.exit(0)\n"
                "except OSError:\n"
                "    print('BLOCKED'); sys.exit(7)\n")
        with tempfile.TemporaryDirectory() as d:
            ex = _exec_command(d, {"command": [sys.executable, "-c", code]})
        self.assertTrue(ex.network_isolated)
        self.assertNotEqual(ex.exit_code, 0)            # the connect did NOT succeed
        self.assertNotIn("REACHED", ex.result.output)

    def test_normal_command_still_runs_isolated(self):
        with tempfile.TemporaryDirectory() as d:
            ex = _exec_command(d, {"command": ["echo", "hello"]})
        self.assertTrue(ex.network_isolated)
        self.assertTrue(ex.result.ok)
        self.assertIn("hello", ex.result.output)


if __name__ == "__main__":
    unittest.main()
