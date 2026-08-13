"""LIVE proof for ② Stage B — "the page". Launches the real hardened surface over a real Host on
Sparky's gpt-oss:120b, then drives it ENTIRELY over HTTP through its own doorway: the single-use
bootstrap handshake, a POST /submit directive, and polling GET /state — asserting the governed
steps and a DONE task come back through the socket. This is the Stage-B proof: "turn it on, open
the page, give it a job, watch the governed steps happen in front of you" — done programmatically.

It also proves the door itself live: a no-token /state is refused, and a cross-origin /submit is
refused, against the actual running server.

Usage (on Sparky):  python3 red-team/collaborator/e2e_sparky_page.py
Env: OLLAMA_BASE, OLLAMA_MODEL, TEMP.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from collaborator.host import Collaborator  # noqa: E402
from collaborator.model_client import OllamaClient  # noqa: E402
from collaborator.session import Session  # noqa: E402
from collaborator.surface import SalSurface  # noqa: E402

BASE_URL = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11500/v1")
MODEL = os.environ.get("OLLAMA_MODEL", "gpt-oss:120b")
TEMP = float(os.environ.get("TEMP", "0.0"))

PHASES: list = []


def phase(name: str, ok: bool, detail: str) -> None:
    PHASES.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)


def _req(method, url, headers=None, data=None, timeout=30):
    r = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        try:
            return e.code, dict(e.headers), e.read()
        finally:
            e.close()


def main() -> int:
    print(f"HOST+SURFACE live proof — {MODEL} @ {BASE_URL} (temp={TEMP})\n", flush=True)
    ws = Path(tempfile.mkdtemp(prefix="sal_page_"))
    session = Session(workspace=ws,
                      capabilities=("fs.read:project", "fs.write:project", "shell.exec"),
                      proactivity="conservative", default_importance=0.5)
    client = OllamaClient(BASE_URL, MODEL, timeout=180, temperature=TEMP)
    host = Collaborator(session, client).start()
    surface = SalSurface(host, port=0).serve()
    base = f"http://127.0.0.1:{surface.port}"
    print(f"surface up: {surface.url}\n", flush=True)

    try:
        # D1 — the door BEFORE a session: no-token /state is refused (live).
        st, _, _ = _req("GET", f"{base}/state")
        phase("D1 no-token /state refused", st == 403, f"status={st}")

        # Bootstrap handshake (what the browser does on first load).
        st, hdrs, body = _req("GET", surface.url)
        cookie = re.search(r"sal_session=([^;]+)", hdrs.get("Set-Cookie", ""))
        csrf = re.search(rb'const CSRF = "([^"]+)"', body)
        ok = st == 200 and cookie and csrf and "HttpOnly" in hdrs.get("Set-Cookie", "")
        phase("D2 bootstrap → HttpOnly session", bool(ok),
              f"status={st} cookie={bool(cookie)} csrf={bool(csrf)}")
        creds = {"Cookie": f"sal_session={cookie.group(1)}",
                 "X-Sal-Token": csrf.group(1).decode()}

        # D3 — cross-origin /submit refused (live door).
        st, _, _ = _req("POST", f"{base}/submit",
                        headers={**creds, "Content-Type": "application/json",
                                 "Origin": "http://evil.example"},
                        data=json.dumps({"text": "x"}).encode())
        phase("D3 cross-origin /submit refused", st == 403, f"status={st}")

        # P1 — give Sal a job THROUGH THE PAGE and watch it run to DONE via /state.
        st, _, body = _req("POST", f"{base}/submit",
                           headers={**creds, "Content-Type": "application/json"},
                           data=json.dumps({"text":
                               "Create a file page_proof.txt containing exactly two short lines "
                               "about the color blue, then tell me you are done."}).encode())
        task_id = json.loads(body)["task_id"] if st == 200 else None
        phase("P1 submit accepted via /submit", st == 200 and bool(task_id), f"task_id={task_id}")

        # Poll /state (as the page does) until the task is DONE/FAILED — watch governed steps arrive.
        final, snap = None, {}
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            st, _, body = _req("GET", f"{base}/state", headers=creds)
            snap = json.loads(body)
            t = next((t for t in snap.get("tasks", []) if t["id"] == task_id), None)
            if t and t["state"] in ("done", "failed", "cancelled"):
                final = t
                break
            time.sleep(1.0)

        governed = len(snap.get("attending", []))
        ran = snap.get("counts", {}).get("ran", 0)
        wrote = (ws / "page_proof.txt").exists()
        phase("P2 task ran to DONE through the socket",
              bool(final) and final["state"] == "done",
              f"state={final['state'] if final else 'timeout'} governed_steps={governed} ran={ran}")
        phase("P3 governed steps visible in /state", governed > 0 and ran > 0,
              f"attending={governed} ran={ran} file_written={wrote}")

        # ---- Stage C: steer a running job entirely via POST /control ----
        def ctl(action, **extra):
            body = json.dumps(dict(action=action, **extra)).encode()
            st, _, b = _req("POST", f"{base}/control",
                            headers={**creds, "Content-Type": "application/json"}, data=body)
            return st, (json.loads(b).get("ok") if st == 200 else None)

        def state():
            _, _, b = _req("GET", f"{base}/state", headers=creds)
            return json.loads(b)

        # C1 — pause / resume flips snapshot.paused live.
        st1, ok1 = ctl("pause"); paused = state()["paused"]
        st2, ok2 = ctl("resume"); unpaused = state()["paused"]
        phase("C1 pause/resume via /control", st1 == 200 and ok1 and paused and st2 == 200 and not unpaused,
              f"paused={paused}->{unpaused}")

        # C2 — tighten a leash via /control so a write HOLDS, then APPROVE it to DONE via /control.
        # gpt-oss intermittently no-ops a short write prompt (returns empty completions -> the task
        # honestly FAILS) even at temp 0; that is a known model-reliability quirk, not a surface
        # issue, so we RETRY the submit until the write actually holds (or give up after N).
        stl, okl = ctl("set_leash", tool="write_file", leash="propose_first")
        tid, held = None, None
        for attempt in range(5):
            _, _, body = _req("POST", f"{base}/submit",
                              headers={**creds, "Content-Type": "application/json"},
                              data=json.dumps({"text":
                                  "Create a file control_proof.txt containing just the word: steered."}).encode())
            tid = json.loads(body)["task_id"]
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                t = next((t for t in state().get("tasks", []) if t["id"] == tid), None)
                if t and t["state"] in ("awaiting_approval", "done", "failed"):
                    held = t
                    break
                time.sleep(1.0)
            if held and held["state"] == "awaiting_approval":
                break  # got a real hold to steer
        did_hold = bool(held) and held["state"] == "awaiting_approval"
        phase("C2a set_leash held a write via /control", okl and did_hold,
              f"leash_ok={okl} state={held['state'] if held else 'timeout'} "
              f"reply={held.get('reply','')[:120]!r} held_n={len(held.get('held',[]))}" if held else "timeout")

        approved_done = None
        if did_hold:
            sta, oka = ctl("approve", task_id=tid)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                t = next((t for t in state().get("tasks", []) if t["id"] == tid), None)
                if t and t["state"] in ("done", "failed", "cancelled"):
                    approved_done = t
                    break
                time.sleep(1.0)
        wrote = (ws / "control_proof.txt").exists()
        phase("C2b approve via /control → DONE",
              bool(approved_done) and approved_done["state"] == "done" and wrote,
              f"state={approved_done['state'] if approved_done else 'n/a'} file_written={wrote}")

        snap = state()
        print("\n--- final /state snapshot (trimmed) ---", flush=True)
        print(json.dumps({"paused": snap.get("paused"), "counts": snap.get("counts"),
                          "leashes": snap.get("leashes"),
                          "attending": snap.get("attending", [])[-4:],
                          "tasks": snap.get("tasks")}, indent=2)[:2000], flush=True)
    finally:
        surface.shutdown()
        host.stop()

    ok_all = all(ok for _, ok, _ in PHASES)
    print(f"\n{'='*54}\nSTAGE B LIVE PROOF: {'ALL PASS' if ok_all else 'SOME FAILED'}", flush=True)
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
