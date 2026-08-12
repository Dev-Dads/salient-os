# maintproxy DESIGN panel: qwen/qwen3-max

_finish=stop seconds=41.5 cost=$0.0235 usage={'prompt_tokens': 18216, 'completion_tokens': 2370, 'total_tokens': 20586, 'cost': 0.02345148, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.02345148, 'upstream_inference_prompt_cost': 0.01420848, 'upstream_inference_completions_cost': 0.009243}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

**ID / TITLE / SEVERITY / WHERE / CONCRETE ATTACK OR FLAW / WHETHER ANOTHER CONTROL CATCHES IT / FIX-OR-REDESIGN**

---

### **1. NETNS DEFAULT-DENY EDITABILITY BY MAPPED-ROOT**
- **SEVERITY**: CRITICAL  
- **WHERE**: Privileged helper (2b) + netns default-deny ruleset  
- **FLAW**: The maintenance shell runs as *mapped-root inside its own netns* and holds `CAP_NET_ADMIN` **within that netns**. The nft default-deny ruleset lives **inside the same netns**, so the shell can trivially:
  - `nft flush ruleset`
  - Delete the drop rule
  - Add a default route (`ip route add default via <host-veth-ip>`)
  - Re-address the veth interface or bring up a new one
  - Reach the internet **directly**, bypassing the proxy and allowlist entirely.

  This breaks the entire mediation model: the “default-deny” is enforced only by voluntary compliance of an untrusted, privileged-in-netns payload.

- **ANOTHER CONTROL?** No. The in-sandbox guard checks that a *direct connect fails*, but if the shell deletes the rule **after** the guard passes and **before** it does real work (e.g., via backgrounded `nft flush &`), the guard is blind. The guard runs **once before exec**, not continuously.

- **FIX/REDESIGN**: Move egress filtering **outside the sandbox’s control**:
  - Install nft rules on the **host side** of the veth pair (in the **host netns**) to drop all traffic from the veth peer except to the proxy IP:port.
  - Or run the sandbox in a netns owned by a **different user namespace** (not mapped-root), so it lacks `CAP_NET_ADMIN`.
  - Or use a **separate, host-owned netns** with fixed rules that the sandbox merely joins (no creation/modification rights).

  **Without this, the design is fundamentally unsound.**

---

### **2. PRIVILEGED HELPER TCB — RACE + INJECTION SURFACE**
- **SEVERITY**: HIGH  
- **WHERE**: Privileged helper (2b) + sudo invocation  
- **FLAW**: 
  - **Race on `/run/netns/<name>`**: Between netns creation and `setns+exec`, a malicious concurrent process (or symlink attack if name predictable) could replace or interfere with the netns file.
  - **CSPRNG name collision**: Low probability, but if two runs collide, teardown of one could break the other.
  - **sudo -n <helper> <argv>**: If `argv` is not strictly fixed and validated, it could be influenced by environment or timing (e.g., via `PATH` if not absolute). The ADR claims “fixed-arg, model-input-free”, but implementation must guarantee this at the sudoers level (`/etc/sudoers` must hardcode full path + args).
  - **Privilege drop ordering**: If `setns` happens in the same process that drops privs, there’s a window where root has access to both namespaces.

- **ANOTHER CONTROL?** Partially. Parent/child split helps, but race on netns bind-mount remains. The positive proof token doesn’t cover this phase.

- **FIX/REDESIGN**: 
  - Use `unshare(2)` + `setns(2)` in a **dedicated, short-lived fork** that never touches untrusted data.
  - Bind-mount netns with `O_NOFOLLOW | O_CLOEXEC`.
  - Enforce in sudoers: `user ALL=(root) NOPASSWD: /full/path/to/helper --run-id=[a-f0-9]{32}`, no wildcards.
  - Prefer **pre-created, host-managed netns templates** over per-run creation.

---

### **3. PROXY DATA PATH — SSRF + REBIND + PARSING**
- **SEVERITY**: HIGH  
- **WHERE**: Unprivileged proxy (2a)  
- **FLAW**:
  - **SSRF to host services**: A `CONNECT` to the **host veth IP** (e.g., `169.254.0.1:22`) would let the shell reach **any service on the host** bound to that IP or `0.0.0.0`. The proxy binds to the veth IP, but if it listens on `0.0.0.0` or doesn’t validate the destination isn’t localhost/veth-subnet, this is a pivot.
  - **DNS rebind race**: Between `getaddrinfo` and `create_connection`, DNS could rebind to a private IP. The code uses “resolve-once-pin”, but **if the same hostname resolves to multiple IPs**, and the first safe IP becomes unreachable, some stdlib implementations may retry with another IP (not guaranteed atomic pinning).
  - **`canonical_host` bypass**: Does it reject IPv4 literals? Yes (requires dot, rejects userinfo/port). But what about **IDN homographs**? It uses IDNA encoding, which should normalize, but confusable characters (e.g., `аррӏе.com` vs `apple.com`) are **not rejected** — this is a known limitation of pure canonicalization without allowlist vetting by humans. However, since allowlist is operator-signed, this is **mitigated**.
  - **CONNECT smuggling**: Oversized headers, pipelined requests after CONNECT, or CRLF injection could cause parsing errors or proxy confusion. The proxy must **strictly parse one CONNECT, then switch to raw splice**.

- **ANOTHER CONTROL?** Partial. `is_safe_public_ip` blocks private IPs, but **only if the pinned IP is used exclusively**. If the proxy falls back to another resolved IP, it fails. Also, SSRF to host-veth IP is **not blocked** by current logic.

- **FIX/REDESIGN**:
  - Explicitly block any `CONNECT` where `host` resolves (even once) to a **link-local, loopback, or veth subnet IP** — even if `is_safe_public_ip` passed earlier.
  - Bind proxy **only to veth IP**, not `0.0.0.0`.
  - Add explicit deny of `127.0.0.0/8`, `::1`, `fe80::/10`, and the **veth /31 subnet** as destinations.
  - Use `socket.SOCK_STREAM | socket.SOCK_CLOEXEC` and disable retries in resolver.

---

### **4. ALLOWLIST AUTHORITY + EMPTY HANDLING**
- **SEVERITY**: MEDIUM  
- **WHERE**: Wiring (2c) + cap derivation  
- **FLAW**: 
  - If the signed grant contains **no `net.maint:<host>` caps**, the allowlist is empty. The design says “non-empty verified allowlist” is required for `PROXIED_ROUTE`, but if this check is missed, the proxy could start with an empty allowlist → **deny all**, which is safe. But if the ladder **falls back to routeless**, that’s fine. Risk is if it **fails open** (e.g., proxy allows all when allowlist missing).
  - Model influence: The ADR claims the model never chooses the allowlist. But if the collaborator process is compromised, it could lie about the grant contents. However, since caps are **verified using a host-held key**, and the proxy receives the allowlist **after verification**, this is acceptable.

- **ANOTHER CONTROL?** Yes — the monotonic ladder requires `non-empty verified allowlist` to enter `PROXIED_ROUTE`. Assuming this is enforced, risk is low.

- **FIX/REDESIGN**: Assert `len(allowlist) > 0` in proxy startup; exit if empty. Log loudly.

---

### **5. FAIL-CLOSED LADDER + PROOF TOKEN RELIABILITY**
- **SEVERITY**: MEDIUM  
- **WHERE**: In-sandbox guard + wiring  
- **FLAW**: 
  - The guard checks direct connect **once** to a canary IP. But:
    - The canary IP might be **blocked by external firewall**, causing false failure.
    - The shell could **pass the guard**, then modify netns rules afterward.
    - Cached `maintproxy_available()` could go stale (e.g., proxy crashes after cache).
  - Token proves **belief + one-time behavior**, not continuous enforcement.

- **ANOTHER CONTROL?** No continuous monitoring. But given the shell is **human-run and ephemeral**, post-guard modification is a lesser concern than pre-guard bypass.

- **FIX/REDESIGN**: Acceptable for human maintenance. Document that async laundering is out of scope. Ensure guard uses a **fresh, non-routable canary** (e.g., `192.0.2.1` – TEST-NET-1) that should always be unroutable, so failure to connect proves default-deny works.

---

### **6. ASYNC LAUNDERING + FULL-FS WRITE**
- **SEVERITY**: LOW (documented residual)  
- **WHERE**: Non-goal section  
- **FLAW**: Shell can write cron jobs, SSH keys, etc., enabling future unmediated egress.

- **ANOTHER CONTROL?** No — explicitly out of scope. Same as today.

- **FIX/REDESIGN**: Not fixable without removing FS write. Honest documentation suffices.

---

## META-QUESTION: IS THIS WORTH BUILDING?

**No.** The **critical flaw** in netns default-deny editability by the sandboxed shell (Finding #1) **breaks the core security contract**. Even if fixed, the design introduces a **new privileged TCB** (helper + proxy) to enable a capability (**interactive network fetches**) that is **rare, high-risk, and better served by offline artifact vendoring**.

The alternative — **keeping maintenance network-less** and requiring operators to vendor drivers/binaries ahead of time — is **simpler, more secure, and aligns with zero-trust maintenance**. Occasional manual intervention (e.g., USB transfer) is preferable to building and maintaining a complex, privilege-heavy proxy architecture with inherent mediation gaps (“where, not what”).

The observed-route rejection (ADR 0005) was correct: **any network path for maintenance is a liability**. This design does not overcome that — it merely shifts the attack surface.

---

## STEELMAN

This design thoughtfully minimizes privilege by pushing the data path to unprivileged code, leverages existing egress.py safety logic, and avoids packet forwarding complexities. The positive proof token and monotonic fail-closed ladder show strong attention to verification over belief.

---

## VERDICT

**SERIOUS_FLAWS** — The maintenance shell’s ability to modify its own netns’s firewall rules completely undermines the default-deny guarantee, making the proxy mediation optional rather than enforced. **Do the alternative: keep maintenance network-less and vendor artifacts.**
