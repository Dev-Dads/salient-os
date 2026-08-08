"""Red-team the COLLABORATOR plain-language Stage plan (design review, not code).

Mirrors the proven redteam_h3.py OpenRouter runner, but the material under review
is a DESIGN PLAN (docs/collaborator-plain-language.md) plus the REAL salienceos API
surface it must build on, so reviewers can judge feasibility — not just prose.

Usage:
  python redteam_collaborator_plan.py            # general reasoning panel (default)
  python redteam_collaborator_plan.py general x-ai/grok-4.5   # rerun a subset
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
SOS = HERE.parent.parent  # repo root: D:\repo\salient-os
KEY = os.environ["OPENROUTER_API_KEY"].strip()

# The plan under review + vision context + the REAL core API it must build on.
_DOCS = [
    ("THE PLAN UNDER REVIEW — docs/collaborator-plain-language.md", "docs/collaborator-plain-language.md"),
    ("VISION CONTEXT — docs/ROADMAP-plain-language.md", "docs/ROADMAP-plain-language.md"),
]
_CORE = [
    "salienceos/interpreter/directive.py",
    "salienceos/interpreter/interpreter.py",
    "salienceos/control/govern.py",
    "salienceos/control/outcome.py",
    "salienceos/consumers/__init__.py",
    "salienceos/consumers/consume.py",
]

CONTEXT = """CONTEXT FOR REVIEWERS (facts about the system the plan builds on)

SalienceOS core invariant P-01: "salience influences; policy authorizes." High
salience buys more scrutiny / compute / retention / verification — NEVER more
capability, reach, or permission. Only policy grants authority.

The plan proposes a "Collaborator": a governed agent loop the project will OWN
(model client + our own tool-call parsing + our own toolset), where every tool
action is mediated by the existing salienceos judgment core before it runs.

Load-bearing facts about the real core (verified against source, included below):
- The governed flow per action is: issue_policy(...) -> emit SalienceSignal(s)
  (bounded ref-tokens only; a signal CANNOT carry a prompt/body/args by
  construction) -> interpret(policy, signals, key) -> Directive -> govern(...)/
  decide(directive, verdict) -> GovernedOutcome -> consume(outcome, now_days) ->
  (AdaptationDecision, MemoryRetention).
- BINDING KEY: decide() binds only when directive.subject == verdict.envelope_id.
  So each action must use ONE id as both the salience subject and the verifier
  envelope_id, or the outcome comes back unbound ("act on nothing").
- The core (salienceos/) is AST-enforced stdlib-only and no-async, so a
  network/model-client loop CANNOT live inside it; the Collaborator must be a new
  sibling package that consumes salienceos as a governed library.
- The current rig agent loop parses ONLY structured tool_calls; models that emit a
  tool call as plain text are silently dropped (the "box tool-exec gap"). The plan's
  "tool-reading we control" is meant to fix exactly this.
- The two learning channels (memory RETAIN inhibitor + weight HARD BLOCK) already
  exist and are tested in salienceos/consumers/. They are DORMANT unless a host
  drives an allow_adaptation=True, verified path. The plan claims the Collaborator
  becomes that host.

Judge the plan against these facts.
"""

SYSTEM = """You are a senior systems architect and security reviewer doing an adversarial design review of a PLAIN-LANGUAGE STAGE PLAN (not code) for SalienceOS. The owner will read this plan to approve a build; a technical spec sits under it. The real judgment-core API the plan must build on is included so you can judge FEASIBILITY, not just prose. This is the "before any code" review — your value is catching what's wrong, unsafe, or missing NOW.

Attack the plan hardest, in order:
1. P-01 INTEGRITY: any place the plan lets salience/importance buy AUTHORITY (capability, reach, permission) rather than only scrutiny/compute/retention — or any path where the governance seam can be bypassed so an action runs ungoverned. Scrutinize the "leash" and "trust dial": are they truly policy (authority), kept separate from salience (influence)? Is "importance buys effort, never permission" actually preserved end to end?
2. SEAM FEASIBILITY & COMPLETENESS: given the real flow (issue_policy -> signals -> interpret -> govern -> consume, bound by subject==envelope_id), can "EVERY action mediated before it happens" actually be built as described? Hunt action paths that could slip the seam: streamed/partial tool calls, multiple tool calls in one turn, tool calls the model emits mid-sentence, retries/errors, nested or chained tools, the model editing its own governance inputs.
3. THE HONESTY CLAIM: the plan claims owning the loop yields "hands that can't lie about what they did" and that nothing the model merely narrates is mistaken for a real action. Is that truly guaranteed by owning the parse, or are there gaps (a tool that runs but whose RESULT the model then misreports to the human; a governed-but-failed action reported as success; the conversational summary diverging from the audit trail)?
4. FAIL-SAFE POSTURE: when the model is confused/adversarial, the governance errors, a tool fails, or the human is absent — does the plan default SAFE (deny/hold/notify), or can it leak into acting? Name the concrete failure and what the plan should guarantee.
5. THE STAGE-4 TIE-IN: the plan says the Collaborator makes the two learning-channels' disagreement observable on real activity. Given consumers require allow_adaptation AND a VERIFIED outcome, is that claim sound? Does the plan under-specify how adaptation-eligible, verified actions even arise from a chat/tool loop? Could wiring an adaptation path here weaken the "importance never grants permission" rule?
6. SCOPE HONESTY: is "Step 0" a coherent, genuinely demonstrable first milestone, or does it hide dependencies (a real model, verification evidence, the binding-key plumbing)? Is anything OVERSOLD versus the plan's own "honest scope" section? Is the leash/trust-in-host-config caveat honest about what is NOT yet enforced?
7. MISSING PIECES: anything a plan like this MUST address and doesn't — concurrency, multi-step delegated plans, audit tamper-resistance, where verification evidence comes from for tool actions, the trust boundary of the conversational surface, an off switch, reversibility.

Calibration: be rigorous, concrete, honest. Every finding must name the PLAN SECTION and a CONCRETE failure scenario or unmet requirement — no vague "consider X". Distinguish a genuine design flaw from a detail correctly deferred to the technical spec. This is a v0 plan for a personal system; do not demand enterprise ceremony, but DO demand P-01 integrity and a safe default. If the plan is sound with only minor gaps, say so plainly — do not invent problems."""

USER_TMPL = """Adversarially review the DESIGN PLAN below (with the real core API for feasibility).

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / PLAN SECTION / CONCRETE FAILURE (the exact scenario, input, or unmet requirement) / WHY IT MATTERS / SUGGESTED FIX (minimal change to the plan). Then STEELMAN (2-3 sentences on what the plan gets genuinely right) and VERDICT (SOUND / MINOR_GAPS / SERIOUS_GAPS + one sentence).

=================== BEGIN MATERIAL ===================
{bundle}
=================== END MATERIAL ==================="""

PANEL = [
    "deepseek/deepseek-v4-pro",
    "x-ai/grok-4.5",
    "mistralai/mistral-medium-3-5",
    "moonshotai/kimi-k3",
    "z-ai/glm-5.2",
]
MODELS = sys.argv[2:] if len(sys.argv) > 2 else PANEL


def build_bundle() -> str:
    parts = [CONTEXT]
    for label, rel in _DOCS:
        parts.append(f"\n\n########## {label} ##########\n\n{(SOS / rel).read_text(encoding='utf-8')}")
    parts.append("\n\n########## REAL CORE API (salienceos/, for feasibility) ##########\n")
    for rel in _CORE:
        parts.append(f"\n\n----- {rel} -----\n\n{(SOS / rel).read_text(encoding='utf-8')}")
    return "".join(parts)


BUNDLE = build_bundle()


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
            "X-Title": "SalienceOS Collaborator Plan Red-Team",
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
    raw = HERE / "raw_plan"
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
                      f"out_tokens={r['usage'].get('completion_tokens','?')}  chars={len(r['content'])}")
                (raw / f"{slug}.md").write_text(
                    f"# Collaborator-plan red-team: {m}\n\n_finish={r['finish']} "
                    f"seconds={r['seconds']} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
