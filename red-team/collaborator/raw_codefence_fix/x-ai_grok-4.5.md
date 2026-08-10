# PR#34 codefence fail-closed panel: x-ai/grok-4.5

_finish=stop seconds=73.6 cost=$0.0414 usage={'prompt_tokens': 9256, 'completion_tokens': 3858, 'total_tokens': 13114, 'cost': 0.0414424, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0414424, 'upstream_inference_prompt_cost': 0.0182944, 'upstream_inference_completions_cost': 0.023148}, 'completion_tokens_details': {'reasoning_tokens': 2215, 'image_tokens': 0, 'audio_tokens': 0}}_

# PR #34 Delta Review — Adversarial CERTIFICATION

**Scope:** Only the new empty-`PROTECTED_ROOTS` fail-closed guard in `disjoint_from_code`. Base #33 surface (autonomy floor, resolve()/symlink disjointness, porous `names_code_root`, honest flag) is not re-opened.

---

## Findings

### F1 / PARTIAL `PROTECTED_ROOTS` STILL FAIL-OPEN FOR MISSING PACKAGE / LOW (residual non-goal)
- **LOCATION:** `collaborator/codefence.py:48-66` (`_resolved_roots`); empty check at `codefence.py:75-84`
- **CONCRETE INPUT:** Import env where `collaborator.__file__` resolves but `salienceos.__file__` is missing/unresolvable (or the reverse).  
  `PROTECTED_ROOTS == (Path("…/collaborator"),)` — truthy, so the new guard does **not** raise. A workspace overlapping only the missing package is allowed by `disjoint_from_code`.
- **WHY IT BREAKS A GUARANTEE:** It does **not** break the #34 claim (claim is empty-only). It is the pre-existing partial-roots gap: F1 is documented to span **both** packages, but one skipped root still yields a non-empty tuple and a silent hole for that package.
- **ANOTHER CHECK:** Autonomy floor (`code_protection_available()==False` → no auto `run_command`) still holds; OS bind still deferred. Disjointness for the *present* root still holds. No #34 check addresses partial.
- **FIX (optional, out of claim):** In `_resolved_roots` / import, require both expected names (or a minimum cardinality + name set) and fail closed / warn if the set is incomplete — separate from the empty tuple guard.
- **CLASS:** Stated non-goal / residual. Worth tracking; not a #34 guarantee break.

### F2 / `names_code_root` STILL NO-OPS WHEN ROOTS EMPTY / LOW (belt-and-suspenders nit)
- **LOCATION:** `collaborator/codefence.py:128-133` (`for root in PROTECTED_ROOTS`)
- **CONCRETE INPUT:** `PROTECTED_ROOTS = ()` then `names_code_root(["python","-c","open('/x/collaborator/g.py','w')"])` → `""` (no raise).
- **WHY IT BREAKS A GUARANTEE:** It does not. `names_code_root` was certified **porous / not a boundary** in #33. Live govern/approve paths require a `Session`, and `Session.__init__` always calls `disjoint_from_code` (`session.py` ~workspace init), which now refuses construction when roots are empty. No session ⇒ no govern path on empty roots.
- **ANOTHER CHECK:** Yes — `disjoint_from_code` empty raise blocks Session construction. Autonomy floor still independent.
- **FIX:** None required for the claim. Optional symmetry: early-return documentation or assert in DiD helper only if you want defense-in-depth consistency (not load-bearing).
- **CLASS:** Nit / non-boundary layer.

### F3 / EXCEPTION TYPE SEMANTICS OVERLOADED / LOW (nit, non-breaking)
- **LOCATION:** `collaborator/codefence.py:80-84`
- **CONCRETE INPUT:** `PROTECTED_ROOTS=()` + any workspace → `WorkspaceOverlapsCodeError("cannot locate the Collaborator's own code roots…")` even though no overlap was evaluated.
- **WHY IT BREAKS A GUARANTEE:** Does not. Subclasses `ValueError` as designed so `Session` construction and existing `assertRaises(ValueError)` keep working (`test_empty_protected_roots_fails_closed`, `test_session_construction_refuses_overlap`). Message is explicit and non-leaky.
- **ANOTHER CHECK:** N/A (intentional fail-loud compose).
- **FIX:** Optional dedicated `CodeRootsUnavailableError(WorkspaceOverlapsCodeError)` if callers ever need to distinguish; not required for safety.
- **CLASS:** API-semantics nit.

---

## Attacks attempted vs claim — NON-findings (blocked / clean)

| Attack | Result |
|--------|--------|
| Empty tuple bypasses `if not PROTECTED_ROOTS` | **Blocked.** `not ()` is `True` in Python; raises before the for-loop. |
| Empty-like only via `_resolved_roots` (`[]`→`()`) | **Blocked.** Only `tuple`; emptiness caught. |
| Non-empty invalid/nonexistent roots from `_resolved_roots` | **Not produced.** Append only after `resolve()` + `p.is_dir()`. Different threat than empty; not a silent no-op of the *loop* (loop still runs). |
| Path where empty roots + `disjoint_from_code` does not raise | **None found** in shipped code. |
| Session construction skips `disjoint_from_code` | **No.** Unconditional call after `self.workspace = Path(workspace)`. |
| Normal non-empty path regression | **None.** Early raise is dead when `PROTECTED_ROOTS` has ≥1 entry; loop/resolve behavior unchanged. |
| Exception type breaks callers/tests | **No.** Still `WorkspaceOverlapsCodeError` ⊂ `ValueError`. |
| Shadows unresolvable-workspace raise in a harmful way | **No meaningful regression.** On empty roots every workspace is refused anyway; on non-empty, order identical to pre-#34 after the new branch. |
| Raise unsafe (crash/info-leak/non-deterministic) | **Safe.** Pure control-flow raise; fixed message; no path/env dump beyond static text. |
| Legitimate normal env hits the new raise | **No.** `collaborator` `__file__` resolves in-band; `test_protected_roots_cover_both_packages_and_are_real_dirs` pins non-empty. Pathological double-unresolvable import only. |

**Cannot break the headline empty-roots fail-closed claim.** That is a valuable NON-finding.

---

## CERTIFICATION LINE

**CERTIFIED** — When `PROTECTED_ROOTS` is empty, `disjoint_from_code` always raises `WorkspaceOverlapsCodeError` before any no-op loop; the normal non-empty path and `Session` construction behavior/exception family are unchanged.

---

## STEELMAN

The #33 panel’s convergent fail-open (empty roots ⇒ zero iterations ⇒ silent pass) is closed at the correct layer: construction-time `disjoint_from_code`, which every `Session` already must pass, with a `ValueError` subclass that matches existing fail-loud construction. The guard is minimal, deterministic, tested via `patch.object(..., ())`, and does not disturb resolve/overlap logic when roots exist. Treating full emptiness as “refuse all workspaces” is the right governance default for a check that would otherwise protect nothing.

---

## VERDICT

**SOUND** — The shipped delta enforces fail-closed on empty `PROTECTED_ROOTS` with no demonstrated bypass and no normal-path regression; residual partial-roots (F1) is a pre-existing non-goal of this PR, not a break of the certified claim.

**Single highest-value fix (optional follow-up, not blocking):** fail closed or hard-require both `collaborator` and `salienceos` roots by name/cardinality at import so a one-package resolve cannot leave a silent hole for the other — outside #34’s empty-tuple claim.
