"""Red-team the SalienceOS interpreter against five coding models (same panel)."""

import concurrent.futures as cf
import json
import os
import pathlib
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).parent
BUNDLE = (HERE / "bundle_control.txt").read_text(encoding="utf-8")
KEY = os.environ["OPENROUTER_API_KEY"].strip()

MODELS = [
    "deepseek/deepseek-r1",
    "x-ai/grok-4.5",
    "qwen/qwen3-coder",
    "moonshotai/kimi-k2-thinking",
    "z-ai/glm-4.6",
]

SYSTEM = """You are a senior security-and-correctness reviewer doing an adversarial red-team of a Python "control seam" that composes two already-hardened components: an interpreter (invariant P-01: salience influences, policy authorizes) and a verifier (invariant M1: a VERIFIED verdict requires an executor-independent world fact). Be rigorous, concrete, calibrated; a flaw with no concrete triggering input is LOW at most; anchor every finding to file+function.

The seam's THREE invariants — attack these hardest:
1. Salience may only ESCALATE verification. The effective stakes = max(envelope.stakes, escalate_to) must NEVER fall below the policy-signed envelope.stakes. A directive/caller must not be able to LOWER the verifier's scrutiny.
2. Fail-closed clearance: decide() denies clearance on subject/envelope mismatch, a FAILED verdict, or achieved < required. No non-VERIFIED-enough action may be cleared.
3. Adaptation sealed gate: adaptation_allowed requires an actual Status.VERIFIED, never merely cleared or INTEGRITY_ATTESTED.

Also check: the verifier change (escalate_to on Verifier.verify; max_stakes/STAKES_ORDER) does not weaken any prior verifier guarantee and is a no-op when escalate_to=None; the depth<->stakes<->level mapping is total and correct; decide() is pure. In-scope adversary: a wrong/misfiring subsystem, a buggy caller, non-malicious corruption, and anyone trying to get clearance/adaptation/lower-scrutiny without earning it. A holder of the policy signing key is out of scope."""

USER_TMPL = """Below is the control seam, the verifier files it changed, supporting context, and the tests. Red-team the IMPLEMENTATION for concrete defects under the three invariants above.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW, +OUT-OF-SCOPE if it needs the signing key) / LOCATION (file+function) / CONCRETE TRIGGER (exact input/state) / WHY IT MATTERS / SUGGESTED FIX (minimal). Then STEELMAN (2-3 sentences on what is genuinely right) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence). If nothing above LOW survives honest effort, say so plainly.

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
        "max_tokens": 8000,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://salient-os.local/redteam",
            "X-Title": "SalienceOS Control Seam Red-Team",
        },
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        return {"model": model, "error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:800]}"}
    except Exception as e:  # noqa: BLE001
        return {"model": model, "error": f"{type(e).__name__}: {e}"}
    dt = time.time() - t0
    choice = (data.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content", "")
    usage = data.get("usage", {})
    return {"model": model, "seconds": round(dt, 1), "usage": usage, "content": content,
            "finish": choice.get("finish_reason")}


def main():
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
                (HERE / f"ct_{slug}.md").write_text(f"# {m}\n\nERROR: {r['error']}\n", encoding="utf-8")
            else:
                print(f"[ OK ] {m}  {r['seconds']}s  finish={r['finish']}  "
                      f"out_tokens={r['usage'].get('completion_tokens','?')}  chars={len(r['content'])}")
                (HERE / f"ct_{slug}.md").write_text(
                    f"# Red-team (control seam): {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (HERE / "ct_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
