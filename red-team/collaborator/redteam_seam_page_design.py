"""External 5-vendor DESIGN pressure-test for ② Stage B — "the page" (surface.py), BEFORE building.

Per Josh's rule (pressure-test plans externally before building). This is a network door onto a
governed agent, so the panel gets an explicit APPSEC attack on the hardened localhost door and the
P-01 "a new way IN is never a new way to grant POWER" claim. Reproduce-before-accept; per-model +
total cost. The design under review is red-team/collaborator/08-seam-page-design.md; the code it
wraps (host.py interface, view.snapshot/render_html) is bundled so the reviewers see the real seam.

Usage:  python red-team/collaborator/redteam_seam_page_design.py [openai/gpt-5.1 ...]
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
    ("THE DESIGN UNDER REVIEW — 08-seam-page-design.md (read first, attack THIS)",
     "red-team/collaborator/08-seam-page-design.md"),
    ("THE HOST it wraps — collaborator/host.py (the surface only calls submit()/snapshot())",
     "collaborator/host.py"),
    ("THE VIEW it renders — collaborator/view.py (snapshot() dict + render_html visual language)",
     "collaborator/view.py"),
    ("THE PLAIN-LANGUAGE AUTHORITY above the spec — seam-surface-plain-language.md",
     "docs/seam-surface-plain-language.md"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

DESIGN pressure-test (NO code exists yet) of ② Stage B, "the page", for the SalienceOS
"Collaborator". Vocabulary: the "seam" is `govern_action` (governance.py — the SOLE authority
boundary, default-deny); the "Host" (`Collaborator` in host.py) is one governed worker that owns
the loop + propose + view + ledger on a single serial thread; it exposes `submit(text)` (queue a
governed turn), `snapshot()` (the render dict), and controls (pause/approve/veto/set_leash/...).
Stage A (the Host) is SHIPPED and externally certified. Stage B adds a LOCAL WEB PAGE over it.

=== WHAT STAGE B IS (attack the DESIGN, not missing code) ===
A stdlib-only `collaborator/surface.py` = `ThreadingHTTPServer` wrapping an ALREADY-RUNNING Host,
bound to 127.0.0.1 only, serving THREE routes:
  GET  /        (token in ?k=)                -> the self-contained page shell
  GET  /state   (token in X-Sal-Token header) -> json(host.snapshot()), polled ~1 Hz
  POST /submit  (token header + same-origin)   -> host.submit(body.text) -> {task_id}
Stage B is WATCH + one input (submit). Control BUTTONS (pause/approve/veto/tighten) are Stage C;
B renders them as visible STATE, not levers. A held task shows honestly as "awaiting you — Stage C".

=== THE HARDENED DOOR (Josh chose this; attack every layer) ===
  1. Bind 127.0.0.1 ONLY, never 0.0.0.0 (off-box cannot reach the socket).
  2. Per-run secret = secrets.token_urlsafe(32), generated at launch, printed ONCE in the launch
     URL, required on EVERY request, compared with secrets.compare_digest (constant-time). The
     token is NOT passed as an argv (ps-invisible) and NOT written to a file.
  3. CSRF defense: the initial GET / carries the token in ?k= (bookmarkable); the page then holds
     it in JS memory and sends it on /state and /submit as a CUSTOM header `X-Sal-Token`. A custom
     header is unforgeable cross-origin without a CORS preflight, and the server sends NO permissive
     CORS headers -> a page in another tab can neither read /state nor POST /submit. A simple <form>
     POST also cannot set a custom header.
  4. Anti DNS-rebinding: reject any request whose Host header is not 127.0.0.1:<port>/localhost:<port>.
  5. Origin pin on /submit: a present Origin must equal our own; a foreign Origin -> 403.
  6. Body cap 64 KiB on /submit -> 413. Method+path allowlist (else 404/405).
  7. Token never logged / echoed in errors / placed in /state; default request logging (which would
     log ?k=) is silenced; plan adds history.replaceState + Referrer-Policy:no-referrer so the
     ?k= token does not leak via browser history or a Referer header.

=== THE ONE RULE — P-01 (attack it) ===
The page is a new way IN, never a new way to grant POWER. The surface process ONLY calls
host.submit() and host.snapshot(); it does NOT import governance/policycaps or mint capabilities.
There is NO code path from an HTTP request to a capability grant. submit(text) only queues a
governed turn — the text becomes the model's directive, still gated by `govern_action`; it must
NOT reach any authority-bearing field (a `leash=` kwarg, `intent.source`, `autonomous`, an emit).

=== THE DESIGN CLAIMS (attack EACH) ===
  D1 (door integrity): with the above, NO other local process (without the token), NO web page in
     another tab/origin, and NO DNS-rebinding attack can read /state or drive /submit. The only
     caller who can is a browser the human pointed at the printed URL. Find a concrete bypass:
     a CSRF that lands /submit, a rebinding that passes the Host pin, a simple-request read of
     /state, a token leak (Referer/history/logs/ps/timing), a preflight-less cross-origin path.
  D2 (P-01 / no authority via the door): no HTTP request — including a maliciously crafted submit
     body — can grant a capability, loosen a leash past its signed cap, set autonomous/intent.source,
     or trigger an emission. The worst a submit body can do is give the MODEL a directive the seam
     then governs. Find any way request-derived data reaches an authority field, or any control the
     "watch-only" page exposes that the plan says it shouldn't.
  D3 (honest scope / no dead-end-that-looks-like-a-grant): a held task shown as "awaiting you"
     with no page approve (until C) is honest, not a stuck state that misleads or that some retry
     silently escalates. Find a place the watch-only surface misrepresents governance state, or a
     held/paused task that the page could nudge past the seam.
  D4 (availability / self-DoS): a flood of /submit (each queuing a Task on the single worker), a
     slowloris, a giant body, or a wedged model turn cannot corrupt state or make the door lie —
     at worst it queues work. Identify a resource/quota issue that becomes a SAFETY (not just perf)
     problem, and whether the body cap / threading / single worker bound it.

=== OUT OF SCOPE (do NOT rate as bugs) ===
  - Multi-user / login auth — a Stage-③ (chassis) concern; single-user localhost is the whole point.
  - Control BUTTONS (pause/approve/veto/tighten) missing from B — that is Stage C by design.
  - SSE/websocket vs polling — polling is the chosen v0; not a flaw.
  - Aesthetics / the "front door" look — a ③ decision.
  - Already-certified Host/loop/seam internals except where the DOOR newly exposes them.
  - Speculative "what if the human's own browser has malware with the token" — the token is the
     human's; a compromised human endpoint is out of this threat model (name it, don't rate it).
"""

SYSTEM = """You are a senior application-security engineer red-teaming the DESIGN of a localhost web
door onto a governed AI agent, BEFORE it is built. BREAK the door and the P-01 authority claim, but
also CERTIFY: if you cannot break a claim, say so explicitly (a blocked attack is a valuable
non-finding). Assume the author is wrong until proven otherwise. Be maximally adversarial about:
  - CSRF / cross-origin: can any page in another tab, without the token, cause /submit to run or
    read /state? Walk the exact browser mechanics (simple request vs preflight, what headers a
    <form>/<img>/fetch/WebSocket can set cross-origin, whether the custom-header + no-CORS combo
    truly blocks it). Does the Host-header pin actually stop DNS-rebinding (the rebind request's
    Host header)? Is there a token leak via Referer, browser history, window.name, logs, ps, or a
    timing side-channel on the compare?
  - AUTHORITY (P-01): trace a hostile submit body. Can it reach a leash=/intent.source/autonomous/
    emit, or otherwise widen what the model may do? Is "the surface only calls submit()/snapshot()"
    actually sufficient, given submit's text is attacker-chosen?
  - Scope honesty: does watch-only B ever misrepresent governance or expose a lever it shouldn't?
Name the exact layer that fails, give a CONCRETE attack (request, headers, sequence), and say
whether another layer independently catches it. Distinguish a real door/authority break from a nit
or a stated non-goal. Also flag any MISSING defense the design should add before building."""

USER_TMPL = """Pressure-test the Stage B DESIGN below (no code yet — attack the design + the threat model).

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / WHICH LAYER FAILS /
CONCRETE ATTACK (request + headers + sequence) / WHY IT BREAKS A CLAIM / WHETHER ANOTHER LAYER
CATCHES IT / FIX or MISSING-DEFENSE-TO-ADD.
Then a CERTIFICATION LINE for EACH claim D1, D2, D3, D4: CERTIFIED / NOT-CERTIFIED + one sentence.
Then STEELMAN (2-3 sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence, and
the single highest-value change to make before building).

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
                 "X-Title": "SalienceOS Seam-Page Design Panel"},
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
    raw = HERE / "raw_seampage_design"
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
                    f"# Seam-page design panel: {m}\n\n_cost={_fmt(r.get('cost'))} finish={r['finish']}_\n\n{r['content']}\n",
                    encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n=========== API COST — seam-page design panel ===========")
    total, have = 0.0, False
    for m in MODELS:
        c = results.get(m, {}).get("cost")
        if isinstance(c, (int, float)):
            total += c; have = True
        print(f"  {m:32s} {_fmt(c)}")
    print(f"  {'TOTAL':32s} {_fmt(total) if have else 'n/a'}")


if __name__ == "__main__":
    main()
