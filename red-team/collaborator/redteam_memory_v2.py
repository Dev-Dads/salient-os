"""Red-team the ④ COLLABORATOR-MEMORY design **v2 (two-agent)**. v1 took SERIOUS_FLAWS on a
self-contradictory recall model + an under-specified fact/injection/privacy surface; v2 resolves
it structurally by SPLITTING the roles: a history-blind DOER (acts on facts) + a separate
PROPOSER (a distinct agent that reads the CDMS gist TUPLES as third-person observed record). The
doer's deeds are ingested as `ambiguous` provenance (CDMS "quarantine": gists but NEVER scars).

Attack the NEW architecture: does the separation actually hold the identity firewall; does
`ambiguous` truly bar all self-authored authority; does the SURVIVING fact path (world/user/system
facts the agents see) get properly fenced; and does the two-agent split introduce NEW holes?

Mirrors redteam_memory.py (OpenRouter panel, cost accounting). Writes to raw_memory_v2/.

Usage:  python red-team/collaborator/redteam_memory_v2.py [general x-ai/grok-4.5 ...]
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
    ("THE DESIGN UNDER REVIEW — 05-memory-design-v2.md", "red-team/collaborator/05-memory-design-v2.md"),
    ("THE SEAM THAT CONSUMES MEMORY — collaborator/propose.py", "collaborator/propose.py"),
    ("THE IN-REPO FIREWALL PRECEDENT — salienceos/consumers/memory.py", "salienceos/consumers/memory.py"),
    ("THE AUTHORITY FLOOR — collaborator/policycaps.py", "collaborator/policycaps.py"),
    ("THE SEAM IT WOULD WIRE INTO — collaborator/governance.py", "collaborator/governance.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

SalienceOS invariant P-01: salience INFLUENCES (scrutiny/compute/retention), policy AUTHORIZES
(capability). The Collaborator is a governed agent-loop host over the salienceos core; authority
is a signed grant (③ PolicyCaps): capability comes from a verified HMAC-signed artifact, and
mutable config can only TIGHTEN, never widen.

UNDER REVIEW is design v2 for the Collaborator's MEMORY, built on CDMS (a separate repo: a local
salience-decay memory — episodic -> [sleep/dream consolidation] -> gist tuples -> scars — with a
three-rank provenance firewall: trusted > ambiguous > untrusted). v1 was found self-
contradictory (deeds stamped `untrusted` could never surface, so the memory was empty where it
was meant to help). v2 resolves this by a STRUCTURAL split into TWO agents:

- The DOER (hands): executes a task/proposal. Its context is the FACT layer (world + user facts)
  — never the history layer, never a recall of its own deeds. ③ gates what it may touch.
- The PROPOSER (sense): a SEPARATE agent that consumes the CDMS gist TUPLES
  ⟨subject,relation,object,valence,frequency,support⟩ — "what the system did and how it turned
  out" — plus facts for grounding, and brings the human a governed proposal. It never DID the
  deeds it reads, so history is third-person to it.

THREE LOCKS (claimed independent): (1) SEPARATION closes self-attribution (the maker of the deeds
is not the reader); (2) deeds ingest as `ambiguous` = CDMS "quarantine", which VERIFIED-IN-CDMS-
CODE clusters into gist tuples (consolidate.py:613-616 "Ambiguous (quarantine) and trusted still
cluster normally") but can NEVER mint a scar/guardrail (consolidate.py:333-336 "ambiguous
(quarantine) are excluded regardless of recurrence"); (3) ③ PolicyCaps gates every run — history
can produce a PROPOSAL, only ③ authorizes a RUN.

ALSO VERIFIED IN CDMS: gisting is project-scoped (consolidate.py:620-624); scars need
corroboration across >= N distinct sessions and gists need min_cluster_support
(consolidate.py:344,370-372) — bounding feedback; secrets are scrubbed at ingest (store.py:228);
`ambiguous` DOES surface on raw episodic recall (db.py:595-596 drops only `untrusted`) — which is
why the proposer is restricted to GIST TUPLES, not raw episodic retrieve.

The v1 panel confirmed the core (memory never reaches the capability gate). The CDMS engine
internals and ③ are assumed correct. Attack the v2 TWO-AGENT design: the separation, the
`ambiguous` repurpose, the SURVIVING fact-path fencing (world/user/system facts), and any NEW
holes the split introduces.
"""

SYSTEM = """You are a senior security reviewer doing an adversarial review of design v2 for the
Collaborator's MEMORY — a TWO-AGENT split (history-blind doer + separate tuple-reading proposer)
meant to make memory INFORM but never AUTHORIZE. The CDMS engine internals and ③ PolicyCaps are
assumed correct. Attack the v2 architecture and its wiring.

Attack hardest, in order:
1. DOES SEPARATION ACTUALLY HOLD THE IDENTITY FIREWALL? The claim is "the proposer reads history
   third-person, so no self-attribution." Is that real or hand-wavy? Can the proposer's system
   prompt / tuple framing still make it self-attribute ("you are the kind of agent that does X")?
   The two agents share ONE CDMS store — is an identity/access split without a process/crypto
   boundary sufficient? Is "observed-stance framing" an enforceable, test-pinnable property or a
   convention that the first prompt-tweak breaks?
2. DOES `ambiguous` BAR ALL SELF-AUTHORED AUTHORITY? Trace gist -> proposal -> human-approve ->
   deed -> gist. Even though `ambiguous` never scars, can the gist tuples (behavioral persona)
   drive the proposer to reliably propose escalating actions, or manufacture apparent authority?
   Is repurposing `ambiguous` for "the doer's own governed deeds" sound, or does it collide with
   any other CDMS use of `ambiguous` (mixed-origin content) in the same store?
3. THE SURVIVING FACT PATH (the design says this is the real remaining work — pressure it): world
   facts are verifier-observed from workspace files ("verifier-grounded = true, not safe-as-
   instruction"). Is the single collaborator-side DATA fence sufficient at the propose seam
   (propose.py concatenates context into a user message against a thin JSON-only system prompt)?
   Is the system-store operator-pinned allowlist/denylist predicate actually definable and
   enforceable, or still hand-wavy? Can a fact still act as an instruction or leak PII/credentials
   cross-user?
4. NEW HOLES FROM THE SPLIT: the proposer->doer proposal channel (can a biased/injected proposer
   drive the doer beyond what ③ allows? within-granted-capability harm on human-approve?); the
   doer's FACT context as the remaining injection vector; the shared-store-between-two-agents
   boundary; whether "the doer is history-blind" is structurally guaranteed or just intended.
5. THE PROPOSER READS TUPLES, NOT RAW EPISODIC — but `ambiguous` surfaces on raw recall. Is there
   ANY path (a memory.read capability, a fallback, an adapter default) by which the proposer or
   the model reaches raw episodic deed text (secrets, injection payloads) instead of distilled
   tuples? If so, the fact-path fencing must also cover it.
6. FEEDBACK / AMPLIFICATION AT THE SYSTEM LEVEL: separation removes per-agent self-attribution,
   but the loop doer->deed->gist->proposer->proposal->doer still exists. Is CDMS's
   support/corroboration/decay a SUFFICIENT bound, or can the proposer still be driven to
   flood/drift/echo-chamber? Is the veto decaying-inhibitor floor well-defined?
7. HONESTY: are the "three independent locks" truly independent, or does one silently depend on
   another (e.g., separation secretly relies on the proposer never getting raw episodic)? Is the
   single-trust-domain + `ambiguous`-repurpose + best-effort-redaction scoping correct and
   complete, or is there a place a reader would over-trust? Are the enforced-v0 vs deferred
   Properties honestly sorted?

Calibration: concrete, honest; every finding names the location (design section or file) and a
concrete scenario. This is a DESIGN review of a two-agent memory layer on a reviewed firewall,
honestly scoped to a single cross-repo trust domain — do NOT demand a cryptographic doer<->store
boundary it explicitly defers, and do NOT re-litigate CDMS engine internals; DO demand that the
separation is real and testable, that `ambiguous` bars self-authored authority, that the fact
path is genuinely fenced, and that the split introduces no new authority path. If sound with only
minor notes, say so plainly."""

USER_TMPL = """Adversarially review the TWO-AGENT COLLABORATOR-MEMORY DESIGN (v2) below.

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
        "usage": {"include": True},
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS Memory v2 Red-Team"},
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
    raw = HERE / "raw_memory_v2"
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
                    f"# Memory v2 red-team: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    # ---- API cost report (per-review, per Josh's standing request) ----
    print("\n============== API COST — ④ memory review v2 ==============")
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
    print("==========================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
