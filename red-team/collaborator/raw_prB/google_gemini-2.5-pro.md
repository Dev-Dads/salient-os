# PR B run_command parity + MINOR-B panel: google/gemini-2.5-pro

_finish=stop seconds=55.5 cost=$0.1351 usage={'prompt_tokens': 62624, 'completion_tokens': 5682, 'total_tokens': 68306, 'cost': 0.1351, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1351, 'upstream_inference_prompt_cost': 0.07828, 'upstream_inference_completions_cost': 0.05682}, 'completion_tokens_details': {'reasoning_tokens': 4660, 'image_tokens': 0, 'audio_tokens': 0}}_

Excellent. I will now perform the adversarial code review and certification as requested.

### Security Review Findings

---

#### **ID: B1-1**
*   **TITLE:** Unhandled Exception in `directive.grants_capability` Can Crash `govern_action`
*   **SEVERITY:** MEDIUM
*   **LOCATION:** `collaborator/governance.py:416`
*   **CONCRETE INPUT OR BYPASS:**
    This is not a bypass but a denial-of-service vulnerability that breaks a core guarantee of the governance seam. `govern_action` promises never to raise an exception. However, the condition for the isolation-earns-autonomy floor does not handle exceptions from its components.

    A `directive` object can be constructed where `grants_capability` is a property or method that raises an exception. If such an object is passed to `govern_action`, the `if` statement at line 416 will raise, crashing the function and likely the turn loop.

    **Repro:**
    ```python
    class HostileDirective:
        @property
        def grants_capability(self):
            raise ValueError("Hostile grant check")

    # In a test, with code_protection_available=True and netns_available=False:
    # This call will raise ValueError instead of returning a DENIED/HELD Decision.
    govern_action(
        session_with_atr_shell,
        ToolIntent("run_command", ...),
        directive=HostileDirective()
    )
    ```
*   **WHY IT BREAKS A GUARANTEE:**
    It breaks the explicit "never raises" guarantee of `govern_action`. In a core governance component, an unhandled exception is a serious reliability and availability flaw. While it doesn't directly cause a policy bypass (it fails by crashing, not by opening a hole), it prevents any further actions from being governed, constituting a denial of service.
*   **WHETHER ANOTHER LAYER CATCHES IT:**
    No. This crash occurs inside the primary `govern_action` function, which is the top-level entry point for governing a tool call. An exception here would propagate up and likely terminate the `run_turn` loop, preventing any subsequent actions in that turn from being processed.
*   **FIX:**
    Wrap the condition in a `try...except` block, treating any exception during the capability check as a failure to grant the capability (fail-closed).

    ```python
    # collaborator/governance.py:416
    try:
        has_raw_opt_in = (enforced(session) and directive.grants_capability(SHELL_RAW_NETWORK_CAP))
    except Exception:
        has_raw_opt_in = False

    if (intent.name == "run_command" and leash == ACT_THEN_REPORT
            and not netns_available()
            and not has_raw_opt_in):
        leash = apply_cap(leash, PROPOSE_FIRST)
    ```

---
### Certification

*   **C1 (B1 floor):** **NOT-CERTIFIED.** An unhandled exception in a dependency can crash the governance function, breaking its "never raises" guarantee, although this does not lead to a direct bypass of the floor.
*   **C2 (MINOR-B seal):** **CERTIFIED.** The approved==executed seal for held `run_command` and `write_file` actions is sound, correctly using structural freezing and an injective, tool-scoped seal to prevent payload mutation before execution.

---
### Steelman & Verdict

The author has implemented two orthogonal, monotonic autonomy floors for `run_command`, correctly composing them to ensure the stricter outcome always wins. The MINOR-B seal extension is exceptionally well-executed, addressing the core TOCTOU vector (by-reference mutation) with a structural fix (`freeze_args`) and backing it with a robust, injective integrity seal. The implementation demonstrates a deep understanding of prior security feedback, systematically closing vectors related to tool rebinding, hostile `__str__` methods, and making the `approve()` function a self-contained security boundary.

**VERDICT:** **MINOR_ISSUES.** The core logic of both the autonomy floor and the integrity seal is sound, but the B1 floor has a robustness flaw where an unexpected exception can crash the entire governance function.

**Highest-Value Fix:** The single highest-value fix is to wrap the B1 floor's capability-check condition in a `try...except` block to ensure that `govern_action` upholds its "never raises" guarantee, failing closed to a `HELD` decision rather than crashing on an unexpected error.
