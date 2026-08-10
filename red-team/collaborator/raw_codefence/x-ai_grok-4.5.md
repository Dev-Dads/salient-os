# PR#33 codefence certification panel: x-ai/grok-4.5

_finish=stop seconds=175.9 cost=$0.1413 usage={'prompt_tokens': 41912, 'completion_tokens': 9618, 'total_tokens': 51530, 'cost': 0.1413144, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1413144, 'upstream_inference_prompt_cost': 0.0836064, 'upstream_inference_completions_cost': 0.057708}, 'completion_tokens_details': {'reasoning_tokens': 7377, 'image_tokens': 0, 'audio_tokens': 0}}_

# PR #33 Adversarial Review + Certification

## Findings

### F-1 / TITLE: Empty `PROTECTED_ROOTS` makes `disjoint_from_code` a no-op (documented fail-open)
**SEVERITY:** LOW  
**LOCATION:** `collaborator/codefence.py:41-56` (`_resolved_roots`), `collaborator/codefence.py:74-88` (`disjoint_from_code`)

**CONCRETE INPUT:**  
If both root resolutions are skipped (`__file__` / `salienceos.__file__` missing, `resolve()` throws, or `parent` is not a dir), then `PROTECTED_ROOTS == ()`.  
```python
Session(workspace="/path/to/collaborator")  # does not raise
# write_file default leash is act_then_report → autonomous write into the code tree
```

**WHY IT MATTERS:** The workspace↔code invariant this PR claims to enforce is skipped entirely. Fenced `write_file` (ATR by default) could then rewrite the rulebook without a human hand. This does **not** break the headline *run_command* autonomy claim (floor does not consult roots).

**ANOTHER CHECK?** Autonomy-withheld floor still holds for `run_command`. No independent check restores disjointness.

**FIX:** Fail closed at import (or at first `disjoint_from_code`) if `PROTECTED_ROOTS` is empty; refuse Session construction rather than silently protecting nothing.

---

### F-2 / TITLE: In-workspace symlink/junction to a code root is outside `disjoint_from_code`’s predicate
**SEVERITY:** LOW (belt-and-suspenders / residual; not an autonomy break)
**LOCATION:** `collaborator/codefence.py:74-88`

**CONCRETE INPUT:**  
Workspace `/tmp/ws` is disjoint by path prefix; host or a later approved shell does `ln -s /abs/path/to/collaborator /tmp/ws/code`. Path-prefix check still passes. A later `run_command` can write through the link without the workspace path itself overlapping a protected root.

**WHY IT MATTERS:** Docstring goal (“governed shell has no in-workspace path to the rules”) is path-prefix only, not reachability. `Path.resolve()` on the *workspace argument* closes equal/inside/containing via symlink/**`..`**/relative/junction for the workspace root itself; it does **not** walk workspace children.

**ANOTHER CHECK?**  
- `write_file`/`read_file`: `resolve_in_workspace` denies escape through the link.  
- Autonomous `run_command`: still floored to HELD.  
- Human-approved `run_command` through the link: acknowledged residual (deceived/approved shell), out of scope.

**FIX (optional DiD):** Not required for the headline claim. If desired, document explicitly as non-goal or add a one-time workspace tree scan (expensive; easy to get wrong).

---

### F-3 / TITLE: No control path relies on `names_code_root` as the sole boundary
**SEVERITY:** n/a — **NON-FINDING (certified)**  
**LOCATION:** `governance.py` proposer deny; `loop.py` approve re-deny; preview/`code_touch` only

Porous bypasses (base64, `python -c` with computed path, vars) are **by design**. Real autonomous boundary is the ATR→`propose_first` floor, which does not call `names_code_root`. Human-approved rewrite remains the stated residual.

---

### F-4 / TITLE: Autonomous `run_command` with `code_protection_available()==False` — attack blocked
**SEVERITY:** n/a — **NON-FINDING (blocked attack)**  
**LOCATION:** `governance.py` autonomy-withheld floor (after emission floor, before allowlist dispatch); dispatch at `if leash == ACT_THEN_REPORT: return execute_and_verify(...)`

**Probes attempted:**

| Probe | Result |
|--------|--------|
| `leash_overrides={"run_command": ACT_THEN_REPORT}` | Floored to `PROPOSE_FIRST` → HELD |
| Keyword `leash=ACT_THEN_REPORT` | Floored (does not consult `host_directed_atr`) |
| Signed PolicyCaps `run_command→act_then_report` | `apply_cap` keeps ATR, then floor tightens |
| `intent.source` ∈ {structured, content_block, content_json, host, proposed, other} | Floor is source-agnostic |
| Parser-originated intents | Parser never passes keyword `leash`; cannot forge `proposed` |
| `leash` still ATR at dispatch for `run_command` | No — floor is exact pre-dispatch complement for `name=="run_command" ∧ ATR ∧ ¬protection` |
| Tool-name alias | None; only exact `run_command` |
| `apply_cap` monotonicity | Only tightens; `NOTIFY_ONLY` host tighten survives |
| Direct `approve()` | Is the human hand (out of scope for “autonomous”) |
| `emit()` | `net_post` only |
| Empty `PROTECTED_ROOTS` | Floor still applies |
| Weird `command` types (dict/None/int) | `names_code_root` / `_exec_command` fail soft; no crash-open to RAN |

**Floor vs dispatch gap:** None for this build. Dispatch is allowlist on `leash == ACT_THEN_REPORT`; floor strips that value for every `run_command` while protection is false.

---

### F-5 / TITLE: `approve()` args snapshot matches execute; scan/exec mismatch not available
**SEVERITY:** n/a — **NON-FINDING**  
**LOCATION:** `loop.py` `args = dict(decision.args)` then re-deny on `args`, then `execute_and_verify(..., args, ...)`

Same snapshot object path for code-root re-deny and execute. No autonomous path: HELD still requires `approve()`.

Note: `run_command` has no emission-style seal (pre-existing; deceived-human TOCTOU is explicitly out of scope). Proposer hard-deny + collaborator-origin re-deny remain porous DiD only.

---

### F-6 / TITLE: `code_protected` cannot render TRUE in this build
**SEVERITY:** n/a — **NON-FINDING**  
**LOCATION:** `codefence.py` `return False`; `tools.py` `_exec_command` sets `code_protected=code_protection_available()`; preview uses the same

Summary shows `⚠ code NOT protected` whenever the flag is non-`None` and false. No production path sets `True`.

---

### F-7 / TITLE: Regression check on existing guarantees
**SEVERITY:** n/a — **NON-FINDING**

| Guarantee | Status |
|-----------|--------|
| `write_file` ATR auto-run | Untouched (floor is `intent.name == "run_command"` only) |
| `net_post` emission floor / F1 | Still above; independent conditions; floor only ANDs tighter |
| Proposer shell floor (`source=="proposed"`) | Still present; autonomy floor is additional universal withhold |
| Workspace fence `resolve_in_workspace` | Unchanged |
| `approve()` seal / consumed / cross-subject | New check is before `consumed=True`; deny paths do not consume; seal logic unchanged |

---

### F-8 / TITLE: Crash-open of `govern_action` / Session on weird inputs
**SEVERITY:** n/a — **NON-FINDING for this delta**

- Unresolvable workspace → `WorkspaceOverlapsCodeError` (fail closed).  
- `names_code_root` accepts non-list commands via `str(command or "")`.  
- `_exec_command` returns `ok=False` for bad command types (no throw into RAN).  
- Pre-existing `intent.args` assumed dict (parser/`ToolIntent` contract) — not introduced here.

---

### F-9 / TITLE: Symlink/case/`..`/relative/junction on the **workspace path itself**
**SEVERITY:** n/a — **NON-FINDING (blocked for stated equal/inside/containing)**  
**LOCATION:** `disjoint_from_code` uses `Path(workspace).resolve()` vs import-time resolved `PROTECTED_ROOTS`

Equal / inside / containing after resolution are refused. Symlink workspace → code root, `..`, relative, and normal junctions are closed by `resolve()` when the path is resolvable. Case folding follows platform `realpath` behavior for existing paths (same pattern as the rest of the tree). Residual: empty-roots fail-open (F-1), not a resolve bypass.

---

## CERTIFICATION LINE

**CERTIFIED** — With `code_protection_available()==False`, every `run_command` that would be `act_then_report` is monotonically floored to `propose_first` before the allowlist auto-run dispatch on all sources and leash inputs, so an autonomous RAN/FAILED `run_command` (no human `approve()`) is unreachable through the seam.

---

## STEELMAN

The design correctly refuses to pretend `names_code_root` is a fence and instead removes the dangerous privilege entirely: unfenced shell may not auto-run until a real structural probe exists. Placing a source-agnostic, monotonic `apply_cap` floor immediately before the F0 allowlist dispatch makes the trigger the practical complement of auto-exec for `run_command`, and composing it with the emission/proposer floors by tightening-only avoids regressing F1. Honest `code_protected=False` plus construction-time path-prefix disjointness are the right cheap cross-platform layers given the owner’s “don’t hard-fence maintenance shells” constraint.

---

## VERDICT

**SOUND** — Headline autonomous self-rewrite path is closed; residuals are documented fail-open on empty roots (LOW), porous DiD recognizer (non-goal), and human-approved shell (explicit non-goal).  

**Single highest-value fix:** Fail closed when `PROTECTED_ROOTS` is empty (import or Session construction) so the workspace disjointness invariant cannot silently disappear on odd packaging layouts.
