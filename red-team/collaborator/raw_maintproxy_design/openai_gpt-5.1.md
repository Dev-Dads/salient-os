# maintproxy DESIGN panel: openai/gpt-5.1

_finish=stop seconds=109.6 cost=$0.0946 usage={'prompt_tokens': 18044, 'completion_tokens': 7201, 'total_tokens': 25245, 'cost': 0.094565, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.094565, 'upstream_inference_prompt_cost': 0.022555, 'upstream_inference_completions_cost': 0.07201}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID 1 / NETNS RULES ARE EDITABLE BY THE SHELL  
SEVERITY: CRITICAL  
WHERE: 2b (privileged helper netns + nft), “Open questions: … CAP_NET_ADMIN in its own netns”  

CONCRETE ATTACK OR FLAW  
- As written, the maintenance shell is:
  - in a separate network namespace prepared by the root helper, and  
  - then further wrapped in bwrap’s userns (mapped-root inside the netns), which gives it CAP_NET_ADMIN **inside that netns**.  
- The nft default-drop rules and any routes/interfaces the helper installs live in that same netns.  
- A mapped-root shell with CAP_NET_ADMIN in the netns can:
  - `nft flush ruleset` or delete the drop chain/table.  
  - `ip addr add` or change addresses on the veth or `lo`.  
  - `ip link add` a new interface type that doesn’t traverse the proxied veth.  
  - `ip route add default via <some gateway>` if any L2 path exists (including later-added host veths).  
  - Or simply disable nft (set policy accept) and use raw sockets.  
- Once nft OUTPUT policy is reset to accept, **any binary that ignores HTTPS_PROXY** (or one that the model/human explicitly drives with `curl --noproxy '*'/nc`) can connect directly to arbitrary remote IPs. The proxy and its allowlist are bypassed entirely.  
- This is not an obscure trick: CAP_NET_ADMIN in your own netns means full control of firewall and routing inside that namespace. That’s what it is for.

WHETHER ANOTHER CONTROL CATCHES IT  
- The “in-sandbox guard” (SALIENT_PROXY_VERIFIED) proves that at guard time:
  - A canary direct connect to some external IP fails, and  
  - The proxy is reachable.  
- It never proves that nft rules remain intact **after** the shell starts. Nothing pins rules against later change, and the guard does not re-check.  
- There is no second layer of enforcement on the host side (e.g., nft on the host side of the veth or host routing) that would still block arbitrary egress once the netns firewall is removed.  
- Therefore **no other control catches this**. It is a structural bypass of the entire mediation.

FIX-OR-REDESIGN  
You must remove CAP_NET_ADMIN from the shell’s netns, and/or move the enforcement point out of its reach:

Option A (strongly preferred): host-owned enforcement  
- Put the maint shell in a netns where it **does not** have CAP_NET_ADMIN; only the helper’s process does:
  - Do not run a nested userns inside the helper’s netns, or arrange the bwrap mapping so the process is not “root” in the netns. You may need a different privilege model than “mapped-root in netns”.  
- Move the DROP+allow-to-proxy rules to the host side of the veth, e.g.:
  - host namespace nft: a dedicated table/chain binding on the host interface of the veth that only allows packets from that veth peer to `daddr=<proxy-ip>, dport=<proxy-port>`, drop everything else.  
  - As long as the shell has no CAP_NET_ADMIN in the host netns, it cannot touch these rules.  
- In this design the netns’s own iptables/nft rules and routes are irrelevant for egress control; the host-side nft remains the choke-point.

Option B: separate “owner” of the netns  
- Create the netns, apply nft and routes, and then drop into a **non-root user** inside that netns without CAP_NET_ADMIN.  
- That means abandoning “mapped-root with CAP_NET_ADMIN” semantics for maintenance shells, or isolating that mapping in a way that does not grant netns caps (e.g., unshare userns differently, or don’t map uid 0 at all).  

If CAP_NET_ADMIN must remain in the shell, then the default-deny inside the netns is theatrics; you instead must enforce *only* on host side and treat everything in the netns as adversarial.


---

ID 2 / IN-NETNS Nftables “POLICY DROP” ISN’T A GUARANTEE  
SEVERITY: HIGH  
WHERE: 2b (helper nft rules: “policy drop on output … accept only … proxy+established/related+loopback”)  

CONCRETE ATTACK OR FLAW  
- Even if you fix CAP_NET_ADMIN, using **only** nft in the netns as your guard is inherently fragile:
  - The maintenance process still has full process control in that netns; any kernel bug or nftables mis-configuration there is a single point of failure.  
  - `conntrack`/`established,related` semantics can be abused if you later permit anything other than strict connect-to-proxy. For example, any conntrack-based acceptance rule that isn’t pinned to the right tuples can be turned into a covert channel (e.g., ICMP or keep-alive traffic piggyback).  
  - Future enhancement pressure (“allow port 53 for local DNS in the netns” or “let it talk to some local host service”) will invariably push more rules into this netns filter, and those will be under the shell’s policy scope if CAP_NET_ADMIN remains.  

WHETHER ANOTHER CONTROL CATCHES IT  
- The proposal has no host-level enforcement nor ip_forwarding; everything is pushed into the “private” netns. No second layer catches errors here.  
- The guard can detect gross failure (direct connect works at guard time) but not rule bugs that are only exploitable under certain traffic patterns or after some manipulation.  

FIX-OR-REDESIGN  
- Treat the enforcement plane as part of the host TCB, not the per-shell netns:
  - Install nft on the host veth side in a host namespace-owned chain; only allow `oifname==veth-host-end && daddr==<proxy-ip> && dport==<proxy-port>`.  
  - Inside the netns, use at most minimal rules (e.g., allow lo) and treat them as non-security-critical.  
- Simplify the rules in the host table to the absolute minimum; avoid `ct state established,related` where you don’t need it (you typically don’t: unidirectional from netns→proxy; reply traffic is matched by connection tracking internal to netfilter).  
- Reserve a dedicated nft table+chain for this veth; helper only adds/removes that table, never calls `flush ruleset`, and the rest of the host firewall remains unchanged.


---

ID 3 / USERNS + NETNS INTERACTION NOT FULLY SPECIFIED  
SEVERITY: HIGH  
WHERE: Junction of maintain.py (bwrap sandbox) and the helper (“drop --unshare-net from bwrap”; helper does setns then privilege drop)  

CONCRETE ATTACK OR FLAW  
- The ADR banks on the existing maintain.py model:  
  - bwrap creates a new userns mapping the collaborator uid to root (inside) and drops caps inside that userns.  
- The new plan changes the network setup:
  - Helper (root) creates a netns + veth, then `setns()` into that netns and **then** execs bwrap, dropping capabilities etc.  
- It is not clearly specified:
  - Whether bwrap will create a new userns nested inside the helper’s (already-netns-isolated) process, or reuse the helper’s userns.  
  - What that implies for capabilities in the netns:  
    - In practice, a new userns with uid 0 mapped to the caller’s uid usually gives CAP_NET_ADMIN within that netns. That’s precisely how `unshare --map-root-user --net` works.  
- The design assumes the bwrap guard (caps all-zero, NoNewPrivs=1) pertains to **mount ns and classic caps**, but does not discuss netns capability boundary:
  - If the userns created by bwrap is *inside* the host root’s userns, the kernel’s capability rules are non-trivial; some operations in the outer netns may still be allowed.  
  - It is easy to get into a state where the process has no *bounding* capabilities from the bwrap’s point of view but still has per-netns caps or can influence network devices created by the outer helper.

WHETHER ANOTHER CONTROL CATCHES IT  
- maintain.py’s guard checks `/proc/self/status` CapEff/CapPrm/CapBnd/CapAmb and NoNewPrivs — but those are the capabilities in the current user+pid namespace, not a per-netns map; Linux’ capability evaluation across userns/netns isn’t fully captured by that simple check.  
- There is no explicit proof that “CapEff/Prm/Bnd all zero” implies **no CAP_NET_ADMIN in this netns** after the helper’s manipulations.  

FIX-OR-REDESIGN  
- Design the sequence explicitly and test under different kernels:  
  - Root helper: host userns, create netns+veth.  
  - Drop into a **non-root uid** inside that netns before calling bwrap; avoid creating another userns if possible. This way, the maintenance process is never root in that netns → no CAP_NET_ADMIN.  
- Alternatively, if you must keep the bwrap userns mapping, explicitly confirm at runtime (in the guard) that:
  - `capsh --print` or equivalent within the netns sees no CAP_NET_ADMIN in any set;  
  - Attempting `ip link set` or `nft` commands fail with EPERM.  
- Put that into the SALIENT_PROXY_VERIFIED guard: treat “able to do a netns-admin operation” as a hard failure and fall back to routeless.


---

ID 4 / ROOT HELPER EXEC-PATH AND SUDOERS SURFACE  
SEVERITY: MEDIUM  
WHERE: 2b (Privileged helper; “sudo -n … fixed-arg, model-input-free”; egressobserver posture)  

CONCRETE ATTACK OR FLAW  
- New SUID-style boundary: `sudo -n <helper> <argv>` with:
  - A root binary that creates netns+veth, manipulates nft, and then `exec`s a payload within that context.  
- Attack surfaces to consider:
  - Sudoers misconfiguration: if the `sudoers` rule is not restricted to an exact path and specific arg pattern, a compromised collaborator could drive root to run helper with attacker-chosen arguments (e.g. run arbitrary command as root, or create veth with attacker-chosen host addresses).  
  - Arg injection: any point where helper takes strings that came indirectly from the model/human shell (netns name, veth names, ip addresses) before privilege drop. The doc says “fixed-arg, model-input-free,” but the actual *parameters* are not frozen in the ADR.  
  - Race on `/run/netns/<name>`: if helper uses a predictable name or doesn’t open+O_EXCL the namespace file descriptor securely, an attacker process might create or symlink `/run/netns/<name>` between helper’s create and setns, leading the helper to setns into a different netns.  
  - Reserved-name collision: concurrent calls with the same uid and random suffix collision could re-use nft tables or veth names; if teardown deletes “its” table with a too-broad match, it may tear down other firewall state.  

WHETHER ANOTHER CONTROL CATCHES IT  
- The ADR states “CSPRNG suffix” and “never flush ruleset,” which mitigates name collision and global firewall damage. But:  
  - There is no explicit requirement that the helper **open()s the netns fd and setns()es that** rather than re-looking up by name (subject to symlink race).  
  - The monotonic route ladder only works if proxied-route is honestly constructed; a compromised helper could quietly fail-open by skipping nft installation.  

FIX-OR-REDESIGN  
- Make the helper contract precise:
  - Sudoers: lock it to a single absolute path, no environment, no extra arguments; use `sudo -n /usr/lib/salient/maintnet-helper` with **no parameters** coming from the model; all configuration is via root-owned config or environment set by the collaborator binary before calling sudo.  
  - Helper must:  
    - create the netns, immediately `open("/proc/self/ns/net")` after unshare, and keep fd; never rely on `/run/netns/<name>` for setns.  
    - allocate nft tables with unique names and match them exactly on teardown; never call `flush ruleset` and never delete any table not its own.  
  - After finishing netns+veth+nft setup, drop all caps (including CAP_NET_ADMIN) before execing the unprivileged proxy or bwrap; confirm with `prctl(NO_NEW_PRIVS)` and cap bounding sets.  
- Treat any deviation as fatal: helper must return “proxied unavailable,” and the system falls back to routeless.  

Is a root helper acceptable vs network-less? Only if this contract is respected and code is small, audited C or minimal Rust; a Python helper under sudo is a significant footgun unless you’re extremely disciplined about the environment and modules.


---

ID 5 / PROXY ACCESSIBLE FROM HOST OR LAN  
SEVERITY: MEDIUM  
WHERE: 2a (maintproxy binding), 2b (veth config)  

CONCRETE ATTACK OR FLAW  
- The design says: “proxy bound to the veth host-end IP:port, started per-run.” But it does not explicitly state:
  - That the proxy only binds to the specific /31 host address, not 0.0.0.0.  
  - That host routing/nft prevents any other host process or LAN machine from reaching that IP.  
- If the host veth interface is inadvertently routable from the rest of the host or LAN:
  - Any process on the host (malicious user, compromised service) could use the proxy as a “blessed” CONNECT path to egress with the maintenance allowlist.  
  - If your DNS safety+allowlist isn’t careful, this is effectively a second egress channel for other code running on the machine.  

WHETHER ANOTHER CONTROL CATCHES IT  
- Nothing in the ADR guarantees `SO_BINDTODEVICE` or similar, nor host firewall rules to confine access to the veth from only the target netns.  
- `is_safe_public_ip` only governs **outbound** target; it does not constrain who can reach the proxy inbound.  

FIX-OR-REDESIGN  
- Bind the proxy narrowly and fence it:  
  - Bind `maintproxy` to the **link-local veth host IP** and set up host nft rules that drop packets to that IP from any interface other than the veth peer.  
  - Alternatively, use a UNIX domain socket in the host ns and bind that into the netns as a special file; this innately prevents other hosts or unprivileged processes from accessing it via IP.  
- Verification: helper should include a self-test that “host default netns cannot connect to the proxy address,” and refuse proxied mode if this fails.  


---

ID 6 / CONNECT PARSING + AUTHORITY CONFUSION SURFACE  
SEVERITY: MEDIUM  
WHERE: 2a maintproxy CONNECT handling  

CONCRETE ATTACK OR FLAW  
- The design says:
  - Only `CONNECT host:443 HTTP/1.1` is accepted; `h = egress.canonical_host("https://" + host)`; `h in allowlist`.  
- Potential flaws:
  - CONNECT request line parsing: if not handled strictly, you can get:
    - “smuggling” or header-injection via embedded spaces or control characters in the `host` token. Upstream code partially defends against control chars, but you have to replicate that logic precisely in CONNECT parsing.  
  - Authority confusion:
    - CONNECT’s target is an authority-form (`host:port`), but you are feeding `"https://"+host` into `canonical_host`, and rely on canonical_host to reject IP-literals, userinfo, ports, non-https. As long as its contract is respected, that’s okay, but your proxy must never treat the raw CONNECT host as authoritative.  

WHETHER ANOTHER CONTROL CATCHES IT  
- `canonical_host` already rejects:  
  - Non-HTTPS schemes, userinfo, bad characters, IPv6 literals, dotless, ports != 443.  
- That means if you **only** depend on canonical_host and never use raw host for connect or audit, many confusion attacks are blocked.  

FIX-OR-REDESIGN  
- Implement CONNECT parsing as:
  - strict split on SP: `method`, `request-target`, `http-version`, rejecting any `request-target` with control characters or anything not matching `^[A-Za-z0-9.-]+:443$`.  
  - Then feed only `request-target.split(':',1)[0]` into `canonical_host("https://"+host)`. If canonical_host returns None, 403.  
- Disallow any extra path/absolute-form; no `CONNECT http://host:443 HTTP/1.1`.  
- After `200 Connection established`, do not interpret any subsequent bytes; just splice. Have read/write timeouts and cap on total connections and per-connection lifetime.


---

ID 7 / DNS RESOLVE→PIN RACE IS MOSTLY ADDRESSED BUT NEEDS STRICTNESS  
SEVERITY: LOW  
WHERE: 2a maintproxy (resolve and is_safe_public_ip), reuse of egress._resolve  

CONCRETE ATTACK OR FLAW  
- The design reuses egress.py’s contract:
  - `getaddrinfo` → choose first `is_safe_public_ip` → connect to that IP, keep `host` for SNI and cert check.  
- Attacker possibilities:
  - DNS rebind between `getaddrinfo` and `socket.create_connection` is mostly moot here because you are connecting to the pinned IP, not re-resolving.  
  - However, if you iterate addresses incorrectly (e.g., connect to an unsafe IP on failure code-paths, or if you choose v6 vs v4 wrongly), you could see subtle bypasses (e.g., NAT64 ranges marked is_global).  

WHETHER ANOTHER CONTROL CATCHES IT  
- `is_safe_public_ip` is reasonably strict: rejects private, loopback, link-local, multicast, reserved, unspecified, CGNAT, NAT64.  
- This is already better than most.  

FIX-OR-REDESIGN  
- Ensure maintproxy uses the **exact same** `_resolve` and `is_safe_public_ip` functions as egress, not re-implementations.  
- In the proxy code, fail closed if no safe IP is found; never attempt a second resolution or fallback to an unsafe address.  
- Document that DNS races are treated as out-of-scope beyond resolve-once-pin; do not add “retry with next IP on connection failure” unless you re-run is_safe_public_ip each time.


---

ID 8 / SSRF INTO HOST OR VETH VIA CONNECT  
SEVERITY: MEDIUM  
WHERE: 2a maintproxy (safe public IP only)  

CONCRETE ATTACK OR FLAW  
- If the allowlist ever contains the proxy’s own host IP, loopback, or a name that resolves to the veth subnet:
  - A CONNECT to such a host could create a tunnel back into the host — the proxy will open a TCP connection from host netns to that IP. If that IP is e.g. 127.0.0.1 or a private address accessible from the host, this becomes an SSRF channel to internal services.  
- `is_safe_public_ip` aims to prevent this by forbidding non-global addresses; that’s good – but if a DNS name for an allowlisted host sometimes returns a private IP (split-horizon DNS), your policy is to deny it, which may surprise operators.  

WHETHER ANOTHER CONTROL CATCHES IT  
- `is_safe_public_ip` does catch local/veth/private hosts, loopbacks, metadata, NAT64, CGNAT.  
- But **only if** maintproxy uses it strictly and never has exceptions for “maintenance” hosts.  

FIX-OR-REDESIGN  
- For maintproxy specifically, forbid:
  - Any host whose resolved IP is not `is_safe_public_ip`. No exceptions.  
- Add explicit tests to egress.py and maintproxy for:
  - IPv4-mapped v6, v6 private ranges, NAT64, and addresses that might be used for host-local proxies; treat all as unsafe.  


---

ID 9 / CONNECT TO PROXY-SELF OR OTHER SPECIAL HOSTS  
SEVERITY: MEDIUM  
WHERE: 2a maintproxy, 2b netns  

CONCRETE ATTACK OR FLAW  
- If the allowlist ever lists:
  - the proxy’s own host veth IP,  
  - the host’s default IP / hostname,  
  - or something that resolves to 127.0.0.1 or 0.0.0.0 via DNS misconfiguration,  
then the proxy will open a TCP connection from host netns back to itself or to another local service. This could allow:
  - Loopback CONNECT loops (proxy talks to itself, causing resource exhaustion or weird state).  
  - SSRF into host-local services running on 127.0.0.1:443.  

WHETHER ANOTHER CONTROL CATCHES IT  
- Again, `is_safe_public_ip` should reject loopback.  
- But if a host resolves to the veth’s host-side address (e.g. operator uses a DNS name for “myproxy.local” incorrectly), you can end up tunneling back into the proxy or to some service bound on that IP.  

FIX-OR-REDESIGN  
- Augment `is_safe_public_ip` in the proxy with a check against:
  - The known addresses of the local host and all veth addresses created for the maint path; reject any such address even if ipaddress considers it “global”.  
- Consider a “no internal AS” list: if you know your organization’s public IP ranges that should never be reachable from maint, encode them as unsafe.  


---

ID 10 / ALLOWLIST DERIVATION IS SOUND BUT NEEDS TIGHT MATCHING  
SEVERITY: LOW  
WHERE: 2c (cap derivation), egress.canonical_host  

CONCRETE ATTACK OR FLAW  
- The allowlist is derived from signed caps: `net.maint:<canonical-host>`, with the same canonical_host logic as egress, and “model never chooses it.”  
- Residual risks:
  - If maintenance caps are generated from a different canonicalization routine than egress.canonical_host, there can be mismatches (confusables, punycode differences, trailing dot).  
  - If the allowlist match in maintproxy is case-insensitive or normalizes differently, an attacker might get a host authorized that differs under some encodings.  

WHETHER ANOTHER CONTROL CATCHES IT  
- canonical_host is strict and deterministic; if both the cap generator and maintproxy use **exactly** that function, you’re okay. The ADR states that, but the implementation must follow.  

FIX-OR-REDESIGN  
- Keep allowlist keys and maint hosts strictly:  
  - Allowlist entries must be raw strings from canonical_host.  
  - Proxy compares exactly (`==`); no further normalization.  
- For empty allowlist:
  - Must mean “deny all”; design says “PROXIED_ROUTE only if non-empty verified allowlist; else ROUTELESS.” Keep that invariant in code (and test).  

---

ID 11 / DESTINATION-ALLOWLIST DEMOTES CONTENT GUARANTEES ON MAINT PATH  
SEVERITY: MEDIUM  
WHERE: 4 (meta behavior; “where not what”)  

CONCRETE ATTACK OR FLAW  
- Current egress.py is a content-aware channel:
  - Methods restricted, request line size capped, body size bounded, no arbitrary streaming, no MITM but strong logging and exfil bounds.  
- The proposed maint proxy:
  - For allowlisted hosts, becomes an unrestricted bidirectional stream (aside from connect/idle limits): human shell can:
    - `curl --proxy $HTTPS_PROXY https://allowlisted.example` and POST hundreds of MB if the proxy doesn’t set a per-connection byte cap.  
    - SSH over CONNECT (if the dest uses 443 but speaks SSH, or via pluggable tunnels), bypassing method and payload checks.  
- This is a **semantic demotion** vs the rest of the system: you now have a path where the agent (via human shell) can emit arbitrary data to certain hosts without the egress.py constraints. It’s stated as an intended trade, but changes your security story: “governed destinations but ungovened content” for maintenance.  

WHETHER ANOTHER CONTROL CATCHES IT  
- None. This is the feature. The only mitigation is:
  - Caps are only for specific hosts.  
  - Human-approved and logging.  
- But it means you cannot continue to claim, globally, that “all egress payloads are bounded and audited via egress.py” — maintenance is an exception.  

FIX-OR-REDESIGN  
- Be explicit in documentation and threat model:
  - Maintenance shells are *human-governed* and may exfiltrate arbitrary data to allowlisted hosts; this is outside the strict content constraints of egress.py.  
- If you want stronger control:
  - Implement a second path: TLS-terminating MITM for maintenance, with method/body quotas and logging — but that’s exactly what you previously found too invasive.  
- As a compromise, enforce **per-connection and per-session byte and time caps** at the proxy level:
  - E.g., total bytes per maintenance session per host; idle timeout; max duration. This limits exfil bandwidth even if content is opaque.


---

ID 12 / FAIL-CLOSED LADDER CAN BE LIED TO BY HELPER OR PROXY  
SEVERITY: HIGH  
WHERE: 2c (route rung), “maintproxy_available()”, SALIENT_PROXY_VERIFIED guard  

CONCRETE ATTACK OR FLAW  
- Ladder logic:
  - PROXIED_ROUTE if: opted-in + maintproxy_available() + non-empty allowlist.  
  - Else ROUTELESS; then UNISOLATED.  
- Maintproxy_available is described as:
  - A “verified host probe (helper reachable + netns/veth/nft installable + proxy startable + in-sandbox guard passes), cached positive-only.”  
- Risks:
  - If the helper is compromised or mis-implemented, it might:
    - Claim success without actually installing nft rules or netns, or with a misconfigured veth;  
    - Still manage to produce a SALIENT_PROXY_VERIFIED token (e.g., stub guard script that simulates a failed direct connect).  
  - Cached positive probe:
    - If you cache that proxied route is “available,” then subsequent runs might skip re-running the guard, even if the environment changed (nft changed by admin, kernel upgrade, different host state).  
  - The POSITIVE PROOF TOKEN checks:
    - Only one canary IP, once. If that IP’s routing is manipulated (e.g., host static route stolen, BGP injection on corp networks), the “direct connect fails” check may pass even when there is a real route to other IPs.  

WHETHER ANOTHER CONTROL CATCHES IT  
- There are no cross-checks from another component:
  - The agent trusts the helper and the in-sandbox guard to be honest.  
- However, these are part of the TCB explicitly; this is not an external attacker but a bug/compromise of trusted code.  

FIX-OR-REDESIGN  
- Strengthen SALIENT_PROXY_VERIFIED semantics:
  - Check more than one canary IP and possibly multiple ASes; failing all proves more strongly that default route is blocked.  
  - Additionally, try a direct connect to an IP that is **known to be reachable** from the host default netns (e.g., `8.8.8.8:443`) and confirm it fails from the maint ns; that’s closer to a functional assertion.  
- Disable positive-only caching:
  - maintproxy_available should be per-run or at least revalidated frequently; environment can change. Use lightweight fast checks if you are worried about overhead.  
- Treat any inconsistency (e.g., direct connect unexpectedly succeeding) as a signal to **fall back to routeless** and mark proxied path as unavailable until re-probed on a fresh host state restart.


---

ID 13 / TEARDOWN LEAKS OR GHOST NETNS STATE  
SEVERITY: MEDIUM  
WHERE: 2b helper teardown, 5 (teardown leaves a netns/veth/rule for the next run)  

CONCRETE ATTACK OR FLAW  
- Helper “deletes only its own netns/veth/nft table. Never flush ruleset.” But:
  - If teardown is not robust (e.g., crash after creating veth but before nft table removal, or vice versa), you can leave:
    - A netns bound to a file in /run/netns with partially applied rules.  
    - A veth with host-side nft referencing a now-nonexistent peer.  
  - A later run might attach to a stale netns (if naming scheme collides) or rely on default host firewall that has leftover rules pointing at wrong devices.  

WHETHER ANOTHER CONTROL CATCHES IT  
- No explicit “garbage collector” for stale netns/veth/tables is described.  
- SALIENT_PROXY_VERIFIED guard only checks within the current run’s context; it doesn’t detect environment leaks, only that the current ns is at least default-deny at guard time.  

FIX-OR-REDESIGN  
- Give each run a unique opaque ID, and:
  - The helper must maintain a small host-side registry (root-owned dir with a single file per active run), recording veth and nft table names.  
  - On startup, helper does a cleanup pass:
    - For any entries not associated with an active process, tear down netns, veth, nft table safely.  
- During teardown, handle partial failures gracefully; on error, mark the host as “proxied unavailable” until next successful cleanup.  
- Ensure netns objects are referred to by open fds rather than by name when possible; delete /run/netns entries last.


---

META-QUESTION: IS THIS WORTH BUILDING VS KEEPING MAINTENANCE NETWORK-LESS?  

Given the CRITICAL architectural issue (CAP_NET_ADMIN in the shell’s netns with rules in that same ns), as well as the necessary complexity of a root helper + veth + host nft + proxy, the current design is **not** worth building as-is. Even if you fix the CAP_NET_ADMIN placement and move enforcement to the host, you are still accepting a significantly expanded TCB (root helper, proxy, more iptables/nft complexity) to enable a niche capability (online driver / binary fetch for human maintenance) that can be handled with safer alternatives most of the time (vendored artifacts, out-of-band maintenance).  

In contrast, alternatives:  
- Keep maintenance `run_command` **network-less** plus:
  - Expand the use of vendored or centrally fetched artifacts (driver bundles, package mirrors) via existing egress.py with content controls.  
  - Where absolutely necessary, use **user-mode networking** (pasta/slirp4netns) from inside the sandbox with the same egress.py gateway (or with strictly host-local NAT + egress filtering) — these approaches give a narrow, easier-to-audit data path without building a new root helper and complex netns choreography.  
- If you really need interactive networked maintenance, a simpler TLS-terminating proxy with explicit policy and logging (at egress.py’s level of control) might be a better, if politically harder, investment: fewer moving parts, more predictable semantics.

So: as designed, with the netns-default-deny crux unresolved, this is *not* worth building. If you *do* invest, the first and highest-value change is to relocate enforcement to the host side of the veth and ensure the shell has **no CAP_NET_ADMIN in any namespace that matters**.

---

STEELMAN (best case for the design)  
- Giving the maintenance shell controlled, proxied network access via a destination-allowlist and reusing egress.py’s canonical_host + is_safe_public_ip logic is conceptually sound: it keeps origin control and avoids a second unmediated path. The design correctly identifies that an explicit CONNECT proxy plus netns containment avoids many routing and packet-filtering pathologies inherent in ip_forward/masquerade designs.  
- With host-side nft enforcement, no CAP_NET_ADMIN in the shell, and a small, hardened root helper, you could provide operators a practical way to do online maintenance (driver downloads, vendor-only binaries) without having to fully relax the system’s default-defensive stance on outbound network use.

VERDICT: SERIOUS_FLAWS — as specified, the CRITICAL CAP_NET_ADMIN + in-netns nft placement makes the entire mediation bypassable by a straightforward attacker with shell access. The single highest-value change is: **move all enforcement to the host side of the veth and ensure the maintenance shell never has CAP_NET_ADMIN in any netns whose packets can reach the wire.** Absent that redesign, you are better off doing the alternative: keep maintenance network-less and rely on vendored artifacts or tightly supervised, separate maintenance channels.
