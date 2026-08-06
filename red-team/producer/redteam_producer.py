"""Red-team PR-H1 (the SalienceOS producer wired into quorum-agent).

Usage:
  python redteam_producer.py general   # Pass 1 — general reasoning panel (default)
  python redteam_producer.py coding    # Pass 2 — coding-specialist panel (grok anchored)
  python redteam_producer.py general x-ai/grok-4.5   # rerun a subset of a pass
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
BUNDLE = (HERE / "bundle_producer.txt").read_text(encoding="utf-8")
KEY = os.environ["OPENROUTER_API_KEY"].strip()

PANELS = {
    # Pass 1 — reasoning-heavy: invariants, threat model, fail-closed logic.
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

SYSTEM = """You are a senior security-and-correctness reviewer doing an adversarial red-team of PR-H1 of SalienceOS: a PRODUCE-ONLY observer that wires the SalienceOS judgment system into the quorum-agent host (a fork of hermes) as a test rig. The CONTEXT section states the guarantees; the observer, the dispatch seam, the host emit-sites, the tests, and the vendored APIs it calls all follow. This code was ALREADY internally reviewed twice and its accepted findings fixed — your value is finding what those reviews missed, not re-reporting the design or the already-fixed items.

Attack hardest, in order:
1. PRODUCE-ONLY / NEVER-CRASH-THE-HOST: any path where the observer changes what the agent does, or lets an exception reach the host. The three containment layers all catch `except Exception`, so hunt specifically for a BaseException/SystemExit/KeyboardInterrupt path from a host API the observer calls (config read, get_hermes_home, mkdir/open, the salienceos calls) that escapes those guards — beyond the already-fixed get_config_value.
2. FAIL-CLOSED ATTRIBUTION: a concrete sequence of hook calls that records a signal with no open window, a closed window, a mismatched turn_id, or cross-session/cross-turn; or a way the durable subject/filename leaks the raw session_id.
3. AUDIT FENCE: any input (tool_name/status/provider/args) that puts unbounded or non-ref content on the bus, OR makes a produced signal FAIL valid_signal (noise / dropped-with-log).
4. SEAM: any way enabling salience changes invoke_hook's return value, the relay dispatch, hook ordering, or the EFFECT of a hook (not just firing a previously-dead observational hook, which is expected and acceptable).
5. CONCURRENCY / RESOURCE: a race or deadlock on _LOCK / _WINDOWS / _BUSES, a violation of the single-threaded SalienceBus contract, or unbounded growth on a long-lived host.
6. TEST HONESTY: any guarantee above whose test would stay green if the code were sabotaged (mutation-blind, vacuous, over-mocked, or circular).

Calibration: be rigorous, concrete, honest. A finding needs a CONCRETE triggering input or call sequence; no concrete trigger => LOW at most. Anchor to file+function. OUT OF SCOPE (do not report): the internals of the vendored salienceos/ package (verbatim already-reviewed copy — only flag the OBSERVER MISUSING its APIs); ADR 0001/0002 documented exclusions (consistent malicious rewrite, tail-truncation-across-reopen); holders of secrets. If nothing above LOW survives honest effort, say so plainly."""

USER_TMPL = """Red-team the IMPLEMENTATION below for concrete defects under the stated guarantees.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW, +OUT-OF-SCOPE if it needs vendored-internals or secret holders) / LOCATION (file+function) / CONCRETE TRIGGER (exact call sequence/input/state) / WHY IT MATTERS / SUGGESTED FIX (minimal). Then STEELMAN (2-3 sentences on what is genuinely right) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence).

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
            "X-Title": "SalienceOS Producer Red-Team",
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
    raw = HERE / "raw" / PASS
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
                    f"# Red-team (producer, pass={PASS}): {m}\n\n_finish={r['finish']} "
                    f"seconds={r['seconds']} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]
    print(f"\nDone ({PASS}): {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
