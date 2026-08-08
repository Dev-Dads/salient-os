# Propose-channel red-team: mistralai/mistral-medium-3-5

_finish=stop seconds=50.8 usage={'prompt_tokens': 11819, 'completion_tokens': 3047, 'total_tokens': 14866, 'cost': 0.040581, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.040581, 'upstream_inference_prompt_cost': 0.0177285, 'upstream_inference_completions_cost': 0.0228525}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

### **Adversarial Review of the PROPOSE-CHANNEL DESIGN**

---

### **FINDING 1: AUTHORITY LEAK VIA STALE CAPABILITY CHECK**
**ID:** AUTH-LEAK-01
**SEVERITY:** CRITICAL
**LOCATION:** `govern_action()` in `collaborator/governance.py` (lines ~120-140) + `approve()` in `collaborator/loop.py` (lines ~200-210)
**CONCRETE SCENARIO:**
1. Host grants `fs.write:project` at session start.
2. Proposer originates a `write_file` proposal while capability is granted → `govern_action` checks capability, returns `HELD`.
3. Host **revokes** `fs.write:project` (e.g., via `session.capabilities = ("fs.read:project",)`).
4. Host approves the stale proposal via `approve()`.
5. `approve()` **reuses the cached `directive`** from the original `govern_action` call, which was minted when the capability *was* granted. The capability gate in `approve()` is **not re-checked**—it only verifies `decision.status == HELD` and then calls `execute_and_verify` with the old `directive`.
6. The action **runs** despite the capability being revoked.

**WHY IT MATTERS:**
The central claim ("surfacing grants nothing; approval re-gates at run time") is **false**. The `directive` is a capability-bearing object (it encodes the policy decision at origination time). If the capability is revoked *after* proposal but *before* approval, the stale `directive` bypasses the gate. This violates P-01: salience (importance/confidence) cannot buy authority, but **time can**.

**SUGGESTED FIX:**
`approve()` must **re-run the capability gate** at approval time. Either:
- Re-call `govern_action` with the current session (and `leash=ACT_THEN_REPORT` to force execution), **or**
- Explicitly re-check `directive.grants_capability(tool.capability)` in `approve()` using the *current* session’s capabilities (not the cached `directive`’s).

---

### **FINDING 2: LEASH PARAMETER CAN WIDEN AUTHORITY (FAIL-OPEN)**
**ID:** LEASH-WIDEN-01
**SEVERITY:** CRITICAL
**LOCATION:** `govern_action()` in `collaborator/governance.py` (lines ~140-150)
**CONCRETE SCENARIO:**
1. Host passes an **invalid `leash`** (e.g., `"act_then_report_please"`) to `govern_action`.
2. The design claims: *"an invalid value falls back to the safe default"* (Section "Per-task adjustable leash").
3. **But the code does not implement this.** In `govern_action`, the `leash` parameter is passed directly to `_leash_for()` (line ~125), which only looks up overrides in `session.leash_overrides`. If the host passes a garbage `leash` (e.g., `"unleashed"`), it is **not validated** against the three allowed values (`ACT_THEN_REPORT`, `PROPOSE_FIRST`, `NOTIFY_ONLY`).
4. The `leash` is then used in the `Decision` and later in `execute_and_verify`. If an invalid `leash` slips through, the `if leash == NOTIFY_ONLY` / `if leash == PROPOSE_FIRST` checks (lines ~145-150) **both fail**, and the code falls through to `execute_and_verify` (line ~152), **running the action unleashed**.

**WHY IT MATTERS:**
This is a **fail-open** path. The `leash` parameter is **host-supplied but unvalidated**, and an invalid value causes an **unleashed run**, violating the claim that `leash` cannot widen authority.

**SUGGESTED FIX:**
Add validation in `govern_action`:
```python
if leash not in (ACT_THEN_REPORT, PROPOSE_FIRST, NOTIFY_ONLY):
    leash = _leash_for(session, tool)  # fall back to session default
```
Or better, **fail closed** (deny the action if `leash` is invalid).

---

### **FINDING 3: CONFIDENCE AS A BACKDOOR (SURFACING INFLUENCES VERIFICATION)**
**ID:** CONF-BACKDOOR-01
**SEVERITY:** HIGH
**LOCATION:** `Proposer.propose()` (not shown in code, but described in design) + `govern_action()` (salience signals)
**CONCRETE SCENARIO:**
1. The design states: *"confidence is model-self-rated and gates surfacing. That is safe because surfacing grants nothing."*
2. However, in `govern_action`, the `importance` parameter (which can be influenced by the proposer’s confidence) is passed to `_emit_signals`, which emits a `SalienceSignal` with `Facet.ATTENTION` and the `importance` value.
3. **Salience signals influence verification depth** (per `salienceos/verifier.py` and the core’s design). Higher `importance` → higher `verification_depth` in the `directive`.
4. A proposal with **artificially high confidence** (e.g., 0.99) could thus **increase verification depth** for the action when it is later approved, even though the confidence was only supposed to gate surfacing.

**WHY IT MATTERS:**
This **indirectly** lets the model influence authority. While the capability gate is still enforced, the **verification depth** (a safety-critical parameter) is being **upgraded by model-influenced confidence**, which violates the spirit of P-01 (salience influences scrutiny, but **model-originated salience should not**).

**SUGGESTED FIX:**
- **Decouple confidence from importance.** The proposer’s confidence should **only** gate surfacing, not feed into `importance` or any salience signal.
- Or, **clamp importance** for originated proposals to a fixed value (e.g., `session.default_importance`), ignoring confidence for governance.

---

### **FINDING 4: PROPOSAL ORIGINATION SIDE EFFECTS (AUDIT POLLUTION)**
**ID:** AUDIT-POLLUTION-01
**SEVERITY:** MEDIUM
**LOCATION:** `Proposer.propose()` (implied) + `govern_action()` (audit bus)
**CONCRETE SCENARIO:**
1. The proposer originates a `write_file` proposal with `leash=PROPOSE_FIRST`.
2. `govern_action` is called, which emits `SalienceSignal`s to the bus (line ~100 in `governance.py`).
3. These signals are **written to the audit trail** (if `session.bus` is persistent).
4. The proposal is **never approved** (host vetoes it).
5. The audit trail now contains a **governed action that never ran**, but is **indistinguishable from a real action** in the bus. A later forensic tool might treat it as a "real" action.

**WHY IT MATTERS:**
The design claims proposals are **inert**, but they **pollute the audit trail** with governed-but-denied actions. This could:
- Mislead auditors into thinking an action ran when it didn’t.
- If the audit trail is used for **learning** (e.g., in Stage-4), a vetoed proposal might **train the model** on a "successful" action that never happened.

**SUGGESTED FIX:**
- **Tag proposals in the audit trail** (e.g., `Facet.PROPOSAL` signal) to distinguish them from real actions.
- **Exclude proposals from learning** (if `allow_adaptation` is enabled, skip `consume()` for `HELD` decisions).

---

### **FINDING 5: FAIL-OPEN ON MALFORMED PROPOSAL**
**ID:** FAIL-OPEN-01
**SEVERITY:** MEDIUM
**LOCATION:** `Proposer.propose()` (implied) + `parse_message()` in `collaborator/toolcall.py`
**CONCRETE SCENARIO:**
1. The proposer generates a **malformed tool call** (e.g., `{"name": "write_file", "args": "not_a_dict"}`).
2. `parse_message()` returns this as `ambiguous` (not `intents`).
3. The design states: *"Proposer/model error, unparseable suggestion, or 'nothing to propose' ⇒ no proposal"*.
4. **But the code path is unclear.** If `Proposer.propose()` receives an `ambiguous` parse result, does it:
   - Drop the proposal entirely (correct)?
   - Surface it as a "malformed proposal" (incorrect, as it might leak partial data)?
5. If the latter, a **partially parsed malicious payload** (e.g., `{"name": "run_command", "args": {"command": "rm -rf /"}}`) could be surfaced as "ambiguous" but still **revealed to the host**, who might copy-paste it.

**WHY IT MATTERS:**
This is a **fail-open** path where a malformed proposal **leaks unsafe content** to the host, who might execute it manually.

**SUGGESTED FIX:**
- **Drop all ambiguous proposals silently** (no surfacing).
- **Log them separately** (for debugging, not user-facing).

---

### **FINDING 6: LEASH DEFAULTING DOES NOT PRESERVE TODAY’S BEHAVIOR**
**ID:** LEASH-DEFAULT-01
**SEVERITY:** LOW
**LOCATION:** `_leash_for()` in `collaborator/governance.py` (line ~90)
**CONCRETE SCENARIO:**
1. Today (Step-0), `govern_action` uses `_leash_for(session, tool)` to determine the leash.
2. The new design adds an **optional `leash` parameter** to `govern_action`.
3. If the host **does not pass `leash`**, the function uses `_leash_for(session, tool)` as before.
4. **But** if the host passes `leash=None`, the code **falls back to `_leash_for`**, which is correct.
5. However, the design does not specify whether `leash=None` is allowed or if it should be treated as invalid. If a host accidentally passes `leash=None`, it **silently defaults**, which could be confusing.

**WHY IT MATTERS:**
This is a **minor inconsistency** in the API. The claim that "defaulting preserves today’s behaviour" is **technically true**, but the edge case of `leash=None` is not explicitly handled.

**SUGGESTED FIX:**
Explicitly document that `leash=None` falls back to the session default. Or, **reject `None`** and require the host to omit the parameter if they want the default.

---

### **FINDING 7: HONESTY OF THE CLAIM ("GROWS WITH YOU, SAFELY")**
**ID:** CLAIM-HONESTY-01
**SEVERITY:** HIGH
**LOCATION:** Central claim in `01-propose-design.md` ("surfacing grants nothing")
**CONCRETE SCENARIO:**
The claim is **dishonest by omission** because:
1. **Stale capability check (Finding 1)** means approval can bypass the gate.
2. **Confidence influences verification depth (Finding 3)** means model-originated salience **does** affect scrutiny.
3. **Audit pollution (Finding 4)** means proposals have **side effects** (audit trail writes).

**WHY IT MATTERS:**
The claim **"surfacing grants nothing"** is **literally false** in the presence of Finding 1 (approval can run without re-checking capability). The claim **"grows with you, safely"** is **misleading** because confidence can indirectly upgrade verification depth.

**SUGGESTED FIX:**
- Revise the claim to: *"Surfacing grants no **direct** authority, but approval must re-gate capability at run time. Confidence only gates surfacing and does not influence verification depth."*
- Explicitly state that **proposals do not mutate state but may write to the audit trail**.

---

---

### **STEELMAN**
The design is **mostly sound** in its core structure:
- Proposals are **held by default** (`leash=PROPOSE_FIRST`).
- The **capability gate is enforced at origination** (for proposals).
- The **approve() path reuses the same `execute_and_verify`** as Step-0.
- **Fail-closed** is the default for most error paths.

However, **two critical flaws** (stale capability check, leash validation) **break P-01**, and **confidence leaking into verification depth** is a **serious design smell**. The audit pollution is a **minor but real** side effect.

---

### **VERDICT**
**SERIOUS_FLAWS**
The design **fails to preserve P-01** due to the stale capability check in `approve()` and the unvalidated `leash` parameter, and it **overclaims inertness** by allowing confidence to influence verification depth. These must be fixed before implementation.
