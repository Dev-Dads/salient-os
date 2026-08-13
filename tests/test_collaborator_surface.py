"""The page (② Stage B): a hardened localhost door over the Host. These tests drive a FAKE
in-process host on an ephemeral loopback port — no model, no network beyond 127.0.0.1 — and
pin every door invariant the design panel demanded: single-use bootstrap, HttpOnly SameSite
session cookie, the dual CSRF wall, the strict Host allowlist (anti DNS-rebinding), the Origin
pin, upgrade rejection, the body cap, the pending-work 429 cap, the security headers, and P-01
(the surface only ever calls submit()/snapshot() and imports no governance/policycaps). Every
wait is bounded."""

import ast
import json
import pathlib
import re
import unittest
import urllib.error
import urllib.request

from collaborator.surface import SalSurface


class FakeHost:
    """Records submit() calls; returns a canned snapshot. `pending` sets how many non-terminal
    tasks the snapshot reports (drives the 429 cap test)."""

    def __init__(self, pending: int = 0):
        self.submitted: list = []
        self._pending = pending

    def submit(self, text: str) -> str:
        self.submitted.append(text)
        return "task-" + str(len(self.submitted))

    def snapshot(self) -> dict:
        tasks = [{"id": f"t{i}", "prompt": "p", "state": "running", "reply": "",
                  "decisions": 0, "held": [], "error": ""} for i in range(self._pending)]
        return {"paused": False, "proactivity": "conservative",
                "capabilities": ["fs.read:project"], "leashes": {"write_file": "propose_first"},
                "attending": [{"tool": "read_file", "status": "ran", "leash": "act_then_report",
                               "origin": "direct", "summary": "read a file"}],
                "ran": [], "proposals": [],
                "counts": {"governed": 1, "ran": 1, "held": 0, "paused": 0,
                           "proposals_pending": 0},
                "tasks": tasks, "busy": False}


def _req(method, url, headers=None, data=None):
    r = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        try:
            return e.code, dict(e.headers), e.read()
        finally:
            e.close()


class SurfaceTestBase(unittest.TestCase):
    pending = 0

    def setUp(self):
        self.host = FakeHost(pending=self.pending)
        self.sfc = SalSurface(self.host, port=0).serve()
        self.base = f"http://127.0.0.1:{self.sfc.port}"
        self.addCleanup(self.sfc.shutdown)

    def _bootstrap(self):
        """Do the real first-load handshake; return (cookie_header_value, csrf, html)."""
        code, hdrs, body = _req("GET", self.sfc.url)
        self.assertEqual(code, 200)
        set_cookie = hdrs.get("Set-Cookie", "")
        sess = re.search(r"sal_session=([^;]+)", set_cookie).group(1)
        html = body.decode("utf-8")
        csrf = re.search(r'const CSRF = "([^"]+)"', html).group(1)
        return f"sal_session={sess}", csrf, html

    def _authed(self):
        cookie, csrf, _ = self._bootstrap()
        return {"Cookie": cookie, "X-Sal-Token": csrf}


class TestBootstrap(SurfaceTestBase):
    def test_bootstrap_sets_httponly_samesite_cookie(self):
        code, hdrs, body = _req("GET", self.sfc.url)
        self.assertEqual(code, 200)
        sc = hdrs.get("Set-Cookie", "")
        self.assertIn("sal_session=", sc)
        self.assertIn("HttpOnly", sc)
        self.assertIn("SameSite=Strict", sc)
        self.assertIn('const CSRF = "', body.decode("utf-8"))

    def test_bootstrap_wrong_token_forbidden(self):
        code, _, _ = _req("GET", f"{self.base}/?k=wrongwrongwrong")
        self.assertEqual(code, 403)

    def test_bootstrap_missing_token_forbidden(self):
        code, _, _ = _req("GET", f"{self.base}/")
        self.assertEqual(code, 403)

    def test_bootstrap_is_single_use(self):
        code, _, _ = _req("GET", self.sfc.url)   # consume it
        self.assertEqual(code, 200)
        code2, _, _ = _req("GET", self.sfc.url)  # same ?k= again, no cookie
        self.assertEqual(code2, 403)

    def test_reload_with_cookie_succeeds(self):
        cookie, _, _ = self._bootstrap()
        code, _, _ = _req("GET", f"{self.base}/", headers={"Cookie": cookie})
        self.assertEqual(code, 200)


class TestStateAuth(SurfaceTestBase):
    def test_state_authed_returns_snapshot(self):
        code, hdrs, body = _req("GET", f"{self.base}/state", headers=self._authed())
        self.assertEqual(code, 200)
        snap = json.loads(body)
        for k in ("paused", "proactivity", "capabilities", "leashes", "attending", "ran",
                  "proposals", "counts", "tasks", "busy"):
            self.assertIn(k, snap)

    def test_state_without_csrf_forbidden(self):
        cookie, _, _ = self._bootstrap()
        code, _, _ = _req("GET", f"{self.base}/state", headers={"Cookie": cookie})
        self.assertEqual(code, 403)

    def test_state_wrong_csrf_forbidden(self):
        cookie, _, _ = self._bootstrap()
        code, _, _ = _req("GET", f"{self.base}/state",
                          headers={"Cookie": cookie, "X-Sal-Token": "nope"})
        self.assertEqual(code, 403)

    def test_state_without_cookie_forbidden(self):
        _, csrf, _ = self._bootstrap()
        code, _, _ = _req("GET", f"{self.base}/state", headers={"X-Sal-Token": csrf})
        self.assertEqual(code, 403)


class TestHostAndOriginPins(SurfaceTestBase):
    def test_host_header_spoof_forbidden(self):
        # (leading/trailing whitespace is stripped by the HTTP header parser before our code sees
        # it, normalizing to the legit value — so it's not a reachable bypass and isn't tested here.)
        bad_hosts = ("evil.com", "127.0.0.1", "localhost",              # missing port
                     f"localhost.:{self.sfc.port}",                     # trailing dot
                     f"127.0.0.1:{self.sfc.port + 1}")                  # wrong port
        for bad in bad_hosts:
            headers = {**self._authed_once, "Host": bad}
            code, _, _ = _req("GET", f"{self.base}/state", headers=headers)
            self.assertEqual(code, 403, f"Host {bad!r} should be refused")

    def setUp(self):
        super().setUp()
        # one shared credential set (each _authed() would consume a fresh bootstrap, but bootstrap
        # is single-use — so capture creds once and reuse them across the loop)
        self._authed_once = self._authed()

    def test_missing_host_forbidden(self):
        # urllib always sends Host, so assert via a manual override to empty is impractical here;
        # the strict allowlist (exact match) refuses anything not in the set, covered above.
        code, _, _ = _req("GET", f"{self.base}/state",
                          headers={**self._authed_once, "Host": ""})
        self.assertEqual(code, 403)

    def test_cross_origin_submit_forbidden(self):
        code, _, _ = _req("POST", f"{self.base}/submit",
                          headers={**self._authed_once, "Content-Type": "application/json",
                                   "Origin": "http://evil.com"},
                          data=json.dumps({"text": "x"}).encode())
        self.assertEqual(code, 403)
        self.assertEqual(self.host.submitted, [])

    def test_cross_origin_state_forbidden(self):
        # /state is Origin-pinned too (defense-in-depth beyond the custom-header wall).
        code, _, _ = _req("GET", f"{self.base}/state",
                          headers={**self._authed_once, "Origin": "http://evil.com"})
        self.assertEqual(code, 403)

    def test_same_origin_submit_allowed(self):
        code, _, _ = _req("POST", f"{self.base}/submit",
                          headers={**self._authed_once, "Content-Type": "application/json",
                                   "Origin": self.base},
                          data=json.dumps({"text": "go"}).encode())
        self.assertEqual(code, 200)
        self.assertEqual(self.host.submitted, ["go"])


class TestSubmit(SurfaceTestBase):
    def test_submit_records_text(self):
        code, _, body = _req("POST", f"{self.base}/submit",
                             headers={**self._authed(), "Content-Type": "application/json"},
                             data=json.dumps({"text": "do a thing"}).encode())
        self.assertEqual(code, 200)
        self.assertEqual(self.host.submitted, ["do a thing"])
        self.assertEqual(json.loads(body)["task_id"], "task-1")

    def test_submit_empty_text_rejected(self):
        code, _, _ = _req("POST", f"{self.base}/submit",
                          headers={**self._authed(), "Content-Type": "application/json"},
                          data=json.dumps({"text": "   "}).encode())
        self.assertEqual(code, 400)
        self.assertEqual(self.host.submitted, [])

    def test_submit_body_cap(self):
        big = json.dumps({"text": "x" * (65 * 1024)}).encode()
        code, _, _ = _req("POST", f"{self.base}/submit",
                          headers={**self._authed(), "Content-Type": "application/json"},
                          data=big)
        self.assertEqual(code, 413)
        self.assertEqual(self.host.submitted, [])


class TestPendingCap(SurfaceTestBase):
    pending = 32  # host reports 32 non-terminal tasks -> at the default cap

    def test_submit_refused_when_saturated(self):
        code, _, _ = _req("POST", f"{self.base}/submit",
                          headers={**self._authed(), "Content-Type": "application/json"},
                          data=json.dumps({"text": "one more"}).encode())
        self.assertEqual(code, 429)
        self.assertEqual(self.host.submitted, [])


class TestMethodAndPath(SurfaceTestBase):
    def test_unknown_path_404(self):
        code, _, _ = _req("GET", f"{self.base}/nope", headers=self._authed())
        self.assertEqual(code, 404)

    def test_wrong_method_405(self):
        code, _, _ = _req("PUT", f"{self.base}/state", headers=self._authed())
        self.assertEqual(code, 405)

    def test_post_to_state_405(self):
        code, _, _ = _req("POST", f"{self.base}/state", headers=self._authed())
        self.assertEqual(code, 405)

    def test_upgrade_rejected(self):
        code, _, _ = _req("GET", f"{self.base}/state",
                          headers={**self._authed(), "Connection": "Upgrade",
                                   "Upgrade": "websocket"})
        self.assertEqual(code, 400)


class TestSecurityHeadersAndLeak(SurfaceTestBase):
    def test_headers_present_on_state(self):
        _, hdrs, _ = _req("GET", f"{self.base}/state", headers=self._authed())
        self.assertIn("default-src 'none'", hdrs.get("Content-Security-Policy", ""))
        self.assertEqual(hdrs.get("Referrer-Policy"), "no-referrer")
        self.assertEqual(hdrs.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(hdrs.get("Cache-Control"), "no-store")

    def test_no_secret_in_state_or_errors(self):
        headers = self._authed()
        # the session + csrf secrets must never appear in a response body
        sess = headers["Cookie"].split("=", 1)[1]
        csrf = headers["X-Sal-Token"]
        _, _, body = _req("GET", f"{self.base}/state", headers=headers)
        self.assertNotIn(sess.encode(), body)
        self.assertNotIn(csrf.encode(), body)
        _, _, ebody = _req("GET", f"{self.base}/nope", headers=headers)
        self.assertNotIn(sess.encode(), ebody)
        self.assertNotIn(csrf.encode(), ebody)


class TestStructuralInvariants(unittest.TestCase):
    """P-01 + bind-scope invariants read straight off the source / server object."""

    SRC = pathlib.Path(__file__).resolve().parents[1] / "collaborator" / "surface.py"

    def test_binds_loopback_only(self):
        host = FakeHost()
        sfc = SalSurface(host, port=0)
        self.addCleanup(sfc.shutdown)
        self.assertEqual(sfc.host_addr, "127.0.0.1")
        self.assertNotEqual(sfc.host_addr, "0.0.0.0")

    def test_refuses_non_loopback_bind(self):
        with self.assertRaises(ValueError):
            SalSurface(FakeHost(), host_addr="0.0.0.0")

    def test_uses_constant_time_compare(self):
        src = self.SRC.read_text(encoding="utf-8")
        self.assertIn("secrets.compare_digest", src)
        # every secret comparison must go through compare_digest — no `==` on a secret
        self.assertNotRegex(src, r"self\._(session|csrf|bootstrap)\s*==")

    def test_p01_no_governance_import(self):
        """The surface module must import NOTHING from governance/policycaps (AST-checked, so a
        docstring mention doesn't count). Its only Collaborator import is host state constants +
        (in the launcher) the concrete Session/Client/Host wiring."""
        tree = ast.parse(self.SRC.read_text(encoding="utf-8"))
        forbidden = {"governance", "policycaps", "policy"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module.split(".")[-1]
                self.assertNotIn(mod, forbidden,
                                 f"surface.py must not import {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[-1], forbidden)


if __name__ == "__main__":
    unittest.main()
