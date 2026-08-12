"""Cross-session DURABLE emission-quota COUNTER store (ADR 0003 residual-sweep follow-up).

The per-destination emission quota bounded HOW MANY emissions may go to a host, but the counter was
session-lived, so a RESTART reset it and a drip channel could refresh its budget. This persists the
counter across sessions, integrity-protected + out-of-workspace. Pins: round-trip; missing=fresh;
tamper/wrong-key/subject-mismatch/corrupt => UNTRUSTED and FAIL-CLOSED (a restrictive bound must not be
loosened by a store it can't trust); cross-session persistence closes the restart-reset gap; domain
separation from the provenance store; no store => unchanged in-memory behaviour.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collaborator import emissionquotastore as eqs
from collaborator import provenancestore as ps
from collaborator.egress import EgressRecord, EgressResult
from collaborator.governance import DENIED, RAN, govern_action
from collaborator.policycaps import mint, workspace_subject
from collaborator.session import Session
from collaborator.toolcall import ToolIntent

KEY = b"collab-policy-key"           # Session's default policy_key
CAPS_KEY = b"caps-key"
SUBJ = "workspace:abc"
NP_URL = "https://api.example/v1/x"


def _fake_post(url, body, *, content_type="application/json", auth=None, keep_preview=False, **kw):
    return EgressResult(EgressRecord(
        canonical_dest="api.example", method="POST", request_target_hash="th", request_bytes=3,
        status=200, response_hash="rh", response_len=2, redirect_location=None,
        resolved_ip="93.184.216.34", ok=True, request_body_hash="bh", request_body_len=len(body),
        request_body_preview=""), body=b"ok")


class StoreUnit(unittest.TestCase):
    def _f(self, d):
        return str(Path(d) / "emq.json")

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._f(d)
            self.assertTrue(eqs.save(path, SUBJ, KEY, {"api.example": 3, "b.example": 1}))
            counts, ok = eqs.load(path, SUBJ, KEY)
            self.assertTrue(ok)
            self.assertEqual(counts, {"api.example": 3, "b.example": 1})

    def test_missing_is_fresh_trusted(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(eqs.load(self._f(d), SUBJ, KEY), ({}, True))

    def test_tampered_count_is_untrusted(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._f(d)
            eqs.save(path, SUBJ, KEY, {"api.example": 3})
            doc = json.loads(Path(path).read_text())
            doc["body"]["counts"]["api.example"] = 0       # roll the count back WITHOUT re-MACing
            Path(path).write_text(json.dumps(doc))
            self.assertEqual(eqs.load(path, SUBJ, KEY), ({}, False))

    def test_wrong_key_and_subject_are_untrusted(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._f(d)
            eqs.save(path, "workspace:AAA", KEY, {"api.example": 2})
            self.assertEqual(eqs.load(path, "workspace:AAA", b"other-key"), ({}, False))
            self.assertEqual(eqs.load(path, "workspace:BBB", KEY), ({}, False))

    def test_corrupt_is_untrusted_not_a_raise(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._f(d)
            Path(path).write_text("{nope")
            self.assertEqual(eqs.load(path, SUBJ, KEY), ({}, False))

    def test_negative_count_is_untrusted_not_silently_filtered(self):
        # External panel gemini: a validly-signed store with a NEGATIVE count must be UNTRUSTED (fail
        # closed), never verify-then-filter into a trusted-empty (budget refresh).
        with tempfile.TemporaryDirectory() as d:
            path = self._f(d)
            eqs.save(path, SUBJ, KEY, {"api.example": -5})     # a valid MAC over a negative count
            self.assertEqual(eqs.load(path, SUBJ, KEY), ({}, False))

    def test_domain_separation_from_provenance_store(self):
        # a PROVENANCE store file must NOT verify as an emission-quota store (distinct MAC domain)
        with tempfile.TemporaryDirectory() as d:
            path = self._f(d)
            ps.save(path, SUBJ, KEY, {"build.sh"}, False)   # write a PROVENANCE store to the same path
            counts, ok = eqs.load(path, SUBJ, KEY)
            self.assertFalse(ok)                              # cross-kind load is untrusted
            self.assertEqual(counts, {})


class SessionDurability(unittest.TestCase):
    def _ws_store(self, root):
        ws = Path(root) / "ws"
        ws.mkdir()
        return str(ws), str(Path(root) / "emq.json")

    def test_store_inside_workspace_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            ws, _ = self._ws_store(root)
            with self.assertRaises(ValueError):
                Session(workspace=ws, emission_quota_store=str(Path(ws) / "emq.json"))

    def test_count_persists_across_sessions(self):
        with tempfile.TemporaryDirectory() as root:
            ws, store = self._ws_store(root)
            s1 = Session(workspace=ws, emission_quota={"api.example": 2}, emission_quota_store=store)
            self.assertTrue(s1.emission_allowed("api.example")); s1.consume_emission("api.example")
            self.assertTrue(s1.emission_allowed("api.example")); s1.consume_emission("api.example")
            # a fresh session over the SAME workspace + store inherits the used budget -> now exhausted
            s2 = Session(workspace=ws, emission_quota={"api.example": 2}, emission_quota_store=store)
            self.assertEqual(s2._emission_counts.get("api.example"), 2)
            self.assertFalse(s2.emission_allowed("api.example"))     # restart did NOT reset the quota

    def test_untrusted_store_fails_closed_for_limited_hosts(self):
        with tempfile.TemporaryDirectory() as root:
            ws, store = self._ws_store(root)
            Session(workspace=ws, emission_quota={"api.example": 5},
                    emission_quota_store=store).consume_emission("api.example")
            Path(store).write_text("corrupt")                        # agent can't cause this; disk can
            s = Session(workspace=ws, emission_quota={"api.example": 5}, emission_quota_store=store)
            self.assertTrue(s._emission_store_untrusted)
            self.assertFalse(s.emission_allowed("api.example"))      # LIMITED host -> denied (fail closed)
            self.assertTrue(s.emission_allowed("free.example"))      # UNLIMITED host -> unaffected

    def test_save_failure_trips_fail_closed_deny(self):
        # External panel (grok F1/opus): for a RESTRICTIVE bound a persist FAILURE must fail closed, not
        # silently degrade to session-lived (a restart would reload a stale lower count -> budget refresh).
        with tempfile.TemporaryDirectory() as root:
            ws, store = self._ws_store(root)
            s = Session(workspace=ws, emission_quota={"api.example": 5}, emission_quota_store=store)
            with mock.patch("collaborator.emissionquotastore.save", return_value=False):
                s.consume_emission("api.example")
            self.assertTrue(s._emission_store_untrusted)
            self.assertFalse(s.emission_allowed("api.example"))     # limited host now denied

    def test_untrusted_store_is_not_self_healed_by_a_consume(self):
        # External panel (grok F2): while untrusted, a consume (e.g. an unlimited host) must NOT overwrite
        # the corrupt store with a clean low-count one — that would silently refresh the budget for the
        # next session. The store stays untrusted until the operator resolves it.
        with tempfile.TemporaryDirectory() as root:
            ws, store = self._ws_store(root)
            Session(workspace=ws, emission_quota={"api.example": 3},
                    emission_quota_store=store).consume_emission("api.example")
            corrupt = "corrupt-bytes"
            Path(store).write_text(corrupt)
            s = Session(workspace=ws, emission_quota={"api.example": 3}, emission_quota_store=store)
            self.assertTrue(s._emission_store_untrusted)
            s.consume_emission("free.example")                     # an unlimited host still emits...
            self.assertEqual(Path(store).read_text(), corrupt)     # ...but does NOT overwrite the store
            # a fresh session still sees the untrusted store (no silent self-heal)
            self.assertTrue(Session(workspace=ws, emission_quota={"api.example": 3},
                                    emission_quota_store=store)._emission_store_untrusted)

    def test_no_store_is_in_memory_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            ws, _ = self._ws_store(root)
            s = Session(workspace=ws, emission_quota=1)
            self.assertIsNone(s._emission_store)
            self.assertFalse(s._emission_store_untrusted)
            self.assertTrue(s.emission_allowed("api.example")); s.consume_emission("api.example")
            self.assertFalse(s.emission_allowed("api.example"))

    def test_end_to_end_quota_survives_a_restart(self):
        # the dispatch-point consume persists; a NEW session denies the over-quota emission (the drip
        # channel cannot refresh its budget by restarting).
        with tempfile.TemporaryDirectory() as root:
            ws, store = self._ws_store(root)

            def _auto(tmp):
                signed = mint(("net.post:api.example", "net.post.auto:api.example"),
                              {"net_post": "act_then_report"}, "admin", workspace_subject(tmp), CAPS_KEY)
                return Session(workspace=tmp, policy_caps=signed, caps_key=CAPS_KEY,
                               emission_quota={"api.example": 1}, emission_quota_store=store)

            def _emit(s):
                return govern_action(s, ToolIntent("net_post", {"url": NP_URL, "body": '{"m":"x"}'}, "host"),
                                     leash="act_then_report")

            with mock.patch("collaborator.egress.post", _fake_post):
                self.assertEqual(_emit(_auto(ws)).status, RAN)       # session 1: 1 emission (quota=1)
                over = _emit(_auto(ws))                               # session 2 (restart): over quota
            self.assertEqual(over.status, DENIED)
            self.assertIn("quota exhausted", over.reason)


if __name__ == "__main__":
    unittest.main()
