"""Red-team the SalienceOS consumer gates against the five-model general panel."""

import concurrent.futures as cf
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).parent
BUNDLE = (HERE / "bundle_consumers.txt").read_text(encoding="utf-8")
KEY = os.environ["OPENROUTER_API_KEY"].strip()

MODELS = [
    "deepseek/deepseek-v4-pro",
    "x-ai/grok-4.5",
    "mistralai/mistral-medium-3-5",
    "moonshotai/kimi-k3",
    "z-ai/glm-5.2",
]
if len(sys.argv) > 1:  # rerun subset: pass model ids as args
    MODELS = sys.argv[1:]

SYSTEM = """You are a senior security-and-correctness reviewer doing an adversarial red-team of the newest build stage of SalienceOS: the consumer gates that make the control seam's GovernedOutcome load-bearing (a memory-retention governor and a weight-adaptation gate), plus the interpreter's new recorded rationale, the seam's self-describing outcome, and the bus's reader/replay. The CONTEXT section states the invariants; the code and its tests follow. This code was already internally reviewed twice — your value is finding what those reviews missed, not re-reporting the design.

Attack hardest, in order:
1. Any path where the two channels' DISAGREEMENT property fails: an inhibitor that can be manufactured without an asserted over-cap risk, lost despite one, misattributed to the wrong subject, or made to decay.
2. Any way nomination exceeds its single predicate, anything that lets unverified content be nominated, any promote/apply surface, any capability leak through the consumers.
3. The seam boundary: a directive or outcome shape that reaches the gates and makes them crash (a crash is not a deny), lie, or act on withheld identity.
4. The bus: replay/reader correctness under adversarial files within ADR 0001's stated scope (accidental corruption IS in scope; consistent rewrite and tail-truncation-across-reopen are documented exclusions — do not report those); the audit fence (can anything prompt-sized become durable?).
5. Test honesty: an invariant claimed above whose test would stay green if the code were sabotaged.

Calibration: be rigorous, concrete, and honest. A finding needs a CONCRETE triggering input; no concrete trigger means LOW at most. Anchor to file+function. Hand-forged GovernedOutcome/Verdict/MemoryRetention objects and holders of the policy signing key are OUT OF SCOPE (equivalent to bypassing the verifier) — but hand-built DIRECTIVES reaching decide(), and adversarial bus FILES, are in scope. If nothing above LOW survives honest effort, say so plainly."""

USER_TMPL = """Red-team the IMPLEMENTATION below for concrete defects under the stated invariants.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW, +OUT-OF-SCOPE if it needs a forged record or the signing key) / LOCATION (file+function) / CONCRETE TRIGGER (exact input/state) / WHY IT MATTERS / SUGGESTED FIX (minimal). Then STEELMAN (2-3 sentences on what is genuinely right) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence).

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
            "X-Title": "SalienceOS Consumers Red-Team",
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
    raw = HERE / "raw"
    raw.mkdir(exist_ok=True)
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
                (raw / f"cs_{slug}.md").write_text(f"# {m}\n\nERROR: {r['error']}\n", encoding="utf-8")
            else:
                print(f"[ OK ] {m}  {r['seconds']}s  finish={r['finish']}  "
                      f"out_tokens={r['usage'].get('completion_tokens','?')}  chars={len(r['content'])}")
                (raw / f"cs_{slug}.md").write_text(
                    f"# Red-team (consumers): {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "cs_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
