# PolicyCaps red-team: mistralai/mistral-medium-3-5

_finish=stop seconds=61.6 usage={'prompt_tokens': 13115, 'completion_tokens': 3885, 'total_tokens': 17000, 'cost': 0.04881, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.04881, 'upstream_inference_prompt_cost': 0.0196725, 'upstream_inference_completions_cost': 0.0291375}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

### **Adversarial Review: Signed PolicyCaps Design**

---

## **Findings**

### **1. BYPASS / INCOMPLETE MEDIATION**
#### **ID: F1-BYPASS-LEGACY / CRITICAL**
**LOCATION:** `collaborator/governance.py:govern_action()`, `collaborator/session.py`
**SCENARIO:**
- An attacker (or misconfigured host) omits `SignedPolicyCaps` from the session.
- The design states: *"no PolicyCaps => legacy behaviour"*.
- In `govern_action()`, the capability check (`directive.grants_capability`) still sources from `session.capabilities` (mutable config), **not** the verified grant.
- Thus, an attacker can **bypass the entire hardening layer** by simply not attaching a grant.

**WHY IT MATTERS:**
- The **primary security claim** ("authority is bound to a signed grant") is **completely bypassable** if the grant is optional.
- This is a **critical incomplete mediation** flaw: the legacy path is a **parallel authority source** that is not gated by the new layer.

**SUGGESTED FIX:**
- **Remove the legacy path.** If `SignedPolicyCaps` is present, **all** authority must come from the verified grant. If absent, **fail closed** (deny all actions).
- Alternatively, **document this as an explicit opt-in hardening** (not a security boundary) and **warn** that sessions without grants are **not protected** by the new layer.

---

#### **ID: F2-BYPASS-REAUTH / HIGH**
**LOCATION:** `collaborator/governance.py:reauthorized_or_denied()`
**SCENARIO:**
- A held action (e.g., `PROPOSE_FIRST`) is re-gated at approval time via `reauthorized_or_denied()`.
- This function **re-derives authority from `session.capabilities`** (mutable config), **not** the verified grant.
- If an attacker mutates `session.capabilities` between the original `govern_action()` and the re-gate, the action **could slip through** with widened authority.

**WHY IT MATTERS:**
- The **re-gate path is not mediated by the verified grant**, meaning the **TOCTOU (Time-of-Check-to-Time-of-Use) window** is still open.
- This **violates the "no widening" property** for held actions.

**SUGGESTED FIX:**
- Modify `reauthorized_or_denied()` to **source capabilities from the verified grant** (if present) instead of `session.capabilities`.
- If no grant is present, **fail closed** (deny the re-gate).

---

#### **ID: F3-BYPASS-LEASH / HIGH**
**LOCATION:** `collaborator/governance.py:_leash_for()`, `collaborator/governance.py:execute_and_verify()`
**SCENARIO:**
- The leash cap is **only applied in `execute_and_verify()`** (for `ACT_THEN_REPORT`).
- However, `govern_action()` **does not enforce the leash cap** when resolving the leash for `PROPOSE_FIRST` or `NOTIFY_ONLY`.
- An attacker could **set `session.leash_overrides` to `ACT_THEN_REPORT`** for a tool capped at `PROPOSE_FIRST`, and the action would **run immediately** (bypassing the cap).

**WHY IT MATTERS:**
- The **leash cap is not enforced on all paths** (only on `ACT_THEN_REPORT`).
- This **violates the "no loosening" property** for non-execute paths.

**SUGGESTED FIX:**
- **Apply the leash cap in `_resolve_leash()`** (or wherever the leash is resolved) **before** any decision is made.
- Ensure **all paths** (including `PROPOSE_FIRST` and `NOTIFY_ONLY`) **respect the cap**.

---

### **2. WIDEN**
#### **ID: F4-WIDEN-LEASH-ORDERING / MEDIUM**
**LOCATION:** `03-policycaps-design.md` (Leash ordering section)
**SCENARIO:**
- The design defines:
  ```python
  act_then_report (0, loosest) < propose_first (1) < notify_only (2, strictest)
  effective_rank = max(host_rank, cap_rank)  # cap = "no looser than this"
  ```
- However, **`max()` is incorrect** for this ordering.
  - If `host_rank = 0` (`ACT_THEN_REPORT`) and `cap_rank = 1` (`PROPOSE_FIRST`), `max(0, 1) = 1` → `PROPOSE_FIRST` (correct).
  - But if `host_rank = 1` (`PROPOSE_FIRST`) and `cap_rank = 0` (`ACT_THEN_REPORT`), `max(1, 0) = 1` → `PROPOSE_FIRST` (correct).
  - **Wait, this seems correct.**
  - **BUT:** The **description says "cap = no looser than this"**, meaning the **cap is a floor on strictness** (higher rank = stricter).
  - The **`max()` operation** actually **enforces the stricter of the two**, which is correct.
  - **However**, the **ranking is counterintuitive** (0 = loosest, 2 = strictest). If someone misorders them (e.g., swaps `PROPOSE_FIRST` and `NOTIFY_ONLY`), the logic **fails silently**.

**WHY IT MATTERS:**
- **Off-by-one or misordering in the rank values could silently allow loosening.**
- The **design does not enforce that the ranks are assigned correctly** (e.g., via an enum or validation).

**SUGGESTED FIX:**
- **Use an enum** for leash levels with **explicit ranks** to prevent misordering.
- **Add validation** that `leash_cap` ranks are **≥ the tool’s default leash rank** (to prevent accidental weakening).

---

#### **ID: F5-WIDEN-CAPS-MUTATION / MEDIUM**
**LOCATION:** `collaborator/session.py:Session.__init__()`
**SCENARIO:**
- The design claims: *"mutating `session.capabilities` cannot add a capability (the verified caps are authoritative)"*.
- However, **`session.capabilities` is still mutable** (it’s a `tuple`, but the **`Session` object allows reassignment**).
- If an attacker **reassigns `session.capabilities` after verification**, the **next action** could use the **widened set** (if the grant is not re-verified per-action).

**WHY IT MATTERS:**
- The **design assumes that `session.capabilities` is not used after verification**, but **does not enforce this**.
- If **any path** (e.g., a bug in `reauthorized_or_denied()`) **reads `session.capabilities` instead of the verified grant**, the **bypass is possible**.

**SUGGESTED FIX:**
- **Make `session.capabilities` immutable** (e.g., a `@property` that always returns the verified grant’s caps if present).
- **Remove `session.capabilities` entirely** and **only use the verified grant** when present.

---

### **3. FAIL-CLOSED**
#### **ID: F6-FAIL-OPEN-VERIFY / CRITICAL**
**LOCATION:** `collaborator/policycaps.py:verify()` (not yet implemented, but implied in design)
**SCENARIO:**
- The design states: *"Invalid/absent-when-required ⇒ fail closed: zero capabilities, strictest leash."*
- However, **the implementation of `verify()` is not shown**, and **no explicit handling of exceptions is defined**.
- If `verify()` **raises an exception** (e.g., due to a malformed signature), and the caller **does not catch it**, the **action could proceed with the mutable `session.capabilities`** (fail-open).

**WHY IT MATTERS:**
- **A single unhandled exception in `verify()` could bypass the entire security layer.**
- The **design does not specify how `verify()`’s failures are handled** in `govern_action()`.

**SUGGESTED FIX:**
- **Explicitly define `verify()` to return `(PolicyCaps | None, error: str | None)`** (never raise).
- **In `govern_action()`, if `verify()` fails, immediately return `DENIED` with zero caps and strictest leash.**

---

#### **ID: F7-FAIL-OPEN-MISSING-KEY / HIGH**
**LOCATION:** `03-policycaps-design.md` (Tamper-evident section)
**SCENARIO:**
- The design states: *"Absent key when a grant is present → fail closed."*
- However, **if `caps_key` is `None` or missing**, but a `SignedPolicyCaps` is present, **what happens?**
- If the **verification is skipped** (e.g., due to a missing key), the **grant is silently ignored**, and the **legacy path is used** (fail-open).

**WHY IT MATTERS:**
- **A missing key should not silently fall back to mutable config.**
- This **violates the fail-closed property**.

**SUGGESTED FIX:**
- **If a grant is present but the key is missing, fail closed** (deny all actions).
- **Log a warning** to alert the host of the misconfiguration.

---

#### **ID: F8-FAIL-OPEN-MALFORMED-CAPS / MEDIUM**
**LOCATION:** `collaborator/policycaps.py:verify()` (implied)
**SCENARIO:**
- If the `PolicyCaps` JSON is **malformed** (e.g., `capabilities` is not a tuple, `leash_caps` is missing a tool), **what happens?**
- If `verify()` **returns an error but the caller does not enforce zero caps**, the **action could proceed with partial or default authority**.

**WHY IT MATTERS:**
- **Malformed grants must not silently degrade to a weaker state.**

**SUGGESTED FIX:**
- **Define `verify()` to return `None` (invalid) for any malformed input.**
- **In `govern_action()`, treat `None` as fail-closed (zero caps, strictest leash).**

---

### **4. HONESTY OF THE CLAIM**
#### **ID: F9-OVERSOLD-BOUNDARY / HIGH**
**LOCATION:** `03-policycaps-design.md` (Honest scope section)
**SCENARIO:**
- The design **correctly states** that symmetric HMAC in a single trust domain **does not protect against an in-process re-signer**.
- However, the **document title ("Signed PolicyCaps") and the claim ("binding authority to a signed grant")** **implies a stronger guarantee** than what is delivered.
- A reader might **assume** that the signature **prevents tampering by any component**, not just **non-crypto mutations**.

**WHY IT MATTERS:**
- **The scoping is honest, but the presentation is misleading.**
- **Users may over-trust the layer** and assume it provides **asymmetric-level guarantees**.

**SUGGESTED FIX:**
- **Rename the feature** to **"Verified PolicyCaps"** (not "Signed") to **avoid implying cryptographic isolation**.
- **Add a prominent warning** in the doc: *"This is **not** a hard boundary against a compromised in-process component. It only detects non-crypto tampering."*

---

#### **ID: F10-CANONICALIZATION-COLLISION / MEDIUM**
**LOCATION:** `03-policycaps-design.md` (PolicyCaps definition)
**SCENARIO:**
- The design **does not specify how `canonical(caps)` is implemented**.
- If the canonicalization is **not collision-resistant** (e.g., JSON with unsorted keys, unstable types), **two different `PolicyCaps` could produce the same signature**.
- Example:
  ```python
  caps1 = {"capabilities": ("a", "b"), "leash_caps": {...}}
  caps2 = {"capabilities": ("b", "a"), "leash_caps": {...}}  # Same semantic meaning, different order
  ```
  - If `canonical()` does not **sort the tuple**, these **sign differently**.
  - If `canonical()` **does sort**, but **does not validate types**, an attacker could **swap a tuple for a list** and get the same signature.

**WHY IT MATTERS:**
- **Signature collisions could allow an attacker to forge a grant** with different (but semantically equivalent) caps.

**SUGGESTED FIX:**
- **Define `canonical()` explicitly**:
  - Sort all dict keys.
  - Convert all sequences (tuples/lists) to **sorted tuples**.
  - Enforce **strict types** (e.g., reject lists where tuples are expected).
- **Add a test** that two semantically equivalent but syntactically different `PolicyCaps` **do not collide**.

---

### **5. REPLAY / BINDING**
#### **ID: F11-REPLAY-SUBJECT / MEDIUM**
**LOCATION:** `03-policycaps-design.md` (PolicyCaps definition)
**SCENARIO:**
- The design states: *"`caps.subject` == this session's workspace — no replay onto another session."*
- However, **`workspace` is a `Path` object**, and **path comparison is not always sound**:
  - **Symlinks**: `/real/path` vs `/symlink/path` could resolve to the same directory but compare as unequal.
  - **Relative vs. absolute**: `./workspace` vs `/abs/workspace` could be the same but compare as unequal.
  - **Case sensitivity**: On case-insensitive filesystems (e.g., macOS), `Workspace` vs `workspace` could be the same.

**WHY IT MATTERS:**
- **Replay attacks could succeed if the subject binding is not normalized.**

**SUGGESTED FIX:**
- **Normalize `subject` and `session.workspace`** before comparison:
  - Resolve symlinks (`Path.resolve()`).
  - Use absolute paths.
  - Normalize case (if on a case-insensitive filesystem).

---

#### **ID: F12-NO-NONCE-EXPIRY / LOW**
**LOCATION:** `03-policycaps-design.md` (PolicyCaps definition)
**SCENARIO:**
- The design **does not include a nonce or expiry** in `PolicyCaps`.
- If a grant is **stolen (e.g., via logs or a backup)**, it could be **reused indefinitely** (even if the key is rotated).

**WHY IT MATTERS:**
- **Lack of expiry increases the window for replay attacks.**

**SUGGESTED FIX:**
- **Add an optional `expiry` field** (timestamp) to `PolicyCaps`.
- **Reject grants where `expiry < now`**.
- **Document that v0 defers this**, but **future versions should include it**.

---

### **6. BACKWARD COMPAT AS A HOLE**
#### **ID: F13-LEGACY-BYPASS / CRITICAL**
**LOCATION:** `03-policycaps-design.md` (Backward compatible section)
**SCENARIO:**
- The design **explicitly allows** sessions without `PolicyCaps` to **behave as today** (mutable config).
- This means **an attacker can opt out of the hardening layer** by simply **not attaching a grant**.

**WHY IT MATTERS:**
- **The entire security layer is optional**, meaning **it does not provide a security boundary**—only **tamper-detection for opt-in sessions**.
- This **contradicts the claim** that the design **"closes the gap"** of mutable authority.

**SUGGESTED FIX:**
- **If the goal is hardening, make `PolicyCaps` mandatory.**
- **If opt-in is required, document it as "tamper-detection for willing participants" (not a security boundary).**

---

---

## **Steelman**
The design **honestly scopes** the threat model to a **single trust domain** and **does not oversell asymmetric guarantees**. It **correctly identifies** that symmetric HMAC **only detects non-crypto tampering** and **fails closed on verification errors**. The **leash capping logic** is **mathematically sound** (if implemented correctly), and the **backward-compat path** is **explicitly documented**.

However, **the optional nature of `PolicyCaps` and the incomplete mediation in `reauthorized_or_denied()` and leash resolution** **severely weaken the security claims**. The **fail-closed properties are not fully enforced** in all error paths, and **canonicalization risks** could allow **signature collisions**.

---

## **Verdict**
**SERIOUS_FLAWS** – The design has **critical bypasses (legacy path, re-gate path, leash cap enforcement)** and **incomplete fail-closed handling**, but the **honesty of scope is correct** and the **core logic (when fully mediated) is sound**.

**One-sentence summary:** *The hardening layer is opt-in and incomplete, with critical mediation gaps that allow bypasses, but the threat model and honesty are otherwise well-scoped.*
