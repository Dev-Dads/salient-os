# Stage-4-live red-team: z-ai/glm-5.2

_finish=stop seconds=116.1 usage={'prompt_tokens': 10832, 'completion_tokens': 9727, 'total_tokens': 20559, 'cost': 0.0089720622, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 9423, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0089720622, 'upstream_inference_prompt_cost': 0.0008402902, 'upstream_inference_completions_cost': 0.008131772}, 'completion_tokens_details': {'reasoning_tokens': 7797, 'image_tokens': 0, 'audio_tokens': 0}}_

# Adversarial Review: Stage-4-Live Wiring

## Findings

---

### F-1 / ADAPTATION SIGNAL EMITTED BUT NEVER CONSUMED FOR NON-ARTIFACT TOOLS
**SEVERITY:** MEDIUM
**LOCATION:** `collaborator/governance.py` — `_emit_signals()` emits `Facet.ADAPTATION` for every tool when `allow_adaptation=True`; `execute_and_verify()` only calls `consume()` on the `verify_mode == "artifact"` path.
**CONCRETE SCENARIO:** A session with `allow_adaptation=True` runs `run_command` (default risk 0.5, over the 0.4 cap). `_emit_signals` publishes an `ADAPTATION` signal to the audit bus. The tool takes the `verify_mode == "exit"` branch, which returns a `Decision` with no `outcome` and no `consume()` call. The weight gate and memory governor never see this action. The audit bus records 3 signals (including ADAPTATION) suggesting learning was considered, but no learning records exist. A reviewer reading the bus would believe the disagreement was armed for this action when it was not.
**WHY IT MATTERS:** This is a faithfulness gap. The claim is "the disagreement fires on a real governed action," but it only fires for artifact-verified tools. For `run_command` — a tool with a higher default risk than `write_file` — the disagreement is structurally impossible because no `GovernedOutcome` is produced. The signal is a promise the wiring doesn't keep.
**SUGGESTED FIX:** Either (a) do not emit `ADAPTATION` for tools whose `verify_mode` won't produce a governed outcome (cleanest — the signal should only fire when consume can follow), or (b) route exit-code-verified commands through `govern()` as well so they produce an outcome. At minimum, document that the learning path is artifact-verification-only.

---

### F-2 / SWALLOWED CONSUME() EXCEPTION SILENTLY DROPS A POTENTIAL INHIBITOR
**SEVERITY:** MEDIUM
**LOCATION:** `collaborator/governance.py` — `execute_and_verify()`, the `except Exception` block around `consume()`.
**CONCRETE SCENARIO:** A future change to the gate (or a subtle type mismatch where `outcome` is a subclass or proxy of `GovernedOutcome`) causes `nominate()` to raise `TypeError`, or `retain()` to raise `HandoffMismatchError`. The `except Exception` block sets `adaptation = memory = None` and `disagreement = False`. The action's summary reports `[write_file ✓ ran, verified]` with no tail — the user sees a clean verified write. A real risk-reject inhibitor that should have been pinned is silently lost, and no error reaches the audit bus.
**WHY IT MATTERS:** The fail-safe direction is correct for the *action report* (the action already ran; you can't un-run it), but fail-open on the *disagreement flag* is the wrong direction for a safety signal. `HandoffMismatchError` specifically exists to prevent silent inhibitor loss (the gate docstring says "silently dropping an inhibitor is the fail-OPEN direction"). The wiring re-introduces exactly that fail-open by catching it. The wiring has a `bus` available but doesn't log the error.
**SUGGESTED FIX:** Keep the no-raise contract for the action report, but surface the error: log to `session.bus` (or at minimum `warnings.warn`), and consider a `learning_error: str | None` field on `Decision` so the summary can say `⟂ LEARNING ERROR — inhibitor may be lost` instead of silently reporting clean.

---

### F-3 / `retention_class='ephemeral'` LABELLED ALONGSIDE "RETAIN AS WARNING" IS MISLEADING
**SEVERITY:** LOW
**LOCATION:** `stage4_live_proof.py` output line: `MEMORY gate  -> inhibitor=True  class='ephemeral'  (RETAIN as warning)`
**CONCRETE SCENARIO:** A reviewer reads the proof output. They see `class='ephemeral'` (half-life 0.02 days per `HALF_LIFE_DAYS`) next to "RETAIN as warning." They infer the warning is short-lived. The next line (`day 0 = 1.0, day 100000 = 1.0 -> NO DECAY`) corrects this, but the label invites the wrong reading. The `ephemeral` class is the directive's retention rung; the inhibitor pin is orthogonal and overrides decay. The proof doesn't explain this orthogonality.
**WHY IT MATTERS:** The claim's honesty depends on the reader understanding that the inhibitor pin, not the retention class, is what makes this permanent. The proof's presentation obscures that. A skeptic could read "ephemeral" as contradicting "never decays."
**SUGGESTED FIX:** Add a parenthetical: `class='ephemeral' (inhibitor pin overrides class decay — see weight below)`.

---

### F-4 / `issue_policy` POSITIONAL ARG ORDER UNVERIFIABLE — `retention_class` MAY NOT BE "semantic"
**SEVERITY:** LOW
**LOCATION:** `collaborator/governance.py` — the `issue_policy("collab-policy", action_id, ..., "semantic", ...)` call.
**CONCRETE SCENARIO:** The wiring passes `"semantic"` as the 8th positional argument, presumably intending it as `retention_class`. The proof output shows `class='ephemeral'`, not `'semantic'`. Without the `issue_policy` signature in the material, I cannot determine whether (a) `"semantic"` maps to `routing_hint` (not `retention_class`), meaning the retention class was never set and the interpreter floored it, or (b) the interpreter deliberately downgraded the class for this unverified outcome (gate behavior, out of scope). If (a), this is an API misuse that silently weakens retention for all actions.
**WHY IT MATTERS:** If the arg is in the wrong position, every action's retention class is unintentionally `ephemeral`, and the "semantic" label is going to an advisory field. The disagreement still works (inhibitor is orthogonal to class), but the retention durability for non-inhibitor records would be wrong.
**SUGGESTED FIX:** Verify against the `issue_policy` signature that position 8 is `retention_class`. If not, use keyword arguments: `issue_policy(..., retention_class="semantic", ...)`.

---

### F-5 / `now_days` HANDLING: `or 0.0` IS REDUNDANT BUT HARMLESS
**SEVERITY:** LOW
**LOCATION:** `collaborator/governance.py` — `now_days = float(getattr(session, "now_days", 0.0) or 0.0)`
**CONCRETE SCENARIO:** `session.now_days` is `0.0` (the default). `0.0 or 0.0` evaluates to `0.0` — correct. If `now_days` were a valid `0.0`, the `or` is a no-op. If it were `None`, `None or 0.0` = `0.0` — correct. The `or` is only needed to handle `None`, but `Session.__init__` already coerces to `float(now_days)`, so `None` would raise there. The `or` is dead code that suggests the author wasn't sure about the type.
**WHY IT MATTERS:** Minor — no behavioral impact, but signals uncertainty about the session contract.
**SUGGESTED FIX:** `now_days = float(session.now_days)` — the Session already guarantees a float.

---

## STEELMAN

The wiring is genuinely faithful to the gate's design: `consume()` is called on the correct bound outcome (same `action_id` as subject and envelope), the `disagreement` flag is a precise three-way read of the two records (`not nominated` + `handoff is not None` + `memory.inhibitor`), and it correctly narrows to RISK_EXCEEDED only (handoff is never set for other rationales). P-01 is intact: risk is host-computed from a fixed table, the model cannot suppress the RISK signal or inflate importance to avoid the inhibitor, and `allow_adaptation=False` produces a clean dormancy with no signal, no consume, and no records. The claim that "the disagreement fires live" is honest — a real file was written, a real governed outcome was produced, and both channels produced real records.

## VERDICT

**MINOR_ISSUES** — The core wiring is faithful, P-01-safe, and honestly claimed; the two medium findings (ADAPTATION signal emitted for unconsumed tool paths, and fail-open exception swallowing in consume) should be addressed before this seam is trusted beyond the write_file artifact path.
