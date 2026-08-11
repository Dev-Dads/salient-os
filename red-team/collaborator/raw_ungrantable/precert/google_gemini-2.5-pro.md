# ungrantable-namespace CODE panel: google/gemini-2.5-pro

_finish=stop seconds=55.2 cost=$0.0759 usage={'prompt_tokens': 13649, 'completion_tokens': 5879, 'total_tokens': 19528, 'cost': 0.07585125, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.07585125, 'upstream_inference_prompt_cost': 0.01706125, 'upstream_inference_completions_cost': 0.05879}, 'completion_tokens_details': {'reasoning_tokens': 3724, 'image_tokens': 0, 'audio_tokens': 0}}_

Excellent. This is a senior-level adversarial review. I will now perform the requested analysis.

***

### Adversarial Review and Certification

Here are the findings from the review of the change to introduce the `offense:` un-grantable namespace.

***

### Finding F-01: Unicode Normalization Bypass Grants `offense:` Capability

*   **ID:** F-01
*   **TITLE:** Unicode Normalization Bypass Grants `offense:` Capability
*   **SEVERITY:** **CRITICAL**
*   **LOCATION:** `salienceos/interpreter/policy.py:27` (function `is_ungrantable_capability`)
*   **CONCRETE INPUT OR BYPASS:**
    Provide a capability string using Unicode full-width characters that normalize to the prohibited prefix, such as:
    `'ｏｆｆｅｎｓｅ：evil.com'`
*   **WHY IT BREAKS A GUARANTEE:**
    The core recognizer `is_ungrantable_capability` uses `capability.casefold().startswith(...)`. While `casefold()` is superior to `lower()` for case-insensitivity, it does **not** normalize Unicode compatibility characters, such as full-width letters and symbols.

    1.  `is_ungrantable_capability('ｏｆｆｅｎｓｅ：evil.com')` returns `False` because the full-width string does not start with the ASCII `offense:`.
    2.  Because the recognizer fails, all three layers of defense fail:
        *   `collaborator.policycaps.mint` will **not** raise a `ValueError` and will successfully mint a signed grant containing this capability.
        *   `salienceos.interpreter.policy.issue_policy` will **not** strip the capability, so it will be included in the signed `PolicyCaps` envelope.
        *   `salienceos.interpreter.directive.grants_capability` will **not** trigger its unconditional `return False` and will proceed to the membership check (`capability in self.allowed_capabilities`). Since the capability is present, it will return `True`.

    This is a complete bypass of the ADR-0004 guarantee, allowing a prohibited `offense:` capability to be granted and authorized.
*   **WHETHER ANOTHER LAYER CATCHES IT:**
    No. All three defensive layers (`mint`, `issue_policy`, `grants_capability`) rely on this single, flawed recognizer function. The failure is in the shared dependency, so all checks are bypassed simultaneously.
*   **FIX:**
    Strengthen `is_ungrantable_capability` to perform Unicode normalization before case-folding. The `NFKC` normalization form is appropriate here as it handles compatibility characters.

    ```python
    # salienceos/interpreter/policy.py
    import unicodedata

    def is_ungrantable_capability(capability) -> bool:
        """..."""
        if not isinstance(capability, str):
            return False
        # Normalize compatibility characters (e.g., full-width) to their ASCII equivalents
        # BEFORE case-folding and checking the prefix.
        normalized_cap = unicodedata.normalize('NFKC', capability)
        return normalized_cap.casefold().startswith(RESERVED_UNGRANTABLE_PREFIXES)
    ```

***

### Finding F-02: Authority Bypass via Direct Access to `directive.allowed_capabilities`

*   **ID:** F-02
*   **TITLE:** Authority Bypass via Direct Access to `directive.allowed_capabilities`
*   **SEVERITY:** **LOW**
*   **LOCATION:** `salienceos/interpreter/directive.py:54` (the `allowed_capabilities` field)
*   **CONCRETE INPUT OR BYPASS:**
    This is a bypass in a hypothetical consumer, not the core logic itself. Assume an attacker has compromised the `policy_key` and can issue arbitrary policies. They issue a policy containing `'offense:evil.com'`. The `issue_policy` function strips this. However, the attacker can bypass `issue_policy` and construct a `PolicyCaps` object manually, sign it, and have it processed by `interpret`.

    The resulting `Directive` object `d` will have `d.allowed_capabilities == ('offense:evil.com', ...)`.
    A non-compliant consumer could then check for authority incorrectly:
    `if 'offense:evil.com' in d.allowed_capabilities: # This is TRUE`
    `    do_prohibited_action()`

    This bypasses the `d.grants_capability('offense:evil.com')` check, which would have correctly returned `False`.
*   **WHY IT BREAKS A GUARANTEE:**
    This doesn't break the guarantee of `grants_capability` but highlights a critical architectural assumption: that **no consumer ever reads `directive.allowed_capabilities` directly for an authorization decision**. The documentation for `Directive` states this contract, but the design does not enforce it programmatically (e.g., by making the attribute private). If any consumer violates this contract, the load-bearing structural guarantee in `grants_capability` is rendered ineffective.
*   **WHETHER ANOTHER LAYER CATCHES IT:**
    No. This is an architectural issue concerning the API boundary of the `Directive` object. The core logic cannot catch a consumer's misuse of its output.
*   **FIX:**
    This is a non-finding against the diff itself, but a valuable hardening recommendation. To make the intended contract clearer and harder to violate accidentally, rename the field to `_allowed_capabilities`. This Python convention signals that it is an internal implementation detail and should not be accessed directly, forcing consumers toward the public `grants_capability` method.

***

### Finding F-03: `TypeError` on Non-Iterable `capabilities` Argument

*   **ID:** F-03
*   **TITLE:** `TypeError` on Non-Iterable `capabilities` Argument
*   **SEVERITY:** **LOW**
*   **LOCATION:** `collaborator/policycaps.py:90` and `salienceos/interpreter/policy.py:100`
*   **CONCRETE INPUT OR BYPASS:**
    Call `mint` or `issue_policy` with a non-iterable `capabilities` argument.
    `mint(capabilities=None, ...)`
    `issue_policy(granted_capabilities=None, ...)`
*   **WHY IT BREAKS A GUARANTEE:**
    The functions are not total; they raise an unhandled `TypeError` when the list comprehension or generator expression attempts to iterate over `None`. While not a security bypass, functions at core security boundaries should be robust against malformed inputs and fail closed (e.g., by treating `None` as an empty list of capabilities) rather than raising an exception that could constitute a denial-of-service vector. The docstring for `is_ungrantable_capability` explicitly mentions totality as a goal, which should apply to the other boundary functions as well.
*   **WHETHER ANOTHER LAYER CATCHES IT:**
    No, the exception propagates up the stack.
*   **FIX:**
    Ensure the input is treated as an iterable, defaulting `None` to an empty tuple.

    ```python
    # collaborator/policycaps.py:90
    caps_list = tuple(str(c) for c in (capabilities or ()))
    # salienceos/interpreter/policy.py:100
    granted_capabilities=tuple(c for c in (granted_capabilities or ()) if not is_ungrantable_capability(c)),
    ```

***

### Certification of Other Claims

*   **Legitimate Capabilities:** **CERTIFIED.** The `is_ungrantable_capability` check uses `startswith("offense:")`. This is precise and has no collision path with existing legitimate capabilities like `fs.read:project` or `net.get:example.com`. No legitimate capability is broken by this change.
*   **Signature Coherence:** **CERTIFIED.** The `issue_policy` function strips capabilities *before* the `PolicyCaps` object is constructed and signed. The `verify_policy` function verifies the signature against the payload derived from that same object. The data signed and the data verified are therefore coherent. A stripped capability never enters the signed payload.
*   **Boundary Conditions:** **NOT CERTIFIED.** The Unicode normalization bypass (F-01) is a critical failure on a boundary condition. Other boundary checks (e.g., `offensexyz`, `x-offense:y`) are handled correctly.

***

### CERTIFICATION LINE

**NOT-CERTIFIED:** A Unicode normalization vulnerability allows a full-width `ｏｆｆｅｎｓｅ：` capability to bypass all three layers of defense and become granted, breaking the core structural guarantee.

### STEELMAN

The author has implemented a robust, multi-layered defense to make the `offense:` namespace structurally un-grantable, correctly identifying that the final check must be unconditional in the `grants_capability` accessor. The use of `casefold()` demonstrates an awareness of internationalization issues beyond simple `lower()`, and the defense-in-depth approach (reject at `mint`, strip at `issue`, refuse at `grant`) is sound architectural practice. The change correctly isolates the prohibition to a prefix, ensuring no existing, legitimate capabilities are affected.

### VERDICT

**SERIOUS_FLAWS:** The change is conceptually sound and well-layered, but a critical Unicode normalization vulnerability in the core recognizer function allows a direct bypass of the entire prohibition.

**Highest-Value Fix:** Immediately patch `is_ungrantable_capability` to include `NFKC` normalization, as detailed in finding **F-01**. This single-line change restores the integrity of all three defensive layers and fulfills the ADR's primary goal.
