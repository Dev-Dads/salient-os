"""Red-team the STAGE-4-LIVE wiring: does the Collaborator faithfully fire the
already-built two-channel disagreement, without misusing or weakening the gate?

Mirrors redteam_collaborator_plan.py (OpenRouter, general panel). The material is
the small collaborator wiring that drives the learning path + the salienceos gate
it drives + the tests + the live proof, so reviewers can judge faithfulness.

Usage:  python red-team/collaborator/redteam_stage4.py [general x-ai/grok-4.5 ...]
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
    ("THE WIRING UNDER REVIEW — collaborator/governance.py", "collaborator/governance.py"),
    ("collaborator/session.py", "collaborator/session.py"),
    ("TESTS — tests/test_collaborator_stage4.py", "tests/test_collaborator_stage4.py"),
    ("LIVE PROOF — red-team/collaborator/stage4_live_proof.py", "red-team/collaborator/stage4_live_proof.py"),
    ("LIVE PROOF OUTPUT — stage4_live_proof_output.txt", "red-team/collaborator/stage4_live_proof_output.txt"),
    ("THE GATE (already built + reviewed) — salienceos/consumers/consume.py", "salienceos/consumers/consume.py"),
    ("salienceos/consumers/adaptation.py", "salienceos/consumers/adaptation.py"),
    ("salienceos/consumers/memory.py", "salienceos/consumers/memory.py"),
    ("salienceos/consumers/handoff.py", "salienceos/consumers/handoff.py"),
    ("salienceos/interpreter/directive.py", "salienceos/interpreter/directive.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

SalienceOS invariant P-01: salience influences (scrutiny/compute/retention), policy
authorizes (capability). The memory-retention governor + weight-adaptation gate — two
deliberately DISAGREEING channels — ALREADY EXIST and are tested in salienceos/consumers/
(do NOT re-review their internals; assume them correct). For high-salience high-risk
content the memory channel RETAINS a non-decaying inhibitor (a warning) while the weight
channel HARD BLOCKS learning. The trigger is the interpreter's recorded
AdaptationRationale.RISK_EXCEEDED (asserted over-cap risk under an allow_adaptation policy).

The change under review is small "Stage-4-live" wiring in the Collaborator (a governed
agent-loop host that consumes salienceos): when a session sets allow_adaptation, the seam
(a) emits a Facet.ADAPTATION signal, and (b) after the governed outcome, calls
consume(outcome, now_days) and records (AdaptationDecision, MemoryRetention) plus a
`disagreement` flag. The claim: this makes the dormant disagreement fire on a real
governed action (a risky+important write), promoting the Stage-1 disagreement proof from
a unit fixture to a live worker. Note the demo action RAN but did NOT verify (risk 0.9 ->
FULL depth, one source) — the design says the disagreement fires from RISK_EXCEEDED on a
BOUND outcome regardless of clearance.

Judge the WIRING's faithfulness, not the gate's internals.
"""

SYSTEM = """You are a senior correctness-and-safety reviewer doing an adversarial review of a small integration ("Stage-4-live") that makes SalienceOS's already-built two-channel learning disagreement fire through a host (the Collaborator). The gate itself (salienceos/consumers/) is out of scope — assume it correct. Your job: is the WIRING faithful, honest, and safe?

Attack hardest, in order:
1. FAITHFULNESS: does the wiring exercise the gate as designed, or does it fake/short-circuit the disagreement? Is the `disagreement` flag (not nominated AND handoff present AND memory.inhibitor) a correct read of the two records, or could it report a disagreement that didn't happen, or miss one that did? Is consume() called on the RIGHT outcome (bound, same action)?
2. P-01 INTEGRITY: does emitting a Facet.ADAPTATION signal, or the host-supplied importance/risk, let salience or the model buy learning/permission it shouldn't? Can the model influence whether its own risky action is learned or inhibited? Is risk genuinely host-computed (not model-selectable)? Could allow_adaptation + a crafted risk value nominate a risky action FOR learning (the opposite failure — an over-cap risk that is NOT inhibited)?
3. HONESTY OF THE CLAIM: the demo action ran but did not verify. Is it honest to call this "the disagreement fires live"? Does the proof/synthesis overclaim (e.g., imply the write was verified, or that the inhibitor's 'ephemeral' class means something it doesn't)? Is the "no decay" evidence sound?
4. FAIL-SAFE: the consume() call is wrapped so a failure yields no records rather than blocking the action — is that the right direction here, or could a swallowed exception hide a real inhibitor (fail-open on a warning that should have been pinned)? Should a learning-path error be surfaced?
5. SCOPE/DORMANCY: is it true that allow_adaptation=False produces no ADAPTATION signal, no consume, and no inhibitors (host-dormant)? Any path where an inhibitor leaks when adaptation is off, or where a non-risky action gets inhibited?
6. MISUSE OF THE API: any incorrect use of consume/nominate/retain/effective_weight, wrong now_days handling, or a place the wiring re-derives what it should only consume.

Calibration: concrete, honest; every finding names the file+location and a concrete scenario. This is a small integration on reviewed safety code — do not re-review the gate, and do not demand ceremony; DO demand faithfulness, P-01, and an honest claim. If it's sound with only minor notes, say so."""

USER_TMPL = """Adversarially review the STAGE-4-LIVE WIRING below.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION / CONCRETE SCENARIO / WHY IT MATTERS / SUGGESTED FIX. Then STEELMAN (2-3 sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence).

=================== BEGIN MATERIAL ===================
{bundle}
=================== END MATERIAL ==================="""

PANEL = ["deepseek/deepseek-v4-pro", "x-ai/grok-4.5", "mistralai/mistral-medium-3-5",
         "moonshotai/kimi-k3", "z-ai/glm-5.2"]
MODELS = sys.argv[2:] if len(sys.argv) > 2 else PANEL


def build_bundle() -> str:
    parts = [CONTEXT]
    for label, rel in _FILES:
        parts.append(f"\n\n########## {label} ##########\n\n{(SOS / rel).read_text(encoding='utf-8')}")
    return "".join(parts)


BUNDLE = build_bundle()


def call(model: str) -> dict:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": USER_TMPL.format(bundle=BUNDLE)}],
        "temperature": 0.3, "max_tokens": 16000,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS Stage-4-live Red-Team"},
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
    if not content:
        return {"model": model, "error": f"empty content (finish={choice.get('finish_reason')})"}
    return {"model": model, "seconds": round(dt, 1), "usage": data.get("usage", {}),
            "content": content, "finish": choice.get("finish_reason")}


def main():
    raw = HERE / "raw_stage4"
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
                print(f"[ OK ] {m}  {r['seconds']}s  finish={r['finish']}  chars={len(r['content'])}")
                (raw / f"{slug}.md").write_text(
                    f"# Stage-4-live red-team: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
