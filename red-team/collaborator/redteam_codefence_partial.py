"""Red-team + CERTIFY the completeness fix (require BOTH code-root packages; fail closed on empty OR
partial PROTECTED_ROOTS) on top of the already-reviewed codefence.

DELTA panel (Josh's standing rule: external review on EVERY non-doc PR). The base codefence was
certified in the PR #33 panel; the empty-only fail-closed in the PR #34 panel — whose 5/5 convergent
finding (partial resolve still fails open) THIS change addresses. Attack the NEW completeness check.

Reports per-model + total API cost (standing request).

Usage:  python red-team/collaborator/redteam_codefence_partial.py [openai/gpt-5.1 ...]
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
    ("THE DELTA — the exact diff this PR ships (read this first)",
     "red-team/collaborator/raw_codefence_partial/partial.diff"),
    ("THE CODE (full, for grounding) — collaborator/codefence.py",
     "collaborator/codefence.py"),
    ("THE CODE — collaborator/session.py (calls disjoint_from_code at construction)",
     "collaborator/session.py"),
    ("WHAT IS PINNED — tests/test_collaborator_codefence.py (empty + PARTIAL fail-closed tests)",
     "tests/test_collaborator_codefence.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

DELTA CODE review + CERTIFICATION of a SHIPPED completeness hardening on codefence.py. Lineage:
PR #33 added codefence (5-vendor panel CERTIFIED the run_command autonomy claim). PR #34 made
disjoint_from_code fail closed when PROTECTED_ROOTS is EMPTY. The PR #34 panel then found — 5/5
convergent — that #34 fixed only the EMPTY case: a PARTIAL resolve (collaborator/ resolves but
salienceos/ does not) yields a NON-EMPTY tuple that passes the emptiness check while silently
fencing only one package, leaving salienceos/ reachable. THIS change closes that.

codefence.py protects the Collaborator's own code (collaborator/ + salienceos/). PROTECTED_ROOTS is
resolved once at import from __file__ (collaborator/, always present — codefence.py lives in it) and
salienceos.__file__. disjoint_from_code() refuses a Session workspace that equals/contains/is-inside
a protected root, and is called unconditionally by Session.__init__.

WHAT THIS PR SHIPS (attack + CERTIFY this):
  - _resolved_roots() returns (slot_name, dir) pairs, resolving each package from its IMPORTED MODULE:
    "collaborator" from THIS module's __file__, "salienceos" from salienceos.__file__. Module globals:
    PROTECTED_ROOTS = the resolved dirs; _RESOLVED_PACKAGES = frozenset of slot names that resolved.
  - SINGLE source of truth: _code_slots() returns (name, __file__) per package; _EXPECTED_PACKAGES is
    DERIVED from it (not a duplicate literal), so the resolution sources and the completeness list
    cannot drift. PROTECTED_ROOTS and _RESOLVED_PACKAGES both derive from the same _RESOLVED tuple
    (no dedup), so they cannot desync. (This is the final delta after prior rounds fixed basename
    brittleness, removed a path-dedup, and unified the perimeter list.)
  - disjoint_from_code() computes `missing = [pkg for pkg in _EXPECTED_PACKAGES if pkg not in
    _RESOLVED_PACKAGES]` and, if `missing`, FAILS CLOSED (raises WorkspaceOverlapsCodeError, a
    ValueError) — refusing EVERY workspace. Covers EMPTY (both slots absent) AND PARTIAL (one absent).
    Completeness is by SLOT (which module resolved), NOT by directory basename.
  - This is the REVISED design after a prior completeness panel found that a basename-matching version
    (`pkg not in {r.name ...}`) would FALSE-FAIL on legitimate odd-named package dirs (case-insensitive
    checkout, symlink, editable/vendor install). The slot approach fixes that regression; it also moots
    the "decoy dir" concern (roots come ONLY from imported modules — a decoy enters only if it IS the
    imported salienceos, in which case protecting it is correct).
  - Tests: incomplete-slots (empty / collaborator-only / salienceos-only) all fail closed; odd-basename
    + complete-slots does NOT false-fail.

THE CERTIFICATION CLAIM (attack it): "disjoint_from_code fails CLOSED unless BOTH expected package
SLOTS resolved (empty AND partial), with NO false-fail regression on legitimate layouts whose package
dir basenames differ from the import names; and completeness is enforced at Session construction, so
no Session with incomplete roots exists (no in-band consumer sees incomplete roots)."

OUT OF SCOPE (already certified — do NOT re-report): the run_command autonomy-withheld floor; the
disjointness resolve()/symlink/case/traversal handling for the both-present case; names_code_root's
documented porousness AS A RECOGNIZER; the honest code_protected flag; the human-approved-rewrite
residual; OS-level structural prevention (deferred). ADR 0001/0002; the core gate internals.
"""

SYSTEM = """You are a senior application-security engineer doing an adversarial DELTA review AND
CERTIFICATION of a SLOT-BASED completeness hardening in a governance guard. BREAK it, but also
certify: if you cannot break a claim, say so explicitly (a blocked attack is a valuable NON-finding).
Assume the author is wrong until proven otherwise. Concretely try to:
  - defeat the completeness intent (fail-OPEN): is there a state where a slot is marked resolved in
    _RESOLVED_PACKAGES yet the REAL package is left unfenced (its dir not in PROTECTED_ROOTS)? Can
    PROTECTED_ROOTS and _RESOLVED_PACKAGES DESYNC? Both are derived from the same _RESOLVED tuple with
    NO path-dedup (a prior version's `p not in seen` was REMOVED after the panel flagged it) — confirm
    a slot's name and its path always land together, and that removing the dedup introduced no
    fail-open (the degenerate "both packages in one dir" case now protects that dir under both slots).
    Since roots come ONLY from imported modules' __file__ (not a filesystem scan), confirm/deny that a
    "decoy directory" can cause a false PASS.
  - find a REGRESSION / false fail-closed: does the slot check raise in a LEGITIMATE both-present
    environment? Confirm the basename-independence claim: odd-named package dirs (case-insensitive
    checkout, symlink, editable/vendor, monorepo) must NOT false-fail now that completeness is by
    slot. Is there any residual basename dependency left in the gate?
  - namespace packages / no __file__: if salienceos.__file__ is None, the slot is absent → fail
    closed. Is that the correct/safe outcome, or does it wrongly refuse a valid namespace-package
    deployment (availability vs safety trade — which is right for a governance guard)?
  - is there still a downstream consumer of PROTECTED_ROOTS / _RESOLVED_PACKAGES that runs WITHOUT a
    Session having passed disjoint_from_code? Confirm/deny names_code_root and any other reader are
    only reachable post-construction.
  - could _EXPECTED_PACKAGES / the _resolved_roots slot list drift out of lockstep (a third F1
    package added to one but not the other)? Is the two-slot assumption correct for this repo today?
  - is the raise safe (deterministic, no info leak — the message now interpolates only `missing`, a
    subset of the static slot names)?
Name file:line, give a CONCRETE input/bypass, and state whether ANOTHER check independently catches
it. Distinguish a real guarantee-breaking bug from a belt-and-suspenders nit or a stated non-goal."""

USER_TMPL = """Review + CERTIFY the SHIPPED completeness delta below (require both code-root packages).

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) /
CONCRETE INPUT OR BYPASS / WHY IT BREAKS A GUARANTEE / WHETHER ANOTHER CHECK CATCHES IT / FIX.
Then a CERTIFICATION LINE for the claim ("fails closed unless both packages located; no regression;
no in-band consumer of incomplete roots") — CERTIFIED / NOT-CERTIFIED + one sentence. Then STEELMAN
(2-3 sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence, and the single
highest-value fix).

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
                 "HTTP-Referer": "https://salient-os.local/redteam",
                 "X-Title": "SalienceOS codefence completeness Panel"},
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
    raw = HERE / "raw_codefence_partial"
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
                    f"# codefence completeness panel: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — codefence completeness panel =======")
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
    print("=======================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
