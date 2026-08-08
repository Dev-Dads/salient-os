# Stage-4-live red-team: x-ai/grok-4.5

_finish=stop seconds=166.4 usage={'prompt_tokens': 11693, 'completion_tokens': 7696, 'total_tokens': 19389, 'cost': 0.0693444, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0693444, 'upstream_inference_prompt_cost': 0.0231684, 'upstream_inference_completions_cost': 0.046176}, 'completion_tokens_details': {'reasoning_tokens': 5564, 'image_tokens': 0, 'audio_tokens': 0}}_

# Stage-4-live wiring — adversarial review

Gate internals treated as given. Findings are wiring-only.

---

## FINDING F-1
**TITLE:** `consume()` errors are swallowed → inhibitor/disagreement fail-open  
**SEVERITY:** HIGH  
**LOCATION:** `collaborator/governance.py` — `execute_and_verify`, Stage-4-live `try/except` around `consume`

**CONCRETE SCENARIO:**  
`retain()` raises `HandoffMismatchError` (or `TypeError` on a bad `now_days`, e.g. host clock `now_days=-1` / NaN). The `except Exception` branch sets `adaptation = memory = None`, `disagreement = False`. The Decision looks like “adaptation off / nothing to see,” while the directive still carries `RISK_EXCEEDED`. Downstream anything that persists only `Decision.memory` never pins the warning.

**WHY IT MATTERS:**  
The gate deliberately **raises** on mis-attributed handoff because “silently dropping an inhibitor is the fail-OPEN direction.” The host undoes that by catching-all and zeroing records. Action report stays up (good), but the Stage-4 safety claim (permanent warning) is fail-open and indistinguishable from dormancy.

**SUGGESTED FIX:**  
Still never block the action status; surface a dedicated field, e.g. `learning_error=str(exc)` / `learning_status="error"`, leave `disagreement` unset only when consume did not run cleanly, and audit-log the exception. Do not equate “consume failed” with “no inhibitor.”

---

## FINDING F-2
**TITLE:** Learning channels only wired on `verify_mode == "artifact"` — natural over-cap tool never `consume`s  
**SEVERITY:** HIGH  
**LOCATION:** `collaborator/governance.py` — `execute_and_verify`: `consume` only on the artifact/`govern()` path; `verify_mode in {"none","exit"}` returns earlier with no outcome/consume  
**ALSO:** `_TOOL_RISK`: `run_command: 0.5` (> `0.4` cap), `write_file: 0.1` (< cap)

**CONCRETE SCENARIO:**  
Session `allow_adaptation=True`, model runs `run_command` (default risk 0.5 → interpreter can stamp `RISK_EXCEEDED`). Path uses supervised exit clearance, returns `RAN`/`FAILED` **without** `govern()` → **no** `consume` → `adaptation/memory` stay default empty, `disagreement=False`. No weight block record, no inhibitor.  
Conversely, default `write_file` (risk 0.1) never hits `RISK_EXCEEDED` unless the caller overrides `risk`.

**WHY IT MATTERS:**  
The only stock tool risk above the adaptation cap never enters the two-channel seam. Live disagreement in tests/proof is manufactured by `risk=0.9` on an artifact write, not by the host risk table on a normal path. Wiring does not exercise the gate for the tool class that would naturally disagree.

**SUGGESTED FIX:**  
Either (a) build a real `GovernedOutcome` (or equivalent bound outcome) for exit-mode tools and `consume` it, or (b) narrow the claim to “artifact-governed mutating actions” and put naturally over-cap risk on an artifact tool (or host risk model that actually scores writes). Don’t leave `run_command` at 0.5 as a silent dead end for learning safety.

---

## FINDING F-3
**TITLE:** `risk=` (and `importance=`) are open call parameters — host table is optional, not enforced  
**SEVERITY:** MEDIUM  
**LOCATION:** `collaborator/governance.py` — `govern_action`:  
`rk = _TOOL_RISK.get(tool.name, 0.3) if risk is None else risk`  
Comments claim risk is “Not model-selectable.”

**CONCRETE SCENARIO (under-cap → learn):**  
`allow_adaptation=True`, caller passes `risk=0.0` on a sensitive `write_file` that still clears at shallow depth → no `RISK_EXCEEDED` → if `adaptation_allowed`, weight **nominates** (opposite of inhibitor path).  
**CONCRETE SCENARIO (over-cap demo):**  
Proof/tests pass `risk=0.9` to force disagreement — fine for a host harness, but any agent-loop that forwards model- or content-chosen risk makes influence model-selectable.

**WHY IT MATTERS:**  
P-01: salience/influence must not buy authority; here risk also gates **whether learning is blocked vs nominated**. Understated risk + successful verify is “nominate a risky write for learning.” Code comments assert host-only risk; the API does not.

**SUGGESTED FIX:**  
Compute risk only inside the host (tool table and/or host classifier); remove `risk=` from the public `govern_action` surface or accept it only from a trusted host adapter, not the model loop. Keep overrides behind a test-only seam if needed.

---

## FINDING F-4
**TITLE:** Claim/proof slightly oversell “live risky action” and “ephemeral = warning”  
**SEVERITY:** LOW  
**LOCATION:** `red-team/collaborator/stage4_live_proof.py` (+ output); `Decision` docstring in `governance.py`; tests’ `risk=0.9` injection

**CONCRETE SCENARIO:**  
Proof prints `class='ephemeral' (RETAIN as warning)` while no-decay is solely from `memory.inhibitor` (gate: inhibitors skip half-life). A reader can think the ladder class itself is the pin. Also “real risky+important action” is a normal write with **injected** risk 0.9; file write + unbound learning path are real, inherent tool risk is not.

**WHY IT MATTERS:**  
Does not fake the gate (rationale, handoff, inhibitor, `w0==wfar` are real). It does pad the narrative past what the wiring+defaults guarantee in production.

**SUGGESTED FIX:**  
Proof copy: “host-asserted risk=0.9 (override); retention_class is ladder rung; **pin = inhibitor flag** (class may still read `ephemeral`).” Claim: disagreement on a **bound** `GovernedOutcome` with recorded `RISK_EXCEEDED`, including uncleared — which you already show via `verified: False`.

---

## FINDING F-5
**TITLE:** `disagreement` predicate is faithful (redundant but not wrong); no re-derive of risk  
**SEVERITY:** LOW (positive / residual note)  
**LOCATION:** `collaborator/governance.py` —  
`disagreement = (not adaptation.nominated) and adaptation.handoff is not None and bool(memory.inhibitor)`

**CONCRETE SCENARIO:**  
Happy path `RISK_EXCEEDED`: `nominate` → not nominated + handoff; `retain` → `inhibitor=True` → flag True. Non-risk refusals (e.g. unverified novelty): no handoff → flag False. Dormant `allow_adaptation=False`: no signal, no consume → all None/False (test-covered).

**WHY IT MATTERS:**  
Does not report a disagreement that the channels didn’t produce; does not re-check raw salience or `verdict.status`. Redundancy (`handoff` iff risk-reject; `inhibitor` iff handoff accepted) is fine. Residual: any consume failure clears the flag (see F-1), so “false negative disagreement” is the error-path issue, not the predicate algebra.

**SUGGESTED FIX:**  
Optional: `disagreement = memory is not None and memory.inhibitor and adaptation is not None and adaptation.handoff is not None` (nominated is implied). Prefer fixing F-1 over micro-tuning the boolean.

---

## FINDING F-6
**TITLE:** Dormancy path is correct; no inhibitor leak with adaptation off  
**SEVERITY:** LOW (positive)  
**LOCATION:** `_emit_signals` (ADAPTATION only if `allow_adaptation`); `execute_and_verify` consume gated the same way; `Session.allow_adaptation` default `False`; `DormantOtherwise` tests / proof CONTRAST 2

**CONCRETE SCENARIO:**  
Default session, `risk=0.9` write: policy gets `allow_adaptation=False`, no ADAPTATION facet, no `consume`, `adaptation is memory is None`, `disagreement is False`. Low-risk write with adaptation on: no inhibitor (test).

**WHY IT MATTERS:**  
Host-dormant claim holds on the wiring you shipped. No extra finding.

---

## STEELMAN

The integration does the right core thing: host `allow_adaptation` drives an ADAPTATION influence signal and policy bit; after a real `govern()` outcome it calls `consume(outcome, now_days)` once and records both channel results without re-implementing nomination/retention. The disagreement flag matches the designed RISK_EXCEEDED shape (block + handoff + inhibitor), dormancy is clean, the proof honestly shows `verified: False`, and P-01 capability remains policy-gated with risk/importance as signals only on the demonstrated path.

---

## VERDICT

**MINOR_ISSUES** — Happy-path wiring is faithful to the gate and does not fake disagreement or punch a capability hole; fix the consume fail-open (F-1) and the artifact-only / synthetic-risk coverage gap (F-2–F-3) before treating “disagreement fires live in the worker” as production-complete.
