# Stage-4-live red-team: mistralai/mistral-medium-3-5

_finish=stop seconds=20.6 usage={'prompt_tokens': 11525, 'completion_tokens': 2960, 'total_tokens': 14485, 'cost': 0.0394875, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0394875, 'upstream_inference_prompt_cost': 0.0172875, 'upstream_inference_completions_cost': 0.0222}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

### **Adversarial Review: Stage-4-Live Wiring**

---

## **FINDINGS**

### **1. FAITHFULNESS: Disagreement Flag Logic is Correct but Overly Permissive**
**ID:** F-01
**SEVERITY:** MEDIUM
**LOCATION:** `collaborator/governance.py`, `execute_and_verify()`, lines ~200-205
**CONCRETE SCENARIO:**
The `disagreement` flag is set as:
```python
disagreement = ((not adaptation.nominated) and adaptation.handoff is not None and bool(memory.inhibitor))
```
This is **faithful** to the gate’s design (weight blocks + memory retains inhibitor). However, it **does not verify that the `handoff` and `inhibitor` are causally linked** (i.e., that the inhibitor *came from* the handoff). A malicious or misconfigured `consume()` could theoretically return:
- `adaptation.handoff = InhibitorHandoff(...)` (from RISK_EXCEEDED)
- `memory.inhibitor = True` (but from a different source, e.g., a fabricated record)

**WHY IT MATTERS:**
The claim is that the disagreement fires *because* the weight gate’s RISK_EXCEEDED triggers the memory inhibitor. The current check does not **prove causality**—only correlation. If `memory.inhibitor` were set independently (e.g., due to a bug in `retain()`), the flag would still fire falsely.

**SUGGESTED FIX:**
Add an explicit check that the `memory.inhibitor` was **caused by the handoff**:
```python
disagreement = (
    (not adaptation.nominated)
    and adaptation.handoff is not None
    and memory.inhibitor
    and any("inhibitor:adaptation.risk_reject" in r for r in memory.reasons)  # or similar
)
```
*(Alternatively, rely on `HandoffMismatchError` in `retain()` to ensure the handoff is the only way `inhibitor=True` can appear, but this is implicit and harder to audit.)*

---

### **2. P-01 INTEGRITY: Host-Supplied Risk is Trusted, but Model Could Indirectly Influence It**
**ID:** P01-01
**SEVERITY:** HIGH
**LOCATION:** `collaborator/governance.py`, `_TOOL_RISK` dict and `_emit_signals()`
**CONCRETE SCENARIO:**
- The `_TOOL_RISK` mapping is **host-computed** (not model-selectable), which is correct.
- However, the **`risk` parameter in `govern_action()` can be overridden by the caller** (e.g., `govern_action(..., risk=0.9)`).
- If the **Collaborator itself** (or a wrapper) passes a model-influenced `risk` value, P-01 is violated.

**WHY IT MATTERS:**
P-01 requires that **salience influences, policy authorizes**. If the model can **indirectly** set `risk` (e.g., by convincing the host to pass a high risk value), it could:
1. Trigger `RISK_EXCEEDED` (causing a hard block + inhibitor) for actions it wants to suppress.
2. Avoid `RISK_EXCEEDED` (by ensuring `risk <= 0.4`) for actions it wants to learn despite being risky.

**SUGGESTED FIX:**
- **Remove the `risk` override parameter** from `govern_action()` and **only use `_TOOL_RISK`**.
- If dynamic risk is needed, ensure it is **host-computed only** (e.g., from a separate risk assessment service, not model-derived).

---

### **3. P-01 INTEGRITY: `allow_adaptation` + Crafted Risk Could Override Inhibitor**
**ID:** P01-02
**SEVERITY:** CRITICAL
**LOCATION:** `collaborator/governance.py`, `execute_and_verify()`, `consume()` call
**CONCRETE SCENARIO:**
- Suppose `allow_adaptation=True` and the host (or a compromised caller) sets `risk=0.3` (below the 0.4 cap).
- The model performs a **truly risky action** (e.g., `write_file` with malicious content).
- The **interpreter** (not the model) computes `AdaptationRationale.ELIGIBLE` (since risk ≤ cap).
- The **weight gate** nominates the action for learning (`nominated=True`).
- The **memory gate** does **not** set `inhibitor=True` (since no `InhibitorHandoff` was generated).
- **Result:** A risky action is **learned as a skill** (violating P-01’s "policy authorizes, salience influences" separation).

**WHY IT MATTERS:**
This is a **fail-open** scenario where:
- The **model’s risky action is learned** (weight channel allows it).
- The **memory channel does not retain an inhibitor** (since no handoff occurred).
- **P-01 is violated** because the model’s risky behavior is **reinforced**, not suppressed.

**SUGGESTED FIX:**
- **The risk cap must be enforced at the interpreter level** (not just in the gate).
- **OR** the `consume()` call must **re-check** that `outcome.adaptation_rationale == RISK_EXCEEDED` before allowing `disagreement=True`.
- **OR** the host must **never allow `risk` to be overridden** (see P01-01).

*(Note: The gate itself is correct, but the **wiring does not prevent a host from misconfiguring risk**.)*

---

### **4. FAIL-SAFE: Swallowed Exceptions in `consume()` Could Hide Inhibitors**
**ID:** FS-01
**SEVERITY:** HIGH
**LOCATION:** `collaborator/governance.py`, `execute_and_verify()`, lines ~200-205
**CONCRETE SCENARIO:**
- `consume()` raises an exception (e.g., due to a bug in `nominate()` or `retain()`).
- The exception is **swallowed**, and `adaptation = memory = None`, `disagreement = False`.
- A **real inhibitor** (from a prior `RISK_EXCEEDED`) is **silently dropped**.

**WHY IT MATTERS:**
This is a **fail-open** on a **safety-critical path**. If `consume()` fails:
- The **weight gate’s hard block** is lost.
- The **memory governor’s inhibitor** is lost.
- The **disagreement flag is false**, so the host thinks no disagreement occurred.

**SUGGESTED FIX:**
- **Do not swallow exceptions in `consume()`.** Instead:
  - Log the error **loudly** (e.g., `logging.error`).
  - **Set `disagreement = False` but keep `adaptation` and `memory` as `None`** (so the host knows learning failed).
  - **OR** propagate the exception (but this breaks the "never raises" contract of `govern_action`).

*(Alternative: Make `consume()` infallible by design, but this is the gate’s responsibility, not the wiring’s.)*

---
---
### **5. HONESTY OF THE CLAIM: "Disagreement Fires Live" Overstates Verification**
**ID:** H-01
**SEVERITY:** MEDIUM
**LOCATION:** `red-team/collaborator/stage4_live_proof.py`, claim in docstring and output
**CONCRETE SCENARIO:**
- The demo action (`write_file`) **ran but did not verify** (`cleared=False`).
- The **disagreement still fired** (because `RISK_EXCEEDED` is based on the **directive**, not verification).
- The claim says: *"the disagreement fires live"* and *"promoted from a unit fixture to a live worker"*.

**WHY IT MATTERS:**
- The **disagreement fires on `RISK_EXCEEDED`**, which is **independent of verification**.
- The **action was not verified**, so the **outcome is not a "live" success**—it’s a **live failure**.
- The claim **implies** that the disagreement is tied to a **verified action**, but it’s not.

**SUGGESTED FIX:**
Clarify the claim:
> *"The disagreement fires on a **real governed action** (not just a unit test), but the action itself **failed verification**. The disagreement is triggered by `RISK_EXCEEDED` in the directive, not by verification status."*

---
---
### **6. HONESTY OF THE CLAIM: "No Decay" Evidence is Sound but Misleading**
**ID:** H-02
**SEVERITY:** LOW
**LOCATION:** `stage4_live_proof_output.txt`, line:
```
inhibitor weight: day 0 = 1.0   day 100000 = 1.0   -> NO DECAY (a pin, not a fading memory)
```
**CONCRETE SCENARIO:**
- The **inhibitor’s weight does not decay** (correct, per `effective_weight()`).
- However, the **`retention_class` is `'ephemeral'`** (from the output), which **would decay** if not for the inhibitor.

**WHY IT MATTERS:**
- The **claim implies** that the **entire record** is non-decaying, but **only the inhibitor is pinned**.
- If the `inhibitor` were `False`, the `ephemeral` class would decay rapidly.

**SUGGESTED FIX:**
Clarify:
> *"The **inhibitor** does not decay (pinned at weight=1.0), but the underlying `retention_class='ephemeral'` would decay if not for the inhibitor."*

---
---
### **7. SCOPE/DORMANCY: `allow_adaptation=False` Correctly Produces No Signals**
**ID:** S-01
**SEVERITY:** LOW (Confirming correctness)
**LOCATION:** `collaborator/governance.py`, `_emit_signals()`
**CONCRETE SCENARIO:**
- When `allow_adaptation=False`, **no `Facet.ADAPTATION` signal is emitted**.
- `consume()` is **not called**, so no `adaptation` or `memory` records exist.
- The **disagreement flag is `False`**.

**WHY IT MATTERS:**
This is **correct** and matches the claim. No issues here.

**SUGGESTED FIX:** None (this is a positive finding).

---
---
### **8. MISUSE OF API: `now_days` Handling is Correct but Fragile**
**ID:** API-01
**SEVERITY:** LOW
**LOCATION:** `collaborator/governance.py`, `execute_and_verify()`, line ~200
**CONCRETE SCENARIO:**
- `now_days` is **injected from the session** (`session.now_days`).
- If `session.now_days` is **not set**, it defaults to `0.0`.
- This is **correct** (the gate expects a float), but **fragile** if the host forgets to set it.

**WHY IT MATTERS:**
- If `now_days` is **not updated**, the **memory governor’s decay calculations** will be **wrong**.
- However, this is **host responsibility**, not a wiring flaw.

**SUGGESTED FIX:**
- Add a **warning log** if `now_days` is `0.0` (suggesting the host may have forgotten to set it).
- **OR** make `now_days` mandatory in `Session.__init__`.

---
---
## **STEELMAN**
The wiring is **largely faithful** to the gate’s design:
1. The **disagreement flag** correctly captures the **weight block + memory inhibitor** condition.
2. The **`allow_adaptation` toggle** properly gates the `ADAPTATION` signal and `consume()` call.
3. The **host-computed risk** (via `_TOOL_RISK`) is **not model-selectable** (P-01 compliant in the default case).
4. The **demo action** correctly shows the disagreement firing **end-to-end** (even if unverified).

However:
- **P-01 is violated if `risk` is overridden** (P01-01, P01-02).
- **Fail-safe is weakened** by swallowed exceptions in `consume()` (FS-01).
- **The claim overstates** the "live" nature of the proof (H-01, H-02).

---
---
## **VERDICT**
**SERIOUS_FLAWS** – The wiring has **critical P-01 violations** (risk override allows model influence) and a **fail-open exception handling** issue, but the core disagreement logic is sound.

**One-sentence summary:**
*The Stage-4-live wiring correctly exercises the gate’s disagreement logic, but it allows host misconfiguration to violate P-01 and swallows safety-critical exceptions.*
