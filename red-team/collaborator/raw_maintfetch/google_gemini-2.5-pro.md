# maintfetch CODE panel: google/gemini-2.5-pro

_finish=stop seconds=112.9 cost=$0.1581 usage={'prompt_tokens': 36701, 'completion_tokens': 11221, 'total_tokens': 47922, 'cost': 0.15808625, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.15808625, 'upstream_inference_prompt_cost': 0.04587625, 'upstream_inference_completions_cost': 0.11221}, 'completion_tokens_details': {'reasoning_tokens': 7557, 'image_tokens': 0, 'audio_tokens': 0}}_

This is a strong piece of security engineering. The author has clearly internalized the principles of fail-closed design, capability separation, and closing TOCTOU gaps. The reuse of the `egress.py` contract is disciplined, and the new logic is layered carefully. My review stance is adversarial, but the code consistently proves itself sound against the specified attack classes. I found one minor robustness issue but no security flaws.

### 1. THE STREAMING FAIL-CLOSED CEILING

**ID:** MAINT-1
**TITLE:** SOUND: Streaming Ceiling and Cleanup Logic are Robust
**SEVERITY:** N/A (Control is effective)
**FILE:FUNCTION:** `egress.py:fetch_to_file`, `tools.py:_exec_maint_fetch`
**CONCRETE BUG OR ATTACK:** This control is **SOUND**. I attacked it from several angles, and it held up:
*   **Over-cap Staging:** An oversized artifact **cannot** be staged as if complete. The check `if total + len(chunk) > max_bytes:` in `egress.py:482` happens *before* `sink.write(chunk)`. This prevents the oversized chunk from ever being written. The function then returns a non-ok record, which the executor correctly interprets as a failure.
*   **Off-by-One:** The check is `> max_bytes`, not `>=`. This is correct. A file of exactly `max_bytes` will pass; a file of `max_bytes + 1` will fail.
*   **Reliable Partial Deletion:** The executor `_exec_maint_fetch` correctly handles cleanup. The `with open(target, "wb") as sink:` block at `tools.py:560` ensures the file handle is closed *before* the subsequent check `if not rec.ok:` at `tools.py:568`. This call to `_unlink_quiet(target)` therefore runs on a closed file, which is critical for reliability, especially on Windows. The `try...except OSError` in `_unlink_quiet` ensures the executor doesn't crash if deletion fails for an external reason (e.g., AV lock).
*   **Non-2xx Staging:** A non-2xx response is caught at `egress.py:476`. The code drains a bounded portion of the response body but **never** calls `sink.write()`, correctly preventing an error page from being staged as the artifact.
*   **Loop Hangs:** The `while True: chunk = resp.read(...); if not chunk: break` loop at `egress.py:479` is the standard, correct idiom for reading from a socket-like object until EOF. It will not hang on a clean EOF, and a network stall would be caught by the underlying socket timeout.
*   **`sink.write()` `OSError`:** A write failure (e.g., disk full) is handled at two layers. First, it's caught by the broad `except (ssl.SSLError, OSError, ...)` block at `egress.py:494`, which returns a non-ok record. The executor `_exec_maint_fetch` then sees `rec.ok` is false and calls `_unlink_quiet` to delete the partial file. This is a robust, fail-closed design.
*   **`response_len`/`hash` Correctness:** For a successful fetch, `total` and `h` are updated incrementally and reflect exactly the bytes written to the sink. For a failed fetch (including over-cap), a `_refused` record is returned, which has `response_hash=None` and `response_len=0`, correctly reflecting that no complete, valid artifact was staged.

**WHETHER ANOTHER CONTROL CATCHES IT:** N/A. The primary controls are sound.
**FIX:** No fix required.

### 2. THE net.maint AUTHORITY NAMESPACE

**ID:** MAINT-2
**TITLE:** SOUND: Authority Namespace is Correctly Isolated
**SEVERITY:** N/A (Control is effective)
**FILE:FUNCTION:** `egress.py:required_capability`
**CONCRETE BUG OR ATTACK:** This control is **SOUND**.
*   **Namespace Separation:** The function `required_capability` at `egress.py:143` has explicit, separate `if` branches for `method` "GET", "POST", and "MAINT" (`egress.py:152-154`), returning distinct capability strings with prefixes `net.get:`, `net.post:`, and `net.maint:`. A grant for one cannot confer authority for another. The tests (`test_maint_is_a_separate_namespace_from_get_and_post`) confirm this.
*   **Authorize==Connect:** The `canonical_host` function is the single source of truth. It is called once in `required_capability` to generate the capability key at the governance gate. It is called again in `fetch_to_file` (`egress.py:420`) to determine the `Host` header and the `server_hostname` for SNI validation. This correctly implements the "authorize what you connect to" principle.
*   **Hold/Approve TOCTOU:** Governance calls `required_capability` at both hold time and approve time. Because this function is deterministic and the single point of derivation, the capability key cannot diverge. The test `test_host_removed_between_hold_and_approve_denies` confirms that a capability present at hold but absent at approve results in a DENY.
*   **Ineligible URL:** An ineligible URL causes `canonical_host` to return `None`. `required_capability` checks `if host is None: return None` (`egress.py:145`), which the governance gate correctly interprets as a DENY. It does not generate a malformed or wildcard capability like `net.maint:`.
*   **Pseudo-Method Collision:** The `egress_method="MAINT"` in the tool definition is only used as an input to `required_capability`. The actual network call in `fetch_to_file` is hardcoded to `conn.putrequest("GET", ...)` at `egress.py:466`. The "MAINT" string never goes on the wire, so no collision with a real or future HTTP method is possible.

**WHETHER ANOTHER CONTROL CATCHES IT:** N/A. The primary controls are sound.
**FIX:** No fix required.

### 3. THE url+dest SEAL (approved==executed)

**ID:** MAINT-3
**TITLE:** SOUND: `held_action_seal` Correctly Binds `maint_fetch` Arguments
**SEVERITY:** N/A (Control is effective)
**FILE:FUNCTION:** `tools.py:held_action_seal`, `tools.py:freeze_args`
**CONCRETE BUG OR ATTACK:** This control is **SOUND**. The author correctly identified that `maint_fetch`, as an egress tool that mutates the local workspace, needed a seal, and that `emission_seal` was inappropriate.
*   **Correct Seal Minting:** `maint_fetch` was correctly added to `SEALED_TOOLS` (`tools.py:177`). The `held_action_seal` function has a new branch (`tools.py:214-217`) with a unique type tag (`b"M"`) that seals both `url` and `dest`. This ensures the human approves the exact source and destination.
*   **Seal Verification:** The test `test_seal_mismatch_after_hold_is_denied` confirms that the `approve` loop re-validates this seal and will DENY an action if the `dest` (or `url`) is mutated after being held.
*   **No Seal Gap:** `web_fetch` is egress and not sealed (it's read-only with no workspace side-effect). `net_post` is egress and sealed by `emission_seal`. `maint_fetch` is egress and is now correctly sealed by `held_action_seal`. There is no gap.
*   **Rebind Replay:** The seal correctly includes the tool name (`tool_name.encode("utf-8")`) as the first hashed component (`tools.py:221`), preventing a seal for one tool from being replayed to approve another.
*   **`freeze_args`:** The `freeze_args` function was correctly updated (`tools.py:250`) to coerce `url` and `dest` to immutable strings, preventing TOCTOU via a shared mutable object (like a `str` subclass with a drifting `__str__`).
*   **Injective Framing:** The seal uses length-prefixing for all fields (`tools.py:222-223`), making the serialization injective and robust against byte-stealing attacks between fields.

**WHETHER ANOTHER CONTROL CATCHES IT:** N/A. The primary control is sound.
**FIX:** No fix required.

### 4. THE WORKSPACE FENCE ON dest

**ID:** MAINT-4
**TITLE:** LOW: Unhandled `dest` Pointing to a Directory Leads to Poor Failure Mode
**SEVERITY:** LOW
**FILE:FUNCTION:** `tools.py:_exec_maint_fetch`
**CONCRETE BUG OR ATTACK:** The workspace fence itself is **SOUND** against escape, but there is a robustness gap.
*   **Escape Vectors:** `resolve_in_workspace` correctly uses `Path.resolve()` and checks that the resolved path is a child of the workspace root (`tools.py:270-272`). This prevents escapes via `../`, absolute paths, and symlinks pointing outside the workspace. `os.makedirs` at `tools.py:559` operates on the *already-fenced* path, so it cannot create directories outside the fence.
*   **The Bug:** If `dest` is a path that resolves to a *directory* inside the workspace, `resolve_in_workspace` will pass. However, the call `with open(target, "wb")` at `tools.py:560` will then fail with an `IsADirectoryError` (an `OSError` subclass). This is caught by the `except OSError` block at `tools.py:562`, which then calls `_unlink_quiet(target)`. `os.unlink` on a directory also fails. The user receives a generic `stage failed: IsADirectoryError` message, which is confusing. This is a fail-closed condition, so it's not a security vulnerability, but it's an unhandled edge case.
**WHETHER ANOTHER CONTROL CATCHES IT:** The `except OSError` block catches the error and prevents a crash, turning it into a failed tool call. However, the handling is not graceful.
**FIX:** Add a check in `_exec_maint_fetch` after resolving the path to ensure it's not a directory, providing a clearer error.

```python
# tools.py:_exec_maint_fetch, after line 557
    target = resolve_in_workspace(workspace, rel)
    if target.is_dir():
        rec = egress._refused(egress.canonical_host(url) or "", "", 0,
                              f"stage failed: destination '{rel}' is a directory")
        return Execution(result=ToolResult(ok=False, error=rec.error), egress=rec)
    _before = _observe_begin()
    # ... rest of the function
```

### 5. THE max_bytes THREADING + LEASH

**ID:** MAINT-5
**TITLE:** SOUND: `max_bytes` and Leash Controls are Correctly Implemented
**SEVERITY:** N/A (Control is effective)
**FILE:FUNCTION:** `session.py:Session.__init__`, `governance.py:execute_and_verify`, `tools.py:Tool`
**CONCRETE BUG OR ATTACK:** This control is **SOUND**.
*   **`max_bytes` Source:** The value for `max_bytes` is sourced from `session.maint_fetch_max_bytes` inside `governance.py:execute_and_verify` (`governance.py:790-791`). It is then passed explicitly through `execute_tool` into `_exec_maint_fetch`. The model's `args` are never consulted for this value, so the agent cannot widen its own fetch ceiling.
*   **Session Validation:** The `Session` class constructor (`session.py:153-159`) performs strict validation on `maint_fetch_max_bytes`, raising a `ValueError` if it is not `None` or a positive integer. This fail-loud approach prevents a misconfiguration (e.g., `True`, `0`, `-1`) from silently disabling the limit.
*   **Human Gating:** The tool definition at `tools.py:141` correctly sets `default_leash=PROPOSE_FIRST`. The ADR and code confirm there is no `net.maint.auto` capability or other logic to auto-lift this leash. Every `maint_fetch` is human-gated by default.
*   **`mutating=False`:** The choice of `mutating=False` correctly keeps `maint_fetch` out of the autonomous emission logic, quota system, and credential injection paths in `governance.py`, which are all gated on `tool.mutating`. This is a critical and correctly implemented design choice.

**WHETHER ANOTHER CONTROL CATCHES IT:** N/A. The primary controls are sound.
**FIX:** No fix required.

### 6. THE REUSED CONTRACT in fetch_to_file

**ID:** MAINT-6
**TITLE:** SOUND: `fetch_to_file` Faithfully Replicates `fetch`'s Security Contract
**SEVERITY:** N/A (Control is effective)
**FILE:FUNCTION:** `egress.py:fetch_to_file`
**CONCRETE BUG OR ATTACK:** This control is **SOUND**. I performed a line-by-line comparison of `fetch_to_file` against `fetch` and found that the transport security contract was replicated faithfully.
*   **No Drift:** All core safety checks are identical: `canonical_host` for the URL, `is_safe_public_ip` for the resolved IP, the use of `_PinnedHTTPSConnection` to prevent DNS re-binding, the fail-closed redirect handling, HTTPS-only enforcement, and request-target cleanliness/bounds checks.
*   **Intentional Differences are Safe:** The one major difference is the streaming read loop vs. `fetch`'s single `read()`. This is the core of the new feature. The other difference is the explicit non-2xx check (`egress.py:476`) that drains the response but doesn't write it to the sink; this is a necessary and safe adaptation for a file-writing function, as opposed to `fetch` which can return the error body to the model.
*   **Never Raises:** Like `fetch`, the entire function body is wrapped in a `try...except` block (`egress.py:465-497`) that catches expected network, SSL, and HTTP parsing errors and converts them into a non-ok `EgressResult`, fulfilling the never-raises contract.

**WHETHER ANOTHER CONTROL CATCHES IT:** N/A. The primary controls are sound.
**FIX:** No fix required.

---

### STEELMAN & VERDICT

**STEELMAN:** The author has delivered a textbook example of secure feature extension. By reusing the battle-tested `egress.py` transport contract and focusing new logic on the narrow problem of streaming, they minimized the new attack surface. The careful separation of the `net.maint` capability, robust TOCTOU closure via `held_action_seal` and `freeze_args`, and strict host-side control over the byte ceiling demonstrate a deep understanding of the system's security posture. The implementation is not just correct; it is layered, disciplined, and demonstrably fail-closed at every stage.

**VERDICT:** **SOUND**. The `maint_fetch` implementation is exceptionally robust and faithfully implements the security contract outlined in the ADR, successfully navigating the complexities of streaming I/O, capability management, and TOCTOU prevention.

**The single highest-value change** would be to add a pre-emptive check for directory destinations in `_exec_maint_fetch` to improve the robustness and clarity of failure modes, even though it is not a security vulnerability.
