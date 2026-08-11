# ungrantable-namespace CODE panel: qwen/qwen3-max

_finish=stop seconds=56.4 cost=$0.0197 usage={'prompt_tokens': 12223, 'completion_tokens': 2598, 'total_tokens': 14821, 'cost': 0.01966614, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.01966614, 'upstream_inference_prompt_cost': 0.00953394, 'upstream_inference_completions_cost': 0.0101322}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

**FINDINGS**

---

**ID**: F-01  
**TITLE**: Casefold edge case with Turkish dotless I may bypass prefix check  
**SEVERITY**: HIGH  
**LOCATION**: `salienceos/interpreter/policy.py:32` (`is_ungrantable_capability`)  
**CONCRETE INPUT**: `"İffense:evil.com"` (note capital dotted İ, U+0130)  
**WHY IT BREAKS A GUARANTEE**: The function uses Python’s `str.casefold()`, which in the Turkish locale maps `'I'` → `'ı'` (dotless i, U+0131), **not** `'i'`. However, even in non-Turkish locales, `casefold()` is Unicode-aware and **does not normalize all visually similar characters**. But more critically: **`"İ".casefold()` is `"i\u0307"` (i + combining dot above)**, which does **not** start with `"offense:"`. So this input **does NOT bypass**.

Wait — let's test actual problematic inputs:

The real risk is a string that **casefolds to something starting with "offense:"**, e.g., if there were full-width or compatibility variants. But `"ｏｆｆｅｎｓｅ："` (full-width) casefolds to itself (it’s already lowercase), and `"ｏｆｆｅｎｓｅ：".startswith("offense:")` is False because the characters are different.

However, consider: **`"OFFENSE:".casefold()` → `"offense:"`** — which **is correctly caught**.

Now consider: **`"offense\u200b:"`** (zero-width space after 'e') — this does **not** start with `"offense:"` because of the ZWSP. So it’s **not** caught — but also **not** an `offense:` capability by design. The reservation is **prefix-based on exact normalized form**, not semantic.

But what about **`"offensE:"`**? `.casefold()` → `"offense:"` → caught.

✅ **Conclusion**: The use of `casefold()` is **correct and sufficient** for case insensitivity. No known Unicode sequence casefolds to a string starting with `"offense:"` unless it is intended to be one. The test suite explicitly checks mixed case (`"Offense:x"`, `"OFFENSE:..."`) and they pass.

➡️ **NO BYPASS**. This is **not a finding**.

---

**ID**: F-02  
**TITLE**: Prefix boundary confusion: `"offense"` (no colon) or `"offensexyz:"`  
**SEVERITY**: LOW  
**LOCATION**: `salienceos/interpreter/policy.py:32`  
**CONCRETE INPUT**: `"offense"` or `"offens3:"` or `"xoffense:y"`  
**WHY IT BREAKS A GUARANTEE**: These do **not** start with `"offense:"`, so `is_ungrantable_capability` returns `False`.  
**IS THIS A PROBLEM?** No — the reservation is **explicitly for the `offense:` namespace**, i.e., the prefix **must end with colon**. This is by design (see ADR 0004: “reserved prefix set”). Capabilities like `"offense"` (no colon) are **not** in the prohibited class. The system allows them — but none exist, and they are **not offense-semantic** by convention.  
**ANOTHER LAYER?** N/A — this is **by design**.  
**FIX**: None needed. Documented behavior.

➡️ **NOT A FINDING** (documented non-goal).

---

**ID**: F-03  
**TITLE**: Non-string input causes `is_ungrantable_capability` to return `False`, but could break downstream if passed to `grants_capability`  
**SEVERITY**: LOW  
**LOCATION**: `salienceos/interpreter/policy.py:32`  
**CONCRETE INPUT**: `capability = None`, `123`, `b"offense:x"`  
**WHY IT BREAKS A GUARANTEE**: The function returns `False` for non-strings, as documented (“a non-string is not a capability string”).  
**DOES ANYTHING BREAK?** In `Directive.grants_capability`, the argument is typed as `str`, but Python doesn’t enforce it. If a consumer passes `None`, then:
```python
isinstance(None, str) → False → is_ungrantable_capability(None) → False
then: None in self.allowed_capabilities → always False (safe)
```
Similarly, `b"offense:x"` → not a str → not ungrantable → then `b"..." in ("offense:x",)` → False.

So **no false grant**, and **no crash**. The function is **total** and **fail-closed**.

Test suite confirms: `is_ungrantable_capability(None) == False`.

➡️ **NOT A FINDING** — robust and correct.

---

**ID**: F-04  
**TITLE**: `issue_policy` strips `offense:` caps before signing; but what if a consumer reads `allowed_capabilities` directly instead of using `grants_capability`?  
**SEVERITY**: CRITICAL (if true)  
**LOCATION**: Any consumer of `Directive.allowed_capabilities`  
**CONCRETE INPUT**: A hand-built `Directive` with `allowed_capabilities=("offense:evil.com",)`  
**WHY IT BREAKS A GUARANTEE**: If any code checks `"offense:evil.com" in d.allowed_capabilities` **instead of** `d.grants_capability("offense:evil.com")`, it would **incorrectly infer authority**.  
**ANALYSIS**: The context states: **“`grants_capability()` is the only capability accessor, so a consumer cannot infer authority from the scalar knobs.”** And the test `test_grants_capability_refuses_offense_even_if_present_in_allowed` **explicitly verifies** that even when present, it’s not granted.  
Search the codebase (implied by context): all capability checks **must** go through `grants_capability`. The design enforces this.  
**IS THERE A DIRECT READ?** The provided materials show **no such consumer**. The `collaborator/policycaps.py` uses `granted_capabilities` from `PolicyCaps`, but those are **stripped at issue_policy**, and mint rejects them anyway.  
In `directive.py`, the field is public, but the **contract** is clear: use `grants_capability`. The test suite validates the structural guarantee.

➡️ **NO EVIDENCE OF BYPASS**. The dual-layer defense (strip + unconditional refuse) ensures safety even if someone misreads `allowed_capabilities`. But the **load-bearing guarantee is in `grants_capability`**, which is **unconditional**.

Thus, **even if a consumer reads `allowed_capabilities`**, they **should not treat presence as authority** — and the system does not. No finding.

---

**ID**: F-05  
**TITLE**: Signature coherence — does stripping in `issue_policy` cause a mismatch between signed payload and verification?  
**SEVERITY**: MEDIUM  
**LOCATION**: `salienceos/interpreter/policy.py:103` (`issue_policy`)  
**CONCRETE INPUT**: `granted_capabilities=("fs.read:ok", "offense:bad")`  
**WHAT HAPPENS**: Stripped to `("fs.read:ok",)` before building `PolicyCaps`, then signed.  
**VERIFICATION**: `verify_policy` checks that `granted_capabilities` is a tuple of strings — which it is. It **does not** re-apply the strip. But that’s fine — the signed payload **never contained** the offense cap.  
**COHERENCE**: Yes — what is signed is exactly what is verified. The strip happens **before** signing, so the envelope is clean.  
**TEST CONFIRMATION**: `test_issue_policy_strips_the_prohibited_namespace` shows the interpreted directive has only the legit cap.  
**NO ISSUE**.

➡️ **NOT A FINDING**.

---

**ID**: F-06  
**TITLE**: Legitimate capability broken due to prefix collision  
**SEVERITY**: HIGH  
**LOCATION**: All  
**CONCRETE INPUT**: `"offense"` (no colon), `"shell.offense:..."`, `"net.offense.get:x"`  
**ANALYSIS**: None of these start with `"offense:"`, so they are **not stripped or refused**. Existing capabilities (`fs.*`, `shell.exec`, etc.) are unaffected. Test suite includes `"offense"` (no colon) and confirms it’s **not** ungrantable.  
**NO COLLISION**.

➡️ **NOT A FINDING**.

---

**ID**: F-07  
**TITLE**: `mint` converts capabilities to `str(c)` — what if `c` is a malicious object with `__str__` that returns `"offense:x"` only after validation?  
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/policycaps.py:84`  
**CONCRETE INPUT**: 
```python
class Sneaky:
    def __init__(self):
        self.used = False
    def __str__(self):
        if self.used:
            return "offense:evil.com"
        self.used = True
        return "fs.read:ok"
```
Then: `caps_list = tuple(str(c) for c in capabilities)` → first `str(c)` → `"fs.read:ok"` → passes `is_ungrantable_capability` → then later, if `__str__` is called again (e.g., in `_canonical`), it returns `"offense:evil.com"`.  
**DOES THIS WORK?** Let's trace:
- `caps_list` is built: `[str(c)]` → `"fs.read:ok"`
- Loop: `for c in caps_list`: checks `"fs.read:ok"` → OK
- `PolicyCaps(capabilities=caps_list, ...)` → stores `("fs.read:ok",)`
- `_canonical(caps)` calls `str(c)` **again** on each capability → now `c` is the **string** `"fs.read:ok"`, not the original object! Because `caps_list` is `tuple(str(c) for c in capabilities)` — so it’s **already converted to string**.

So the malicious object is **not stored** — only its **first string representation** is. No second call to `__str__`.

✅ Safe.

➡️ **NOT A FINDING**.

---

**ID**: F-08  
**TITLE**: Empty `RESERVED_UNGRANTABLE_PREFIXES` would fail-open — but test pins it  
**SEVERITY**: LOW (defense-in-depth)  
**LOCATION**: `tests/test_no_laundering.py:109`  
**ANALYSIS**: Test `test_reserved_prefix_tuple_is_the_single_source` asserts `"offense:" in RESERVED_UNGRANTABLE_PREFIXES`. This prevents accidental deletion. Good.

➡️ **NOT A FINDING** — mitigated.

---

### CERTIFICATION LINE

**CERTIFIED**: no in-band path grants an `offense:` capability; no legitimate capability is broken.

---

### STEELMAN

The change implements a robust, multi-layered prohibition of the `offense:` capability namespace: it is rejected at mint time, stripped from policy envelopes, and unconditionally refused by the canonical capability accessor—even if present in the allowed set. The use of case-insensitive prefix matching via `casefold()` correctly handles Unicode case variants, and the design explicitly excludes non-colon-prefixed strings like `"offense"` from the reservation. All paths are covered, and the test suite validates edge cases including hand-built directives and mixed-case inputs.

---

### VERDICT

**SOUND**. The implementation correctly enforces structural ungrantability of the `offense:` namespace with no bypasses or collateral damage. The single highest-value fix is already present: the unconditional early-return `False` in `Directive.grants_capability`, which serves as the load-bearing guarantee that cannot be circumvented by data manipulation.
