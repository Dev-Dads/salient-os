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

    def putrequest(self, method, target):
        self.sent["method"] = method
        self.sent["target"] = target

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


class Capability(unittest.TestCase):
    def test_capability_string(self):
        self.assertEqual(egress_capability("example.com"), "net.get:example.com")

    def test_required_capability_from_url(self):
        self.assertEqual(required_capability("https://docs.python.org/3/"), "net.get:docs.python.org")

    def test_required_capability_none_on_ineligible(self):
        self.assertIsNone(required_capability("http://example.com/"))
        self.assertIsNone(required_capability("https://a@b.com/"))


class SafePublicIP(unittest.TestCase):
    def test_blocks_the_dangerous_ranges(self):
        for ip in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1",
                   "169.254.169.254",  # cloud metadata
                   "0.0.0.0", "224.0.0.1", "::1", "fd00::1", "fe80::1"):
            self.assertFalse(is_safe_public_ip(ip), ip)

    def test_allows_global_unicast(self):
        for ip in ("8.8.8.8", "93.184.216.34", "1.1.1.1"):
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

    def test_query_length_capped(self):
        big = "https://docs.example/x?d=" + ("A" * (egress.MAX_URL_QUERY + 1))
        r = fetch(big, resolver=lambda h: ["93.184.216.34"],
                  connection_factory=_factory(_FakeResp(200), []))
        self.assertFalse(r.record.ok)
        self.assertIn("query exceeds cap", r.record.error)

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


if __name__ == "__main__":
    unittest.main()
