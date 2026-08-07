"""Red-team PR-H2 (the SalienceOS compute-budget CONSUMER wired into quorum-agent).

Usage:
  python redteam_h2.py general   # Pass 1 — general reasoning panel (default)
  python redteam_h2.py coding    # Pass 2 — coding-specialist panel (grok anchored)
  python redteam_h2.py general x-ai/grok-4.5   # rerun a subset of a pass
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
BUNDLE = (HERE / "bundle_consumer.txt").read_text(encoding="utf-8")
KEY = os.environ["OPENROUTER_API_KEY"].strip()

PANELS = {
    # Pass 1 — reasoning-heavy: invariants, threat model, fail-open logic.
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

SYSTEM = """You are a senior security-and-correctness reviewer doing an adversarial red-team of PR-H2 of SalienceOS: the FIRST behavior-changing CONSUMER of a salience judgment system, wired into the quorum-agent host (a fork of hermes) as a test rig. It reads a per-turn `Directive` (recorded by the already-merged produce-only observer) and applies its compute_budget to the host's per-turn iteration budget. The CONTEXT section states the guarantees; the consumer, its host call site, ordering context, the tests, and the vendored judgment APIs it reads all follow. This code was ALREADY internally reviewed twice and its accepted findings fixed — your value is finding what those reviews missed, not re-reporting the design or already-fixed items.

Attack hardest, in order:
1. FAIL-OPEN / NEVER-BRICK: any path where the consumer crashes the host turn, propagates an exception (hunt specifically for a BaseException/SystemExit path from a host API it calls — config read, get_hermes_home, open/mkdir, the salienceos calls — that escapes its `except (Exception, SystemExit)`), or returns a budget < 1 / non-int that would set max_iterations to 0 and brick the agent.
2. A3 STALENESS: a concrete turn sequence where bounded_iterations applies turn N's own directive, or a 2-turns-stale one, instead of turn N-1's — i.e. the finalize-on-read reads the wrong window, or an off-by-one in which window is open at the :491 call site.
3. DENY-SHAPED / NO-RE-CLAMP: an input where a hard-deny or malformed directive is NOT treated as absent (returns a bogus budget), or where the recorded budget is silently re-clamped/re-derived instead of applied verbatim (Finding D violation).
4. RESTART-FALLBACK INTEGRITY: a path where _budget_from_disk returns an on-disk value WITHOUT the replay-verifying bus having validated the chain (corrupt/tampered tail accepted), or a TOCTOU between verify and read, or where a fresh-session read creates spurious files/buses.
5. CONCURRENCY / RESOURCE: a race or deadlock on the non-reentrant _LOCK (re-acquire on the finalize-on-read path?), a violation of the single-threaded SalienceBus contract, or unbounded growth (is _LAST_DIRECTIVE freed on session close like _BUSES?).
6. HONESTY: docstring/config text that overstates what the consumer does (claims it moves the budget when v0 is behavior-preserving), understates the kill-switch defaults, or misleads an operator.
7. TEST HONESTY: any guarantee above whose test would stay GREEN if the corresponding production line were sabotaged (mutation-blind, vacuous, circular, or tests that only seed _LAST_DIRECTIVE directly and thus never exercise the real finalize-on-read wiring). Name the mutation and predict the test result.

Calibration: be rigorous, concrete, honest. A finding needs a CONCRETE triggering input or call sequence; no concrete trigger => LOW at most. Anchor to file+function. OUT OF SCOPE (do not report): the internals of the vendored salienceos/ package (only flag the CONSUMER MISUSING its APIs); the PRODUCE path reviewed under PR-H1 except where this PR changed it; ADR 0001/0002 documented exclusions (consistent malicious rewrite, tail-truncation-across-reopen); the fact that v0 is intentionally behavior-preserving (that is by design, not a bug — but DO flag if it is misdescribed or if A3/A4 are therefore untestable as written). If nothing above LOW survives honest effort, say so plainly."""

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
        "max_tokens": 12000,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://salient-os.local/redteam",
            "X-Title": "SalienceOS Consumer Red-Team",
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
    raw = HERE / "raw_h2" / PASS
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
                    f"# Red-team (consumer, pass={PASS}): {m}\n\n_finish={r['finish']} "
                    f"seconds={r['seconds']} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]
    print(f"\nDone ({PASS}): {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
