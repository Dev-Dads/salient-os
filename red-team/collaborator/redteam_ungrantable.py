"""External CODE panel on the shipped core change: the structurally un-grantable prohibited
capability namespace (ADR 0004 / ADR 0003 revisit #4). A tiny, surgical change to the CORE capability
invariant (P-01's sibling), so it earns external eyes despite its size.

Bundles the exact diff PLUS the whole capability path it changes (policy.py, directive.py) and the
collaborator belt (policycaps.py) so reviewers can hunt for a BYPASS — any path by which an
`offense:` capability could still be granted, or by which a legitimate capability is broken.

Reports per-model + total API cost.

Usage:  python red-team/collaborator/redteam_ungrantable.py [openai/gpt-5.1 ...]
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
    ("THE DELTA — the exact diff this PR ships (read first)",
     "red-team/collaborator/raw_ungrantable/ungrantable.diff"),
    ("THE CORE CAPABILITY PATH — salienceos/interpreter/policy.py (RESERVED_UNGRANTABLE_PREFIXES + "
     "is_ungrantable_capability + issue_policy strip; the signed authority envelope)",
     "salienceos/interpreter/policy.py"),
    ("THE GATE ACCESSOR — salienceos/interpreter/directive.py (grants_capability — the one capability "
     "accessor every consumer uses; the unconditional refusal lives here)",
     "salienceos/interpreter/directive.py"),
    ("THE OPERATOR BELT — collaborator/policycaps.py (mint rejects the namespace; the signed HMAC grant)",
     "collaborator/policycaps.py"),
    ("THE DESIGN — docs/adr/0004-tier3-scope-artifact-external-trust-root.md (why: the design panel "
     "showed the Tier-3 unlock would be single-node ceremony; this locks the class by core instead)",
     "docs/adr/0004-tier3-scope-artifact-external-trust-root.md"),
    ("WHAT IS PINNED — tests/test_no_laundering.py (the structural guarantees as tests)",
     "tests/test_no_laundering.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

CODE review + CERTIFICATION of a SHIPPED diff to SalienceOS core. SalienceOS is a salience-based AI
control plane; core invariant P-01: salience INFLUENCES, policy AUTHORIZES. A signed PolicyCaps carries
capability; the interpreter turns it into a `Directive` whose `grants_capability(cap)` is THE authority
accessor (directive.py). Capabilities are exact strings (e.g. fs.read:project, shell.exec, net.get:<host>).

WHAT SHIPPED (attack + CERTIFY): ADR 0003 defines a prohibited class — a capability naming a THIRD PARTY
the operator cannot prove authority over ("offense", authorized-offense/pentest against a target). ADR
0003 wanted this class made UN-GRANTABLE BY CONSTRUCTION in core (its revisit #4). This diff does exactly
that and NOTHING else:
  - policy.py: RESERVED_UNGRANTABLE_PREFIXES = ("offense:",); is_ungrantable_capability(cap) is total +
    case-insensitive; issue_policy STRIPS any offense: cap before building/signing the envelope.
  - directive.py: grants_capability refuses the reserved namespace UNCONDITIONALLY, before the membership
    check — so no directive grants offense: regardless of what allowed_capabilities holds (even a
    hand-built or mis-wired one). This is the load-bearing structural guarantee.
  - collaborator/policycaps.py: mint REJECTS an offense: capability (fail loud) — the operator cannot even
    construct such a grant.

No existing capability uses the offense: prefix, so behaviour is unchanged for every real capability.

THE CERTIFICATION CLAIM (attack it HARDEST): "there is NO in-band path by which an `offense:`-namespaced
capability is granted — not via a signed PolicyCaps, not via issue_policy, not via a directly-constructed
Directive, not via any case/normalization trick; AND no legitimate (non-offense) capability is broken by
the change." Try to grant an offense capability anyway; try to make a legit capability fail; try to make
is_ungrantable_capability raise or mis-classify (unicode/casefold edge, non-str, prefix boundary like
'offensexyz:' or 'x.offense:y'); check whether any consumer reads `allowed_capabilities` DIRECTLY instead
of grants_capability and thus sees a stripped-but-not-refused cap; check the issue_policy strip vs the
signature (does stripping change what is signed vs verified coherently?).

OUT OF SCOPE (do not re-litigate): the correctness of the HMAC signing scheme (assume correct); the
DESIGN decision to defer the Tier-3 unlock (already panel-driven); ADR 0001/0002. Attack THIS diff.
"""

SYSTEM = """You are a senior security engineer doing an adversarial CODE review AND CERTIFICATION of a
surgical change to a capability-authorization core. BREAK it — find a path that grants the prohibited
`offense:` namespace, or that breaks a legitimate capability, or that makes the recognizer raise/misclassify
— but ALSO certify: if you cannot break a claim, say so EXPLICITLY (a blocked attack is a valuable
NON-finding). Assume the author is wrong until proven otherwise. Concretely try:
  - GRANT AN OFFENSE CAP ANYWAY: any path where grants_capability returns True for an offense: string, or
    where a consumer treats an offense: cap as authority without calling grants_capability (does anything
    read directive.allowed_capabilities directly for an authority decision?). The strip is in issue_policy;
    the refusal is in grants_capability — is EITHER alone sufficient, and is there a path that hits neither?
  - NORMALIZATION / BOUNDARY: casefold edge cases (Turkish-I, full-width, unicode that casefolds to
    'offense:'), prefix-boundary confusion ('offensexyz', 'x-offense:y', 'OFFENSE :x' with space), a
    capability that is offense-semantic but not offense:-prefixed (out of scope for THIS reservation, but
    note if the reservation is trivially side-stepped by naming).
  - BREAK A LEGIT CAP: does the change ever refuse or strip a non-offense capability (fs.*, shell.exec,
    net.get:, net.post:, shell.raw_network, shell.contained_autonomy)? Any collision?
  - RAISE / TOTALITY: feed is_ungrantable_capability / mint / issue_policy a non-str, None, bytes, a huge
    string, a non-iterable capabilities arg — does anything raise where it should fail closed?
  - SIGNATURE COHERENCE: issue_policy strips before signing; verify checks the signed payload. Is there a
    mismatch where a stripped cap still rides, or a valid grant is now rejected?
Name file:line, give a CONCRETE input, and state whether another layer catches it. Distinguish a real
break from a documented non-goal. If the change is sound, say so plainly; do not invent severity."""

USER_TMPL = """Review + CERTIFY the SHIPPED diff below (the structurally un-grantable `offense:` namespace).

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) / CONCRETE INPUT
OR BYPASS / WHY IT BREAKS A GUARANTEE / WHETHER ANOTHER LAYER CATCHES IT / FIX. Then a CERTIFICATION LINE
for the headline ("no in-band path grants an offense: capability; no legitimate capability is broken") —
CERTIFIED / NOT-CERTIFIED + one sentence. Then STEELMAN (2-3 sentences) and VERDICT (SOUND / MINOR_ISSUES /
SERIOUS_FLAWS + one sentence, and the single highest-value fix).

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
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS ungrantable CODE Panel"},
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
    raw = HERE / "raw_ungrantable"
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
                    f"# ungrantable-namespace CODE panel: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — ungrantable-namespace CODE panel =======")
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
    print("===========================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
