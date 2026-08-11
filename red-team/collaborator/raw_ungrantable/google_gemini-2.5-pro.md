# ungrantable-namespace CODE panel: google/gemini-2.5-pro

_finish=stop seconds=53.2 cost=$0.0728 usage={'prompt_tokens': 15033, 'completion_tokens': 5399, 'total_tokens': 20432, 'cost': 0.07278125, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.07278125, 'upstream_inference_prompt_cost': 0.01879125, 'upstream_inference_completions_cost': 0.05399}, 'completion_tokens_details': {'reasoning_tokens': 3546, 'image_tokens': 0, 'audio_tokens': 0}}_

This is a comprehensive adversarial review and certification of the submitted change.

### Executive Summary

The change introduces a structural prohibition against granting capabilities within the `offense:` namespace. The implementation is multi-layered and robust, with defenses at the operator's minting interface, the core policy issuance layer, and the final authority-accessor gate. The core claim—that no in-band path can grant an `offense:` capability—holds. The normalization strategy is sound and defends against Unicode confusable attacks. No legitimate capabilities are broken. A single low-severity fragility finding was identified where a core function could raise an exception on malformed input instead of failing closed, but this does not compromise the primary security guarantee.

***

### Findings

#### FINDING FRAG-01: `issue_policy` Raises `TypeError` on Non-Iterable `granted_capabilities`

*   **ID:** FRAG-01
*   **TITLE:** `issue_policy` Raises `TypeError` on Non-Iterable `granted_capabilities`
*   **SEVERITY:** LOW
*   **LOCATION:** `salienceos/interpreter/policy.py:116`
*   **CONCRETE INPUT:** `issue_policy(..., granted_capabilities=None, ...)`
*   **WHY IT BREAKS A GUARANTEE:** This is a fragility finding, not a bypass of the `offense:` prohibition. Core system functions, especially those at trust boundaries like `issue_policy`, should be total and fail closed rather than raising exceptions on malformed inputs. The generator expression `(c for c in granted_capabilities if ...)` will raise a `TypeError: 'NoneType' is not iterable` if `granted_capabilities` is `None`. While not a security vulnerability in itself, it represents a potential denial-of-service vector against the interpreter's policy-issuance path.
*   **WHETHER ANOTHER LAYER CATCHES IT:** No. The exception is unhandled and would propagate up, likely terminating the request. The `mint` function in `collaborator/policycaps.py` has a similar issue, but it is less critical as it is an operator-facing utility rather than a core interpreter component.
*   **FIX:** Ensure the `granted_capabilities` argument is iterable before the generator expression is evaluated.
    ```python
    # salienceos/interpreter/policy.py:116
    
    # Before:
    # granted_capabilities=tuple(c for c in granted_capabilities if not is_ungrantable_capability(c)),
    
    # After (Fix):
    caps_iterable = granted_capabilities if hasattr(granted_capabilities, '__iter__') and not isinstance(granted_capabilities, (str, bytes)) else ()
    granted_capabilities=tuple(c for c in caps_iterable if not is_ungrantable_capability(c)),
    ```

***

### Certification of Claims

#### **CLAIM:** "there is NO in-band path by which an `offense:`-namespaced capability is granted... AND no legitimate (non-offense) capability is broken."

**CERTIFIED:** The change successfully prevents the in-band granting of `offense:`-namespaced capabilities through multiple, redundant layers of defense, and does not break any legitimate, non-offense capabilities.

**Analysis of Attack Paths (Blocked Attacks / Non-Findings):**

1.  **GRANT AN OFFENSE CAP ANYWAY:** **BLOCKED.**
    *   **Path 1: Standard Policy Issuance.** `issue_policy` at `policy.py:116` strips any `offense:` capability before the `PolicyCaps` object is constructed and signed. The resulting directive will not contain the capability.
    *   **Path 2: Hand-Crafted `Directive`.** Even if a `Directive` object is constructed manually with an `offense:` capability in its `allowed_capabilities` tuple (as demonstrated in `tests/test_no_laundering.py:99`), the final authority check fails. `Directive.grants_capability` at `directive.py:61` unconditionally returns `False` for any capability matching `is_ungrantable_capability`, *before* checking for membership in `allowed_capabilities`. This is the load-bearing structural guarantee, and it holds.
    *   **Path 3: Hand-Crafted `SignedPolicyCaps`.** An operator with the HMAC key could bypass `collaborator.policycaps.mint` and create a validly signed `SignedPolicyCaps` containing an `offense:` capability. However, the `collaborator.policycaps.granted_capabilities` function at `collaborator/policycaps.py:161` also strips these capabilities on the read path, preventing them from entering the system seam. This is effective defense-in-depth.
    *   **Conclusion:** All analyzed paths are blocked. The layering is effective.

2.  **NORMALIZATION / BOUNDARY:** **BLOCKED.**
    *   **Unicode Confusables:** `is_ungrantable_capability` at `policy.py:44` correctly uses `unicodedata.normalize("NFKC", ...).casefold()`. This defends against full-width characters (e.g., `ｏｆｆｅｎｓｅ：`), case variations (`OFFENSE:`), and other compatibility forms. The test matrix at `tests/test_no_laundering.py:79` confirms this.
    *   **Prefix Boundary:** The `startswith()` check is precise. Inputs like `'offensexyz'`, `'not-offense:foo'`, or `'offense :foo'` (with a space) are correctly classified as not being part of the reserved namespace.
    *   **Semantic Bypass (Out of Scope):** The reservation is for the `offense:` prefix only. A capability like `net.raw_network:evil.com` is not and is not intended to be blocked by this change. This is a documented non-goal, as stated in the ADR.

3.  **BREAK A LEGIT CAP:** **BLOCKED.**
    *   The prefix `offense:` is unique and does not collide with any existing legitimate capabilities (`fs.*`, `shell.*`, `net.*`). The `startswith` logic is not subject to substring collisions (e.g., `fs.read:notes_on_offense_team` is not affected). The change correctly isolates the new restriction without causing collateral damage.

4.  **RAISE / TOTALITY:** **PARTIALLY BLOCKED.**
    *   `is_ungrantable_capability` is total. It correctly handles non-string inputs by returning `False`, as verified in `tests/test_no_laundering.py:85`.
    *   `issue_policy` is not total, as identified in finding **FRAG-01**. It can raise a `TypeError`. However, this leads to a crash (fail-stop), not an incorrect authorization (fail-unsafe).

5.  **SIGNATURE COHERENCE:** **BLOCKED.**
    *   The stripping in `issue_policy` occurs *before* the `PolicyCaps` object is created. The `signed_payload()` method reads from the already-stripped `granted_capabilities` field. Therefore, the data that is signed is identical to the data that is later used for verification. A policy issued with a legitimate and an `offense:` capability will be validly signed and verified, but only for the legitimate capability. This is correct and coherent.

***

### Steelman & Verdict

**STEELMAN (Best argument for the change):** This change is a textbook example of graduating a security guarantee from a behavioral convention into a structural invariant. It correctly identifies that the most critical enforcement point is the final authority accessor (`Directive.grants_capability`) and makes the prohibition absolute at that layer, rendering any upstream bypass attempts moot. By implementing redundant checks at policy issuance (`issue_policy`) and operator minting (`mint`), it provides robust defense-in-depth that fails loud and clean at the earliest possible point. The use of NFKC normalization demonstrates a mature understanding of Unicode-based attacks, closing a common vector for bypassing string-based security controls.

**VERDICT:** **SOUND.** The change is a robust, multi-layered implementation of the design goal, correctly making the `offense:` namespace un-grantable by construction without impacting legitimate capabilities.

The single highest-value fix is to address the fragility in `issue_policy` (**FRAG-01**) to make the core API fully robust against malformed inputs, aligning it with the fail-closed design philosophy evident elsewhere in the system.
