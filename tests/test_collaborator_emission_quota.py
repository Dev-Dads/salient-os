"""ADR 0003 residual sweep — per-DESTINATION emission quota + method->cap fail-closed.

The egress caps bound WHERE an emission may go and the byte/time caps bound HOW BIG each is, but
nothing bounded HOW MANY: a granted net.post:<host> (or an autonomous net.post.auto:<host>) could
emit to that host unbounded. This caps the emission COUNT per canonical host, checked + consumed at
the single dispatch point both the autonomous and human-approved paths reach (fail closed). Plus:
required_capability now FAILS CLOSED on a write verb (PUT/DELETE/...) instead of silently mapping it
to the READ cap.
"""

import tempfile
import unittest
from unittest import mock

from collaborator import egress
from collaborator.governance import DENIED, FAILED, HELD, RAN, govern_action
from collaborator.loop import approve
from collaborator.policycaps import mint, workspace_subject
from collaborator.session import Session
from collaborator.toolcall import ToolIntent

NP_URL = "https://api.example/v1/x"
KEY = b"caps-key"


def _fake_post(url, body, *, content_type="application/json", auth=None, keep_preview=False, **kw):
    from collaborator.egress import EgressRecord, EgressResult
    return EgressResult(EgressRecord(
        canonical_dest="api.example", method="POST", request_target_hash="th", request_bytes=3,
        status=200, response_hash="rh", response_len=2, redirect_location=None,
        resolved_ip="93.184.216.34", ok=True, request_body_hash="bh", request_body_len=len(body),
        request_body_preview=""), body=b"ok")


def _auto_quota(tmp, quota, auto_host="api.example"):
    signed = mint(("net.post:api.example", f"net.post.auto:{auto_host}"),
                  {"net_post": "act_then_report"}, "admin", workspace_subject(tmp), KEY)
    return Session(workspace=tmp, policy_caps=signed, caps_key=KEY, emission_quota=quota)


def _emit(s):
    # A HOST-DIRECTED autonomous emission (source='host' + keyword leash) — reaches execute_and_verify.
    return govern_action(s, ToolIntent("net_post", {"url": NP_URL, "body": '{"m":"x"}'}, "host"),
                         leash="act_then_report")


class QuotaConfigValidation(unittest.TestCase):
    def test_accepts_none_int_and_dict(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(Session(workspace=d, emission_quota=None).emission_quota)
            self.assertEqual(Session(workspace=d, emission_quota=5).emission_quota, 5)
            self.assertEqual(Session(workspace=d, emission_quota={"api.example": 3}).emission_quota,
                             {"api.example": 3})

    def test_dict_keys_are_canonicalized_so_a_mixed_case_key_still_applies(self):
        # External-panel finding: a verbatim key would silently never match the canonicalized runtime
        # host. Keys are canonicalized at construction so a natural mixed-case key DOES bound the host.
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, emission_quota={"API.Example": 1})
            self.assertEqual(s.emission_quota, {"api.example": 1})
            self.assertTrue(s.emission_allowed("api.example"))            # 0 consumed -> allowed
            s.consume_emission("api.example")
            self.assertFalse(s.emission_allowed("api.example"))           # now capped at the canonical host

    def test_rejects_malformed_quota_loudly(self):
        with tempfile.TemporaryDirectory() as d:
            for bad in (-1, True, {"api.example": -1}, {"api.example": True}, {5: 3}, "3", 1.5,
                        {"api.example": "3"},
                        {"nodot": 1},                       # not a valid canonical host -> loud, not silent
                        {"API.Example": 1, "api.example": 2}):  # two keys collide to one canonical host
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    Session(workspace=d, emission_quota=bad)


class QuotaCounting(unittest.TestCase):
    def test_unlimited_when_none(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, emission_quota=None)
            for _ in range(100):
                self.assertTrue(s.emission_allowed("api.example"))
                s.consume_emission("api.example")

    def test_global_int_caps_every_host_independently(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, emission_quota=2)
            for host in ("a.example", "b.example"):        # each host has its OWN count
                self.assertTrue(s.emission_allowed(host)); s.consume_emission(host)
                self.assertTrue(s.emission_allowed(host)); s.consume_emission(host)
                self.assertFalse(s.emission_allowed(host))  # third to THIS host blocked

    def test_dict_caps_listed_host_only(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, emission_quota={"limited.example": 1})
            self.assertTrue(s.emission_allowed("limited.example")); s.consume_emission("limited.example")
            self.assertFalse(s.emission_allowed("limited.example"))   # listed host: capped
            for _ in range(10):                                       # unlisted host: unlimited
                self.assertTrue(s.emission_allowed("free.example")); s.consume_emission("free.example")

    def test_none_host_is_always_allowed(self):
        # An ineligible URL canonicalizes to None; the egress gate denies it upstream, so this method
        # must never be the thing that (dis)allows a None host — it only ever ADDS a bound.
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, emission_quota=0)
            self.assertTrue(s.emission_allowed(None))


class MethodCapFailClosed(unittest.TestCase):
    def test_reads_map_to_net_get(self):
        for m in ("GET", "get", "HEAD"):
            self.assertEqual(egress.required_capability("https://h.example/x", m), "net.get:h.example")

    def test_post_maps_to_net_post(self):
        self.assertEqual(egress.required_capability("https://h.example/x", "POST"), "net.post:h.example")

    def test_write_verbs_fail_closed_never_the_read_cap(self):
        # PUT/DELETE/PATCH (no tool issues them today) must DENY, not inherit the READ cap.
        for m in ("PUT", "DELETE", "PATCH", "OPTIONS", "TRACE", "bogus"):
            self.assertIsNone(egress.required_capability("https://h.example/x", m), m)

    def test_empty_or_none_method_defaults_to_read(self):
        # An absent method (None / "") is a READ by default, unchanged behaviour — not a write verb.
        for m in (None, ""):
            self.assertEqual(egress.required_capability("https://h.example/x", m), "net.get:h.example")

    def test_ineligible_url_is_none_regardless_of_method(self):
        self.assertIsNone(egress.required_capability("http://h.example/x", "GET"))   # not https


class QuotaEndToEnd(unittest.TestCase):
    def test_quota_blocks_after_n_autonomous_emissions(self):
        with tempfile.TemporaryDirectory() as d:
            s = _auto_quota(d, {"api.example": 2})
            with mock.patch("collaborator.egress.post", _fake_post):
                self.assertEqual(_emit(s).status, RAN)       # 1
                self.assertEqual(_emit(s).status, RAN)       # 2
                over = _emit(s)                              # 3 -> over quota
            self.assertEqual(over.status, DENIED)
            self.assertIn("quota exhausted", over.reason)

    def test_none_quota_is_unlimited(self):
        with tempfile.TemporaryDirectory() as d:
            s = _auto_quota(d, None)
            with mock.patch("collaborator.egress.post", _fake_post):
                for _ in range(6):
                    self.assertEqual(_emit(s).status, RAN)

    def test_approve_path_enforces_and_consumes_quota(self):
        # External-panel F1 (gpt, refuted): a HELD emission does NOT bypass the quota — approve() routes
        # through the SAME execute_and_verify dispatch point, so it both CHECKS and CONSUMES there.
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d,
                        policy_caps=mint(("net.post:api.example",), {"net_post": "propose_first"},
                                         "admin", workspace_subject(d), KEY),
                        caps_key=KEY, emission_quota={"api.example": 1})
            intent = ToolIntent("net_post", {"url": NP_URL, "body": "{}"}, "structured")
            with mock.patch("collaborator.egress.post", _fake_post):
                held1 = govern_action(s, intent)
                self.assertEqual(held1.status, HELD)                 # propose_first -> held, nothing consumed yet
                self.assertEqual(approve(s, held1).status, RAN)      # consumed at dispatch
                held2 = govern_action(s, intent)
                self.assertEqual(held2.status, HELD)
                over = approve(s, held2)                             # over quota, enforced on the approve path
            self.assertEqual(over.status, DENIED)
            self.assertIn("quota exhausted", over.reason)

    def test_failed_emission_still_consumes_quota(self):
        # Consume happens right BEFORE dispatch, so a failing attempt (a retry channel) still burns quota.
        with tempfile.TemporaryDirectory() as d:
            s = _auto_quota(d, {"api.example": 1})

            def _boom(*a, **k):
                raise RuntimeError("network down")
            with mock.patch("collaborator.egress.post", _boom):
                failed = _emit(s)
            self.assertEqual(failed.status, FAILED)                       # the emission failed...
            self.assertEqual(s._emission_counts.get("api.example"), 1)    # ...but the attempt was counted
            with mock.patch("collaborator.egress.post", _fake_post):
                self.assertEqual(_emit(s).status, DENIED)                 # quota now exhausted


if __name__ == "__main__":
    unittest.main()
