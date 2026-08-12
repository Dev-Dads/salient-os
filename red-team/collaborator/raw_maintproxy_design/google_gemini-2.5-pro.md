# maintproxy DESIGN panel: google/gemini-2.5-pro

_finish=stop seconds=81.6 cost=$0.1005 usage={'prompt_tokens': 20022, 'completion_tokens': 7551, 'total_tokens': 27573, 'cost': 0.1005375, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1005375, 'upstream_inference_prompt_cost': 0.0250275, 'upstream_inference_completions_cost': 0.07551}, 'completion_tokens_details': {'reasoning_tokens': 4278, 'image_tokens': 0, 'audio_tokens': 0}}_

As requested, here is an adversarial design review of the proposed maintenance egress proxy.

### A. Architectural Findings

---
**ID:** 0006-F1
**TITLE:** CRITICAL: Netns Firewall is Self-Administered, Allowing Trivial Bypass
**SEVERITY:** CRITICAL
**WHERE:** §2b Privileged helper (nft rule placement); §Open questions (the "crux")

**CONCRETE ATTACK OR FLAW:**
The entire mediation premise rests on the default-deny nft ruleset forcing traffic through the proxy. However, these rules are installed **inside the maintenance netns**, and the `bwrap` sandbox running inside that netns is given `CAP_NET_ADMIN` (via the `--map-root-user` that creates the userns).

`CAP_NET_ADMIN` within a network namespace grants full administrative control over that namespace's networking stack. A malicious or compromised maintenance payload can trivially bypass the proxy and all allowlist checks by simply disabling the firewall that is supposed to contain it.

Concrete bypass commands the shell can run:
1.  `nft flush ruleset inet` — This single command deletes all rules, removing the default-deny policy and the proxy exception. The shell now has a raw, unfiltered path to the host's veth peer IP.
2.  `nft delete rule inet filter output handle <N>` — A more targeted version of the above.
3.  `ip route add 1.1.1.1/32 dev <veth_if>` followed by `ip route add default via <host_veth_ip>` — While the host isn't forwarding (`ip_forward=0`), this demonstrates control over the routing table.

After disabling the firewall, the shell cannot reach the public internet directly (as the host isn't a router), but it **can** initiate connections to **any service running on the host** that is bound to `0.0.0.0` or the host's veth IP address. This is a critical SSRF and pivot vector, completely bypassing the `is_safe_public_ip` check and the destination allowlist. The mediation is theater.

**WHETHER ANOTHER CONTROL CATCHES IT:**
No. The `SALIENT_PROXY_VERIFIED` proof token's canary check (`direct egress is denied`) runs *before* the payload. It proves the firewall was active at T=0, but the payload can disable it at T=1. The check does not prevent a subsequent bypass.

**FIX-OR-REDESIGN:**
**FIX.** This is a critical but fixable flaw in placement, not concept. The enforcement boundary must be moved outside the container's administrative domain.

Instead of installing rules inside the guest netns, the privileged helper must install them on the **host** netns, specifically filtering traffic arriving *from* the sandbox's veth interface.

**Concrete Fix:**
1.  The helper creates the `veth_host` and `veth_guest` pair.
2.  On the **host**, it adds a rule to the `input` chain of the `filter` table:
    `nft add rule inet filter input iifname "veth_host" tcp dport != <proxy_port> drop`
    `nft add rule inet filter input iifname "veth_host" udp drop`
3.  This rule is outside the guest's `CAP_NET_ADMIN` scope. The guest can flush its own tables all it wants; the host's `input` chain will still drop any packet not destined for the proxy's specific TCP port. This robustly enforces the choke point.

---
**ID:** 0006-F2
**TITLE:** Privileged Helper is Vulnerable to Netns Path TOCTOU Race
**SEVERITY:** HIGH
**WHERE:** §2b Privileged helper

**CONCRETE ATTACK OR FLAW:**
The helper follows a "create, then use" pattern for the named network namespace:
1.  `ip netns add salient_route_u<uid>_<rand>` (creates `/run/netns/salient...`)
2.  ... (other setup)
3.  The child process does `setns()` on the path `/run/netns/salient...` before dropping privileges and exec'ing `bwrap`.

Between step 1 and step 3, a local attacker can win a race to replace the netns object on the filesystem. For example, an attacker could repeatedly `rm /run/netns/salient...` and symlink it to `/proc/1/ns/net` (the host's netns). If they win the race, the `setns()` call will place the maintenance shell in the **host network namespace**, not the isolated one. The `nft` rules would have been installed in the (now-orphaned) isolated netns, so the shell runs on the host with no firewall and direct internet access.

The CSPRNG-named netns makes prediction hard but does not eliminate the race window for a determined local attacker.

**WHETHER ANOTHER CONTROL CATCHES IT:**
The `SALIENT_PROXY_VERIFIED` token *should* catch this. Its canary check for direct egress would succeed, causing the guard to exit and not emit the token. This downgrades the impact from a silent bypass to a denial of service, but the race condition in the privileged helper remains a serious flaw.

**FIX-OR-REDESIGN:**
**FIX.** This is a classic TOCTOU (Time-of-Check, Time-of-Use) vulnerability with a standard kernel fix. The helper should use a file descriptor, not a path, to manage the namespace.

**Concrete Fix:**
1.  The privileged helper creates the netns as before.
2.  It then `open()`s the netns file (e.g., `/run/netns/salient...`) to get a stable file descriptor `netns_fd`.
3.  It passes this `netns_fd` to the child process (which inherits it across `fork`).
4.  The child calls `setns(netns_fd, CLONE_NEWNET)`. This operates on the kernel object referenced by the FD, which cannot be swapped out from under it by another process manipulating filesystem paths.

---
**ID:** 0006-F3
**TITLE:** Proxy Data Path is Vulnerable to CONNECT Request Smuggling
**SEVERITY:** HIGH
**WHERE:** §2a Unprivileged proxy (data path)

**CONCRETE ATTACK OR FLAW:**
The proxy design is described as: "reply `200 Connection established`; **splice raw bytes** bidirectionally". This is vulnerable to HTTP request smuggling via the CONNECT method.

A malicious client can send pipelined data immediately after its `CONNECT` request in the same TCP packet:
```
CONNECT allowlisted.example.com:443 HTTP/1.1\r\n
Host: allowlisted.example.com\r\n
\r\n
GET /smuggled-request-to-allowlisted-host HTTP/1.1\r\n
Host: allowlisted.example.com\r\n
...
```
The proxy will parse the `CONNECT` line, establish the outbound connection, send `200 OK` to the client, and then begin splicing. The kernel's TCP buffers will contain the `GET /smuggled...` request, which gets spliced directly to `allowlisted.example.com` *inside the TLS stream established by the client*.

This is not a flaw in the *destination* control, but it breaks the assumption of what a CONNECT proxy does. More severely, if the client connects to a non-TLS service on port 443 on an allowlisted host, this smuggled data becomes a cleartext request injection. The design's "where, not what" trade is predicated on the client performing a proper TLS handshake, which this bypasses.

**WHETHER ANOTHER CONTROL CATCHES IT:**
No. This happens at the application layer inside the proxy. The netns firewall, allowlist, and `is_safe_public_ip` checks are all passed correctly.

**FIX-OR-REDESIGN:**
**FIX.** The proxy implementation must be hardened. Before beginning to splice, the proxy must ensure that the client's socket buffer has been drained of any data sent beyond the initial `CONNECT` request headers. It should read from the client socket until it receives the `\r\n\r\n` marking the end of the headers and confirm no other data is present before sending the `200 OK` and starting the splice. Standard library HTTP servers often handle this, but a naive socket loop will not.

---
**ID:** 0006-F4
**TITLE:** Proxy Binding Scope is Underspecified, Risks Becoming a Host-Wide Proxy
**SEVERITY:** MEDIUM
**WHERE:** §2a Unprivileged proxy

**CONCRETE ATTACK OR FLAW:**
The design states the proxy is "bound to the veth host-end IP:port". This is the correct design. However, if implemented incorrectly by binding to `0.0.0.0:<proxyport>` or `[::]:<proxyport>`, the proxy becomes reachable by **any other process on the host**, not just the sandboxed shell.

This would create a new, host-wide egress path that other (potentially less-governed) processes could use. It would bypass their own network controls by tunneling through this maintenance proxy, leveraging its `net.maint` allowlist. This violates the principle of least privilege and the stated scope of governing *only* the maintenance shell.

**WHETHER ANOTHER CONTROL CATCHES IT:**
No. This is an implementation detail that, if gotten wrong, breaks the isolation model. The host's primary firewall would not block this intra-host traffic.

**FIX-OR-REDESIGN:**
**FIX.** This is a critical implementation requirement, not a design flaw. The ADR should be strengthened to mandate that the `maintproxy.py` listener socket **MUST** be bound exclusively to the IP address of the host-side veth interface for that specific run. This must be asserted in code and tested.

---
**ID:** 0006-F5
**TITLE:** "Where, Not What" Demotes Guarantees, Creating an Incoherent Control
**SEVERITY:** MEDIUM
**WHERE:** §Goal + non-goal; §The meta-question

**CONCRETE ATTACK OR FLAW:**
This is a strategic flaw, not a technical one. The SalienceOS egress posture, embodied by `egress.py`, is built on strong "what" controls: bounded requests/responses, hashed bodies, no redirects, etc. This is channel-integrity logging.

The proposed proxy explicitly trades this for "where, not what" by splicing raw bytes. While honest, this creates an incoherence:
*   A governed, autonomous `POST` via `egress.py` to `api.github.com` is heavily mediated.
*   A human-approved `curl` in the maintenance shell to `api.github.com` (if allowlisted for `net.maint`) is a completely uninspected, unbounded raw pipe.

An attacker who gains control of the maintenance shell (e.g., by deceiving the human operator) can now exfiltrate arbitrary amounts of data to an allowlisted host, completely bypassing the `MAX_POST_BODY` and other emission controls that `egress.py` enforces. The `net.maint` allowlist becomes a high-bandwidth, unmonitored exfiltration channel.

**WHETHER ANOTHER CONTROL CATCHES IT:**
No. This is a deliberate design trade. The human approval for the `run_command` is the intended gate, but it's a much weaker gate than the technical controls in `egress.py`.

**FIX-OR-REDESIGN:**
**REDESIGN (or accept the risk explicitly).** The panel must decide if this trade is acceptable.
1.  **Accept:** Acknowledge that `net.maint` capabilities grant a qualitatively different, higher-risk level of access than `net.get`/`net.post` caps. The human gate is the only control.
2.  **Redesign (for coherence):** Instead of a raw splice, the proxy could enforce `egress.py`-like bounds (e.g., total bytes transferred, connection duration) even without MITM. This adds complexity but restores some of the "what" control. For example, the proxy could use `splice()` but with a timeout and a counter, killing the connection if it exceeds a data cap. This would prevent unbounded exfiltration.

### B. Meta-Question: Is This Worth Building?

Yes, this architecture is worth building, **but only if the critical flaw (F1) is fixed.** The alternatives are worse:
*   **Keep maintenance network-less:** This is the most secure option but fails to meet the stated user need for fetching non-vendored artifacts, pushing maintenance workarounds onto operators.
*   **TLS-terminating (MITM) proxy:** This would provide "what" inspection but introduces a massive TCB (TLS libraries, HTTP parsers, certificate authority management) and breaks client-side certificate pinning, making it *less* secure for many modern tools (`git`, `rustup`). The complexity and risk are not justified for this use case.
*   **pasta/slirp usermode networking:** These are complex dependencies that solve the "how to get packets out without root" problem, but they do not solve the *policy enforcement* problem. You would still need to layer a firewalling mechanism on top, leading back to a similar set of problems but with a more opaque and less standard networking stack.

The proposed veth + host-side nft + unprivileged proxy model uses standard, well-understood Linux kernel features to create a robust, low-TCB choke point. It provides the necessary capability with controls that are significantly better than nothing, and far less complex than a full MITM proxy.

### Steelman & Verdict

**STEELMAN:** The proposed design provides a crucial capability—controlled network access for human maintenance—by correctly separating the minimal, fixed-argument privileged setup from the complex, unprivileged data path. It reuses the system's core `egress.py` logic for destination validation and employs a strong, behavior-based proof-of-confinement token (`SALIENT_PROXY_VERIFIED`) to ensure it fails closed, representing a pragmatic and security-conscious evolution from the current network-less state.

**VERDICT:** **SERIOUS_FLAWS.** The architecture is fundamentally broken as-is because the sandboxed shell can disable its own firewall, but this and other flaws are eminently fixable.

**Single highest-value change:** Move the default-deny nftables rules from inside the guest netns to the host's `input` chain, filtering on the host-side veth interface (`iifname "veth_host"`), to make them un-editable by the sandboxed process.
