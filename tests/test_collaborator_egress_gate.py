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
from collaborator.governance import DENIED, FAILED, HELD, NOTIFIED, RAN, govern_action
from collaborator.loop import approve, emit
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


ATR = "act_then_report"


def _np(url=NP_URL, body='{"m":"x"}', source="structured"):
    return ToolIntent("net_post", {"url": url, "body": body}, source)


def _emit_directed(s, intent):
    """A HOST-DIRECTED autonomous emission via the operator entry point: BOTH source='host' (only
    emit() stamps it — the parser never can) AND the keyword leash=act_then_report. Both are
    required for autonomy and NEITHER is model-reachable (red-team F1 / F-5 defence-in-depth)."""
    return govern_action(s, ToolIntent(intent.name, intent.args, "host"), leash=ATR)


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

    def test_host_directed_plus_signed_auto_grant_lifts_to_autonomous(self):
        with tempfile.TemporaryDirectory() as d:
            s = self._auto(d)
            with mock.patch("collaborator.egress.post", _fake_post):
                dec = _emit_directed(s, _np())   # host passes keyword leash=act_then_report
            self.assertEqual(dec.status, RAN)
            self.assertEqual(dec.leash, "act_then_report")

    def test_model_emitted_never_auto_lifts(self):
        # red-team F1: a model tool-call carries a PARSE-CHANNEL source ("structured"), which is NOT
        # a host-directed signal. Even WITH a signed auto grant + credentials, a model-emitted
        # emission (no keyword leash) stays HELD — the model can never self-originate autonomy.
        for src in ("structured", "content_block", "content_json", "research", "", "proposed"):
            with tempfile.TemporaryDirectory() as d:
                s = self._auto(d)
                s.egress_credentials = {"api.example": "Bearer sk-secret"}
                dec = govern_action(s, _np(source=src))   # NO keyword leash (the run_turn path)
                self.assertEqual(dec.status, HELD, src)
                self.assertEqual(dec.leash, "propose_first", src)

    def test_proposer_originated_stays_held_even_if_host_directed(self):
        # A proposer origination can never be autonomous even if a keyword leash slipped in — call
        # govern_action DIRECTLY so source stays "proposed" (not rewritten to "host" by the helper).
        with tempfile.TemporaryDirectory() as d:
            s = self._auto(d)
            dec = govern_action(s, _np(source="proposed"), leash=ATR)
            self.assertEqual(dec.status, HELD)
            self.assertEqual(dec.leash, "propose_first")

    def test_explicit_signed_leash_cap_still_tightens_an_auto_host(self):
        with tempfile.TemporaryDirectory() as d:
            s = self._auto(d, leash_cap="propose_first")   # operator explicitly caps net_post
            dec = _emit_directed(s, _np())
            self.assertEqual(dec.status, HELD)
            self.assertEqual(dec.leash, "propose_first")

    def test_auto_for_a_different_host_does_not_lift(self):
        with tempfile.TemporaryDirectory() as d:
            s = self._auto(d, auto_host="other.example")   # auto is per-host, exact-match
            dec = _emit_directed(s, _np())
            self.assertEqual(dec.status, HELD)
            self.assertEqual(dec.leash, "propose_first")

    def test_legacy_unsigned_session_cannot_auto_lift(self):
        # red-team F5: autonomy requires a SIGNED grant, never mutable session.capabilities.
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("net.post:api.example", "net.post.auto:api.example"))
            dec = _emit_directed(s, _np())   # host-directed, but no signed grant
            self.assertEqual(dec.status, HELD)
            self.assertEqual(dec.leash, "propose_first")


class HostEmitEntryPoint(unittest.TestCase):
    """The operator entry point that USES autonomous emission (ADR 0003 Tier 2). emit() is CALLER
    authority — it passes the keyword leash the model / run_turn never carry (red-team F1) — and
    autonomy still requires BOTH signed signals (per-host auto grant + net_post ATR leash-cap)."""

    URL = "https://api.example/v1/chat"

    def setUp(self):
        _CAPTURED.clear()   # module-level capture must not leak from a prior test

    def _full(self, d):
        return _granted(d, ("net.post:api.example", "net.post.auto:api.example"),
                        {"net_post": "act_then_report"})

    def test_autonomous_emit_with_full_grant_runs_body_free_and_injects_credential(self):
        with tempfile.TemporaryDirectory() as d:
            s = self._full(d)
            s.egress_credentials = {"api.example": "Bearer sk-live"}   # host-injected, never logged
            with mock.patch("collaborator.egress.post", _fake_post):
                dec = emit(s, self.URL, '{"model":"x"}', autonomous=True)
            self.assertEqual(dec.status, RAN)
            self.assertEqual(dec.leash, "act_then_report")
            self.assertEqual(_CAPTURED.get("auth"), "Bearer sk-live")   # credential injected by the seam
            self.assertFalse(_CAPTURED.get("keep_preview"))             # autonomous -> body-free audit
            self.assertEqual(dec.egress.request_body_preview, "")

    def test_autonomous_emit_without_leash_cap_notifies_loudly_not_silently(self):
        # auto cap granted but net_post UNLISTED in leash_caps -> "require both" leaves it notify-only
        # (an unlisted tool is notify_only under enforcement, CONSISTENTLY at every gate), with a LOUD
        # diagnosable reason so the operator adds the leash-cap and re-emits (MINOR-A: never a silent
        # no-op). A recoverable HELD was considered (OBS-1) but an unlisted net_post denies at the
        # approve() re-gate too, so terminal-loud-notify is the honest, consistent outcome.
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example", "net.post.auto:api.example"), {})
            with mock.patch("collaborator.egress.post", _fake_post):  # must NOT be reached
                dec = emit(s, self.URL, '{"model":"x"}', autonomous=True)
            self.assertEqual(dec.status, NOTIFIED)
            self.assertEqual(_CAPTURED, {})                           # nothing emitted
            self.assertIn("net.post.auto:api.example", dec.reason)    # loud, diagnosable
            self.assertIn("requires BOTH", dec.reason)

    def test_autonomous_emit_without_auto_grant_is_held(self):
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example",), {"net_post": "act_then_report"})  # no auto cap
            dec = emit(s, self.URL, '{"model":"x"}', autonomous=True)
            self.assertEqual(dec.status, HELD)
            self.assertEqual(dec.leash, "propose_first")

    def test_non_autonomous_emit_holds_then_approve_runs_with_bounded_preview(self):
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example",), {"net_post": "propose_first"})
            held = emit(s, self.URL, '{"model":"x"}')   # autonomous defaults False -> held
            self.assertEqual(held.status, HELD)
            self.assertTrue(held.seal)                   # emission sealed at hold (approved == sent)
            with mock.patch("collaborator.egress.post", _fake_post):
                out = approve(s, held)
            self.assertEqual(out.status, RAN)
            self.assertTrue(_CAPTURED.get("keep_preview"))   # human-gated -> bounded body preview


class NetPostCredentialInjection(unittest.TestCase):
    def _auto(self, d):
        return _granted(d, ("net.post:api.example", "net.post.auto:api.example"),
                        {"net_post": "act_then_report"})

    def test_host_credential_injected_for_consented_host(self):
        with tempfile.TemporaryDirectory() as d:
            s = self._auto(d)
            s.egress_credentials = {"api.example": "Bearer sk-secret"}
            with mock.patch("collaborator.egress.post", _fake_post):
                dec = _emit_directed(s, _np())
            self.assertEqual(dec.status, RAN)
            self.assertEqual(_CAPTURED.get("auth"), "Bearer sk-secret")

    def test_no_credential_configured_means_none_sent(self):
        with tempfile.TemporaryDirectory() as d:
            s = self._auto(d)  # no egress_credentials
            with mock.patch("collaborator.egress.post", _fake_post):
                dec = _emit_directed(s, _np())
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
                dec = _emit_directed(s, it)
            self.assertEqual(dec.status, RAN)
            self.assertIsNone(_CAPTURED.get("auth"))


class NetPostAuditSplit(unittest.TestCase):
    def test_autonomous_emission_is_body_free(self):
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example", "net.post.auto:api.example"),
                         {"net_post": "act_then_report"})
            with mock.patch("collaborator.egress.post", _fake_post):
                dec = _emit_directed(s, _np(body="secret"))
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
            dec = _emit_directed(s, _np(body="\ud800"))   # must NOT raise
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
                dec = _emit_directed(s, _np())
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

    def test_tighten_to_notify_only_between_hold_and_approve_denies(self):
        # red-team F3: the operator caps net_post to notify_only ("never run") after the hold.
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example",), {"net_post": "propose_first"})
            held = govern_action(s, _np())
            self.assertEqual(held.status, HELD)
            s.policy_caps = mint(("net.post:api.example",), {"net_post": "notify_only"}, "admin",
                                 workspace_subject(d), b"caps-key")
            with mock.patch("collaborator.egress.post", _fake_post):  # must NOT be reached
                out = approve(s, held)
            self.assertEqual(out.status, DENIED)
            self.assertIn("notify_only", out.reason)


class LeashFailClosed(unittest.TestCase):
    """red-team F0: an unrecognised leash string must fail CLOSED (held/notified), never run — the
    old denylist dispatch ran on the `else` branch, so a typo'd leash slipped through autonomously."""

    def test_typod_leash_override_rejected_at_construction(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                Session(workspace=d, capabilities=("net.post:api.example",),
                        leash_overrides={"net_post": "propose-first"})   # hyphen typo

    def test_unknown_leash_reaching_dispatch_holds_not_runs(self):
        # A runtime mutation past the constructor guard still fails closed at the seam.
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("net.post:api.example",))
            s.leash_overrides = {"net_post": "ask"}
            dec = govern_action(s, _np())
            self.assertEqual(dec.status, HELD)

    def test_run_command_typo_leash_does_not_autorun(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("shell.exec",))
            s.leash_overrides = {"run_command": "ask_first"}
            dec = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            self.assertNotEqual(dec.status, RAN)

    def test_apply_cap_never_returns_an_unknown_string(self):
        from collaborator.policycaps import apply_cap
        self.assertEqual(apply_cap("propose-first", "notify_only"), "notify_only")
        self.assertEqual(apply_cap("act_then_report", "bogus"), "notify_only")
        self.assertEqual(apply_cap("bogus", None), "notify_only")

    def test_mint_rejects_an_invalid_leash_cap(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                mint(("net.post:api.example",), {"net_post": "notify"}, "admin",
                     workspace_subject(d), b"caps-key")


class AuthorityLensHardening(unittest.TestCase):
    """Regressions for the PR-A authority/F1 red-team (rt-authority-prA): the `autonomous` knob is
    literal-True only (F-1), autonomy needs source='host' as a SECOND barrier (F-5), and the loud
    hint is operator-directed + enforced + accurately worded (F-2/F-3)."""

    URL = "https://api.example/v1/x"

    def setUp(self):
        _CAPTURED.clear()

    def _full(self, d):
        return _granted(d, ("net.post:api.example", "net.post.auto:api.example"),
                        {"net_post": "act_then_report"})

    def test_autonomous_must_be_literal_true(self):  # F-1
        for val in ("false", "no", "0", 1, "yes", [1], "true"):   # truthy non-True must NOT emit
            with tempfile.TemporaryDirectory() as d:
                s = self._full(d)
                with mock.patch("collaborator.egress.post", _fake_post):
                    dec = emit(s, self.URL, "{}", autonomous=val)
                self.assertEqual(dec.status, HELD, repr(val))
                self.assertEqual(dec.leash, "propose_first", repr(val))

    def test_literal_true_still_emits(self):  # F-1 control
        with tempfile.TemporaryDirectory() as d:
            s = self._full(d)
            with mock.patch("collaborator.egress.post", _fake_post):
                dec = emit(s, self.URL, "{}", autonomous=True)
            self.assertEqual(dec.status, RAN)

    def test_keyword_leash_without_source_host_does_not_auto_lift(self):  # F-5
        # A model-shaped intent that somehow carried the keyword leash still can't auto-lift: autonomy
        # requires BOTH source='host' AND the keyword leash, two independent non-model-reachable barriers.
        with tempfile.TemporaryDirectory() as d:
            s = self._full(d)
            dec = govern_action(s, ToolIntent("net_post", {"url": self.URL, "body": "{}"},
                                              "structured"), leash=ATR)
            self.assertEqual(dec.status, HELD)
            self.assertEqual(dec.leash, "propose_first")

    def test_loud_hint_is_plain_for_a_model_originated_intent(self):  # F-3
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example", "net.post.auto:api.example"),
                         {"net_post": "notify_only"})
            dec = govern_action(s, ToolIntent("net_post", {"url": self.URL, "body": "{}"}, "structured"))
            self.assertEqual(dec.status, NOTIFIED)
            self.assertEqual(dec.reason, "notify-only leash")     # no operator-facing nudge from model output

    def test_loud_hint_is_plain_on_an_unsigned_session(self):  # F-2b
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("net.post:api.example", "net.post.auto:api.example"),
                        leash_overrides={"net_post": "notify_only"})
            dec = govern_action(s, ToolIntent("net_post", {"url": self.URL, "body": "{}"}, "structured"))
            self.assertEqual(dec.status, NOTIFIED)
            self.assertEqual(dec.reason, "notify-only leash")     # autonomy structurally unreachable -> no nudge

    def test_loud_hint_is_accurate_when_operator_directs_a_capped_tool(self):  # F-2a
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example", "net.post.auto:api.example"),
                         {"net_post": "notify_only"})   # operator deliberately capped net_post
            dec = govern_action(s, ToolIntent("net_post", {"url": self.URL, "body": "{}"}, "host"),
                                leash=ATR)
            self.assertEqual(dec.status, NOTIFIED)
            self.assertIn("not capped at act_then_report", dec.reason)
            self.assertNotIn("has no", dec.reason)                # never the false "no leash-cap" claim


class SealCredentialHardening(unittest.TestCase):
    """Regressions for the PR-A seal/credential red-team (rt-seal-prA): the credential must never
    re-enter the audit trail via a response echo (#1), approve() must read args ONCE (#2), a missing
    seal must fail closed (#3), the seal framing must be injective (#4/A5), and a held emission is
    bound to the session that held it (#5)."""

    URL = "https://api.example/v1/x"

    def setUp(self):
        _CAPTURED.clear()

    def _echo_post(self, url, body, *, content_type="application/json", auth=None,
                   keep_preview=False, **kw):
        # A granted-but-hostile / debug endpoint that ECHOES the Authorization header back.
        _CAPTURED.update(url=url, body=body, auth=auth, keep_preview=keep_preview)
        echoed = ('{"error":"bad","echo":{"Authorization":"%s"}}' % (auth or "")).encode("utf-8")
        return EgressResult(EgressRecord(
            canonical_dest="api.example", method="POST", request_target_hash="t",
            request_bytes=len(body), status=200, response_hash="r", response_len=len(echoed),
            redirect_location=None, resolved_ip="1.2.3.4", ok=True, request_body_hash="b",
            request_body_len=len(body),
            request_body_preview=(body[:50] if keep_preview else "")), body=echoed)

    def test_echoed_credential_is_redacted_from_output_and_summary(self):  # #1 HIGH
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example", "net.post.auto:api.example"),
                         {"net_post": "act_then_report"})
            s.egress_credentials = {"api.example": "Bearer sk-SECRET-9f3a"}
            with mock.patch("collaborator.egress.post", self._echo_post):
                dec = emit(s, self.URL, '{"m":"x"}', autonomous=True)
            self.assertEqual(dec.status, RAN)
            blob = (dec.result.output or "") + dec.summary()
            self.assertNotIn("sk-SECRET-9f3a", blob)          # neither the bare token...
            self.assertNotIn("Bearer sk-SECRET-9f3a", blob)   # ...nor the full header value
            self.assertIn("redacted", dec.result.output)

    def test_snapshot_reads_held_args_once_so_a_proxy_cannot_swap_the_wire(self):  # #2 MED-HIGH
        import collections.abc as abc

        class _Flip(abc.Mapping):
            """A proxy args view: returns the SEALED body on the first read, an EXFIL body after."""
            def __init__(self, base, key, second):
                self._d = dict(base); self._k = key; self._second = second; self._n = 0

            def __getitem__(self, k):
                if k == self._k:
                    self._n += 1
                    return self._d[k] if self._n == 1 else self._second
                return self._d[k]

            def __iter__(self):
                return iter(self._d)

            def __len__(self):
                return len(self._d)

        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example",), {"net_post": "propose_first"})
            held = emit(s, self.URL, '{"m":"SEALED"}')
            self.assertEqual(held.status, HELD)
            held.args = _Flip(held.args, "body", '{"m":"EXFIL"}')   # a second read would flip to EXFIL
            with mock.patch("collaborator.egress.post", _fake_post):
                out = approve(s, held)
            # read-once: the snapshot froze the FIRST body; the wire never saw the EXFIL swap.
            self.assertNotEqual(_CAPTURED.get("body"), '{"m":"EXFIL"}')
            self.assertEqual(out.status, RAN)

    def test_egress_held_with_empty_seal_fails_closed(self):  # #3 MED
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example",), {"net_post": "propose_first"})
            held = emit(s, self.URL, '{"m":"x"}')
            self.assertEqual(held.status, HELD)
            held.seal = ""                                    # tamper: clear the seal
            with mock.patch("collaborator.egress.post", _fake_post):  # must NOT be reached
                out = approve(s, held)
            self.assertEqual(out.status, DENIED)
            self.assertIn("no payload seal", out.reason)
            self.assertEqual(_CAPTURED, {})

    def test_seal_framing_is_injective_nul_shift_is_caught(self):  # #4 MED
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example",), {"net_post": "propose_first"})
            held = govern_action(s, ToolIntent("net_post", {"url": self.URL, "body": '{"model":"x"}',
                                                            "content_type": "x\x00y"}, "structured"))
            self.assertEqual(held.status, HELD)
            # Pre-fix, shifting the NUL across the content_type/body boundary collided to one seal.
            held.args["content_type"] = "x"
            held.args["body"] = 'y\x00{"model":"x"}'
            with mock.patch("collaborator.egress.post", _fake_post):
                out = approve(s, held)
            self.assertEqual(out.status, DENIED)
            self.assertIn("seal mismatch", out.reason)

    def test_str_body_and_its_bytes_swap_do_not_seal_alike(self):  # A5
        with tempfile.TemporaryDirectory() as d:
            s = _granted(d, ("net.post:api.example",), {"net_post": "propose_first"})
            held = govern_action(s, ToolIntent("net_post", {"url": self.URL, "body": "PAYLOAD"},
                                               "structured"))
            self.assertEqual(held.status, HELD)
            held.args["body"] = b"PAYLOAD"                    # swap str -> equivalent bytes
            with mock.patch("collaborator.egress.post", _fake_post):  # must NOT be reached
                out = approve(s, held)
            self.assertEqual(out.status, DENIED)
            self.assertIn("seal mismatch", out.reason)

    def test_cross_session_approval_is_refused(self):  # #5 MED
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            sA = _granted(d1, ("net.post:api.example",), {"net_post": "propose_first"})
            held = emit(sA, self.URL, '{"m":"x"}')            # held under session A (workspace d1)
            self.assertEqual(held.status, HELD)
            self.assertTrue(held.origin_subject)
            sB = _granted(d2, ("net.post:api.example",), {"net_post": "propose_first"})
            sB.egress_credentials = {"api.example": "Bearer sk-HIGH-PRIV"}
            with mock.patch("collaborator.egress.post", _fake_post):  # must NOT be reached
                out = approve(sB, held)                       # approve under session B (other subject)
            self.assertEqual(out.status, DENIED)
            self.assertIn("cross-session", out.reason)
            self.assertEqual(_CAPTURED, {})


if __name__ == "__main__":
    unittest.main()
