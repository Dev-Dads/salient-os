# Red-team: qwen/qwen3-coder

_finish=stop seconds=13.8 usage={'prompt_tokens': 17977, 'completion_tokens': 893, 'total_tokens': 18870, 'cost': 0.00821541, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.00821541, 'upstream_inference_prompt_cost': 0.00683126, 'upstream_inference_completions_cost': 0.00138415}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

Looking at this implementation, I'll perform a thorough red-team analysis focusing on the binding threat model.

Let me trace through the core verification flow and examine potential issues:

## Findings

- **ID: F1**
- **TITLE: Host re-hash can be bypassed using symbolic links to escape workspace**
- **SEVERITY: HIGH**
- **LOCATION: salienceos/verifier/observers.py, rehash() function and path traversal**
- **CONCRETE TRIGGER**: Create a symlink in the workspace that points outside the workspace root (e.g., `ln -s /etc/passwd malicious_symlink`), then declare this symlink as an output path in the envelope. The rehash will read the external file content rather than detecting the escape.
- **WHY IT MATTERS under the stated threat model**: The host-namespace re-hash is supposed to be an independent fact, but if it can be tricked into reading arbitrary filesystem locations, an attacker can forge agreement with any claimed hash by pointing to a known file. This violates M1's requirement for genuine executor-independent world facts.
- **SUGGESTED FIX**: Add path sanitization in `rehash()` and other observer functions to ensure resolved paths stay within the workspace root. Use `Path.resolve()` and verify the result is a descendant of the root.

- **ID: F2**
- **TITLE: Write-set diff can be bypassed by creating files outside declared paths**
- **SEVERITY: HIGH**
- **LOCATION: salienceos/verifier/observers.py, snapshot_tree() and observed_write_set()**
- **CONCRETE TRIGGER**: Executor creates files outside the declared workspace directory (e.g., `/tmp/secret` or parent directories). These changes won't be detected by the write-set diff since snapshot_tree() only walks the specified root.
- **WHY IT MATTERS under the stated threat model**: The write-set boundary check is a critical floor obligation meant to detect undeclared mutations. If an executor can make changes outside the monitored area, it can bypass this fundamental safety check, allowing unauthorized side effects to go undetected.
- **SUGGESTED FIX**: Expand snapshot_tree() to monitor a broader scope or implement OS-level monitoring (e.g., using inotify/fanotify on Linux) to detect any filesystem changes made by the executor process.

- **ID: F3**
- **TITLE: Shell command injection in test helper allows arbitrary code execution**
- **SEVERITY: LOW + OUT-OF-SCOPE** (requires compromised test environment)
- **LOCATION: tests/helpers.py, run_write_tool()**
- **CONCRETE TRIGGER**: Malicious content in `target_path` or `content` parameters that get interpolated into the Python script string without proper escaping.
- **WHY IT MATTERS under the stated threat model**: OUT-OF-SCOPE - This is a test-only vulnerability requiring a compromised test harness, not relevant to the production verifier under the specified threat model.
- **SUGGESTED FIX**: Use proper parameter passing instead of string interpolation, though this is test code only.

- **ID: F4**
- **TITLE: Missing validation of envelope op arguments allows malformed contracts**
- **LOCATION: salienceos/verifier/contract.py, _OP_BUILDERS functions**
- **CONCRETE TRIGGER**: Pass malformed args like `{"path": "../escape.txt"}` or `{"content": null}` to envelope builders, causing KeyError or unexpected behavior.
- **WHY IT MATTERS under the stated threat model**: While this might cause failures, it doesn't lead to false VERIFIED states under the current threat model. The contract building will fail closed.
- **SUGGESTED FIX**: Add comprehensive argument validation in the builder functions.

## STEELMAN

This design correctly enforces the core security invariant that VERIFIED status requires independent world facts, implements strong type fencing between CLAIM and WORLD evidence, and maintains a fail-closed architecture throughout. The separation of concerns between different modules and the explicit composer predicate make the verification logic clear and testable.

## VERDICT

**SERIOUS_FLAWS** - The implementation has critical path traversal vulnerabilities in the host observation components that could allow an adversary to forge independent evidence, violating the core M1 invariant that VERIFIED requires genuine executor-independent world facts.
