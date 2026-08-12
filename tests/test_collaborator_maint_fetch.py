"""ADR 0006 — maint_fetch: a mediated MAINTENANCE artifact fetch, STREAMED to a workspace file.

The design panel's chosen alternative to a privileged CONNECT proxy: a human-gated GET that pulls a
non-vendorable artifact (proprietary driver, licensed binary) through egress.py's proven transport
contract and stages it on disk under a host byte ceiling — the shell stays routeless. This exercises:
the SEPARATE net.maint:<host> authority namespace (a read/emit grant never confers it), the streaming
fail-CLOSED over-cap refusal (no truncated/oversized artifact ever staged), the workspace fence on the
dest, the reused safety contract (redirect / unsafe-IP / ineligible-URL all refuse), and the
approved==executed seal over (url, dest).
"""

import hashlib
import io
import os
import tempfile
import unittest
from unittest import mock

from collaborator import egress, tools
from collaborator.egress import (
    DEFAULT_MAINT_MAX_BYTES,
    EGRESS_MAINT_CAP_PREFIX,
    EgressRecord,
    EgressResult,
    canonical_host,
    fetch_to_file,
    required_capability,
)
from collaborator.governance import DENIED, FAILED, HELD, RAN, govern_action
from collaborator.loop import approve
from collaborator.policycaps import mint, workspace_subject
from collaborator.session import Session
from collaborator.tools import (
    SEALED_TOOLS,
    WorkspaceError,
    _exec_maint_fetch,
    freeze_args,
    held_action_seal,
)
from collaborator.toolcall import ToolIntent

_SAFE = ["93.184.216.34"]


def _can_symlink():
    try:
        with tempfile.TemporaryDirectory() as _d:
            os.symlink(os.path.join(_d, "t"), os.path.join(_d, "l"))
        return True
    except (OSError, NotImplementedError, AttributeError):
        return False


_CAN_SYMLINK = _can_symlink()


# --- a STREAMING fake response (advances a cursor + bounds each read; test_egress._FakeResp does
#     neither, so the chunked reader in fetch_to_file would infinite-loop against it) --------------
class _StreamResp:
    def __init__(self, status, body=b"", headers=None, chunk_size=None):
        self.status = status
        self._body = body
        self._pos = 0
        self._headers = headers or {}
        self._chunk_size = chunk_size   # cap each read (simulate small TCP reads) or None = up to n

    def getheader(self, key, default=None):
        return self._headers.get(key, default)

    def read(self, n=-1):
        remaining = len(self._body) - self._pos
        if n is None or n < 0:
            take = remaining
        else:
            take = min(n, remaining)
            if self._chunk_size is not None:
                take = min(take, self._chunk_size)
        chunk = self._body[self._pos:self._pos + take]
        self._pos += take
        return chunk


class _Conn:
    def __init__(self, host, pinned, resp):
        self.host = host
        self.pinned = pinned
        self._resp = resp
        self.sent = {"headers": {}}

    def putrequest(self, method, target, skip_host=False, **kw):
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


def _factory(resp):
    return lambda host, pinned: _Conn(host, pinned, resp)


# ============================ capability derivation (separate namespace) ============================

class CapDerivation(unittest.TestCase):
    def test_maint_kind_yields_net_maint_cap(self):
        self.assertEqual(required_capability("https://docs.example/d.deb", "MAINT"),
                         "net.maint:docs.example")

    def test_maint_is_a_separate_namespace_from_get_and_post(self):
        url = "https://docs.example/d.deb"
        self.assertEqual(required_capability(url, "GET"), "net.get:docs.example")
        self.assertEqual(required_capability(url, "POST"), "net.post:docs.example")
        self.assertEqual(required_capability(url, "MAINT"), "net.maint:docs.example")
        # a maint cap is not a read cap and not an emit cap — three disjoint authorities
        self.assertNotEqual(required_capability(url, "MAINT"), required_capability(url, "GET"))
        self.assertNotEqual(required_capability(url, "MAINT"), required_capability(url, "POST"))

    def test_ineligible_url_yields_no_cap(self):
        self.assertIsNone(required_capability("http://docs.example/d.deb", "MAINT"))       # not https
        self.assertIsNone(required_capability("https://docs.example@evil/d", "MAINT"))     # userinfo
        self.assertIsNone(required_capability("https://docs.example:8443/d", "MAINT"))     # non-443


# ================================ streaming transport contract =====================================

class FetchToFileTransport(unittest.TestCase):
    def test_streams_body_to_sink_ok_record(self):
        body = b"a-real-driver-blob"
        sink = io.BytesIO()
        r = fetch_to_file("https://docs.example/d.deb", sink, resolver=lambda h: _SAFE,
                          connection_factory=_factory(_StreamResp(200, body)))
        self.assertTrue(r.record.ok)
        self.assertEqual(sink.getvalue(), body)                       # exact bytes staged
        self.assertEqual(r.record.response_len, len(body))
        self.assertEqual(r.record.response_hash, hashlib.sha256(body).hexdigest())
        self.assertEqual(r.body, b"")                                 # body NEVER returned to the model
        self.assertEqual(r.record.canonical_dest, "docs.example")

    def test_multi_chunk_stream_reassembles_exactly(self):
        body = b"0123456789" * 50                                     # 500 bytes
        sink = io.BytesIO()
        r = fetch_to_file("https://docs.example/big.bin", sink, resolver=lambda h: _SAFE,
                          connection_factory=_factory(_StreamResp(200, body, chunk_size=7)))
        self.assertTrue(r.record.ok)
        self.assertEqual(sink.getvalue(), body)

    def test_over_cap_fails_closed_single_read(self):
        body = b"0123456789"                                          # 10 bytes
        sink = io.BytesIO()
        r = fetch_to_file("https://docs.example/big.bin", sink, max_bytes=4, resolver=lambda h: _SAFE,
                          connection_factory=_factory(_StreamResp(200, body)))
        self.assertFalse(r.record.ok)
        self.assertIn("exceeds cap", r.record.error)
        self.assertEqual(sink.getvalue(), b"")                        # nothing staged (refused before write)

    def test_over_cap_stops_mid_stream_no_full_write(self):
        body = b"0123456789"                                          # 10 bytes, read 4 at a time
        sink = io.BytesIO()
        r = fetch_to_file("https://docs.example/big.bin", sink, max_bytes=6, resolver=lambda h: _SAFE,
                          connection_factory=_factory(_StreamResp(200, body, chunk_size=4)))
        self.assertFalse(r.record.ok)
        self.assertIn("exceeds cap", r.record.error)
        self.assertLessEqual(len(sink.getvalue()), 6)                 # partial only; never the full artifact
        self.assertNotEqual(sink.getvalue(), body)

    def test_redirect_fails_closed(self):
        sink = io.BytesIO()
        r = fetch_to_file("https://docs.example/d", sink, resolver=lambda h: _SAFE,
                          connection_factory=_factory(
                              _StreamResp(302, b"x", headers={"Location": "https://evil.example/"})))
        self.assertFalse(r.record.ok)
        self.assertIn("redirect not followed", r.record.error)
        self.assertEqual(sink.getvalue(), b"")

    def test_non_2xx_refused_not_staged(self):
        sink = io.BytesIO()
        r = fetch_to_file("https://docs.example/d", sink, resolver=lambda h: _SAFE,
                          connection_factory=_factory(_StreamResp(404, b"not found")))
        self.assertFalse(r.record.ok)
        self.assertIn("status 404", r.record.error)
        self.assertEqual(sink.getvalue(), b"")

    def test_unsafe_ip_refused(self):
        for bad in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "100.64.0.1"):
            sink = io.BytesIO()
            r = fetch_to_file("https://docs.example/d", sink, resolver=lambda h: [bad],
                              connection_factory=_factory(_StreamResp(200, b"x")))
            self.assertFalse(r.record.ok, bad)
            self.assertIn("no safe public IP", r.record.error)
            self.assertEqual(sink.getvalue(), b"")

    def test_ineligible_url_refused_without_connecting(self):
        sink = io.BytesIO()
        boom = lambda h: (_ for _ in ()).throw(AssertionError("must not resolve an ineligible URL"))
        r = fetch_to_file("http://docs.example/d", sink, resolver=boom,
                          connection_factory=_factory(_StreamResp(200, b"x")))
        self.assertFalse(r.record.ok)
        self.assertEqual(sink.getvalue(), b"")

    def test_resolve_failure_fails_closed(self):
        sink = io.BytesIO()
        boom = lambda h: (_ for _ in ()).throw(OSError("dns down"))
        r = fetch_to_file("https://docs.example/d", sink, resolver=boom,
                          connection_factory=_factory(_StreamResp(200, b"x")))
        self.assertFalse(r.record.ok)
        self.assertIn("resolve failed", r.record.error)

    def test_never_returns_none_or_raises_on_junk(self):
        for url in ("", "not a url", "https://", "ftp://x/y"):
            sink = io.BytesIO()
            r = fetch_to_file(url, sink, resolver=lambda h: _SAFE,
                              connection_factory=_factory(_StreamResp(200, b"x")))
            self.assertIsInstance(r, EgressResult)
            self.assertFalse(r.record.ok)


# ==================================== the tool executor ============================================

def _fake_ftf(body, *, ok=True, status=200, err="artifact exceeds cap", partial=b""):
    """A fake egress.fetch_to_file that writes to the sink and returns a scripted record."""
    def _f(url, sink, *, max_bytes=DEFAULT_MAINT_MAX_BYTES, **kw):
        host = canonical_host(url) or ""
        if ok:
            sink.write(body)
            rec = EgressRecord(canonical_dest=host, method="GET", request_target_hash="th",
                               request_bytes=1, status=status,
                               response_hash=hashlib.sha256(body).hexdigest(), response_len=len(body),
                               redirect_location=None, resolved_ip="1.2.3.4", ok=True)
            return EgressResult(rec, body=b"")
        sink.write(partial)                                          # simulate an over-cap partial write
        rec = EgressRecord(canonical_dest=host, method="GET", request_target_hash="th",
                           request_bytes=1, status=status, response_hash=None, response_len=0,
                           redirect_location=None, resolved_ip="1.2.3.4", ok=False, error=err)
        return EgressResult(rec, body=b"")
    return _f


class Executor(unittest.TestCase):
    def test_ok_stages_file_in_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            body = b"driver-bytes"
            with mock.patch("collaborator.egress.fetch_to_file", _fake_ftf(body)):
                ex = _exec_maint_fetch(d, {"url": "https://docs.example/d.deb", "dest": "drivers/d.deb"})
            self.assertTrue(ex.result.ok)
            path = os.path.join(d, "drivers", "d.deb")
            self.assertTrue(os.path.exists(path))
            with open(path, "rb") as f:
                self.assertEqual(f.read(), body)
            self.assertEqual(ex.egress.response_hash, hashlib.sha256(body).hexdigest())

    def test_non_ok_deletes_partial_artifact(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("collaborator.egress.fetch_to_file",
                            _fake_ftf(b"", ok=False, partial=b"HALF")):
                ex = _exec_maint_fetch(d, {"url": "https://docs.example/big", "dest": "big.bin"})
            self.assertFalse(ex.result.ok)
            self.assertFalse(os.path.exists(os.path.join(d, "big.bin")))   # fail closed: no staged bytes
            self.assertIsNotNone(ex.egress)                                # a record is always attached

    def test_dest_escaping_workspace_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(WorkspaceError):
                _exec_maint_fetch(d, {"url": "https://docs.example/d", "dest": "../escape.deb"})

    def test_failure_leaves_no_dest_and_no_temp(self):
        # atomic staging: an over-cap/failed fetch stages to a temp; dest never appears and no temp lingers
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("collaborator.egress.fetch_to_file",
                            _fake_ftf(b"", ok=False, partial=b"HALF")):
                ex = _exec_maint_fetch(d, {"url": "https://docs.example/big", "dest": "big.bin"})
            self.assertFalse(ex.result.ok)
            self.assertFalse(os.path.exists(os.path.join(d, "big.bin")))
            self.assertEqual([f for f in os.listdir(d) if f.startswith(".maintfetch-")], [])

    def test_success_leaves_no_temp(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("collaborator.egress.fetch_to_file", _fake_ftf(b"ok-blob")):
                ex = _exec_maint_fetch(d, {"url": "https://docs.example/d", "dest": "d.bin"})
            self.assertTrue(ex.result.ok)
            self.assertEqual(open(os.path.join(d, "d.bin"), "rb").read(), b"ok-blob")
            self.assertEqual([f for f in os.listdir(d) if f.startswith(".maintfetch-")], [])

    def test_dest_is_a_directory_fails_cleanly(self):
        # gemini LOW: dest resolving to a directory -> os.replace fails -> clean non-ok, no crash
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "adir"))
            with mock.patch("collaborator.egress.fetch_to_file", _fake_ftf(b"blob")):
                ex = _exec_maint_fetch(d, {"url": "https://docs.example/d", "dest": "adir"})
            self.assertFalse(ex.result.ok)
            self.assertIn("publish failed", ex.egress.error)

    @unittest.skipUnless(_CAN_SYMLINK, "symlinks unavailable on this platform")
    def test_preplanted_symlink_dest_is_caught_by_the_fence(self):
        # reproduced on Sparky: resolve_in_workspace FOLLOWS then containment-checks, so a pre-planted
        # symlink at dest pointing OUTSIDE the workspace is a WorkspaceError -> DENY (qwen ID-7 false pos).
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            secret = os.path.join(outside, "secret")
            with open(secret, "w") as f:
                f.write("OUTSIDE")
            os.symlink(secret, os.path.join(d, "link.bin"))
            with self.assertRaises(WorkspaceError):
                _exec_maint_fetch(d, {"url": "https://docs.example/d", "dest": "link.bin"})
            self.assertEqual(open(secret).read(), "OUTSIDE")   # never written through


# ==================================== seal + freeze (TOCTOU) =======================================

class SealAndFreeze(unittest.TestCase):
    def test_maint_fetch_is_sealed(self):
        self.assertIn("maint_fetch", SEALED_TOOLS)

    def test_seal_binds_url_and_dest(self):
        base = held_action_seal("maint_fetch", {"url": "https://docs.example/a", "dest": "a"})
        self.assertTrue(base)
        self.assertNotEqual(base, held_action_seal("maint_fetch", {"url": "https://docs.example/b", "dest": "a"}))
        self.assertNotEqual(base, held_action_seal("maint_fetch", {"url": "https://docs.example/a", "dest": "b"}))
        self.assertEqual(base, held_action_seal("maint_fetch", {"url": "https://docs.example/a", "dest": "a"}))

    def test_seal_is_tool_bound(self):
        # a run_command with the same-looking args must not seal-collide with maint_fetch
        self.assertNotEqual(held_action_seal("maint_fetch", {"url": "x", "dest": "y"}),
                            held_action_seal("write_file", {"path": "x", "content": "y"}))

    def test_freeze_coerces_url_and_dest(self):
        class _Drift(str):
            def __str__(self):  # a drifting __str__ must be pinned ONCE at freeze
                return "drifted"
        frozen = freeze_args({"url": _Drift("https://docs.example/a"), "dest": "d"})
        self.assertIsInstance(frozen["url"], str)
        self.assertEqual(frozen["dest"], "d")


# ================================== end-to-end through the seam ====================================

def _mf(url, dest="d.bin"):
    return ToolIntent("maint_fetch", {"url": url, "dest": dest}, "structured")


class Seam(unittest.TestCase):
    def test_default_deny_without_maint_cap(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d)                                  # no net.maint cap
            dec = govern_action(s, _mf("https://docs.example/d.deb"))
            self.assertEqual(dec.status, DENIED)
            self.assertIn("net.maint:docs.example", dec.reason)

    def test_read_grant_does_not_confer_maint(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("net.get:docs.example",))   # a READ grant
            dec = govern_action(s, _mf("https://docs.example/d.deb"))
            self.assertEqual(dec.status, DENIED)                      # separate namespace — denied
            self.assertIn("net.maint:docs.example", dec.reason)

    def test_granted_is_human_gated_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(workspace=d, capabilities=("net.maint:docs.example",))
            dec = govern_action(s, _mf("https://docs.example/d.deb"))
            self.assertEqual(dec.status, HELD)                        # PROPOSE_FIRST, no auto-lift

    def test_approve_stages_and_carries_record(self):
        with tempfile.TemporaryDirectory() as d:
            signed = mint(("net.maint:docs.example",), {"maint_fetch": "propose_first"}, "admin",
                          workspace_subject(d), b"caps-key")
            s = Session(workspace=d, policy_caps=signed, caps_key=b"caps-key")
            held = govern_action(s, _mf("https://docs.example/d.deb", "drivers/d.deb"))
            self.assertEqual(held.status, HELD)
            with mock.patch("collaborator.egress.fetch_to_file", _fake_ftf(b"blob")):
                out = approve(s, held)
            self.assertEqual(out.status, RAN)
            self.assertTrue(out.cleared)
            self.assertIsNotNone(out.egress)
            self.assertTrue(os.path.exists(os.path.join(d, "drivers", "d.deb")))

    def test_seal_mismatch_after_hold_is_denied(self):
        with tempfile.TemporaryDirectory() as d:
            signed = mint(("net.maint:docs.example",), {"maint_fetch": "propose_first"}, "admin",
                          workspace_subject(d), b"caps-key")
            s = Session(workspace=d, policy_caps=signed, caps_key=b"caps-key")
            held = govern_action(s, _mf("https://docs.example/d.deb", "drivers/d.deb"))
            self.assertEqual(held.status, HELD)
            held.args = dict(held.args)
            held.args["dest"] = "elsewhere/evil.deb"                  # mutate the staged path after approval-hold
            with mock.patch("collaborator.egress.fetch_to_file", _fake_ftf(b"blob")):  # must NOT be reached
                out = approve(s, held)
            self.assertEqual(out.status, DENIED)
            self.assertIn("seal", out.reason.lower())

    def test_host_removed_between_hold_and_approve_denies(self):
        with tempfile.TemporaryDirectory() as d:
            signed = mint(("net.maint:docs.example",), {"maint_fetch": "propose_first"}, "admin",
                          workspace_subject(d), b"caps-key")
            s = Session(workspace=d, policy_caps=signed, caps_key=b"caps-key")
            held = govern_action(s, _mf("https://docs.example/d.deb", "drivers/d.deb"))
            self.assertEqual(held.status, HELD)
            s.policy_caps = mint((), {"maint_fetch": "propose_first"}, "admin",
                                 workspace_subject(d), b"caps-key")   # operator revokes the host
            with mock.patch("collaborator.egress.fetch_to_file", _fake_ftf(b"blob")):  # must NOT be reached
                out = approve(s, held)
            self.assertEqual(out.status, DENIED)
            self.assertIn("not granted at approval time", out.reason)


if __name__ == "__main__":
    unittest.main()
