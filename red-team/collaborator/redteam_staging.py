"""Red-team the SHIPPED staging-subsystem diff (branch feat/proposer-staging-subsystem) BEFORE
merge — it touches a safety-critical verifier FLOOR_KIND boundary, so it gets a code panel, not
just a "flag it" note.

Three shipped changes under one seam ("produce freely in reachable space; gate the consequential
placement"):
  1. VERIFIER (highest risk): observe_action now EXEMPTS the ancestor directories of a declared
     file.write from the write-set boundary (they were false-failing an honest nested write as
     "undeclared" mutations). Attack whether this can HIDE a real undeclared mutation.
  2. CONTROLLED-LOCATION HARD-DENY: a PROPOSER-originated write into a controlled location
     (default `.github/**`) is denied so it stages to scratch; the human approves the placement
     and the Collaborator executes it. Attack the bypass surface.
  3. PROPOSAL STAGE POOL: surfaced-but-undecided proposals are kept PENDING/findable. Attack any
     authority leak / TOCTOU / DoS.

Reports per-model + total API cost (Josh's standing request).

Usage:  python red-team/collaborator/redteam_staging.py [openai/gpt-5.1 ...]
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
    ("THE CODE — salienceos/verifier/observers.py (the EXEMPTION)", "salienceos/verifier/observers.py"),
    ("THE CODE — salienceos/verifier/contract.py (write-set obligation derivation)", "salienceos/verifier/contract.py"),
    ("THE CODE — collaborator/tools.py (is_controlled_location, _exec_write, resolve_in_workspace)", "collaborator/tools.py"),
    ("THE CODE — collaborator/governance.py (the controlled-location deny + govern_action)", "collaborator/governance.py"),
    ("THE CODE — collaborator/propose.py (proposer prompt + pool enrollment)", "collaborator/propose.py"),
    ("THE CODE — collaborator/proposalpool.py", "collaborator/proposalpool.py"),
    ("THE CODE — collaborator/session.py (controlled_paths + proposal_pool wiring)", "collaborator/session.py"),
    ("WHAT IS PINNED — tests/test_verifier_nested_write.py", "tests/test_verifier_nested_write.py"),
    ("WHAT IS PINNED — tests/test_collaborator_staging.py", "tests/test_collaborator_staging.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

This is a CODE review of a SHIPPED diff, not a design review. SalienceOS is a salience-based AI
control plane. Invariant P-01: salience INFLUENCES, policy AUTHORIZES. The "Collaborator" is a
governed agent: a PROPOSER model suggests actions; NOTHING runs until a human approves, and every
action passes a core-enforced capability gate + a workspace fence (resolve_in_workspace refuses a
path that escapes the workspace root). The VERIFIER enforces "the hands can't lie": a tool's
claimed side effects are checked against the independently-observed world. One FLOOR_KIND
obligation is the WRITE-SET BOUNDARY — "the declared paths changed, and NO undeclared path
changed" (observed via a pre/post snapshot_tree diff). Directories map to the marker "dir",
symlinks to "symlink:<target>" (not followed), files to their sha256.

OUT OF SCOPE (do not re-litigate): the two-agent memory architecture, the single-trust-domain
persistence decision (ADR 0002), and the correctness of the core capability gate / signed
PolicyCaps (assumed correct). Attack the NEW code below.

THE THREE CHANGES:

(1) VERIFIER EXEMPTION — observers.py. Before this, an honest NESTED write (a/b/c.txt) FALSE-
FAILED the write-set boundary: _exec_write auto-creates the parent dirs (a/, a/b/) so the write
can land, but those dirs showed up in the observed write-set while the contract declared only the
file — so "undeclared paths changed" tripped and every nested write failed verification. The fix:
`entailed_ancestors(path)` returns the strict ancestor dirs of the declared path, and
`observe_action` EXEMPTS them from the observed write-set (observed_write_set(pre, post, exempt)).
The claim: an ancestor of a DECLARED, separately re-hashed/stat'd write path is ENTAILED by that
write, not an undeclared mutation, and only that path's own parent chain is exempt — so nothing
else can be hidden. ATTACK THAT CLAIM. Especially: an exempted ancestor that PRE-EXISTED as a file
/ symlink / with content (does exempting mask a destructive file->dir replacement, a symlink swap,
or a deletion that NO other obligation catches?). Trace whether artifact re-hash (rehash) / path_
state independently catch each attack; a blocked attack is a useful NON-finding.

(2) CONTROLLED-LOCATION HARD-DENY — tools.is_controlled_location + the deny block in
govern_action. A write whose resolved path lands in a controlled subtree (default `.github`) is
DENIED *only when* intent.source == "proposed" (proposer origin, hardcoded in propose.py's
_candidate_from_response — the model's JSON cannot set it). A user-directed / approved placement is
deliberately unaffected. ATTACK: make a proposer-originated write LAND in `.github/**` anyway —
path aliases (`./`, `..`, trailing dots/slashes), CASE aliases on a case-insensitive filesystem
(`.GitHub/...`), symlinks, unicode/normalization; forging intent.source; or reaching `.github/`
via run_command instead. Also flag OVER-blocking (a legitimate user-directed placement wrongly
denied).

(3) STAGE POOL — proposalpool.ProposalPool, enrolled in propose(). Holds surfaced proposals by
reference so an undecided one stays PENDING/findable; approval still runs the full capability
re-gate (reauthorized_or_denied — TOCTOU). ATTACK: any way a pooled proposal gains authority, is
double-run, skips the approval re-gate, a memory-injected rationale forges UI structure in
snapshot(), or unbounded growth is a real problem.
"""

SYSTEM = """You are a senior security engineer doing an adversarial CODE review of a shipped diff
that modifies a safety-critical verifier boundary and adds an access-control rule. Your job is to
BREAK it, not praise it. Assume the author is wrong until proven otherwise.

Attack hardest, in order (name file:line and give a concrete input/bypass, and state whether any
OTHER check independently catches it):
1. VERIFIER EXEMPTION SOUNDNESS (observers.py). Can the entailed-ancestors exemption HIDE a real
   undeclared mutation from the write-set boundary? Push on: an exempted ancestor that pre-existed
   as a FILE replaced by a dir; an ancestor swapped for a symlink; a pre-existing file at an
   ancestor path being deleted/overwritten as a side effect; a declared path crafted so its
   "ancestors" are a sensitive existing directory; interaction with dir.make / file.delete /
   shell.run declared_outputs; whether observed_write_set's exemption is bounded to strictly the
   declared path's parent chain. For each: what does the verifier conclude vs reality, and does
   rehash()/path_state()/exit independently fail it closed?
2. CONTROLLED-LOCATION BYPASS (tools.is_controlled_location + governance deny). Land a proposer-
   originated write in a controlled subtree despite the deny — path/case/symlink/normalization
   aliases (note vetoledger.normalize_intent uses os.path.normcase as a precedent; does
   is_controlled_location?), forging intent.source, or a different tool. Also: is the root-anchored
   prefix match correct (no false-negative that lets `.github` through, no false-positive that
   blocks legitimate paths)?
3. POOL + PROPOSE WIRING. Authority leak, double execution, skipping the approval capability
   re-gate, snapshot() structure-forging from a memory-injected rationale, unbounded-growth DoS.
4. STAGING PROMPT. Can the _PROPOSER_SYSTEM controlled-location instruction be turned against the
   system, or does it leak a bypass?

Calibration: concrete, honest, code-grounded. Distinguish a real guarantee-breaking bug from a
belt-and-suspenders nit. If an attack is blocked by another obligation or the workspace fence, say
so explicitly (non-findings are valuable). If the code is sound with only minor notes, say so
plainly — do not invent severity."""

USER_TMPL = """Review the SHIPPED staging-subsystem diff below.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) /
CONCRETE INPUT OR BYPASS / WHY IT BREAKS A GUARANTEE / WHETHER ANOTHER CHECK CATCHES IT / FIX.
Then STEELMAN (2-3 sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence).

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
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS Staging Subsystem Red-Team"},
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
    raw = HERE / "raw_staging"
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
                    f"# Staging subsystem red-team: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — staging subsystem CODE red-team =======")
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
