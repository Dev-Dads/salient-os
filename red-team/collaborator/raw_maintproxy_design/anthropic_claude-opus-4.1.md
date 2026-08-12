# maintproxy DESIGN panel: anthropic/claude-opus-4.1

_finish=stop seconds=121.5 cost=$0.4235 usage={'prompt_tokens': 21209, 'completion_tokens': 1405, 'total_tokens': 22614, 'cost': 0.42351, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.42351, 'upstream_inference_prompt_cost': 0.318135, 'upstream_inference_completions_cost': 0.105375}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## FINDINGS

### 1. CRITICAL: NETNS DEFAULT-DENY IS EDITABLE BY IN-NETNS SHELL
**ID:** NETNS-NFT-EDIT  
**SEVERITY:** CRITICAL  
**WHERE:** Privileged helper's nft rules installed IN THE NETNS  
**CONCRETE ATTACK:** The maintenance shell runs as bwrap mapped-root and holds CAP_NET_ADMIN inside its own netns. The nft ruleset with `policy drop` lives IN THAT SAME NETNS. The shell can simply:
```bash
nft flush ruleset  # or delete the drop rule
ip addr add 10.0.0.2/24 dev veth0
ip route add default via 10.0.0.1
# Now reach internet directly, bypassing proxy
```
**CAUGHT BY OTHER CONTROL:** NO - this is the load-bearing mediation  
**FIX:** ARCHITECTURAL REDESIGN - The default-deny MUST be un-editable from inside the netns. Options:
- Install egress filtering on the HOST side of the veth (outside the netns)
- Use a separate user namespace where the shell doesn't have CAP_NET_ADMIN
- Make the netns owned by a different user that the shell can't become

### 2. HIGH: PROXY BINDING REACHABLE FROM HOST
**ID:** PROXY-HOST-REACHABLE  
**SEVERITY:** HIGH  
**WHERE:** maintproxy.py binding to veth host-end IP  
**CONCRETE ATTACK:** The proxy binds to the veth host-end IP. This IP is in the host netns, potentially reachable by:
- Other host processes (not just the maintenance shell)
- Processes in other containers/namespaces on the same host
- Potentially LAN if the host forwards or bridges
**CAUGHT BY OTHER CONTROL:** Partial - host firewall may limit, but not guaranteed  
**FIX:** Bind to 127.0.0.1 in a separate netns, or use a UNIX socket with strict permissions

### 3. MEDIUM: PRIVILEGED HELPER EXEC-AFTER-DROP RACE
**ID:** HELPER-EXEC-RACE  
**SEVERITY:** MEDIUM  
**WHERE:** Privileged helper doing setns+privilege-drop+exec in one process  
**CONCRETE ATTACK:** The design mentions "parent/child split so there is no exec-after-drop in one process" but doesn't show the implementation. If the helper does setns() then setuid-drop then exec() in ONE process, there's a race where signals/ptrace could interfere between the drop and exec.  
**CAUGHT BY OTHER CONTROL:** Depends on NoNewPrivs and proper signal masking  
**FIX:** Fork after setns, drop privileges in parent, exec in child (proper parent/child split)

### 4. MEDIUM: EMPTY ALLOWLIST HANDLING UNSPECIFIED
**ID:** EMPTY-ALLOWLIST  
**SEVERITY:** MEDIUM  
**WHERE:** Wiring section - empty verified allowlist handling  
**CONCRETE ATTACK:** If the allowlist is empty (no net.maint caps granted), the design says "deny-all or fall-back-routeless" but doesn't specify which. If it falls back to routeless, that's correct. If it tries to run the proxy with an empty allowlist, the proxy might fail-open or crash.  
**CAUGHT BY OTHER CONTROL:** SALIENT_PROXY_VERIFIED check should catch a non-functional proxy  
**FIX:** Explicitly fall back to ROUTELESS when allowlist is empty, never start proxy

### 5. LOW: IPV6 DISABLE INCOMPLETE
**ID:** IPV6-PARTIAL  
**SEVERITY:** LOW  
**WHERE:** Helper disabling IPv6 via sysctl  
**CONCRETE ATTACK:** Setting `net.ipv6.conf.all.disable_ipv6=1` doesn't remove existing v6 addresses or prevent link-local. A determined attacker could:
- Use existing link-local addresses
- Manually configure v6 addresses before the sysctl takes effect
**CAUGHT BY OTHER CONTROL:** The nft rules don't allow v6 traffic anyway; proxy doesn't handle v6  
**FIX:** Also set disable_ipv6 for each interface specifically, remove v6 addresses explicitly

### 6. LOW: VETH NAMING COLLISION WINDOW
**ID:** VETH-NAME-RACE  
**SEVERITY:** LOW  
**WHERE:** Helper creating netns/veth with CSPRNG names  
**CONCRETE ATTACK:** Even with CSPRNG suffix, concurrent runs could theoretically collide on names, or an attacker could pre-create a netns with a guessed name.  
**CAUGHT BY OTHER CONTROL:** CSPRNG makes collision astronomically unlikely; SALIENT_PROXY_VERIFIED would catch wrong netns  
**FIX:** Check for name existence before creation, use atomic create-or-fail

## META-QUESTION: IS THIS WORTH BUILDING?

**NO.** The CRITICAL flaw (editable nft rules) breaks the entire security model. The maintenance shell can trivially bypass the proxy and allowlist by editing its own netns's nft rules. This is not a bug but an architectural flaw - you cannot enforce network policy from inside the controlled domain.

## STEELMAN

This design correctly identifies the need for controlled maintenance network access and thoughtfully reuses egress.py's safety mechanisms. The explicit CONNECT proxy approach avoids TLS termination complexity and the fail-closed verification tokens show security consciousness. The privilege separation between proxy and helper is sound in principle.

## VERDICT

**SERIOUS_FLAWS** - The netns default-deny living inside the controlled namespace makes the entire mediation bypassable. **Single highest-value change:** Do the alternative: keep maintenance network-less and vendor required artifacts, OR implement pasta/slirp4netns usermode networking where the network stack itself runs outside the sandbox's privilege domain.
