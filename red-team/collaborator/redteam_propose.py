"""Red-team the ① PROPOSE-CHANNEL design: does the Collaborator's new two-way
channel (it brings the host an unprompted, governed proposal to approve/veto) keep
P-01 intact — i.e. can proposing ever buy an action authority or a run it should not
get, or weaken the leash/capability gate?

Mirrors redteam_stage4.py (OpenRouter, general panel). The material is the DESIGN doc
plus the existing seam it reuses (governance.py/session.py/tools.py/toolcall.py/loop.py),
so reviewers judge whether the design's central claim — "surfacing grants nothing;
approval + the capability gate are the only authority" — actually holds.

Usage:  python red-team/collaborator/redteam_propose.py [general x-ai/grok-4.5 ...]
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
    ("THE DESIGN UNDER REVIEW — 01-propose-design.md", "red-team/collaborator/01-propose-design.md"),
    ("THE SEAM IT REUSES — collaborator/governance.py", "collaborator/governance.py"),
    ("collaborator/session.py", "collaborator/session.py"),
    ("collaborator/tools.py", "collaborator/tools.py"),
    ("collaborator/toolcall.py", "collaborator/toolcall.py"),
    ("collaborator/loop.py (approve() lives here)", "collaborator/loop.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

SalienceOS invariant P-01: salience INFLUENCES (scrutiny/compute/retention/verification),
policy AUTHORIZES (capability). High importance can buy an action more scrutiny; it can
NEVER buy it a capability or a run.

The Collaborator is a governed agent-loop host that consumes the salienceos core. Today
(Step 0, already built + proven live) it is reactive: you instruct it, each tool call is
its own governed action through govern_action() — capability gate (the one core-enforced
authority), then a host-config LEASH (act_then_report / propose_first / notify_only), then
execute+verify. A propose_first action returns HELD and is run only when the host calls
approve() (also already built).

UNDER REVIEW is the DESIGN for Step 1 — the PROPOSE CHANNEL: the Collaborator originates a
proposal ON ITS OWN INITIATIVE (a model suggests a candidate action from workspace
context), governs it as HELD via the SAME seam, and — if a host-config "proactivity" dial
is met — SURFACES it for the host to approve or veto. The design's central safety claim is:
**surfacing a proposal grants no authority; approving runs it through the same
approve()/capability-gate path; therefore the model influencing HOW MANY proposals surface
can at most create noise, never get an action run.** It also adds an optional host-supplied
`leash` parameter to govern_action (per-task leash), claimed to only ever choose among the
three defined leash levels and never widen authority.

Judge the DESIGN's faithfulness to P-01 and the honesty of that central claim. The core
(salienceos/) and the Step-0 seam are assumed correct; attack the NEW channel and the new
leash parameter.
"""

SYSTEM = """You are a senior correctness-and-safety reviewer doing an adversarial review of the DESIGN for a new "propose channel" in the Collaborator (a host that consumes SalienceOS). The core judgment system and the existing Step-0 seam are out of scope — assume them correct. Your job: does this new channel keep P-01 intact and is its central claim honest?

Attack hardest, in order:
1. AUTHORITY LEAK: find ANY path where proposing, surfacing, or the proactivity dial lets an action run, or gain a capability/leash, WITHOUT an explicit host approval that re-passes the capability gate. Is the claim "surfacing grants nothing" actually true given the design? Consider: does approve() re-check capability at run time, or trust the proposal's earlier check? Could a proposal originated when a capability WAS granted be approved later after it was revoked? Could forcing leash=PROPOSE_FIRST at origination interact badly with approve() (which ignores leash and just runs)?
2. CONFIDENCE AS A BACKDOOR: confidence is model-self-rated and gates surfacing. The design says that is safe because surfacing grants nothing. Stress that: is there ANY way the confidence value (or the rationale text, or the candidate args) reaches an authority or verification decision? Could a proposal's importance (salience) buy it more than scrutiny? Could an eager dial + a crafted candidate cause a risky action to be surfaced AND rubber-stamped by a plausible flow?
3. LEASH PARAMETER SAFETY: the new host `leash` param on govern_action. Can it widen authority (run something that should be held)? Can an invalid/malicious value cause an unleashed run? Is it truly host-only (never threaded from model output)? Does defaulting preserve today's behaviour exactly?
4. FAIL-OPEN: proposer/model error, unparseable suggestion, "nothing to propose", a candidate that escapes the workspace or names an ungranted capability — does each fail CLOSED (no proposal, nothing runs), or is there a path that surfaces/runs something anyway?
5. HONESTY OF THE CLAIM: is it honest to call this "grows with you, safely"? Does the design overclaim inertness (a surfaced proposal changes nothing until approved)? Any place origination itself has a side effect (writes, audit that could be spoofed, state that a later action trusts)?
6. AUDIT / PROVENANCE: proposals are originated by the collaborator, not the user. Is that distinction preserved in the audit trail, or could an originated action later be indistinguishable from a user-authorised one in a way that matters?

Calibration: concrete, honest; every finding names the location (design section or file) and a concrete scenario. This is a DESIGN review of a small channel on reviewed safety code — do not re-review the core, do not demand ceremony; DO demand that the central claim (surfacing grants nothing) be literally true and that the new leash param cannot widen authority. If it is sound with only minor notes, say so plainly."""

USER_TMPL = """Adversarially review the PROPOSE-CHANNEL DESIGN below.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION / CONCRETE SCENARIO / WHY IT MATTERS / SUGGESTED FIX. Then STEELMAN (2-3 sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence).

=================== BEGIN MATERIAL ===================
{bundle}
=================== END MATERIAL ==================="""

PANEL = ["deepseek/deepseek-v4-pro", "x-ai/grok-4.5", "mistralai/mistral-medium-3-5",
         "moonshotai/kimi-k3", "z-ai/glm-5.2"]
MODELS = sys.argv[1:] if len(sys.argv) > 1 else PANEL


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
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS Propose-Channel Red-Team"},
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
    raw = HERE / "raw_propose"
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
                    f"# Propose-channel red-team: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
