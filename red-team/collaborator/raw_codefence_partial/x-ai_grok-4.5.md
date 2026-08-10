# codefence completeness panel: x-ai/grok-4.5

_finish=stop seconds=123.5 cost=$0.0656 usage={'prompt_tokens': 11799, 'completion_tokens': 7036, 'total_tokens': 18835, 'cost': 0.0655964, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0655964, 'upstream_inference_prompt_cost': 0.0233804, 'upstream_inference_completions_cost': 0.042216}, 'completion_tokens_details': {'reasoning_tokens': 4286, 'image_tokens': 0, 'audio_tokens': 0}}_

# Adversarial DELTA Review + Certification
**Target:** slot-based completeness hardening (`03413dd`) — `collaborator/codefence.py`  
**Stance:** author wrong until proven otherwise; blocked attacks reported as NON-findings.

---

## Findings

### F1 / NO production desync of PROTECTED_ROOTS ↔ _RESOLVED_PACKAGES
**SEVERITY:** NON-FINDING (blocked)  
**LOCATION:** `collaborator/codefence.py:86-91`  
**CONCRETE INPUT/BYPASS ATTEMPTED:**
- Force partial resolve: `salienceos.__file__ = None` at import → `_RESOLVED=(("collaborator", collab_dir),)`, `PROTECTED_ROOTS=(collab_dir,)`, `_RESOLVED_PACKAGES=frozenset({"collaborator"})`.
- Degenerate co-location: both slots resolve to the same `Path` → `_RESOLVED` keeps two pairs, `PROTECTED_ROOTS` may duplicate the path, `_RESOLVED_PACKAGES` still has both names.
- Try to mark a slot resolved without fencing its dir: impossible — `out.append((name, p))` is one atomic write (`codefence.py:72-77`).

**WHY IT DOES NOT BREAK THE GUARANTEE:**  
Single tuple `_RESOLVED` is the only source; `PROTECTED_ROOTS` and `_RESOLVED_PACKAGES` are pure projections. No path-dedup filter can drop a path while keeping its name (the removed `p not in seen` cannot create a name-without-path). Co-located packages protect that one dir under both slots — correct, not fail-open.

**ANOTHER CHECK:** N/A — structural.  
**FIX:** None.

---

### F2 / Decoy directory cannot cause false PASS
**SEVERITY:** NON-FINDING (blocked; prior panel over-rate correctly refuted)  
**LOCATION:** `collaborator/codefence.py:54-81` (`_resolved_roots`)  
**CONCRETE INPUT/BYPASS ATTEMPTED:** Plant `/tmp/salienceos/` (or any basename-matching tree) on disk while the real import is elsewhere; or plant a decoy and hope completeness passes while the real tree is unfenced.

**WHY IT DOES NOT BREAK:** Roots come only from `(__file__, salienceos.__file__)` via `_code_slots()` — no filesystem scan, no basename discovery. A decoy never enters `PROTECTED_ROOTS` unless it *is* the imported module’s resolved parent, in which case fencing it is correct. Completeness is slot membership, not “dir named salienceos exists.”

**ANOTHER CHECK:** N/A.  
**FIX:** None.

---

### F3 / No fail-OPEN on empty or partial slot resolve
**SEVERITY:** NON-FINDING (blocked — the bug this PR claims to close is closed)  
**LOCATION:** `collaborator/codefence.py:103-112`  
**CONCRETE INPUT/BYPASS ATTEMPTED:**

| State | `_RESOLVED_PACKAGES` | `missing` | Result |
|---|---|---|---|
| empty | `{}` | `['collaborator','salienceos']` | raise |
| collaborator-only | `{collaborator}` | `['salienceos']` | raise |
| salienceos-only | `{salienceos}` | `['collaborator']` | raise |
| both | `{collaborator,salienceos}` | `[]` | proceed to overlap |

Pre-#34/#this: collaborator-only left non-empty `PROTECTED_ROOTS` and silently skipped the empty check — **that path is gone.** Every incomplete matrix raises `WorkspaceOverlapsCodeError` before the overlap loop. Pinned by `test_incomplete_resolved_packages_fail_closed`.

**ANOTHER CHECK:** `Session.__init__` always calls `disjoint_from_code` (`session.py` ~construction block) — no Session object exists without passing this.  
**FIX:** None.

---

### F4 / No false fail-CLOSED on odd-basename legitimate layouts (gate)
**SEVERITY:** NON-FINDING (blocked)  
**LOCATION:** `collaborator/codefence.py:103-104` (completeness predicate)  
**CONCRETE INPUT/BYPASS ATTEMPTED:** Package dirs named `SalienceOS-v2`, `collab-impl`, case-insensitive checkout `SalienceOS`, editable/vendor path, symlink basename ≠ import name — with both modules actually resolved.

**WHY IT DOES NOT BREAK:**  
`missing = [pkg for pkg in _EXPECTED_PACKAGES if pkg not in _RESOLVED_PACKAGES]` uses **slot names only**. No `Path.name`, no `{r.name for r in roots}`. Overlap loop is pure path equality/containment. Residual basename text in the error string (“collaborator/ and salienceos/”) is prose, not a predicate. Pinned by `test_completeness_is_slot_based_not_basename`.

**ANOTHER CHECK:** N/A.  
**FIX:** None for the gate.

---

### F5 / Residual basename dependency in a *test* (not the gate)
**SEVERITY:** LOW (test hygiene / belt-and-suspenders nit)  
**LOCATION:** `tests/test_collaborator_codefence.py` — `CodeRootModel.test_protected_roots_cover_both_packages_and_are_real_dirs` (approx. lines asserting `p.name`)  
**CONCRETE INPUT:** Legitimate install where resolved dirs are `.../SalienceOS-v2` and `.../collab-impl`, both slots resolved.  
**WHY IT MATTERS (weakly):** Product gate passes (F4); this test still does `names = {p.name for p in PROTECTED_ROOTS}` / `assertIn("collaborator"/"salienceos")` and would red on that layout. Does **not** false-fail production, does **not** open a fail-open.  
**ANOTHER CHECK:** New slot-based tests cover the real invariant.  
**FIX:** Assert slot completeness instead of/in addition to basenames, e.g.  
`self.assertEqual(codefence._RESOLVED_PACKAGES, frozenset(codefence._EXPECTED_PACKAGES))`  
and keep `is_dir`/`is_absolute` checks; treat basename match as “common layout” optional soft check only.

---

### F6 / Namespace package / `__file__ is None` → fail closed
**SEVERITY:** NON-FINDING as defect; documented safety/availability tradeoff (correct for this guard)  
**LOCATION:** `collaborator/codefence.py:66-67`, `103-112`  
**CONCRETE INPUT:** `salienceos` imported as a PEP 420 namespace (`salienceos.__file__ is None`); collaborator still resolves.  
**OUTCOME:** `missing=['salienceos']` → refuse every Session.  
**WHY THIS IS THE RIGHT TRADE FOR A GOVERNANCE GUARD:** Completeness intent is “both F1 packages located and fenced.” A namespace top-level gives no stable package dir to put in `PROTECTED_ROOTS`; treating that as success would resurrect partial/empty fail-open. Availability of exotic namespace deployments loses to fail-closed safety. Collaborator cannot be namespace here (`codefence.py` has a real `__file__`).  
**ANOTHER CHECK:** N/A.  
**FIX:** None required. Optional ops note in docs if namespace installs are ever supported (then resolve via `__path__[0]`, still fail closed if empty/unresolvable).

---

### F7 / In-band consumers of incomplete roots without passing `disjoint_from_code`
**SEVERITY:** NON-FINDING for the stated claim  
**LOCATION:** `session.py` (Session.__init__ → `disjoint_from_code`); `names_code_root` at `codefence.py:147+`  
**CONCRETE INPUT/BYPASS ATTEMPTED:**
1. Import codefence with partial resolve; call `names_code_root(...)` with no Session.  
2. Reach `govern_action` / `approve` without a constructed Session.  
3. Subclass Session and skip `super().__init__`.

**ANALYSIS:**
- (1) `names_code_root` *can* read a partial `PROTECTED_ROOTS` out-of-band. It is explicitly **porous DiD, not a boundary**; `code_protection_available()` is still `False`, so autonomy stays withheld. Not an in-band fencing consumer and not a completeness hole in the Session perimeter.
- (2) Govern/approve paths require a `Session`; construction always runs `disjoint_from_code` first. No Session with incomplete slots exists.
- (3) Hostile subclass skipping `__init__` is outside the guard’s threat model (stated non-goal / API abuse).

**CLAIM HOLD:** “no Session with incomplete roots exists (no in-band consumer sees incomplete roots)” — **holds**.  
**FIX:** None for claim. Optional nit: lazy-assert completeness at the top of `names_code_root` if you want defense-in-depth for out-of-band callers (not required).

---

### F8 / `_EXPECTED_PACKAGES` / slot list drift / two-slot assumption
**SEVERITY:** NON-FINDING  
**LOCATION:** `collaborator/codefence.py:43-51`, `54-81`  
**CONCRETE DRIFT ATTEMPT:** Add a third F1 package to only one list.

**WHY BLOCKED:** `_EXPECTED_PACKAGES = tuple(name for name, _ in _code_slots())` and `_resolved_roots()` both iterate `_code_slots()` — one literal perimeter `(("collaborator", __file__), ("salienceos", getattr(salienceos, "__file__", None)))`. No second hand list.  
**Two-slot assumption today:** Correct for this repo (collaborator + salienceos core); tests and module doc agree.  
**FIX:** None. Perimeter growth = edit `_code_slots` only.

---

### F9 / Raise safety (determinism / info leak)
**SEVERITY:** NON-FINDING  
**LOCATION:** `collaborator/codefence.py:109-112`  
**CONCRETE CHECK:** Message interpolates only `missing`, a list comprehension over static `_EXPECTED_PACKAGES` names (`"collaborator"`, `"salienceos"`). No paths, env, usernames, or exception details from resolve failures (those are swallowed in `_resolved_roots`). Deterministic order follows `_EXPECTED_PACKAGES` tuple order. Subclasses `ValueError` as before.  
**FIX:** None.

---

### F10 / Residual gate basename dependency
**SEVERITY:** NON-FINDING  
**LOCATION:** full `disjoint_from_code` + `_resolved_roots`  
**SEARCHED:** No `Path.name` / basename set membership in completeness or overlap predicates. `names_code_root` matches full `root.as_posix()` substrings (path identity, not package basename policy).  
**FIX:** None.

---

## CERTIFICATION LINE

**CERTIFIED** — `disjoint_from_code` fails closed unless both expected package slots resolved (empty and partial); no false-fail regression on odd-basename layouts in the gate; completeness runs at Session construction so no in-band Session/govern consumer observes incomplete roots.

---

## STEELMAN

The delta correctly closes the PR #34 residual (non-empty but partial `PROTECTED_ROOTS`) by moving completeness from “tuple non-empty” / basename matching to **slot membership** derived from a single `_code_slots()` source, with `PROTECTED_ROOTS` and `_RESOLVED_PACKAGES` as inseparable projections of one `_RESOLVED` tuple. Removing path-dedup was right: it cannot desync name from path, and the co-located degenerate case stays safe. Fail-closed on missing `__file__` (namespace) is the correct governance posture; decoy-dir fail-open is unreachable because roots are import-derived only.

---

## VERDICT

**SOUND** — Adversarial attempts at fail-open (partial resolve, desync, decoy dir, dedup removal) and false fail-closed (odd basenames, residual basename gate checks) did not break the certification claim; only a LOW test-basename nit remains.

**Single highest-value fix:** Update `test_protected_roots_cover_both_packages_and_are_real_dirs` to assert `_RESOLVED_PACKAGES == frozenset(_EXPECTED_PACKAGES)` (slot completeness) rather than requiring directory basenames to equal import names — so the suite matches the slot-based product invariant.
