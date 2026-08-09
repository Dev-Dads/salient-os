"""ADR 0003 egress in the governance seam: the capability-DERIVATION gate.

An egress tool's authority is net.get:<canonical-host>, computed from the request and checked
by the same exact-match core gate — so egress is default-deny, the allowlist is exact (no
subdomain widening), an ineligible URL fails closed, and the destination canonicalizes the
SAME way for the capability key as for the connect host. Also pins: the egress_log run path
carries the channel-integrity record, the approval re-gate re-derives + re-checks the
allowlist (emission TOCTOU), and the offense recognizer TAGS without denying.
"""

import tempfile
import unittest
from unittest import mock

from collaborator.egress import EgressRecord, EgressResult
from collaborator.governance import DENIED, FAILED, HELD, RAN, govern_action
from collaborator.loop import approve
from collaborator.policycaps import mint, workspace_subject
from collaborator.session import Session
from collaborator.toolcall import ToolIntent


def _wf(url):
    return ToolIntent("web_fetch", {"url": url}, "structured")


def _ok_record(dest="docs.example"):
    return EgressRecord(canonical_dest=dest, method="GET", request_target_hash="th",
                        request_bytes=3, status=200, response_hash="rh", response_len=2,
                        redirect_location=None, resolved_ip="93.184.216.34", ok=True)


def _fake_ok_fetch(url, **kw):
    return EgressResult(_ok_record(), body=b"ok")


def _granted(tmp, caps, leash_caps=None, key=b"caps-key"):
    signed = mint(caps, leash_caps or {}, "admin", workspace_subject(tmp), key)
    return Session(workspace=tmp, policy_caps=signed, caps_key=key)


class DefaultDeny(unittest.TestCase):
    def test_no_net_cap_denies(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d)  # default caps: fs read/write only, no net.get
            dec = govern_action(s, _wf("https://docs.example/x"))
            self.assertEqual(dec.status, DENIED)
            self.assertIn("net.get:docs.example", dec.reason)

    def test_structural_prohibition_nonconsented_host_unreachable(self):
        # The exact-match allowlist means a host you were not granted is UNREACHABLE, not
        # merely un-recognized — the structural prohibition (ADR 0003).
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("net.get:example.com",))
            dec = govern_action(s, _wf("https://evil.example.com/steal"))  # subdomain != grant
            self.assertEqual(dec.status, DENIED)
            self.assertIn("net.get:evil.example.com", dec.reason)


class IneligibleUrl(unittest.TestCase):
    def test_http_denied_before_grant_check(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("net.get:docs.example",))
            dec = govern_action(s, _wf("http://docs.example/x"))  # not https
            self.assertEqual(dec.status, DENIED)
            self.assertIn("ineligible egress URL", dec.reason)

    def test_userinfo_denied(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("net.get:docs.example",))
            dec = govern_action(s, _wf("https://docs.example@evil.com/"))
            self.assertEqual(dec.status, DENIED)
            self.assertIn("ineligible egress URL", dec.reason)


class GrantedRuns(unittest.TestCase):
    def test_canonicalized_grant_runs_and_carries_record(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("net.get:docs.example",))
            with mock.patch("collaborator.egress.fetch", _fake_ok_fetch):
                dec = govern_action(s, _wf("https://Docs.Example/PAGE"))  # case-variant host
            self.assertEqual(dec.status, RAN)
            self.assertTrue(dec.cleared)
            self.assertIsNotNone(dec.egress)
            self.assertEqual(dec.egress.canonical_dest, "docs.example")

    def test_failed_fetch_reports_not_verified(self):
        bad = EgressResult(EgressRecord(
            canonical_dest="docs.example", method="GET", request_target_hash="", request_bytes=1,
            status=None, response_hash=None, response_len=0, redirect_location=None,
            resolved_ip=None, ok=False, error="no safe public IP"), body=b"")
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("net.get:docs.example",))
            with mock.patch("collaborator.egress.fetch", lambda url, **k: bad):
                dec = govern_action(s, _wf("https://docs.example/x"))
            self.assertEqual(dec.status, FAILED)
            self.assertFalse(dec.cleared)
            self.assertIn("no safe public IP", dec.reason)


class ApprovalRegateTOCTOU(unittest.TestCase):
    def test_host_removed_from_allowlist_between_hold_and_approve_denies(self):
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.get:docs.example",), {"web_fetch": "propose_first"})
            held = govern_action(s, _wf("https://docs.example/x"))
            self.assertEqual(held.status, HELD)
            # Re-mint the signed caps WITHOUT the host (operator revokes the channel).
            s.policy_caps = mint((), {"web_fetch": "propose_first"}, "admin",
                                 workspace_subject(d), b"caps-key")
            with mock.patch("collaborator.egress.fetch", _fake_ok_fetch):  # must NOT be reached
                out = approve(s, held)
            self.assertEqual(out.status, DENIED)
            self.assertIn("not granted at approval time", out.reason)


class OffenseRecognizerIsAuditOnly(unittest.TestCase):
    def test_offense_shape_tags_but_does_not_deny(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("shell.exec",))
            dec = govern_action(s, ToolIntent("run_command", {"command": "nmap -sV example.com"},
                                              "structured"))
            self.assertEqual(dec.status, HELD)          # run_command's propose_first floor — not denied
            self.assertEqual(dec.offense_flag, "nmap")  # tagged for the audit trail
            self.assertIn("offense-shape audit", dec.summary())


if __name__ == "__main__":
    unittest.main()
