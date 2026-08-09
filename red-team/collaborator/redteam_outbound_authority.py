"""External DESIGN panel on ADR 0003 — outbound authority + the prohibition floor (PRE-BUILD).

This is Josh's empirical-adversarial rule applied to a DESIGN, not shipped code: pressure-test
the ADR externally BEFORE any implementation. The panel attacks the DECISION — the two-layer
split, Layer A's egress-bypass surface, Layer B's enforceability/dual-use line, coherence with
P-01 and the actual seam, and what's mis-scoped or missing — not a diff.

Bundles the proposed ADR PLUS the real seam it claims to bind to (the capability gate, the tool
registry, the signed-caps model, the read-only research loop) so reviewers can check the design
is coherent with the code and actually buildable as described — not just internally tidy.

Reports per-model + total API cost (Josh's standing request).

Usage:  python red-team/collaborator/redteam_outbound_authority.py [openai/gpt-5.1 ...]
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
    ("THE PROPOSAL UNDER REVIEW — docs/adr/0003-outbound-authority-and-prohibition-floor.md",
     "docs/adr/0003-outbound-authority-and-prohibition-floor.md"),
    ("THE SEAM IT BINDS TO — collaborator/governance.py (the capability gate, the controlled-"
     "location deny, the run_command floor — where the new denies would live)",
     "collaborator/governance.py"),
    ("THE TOOL REGISTRY — collaborator/tools.py (capabilities, verify_mode, is_controlled_location)",
     "collaborator/tools.py"),
    ("THE CAPABILITY MODEL — collaborator/policycaps.py (signed caps, granted_capabilities, leash cap)",
     "collaborator/policycaps.py"),
    ("THE READ-ONLY RESEARCH LOOP — collaborator/research.py (the web_research trust level this ADR lifts)",
     "collaborator/research.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

This is a DESIGN review of a PROPOSED architecture decision record (ADR 0003), NOT a code review of
a shipped diff. Nothing below the ADR is built yet; the code files are the EXISTING seam the ADR
claims it will extend, included so you can judge whether the design is coherent with the real
system and actually buildable as described.

SalienceOS is a salience-based AI control plane. Core invariant P-01: salience INFLUENCES, policy
AUTHORIZES — a salience signal can nudge a knob WITHIN a signed authority window but can never grant
or widen authority. The "Collaborator" is a governed agent that imports the core: a PROPOSER model
suggests actions; NOTHING runs until a human approves; every action passes ONE core-enforced
capability gate (directive.grants_capability(tool.capability)) plus a second "leash" axis
(act_then_report / propose_first / notify_only) and a workspace fence. Capabilities are carried by a
host-SIGNED PolicyCaps (the model cannot forge one; salience cannot add one). The VERIFIER enforces
"the hands can't lie": a tool's claimed side effects are checked against the independently-observed
world.

ALREADY SHIPPED (the INWARD half of the seam this ADR completes outward):
  - Controlled-location hard-deny + staging: a PROPOSER-originated write into a controlled subtree
    (default `.github/**`, which carries repo-level authority) is DENIED so the proposer stages the
    artifact to reachable scratch; a human approves the PLACEMENT and the Collaborator executes it.
    Keyed on intent.source == "proposed" (hardcoded proposer origin; the model's JSON cannot set it).
  - The run_command floor: a proposer-originated shell command is floored to propose_first — the
    unbounded-reach tool always needs a human hand. This ADR calls that the "down-payment" on Layer A.

Today ALL three real tools are LOCAL (fs.write:project, fs.read:project, shell.exec). There is NO
egress capability; the only way off the machine is run_command.

OUT OF SCOPE (do not re-litigate): the correctness of the core capability gate / signed PolicyCaps
(assume correct); ADR 0001/0002 (audit-chain + persistence trust-domain scoping). Attack the ADR's
DECISION.
"""

SYSTEM = """You are a senior security ARCHITECT doing an adversarial DESIGN review of a proposed ADR
that (Layer A) adds default-deny, allowlisted network egress as a new capability, and (Layer B) adds
an un-grantable "prohibition floor" that no authority may satisfy (no autonomous infiltration /
unauthorized access / offense against a third party). Your job is to find where this design is
wrong, unenforceable, incoherent with the existing seam, or dangerously incomplete — not to praise
it. Assume the author is over-confident until proven otherwise.

Attack hardest, in roughly this order:

1. THE TWO-LAYER DECOMPOSITION. Is "grantable authority vs un-grantable prohibition" a real,
   stable distinction, or does it collapse under pressure? Is the prohibition genuinely a category
   error to model as a capability (as claimed), or is it just a very-high-risk grant in disguise?
   Does the split leave a seam a real action falls through (an action that is neither cleanly
   grantable nor cleanly prohibited)?

2. LAYER A — EGRESS BYPASS + OBSERVER SOUNDNESS. Break the default-deny allowlist: destination
   confusion (IDN/homograph/punycode, case, trailing dot, userinfo@host, port, path-vs-host),
   HTTP redirect to an un-allowlisted host, DNS rebinding, SSRF to internal/link-local/metadata
   endpoints, a CNAME/wildcard that widens a host grant. Attack the "one mediated client that is
   ALSO the observer" (same-channel) verifier: what can it NOT see, and does the ADR oversell it?
   Attack the GET-only / method-split assumption (is a GET ever side-effecting or exfiltrating via
   query/headers/DNS?). Attack the proposer-stages / human-approves new-channel flow for forgery
   or TOCTOU (allowlist mutated between approval and emission).

3. LAYER B — PROHIBITION ENFORCEABILITY + DUAL-USE. Can a DETERMINISTIC recognizer (the ADR insists
   it must be deterministic, not an LLM classifier, because a classifier is "influence" and P-01
   forbids influence authorizing) actually recognize infiltration/offense at all? Give concrete
   FALSE-NEGATIVES (a real attack shaped so the recognizer passes it) AND FALSE-POSITIVES (a
   legitimate authorized pentest/CTF/defensive action wrongly refused). Attack the "out-of-band
   legitimacy artifact" as an unspecified hand-wave: what stops it from collapsing back into the
   in-band grant path it is supposed to be separate from? Is "autonomy control, not misuse-proof
   sandbox" an honest scoping or a convenient escape hatch? Where exactly (above vs below the gate)
   must the deny live to be sound, and does the ADR place it correctly?

4. COHERENCE WITH P-01 AND THE REAL SEAM. Does the design actually fit the capability model in
   policycaps.py / governance.py, or does it require something the seam can't express? Does lifting
   web_research to "read-only GET within the allowlist" as PERCEPTION (never surfaced, grants no
   authority) smuggle authority or exfiltration into the read path? Does anything here violate P-01
   (influence authorizing) or contradict the shipped inward staging model?

5. WHAT IS MISSING OR MIS-SCOPED. Response-size / content limits, timeouts, cost/rate as a DoS or
   a covert channel, secrets leaking OUT in a request, prompt-injection from FETCHED content driving
   the next proposal, logging of request/response bodies (audit vs secret-leak tension), the residual
   run_command raw-reach the ADR admits. Name the ONE change that would most improve the decision.

Calibration: concrete and design-grounded. Cite the ADR section (or file:line in the seam) and give
a concrete scenario, not a vibe. Distinguish a real decision-breaking flaw from a build-time detail.
If a concern is already honestly scoped by the ADR (it states several limits explicitly), say so
rather than re-reporting it as a finding. If the decision is fundamentally sound, say so plainly —
do not invent severity."""

USER_TMPL = """Adversarially review the PROPOSED ADR 0003 below (design review, pre-build).

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LAYER (A egress | B prohibition
| decomposition | coherence | missing) / CONCRETE SCENARIO OR BYPASS / WHY IT BREAKS THE DECISION (or
makes it unenforceable/incoherent) / WHETHER THE ADR ALREADY SCOPES IT / FIX.

Then:
- MISSING: anything the decision must address that it does not.
- DECOMPOSITION JUDGMENT: is the two-layer (grantable authority / un-grantable prohibition) split the
  right frame? If not, propose the better one in 2-3 sentences.
- STEELMAN (2-3 sentences): the strongest version of the author's design.
- VERDICT: SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence, and the single highest-value change.

=================== BEGIN MATERIAL ===================
{bundle}
=================== END MATERIAL ==================="""

# Five distinct strong vendors (OpenAI, Google, Anthropic, xAI, Qwen).
PANEL = ["openai/gpt-5.1", "google/gemini-2.5-pro", "anthropic/claude-opus-4.1",
         "x-ai/grok-4.5", "qwen/qwen3-max"]
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
        "usage": {"include": True},
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS ADR 0003 Design Panel"},
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
    raw = HERE / "raw_outbound"
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
                    f"# ADR 0003 design panel: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — ADR 0003 outbound-authority DESIGN panel =======")
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
    print("===================================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
