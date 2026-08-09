# Egress build code panel: google/gemini-2.5-pro

_finish=stop seconds=161.6 cost=$0.1848 usage={'prompt_tokens': 28006, 'completion_tokens': 14983, 'total_tokens': 42989, 'cost': 0.1848375, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1848375, 'upstream_inference_prompt_cost': 0.0350075, 'upstream_inference_completions_cost': 0.14983}, 'completion_tokens_details': {'reasoning_tokens': 12858, 'image_tokens': 0, 'audio_tokens': 0}}_

Excellent. This is a high-stakes review of a critical security boundary. My job is to break the guarantees laid out in the ADR. I will assume the code is broken until proven otherwise, focusing on the attack vectors specified.

### Executive Summary

The egress module is exceptionally well-hardened against a wide range of common and subtle attacks. The core mediation contract in `egress.py` — particularly the `canonical_host` function and the IP-pinning connection logic — is robust and successfully defends against the entire class of authorize-one/connect-another attacks, DNS rebinding, and SSRF. The TOCTOU re-gate at approval time is correctly implemented.

However, a critical architectural flaw exists in the `research.py` module. It bypasses the one core-enforced capability gate (`directive.grants_capability`) and re-implements its own authority check. This "dual gate" antipattern breaks a core security invariant and will inevitably lead to a policy-bypass vulnerability as the system evolves. While other components are sound, this single issue represents a serious flaw in the shipped build.

---

### Finding 1

*   **ID:** RES-01
*   **TITLE:** Research Module Bypasses Core Capability Gate, Re-implementing Its Own Authority Check
*   **SEVERITY:** HIGH
*   **LOCATION:** `collaborator/research.py:126`
*   **CONCRETE INPUT OR BYPASS:**
    This is an architectural flaw that creates a "dual gate" vulnerability; a concrete bypass depends on a future, plausible change.
    1.  The core gate at `governance.py:228` uses `directive.grants_capability(required_cap)`. This is the single, core-enforced authority for all governed tools, as per the ADR.
    2.  The research module at `research.py:126` performs its own check: `if cap not in granted_capabilities(session):`. It reads the raw capabilities from the session and performs a direct `in` check, bypassing the `Directive` object and the official gate.

    **Hypothetical Bypass Scenario:** Imagine a future version of `PolicyCaps` adds support for negative capabilities (e.g., `!net.get:ads.evil.com` to block a specific subdomain of an otherwise-allowed host). The core `directive.grants_capability` would be updated to understand this logic.
    *   A call to the `web_fetch` tool for `https://ads.evil.com` would be correctly **DENIED** by the core gate in `governance.py`.
    *   A call via `web_research` for `https://ads.evil.com` would be **ALLOWED**. The check in `research.py` would see `net.get:evil.com` in the raw capabilities and the `in` check would pass, completely missing the nuance of the negative grant. The research module would successfully reach a host the operator explicitly prohibited.

*   **WHY IT BREAKS A GUARANTEE:**
    It breaks the ADR's fundamental guarantee of a **single, core-enforced capability gate**. The ADR states: "the capability gate is the one core-enforced authority (`directive.grants_capability`...); the model cannot talk past it". The `research` module does exactly that: it talks past the official gate by re-implementing its own, simpler version. This creates a dangerous divergence where policy enforcement is inconsistent across the system.
*   **WHETHER ANOTHER CHECK CATCHES IT:**
    No. This is the authority check itself. Because it bypasses the core gate, no other check can retroactively apply the correct policy.
*   **FIX:**
    Refactor `_web_get_finding` to use the one true gate. This likely involves creating a minimal, temporary `Directive` for the research context and calling `directive.grants_capability()`, ensuring that all egress decisions flow through the exact same logic.

    ```python
    # collaborator/research.py

    # APPROXIMATE FIX - requires plumbing to create a directive
    def _web_get_finding(session, url) -> str:
        # ... existing checks for trust level ...
        
        # Create a minimal directive for this check
        # NOTE: This plumbing (e.g., a helper function) needs to be built
        try:
            policy = issue_policy("collab-policy-research", "research-act", granted_capabilities(session), ...)
            directive = interpret(policy, [], session.policy_key)
        except Exception:
            return "(refused: governance error during research)"

        cap = egress.required_capability(str(url or ""))
        if cap is None:
            return f"(refused: ineligible web url: {url})"
        
        # Use the ONE TRUE GATE
        if not directive.grants_capability(cap):
            return f"(refused: {cap} is not allowlisted — egress is default-deny)"
        
        result = egress.fetch(str(url))
        # ... rest of function ...
    ```

---
### Non-Finding: Egress Canonicalization and Connection Logic (Vector 1)

The implementation of `egress.canonical_host` and `fetch` is exceptionally robust and appears to be **SOUND**. I was unable to find any bypass.

*   **Authorize-One / Connect-Another:** The use of a single `canonical_host` function to generate the key for both the capability check (`governance.py`) and the connection parameters (`fetch`) is correct and strictly enforced. All `urlsplit` quirks I tested (userinfo, non-standard ports, case variations, IDN/punycode, trailing dots, whitespace, control characters) are correctly handled and lead to a `None` result (fail-closed). The code correctly rejects userinfo in the `netloc` *before* trusting `urlsplit`'s `hostname` attribute, closing a classic bypass.
*   **IP Pin / Rebind / SSRF:** The IP pinning mechanism is sound. `fetch` resolves DNS once, iterates to find the *first* safe public IP, and refuses to proceed if none are found. The `is_safe_public_ip` function correctly uses the `ipaddress` module, which handles edge cases like IPv4-mapped IPv6 addresses (`::ffff:127.0.0.1`). The `_PinnedHTTPSConnection` correctly connects to the pinned IP while using the canonical hostname for TLS SNI and certificate validation, defeating DNS rebind attacks.
*   **Redirects:** Redirects (3xx) are correctly detected and cause the fetch to fail closed, as promised by the ADR. The `Location` header is reported, but not followed.
*   **GET Exfil:** The query length is bounded by `MAX_URL_QUERY`, and the code in `fetch` explicitly forbids any model-supplied headers like `Authorization` or `Cookie` by calling `endheaders()` after setting a fixed, safe set of headers. This correctly implements the "best-effort" mitigation described in the ADR.

---
### Non-Finding: Governance Capability Derivation and TOCTOU (Vector 2)

The capability derivation logic in `governance.py` is **SOUND**.

*   It correctly uses `egress.required_capability` (and therefore `canonical_host`) to derive the capability string from the URL. An ineligible URL correctly results in a `None` capability, which is handled as a `DENIED` decision.
*   The `reauthorized_or_denied` function correctly closes the emission TOCTOU vulnerability. It re-derives the required capability from the frozen arguments of the held decision and re-checks it against the *current* `PolicyCaps` at the moment of approval. The test `test_host_removed_from_allowlist_between_hold_and_approve_denies` correctly pins this exact behavior.

---
### Non-Finding: `flag_offense_shape` as a Control (Vector 4)

The implementation of `flag_offense_shape` is **SOUND** and matches the ADR's "audit-only" guarantee.

*   In both `governance.py:govern_action` and `governance.py:execute_and_verify`, the `offense_flag` is populated and stored on the `Decision` object.
*   Its value is never used in any conditional logic that would alter control flow (i.e., it never causes a denial or changes the leash). It is used only for populating the audit trail via the `Decision.summary()` method. The test `test_offense_shape_tags_but_does_not_deny` confirms this.

---

### STEELMAN

The author would argue that the check in `research.py` (`cap in granted_capabilities(session)`) is functionally identical to the core gate's check *for the specific case of string capabilities*, which is all that exists today. Since `directive.grants_capability` for a string is just a membership test on the same set, there is no current daylight between the two checks, and therefore no vulnerability. Shipping a more complex fix that plumbs the entire `Directive` machinery into the research loop for a non-existent bug is over-engineering and introduces unnecessary complexity.

### VERDICT

**SERIOUS_FLAWS**. While most of the module is exceptionally well-built, creating a second, divergent authority gate is a cardinal sin in security engineering that breaks a primary architectural guarantee.

The single highest-value fix is **RES-01**: Refactor `research.py` to use the one, core-enforced `directive.grants_capability` gate, eliminating the dual-gate vulnerability and ensuring policy is applied consistently across the entire system.
