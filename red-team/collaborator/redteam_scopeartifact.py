"""External DESIGN panel on ADR 0004 — Tier-3 authorized-offense scope artifact (PRE-BUILD).

Josh's empirical-adversarial rule applied to a DESIGN: pressure-test the ADR externally BEFORE any
implementation. The panel attacks the DECISION — the external-key trust root, the laundering
residual it admits, the asymmetric-vs-HMAC split, the gate placement, the dual-use probe, and
coherence with P-01 and the real seam — not a diff.

Bundles the proposed ADR 0004 PLUS the ADR 0003 Tier-3 invariant it honors and the real seam it
binds to (the capability-derivation gate, the HMAC signed-caps model it deliberately does NOT
reuse, the tool registry + audit-only offense recognizer, the session where anchors/artifacts
attach) so reviewers can judge the design is coherent with the code and actually buildable.

Reports per-model + total API cost (Josh's standing request).

Usage:  python red-team/collaborator/redteam_scopeartifact.py [openai/gpt-5.1 ...]
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
    ("THE PROPOSAL UNDER REVIEW — docs/adr/0004-tier3-scope-artifact-external-trust-root.md",
     "docs/adr/0004-tier3-scope-artifact-external-trust-root.md"),
    ("THE INVARIANT IT HONORS — docs/adr/0003-outbound-authority-and-prohibition-floor.md (the tiered "
     "ladder; Tier 3 §162-191 designed-but-locked; the trust-root invariant §170-178, 346-348)",
     "docs/adr/0003-outbound-authority-and-prohibition-floor.md"),
    ("THE HMAC CAP MODEL IT DELIBERATELY DOES NOT REUSE — collaborator/policycaps.py (symmetric HMAC, "
     "operator holds the key; the whole reason Tier 3 needs an EXTERNAL asymmetric key)",
     "collaborator/policycaps.py"),
    ("THE SEAM IT BINDS TO — collaborator/governance.py (the capability = f(intent.args) derivation + "
     "the one capability gate + reauthorized_or_denied re-gate + execute_and_verify moment-of-use "
     "re-assert — where the offense:<target> derivation + active_scope_grant gate would live)",
     "collaborator/governance.py"),
    ("THE TOOL REGISTRY + AUDIT-ONLY OFFENSE RECOGNIZER — collaborator/tools.py (the un-grantable "
     "__derived__ sentinel pattern; flag_offense_shape which stays audit-only)",
     "collaborator/tools.py"),
    ("WHERE ANCHORS/ARTIFACTS ATTACH — collaborator/session.py (host-provisioned, not model-reachable, "
     "constructor args like policy_caps/caps_key; the sticky enforce_caps pattern)",
     "collaborator/session.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

This is a DESIGN review of a PROPOSED architecture decision record (ADR 0004), NOT a code review of a
shipped diff. Nothing in ADR 0004 is built yet; the code files are the EXISTING seam the ADR claims it
will extend, included so you can judge whether the design is coherent with the real system and actually
buildable as described.

SalienceOS is a salience-based AI control plane. Core invariant P-01: salience INFLUENCES, policy
AUTHORIZES — a salience signal can nudge a knob within a signed authority window but can never grant or
widen authority. The "Collaborator" is a governed agent that imports the core: a PROPOSER model suggests
actions; NOTHING runs until a human approves (default); every action passes ONE core-enforced capability
gate (directive.grants_capability(required_cap)) plus a second "leash" axis (act_then_report /
propose_first / notify_only) and a workspace fence. Capabilities are carried by a host-SIGNED PolicyCaps.

THE LADDER (ADR 0003, shipped): Tier 1 = mediated allowlisted egress, a per-host capability
net.get:<canonical-host> DERIVED from the request (capability = f(intent.args)), default-deny, exact-host.
Tier 2 = human-gated raw reach / emission (net.post:<host>, run_command). Tier 3 = scope-gated high-impact
ops that affect a THIRD PARTY (authorized offense / sanctioned pentest) — deliberately LOCKED in v0: no
capability names a non-consented target and there is no in-band path to mint one.

THE DECISIVE FACT ADR 0004 RESTS ON: every key in the system today is a SYMMETRIC HMAC key the
host/operator holds (caps_key, policy_key, executor_key — policycaps.py, session.py). The operator can
mint any PolicyCap. So the Tier-3 artifact CANNOT be an HMAC cap; ADR 0003's invariant ("legitimate only
if rooted in a key the operator does NOT hold") forces asymmetric verification against an EXTERNAL public
key. ADR 0004 is that design: an Ed25519-signed scope artifact, verified against operator-configured
external PUBLIC keys (trust anchors), unlocking a new offense:<canonical-target> capability that the
operator's HMAC PolicyCaps can NEVER mint. Plus one minimal gated offense action (a bounded TCP probe).

ADR 0004 EXPLICITLY ADMITS a residual: on a single node the human operator provisions the anchor set, so
a human who registers a key THEY generated as a "trusted authority" and holds its private key can
self-issue — no single-node system prevents an operator colluding as their own authority (the ADR 0002
single-trust-domain limit). The ADR claims the design still "raises the bar" and states the limit honestly
rather than closing it. ATTACK WHETHER THAT HONESTY IS SOUND OR A CONVENIENT ESCAPE HATCH.

OUT OF SCOPE (do not re-litigate): the correctness of the core capability gate / signed HMAC PolicyCaps
(assume correct); ADR 0001/0002; the Tier-1/Tier-2 egress build (shipped + certified). Attack ADR 0004's
DECISION.
"""

SYSTEM = """You are a senior security ARCHITECT + applied cryptographer doing an adversarial DESIGN review
of a proposed ADR that unlocks Tier 3 (authorized offense against a third party) via an asymmetric,
externally-signed "scope artifact". Find where this design is wrong, unenforceable, incoherent with the
existing seam, cryptographically naive, or dangerously incomplete — do not praise it. Assume the author is
over-confident until proven otherwise.

Attack hardest, in roughly this order:

1. THE TRUST ROOT + THE LAUNDERING RESIDUAL (the crux). The ADR's whole claim is "rooted in a key the
   operator does not hold." But the operator provisions the trust-anchor set. Is the admitted residual (a
   human adding their own generated key as an "external authority") the ONLY laundering path, or are there
   others the ADR misses? Can the MODEL reach anchors/artifacts indirectly (via a proposed run_command
   that writes config, via a tool that mutates session state, via approval flow)? Does "the operator can
   choose anchors but not sign as them" actually hold, or does anchor-choice = de-facto minting? Is stating
   the residual honestly ENOUGH, or does admitting it make Tier 3's "prohibited class" claim hollow — i.e.
   is this really a locked door or a door with a posted sign?

2. THE ARTIFACT + CRYPTO. Attack the Ed25519 scope artifact: canonicalization ambiguity (does the signed
   byte-string bind EXACTLY the semantic fields? signature malleability, field-injection, unicode/nonce
   games, the scope tuple, missing algorithm-agility / downgrade, no key-id vs authority-id confusion),
   replay across engagements/sessions/targets (nonce is present but unchecked against anything — is that a
   replay hole?), the missing revocation before not_after, clock/skew for the window, a valid artifact for
   a target that later changes ownership (DNS/host reassignment), canonical_host parity (does the artifact
   target canonicalize IDENTICALLY to the connect host, or can authorize-one/probe-another diverge like the
   egress bug ADR 0003 closed?).

3. THE GATE PLACEMENT + P-01. Is offense:<target> derived + verified in the RIGHT places (govern-time,
   approval re-gate, moment-of-use) so no refactor reaches an unauthorized run? Does verifying the artifact
   at gate-time (not just attaching it) actually hold the "surfacing grants no authority / P-01 for the
   outside" line, or does anything let the artifact INFLUENCE rather than the external key AUTHORIZE? Is the
   offense: capability genuinely un-mintable by the HMAC PolicyCaps path, or is there a seam where a
   directive could carry it?

4. THE MINIMAL OFFENSE ACTION (dual-use). Is a "single bounded TCP connect probe" honestly minimal, or the
   thin end of a scanner (loop it over ports/hosts, banner-grab as a payload channel)? Does the probe reach
   ONLY the artifact's exact target, or can args (port, host form, redirect, DNS) widen it? Should the probe
   respect the same IP-pin / private-range blocks as egress, or is reaching RFC1918/metadata a feature or a
   footgun here? Is building ANY offense executor the right call vs. shipping only the authorization
   mechanism?

5. WHAT IS MISSING OR MIS-SCOPED. The first third-party dependency (cryptography) as supply-chain / build
   surface; anchor rotation/compromise with no revocation; audit of a Tier-3 action (engagement_id
   provenance, what leaks); the honest-residual framing vs. a real second-trust-domain; whether this should
   graduate structural un-grantability into core (ADR 0003 revisit #4) rather than live collaborator-side.
   Name the ONE change that would most improve the decision.

Calibration: concrete and design-grounded. Cite the ADR section (or file:line in the seam) and give a
concrete scenario, not a vibe. If a concern is already honestly scoped by the ADR (it states several
limits explicitly, including the laundering residual), say so rather than re-reporting it as a finding —
but DO attack whether that honest-scoping is legitimate or an escape hatch. If the decision is
fundamentally sound, say so plainly; do not invent severity."""

USER_TMPL = """Adversarially review the PROPOSED ADR 0004 below (design review, pre-build).

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / AREA (trust-root | crypto/artifact |
gate/P-01 | dual-use probe | missing) / CONCRETE SCENARIO OR BYPASS / WHY IT BREAKS THE DECISION (or makes
it unenforceable/incoherent) / WHETHER THE ADR ALREADY SCOPES IT / FIX.

Then:
- LAUNDERING JUDGMENT: is "rooted in a key the operator does not hold" actually achieved on a single node,
  or is the admitted residual fatal to the claim? Is stating it honestly sufficient? 2-3 sentences.
- MISSING: anything the decision must address that it does not.
- STEELMAN (2-3 sentences): the strongest version of the author's design.
- VERDICT: SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence, and the single highest-value change.

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
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS ADR 0004 Design Panel"},
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
    raw = HERE / "raw_scopeartifact"
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
                    f"# ADR 0004 design panel: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — ADR 0004 Tier-3 scope-artifact DESIGN panel =======")
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
    print("=====================================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
