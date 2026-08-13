"""External 5-vendor CODE review + CERTIFICATION for ② Stage B — the page (collaborator/surface.py):
a hardened localhost web door over the Host. Per Josh's standing rule (external review on EVERY
non-doc PR). This is a NETWORK surface onto a governed agent, so the panel gets a full APPSEC
attack on the door + the P-01 "a new way IN is never a new way to grant POWER" claim. The DESIGN
was pre-paneled (08-seam-page-design.md); this certifies the SHIPPED CODE. Reproduce-before-accept;
per-model + total cost.

Usage:  python red-team/collaborator/redteam_surface.py [openai/gpt-5.1 ...]
"""

import concurrent.futures as cf
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).parent
SOS = HERE.parent.parent
KEY = os.environ["OPENROUTER_API_KEY"].strip()

_FILES = [
    ("THE CODE UNDER REVIEW — collaborator/surface.py (attack THIS)", "collaborator/surface.py"),
    ("WHAT IS PINNED — tests/test_collaborator_surface.py", "tests/test_collaborator_surface.py"),
    ("THE HOST it wraps — collaborator/host.py (surface calls ONLY submit()/snapshot())",
     "collaborator/host.py"),
    ("THE DESIGN (already design-paneled) — 08-seam-page-design.md", "red-team/collaborator/08-seam-page-design.md"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

CODE review + CERTIFICATION of a SHIPPED new module `collaborator/surface.py` in the SalienceOS
"Collaborator". It is ② Stage B, "the page": a localhost-only web surface over the Host
(`Collaborator` in host.py — a governed worker exposing `submit(text)` = queue a governed turn,
`snapshot()` = the render dict, and controls). Stage A (the Host) is shipped + externally
certified; the seam (`govern_action`, governance.py) is the SOLE authority boundary, default-deny.

=== WHAT surface.py IS ===
A stdlib `ThreadingHTTPServer` (bounded) wrapping an ALREADY-RUNNING Host, bound to 127.0.0.1
only, serving THREE routes:
  GET  /        (single-use bootstrap in ?k=)          -> validates, sets session cookie, page
  GET  /state   (session cookie + X-Sal-Token header)  -> json(host.snapshot()), polled ~1 Hz
  POST /submit  (cookie + header + same-origin)         -> host.submit(body.text) -> {task_id}
It is WATCH + one input (submit). Control BUTTONS (pause/approve/veto/tighten) are Stage C, absent
by design; a held task is shown honestly as "awaiting you — Stage C". The page renders snapshot
strings via textContent only. The design was pre-paneled: P-01 + scope certified 5/5; the panel's
two not-certifieds (a durable bearer token in the URL; an unbounded queue) were then closed
structurally in the code below — verify they actually are.

=== THE HARDENED DOOR (attack every layer, in the CODE) ===
  * bind 127.0.0.1 ONLY (SalSurface.__init__ raises on anything else; _BoundedThreadingHTTPServer);
  * SINGLE-USE bootstrap (`_consume_bootstrap`, under a lock) exchanged on first GET / for an
    `HttpOnly; SameSite=Strict; Path=/` session cookie — the durable secret never lives in a URL/
    JS/history/Referer, and HttpOnly blocks XSS reads. Reload uses the cookie path (bootstrap
    untouched);
  * CSRF walled twice: the SameSite=Strict cookie (not sent cross-site) AND a per-session CSRF
    token sent as the custom header `X-Sal-Token`, REQUIRED (constant-time) on BOTH /state and
    /submit (`_guard_authed`). No permissive CORS headers are ever sent;
  * strict Host-header allowlist — exact match to `127.0.0.1:<port>`/`localhost:<port>`
    (`_host_ok`, anti DNS-rebinding); Origin pin on /submit (`_origin_ok`);
  * strict CSP (`default-src 'none'`; per-response nonce for the inline script/style),
    Referrer-Policy: no-referrer, nosniff, no-store (`_security_headers`); textContent-only render;
  * availability bounded: pending-work 429 cap (`_non_terminal_task_count` vs max_pending),
    per-request socket timeout + a TIGHT 5 s body-read deadline (stalled/lying Content-Length ->
    408, can't pin a slot), Content-Length-checked 64 KiB body cap, bounded concurrency
    (non-blocking semaphore -> drop when saturated, never blocks the accept loop);
  * token discipline: secrets generated in-process (never argv/file); request logging prints
    method + PATH only (never the ?k= query); secrets never in /state or an error body.

=== P-01 (the one rule — attack it in the CODE) ===
surface.py imports NOTHING from governance/policycaps and calls ONLY host.submit()/host.snapshot().
No HTTP request — including a hostile submit body — can grant a capability, loosen a leash past its
signed cap, set autonomous/intent.source, or trigger an emission; the worst a submit body does is
hand the MODEL a directive the seam still governs.

=== THE CERTIFICATION CLAIMS (attack EACH, in the shipped code) ===
  C1 (door integrity): no other local process (no token), no page in another tab/origin, and no
     DNS-rebinding can read /state or drive /submit; the only caller is a browser the human pointed
     at the printed URL. Find a concrete bypass in the CODE: a CSRF that lands /submit, a rebinding
     past `_host_ok`, a simple-request read of /state, a token leak (Referer/history/log/timing),
     a single-use race, a header-parsing gap, a missing guard on a route.
  C2 (P-01 / no authority): trace a hostile submit body + every route. No request-derived data
     reaches an authority field; the surface only calls submit()/snapshot(). Find any path that
     grants/loosens/emits, or any import of governance/policycaps.
  C3 (availability / can't be darkened): a /submit flood (429), a slowloris/lying-body (408 in 5 s,
     slot freed), a giant body (413), a wedged host, or connection exhaustion cannot corrupt state,
     hide a held action, or make /state lie. Find a resource path that becomes a SAFETY problem, or
     a way to pin all slots indefinitely.
  C4 (scope honesty): watch-only B exposes NO control that grants/steers beyond `submit`; a held/
     paused task is shown honestly, never nudged past the seam. Find a lever the page shouldn't have
     or a misrepresentation of governance state.

=== OUT OF SCOPE (do NOT rate as bugs) ===
  - Missing control BUTTONS (pause/approve/veto/tighten) — Stage C by design.
  - SSE/websocket vs polling — polling is the chosen v0.
  - Multi-user / login auth — a Stage-③ concern; single-user localhost is the point.
  - A compromised human endpoint that already holds the token — outside the threat model.
  - The single-use-bootstrap prefetch trade (documented) — name it, don't rate it CRITICAL.
  - Already-certified Host/loop/seam internals except where the DOOR newly exposes them.
"""

SYSTEM = """You are a senior application-security engineer doing an adversarial CODE review AND
CERTIFICATION of a SHIPPED localhost web door onto a governed AI agent. BREAK the door and the
P-01 authority claim in the ACTUAL CODE, but also certify: if you cannot break a claim, say so
explicitly (a blocked attack is a valuable non-finding). Assume the author is wrong until proven
otherwise. Be maximally adversarial about:
  - CSRF / cross-origin: read `_guard_authed`, `_host_ok`, `_origin_ok`, the cookie parsing, the
    Set-Cookie flags, and the response headers. Can any page in another tab, without the token,
    cause /submit to run or read /state? Walk the exact browser mechanics (simple vs preflight,
    what a <form>/<img>/fetch can set cross-origin, whether SameSite=Strict + custom-header + no
    CORS truly blocks it). Does `_host_ok`'s exact-match actually stop rebinding? Any token leak
    via the log, an error body, Referer, or a timing side-channel (is compare_digest used for
    EVERY secret)? Is the single-use bootstrap racy (two concurrent GET /?k=)?
  - AUTHORITY (C2): trace a hostile submit body through `_route_submit` -> host.submit. Can it
    reach anything but a model directive? Does surface.py import governance/policycaps?
  - AVAILABILITY (C3): can a slowloris / lying Content-Length / flood pin all slots or darken the
    watch surface? Read the semaphore logic (non-blocking? does the accept loop or shutdown ever
    block?), the body-read deadline, the 429 cap.
Name file:line, give a CONCRETE request (method + headers + sequence), and say whether another
layer independently catches it. Distinguish a real door/authority break from a nit or a stated
non-goal. Also flag any MISSING defense the shipped code should add."""

USER_TMPL = """Review + CERTIFY the SHIPPED surface.py below.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) /
CONCRETE REQUEST OR SEQUENCE / WHY IT BREAKS A CLAIM / WHETHER ANOTHER LAYER CATCHES IT / FIX.
Then a CERTIFICATION LINE for EACH claim C1, C2, C3, C4: CERTIFIED / NOT-CERTIFIED + one sentence.
Then STEELMAN (2-3 sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence, and
the single highest-value fix).

=================== BEGIN MATERIAL ===================
{bundle}
=================== END MATERIAL ==================="""

PANEL = ["openai/gpt-5.1", "google/gemini-2.5-pro", "anthropic/claude-opus-4.1",
         "x-ai/grok-4.5", "qwen/qwen3-max"]
MODELS = sys.argv[1:] if len(sys.argv) > 1 else PANEL


def build_bundle() -> str:
    parts = [CONTEXT]
    for label, rel in _FILES:
        p = SOS / rel
        try:
            body = p.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            body = f"(could not read {rel}: {e})"
        parts.append(f"\n########## {label} ##########\n\n{body}")
    return "\n".join(parts)


BUNDLE = build_bundle()


def call(model: str) -> dict:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": USER_TMPL.format(bundle=BUNDLE)}],
        "temperature": 0.3, "max_tokens": 6500, "usage": {"include": True},
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://salient-os.local/redteam",
                 "X-Title": "SalienceOS Surface Code Panel"},
        method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        return {"model": model, "error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:600]}"}
    except Exception as e:  # noqa: BLE001
        return {"model": model, "error": f"{type(e).__name__}: {e}"}
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or msg.get("reasoning") or ""
    usage = data.get("usage", {})
    if not content:
        return {"model": model, "error": f"empty (finish={choice.get('finish_reason')})", "usage": usage}
    return {"model": model, "seconds": round(time.time() - t0, 1), "usage": usage,
            "cost": usage.get("cost"), "content": content, "finish": choice.get("finish_reason")}


def _fmt(c):
    return f"${c:.4f}" if isinstance(c, (int, float)) else "n/a"


def main():
    raw = HERE / "raw_surface"
    raw.mkdir(parents=True, exist_ok=True)
    print(f"bundle chars={len(BUNDLE)}  models={MODELS}")
    results = {}
    with cf.ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(call, m): m for m in MODELS}
        for fut in cf.as_completed(futs):
            r = fut.result()
            m = r["model"]
            results[m] = r
            slug = m.replace("/", "_")
            if "error" in r:
                print(f"[FAIL] {m}: {r['error'][:180]}")
                (raw / f"{slug}.md").write_text(f"# {m}\n\nERROR: {r['error']}\n", encoding="utf-8")
            else:
                print(f"[ OK ] {m}  {r['seconds']}s  cost={_fmt(r.get('cost'))}  chars={len(r['content'])}")
                (raw / f"{slug}.md").write_text(
                    f"# Surface code panel: {m}\n\n_cost={_fmt(r.get('cost'))} finish={r['finish']}_\n\n{r['content']}\n",
                    encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n=========== API COST — surface code panel ===========")
    total, have = 0.0, False
    for m in MODELS:
        c = results.get(m, {}).get("cost")
        if isinstance(c, (int, float)):
            total += c; have = True
        print(f"  {m:32s} {_fmt(c)}")
    print(f"  {'TOTAL':32s} {_fmt(total) if have else 'n/a'}")


if __name__ == "__main__":
    main()
