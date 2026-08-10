"""Red-team + CERTIFY the PR #34 DELTA (fail-closed on empty PROTECTED_ROOTS) on top of the
already-5/5-CERTIFIED PR #33 codefence.

Per Josh's standing rule (external review on EVERY non-doc PR; doc-only is the sole exception),
this small fail-closed hardening gets its own external panel — no "trivial → skip" carve-out. It is
a DELTA panel: the base codefence was fully certified in the PR #33 panel; attack the NEW guard, not
the already-reviewed floor/disjointness/recognizer.

Reports per-model + total API cost (standing request).

Usage:  python red-team/collaborator/redteam_codefence_fix.py [openai/gpt-5.1 ...]
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
    ("THE DELTA — the exact diff PR #34 ships (read this first)",
     "red-team/collaborator/raw_codefence_fix/codefence_fix.diff"),
    ("THE CODE (full, for grounding) — collaborator/codefence.py",
     "collaborator/codefence.py"),
    ("THE CODE — collaborator/session.py (calls disjoint_from_code at construction)",
     "collaborator/session.py"),
    ("WHAT IS PINNED — tests/test_collaborator_codefence.py (incl. test_empty_protected_roots_fails_closed)",
     "tests/test_collaborator_codefence.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

DELTA CODE review + CERTIFICATION of a SHIPPED, MERGED one-guard change (salient-os PR #34) on top
of PR #33's codefence, which a 5-vendor panel ALREADY CERTIFIED (all 5 certified the headline "no
autonomous run_command self-rewrite while code_protection_available()==False"; 2 SOUND / 3
MINOR_ISSUES). Do NOT re-review the already-certified base (the autonomy-withheld floor, the
disjointness predicate's resolve()-based path checks, names_code_root's porousness, the honest
flag) — attack the NEW guard only.

BACKGROUND: codefence.py protects the Collaborator's own code (collaborator/ + salienceos/). Its
PROTECTED_ROOTS is resolved once at import from __file__ / salienceos.__file__. disjoint_from_code()
refuses a Session workspace that equals/contains/is-inside a protected root. The #33 panel's ONE
convergent finding (4/5, unanimous top-fix): if PROTECTED_ROOTS were EMPTY (both packages'
__file__ unresolvable), the for-loop in disjoint_from_code never executes, so the guard silently
became a NO-OP — a governance guard failing OPEN.

WHAT PR #34 SHIPS (attack + CERTIFY this):
  - disjoint_from_code() now starts with:
        if not PROTECTED_ROOTS:
            raise WorkspaceOverlapsCodeError("cannot locate the Collaborator's own code roots ...")
    i.e. FAIL CLOSED — refuse EVERY workspace (and thus every Session construction) when no code
    roots could be located, rather than silently protecting nothing. WorkspaceOverlapsCodeError is a
    ValueError subclass, composing with Session's other fail-loud construction checks.
  - A docstring wording change in _resolved_roots (no behavior).
  - A regression test test_empty_protected_roots_fails_closed (patches PROTECTED_ROOTS to () and
    asserts disjoint_from_code raises + Session(workspace=tmp) raises).

THE CERTIFICATION CLAIM (attack it): "When PROTECTED_ROOTS is empty, disjoint_from_code FAILS CLOSED
(raises) — the guard can never silently no-op — with no regression to the normal (non-empty-roots)
path or to Session construction."

OUT OF SCOPE (already certified in the #33 panel — do NOT re-report): the run_command autonomy
floor; the disjointness resolve()/symlink/case/traversal handling for the NON-empty case; the porous
names_code_root recognizer; the honest code_protected flag; the human-approved-rewrite residual;
OS-level structural prevention (deferred). ADR 0001/0002; the core gate internals.
"""

SYSTEM = """You are a senior application-security engineer doing an adversarial DELTA review AND
CERTIFICATION of a one-line fail-closed hardening in a governance guard. BREAK it, but also certify:
if you cannot break a claim, say so explicitly (a blocked attack is a valuable NON-finding). Assume
the author is wrong until proven otherwise. Concretely try to:
  - defeat the fail-closed intent: is there a path where PROTECTED_ROOTS is empty (or effectively
    empty) yet disjoint_from_code does NOT raise? Does the check `if not PROTECTED_ROOTS` correctly
    catch the empty tuple? Could PROTECTED_ROOTS be a non-empty tuple of INVALID/nonexistent roots
    that pass the emptiness check but still fail to protect (e.g. roots that never match any real
    workspace)? Is empty the only degenerate state, or is a PARTIAL PROTECTED_ROOTS (e.g. missing
    salienceos) still a fail-open the #34 guard does NOT address (note: that partial case was NOT
    claimed fixed here — is it worth flagging as residual)?
  - find a REGRESSION: does raising when PROTECTED_ROOTS is empty break any LEGITIMATE construction
    in a normal environment (where PROTECTED_ROOTS always has >=1 root)? Does the new early raise
    change the exception TYPE/message that existing callers/tests depend on? Does it shadow the
    later unresolvable-workspace raise?
  - check the guard's LAYER: is disjoint_from_code (construction-time) the right place, or is there a
    code path that consults PROTECTED_ROOTS for safety WITHOUT going through disjoint_from_code
    (leaving that path still fail-open on empty roots)? (e.g. names_code_root with empty roots — does
    anything rely on it as a boundary? it was certified porous in #33.)
  - is the raise itself safe (no crash, no info leak, deterministic)?
Name file:line, give a CONCRETE input/bypass, and state whether ANOTHER check independently catches
it. Distinguish a real guarantee-breaking bug from a belt-and-suspenders nit or a stated non-goal."""

USER_TMPL = """Review + CERTIFY the SHIPPED PR #34 delta below (fail-closed on empty PROTECTED_ROOTS).

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) /
CONCRETE INPUT OR BYPASS / WHY IT BREAKS A GUARANTEE / WHETHER ANOTHER CHECK CATCHES IT / FIX.
Then a CERTIFICATION LINE for the claim ("empty PROTECTED_ROOTS fails closed; no regression to the
normal path or Session construction") — CERTIFIED / NOT-CERTIFIED + one sentence. Then STEELMAN
(2-3 sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence, and the single
highest-value fix).

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
        text = p.read_text(encoding="utf-8") if p.exists() else f"(missing: {rel})"
        parts.append(f"\n\n########## {label} ##########\n\n{text}")
    return "".join(parts)


BUNDLE = build_bundle()


def call(model: str) -> dict:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": USER_TMPL.format(bundle=BUNDLE)}],
        "temperature": 0.3, "max_tokens": 16000,
        "usage": {"include": True},
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://salient-os.local/redteam",
                 "X-Title": "SalienceOS PR#34 codefence fail-closed Panel"},
        method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        return {"model": model, "error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:800]}"}
    except Exception as e:  # noqa: BLE001
        return {"model": model, "error": f"{type(e).__name__}: {e}"}
    dt = time.time() - t0
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or msg.get("reasoning") or ""
    usage = data.get("usage", {})
    if not content:
        return {"model": model, "error": f"empty content (finish={choice.get('finish_reason')})", "usage": usage}
    return {"model": model, "seconds": round(dt, 1), "usage": usage,
            "cost": usage.get("cost"), "content": content, "finish": choice.get("finish_reason")}


def _fmt_cost(c):
    return f"${c:.4f}" if isinstance(c, (int, float)) else "n/a"


def main():
    raw = HERE / "raw_codefence_fix"
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
                print(f"[FAIL] {m}: {r['error'][:200]}")
                (raw / f"{slug}.md").write_text(f"# {m}\n\nERROR: {r['error']}\n", encoding="utf-8")
            else:
                print(f"[ OK ] {m}  {r['seconds']}s  finish={r['finish']}  "
                      f"chars={len(r['content'])}  cost={_fmt_cost(r.get('cost'))}")
                (raw / f"{slug}.md").write_text(
                    f"# PR#34 codefence fail-closed panel: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — PR#34 codefence fail-closed panel =======")
    total = 0.0
    have_any = False
    for m in MODELS:
        r = results.get(m, {})
        c = r.get("cost")
        u = r.get("usage", {}) or {}
        toks = f"{u.get('prompt_tokens','?')}->{u.get('completion_tokens','?')} tok" if u else ""
        if isinstance(c, (int, float)):
            total += c
            have_any = True
        print(f"  {m:<34} {_fmt_cost(c):>10}   {toks}")
    print("  " + "-" * 54)
    print(f"  {'TOTAL':<34} {(_fmt_cost(total) if have_any else 'n/a'):>10}")
    print("============================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
