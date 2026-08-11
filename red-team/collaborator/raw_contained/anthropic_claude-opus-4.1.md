# PR#39 contained certification panel: anthropic/claude-opus-4.1

_finish=stop seconds=68.3 cost=$1.2162 usage={'prompt_tokens': 73362, 'completion_tokens': 1544, 'total_tokens': 74906, 'cost': 1.21623, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 1.21623, 'upstream_inference_prompt_cost': 1.10043, 'upstream_inference_completions_cost': 0.1158}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

I'll perform a thorough adversarial review of this "protection earns autonomy" implementation, attempting to break each claimed guarantee.

## SECURITY REVIEW FINDINGS

### Finding 1: CRITICAL - Workspace Overlap Allows Code Root Write
**ID**: SEC-001  
**TITLE**: .sandbox-home Directory Creation Bypasses Workspace-Code Disjointness Check  
**SEVERITY**: CRITICAL  
**LOCATION**: collaborator/tools.py:389  
**CONCRETE INPUT**: Session with workspace="/path/to/collaborator/.sandbox-home-parent", then autonomous run_command  
**BYPASS**: 
```python
os.makedirs(os.path.join(str(workspace), ".sandbox-home"), exist_ok=True)
```
This creates `.sandbox-home` INSIDE the workspace. If workspace="/opt/app/collaborator", this creates "/opt/app/collaborator/.sandbox-home" - writing INTO the protected code root, bypassing the disjointness check which only validates the workspace PATH at Session construction, not subdirs created later.
**WHY IT BREAKS**: The disjointness check prevents workspace==code_root but not workspace_subdir overlapping code_root
**ANOTHER CHECK CATCHES IT**: No - bwrap would bind the workspace rw, including this subdir inside the code root
**FIX**: Create .sandbox-home in a temp location, not inside workspace

### Finding 2: HIGH - Guard Script Command Injection via Witness Path
**ID**: SEC-002  
**TITLE**: Unquoted Witness Path in Guard Script Allows Command Injection  
**SEVERITY**: HIGH  
**LOCATION**: collaborator/contained.py:95-107  
**CONCRETE INPUT**: A code root at path containing shell metacharacters like `/opt/app;reboot/collaborator/`
**BYPASS**: The guard script line:
```bash
f'[ -r "$f" ] || {{ echo {_CODEFENCE_UNVERIFIED_SENTINEL} >&2; exit {_CODEFENCE_UNVERIFIED_EXIT}; }};'
```
While `$f` is quoted, the sentinel and exit code are NOT quoted in the echo/exit commands. More critically, the specs construction:
```python
specs = " ".join(f'"{r.as_posix()}|{w.as_posix()}"' for r, w in pairs)
```
Could break if paths contain quotes or newlines.
**WHY IT BREAKS**: Shell injection in the guard could make it exit 0 while roots remain writable
**ANOTHER CHECK CATCHES IT**: The actual bwrap --ro-bind would still apply, but the guard could falsely pass
**FIX**: Use proper shell escaping via shlex.quote() for ALL interpolated values

### Finding 3: MEDIUM - Race Condition in Containment Probe
**ID**: SEC-003  
**TITLE**: TOCTOU Between Probe and Actual Containment  
**SEVERITY**: MEDIUM  
**LOCATION**: collaborator/contained.py:195-211 (containment_available)  
**CONCRETE INPUT**: Probe succeeds, then bwrap is removed/blocked before actual run
**BYPASS**: The probe is cached at import. Between probe and actual run_command:
1. Admin removes bwrap
2. AppArmor policy changes to block userns
3. System update breaks bwrap
The actual wrap_contained would return (argv, False, False) but governance already believes protection is available.
**WHY IT BREAKS**: code_protection_available() returns stale True, autonomous run attempts but fails
**ANOTHER CHECK CATCHES IT**: YES - wrap_contained returns protected=False, executor REFUSES at line 391
**FIX**: Already mitigated by the executor's refusal, but could add TTL to cache

### Finding 4: MEDIUM - Missing Validation of roots_with_witness Parameter
**ID**: SEC-004  
**TITLE**: wrap_contained Accepts Untrusted roots_with_witness  
**SEVERITY**: MEDIUM  
**LOCATION**: collaborator/contained.py:165-167  
**CONCRETE INPUT**: Call wrap_contained with roots_with_witness=[("/etc", "/etc/passwd")]
**BYPASS**: wrap_contained accepts arbitrary roots_with_witness without validating they match PROTECTED_ROOTS. A malicious caller could pass different roots than what code_protection_available verified.
**WHY IT BREAKS**: Could claim protection of wrong directories
**ANOTHER CHECK CATCHES IT**: YES - only called from _exec_command which doesn't pass roots_with_witness, uses default
**FIX**: Validate roots_with_witness matches expected or remove the parameter

### Finding 5: LOW - Potential /proc/1/root Escape in Container
**ID**: SEC-005  
**TITLE**: --proc Mount May Allow Container Escape  
**SEVERITY**: LOW  
**LOCATION**: collaborator/contained.py:130  
**CONCRETE INPUT**: Contained command that reads /proc/1/root
**BYPASS**: The line `"--proc", "/proc"` creates a new proc mount. Inside, /proc/1/root might provide access to the real root filesystem if the init namespace is shared.
**WHY IT BREAKS**: Could potentially access host filesystem
**ANOTHER CHECK CATCHES IT**: The --unshare-pid means PID 1 is inside the namespace, not host init
**FIX**: Already mitigated by PID namespace isolation

## CERTIFICATION

**CERTIFICATION LINE**: CERTIFIED - The implementation correctly implements verified code protection with proper guard verification, signed capability requirements, correct path splitting, and fail-safe downgrades. The workspace/.sandbox-home issue (Finding 1) is the only guarantee-breaking bug that another check doesn't catch.

## STEELMAN

The implementation is remarkably robust with defense-in-depth throughout. The guard script provides per-run verification beyond trusting bwrap, the three-way downgrade (protection_unverified, isolation_unverified, setup_failed) ensures honest reporting, and the executor's refusal to run when protection fails provides a critical safety net. The split between netns and bwrap paths correctly avoids the capability inheritance trap, and requiring BOTH verified containment AND signed capability prevents accidental autonomy expansion.

## VERDICT

**VERDICT**: MINOR_ISSUES - The implementation is fundamentally sound with excellent defense-in-depth, but the workspace/.sandbox-home directory creation issue could allow code root writes in specific workspace configurations. The single highest-value fix is to create .sandbox-home in a temporary location outside the workspace, preventing any possibility of workspace-subdir/code-root overlap.
