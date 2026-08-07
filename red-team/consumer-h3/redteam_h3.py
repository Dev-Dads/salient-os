"""Red-team PR-H3 (the compute window opens: ATTENTION from turn activity,
headroom-widened policy — the first governed knob genuinely governs).

Usage:
  python redteam_h3.py general   # Pass 1 — general reasoning panel (default)
  python redteam_h3.py coding    # Pass 2 — coding-specialist panel (grok anchored)
  python redteam_h3.py general x-ai/grok-4.5   # rerun a subset of a pass
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
BUNDLE = (HERE / "bundle_h3.txt").read_text(encoding="utf-8")
KEY = os.environ["OPENROUTER_API_KEY"].strip()

PANELS = {
    # Pass 1 — reasoning-heavy: escalation bounds, feedback loops, fail-open.
    "general": [
        "deepseek/deepseek-v4-pro",
        "x-ai/grok-4.5",
        "mistralai/mistral-medium-3-5",
        "moonshotai/kimi-k3",
        "z-ai/glm-5.2",
    ],
    # Pass 2 — code-specifics on the fixed code; grok LOCKED as the anchor.
    "coding": [
        "qwen/qwen3-coder-plus",
        "kwaipilot/kat-coder-pro-v2.5",
        "moonshotai/kimi-k2.7-code",
        "poolside/laguna-s-2.1",
        "x-ai/grok-4.5",
    ],
}

PASS = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in PANELS else "general"
MODELS = PANELS[PASS]
if len(sys.argv) > 2:  # rerun subset: pass model ids after the pass name
    MODELS = sys.argv[2:]

SYSTEM = """You are a senior security-and-correctness reviewer doing an adversarial red-team of PR-H3 of SalienceOS: the change that makes the compute-budget knob GENUINELY GOVERN in the quorum-agent test rig. PR-H2 (already merged and reviewed) wired the consumer but was behavior-preserving (pinned policy window, no ATTENTION signal). THIS PR widens the produce-policy window to [floor, floor + salience.compute_headroom] (new config knob, default 0 = still pinned) and synthesizes ONE ATTENTION signal per turn window from its attributed activity count, so a busy turn buys the NEXT turn extra iterations. The CONTEXT section states the guarantees; the changed producer, its tests, ordering context, and the vendored judgment APIs follow. This code was ALREADY internally reviewed twice and its accepted findings fixed — your value is finding what those reviews missed, not re-reporting the design or already-fixed items.

Attack hardest, in order:
1. ESCALATION / RUNAWAY: any path where the applied budget exceeds the operator floor + headroom, compounds across turns (a ratchet/feedback loop through any state the previous application mutated), or where headroom is conjured from a bad config value. The agent's own activity drives its own next-turn budget — hunt the loop for instability, and hunt every floor derivation for pollution by a previously-applied value.
2. FAIL-OPEN / NEVER-BRICK: any path in the CHANGED code that crashes the host turn, escapes the (Exception, SystemExit) containment, or yields an applied budget < 1.
3. A4 FLOOR INTEGRITY: a quiet turn (zero attributed events) must yield EXACTLY the floor; the finalize-on-read floor must be the session's pristine operator budget, not a ratcheted or re-derived value.
4. A3 WITH MOVEMENT: budgets now differ turn to turn, so a stale/self-read directive is a REAL wrong number, not a harmless echo. Find a turn sequence applying the wrong turn's budget.
5. AUDIT HONESTY / FENCE: the synthesized ATTENTION signal must be ON the bus record whenever it informed the directive (never interpreted off-record), its provenance must stay ref-shaped (no tool payload), and a failed publish must drop the signal (directive falls to the floor).
6. HONESTY: any docstring/config/box text overstating or understating what moves, the bounds, the saturation, or the default posture.
7. TEST HONESTY: any guarantee above whose test stays GREEN when its production line is sabotaged (mutation-blind, vacuous, or expected values that coincide with a wrong implementation). Name the mutation and predict the result.

Calibration: be rigorous, concrete, honest. A finding needs a CONCRETE triggering input or call sequence; no concrete trigger => LOW at most. Anchor to file+function. OUT OF SCOPE (do not report): the internals of the vendored salienceos/ package (only flag the rig MISUSING its APIs); the consumer read path and PR-H1/H2 behavior except where THIS PR changed it; ADR 0001/0002 documented exclusions; the fact that headroom is operator-uncapped (an operator may set any int — their machine, documented); the coarseness of activity-count-as-attention (documented v1 proxy — but DO flag if docs oversell it). If nothing above LOW survives honest effort, say so plainly."""

USER_TMPL = """Red-team the IMPLEMENTATION below for concrete defects under the stated guarantees.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW, +OUT-OF-SCOPE if it needs vendored-internals) / LOCATION (file+function) / CONCRETE TRIGGER (exact call sequence/input/state) / WHY IT MATTERS / SUGGESTED FIX (minimal). Then STEELMAN (2-3 sentences on what is genuinely right) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence).

=================== BEGIN MATERIAL ===================
{bundle}
=================== END MATERIAL ==================="""


def call(model: str) -> dict:
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER_TMPL.format(bundle=BUNDLE)},
        ],
        "temperature": 0.3,
        "max_tokens": 16000,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://salient-os.local/redteam",
            "X-Title": "SalienceOS H3 Red-Team",
        },
        method="POST",
    )
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
    if not content:
        return {"model": model, "error": f"empty content (finish={choice.get('finish_reason')}, "
                                         f"keys={sorted(msg.keys())})"}
    usage = data.get("usage", {})
    return {"model": model, "seconds": round(dt, 1), "usage": usage, "content": content,
            "finish": choice.get("finish_reason")}


def main():
    raw = HERE / "raw_h3" / PASS
    raw.mkdir(parents=True, exist_ok=True)
    results = {}
    print(f"pass={PASS}  models={MODELS}")
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
                      f"out_tokens={r['usage'].get('completion_tokens','?')}  chars={len(r['content'])}")
                (raw / f"{slug}.md").write_text(
                    f"# Red-team (H3, pass={PASS}): {m}\n\n_finish={r['finish']} "
                    f"seconds={r['seconds']} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]
    print(f"\nDone ({PASS}): {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
