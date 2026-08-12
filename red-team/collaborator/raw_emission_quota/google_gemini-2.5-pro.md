# emission-quota code panel: google/gemini-2.5-pro

_finish=stop seconds=59.2 cost=$0.1080 usage={'prompt_tokens': 33527, 'completion_tokens': 6608, 'total_tokens': 40135, 'cost': 0.10798875, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.10798875, 'upstream_inference_prompt_cost': 0.04190875, 'upstream_inference_completions_cost': 0.06608}, 'completion_tokens_details': {'reasoning_tokens': 3786, 'image_tokens': 0, 'audio_tokens': 0}}_

Excellent. As a senior security engineer, my default stance is that the code is broken until proven safe. I will analyze the provided diff with an adversarial mindset, focusing on the five attack vectors outlined.

Here is my review.

***

### Finding 1: Quota Bypass via Canonicalization Mismatch

*   **ID:** Q-1
*   **TITLE:** Quota Bypass via Canonicalization Mismatch
*   **SEVERITY:** CRITICAL
*   **LOCATION:** `collaborator/session.py:228` (`_validate_emission_quota`) and `collaborator/session.py:241` (`_emission_limit`)
*   **CONCRETE CASE:**
    1.  An operator configures a session with a case-sensitive or non-normalized hostname in the quota dictionary:
        ```python
        session = Session(
            workspace="/tmp/ws",
            emission_quota={"API.Example.com": 1}
        )
        ```
    2.  The model generates a tool call to `net_post` with the URL `https://api.example.com/v1/foo`.
    3.  In `execute_and_verify` (`governance.py:844`), `egress.canonical_host("https://api.example.com/v1/foo")` is called, which returns the lowercased, normalized string `"api.example.com"`.
    4.  The quota check `session.emission_allowed("api.example.com")` is performed.
    5.  Inside `emission_allowed`, `_emission_limit("api.example.com")` is called.
    6.  `_emission_limit` performs a dictionary lookup: `session.emission_quota.get("api.example.com")`. This lookup fails because the key in the dictionary is `"API.Example.com"`.
    7.  `get()` returns `None`, which signifies an unlimited quota.
    8.  The emission is allowed. The counter in `_emission_counts` is incremented for the key `"api.example.com"`.
    9.  The agent can emit to this host an unlimited number of times, completely bypassing the intended quota of 1.
*   **WHY IT IS A QUOTA BYPASS / FAIL-OPEN:** The system fails to apply the configured quota because the keys in the `emission_quota` dictionary are not canonicalized at construction time to match the canonical hostnames derived at runtime. This is a silent fail-open; the operator believes a limit is in place, but it is never enforced.
*   **WHETHER ANOTHER CONTROL CATCHES IT:** No. This is a direct bypass of the quota control itself. Capability checks (`net.post:api.example.com`) will still pass, but the *number* of emissions will be unbounded.
*   **FIX:** Modify `_validate_emission_quota` to canonicalize the keys of the dictionary using `egress.canonical_host`. This ensures that the keys used for configuration match the keys used for enforcement at runtime.

    ```python
    # collaborator/session.py:_validate_emission_quota
    # ...
    if isinstance(q, dict):
        from collaborator.egress import canonical_host  # Import locally
        out = {}
        for host, cap in q.items():
            if (not isinstance(host, str) or isinstance(cap, bool)
                    or not isinstance(cap, int) or cap < 0):
                raise ValueError("emission_quota dict must map host:str -> non-negative int")
            # Canonicalize the key before storing it.
            canon_host = canonical_host(f"https://{host}")
            if not canon_host:
                raise ValueError(f"emission_quota dict contains an invalid host: {host!r}")
            out[canon_host] = cap
        return out
    # ...
    ```
    *(Note: Using `f"https://{host}"` is a simple way to feed the bare host string to `canonical_host` which expects a URL-like structure.)*

***

### Finding 2: Latent Quota Bypass for Future Egress Tools

*   **ID:** Q-2
*   **TITLE:** Latent Quota Bypass for Future Egress Tools
*   **SEVERITY:** MEDIUM
*   **LOCATION:** `collaborator/governance.py:840`
*   **CONCRETE CASE:**
    1.  A future developer adds a new tool, `net_upload_artifact`, which sends data to an external service.
    2.  They define the tool with `egress=True`, `mutating=True`, but `verify_mode="artifact"` because the upload creates a verifiable object at the destination.
    3.  When this tool is used, the execution path enters `execute_and_verify`.
    4.  The code path for `tool.verify_mode == "egress_log"` (`governance.py:840`) is skipped.
    5.  The code proceeds to the `tool.verify_mode == "artifact"` block (`governance.py:910`), which has no quota check.
    6.  The tool executes, performing an emission, but `session.emission_allowed` is never checked and `session.consume_emission` is never called.
*   **WHY IT IS A QUOTA BYPASS:** The quota check and consumption logic are incorrectly tied to the `verify_mode` of the tool (`"egress_log"`) instead of the fundamental properties of the action (an emission, i.e., `egress=True` and `mutating=True`). This creates a latent vulnerability where any future emitting tool that doesn't use `verify_mode="egress_log"` will silently bypass the quota system.
*   **WHETHER ANOTHER CONTROL CATCHES IT:** No. This is a bypass of the quota logic.
*   **FIX:** Decouple the quota check from the `verify_mode` block. The check should happen for any tool that is both an egress and mutating, regardless of how it's verified.

    ```python
    # collaborator/governance.py:execute_and_verify
    # ... (after leash checks)

    # Perform quota check for ANY mutating egress tool, regardless of verify_mode.
    if getattr(tool, "egress", False) and tool.mutating:
        emit_host = egress.canonical_host(str(args.get("url") or ""))
        _allowed = getattr(session, "emission_allowed", None)
        if callable(_allowed) and not _allowed(emit_host):
            return Decision(action_id, tool.name, DENIED,
                            f"per-destination emission quota exhausted for {emit_host}", leash,
                            directive=directive, args=args)
        # Defer consumption until right before the tool call.

    # ... (existing verify_mode blocks)

    if tool.verify_mode == "egress_log":
        # ...
        if getattr(tool, "egress", False) and tool.mutating:
            # ... (credential lookup logic remains here) ...
            _consume = getattr(session, "consume_emission", None)
            if callable(_consume):
                # Consume here, right before execution.
                _consume(egress.canonical_host(str(args.get("url") or "")))
        try:
            execution = execute_tool(...)
        # ...
    
    # ... (other verify_mode blocks)
    # If a new emitting tool with verify_mode="artifact" is added, its block
    # would also need to call consume_emission before execute_tool.
    # A more robust fix would be a single consumption point.
    # A simpler, though slightly less clean, fix is to duplicate the consumption call.
    
    # Alternative (better) Fix Structure:
    # ...
    # (Perform quota check as above)
    # ...
    # to_consume_host = None
    # if getattr(tool, "egress", False) and tool.mutating:
    #     to_consume_host = egress.canonical_host(str(args.get("url") or ""))
    #
    # _consume = getattr(session, "consume_emission", None)
    # if callable(_consume) and to_consume_host:
    #     _consume(to_consume_host)
    #
    # try:
    #     execution = execute_tool(...) # This call would be unified
    # except ...
    # ... (process execution result based on verify_mode)
    ```

***

### Finding 3: No-Count on HELD-then-DENIED Emission

*   **ID:** M-1
*   **TITLE:** Held Emission Attempt Consumes No Quota if Denied on Re-Authorization
*   **SEVERITY:** LOW
*   **LOCATION:** `collaborator/loop.py:177` (`approve`)
*   **CONCRETE CASE:**
    1.  A session has a quota of 1 for `api.example`.
    2.  The model proposes an emission to `api.example`, which is `HELD`. No quota is consumed.
    3.  The operator revokes the `net.post:api.example` capability from the session.
    4.  The operator calls `approve()` on the held decision.
    5.  The `reauthorized_or_denied()` check (`loop.py:177`) fails, and a `DENIED` decision is returned.
    6.  The `approve()` function returns early. `execute_and_verify` is never called.
    7.  No quota is consumed. The operator can repeat this (revoke, approve, get denied) indefinitely. The agent can then be re-granted the capability and will still have its full quota of 1, despite the operator having seen and acted on a proposed emission.
*   **WHY IT IS A MISCOUNT:** The design doc states, "a failing attempt still burns quota (bounds a retry channel)." While a *network-failing* attempt correctly burns quota (as shown in `test_failed_emission_still_consumes_quota`), an *authorization-failing* attempt on a previously-held action does not. This is an inconsistency in the "attempts burn quota" principle. An adversary can't exploit this for exfiltration, but it does represent a miscounting of user-approved attempts that fail pre-dispatch.
*   **WHETHER ANOTHER CONTROL CATCHES IT:** The `reauthorized_or_denied` check correctly prevents the emission, so there is no bypass. This is purely a counting issue.
*   **FIX:** This is a minor logic gap. To fix it, the `approve` function would need to consume quota for a mutating egress tool *before* the `reauthorized_or_denied` check, and then potentially have a way to refund it if the denial was for a non-permanent reason. Given the complexity and low severity, the most pragmatic fix is to document this behavior as a known limitation: only attempts that pass re-authorization and reach the execution stage are counted.

***

### Steelman and Verdict

**STEELMAN (The author's likely defense):**
The code is sound. The critical path for emissions (`net_post`) is correctly routed through `execute_and_verify`, where the quota is checked and consumed right before dispatch. The canonicalization mismatch (Q-1) is a configuration footgun, but host configuration is a trusted input, and operators are expected to know that hostnames are canonicalized. The latent bypass (Q-2) is not a current bug, as no such tool exists, and any future developer adding an emitting tool would be responsible for integrating it with the quota system. The other checks (miscounting, model reachability, method->cap) are robust and covered by tests.

**VERDICT: SERIOUS_FLAWS**

The system contains a silent, critical quota bypass vulnerability.

The canonicalization mismatch (Q-1) is not an acceptable "footgun"; it is a trap. The entire egress system is built on the principle of a single, canonical source of truth for hostnames to prevent exactly this class of mismatch vulnerability. Failing to apply this principle to the quota configuration keys is a critical design flaw that completely negates the control under common and easily-made configuration patterns. While other aspects of the implementation are solid (e.g., the method->capability fix and the counting logic for successful paths), this single issue allows for unbounded emissions and must be fixed.

**Highest-Value Fix:**
Implement the fix for **Q-1: Quota Bypass via Canonicalization Mismatch** by canonicalizing the `emission_quota` dictionary keys upon `Session` construction.
