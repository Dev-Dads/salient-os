# PR B run_command parity + MINOR-B panel: qwen/qwen3-max

_finish=stop seconds=25.5 cost=$0.0961 usage={'prompt_tokens': 55183, 'completion_tokens': 1285, 'total_tokens': 56468, 'cost': 0.09610848, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.09610848, 'upstream_inference_prompt_cost': 0.08608548, 'upstream_inference_completions_cost': 0.010023}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## FINDINGS

### B1-01 / UNSIGNED session.capabilities can bypass B1 floor via legacy config
**SEVERITY**: CRITICAL  
**LOCATION**: collaborator/governance.py:415-418  
**CONCRETE INPUT OR BYPASS**: 
```python
# Session with unsigned capabilities including shell.raw_network
session = Session(
    workspace="/tmp",
    capabilities=("fs.read:project", "fs.write:project", "shell.exec", "shell.raw_network"),
    leash_overrides={"run_command": ACT_THEN_REPORT}
)
intent = ToolIntent("run_command", {"command": ["curl", "http://example.com"]}, "structured")
```
**WHY IT BREAKS A GUARANTEE**: The B1 floor only checks for `shell.raw_network` in signed policy caps via `enforced(session) and directive.grants_capability(SHELL_RAW_NETWORK_CAP)`. However, legacy sessions (unsigned) can include `shell.raw_network` in their mutable `session.capabilities`, which bypasses the floor entirely. This violates C1's requirement that `shell.raw_network` is default-deny and not model-reachable/forgeable.

**WHETHER ANOTHER LAYER CATCHES IT**: NO. The execution belt in `execute_and_verify` uses the same flawed check, so an autonomous run would proceed with raw network access.

**FIX**: Change the condition to require enforcement for ANY `shell.raw_network` grant:
```python
# Replace line 415-418 condition with:
if (intent.name == "run_command" and leash == ACT_THEN_REPORT
        and not netns_available()
        and not (enforced(session) and SHELL_RAW_NETWORK_CAP in granted_capabilities(session))):
```

### MINOR-B-01 / Shallow copy in freeze_args allows nested mutation
**SEVERITY**: HIGH  
**LOCATION**: collaborator/tools.py:235-249  
**CONCRETE INPUT OR BYPASS**:
```python
# Command with nested mutable structure
cmd = ["python", "-c", "print('safe')", {"nested": ["data"]}]
held = govern_action(session, ToolIntent("run_command", {"command": cmd}, "structured"))
# Mutate nested structure after hold
cmd[3]["nested"][0] = "PWNED"
result = approve(session, held)
```
**WHY IT BREAKS A GUARANTEE**: `freeze_args` only performs a shallow copy and converts command lists to tuples of strings. However, if command elements contain nested mutable structures (dicts, lists), these remain shared by reference. The seal computation calls `str()` on each element, but if the nested structure's `__str__` method returns different values based on internal state, the seal and execution can diverge.

**WHETHER ANOTHER LAYER CATCHES IT**: PARTIALLY. The test suite includes cases for hostile `__str__` methods, but only at the top level. Nested structures are not handled, and the executor will use the mutated version during execution while the seal used the original string representation.

**FIX**: Deep freeze all command elements by recursively converting to immutable types or pre-computing string representations:
```python
def freeze_args(args: dict) -> dict:
    frozen = dict(args)
    cmd = frozen.get("command")
    if isinstance(cmd, (list, tuple)):
        # Pre-compute string representations to prevent later mutation
        frozen["command"] = tuple(str(c) for c in cmd)
    # ... rest unchanged
```

### MINOR-B-02 / Seal verification placement allows user-origin mutations to bypass origin-gated re-denies
**SEVERITY**: MEDIUM  
**LOCATION**: collaborator/loop.py:189-220  
**CONCRETE INPUT OR BYPASS**: A user-originated held decision that passes the origin-gated re-denies but has its args mutated before the seal check. Since the seal check comes after the origin-gated re-denies, a mutation that would trigger those re-denies if checked again could slip through.

**WHY IT BREAKS A GUARANTEE**: The comment states the seal check is "the general backstop for ANY other mutation (incl. user-origin, which the origin-gated re-denies above deliberately do not cover)." However, if a user-origin mutation creates a controlled location path or code root reference, it should be caught by the re-denies, but the current ordering means it might only be caught by the seal check, which provides less specific error messaging.

**WHETHER ANOTHER LAYER CATCHES IT**: YES. The seal mismatch would still deny execution, but with a generic "payload changed" message instead of the specific reason.

**FIX**: Move the seal verification before the origin-gated re-denies to ensure mutations are caught early, or add explicit re-checks of the frozen args in the origin-gated conditions.

## CERTIFICATION

**C1 (B1)**: NOT-CERTIFIED. The B1 floor can be bypassed by including `shell.raw_network` in unsigned session capabilities, violating the default-deny requirement and allowing unisolated autonomous runs.

**C2 (MINOR-B)**: NOT-CERTIFIED. The `freeze_args` implementation doesn't handle nested mutable structures in command arguments, potentially allowing execution divergence from the sealed payload.

## STEELMAN

The PR correctly implements the core concepts of monotonic autonomy floors and payload sealing. The test suite is comprehensive and covers many edge cases including hostile string representations and tool rebinds. The execution-time belt provides defense-in-depth by re-verifying isolation requirements. The single source of truth for SEALED_TOOLS prevents drift between sealing and verification.

## VERDICT

SERIOUS_FLAWS. The critical bypass of the B1 floor via unsigned capabilities fundamentally breaks the network isolation guarantee, and the shallow freezing in MINOR-B creates a potential approved≠executed vector. The highest-value fix is to modify the B1 floor condition to reject any `shell.raw_network` grant that isn't properly signed and enforced, ensuring the capability is truly default-deny and not forgeable through legacy session configuration.
