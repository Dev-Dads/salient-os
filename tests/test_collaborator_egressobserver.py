"""Thread #2 — the independent egress observer (ADR 0003 revisit #1b).

Cross-platform, hermetic tests of the pure logic (nft-JSON parse, /proc little-endian decode, the tri-state
reconcile, the begin/end window) + the SAFETY property that the module only ever creates/deletes its own
``table inet salient_obs`` and NEVER runs ``flush ruleset`` (so it cannot disturb a host firewall), plus a
Linux ``@skipUnless`` LIVE proof on real nft that a genuine egress reconciles clean while a stray second
connection is CAUGHT. The strong tier is real bwrap-style privileged infra, so the live guarantee is checked
where it can run; everything else runs everywhere.
"""

import subprocess
import sys
import unittest
from unittest.mock import patch

from collaborator import egressobserver as eo


class NftJsonParse(unittest.TestCase):
    def test_parses_real_dynamic_set_format(self):
        # the exact shape `nft -j list set` emits for a dynamic timeout set (captured live on Sparky)
        j = ('{"nftables":[{"metainfo":{}},{"set":{"name":"dests4","elem":['
             '{"elem":{"val":{"concat":["1.1.1.1",443]},"expires":299}},'
             '{"elem":{"val":{"concat":["8.8.8.8",80]}}}]}}]}')
        self.assertEqual(eo._parse_nft_set(j), {("1.1.1.1", 443), ("8.8.8.8", 80)})

    def test_parse_fails_closed_to_none(self):
        # malformed / schema-mismatch → None (UNCHECKED), never a silent empty that could mint a false clean
        self.assertIsNone(eo._parse_nft_set("not json"))
        self.assertIsNone(eo._parse_nft_set('{"nftables":[]}'))    # no set object = a failure, not "empty"
        # a valid, genuinely-empty set → empty set (a legitimate observation, distinct from a parse failure)
        self.assertEqual(eo._parse_nft_set('{"nftables":[{"set":{"name":"dests4"}}]}'), set())
        self.assertEqual(eo._parse_nft_set('{"nftables":[{"set":{"name":"dests4","elem":[]}}]}'), set())

    def test_present_but_undecodable_elements_are_none_not_empty(self):
        # qwen ID-03 (full close): a `set` object WITH elements that don't carry a 2-concat (nft JSON schema
        # skew, or an injected non-concat element) must fail CLOSED to None — an element we silently dropped
        # could be the very destination a discrepancy would flag. NOT an empty "observed nothing" reading.
        self.assertIsNone(eo._parse_nft_set('{"nftables":[{"set":{"name":"dests4","elem":["not-a-dict"]}}]}'))
        self.assertIsNone(eo._parse_nft_set(
            '{"nftables":[{"set":{"name":"dests4","elem":[{"elem":{"val":{"nope":[1,2]}}}]}}]}'))
        # a PARTIAL decode (one good element, one undecodable) is also UNCHECKED — never a partial truth
        self.assertIsNone(eo._parse_nft_set(
            '{"nftables":[{"set":{"name":"dests4","elem":['
            '{"elem":{"val":{"concat":["1.1.1.1",443]}}},{"bogus":true}]}}]}'))
        # a real element carrying a non-integer port is a malformed real element → UNCHECKED
        self.assertIsNone(eo._parse_nft_set(
            '{"nftables":[{"set":{"name":"dests4","elem":[{"elem":{"val":{"concat":["1.1.1.1","https"]}}}]}}]}'))
        # 'elem' present but not a list → schema mismatch → UNCHECKED
        self.assertIsNone(eo._parse_nft_set('{"nftables":[{"set":{"name":"dests4","elem":{"x":1}}}]}'))


class ProcDecode(unittest.TestCase):
    def test_ipv4_little_endian(self):
        self.assertEqual(eo._hex_to_endpoint("0100007F:0050"), ("127.0.0.1", 80))
        self.assertEqual(eo._hex_to_endpoint("04040808:01BB"), ("8.8.4.4", 443))

    def test_ipv6_and_garbage(self):
        # ::1 loopback, per-word little-endian; and a malformed row -> None (never raises)
        ep = eo._hex_to_endpoint("000000000000000000000000" + "01000000" + ":0050")
        self.assertEqual(ep, ("::1", 80))
        self.assertIsNone(eo._hex_to_endpoint("xyz"))


class Reconcile(unittest.TestCase):
    def _snap(self, dests, tier=eo.TIER_STRONG, n=0):
        return eo.EgressSnapshot(dests=frozenset(dests), conn_count=n, tier=tier)

    def test_strong_clean_is_true(self):
        r = eo.reconcile(self._snap([]), self._snap([("1.1.1.1", 443)], n=1), [("1.1.1.1", 443)])
        self.assertIs(r.reconciled, True)
        self.assertEqual(r.observed_conn_count, 1)

    def test_unexpected_dest_is_a_mismatch_at_any_tier(self):
        for tier in (eo.TIER_STRONG, eo.TIER_PROC):
            with self.subTest(tier=tier):
                r = eo.reconcile(self._snap([], tier), self._snap([("9.9.9.9", 443)], tier), [("1.1.1.1", 443)])
                self.assertIs(r.reconciled, False)
                self.assertIn(("9.9.9.9", 443), r.unexpected)

    def test_strong_claimed_but_unobserved_is_a_mismatch(self):
        r = eo.reconcile(self._snap([]), self._snap([]), [("1.1.1.1", 443)])
        self.assertIs(r.reconciled, False)
        self.assertIn(("1.1.1.1", 443), r.claimed_unobserved)

    def test_proc_clean_is_unchecked_not_a_false_confirm(self):
        # a racy /proc pass that saw no discrepancy must NOT claim "verified" — it is None (unchecked)
        r = eo.reconcile(self._snap([], eo.TIER_PROC), self._snap([], eo.TIER_PROC), [("1.1.1.1", 443)])
        self.assertIsNone(r.reconciled)

    def test_unavailable_is_unchecked(self):
        r = eo.reconcile(self._snap([], eo.TIER_UNAVAILABLE), self._snap([], eo.TIER_UNAVAILABLE), [])
        self.assertIsNone(r.reconciled)


class BeginEndWindow(unittest.TestCase):
    def test_strong_window_reconciles_against_injected_after(self):
        after = eo.EgressSnapshot(dests=frozenset({("1.1.1.1", 443)}), conn_count=1, tier=eo.TIER_STRONG)
        with patch.object(eo, "observer_available", return_value=eo.TIER_STRONG), \
             patch.object(eo, "install", return_value=True) as inst, \
             patch.object(eo, "_nft_snapshot", return_value=after), \
             patch.object(eo, "teardown") as td:
            before = eo.begin()
            self.assertEqual(before.tier, eo.TIER_STRONG)
            self.assertTrue(inst.called)                      # a fresh table was installed
            r = eo.end(before, [("1.1.1.1", 443)])
            self.assertIs(r.reconciled, True)
            self.assertTrue(td.called)                        # scoped teardown ran

    def test_strong_window_catches_a_second_client(self):
        after = eo.EgressSnapshot(dests=frozenset({("1.1.1.1", 443), ("8.8.8.8", 443)}), conn_count=2,
                                  tier=eo.TIER_STRONG)
        with patch.object(eo, "observer_available", return_value=eo.TIER_STRONG), \
             patch.object(eo, "install", return_value=True), \
             patch.object(eo, "_nft_snapshot", return_value=after), \
             patch.object(eo, "teardown"):
            r = eo.end(eo.begin(), [("1.1.1.1", 443)])
            self.assertIs(r.reconciled, False)
            self.assertIn(("8.8.8.8", 443), r.unexpected)


class BlastRadiusSafety(unittest.TestCase):
    """The module must NEVER run `flush ruleset` or touch any table but its own — else it could wipe a host
    firewall. Pin the exact nft argv it is allowed to emit."""

    def _capture(self, fn):
        calls = []

        def fake(args, **kw):
            calls.append(list(args))
            return subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")
        with patch.object(eo, "_run_nft", side_effect=fake), \
             patch.object(eo.sys, "platform", "linux"), \
             patch.object(eo, "_uid", return_value=1000):
            fn()
        return calls

    def test_teardown_only_deletes_our_table(self):
        calls = self._capture(eo.teardown)
        self.assertEqual(calls, [["delete", "table", "inet", "salient_obs"]])

    def test_install_never_flushes_and_scopes_to_our_table(self):
        calls = self._capture(eo.install)
        flat = " ".join(tok for c in calls for tok in c)
        self.assertNotIn("flush", flat)
        self.assertNotIn("ruleset", flat)
        # every table-affecting call names ONLY salient_obs; the create is `-f -` (stdin ruleset)
        for c in calls:
            if "table" in c:
                self.assertIn("salient_obs", c)
        self.assertTrue(any(c[:2] == ["-f", "-"] for c in calls))

    def test_ruleset_body_scopes_to_our_table_and_is_policy_accept(self):
        body = eo._nft_ruleset(1000)
        self.assertIn("table inet salient_obs", body)
        self.assertIn("meta skuid 1000", body)
        self.assertIn("policy accept", body)          # observational only — never drops a packet
        self.assertNotIn("flush", body)
        self.assertNotIn("drop", body)


class OffPlatformHonest(unittest.TestCase):
    @unittest.skipIf(sys.platform == "linux", "asserts the NON-Linux honest fallback")
    def test_no_vantage_off_linux(self):
        eo._reset_probe_cache_for_tests()
        self.assertEqual(eo.observer_available(), eo.TIER_UNAVAILABLE)
        self.assertFalse(eo.snapshot().observed())
        r = eo.end(eo.begin(), [("1.1.1.1", 443)])
        self.assertIsNone(r.reconciled)               # unchecked, never a false clean claim

    def test_nft_command_uses_sudo_when_unprivileged(self):
        # when not euid 0, nft is invoked through non-interactive sudo so a no-passwordless host fails the
        # probe (→ fallback) rather than hanging on a prompt
        self.assertIn(eo._NFT_BIN, eo._NFT)
        if eo._NFT[0] != eo._NFT_BIN:
            self.assertEqual(eo._NFT[:2], [eo._SUDO_BIN, "-n"])


class PanelFixes(unittest.TestCase):
    """External-panel-driven hardening (PR #40 certification): each fix pinned so it can't regress."""

    def test_ruleset_matches_all_tcp_not_just_syn(self):
        # F-01: matching every outbound TCP packet (not only the SYN) is what catches a pre-established
        # connection reused in-window — the rule must NOT be SYN-gated.
        body = eo._nft_ruleset(1000)
        self.assertIn("l4proto tcp", body)
        self.assertNotIn("syn", body)

    def test_snapshot_parse_failure_is_none_not_empty(self):
        # qwen ID-03: nft returns rc==0 but unparseable JSON → _nft_snapshot must be None (UNCHECKED),
        # never an empty-dests STRONG snapshot that could reconcile to a false True.
        ok = subprocess.CompletedProcess([], 0, stdout="garbage-not-json", stderr="")
        with patch.object(eo, "_run_nft", return_value=ok):
            self.assertIsNone(eo._nft_snapshot())

    def test_begin_unavailable_when_install_fails(self):
        # F-03: a failed install() must NOT leave a STRONG before-marker over a possibly-stale table.
        with patch.object(eo, "observer_available", return_value=eo.TIER_STRONG), \
             patch.object(eo, "install", return_value=False):
            self.assertEqual(eo.begin().tier, eo.TIER_UNAVAILABLE)

    def test_end_strong_read_failure_is_unchecked_and_distinct_from_no_vantage(self):
        # gpt-F2/qwen-ID01: the strong tier was set up but the read failed → reconciled None, tier STAYS
        # STRONG (an observer failure, honestly distinct from "this host has no vantage").
        with patch.object(eo, "_nft_snapshot", return_value=None), patch.object(eo, "teardown"):
            r = eo.end(eo.EgressSnapshot(tier=eo.TIER_STRONG), [("1.1.1.1", 443)])
            self.assertIsNone(r.reconciled)
            self.assertEqual(r.tier, eo.TIER_STRONG)
            self.assertIn("read failed", r.note)

    def test_ip_canonicalization_kills_ipv6_textform_false_discrepancy(self):
        # F-05: an expanded-form observed IPv6 must reconcile clean against a compressed claimed IPv6.
        after = eo.EgressSnapshot(dests=frozenset({("2001:db8:0:0:0:0:0:1", 443)}), tier=eo.TIER_STRONG)
        r = eo.reconcile(eo.EgressSnapshot(tier=eo.TIER_STRONG), after, [("2001:db8::1", 443)])
        self.assertIs(r.reconciled, True)

    def test_dest_set_scope_dup_claim_is_true_but_hidden_new_dest_is_false(self):
        # gpt-F1: the verdict is over the DESTINATION SET, not connection multiplicity (the strong counter is
        # PACKETS, not connections). A record over-claiming a repeat connection to an ALREADY-observed dest is
        # NOT concealed egress → True. The dangerous direction (bytes to a HIDDEN, un-claimed dest) is a NEW
        # dest and MUST be caught as unexpected → False. This pins that the scope choice is safe.
        before = eo.EgressSnapshot(tier=eo.TIER_STRONG)
        # over-claim: record claims two connections to the one observed dest; kernel saw the dest → True
        dup = eo.reconcile(before, eo.EgressSnapshot(dests=frozenset({("1.1.1.1", 443)}), conn_count=1,
                           tier=eo.TIER_STRONG), [("1.1.1.1", 443), ("1.1.1.1", 443)])
        self.assertIs(dup.reconciled, True)
        # concealment: a second, un-claimed destination the record hid → False, surfaced as unexpected
        hid = eo.reconcile(before, eo.EgressSnapshot(dests=frozenset({("1.1.1.1", 443), ("9.9.9.9", 443)}),
                           conn_count=2, tier=eo.TIER_STRONG), [("1.1.1.1", 443)])
        self.assertIs(hid.reconciled, False)
        self.assertIn(("9.9.9.9", 443), hid.unexpected)

    def test_transient_none_is_not_cached_but_positive_is(self):
        # gpt-F4/F6, grok-F-04: a transient 'none' at first probe must NOT pin the observer off for the
        # process lifetime; only a POSITIVE tier is cached. (Non-Linux caches UNAVAILABLE permanently — the
        # platform is immutable — so this behaviour is asserted on the Linux probe path.)
        if sys.platform != "linux":
            self.skipTest("Linux-only self-heal path")
        eo._reset_probe_cache_for_tests()
        try:
            with patch.object(eo, "install", return_value=False), \
                 patch.object(eo, "_proc_snapshot", return_value=None):
                self.assertEqual(eo.observer_available(), eo.TIER_UNAVAILABLE)
                self.assertIsNone(eo._available_tier)          # NOT cached → a later probe can self-heal
            with patch.object(eo, "install", return_value=False), \
                 patch.object(eo, "_proc_snapshot",
                              return_value=eo.EgressSnapshot(tier=eo.TIER_PROC)):
                self.assertEqual(eo.observer_available(), eo.TIER_PROC)
                self.assertEqual(eo._available_tier, eo.TIER_PROC)   # positive result IS cached
        finally:
            eo._reset_probe_cache_for_tests()


class SeamWiring(unittest.TestCase):
    """The reconcile verdict rides Execution → Decision → summary(), honestly per tier — evidence, never a
    deny (the RAN status/cleared do not change on a discrepancy)."""

    def _decision(self, **kw):
        from collaborator.governance import Decision, RAN
        from collaborator.tools import ToolResult
        return Decision("a", "web_fetch", RAN, "egress h [200]", "propose_first",
                        cleared=True, result=ToolResult(ok=True, output="X"), egress=object(), **kw)

    def test_summary_reflects_each_reconcile_tier(self):
        self.assertIn("world-observed (reconciled)", self._decision(egress_reconciled=True).summary())
        bad = self._decision(egress_reconciled=False, egress_discrepancy="observed [('9.9.9.9', 443)]").summary()
        self.assertIn("EGRESS DISCREPANCY", bad)
        self.assertIn("9.9.9.9", bad)
        self.assertIn("observation unavailable", self._decision(egress_reconciled=None).summary())

    def test_discrepancy_is_evidence_not_a_deny(self):
        # a mismatch is surfaced but the action still RAN and stayed cleared — reconciliation informs, P-01
        d = self._decision(egress_reconciled=False, egress_discrepancy="x")
        from collaborator.governance import RAN
        self.assertEqual(d.status, RAN)
        self.assertTrue(d.cleared)

    def test_exec_web_fetch_attaches_reconcile_and_claims_resolved_ip(self):
        import types
        from collaborator import tools
        rec = types.SimpleNamespace(ok=True, status=200, canonical_dest="h", response_len=3,
                                    truncated=False, error="", resolved_ip="1.2.3.4")
        result = types.SimpleNamespace(record=rec, text=lambda n: "body")
        obs = eo.ReconcileResult(reconciled=True, tier=eo.TIER_STRONG, note="ok")
        with patch.object(tools.egress, "fetch", return_value=result), \
             patch.object(tools.egressobserver, "begin", return_value=eo.EgressSnapshot(tier=eo.TIER_STRONG)), \
             patch.object(tools.egressobserver, "end", return_value=obs) as end:
            ex = tools._exec_web_fetch("/ws", {"url": "https://h/"})
            self.assertIs(ex.egress_obs, obs)
            self.assertEqual(end.call_args.args[1], [("1.2.3.4", 443)])   # claimed = resolved IP on 443

    def test_egress_claimed_empty_when_unresolved(self):
        import types
        from collaborator.tools import _egress_claimed
        self.assertEqual(_egress_claimed(types.SimpleNamespace(resolved_ip="1.2.3.4")), [("1.2.3.4", 443)])
        self.assertEqual(_egress_claimed(types.SimpleNamespace(resolved_ip=None)), [])


@unittest.skipUnless(eo.observer_available() == eo.TIER_STRONG,
                     "strong nft egress observer unavailable on this host")
class EgressObserverProofLinux(unittest.TestCase):
    """LIVE: real nft OUTPUT hook independently observes egress and catches a stray second connection."""

    @staticmethod
    def _connect(ip, port=443):
        import socket, ssl
        try:
            ctx = ssl._create_unverified_context()
            s = ctx.wrap_socket(socket.create_connection((ip, port), timeout=8), server_hostname="x")
            s.close()
        except OSError:
            pass

    def test_legit_egress_is_independently_observed_and_accounted_for(self):
        # The guarantee for OUR egress: its dest is INDEPENDENTLY observed by the kernel vantage AND is NOT
        # mis-flagged as unexpected against our own claim. We do NOT assert machine-wide `reconciled is True`
        # here: matching ALL outbound TCP (the F-01 fix) means any SAME-UID co-tenant egress in the window
        # (e.g. a CI runner agent's own connections) legitimately makes `reconciled=False` — the documented
        # same-uid co-tenant residual, not a fault in our egress's reconciliation. That per-dest property is
        # robust on a shared-uid host; `reconciled is True` is proven reachable separately below.
        before = eo.begin()
        self._connect("1.1.1.1", 443)
        r = eo.end(before, [("1.1.1.1", 443)])
        self.assertIsNotNone(r.reconciled)                     # strong vantage produced a verdict (not None)
        self.assertIn(("1.1.1.1", 443), r.observed_dests)      # the kernel really saw our connection
        self.assertNotIn(("1.1.1.1", 443), r.unexpected)       # our claimed dest is accounted for, not mis-flagged

    def test_a_quiet_window_can_reconcile_true(self):
        # `reconciled is True` IS reachable when the uid's window is quiet. Retry to ride out bursty same-uid
        # co-tenant egress on a shared runner (the residual); one clean window proves True works. If no window
        # is quiet in several tries (a very busy shared-uid host), skip honestly rather than flake — the
        # per-dest guarantee above already carries the load-bearing proof.
        for _ in range(8):
            before = eo.begin()
            self._connect("1.1.1.1", 443)
            r = eo.end(before, [("1.1.1.1", 443)])
            if r.reconciled is True:
                self.assertIn(("1.1.1.1", 443), r.observed_dests)
                return
        self.skipTest("no quiet same-uid window in 8 tries (busy shared-uid host) — co-tenant residual")

    def test_stray_second_connection_is_caught(self):
        before = eo.begin()
        self._connect("8.8.8.8", 443)                 # a dest the "record" will NOT claim
        r = eo.end(before, [("1.1.1.1", 443)])
        self.assertIs(r.reconciled, False)
        self.assertIn(("8.8.8.8", 443), r.unexpected)

    def test_teardown_leaves_no_table(self):
        eo.install()
        eo.teardown()
        r = eo._run_nft(["list", "table", "inet", "salient_obs"])
        self.assertNotEqual(r.returncode, 0)          # our table is gone


if __name__ == "__main__":
    sys.exit(unittest.main())
