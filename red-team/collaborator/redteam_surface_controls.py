"""External 5-vendor CODE review + CERTIFICATION for ② Stage C — the controls (POST /control in
collaborator/surface.py): the page's buttons that steer a running job. Per Josh's standing rule
(external review on EVERY non-doc PR). The door + the Host controls were already certified; this
panel hammers the ONE new claim — that a control over HTTP can only RESTRICT or EXPRESS host config,
never GRANT/widen authority (P-01). Reproduce-before-accept; per-model + total cost.

Usage:  python red-team/collaborator/redteam_surface_controls.py [openai/gpt-5.1 ...]
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
    ("THE CODE UNDER REVIEW — collaborator/surface.py (attack POST /control + the dispatch table)",
     "collaborator/surface.py"),
    ("THE HOST CONTROLS it maps to — collaborator/host.py (pause/resume/set_leash/set_proactivity/"
     "approve/decline/approve_proposal/veto)", "collaborator/host.py"),
    ("THE LEASH-CAP CRUX — collaborator/governance.py (a host/view leash is CAPPED at ~line 341-353)",
     "collaborator/governance.py"),
    ("THE VIEW setters — collaborator/view.py (set_leash/set_proactivity)", "collaborator/view.py"),
    ("WHAT IS PINNED — tests/test_collaborator_surface.py", "tests/test_collaborator_surface.py"),
    ("THE DESIGN — 09-seam-controls-design.md", "red-team/collaborator/09-seam-controls-design.md"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

CODE review + CERTIFICATION of ② Stage C, "the hand on the leash" — new control BUTTONS on the
SalienceOS "Collaborator" page. Background: `collaborator/surface.py` is a hardened localhost web
door over the Host (`Collaborator` in host.py). Stage B (watch + submit) and its door were ALREADY
externally certified (single-use bootstrap -> HttpOnly SameSite cookie, dual CSRF wall via a custom
X-Sal-Token header required on every route, strict Host/Origin pins, 5s timeouts, 64KiB body cap).
The "seam" is `govern_action` (governance.py) — the SOLE authority boundary, default-deny.

=== WHAT STAGE C ADDS (attack THIS) ===
ONE new route: `POST /control` behind the SAME `_guard_authed(check_origin=True)` as /submit. Body
`{"action": <name>, ...args}` is dispatched through a FIXED allowlist `_CONTROLS`:
  action            -> Host method            args
  pause/resume      -> host.pause()/resume()  (none)      RESTRICT
  set_proactivity   -> host.set_proactivity   (level)     EXPRESS config (surfacing only)
  set_leash         -> host.set_leash         (tool,leash) EXPRESS config (CAP-BOUNDED, see below)
  approve           -> host.approve           (task_id)   run an already-permitted HELD action (re-gated)
  decline           -> host.decline           (task_id)   RESTRICT
  approve_proposal  -> host.approve_proposal  (proposal_id) run a re-gated proposal
  veto              -> host.veto              (proposal_id) RESTRICT
Unknown action / bad-or-missing/oversized/non-string arg -> 400, Host NEVER touched. A control the
Host rejects (unknown tool/leash/level, task not awaiting, proposal gone) -> {"ok": false}. The page
buttons map 1:1 to these actions and re-poll /state; snapshot strings still render via textContent.

=== THE P-01 CLAIM (attack it in the CODE) ===
A control can only RESTRICT or EXPRESS host config — NEVER grant a capability or widen what Sal may
do autonomously. Load-bearing facts to VERIFY (don't assume):
  * The dispatch table is the ENTIRE control surface. `getattr(host, method_name)` uses method_name
    from the FIXED table, never from request data -> a crafted `action` cannot reach an arbitrary
    Host method. There is NO grant/mint method on the Host at all.
  * set_leash is the only "loosen" control. It writes session.leash_overrides[tool], but
    govern_action (governance.py ~341-344) applies the override THEN `apply_cap(leash,
    leash_cap(session, tool))` — the SIGNED grant is the hard ceiling; a host/view leash can tighten
    within it or loosen only UP TO it, never past it. A `proposed`-source action with a loosened
    leash is still forced to propose_first (~351-353). So "loosen a leash from the page" cannot raise
    autonomy beyond the signed cap.
  * set_proactivity only changes how often Sal SUGGESTS (surfacing), never what it may DO.
  * approve/approve_proposal RE-GATE the capability at run time on the worker (they execute an
    already-permitted action; they don't grant one).

=== THE CERTIFICATION CLAIMS (attack EACH) ===
  C1 (no authority via a control): no /control request — any action, any crafted body — can grant a
     capability, loosen a leash PAST the signed cap, set autonomous/intent.source, trigger an
     emission, or otherwise widen what Sal may autonomously do. Find a path from a control to widened
     authority, or a way `action`/args reach a non-allowlisted Host method or an authority field.
  C2 (door unchanged): /control is behind the same cookie + CSRF-header + Host/Origin pins as
     /submit; the Stage-B door is not weakened by the new route or the shared _read_json_body refactor.
     Find a bypass the controls opened (an unauthenticated or cross-origin control that lands).
  C3 (dispatch integrity / fail-safe): unknown action, missing/oversized/non-string arg -> 400 with
     the Host untouched; a Host rejection -> {"ok":false}; no crash, no partial call. Find an input
     that reaches the Host past validation, or a control that half-applies.
  C4 (scope honesty): the page exposes ONLY the 8 allowlisted controls; none is a hidden lever; a
     held/paused task's buttons map to approve/decline, proposals to approve_proposal/veto. Find a
     control the page shouldn't have or a mislabel that misleads about governance state.

=== OUT OF SCOPE (do NOT rate as bugs) ===
  - The Stage-B door mechanics themselves (already certified) except where /control newly exposes them.
  - Multi-user / login auth — a Stage-③ concern; single-user localhost is the point.
  - That a human CAN loosen a leash up to the signed cap — that is the DESIGNED "express your setting"
    (bounded by the cap); only loosening PAST the cap is a bug.
  - SSE/websocket vs polling; aesthetics.
  - The already-documented soft 429 cap / single-use-bootstrap prefetch trade.
"""

SYSTEM = """You are a senior application-security engineer doing an adversarial CODE review AND
CERTIFICATION of new control buttons on a localhost web door onto a governed AI agent. The door and
the Host controls are already certified; your job is to BREAK the P-01 claim that a control can only
RESTRICT or EXPRESS host config, never GRANT/widen authority — and to certify if you cannot. Assume
the author is wrong until proven otherwise. Be maximally adversarial about:
  - AUTHORITY (C1): trace every /control action + a hostile body. Can `action` or an arg reach a
    non-allowlisted Host method (look hard at the getattr dispatch), an authority field, or an
    emission? Can set_leash loosen a tool PAST its signed cap — read governance.py's leash resolution
    (override then apply_cap) and confirm the cap is the ceiling, not the override. Can approve/
    approve_proposal run something NOT already permitted?
  - DOOR (C2): is /control behind the exact same guard as /submit (cookie + X-Sal-Token + Host/Origin
    pin)? Did the shared _read_json_body refactor change /submit's behaviour or weaken any check?
  - DISPATCH (C3): unknown action, missing/non-string/oversized arg, extra keys, action collision
    with a dunder/attr — can any reach the Host or crash the handler?
Name file:line, give a CONCRETE request (action + args + headers), and say whether another layer
(the fixed table, the seam's apply_cap, the guard) independently catches it. Distinguish a real
authority/door break from a nit or a stated non-goal."""

USER_TMPL = """Review + CERTIFY the SHIPPED Stage-C controls below.

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
                 "X-Title": "SalienceOS Surface Controls Panel"},
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
    raw = HERE / "raw_surface_controls"
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
                    f"# Surface controls panel: {m}\n\n_cost={_fmt(r.get('cost'))} finish={r['finish']}_\n\n{r['content']}\n",
                    encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n=========== API COST — surface controls panel ===========")
    total, have = 0.0, False
    for m in MODELS:
        c = results.get(m, {}).get("cost")
        if isinstance(c, (int, float)):
            total += c; have = True
        print(f"  {m:32s} {_fmt(c)}")
    print(f"  {'TOTAL':32s} {_fmt(total) if have else 'n/a'}")


if __name__ == "__main__":
    main()
