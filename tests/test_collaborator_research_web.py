"""ADR 0003: web_research lifts to allowlisted read-only GET WITH injection floors.

A web read is not a workspace read: it is default-deny (the host must be granted
net.get:<canonical-host>), and its bytes are tagged UNTRUSTED (adversary-controlled) so an
injected "do X next" cannot pass as trusted context. Perception only — grants no authority.
"""

import json
import tempfile
import unittest
from unittest import mock

from collaborator.egress import EgressRecord, EgressResult
from collaborator.research import _web_get_finding, run_research
from collaborator.session import Session


def _resp(body=b"HELLO", dest="docs.example", status=200, ok=True):
    return EgressResult(EgressRecord(
        canonical_dest=dest, method="GET", request_target_hash="th", request_bytes=3,
        status=status, response_hash="rh", response_len=len(body), redirect_location=None,
        resolved_ip="93.184.216.34", ok=ok, error=("" if ok else "boom")), body=body)


class _Scripted:
    def __init__(self, msgs):
        self._q = list(msgs)

    def complete(self, messages, tools=None):
        return self._q.pop(0) if self._q else {"content": '{"done": true}'}


class WebGetFinding(unittest.TestCase):
    def test_refused_when_web_not_enabled(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("net.get:docs.example",))  # read_only_research
            self.assertIn("web research not enabled", _web_get_finding(s, "https://docs.example/x"))

    def test_refused_ineligible_url(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("net.get:docs.example",),
                        research_trust="web_research")
            self.assertIn("ineligible web url", _web_get_finding(s, "http://docs.example/x"))

    def test_default_deny_non_allowlisted(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=(), research_trust="web_research")
            out = _web_get_finding(s, "https://docs.example/x")
            self.assertIn("not allowlisted", out)
            self.assertIn("default-deny", out)

    def test_allowlisted_get_is_tagged_untrusted(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("net.get:docs.example",),
                        research_trust="web_research")
            with mock.patch("collaborator.egress.fetch", lambda url, **k: _resp(b"HELLO")):
                out = _web_get_finding(s, "https://docs.example/x")
            self.assertIn("UNTRUSTED WEB CONTENT", out)
            self.assertIn("HELLO", out)

    def test_failed_fetch_surfaced(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("net.get:docs.example",),
                        research_trust="web_research")
            with mock.patch("collaborator.egress.fetch", lambda url, **k: _resp(ok=False)):
                out = _web_get_finding(s, "https://docs.example/x")
            self.assertIn("failed", out)


class RunResearchWithWeb(unittest.TestCase):
    def test_end_to_end_web_get_then_done(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("net.get:docs.example",),
                        research_trust="web_research")
            client = _Scripted([
                {"content": json.dumps({"read": {"name": "web_get",
                                                 "arguments": {"url": "https://docs.example/api"}}})},
                {"content": json.dumps({"done": True})},
            ])
            with mock.patch("collaborator.egress.fetch", lambda url, **k: _resp(b"DOCBODY")):
                findings = run_research(s, client, "ctx", budget=3)
            self.assertEqual(len(findings), 1)
            self.assertIn("UNTRUSTED WEB CONTENT", findings[0])
            self.assertIn("DOCBODY", findings[0])


if __name__ == "__main__":
    unittest.main()
