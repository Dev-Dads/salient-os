"""Red-team the ③ SIGNED-POLICYCAPS design: does binding the Collaborator's authority
to a signed grant actually stop the config / Step-2 control surface from WIDENING
authority, fail closed on tamper, and — crucially — is its security claim HONESTLY
scoped (single trust domain), not oversold?

Mirrors redteam_propose.py (OpenRouter, general panel). Material = the design doc + the
seam it modifies (session.py / governance.py / tools.py), so reviewers judge the threat
model and whether any path still sources authority from mutable config.

Usage:  python red-team/collaborator/redteam_policycaps.py [general x-ai/grok-4.5 ...]
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
    ("THE DESIGN UNDER REVIEW — 03-policycaps-design.md", "red-team/collaborator/03-policycaps-design.md"),
    ("THE SEAM IT MODIFIES — collaborator/governance.py", "collaborator/governance.py"),
    ("collaborator/session.py", "collaborator/session.py"),
    ("collaborator/tools.py", "collaborator/tools.py"),
    ("collaborator/view.py (the Step-2 control surface: set_leash etc.)", "collaborator/view.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

SalienceOS invariant P-01: salience INFLUENCES (scrutiny/compute), policy AUTHORIZES
(capability). The Collaborator is a governed agent-loop host over the salienceos core.
Today its authority is mutable host config: session.capabilities (a tuple) and the leash
(session.leash_overrides, which the Step-2 judgment view's set_leash writes). The core's
capability gate is directive.grants_capability, where the directive comes from
issue_policy(capabilities=session.capabilities, ...) signed with session.policy_key.

UNDER REVIEW is the DESIGN for ③ — SIGNED POLICYCAPS: bind authority to a signed artifact
(capabilities + per-tool leash CAPS + issuer + subject), HMAC-signed by a policy-authority
key. Every governed action verifies the grant and sources the capability set from the
VERIFIED caps (not the mutable tuple), then caps the leash at stricter(host_leash,
leash_cap). Claimed properties: config/control-surface can TIGHTEN but never WIDEN past the
grant; tamper/absent/wrong-subject fails closed (zero caps, strictest leash); backward
compatible when no caps present. The design EXPLICITLY scopes the security to a single trust
domain: symmetric HMAC, the verifying session holds the same key that signs, so it is
tamper-evidence + provenance + fail-closed integrity, NOT a hard boundary against a fully
in-process re-signer (asymmetric / separate authority process is named as the future,
consistent with ADR 0002).

Judge the threat model, the no-widen / fail-closed properties, and — importantly — whether
the honesty scoping is correct or whether the design still oversells (or under-delivers).
The core salienceos is assumed correct; attack the NEW grant layer and its wiring.
"""

SYSTEM = """You are a senior security reviewer doing an adversarial review of the DESIGN for "signed PolicyCaps" — binding a governed worker's authority (capabilities + leash caps) to an HMAC-signed grant the host verifies each action. The core judgment system is out of scope; attack the new grant layer, its wiring into the seam, and the honesty of its security claim.

Attack hardest, in order:
1. BYPASS / INCOMPLETE MEDIATION: find ANY path where authority is still sourced from mutable config after ③. Does EVERY capability check (govern_action AND the re-gate reauthorized_or_denied) read the VERIFIED caps, or could one still read session.capabilities? Does the leash cap apply on BOTH the act path and the approve/re-gate path? Could an action slip through before verification?
2. WIDEN: can the config or the Step-2 view still widen authority past the grant? capability add via session.capabilities? leash loosen via set_leash/leash_overrides below the cap? Is stricter(host, cap) computed correctly (no off-by-one in the rank ordering that lets act_then_report through a propose_first cap)?
3. FAIL-CLOSED: tamper (edit caps without re-sign), absent key while caps present, wrong subject (replay onto another workspace), malformed/empty caps, None fields — does each yield ZERO capabilities + strictest leash, or is there a path that fails OPEN (e.g., verify() exception treated as pass, or missing caps silently granting all)?
4. HONESTY OF THE CLAIM: the design says symmetric-HMAC single-trust-domain = tamper-evidence + provenance + fail-closed, NOT a hard boundary against an in-process re-signer. Is that scoping correct and complete, or does the doc still imply a stronger guarantee somewhere? Is there a place a reader would over-trust it? Is the canonicalization (for the signature) collision/ambiguity-safe (e.g., JSON with sorted keys, stable types), or could two different caps sign the same?
5. REPLAY / BINDING: subject binds a grant to one workspace — is that binding actually enforced at verify time, and is workspace path a sound subject (symlinks, relative vs resolved)? Any nonce/expiry needed for v0, or is it honestly deferred?
6. BACKWARD COMPAT AS A HOLE: "no PolicyCaps => legacy behaviour" — is the legacy path a bypass (an attacker just doesn't attach caps and keeps mutable config)? Is that acceptable for opt-in hardening, and is it documented as such?

Calibration: concrete, honest; every finding names the location (design section or file) and a concrete scenario. This is a DESIGN review of a hardening layer on reviewed code, honestly scoped to a single trust domain — do NOT demand asymmetric crypto it explicitly defers, and do NOT re-litigate the single-domain decision (ADR 0002); DO demand complete mediation, correct fail-closed, and that the claim matches what the code will do. If sound with only minor notes, say so plainly."""

USER_TMPL = """Adversarially review the SIGNED-POLICYCAPS DESIGN below.

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
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS PolicyCaps Red-Team"},
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
    raw = HERE / "raw_policycaps"
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
                    f"# PolicyCaps red-team: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
