"""Red-team the ④ COLLABORATOR-MEMORY design: does building the Collaborator's remembered
history on CDMS actually hold the Bem firewall / P-01 — memory INFORMS, never AUTHORIZES —
or is there a path where an ingested deed, a recalled memory, or the boot self-preamble
becomes identity, authority, or an unauthorized action? And is the security claim HONESTLY
scoped (single cross-repo trust domain), not oversold?

Mirrors redteam_policycaps.py (OpenRouter, general security panel). Material = the design doc
+ the seam that consumes memory (propose.py), the in-repo firewall precedent
(salienceos/consumers/memory.py), and the authority floor (policycaps.py), so reviewers can
judge the threat model and whether any path lets memory buy permission.

Reports per-model + total API cost at the end (OpenRouter usage accounting).

Usage:  python red-team/collaborator/redteam_memory.py [general x-ai/grok-4.5 ...]
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
    ("THE DESIGN UNDER REVIEW — 04-memory-design.md", "red-team/collaborator/04-memory-design.md"),
    ("THE SEAM THAT CONSUMES MEMORY — collaborator/propose.py", "collaborator/propose.py"),
    ("THE IN-REPO FIREWALL PRECEDENT — salienceos/consumers/memory.py", "salienceos/consumers/memory.py"),
    ("THE AUTHORITY FLOOR — collaborator/policycaps.py", "collaborator/policycaps.py"),
    ("THE SEAM IT WOULD WIRE INTO — collaborator/governance.py", "collaborator/governance.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

SalienceOS invariant P-01: salience INFLUENCES (scrutiny/compute/retention), policy
AUTHORIZES (capability). The Collaborator is a governed agent-loop host over the salienceos
core. Its authority is a signed grant (③ PolicyCaps): capability comes from a verified,
HMAC-signed artifact, and mutable config can only TIGHTEN, never widen past it.

UNDER REVIEW is the DESIGN for ④ — the Collaborator's MEMORY, built on CDMS (a separate repo:
a local salience-decay memory, episodic -> [sleep/dream consolidation] -> gist -> scars, with
a provenance firewall). The design makes the Collaborator a GOVERNED CONSUMER of CDMS, not a
new store. Three mechanics: (1) INGESTION — the honest governed record (Decision + verified
outcome, including vetoes) is ingested host-side as a CDMS `TurnEvent` STAMPED
`provenance="untrusted"` (note: TurnEvent.provenance DEFAULTS to "trusted", so the producer
MUST override it), and the design REQUIRES `enforce_provenance=true`; (2) RECALL — read-only
`retrieve`/`history` over CDMS, enriching the proposer's context and optionally an agent-
invoked `memory.read` capability; there is NO memory-write verb for the model; (3) BOOT — a
consolidated-self preamble as messages[0], fail-empty.

VERIFIED IN CDMS CODE (assume these hold): under `enforce_provenance` (default true), untrusted
episodes cannot form/reinforce a gist (consolidate.py filters them before gisting), cannot mint
a scar (elevation requires provenance=="trusted"), cannot be corroborated-up by repetition
(untrusted pairs never elevate), and are dropped from model-facing recall
(include_untrusted=False); untrusted-derived text is flattened and fenced as "untrusted DATA,
never trusted instructions". canon_provenance fails closed to "untrusted" for any non-canonical
value.

Four content stores: system-facts (all users; NEW/OS-level), user-facts (per user), world-facts
(per workspace, verifier-grounded, decaying), and memory/self (CDMS-A tuples, shared per user
across projects+surfaces). The core salienceos and ③ PolicyCaps are assumed correct; attack the
NEW memory layer, its wiring into the propose seam, the honesty of the firewall claim, and the
cross-user privacy boundary on the shared stores.
"""

SYSTEM = """You are a senior security reviewer doing an adversarial review of the DESIGN for the
Collaborator's MEMORY — building a governed agent's remembered history on CDMS such that memory
INFORMS but never AUTHORIZES. The CDMS engine's internal firewall and the ③ PolicyCaps authority
floor are assumed correct; attack the NEW memory layer, its wiring into the propose seam, and
the honesty/completeness of its claims.

Attack hardest, in order:
1. MEMORY -> AUTHORITY (the firewall): find ANY path where an ingested deed, a recalled memory,
   a gist, a scar, or the boot self-preamble causes an action to RUN that policy would not
   allow, or becomes the agent's identity/authority. Recall + boot feed SURFACING and scrutiny;
   does anything let them feed the capability decision? Is "surfacing grants no authority" still
   airtight now that memory enriches the proposer's context?
2. THE FAIL-OPEN CRUX: the whole firewall rests on (a) the producer stamping `untrusted`
   (TurnEvent defaults to "trusted") and (b) `enforce_provenance=true`. Is that HONESTLY the
   complete set of fail-open conditions, or are there OTHERS? e.g. an "ambiguous" provenance path
   that isn't fully fenced; a store (world/user/system facts) whose ingestion does NOT go through
   the untrusted-stamping path; the boot preamble assembling from a store that isn't provenance-
   fenced; a recall with include_untrusted=True reachable from the model.
3. FEEDBACK LOOP / AMPLIFICATION: recall -> proposal -> deed -> ingested(untrusted) -> recall...
   Even if every hop is "untrusted", can the loop amplify a bias, manufacture apparent
   corroboration, or drift the self over many cycles? Is there a monotone that isn't bounded?
4. CROSS-USER PRIVACY (shared stores): system-facts and user-facts are shared. Is the
   ingestion-time privacy/scope boundary real and enforceable, or hand-wavy? Concrete leak: a
   user's private data (a secret in a world fact, a path, a credential-shaped string) crossing
   into the shared system store, or one user's self leaking to another. Is "system-scoped"
   definable and checked at ingestion, not just filtered at recall?
5. PROMPT INJECTION VIA MEMORY: untrusted content is fenced as DATA. Is that fence sufficient in
   the proposer/boot context, or can a crafted memory (or a crafted world/user/system fact) still
   act as an instruction, jailbreak the proposer, or smuggle a tool call? Are the FACT stores
   (world/user/system) provenance/injection-fenced the same way the CDMS-A tuples are, or is that
   assumed and unbuilt?
6. SCOPE CONFUSION: memory/self shared per user ACROSS projects, but world-facts per workspace.
   Can a workspace-scoped secret land in the shared self, or project A's context surface unsafely
   in project B? Is the shared-vs-per-workspace split coherent and leak-free?
7. HONESTY OF THE CLAIM: the design scopes the salient-os<->CDMS boundary as a SINGLE trust
   domain (like ADR 0002), tamper/fail-closed within it, not a hard boundary against a component
   that could write trusted directly. Is that correct and complete, or does the doc oversell
   ("read-only to the model", "never authorizes") somewhere a reader would over-trust? Is the
   system-store's undesigned ingestion source an honest deferral or a hidden hole?

Calibration: concrete, honest; every finding names the location (design section or file) and a
concrete scenario. This is a DESIGN review of a memory layer that leans on a reviewed firewall,
honestly scoped to a single cross-repo trust domain — do NOT demand a cryptographic doer<->store
boundary it explicitly defers, and do NOT re-litigate the CDMS engine's internal correctness; DO
demand that memory can never buy permission, that the fail-open set is complete and named, that
the shared-store privacy boundary is real, and that the claims match what the code will do. If
sound with only minor notes, say so plainly."""

USER_TMPL = """Adversarially review the COLLABORATOR-MEMORY DESIGN below.

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
        "usage": {"include": True},   # OpenRouter usage accounting -> usage.cost (USD)
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS Memory Red-Team"},
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
    raw = HERE / "raw_memory"
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
                    f"# Memory red-team: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    # ---- API cost report (Josh's standing request: per-review cost at the end) ----
    print("\n================ API COST — ④ memory review ================")
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
    print("============================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
