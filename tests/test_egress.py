"""The mediated egress client (ADR 0003 Tier 1) — the transport-level safety contract.

Every CRITICAL finding from the ADR 0003 design panel lives here: authorize-one/connect-
another (canonical host == connect host), redirects fail closed, DNS-rebind / SSRF-to-
metadata (resolve once, safety-check the IP, pin it), HTTPS-only, and GET-as-exfil bounds.
The guards are pure functions; the fetch path injects a fake resolver + connection so the
whole contract is exercised without touching the network.
"""

import unittest

from collaborator import egress
from collaborator.egress import (
    canonical_host,
    egress_capability,
    fetch,
    is_safe_public_ip,
    required_capability,
)


class _FakeResp:
    def __init__(self, status, body=b"", headers=None):
        self.status = status
        self._body = body
        self._headers = headers or {}

    def getheader(self, key, default=None):
        return self._headers.get(key, default)

    def read(self, n=-1):
        return self._body if (n is None or n < 0) else self._body[:n]


class _FakeConn:
    """Records the request and returns a scripted response; captures the connect target."""

    def __init__(self, host, pinned_ip, resp, sink):
        self.host = host
        self.pinned_ip = pinned_ip
        self._resp = resp
        self.sent = {"headers": {}}
        sink.append(self)

    def putrequest(self, method, target, skip_host=False, **kw):
        self.sent["method"] = method
        self.sent["target"] = target
        self.sent["skip_host"] = skip_host

    def putheader(self, key, value):
        self.sent["headers"][key] = value

    def endheaders(self):
        pass

    def getresponse(self):
        return self._resp

    def close(self):
        pass


def _factory(resp, sink):
    return lambda host, pinned_ip: _FakeConn(host, pinned_ip, resp, sink)


class CanonicalHost(unittest.TestCase):
    def test_https_lowercased(self):
        self.assertEqual(canonical_host("https://EXAMPLE.COM/path"), "example.com")

    def test_http_refused(self):
        self.assertIsNone(canonical_host("http://example.com/"))

    def test_bare_host_refused(self):
        self.assertIsNone(canonical_host("example.com"))

    def test_userinfo_refused(self):
        # authorize-one / connect-another: urlsplit's host is evil.com, but we refuse ANY userinfo
        self.assertIsNone(canonical_host("https://docs.python.org@evil.com/"))
        self.assertIsNone(canonical_host("https://user:pass@evil.com/"))

    def test_trailing_dot_stripped(self):
        self.assertEqual(canonical_host("https://example.com./"), "example.com")

    def test_explicit_default_port_ok_other_port_refused(self):
        self.assertEqual(canonical_host("https://example.com:443/"), "example.com")
        self.assertIsNone(canonical_host("https://example.com:8443/"))

    def test_idn_to_punycode(self):
        self.assertEqual(canonical_host("https://bücher.example/"), "xn--bcher-kva.example")

    def test_homograph_distinct_from_ascii(self):
        # Cyrillic 'а' (U+0430) must NOT canonicalize to the ASCII 'apple.com' grant.
        cyr = canonical_host("https://аpple.com/")
        self.assertNotEqual(cyr, "apple.com")

    def test_already_punycode_passthrough(self):
        self.assertEqual(canonical_host("https://xn--bcher-kva.example/"), "xn--bcher-kva.example")

    def test_ipv6_literal_refused(self):
        self.assertIsNone(canonical_host("https://[::1]/"))

    def test_empty_and_garbage(self):
        for bad in ("", "   ", None, "https://", "https://@/", "ftp://example.com/"):
            self.assertIsNone(canonical_host(bad))

    def test_dotless_and_numeric_ip_forms_refused(self):
        # Dotless numeric/hex host forms (decimal/hex IP literals, single-label junk) are refused;
        # a dotted-quad literal and a real FQDN pass (then the resolved IP is safety-checked).
        for bad in ("https://2130706433/", "https://0x7f000001/", "https://2852039166/",
                    "https://localhost/"):
            self.assertIsNone(canonical_host(bad), bad)
        self.assertEqual(canonical_host("https://93.184.216.34/"), "93.184.216.34")


class Capability(unittest.TestCase):
    def test_capability_string(self):
        self.assertEqual(egress_capability("example.com"), "net.get:example.com")

    def test_required_capability_from_url(self):
        self.assertEqual(required_capability("https://docs.python.org/3/"), "net.get:docs.python.org")

    def test_required_capability_none_on_ineligible(self):
        self.assertIsNone(required_capability("http://example.com/"))
        self.assertIsNone(required_capability("https://a@b.com/"))

    def test_required_capability_method_aware(self):
        # ADR 0003 Tier 2: reading a host and emitting to it are SEPARATE capability namespaces.
        self.assertEqual(required_capability("https://api.example/x", "GET"), "net.get:api.example")
        self.assertEqual(required_capability("https://api.example/x", "POST"), "net.post:api.example")
        self.assertEqual(required_capability("https://api.example/x"), "net.get:api.example")  # default GET

    def test_net_get_and_net_post_are_distinct_namespaces(self):
        g = required_capability("https://api.example/", "GET")
        p = required_capability("https://api.example/", "POST")
        self.assertNotEqual(g, p)
        self.assertTrue(p.startswith(egress.EGRESS_POST_CAP_PREFIX))
        self.assertTrue(g.startswith(egress.EGRESS_CAP_PREFIX))


class SafePublicIP(unittest.TestCase):
    def test_blocks_the_dangerous_ranges(self):
        for ip in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1",
                   "169.254.169.254",  # cloud metadata
                   "0.0.0.0", "224.0.0.1", "::1", "fd00::1", "fe80::1"):
            self.assertFalse(is_safe_public_ip(ip), ip)

    def test_blocks_cgnat_mapped_and_nat64(self):
        # Red-team: CGNAT/shared space (Tailscale tailnet), IPv4-mapped IPv6 (version-independent),
        # and NAT64 must all fail closed — the guards the boolean denylist alone missed.
        for ip in ("100.64.0.1", "100.127.255.255",           # RFC6598 CGNAT / tailnet
                   "::ffff:169.254.169.254", "::ffff:10.0.0.1", "::ffff:127.0.0.1",  # IPv4-mapped
                   "64:ff9b::a9fe:a9fe"):                       # NAT64 -> 169.254.169.254
            self.assertFalse(is_safe_public_ip(ip), ip)

    def test_allows_global_unicast(self):
        for ip in ("8.8.8.8", "93.184.216.34", "1.1.1.1", "2606:4700:4700::1111"):
            self.assertTrue(is_safe_public_ip(ip), ip)

    def test_garbage_is_unsafe(self):
        self.assertFalse(is_safe_public_ip("not-an-ip"))
        self.assertFalse(is_safe_public_ip(""))


class Fetch(unittest.TestCase):
    def test_ineligible_url_refused_before_network(self):
        calls = []
        r = fetch("http://example.com/", resolver=lambda h: (_ for _ in ()).throw(AssertionError("resolved!")),
                  connection_factory=_factory(_FakeResp(200), calls))
        self.assertFalse(r.record.ok)
        self.assertEqual(calls, [])  # never resolved, never connected

    def test_private_ip_blocked(self):
        r = fetch("https://rebind.example/", resolver=lambda h: ["10.0.0.5"],
                  connection_factory=_factory(_FakeResp(200), []))
        self.assertFalse(r.record.ok)
        self.assertIn("no safe public IP", r.record.error)

    def test_metadata_ip_blocked(self):
        r = fetch("https://rebind.example/", resolver=lambda h: ["169.254.169.254"],
                  connection_factory=_factory(_FakeResp(200), []))
        self.assertFalse(r.record.ok)

    def test_picks_the_safe_ip_and_pins_it(self):
        sink = []
        r = fetch("https://docs.example/x", resolver=lambda h: ["10.0.0.1", "93.184.216.34"],
                  connection_factory=_factory(_FakeResp(200, b"hi"), sink))
        self.assertTrue(r.record.ok)
        self.assertEqual(r.record.resolved_ip, "93.184.216.34")   # pinned the safe one
        self.assertEqual(sink[0].pinned_ip, "93.184.216.34")       # connected to the pinned IP
        self.assertEqual(sink[0].host, "docs.example")             # SNI/cert = canonical host

    def test_redirect_fails_closed(self):
        r = fetch("https://docs.example/x", resolver=lambda h: ["93.184.216.34"],
                  connection_factory=_factory(_FakeResp(302, headers={"Location": "https://evil.com/"}), []))
        self.assertFalse(r.record.ok)
        self.assertEqual(r.record.status, 302)
        self.assertEqual(r.record.redirect_location, "https://evil.com/")
        self.assertIn("redirect not followed", r.record.error)

    def test_success_returns_body_and_hashes_no_auth_header(self):
        sink = []
        r = fetch("https://docs.example/page?q=1", resolver=lambda h: ["93.184.216.34"],
                  connection_factory=_factory(_FakeResp(200, b"payload"), sink))
        self.assertTrue(r.record.ok)
        self.assertEqual(r.text(), "payload")
        self.assertEqual(r.record.response_len, len(b"payload"))
        self.assertIsNotNone(r.record.response_hash)
        self.assertEqual(sink[0].sent["headers"].get("Host"), "docs.example")
        self.assertNotIn("Authorization", sink[0].sent["headers"])
        self.assertNotIn("Cookie", sink[0].sent["headers"])

    def test_request_target_length_capped_query(self):
        big = "https://docs.example/x?d=" + ("A" * (egress.MAX_URL_TARGET + 1))
        r = fetch(big, resolver=lambda h: ["93.184.216.34"],
                  connection_factory=_factory(_FakeResp(200), []))
        self.assertFalse(r.record.ok)
        self.assertIn("request target exceeds cap", r.record.error)

    def test_request_target_length_capped_path(self):
        # The PATH exfils identically to the query — capping only the query was security theater.
        big = "https://docs.example/" + ("B" * (egress.MAX_URL_TARGET + 1))
        r = fetch(big, resolver=lambda h: ["93.184.216.34"],
                  connection_factory=_factory(_FakeResp(200), []))
        self.assertFalse(r.record.ok)
        self.assertIn("request target exceeds cap", r.record.error)

    def test_response_truncated_at_ceiling(self):
        body = b"Z" * (egress.DEFAULT_MAX_RESPONSE + 100)
        r = fetch("https://docs.example/big", resolver=lambda h: ["93.184.216.34"],
                  max_response=egress.DEFAULT_MAX_RESPONSE,
                  connection_factory=_factory(_FakeResp(200, body), []))
        self.assertTrue(r.record.ok)
        self.assertTrue(r.record.truncated)
        self.assertEqual(r.record.response_len, egress.DEFAULT_MAX_RESPONSE)

    def test_resolve_failure_fails_closed(self):
        def boom(host):
            raise OSError("dns down")
        r = fetch("https://docs.example/", resolver=boom, connection_factory=_factory(_FakeResp(200), []))
        self.assertFalse(r.record.ok)
        self.assertIn("resolve failed", r.record.error)

    def test_get_uses_skip_host_single_host_header(self):
        # M3: skip_host=True so we set exactly ONE canonical Host header (not a duplicate).
        sink = []
        r = fetch("https://docs.example/x", resolver=lambda h: ["93.184.216.34"],
                  connection_factory=_factory(_FakeResp(200, b"ok"), sink))
        self.assertTrue(r.record.ok)
        self.assertTrue(sink[0].sent["skip_host"])
        self.assertEqual(sink[0].sent["headers"].get("Host"), "docs.example")


class _FakePostConn:
    """Like _FakeConn but for the POST path: accepts skip_host, and captures the sent body."""

    def __init__(self, host, pinned_ip, resp, sink):
        self.host = host
        self.pinned_ip = pinned_ip
        self._resp = resp
        self.sent = {"headers": {}}
        self.body = b""
        sink.append(self)

    def putrequest(self, method, target, skip_host=False, **kw):
        self.sent["method"] = method
        self.sent["target"] = target
        self.sent["skip_host"] = skip_host

    def putheader(self, key, value):
        self.sent["headers"][key] = value

    def endheaders(self):
        pass

    def send(self, data):
        self.body += data

    def getresponse(self):
        return self._resp

    def close(self):
        pass


def _post_factory(resp, sink):
    return lambda host, pinned_ip: _FakePostConn(host, pinned_ip, resp, sink)


class Post(unittest.TestCase):
    """ADR 0003 Tier 2 — the mediated EMISSION path reuses the whole Tier-1 contract and adds a
    capped/hashed body, host-injected (never model, never logged) credentials, and a
    body-free-vs-preview audit split."""

    def _ok(self, url="https://api.example/x", body='{"a":1}', **kw):
        sink = []
        r = egress.post(url, body, resolver=lambda h: ["93.184.216.34"],
                        connection_factory=_post_factory(_FakeResp(200, b'{"ok":true}'), sink), **kw)
        return r, sink

    def test_ineligible_url_refused_before_network(self):
        for bad in ("http://api.example/", "https://u@api.example/", "https://api.example:8443/"):
            r = egress.post(bad, "{}",
                            resolver=lambda h: (_ for _ in ()).throw(AssertionError("resolved!")),
                            connection_factory=_post_factory(_FakeResp(200), []))
            self.assertFalse(r.record.ok, bad)
            self.assertEqual(r.record.method, "POST")

    def test_body_must_be_str_or_bytes(self):
        r = egress.post("https://api.example/", {"a": 1}, resolver=lambda h: ["93.184.216.34"],
                        connection_factory=_post_factory(_FakeResp(200), []))
        self.assertFalse(r.record.ok)
        self.assertIn("body must be str or bytes", r.record.error)

    def test_body_cap(self):
        r = egress.post("https://api.example/", "x" * (egress.MAX_POST_BODY + 1),
                        resolver=lambda h: ["93.184.216.34"],
                        connection_factory=_post_factory(_FakeResp(200), []))
        self.assertFalse(r.record.ok)
        self.assertIn("body exceeds cap", r.record.error)
        self.assertEqual(r.record.request_body_len, egress.MAX_POST_BODY + 1)

    def test_content_type_header_injection_rejected(self):
        r = egress.post("https://api.example/", "{}", content_type="application/json\r\nX-Evil: 1",
                        resolver=lambda h: ["93.184.216.34"],
                        connection_factory=_post_factory(_FakeResp(200), []))
        self.assertFalse(r.record.ok)
        self.assertIn("content-type", r.record.error)

    def test_auth_value_injection_rejected(self):
        r = egress.post("https://api.example/", "{}", auth="Bearer x\r\nX-Evil: 1",
                        resolver=lambda h: ["93.184.216.34"],
                        connection_factory=_post_factory(_FakeResp(200), []))
        self.assertFalse(r.record.ok)
        self.assertIn("authorization", r.record.error)

    def test_private_and_metadata_ip_blocked(self):
        for ip in ("10.0.0.5", "169.254.169.254", "100.64.0.1"):
            r = egress.post("https://rebind.example/", "{}", resolver=lambda h, ip=ip: [ip],
                            connection_factory=_post_factory(_FakeResp(200), []))
            self.assertFalse(r.record.ok, ip)

    def test_pins_safe_ip_and_canonical_host(self):
        r, sink = self._ok()
        self.assertTrue(r.record.ok)
        self.assertEqual(r.record.resolved_ip, "93.184.216.34")
        self.assertEqual(sink[0].pinned_ip, "93.184.216.34")   # connected to the pinned safe IP
        self.assertEqual(sink[0].host, "api.example")          # SNI/cert = canonical host
        self.assertTrue(sink[0].sent["skip_host"])             # single canonical Host header
        self.assertEqual(sink[0].sent["headers"].get("Host"), "api.example")

    def test_body_sent_exactly_and_content_length_matches(self):
        r, sink = self._ok(body="hello-world")
        self.assertEqual(sink[0].body, b"hello-world")          # byte-identical to args (no re-encode)
        self.assertEqual(sink[0].sent["headers"].get("Content-Length"), str(len(b"hello-world")))
        self.assertEqual(sink[0].sent["method"], "POST")

    def test_host_injected_auth_sent_no_cookie(self):
        r, sink = self._ok(auth="Bearer sk-secret")
        self.assertEqual(sink[0].sent["headers"].get("Authorization"), "Bearer sk-secret")
        self.assertNotIn("Cookie", sink[0].sent["headers"])

    def test_no_auth_means_no_authorization_header(self):
        r, sink = self._ok()
        self.assertNotIn("Authorization", sink[0].sent["headers"])

    def test_auth_and_preview_never_logged_in_record(self):
        # The audit record is body-free-by-default and NEVER carries the credential.
        r, sink = self._ok(auth="Bearer sk-secret", keep_preview=True, body="payload")
        blob = repr(r.record)
        self.assertNotIn("sk-secret", blob)
        self.assertNotIn("Authorization", blob)

    def test_redirect_fails_closed_no_second_connection(self):
        sink = []
        r = egress.post("https://api.example/", "payload", auth="Bearer sk",
                        resolver=lambda h: ["93.184.216.34"],
                        connection_factory=_post_factory(
                            _FakeResp(302, headers={"Location": "https://evil.com/"}), sink))
        self.assertFalse(r.record.ok)
        self.assertEqual(r.record.status, 302)
        self.assertEqual(r.record.redirect_location, "https://evil.com/")
        self.assertIn("redirect not followed", r.record.error)
        self.assertEqual(len(sink), 1)  # NEVER opened a second connection to re-POST to the target

    def test_record_body_free_by_default(self):
        r, sink = self._ok(body="secret-payload", keep_preview=False)
        self.assertTrue(r.record.request_body_hash)              # linkable by hash
        self.assertEqual(r.record.request_body_len, len(b"secret-payload"))
        self.assertEqual(r.record.request_body_preview, "")      # body-free (autonomous path)

    def test_record_preview_when_kept(self):
        r, sink = self._ok(body="secret-payload", keep_preview=True)
        self.assertEqual(r.record.request_body_preview, "secret-payload")  # human-gated path

    def test_preview_is_bounded(self):
        big = "P" * (egress._BODY_PREVIEW_BYTES + 50)
        r, sink = self._ok(body=big, keep_preview=True)
        self.assertEqual(len(r.record.request_body_preview), egress._BODY_PREVIEW_BYTES)

    def test_success_returns_response_body_hashed(self):
        r, sink = self._ok()
        self.assertTrue(r.record.ok)
        self.assertEqual(r.text(), '{"ok":true}')
        self.assertIsNotNone(r.record.response_hash)
        self.assertEqual(r.record.method, "POST")

    def test_non_ascii_or_control_or_oversize_content_type_refused(self):
        # panel: a non-latin-1 content_type would raise UnicodeEncodeError out of putheader; now
        # a clean refusal (never an exception escaping post()).
        for ct in ("application/json☃", "app/json\r\nX: 1", "x" * 300):
            r = egress.post("https://api.example/", "{}", content_type=ct,
                            resolver=lambda h: ["93.184.216.34"],
                            connection_factory=_post_factory(_FakeResp(200), []))
            self.assertFalse(r.record.ok, ct)

    def test_non_ascii_or_control_auth_refused(self):
        for a in ("Bearer ☃", "Bearer x\r\nX: 1"):
            r = egress.post("https://api.example/", "{}", auth=a,
                            resolver=lambda h: ["93.184.216.34"],
                            connection_factory=_post_factory(_FakeResp(200), []))
            self.assertFalse(r.record.ok, a)

    def test_illegal_request_target_refused_never_raises(self):
        # control / NUL / non-ascii in the path or query -> clean refusal, never an exception
        for u in ("https://api.example/☃", "https://api.example/a\x00b",
                  "https://api.example/x?q=☃"):
            r = egress.post(u, "{}", resolver=lambda h: ["93.184.216.34"],
                            connection_factory=_post_factory(_FakeResp(200), []))
            self.assertFalse(r.record.ok, u)
            self.assertIn("request target", r.record.error)

    def test_lone_surrogate_body_refused_never_raises(self):
        # S1: a lone surrogate is legal JSON (survives a model tool-call) but not utf-8-encodable.
        r = egress.post("https://api.example/", "\ud800", resolver=lambda h: ["93.184.216.34"],
                        connection_factory=_post_factory(_FakeResp(200), []))
        self.assertFalse(r.record.ok)
        self.assertIn("utf-8", r.record.error)

    def test_content_type_is_recorded_in_audit(self):
        # C1: the content-type rides the wire, so the channel-integrity record must carry it.
        r, sink = self._ok(content_type="application/json; x=marker")
        self.assertEqual(r.record.request_content_type, "application/json; x=marker")
        self.assertEqual(sink[0].sent["headers"].get("Content-Type"), "application/json; x=marker")

    def test_c1_control_char_content_type_refused(self):
        # C1: U+0085 (NEL) is latin-1 but non-ASCII -> refused (would otherwise reach the wire).
        r = egress.post("https://api.example/", "{}", content_type="app/json\x85evil",
                        resolver=lambda h: ["93.184.216.34"],
                        connection_factory=_post_factory(_FakeResp(200), []))
        self.assertFalse(r.record.ok)

    def test_over_cap_refusal_keeps_body_hash(self):
        # M4: an over-cap refusal is still linkable to what was attempted (hash + length).
        r = egress.post("https://api.example/", "x" * (egress.MAX_POST_BODY + 1),
                        resolver=lambda h: ["93.184.216.34"],
                        connection_factory=_post_factory(_FakeResp(200), []))
        self.assertFalse(r.record.ok)
        self.assertTrue(r.record.request_body_hash)
        self.assertEqual(r.record.request_body_len, egress.MAX_POST_BODY + 1)

    def test_empty_auth_is_no_credential_not_a_refusal(self):
        # M5: an empty host credential means "send no Authorization", not "refuse the emission".
        r, sink = self._ok(auth="")
        self.assertTrue(r.record.ok)
        self.assertNotIn("Authorization", sink[0].sent["headers"])

    def test_redirect_location_bounded_and_sanitized(self):
        # M1: a huge / CRLF-bearing Location must not enter the audit unbounded or unsanitized.
        loc = "https://evil/" + ("A" * 60000) + "\r\nFORGED: 1"
        r = egress.post("https://api.example/", "{}", resolver=lambda h: ["93.184.216.34"],
                        connection_factory=_post_factory(
                            _FakeResp(302, headers={"Location": loc}), []))
        self.assertFalse(r.record.ok)
        self.assertLessEqual(len(r.record.redirect_location), egress.MAX_URL_TARGET)
        self.assertNotIn("\r", r.record.redirect_location)
        self.assertNotIn("\n", r.record.redirect_location)


class EmissionSeal(unittest.TestCase):
    """The hold-time seal that binds an approved emission to what actually gets sent (Tier 2 has
    no verifier — panel: approved != sent)."""

    def test_seal_stable_and_sensitive_to_every_consequential_field(self):
        base = egress.emission_seal("https://api.example/pay", '{"amt":10}', "application/json")
        self.assertEqual(base, egress.emission_seal("https://api.example/pay", '{"amt":10}',
                                                    "application/json"))
        self.assertNotEqual(base, egress.emission_seal("https://evil.example/pay", '{"amt":10}',
                                                       "application/json"))   # host
        self.assertNotEqual(base, egress.emission_seal("https://api.example/steal", '{"amt":10}',
                                                       "application/json"))   # target
        self.assertNotEqual(base, egress.emission_seal("https://api.example/pay", '{"amt":9999}',
                                                       "application/json"))   # body
        self.assertNotEqual(base, egress.emission_seal("https://api.example/pay", '{"amt":10}',
                                                       "text/plain"))          # content-type

    def test_seal_canonicalizes_host_no_false_mismatch(self):
        # a benign host-case difference is the SAME destination -> same seal (no false denial)
        self.assertEqual(egress.emission_seal("https://API.Example/x", "b"),
                         egress.emission_seal("https://api.example/x", "b"))


if __name__ == "__main__":
    unittest.main()
