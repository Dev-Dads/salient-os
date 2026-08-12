# maintproxy DESIGN panel: x-ai/grok-4.5

_finish=stop seconds=241.1 cost=$0.1113 usage={'prompt_tokens': 19364, 'completion_tokens': 12133, 'total_tokens': 31497, 'cost': 0.1113084, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1113084, 'upstream_inference_prompt_cost': 0.0385104, 'upstream_inference_completions_cost': 0.072798}, 'completion_tokens_details': {'reasoning_tokens': 6124, 'image_tokens': 0, 'audio_tokens': 0}}_

# ADR 0006 — Adversarial Design Review

## Findings

### F-01 / IN-NETNS NFT IS EDITABLE ONLY IF YOU MIS-OWN THE NETNS — CRUX MOSTLY SEALED / SEVERITY: HIGH (conditional CRITICAL)
**WHERE:** §2b helper netns ownership + §Open question “the crux”  
**CONCRETE ATTACK OR FLAW:**  
The panel prompt assumes “bwrap mapped-root holds `CAP_NET_ADMIN` in its own netns ⇒ `nft flush ruleset` / re-address veth / add default route ⇒ direct Internet.” That threat model is **wrong for the design as written**, but only because of a kernel property the ADR never states clearly and therefore might implement away by accident.

Kernel rule (nft/netlink path): admin of a netns requires `ns_capable(net->user_ns, CAP_NET_ADMIN)`. A netns created by real root in the **init user_ns** has `net->user_ns == init_user_ns`. The maintenance payload is:
1. helper (uid 0, init userns) creates netns + veth + nft,
2. `setns` + uid/gid drop to the collaborator,
3. bwrap `--unshare-user --uid 0 --gid 0 --cap-drop ALL --disable-userns`.

Even **with** full caps inside the child userns, `ns_capable(init_user_ns, CAP_NET_ADMIN)` is false. With bwrap’s verified all-zero Eff/Prm/Bnd/Amb (maintain.py guard), it is false twice. So:

- `nft flush ruleset` → EPERM  
- `ip route add default …` / re-address veth / `ip link add` → EPERM  
- re-enable v6 via sysctl → EPERM  

**The mediation is not theater *if and only if* the netns is created by init-userns root and never re-created from inside the sandbox userns.**

**Failure modes that re-open the crux (CRITICAL if present):**
- Helper uses `unshare(CLONE_NEWNET)` after joining a collaborator userns, or creates the netns as the mapped user.
- “Fallback” path that builds the routed netns the way `netns.py` does (`unshare --map-root-user --net`) and then installs nft *there* — that netns is owned by the child userns; mapped-root + any residual/admin path can edit it. (Today’s maintain path drops caps, but a helper bug or non-bwrap path is fatal.)
- Dropping bwrap / cap-drop on the proxied path while keeping a user-owned netns.

**Also:** on hosts with `ip_forward=1` and broad MASQUERADE/FORWARD (Docker, many “dev” boxes), **in-netns nft is load-bearing**. Without it, a veth + default route is a real exit even though this design adds no forward rules of its own. No-forward is *not* a portable second floor.

**ANOTHER CONTROL:** Proof-token canary (direct TCP to public:443 must fail) catches “nft missing/broken” at start of run — not a live re-open after exec (N/A if uneditable), and not host-forward races mid-run.  
**FIX-OR-REDESIGN:**
1. **Normative invariant:** “netns + nft + veth are created only by init-userns root; sandbox userns never owns them.” Assert `stat(/proc/self/ns/user)` vs netns owner in the helper and in the in-sandbox guard.
2. **Defense-in-depth (do this):** host-owned filter on the **host end of the veth** (`iifname veth…` drop / only allow to proxy uid’s listener). Sandbox cannot touch host nft. Prefer this as primary; keep in-netns policy-drop as secondary.
3. Never document “shell has CAP_NET_ADMIN in the netns” — it must not.

---

### F-02 / HOST-SIDE OF VETH IS AN UNFILTERED HAIRPIN / SEVERITY: HIGH
**WHERE:** §2b topology + “no ip_forward, no host FORWARD” pitch  
**CONCRETE:** Proxy terminates TCP (good — no forward path required for the happy path). But the netns still has L3 reachability to the **host veth IP** (and, if nft is wrong/absent and `ip_forward=1`, beyond). Design allows `ip daddr <host-veth-ip> tcp dport <proxyport>`. Residual questions the ADR under-specifies:
- Any service listening on the host veth address (or `0.0.0.0` with weak fw) on that port becomes the choke point — OK if only maintproxy binds it.
- If nft rule is accidentally `daddr <host-veth-ip>` without port, or `oif veth` accept, shell hits **other host-local ports** via the veth IP (ssh, docker TCP, kube, metadata bridges, etc.).
- Hairpin to host loopback via proxy SSRF is a separate issue (F-06); this is **direct** to host via veth.

**ANOTHER CONTROL:** in-netns port-narrow rule (if correct); proof canary does not test “other ports on host-veth-ip.”  
**FIX:** Host nft/iptables: `iif <veth-host> ip protocol tcp tcp dport != PROXY drop` (or only accept to proxy socket). Bind proxy with `SO_BINDTODEVICE` to that veth. Optionally pin accept() to netns peer `/31` only.

---

### F-03 / PRIVILEGED HELPER IS REAL NEW TCB — SEVERAL CLASSIC FOOTGUNS UNSPECIFIED / SEVERITY: HIGH
**WHERE:** §2b helper + sudo -n posture  
**CONCRETE ATTACKS:**
1. **sudoers/argv surface:** `sudo -n maint-helper <args>` is safe only with `NOEXEC`, fixed subcommand, **no** shell passthrough, and either zero free-form args or a strict allowlist (uid, mode flag). A catch-all `helper *` is classic injection (extra flags, path substitution). Model must never reach argv; human `run_command` argv must not either — helper should not take payload argv at the privileged boundary (parent should pass it only after drop, via bwrap).
2. **Privilege-drop ordering:** `setgroups(empty) → setgid → setuid`, then verify `getuid/euid/gid/egid` and `CapAmb=0`, `PR_SET_NO_NEW_PRIVS` **before** any exec. Wrong order = retained gid/supplemental groups (docker, netadmin).
3. **Leaked fds:** a root-opened netlink/nft/netns fd inherited across exec is a full bypass of “unprivileged payload.” Everything `CLOEXEC`; explicit `closefrom(3)` after drop.
4. **`/run/netns/<name>` race:** create → path open/`setns` must be via `open(..., O_PATH|O_NOFOLLOW|O_DIRECTORY)` + `setns(fd)` on the fd, not a second path lookup. CSPRNG name reduces collision; does not fix symlink if directory is ever mode-lax.
5. **Parent/child split:** ADR claims no exec-after-drop in one process — good. Parent must be the sole teardown owner (netns/veth/table), with `PR_SET_PDEATHSIG` / waitpid, and teardown **only** `delete table <reserved>` / `ip netns del <reserved>` — never `nft flush ruleset` (agreed).
6. **Concurrent runs / reserved names:** `salient_route_u<uid>_<rand>` is fine; also lock or refuse if leftover name exists; bound count of orphans.
7. **Payload env/cwd:** clear `LD_*`, `NFT_*`, `IPTABLES_*` for the privileged phase; after drop, set only proxy env. cwd should not be attacker-writable during privileged steps (it isn’t if helper is standalone).

**ANOTHER CONTROL:** bwrap cap-drop + proof token limit blast radius of *some* bugs; they do **not** catch fd leak or sudoers overscope.  
**FIX:** Treat helper as “smaller than container runtime, larger than a script”: fd-sealed, drop-verified, `setns(fd)`, fixed sudoers, parent-only teardown. Fuzz the privileged/unprivileged boundary.  
**Judgment:** A root helper that eventually execs the payload is **acceptable only if** that boundary is boring and tested; vs network-less it is still a **net loss of TCB** for a convenience feature.

---

### F-04 / PROOF TOKEN IS BEHAVIOR-BOUND BUT CANARY + CACHE ARE FRAGILE / SEVERITY: HIGH
**WHERE:** §In-sandbox positive proof token + `maintproxy_available()`  
**CONCRETE:**
- **Good:** token after (prepared-netns marker ∧ `HTTPS_PROXY` exact ∧ direct canary connect **fails** ∧ proxy connect **succeeds**) is belief→behavior, returncode-independent whitelist like `SALIENT_MAINT_VERIFIED`. Payload forging the string on stderr is irrelevant if the guard only execs on success (same pattern as maintain.py).
- **Canary unreliability:** “direct to some public IP:443 fails” is true if nft works **or** if the canary is dead, blackholed, ICMP filtered, or corporate egress blocks raw IP. That is fail-**closed** (command does not run) — safe but brittle → unexpected ROUTELESS/UNAVAILABLE.
- **False sense if inverted:** if implementation ever treats “canary fails” as sufficient without “proxy works,” you can run with no network and still emit a token — broken maintenance, not open egress.
- **Stale positive cache:** positive-only `maintproxy_available()` can select PROXIED_ROUTE when setup will fail; OK if every run still requires the guard. Dangerous if any path skips the guard when cache is warm.
- **Teardown residue:** leftover netns/veth is DoS/resource leak; security issue only if a later run **joins** a stale netns without re-installing nft (reuse-by-name bug).
- **Prepared-netns check:** must be helper-planted secret/inode (e.g. bind-mount token mode 000 root-owned, or netns inode == helper-written value), not merely “not host netns.”

**ANOTHER CONTROL:** ladder ROUTELESS on failure — good if monotonic.  
**FIX:** Canary = connect to a **known-dropped** target (e.g. host-veth-IP:**not-proxy-port**, or TEST-NET address) plus connect to proxy port; bind token to netns inode + proxy URL + allowlist generation id; never skip per-run guard; negative-cache failures.

---

### F-05 / CONNECT PARSER + `canonical_host("https://"+host)` EDGEING / SEVERITY: MEDIUM
**WHERE:** §2a steps 1–3 + egress.py `canonical_host`  
**CONCRETE:**
- Reuse of `canonical_host` is the right move: rejects non-https scheme construction issues, userinfo, non-443 URL ports, IDNA junk, trailing-dot, case, IPv6 literals via `_HOST_CHARS`, dotless hosts.
- **Parse must not feed garbage into the concatenator:** authority must be split as `host:port` with **one** port, DNS host only (no brackets half-parse, no `CONNECT http://host:443`, no spaces, no raw IPv6 without rejecting). If `host` is `evil.com:443` and port is also parsed → double port bugs.
- **CRLF / oversized headers / smuggling:** CONNECT-only proxy that stops parsing at `\r\n\r\n` and then splices is mostly immune to HTTP request smuggling *downstream*, but still needs: absolute header budget, header read timeout (slowloris), reject `Transfer-Encoding`/`Content-Length` body on CONNECT, reject pipelined second request **before** 200 (bytes after CONNECT headers are tunnel data — buffer and pass **after** 200, never interpret as HTTP).
- **Port policy:** `port == 443` only — good for v0. Absolute-form vs authority-form: only authority-form CONNECT.
- **IP-literal allowlist:** `canonical_host("https://8.8.8.8")` returns `"8.8.8.8"` (dotted-quad passes the dot/charset gates). So `net.maint:8.8.8.8` would authorize CONNECT by IP. Probably undesired; tighten `canonical_host` use or reject numeric hosts for `net.maint`.

**ANOTHER CONTROL:** allowlist + `is_safe_public_ip` still apply.  
**FIX:** Strict RFC-ish CONNECT grammar; reject IP-literal authorities for maint; cap headers; timeouts before 200.

---

### F-06 / RESOLVE-ONCE-PIN IS FINE; SSRF TO HOST/VETH VIA PROXY NEEDS EXPLICIT DENY / SEVERITY: HIGH
**WHERE:** §2a steps 4–5 + `is_safe_public_ip`  
**CONCRETE:**
- `getaddrinfo` → first `is_safe_public_ip` → `create_connection((pinned,443))` matches egress.py and **does** pin (no second resolve). Rebind between check and connect is closed.
- `is_safe_public_ip` is strong: `is_global` (CGNAT/tailnet), mapped-v4 projection, reserved (NAT64 `64:ff9b::/96`), loopback/link-local/metadata. Good for CONNECT dest.
- **Gaps:**  
  - CONNECT to an allowlisted **name** that intentionally points at a public IP you did not mean (operator issue).  
  - **Proxy reachable from host/LAN:** binding “veth host IP” is still a host-local address; any host process (or lateral attacker with route to that IP) can use the maint allowlist as an open forward proxy to `net.maint:*` hosts.  
  - If bind is accidentally `0.0.0.0`, LAN gets a free allowlisted egress proxy.  
  - Return path doesn’t expose plaintext (no MITM) — good — but **bytes are unrestricted** once allowed (F-09).

**ANOTHER CONTROL:** destination allowlist limits *names*, not who may call the proxy.  
**FIX:** `bind(veth_host_ip)` + `SO_BINDTODEVICE` + accept only from peer `/31`; optional peer creds if moved to UNIX+scm (different design). Refuse pinned IPs in the veth `/31`, host secondary IPs, and implementation-specific metadata ranges you care about beyond `is_global`.

---

### F-07 / CONNECTION EXHAUSTION / UNBOUNDED Splice / SEVERITY: MEDIUM
**WHERE:** §2a bounds  
**CONCRETE:** Design mentions idle timeout + max concurrent; good. Missing: global bandwidth/byte caps, per-run connection budget, splice half-close handling, stuck ESTABLISHED to slow allowlisted servers. Maintenance shell can open N CONNECTs and push forever — host resource DoS, not allowlist bypass.  
**ANOTHER CONTROL:** none in sandbox (full shell).  
**FIX:** hard caps (conns, bytes, duration) aligned with “fetch drivers,” not “free CDN uplink.”

---

### F-08 / ALLOWLIST AUTHORITY — MOSTLY SOUND / EMPTY-SET LADDER OK / SEVERITY: LOW–MEDIUM
**WHERE:** §2c cap derivation  
**CONCRETE:**  
- Allowlist from verified signed `net.maint:<canonical-host>` with collaborator holding `caps_key` is the right authority split; model must not pass host lists into helper or proxy CLI.  
- **Must** canonicalize grant hosts with the **same** `canonical_host` used at CONNECT time (including IDNA). Mismatch ⇒ fail closed (deny) or, worse, grant on confusable.  
- Empty allowlist ⇒ not PROXIED_ROUTE (design) ⇒ ROUTELESS. Good; must not start proxy with empty set “deny all” while still advertising proxied success.  
- Wildcard non-goal is correct for v0.

**ANOTHER CONTROL:** governance seam (assumed).  
**FIX:** Single function `maint_allowlist(caps) -> frozenset[str]`; proxy takes only that immutable set; unit tests on trailing-dot/IDNA/case.

---

### F-09 / “WHERE NOT WHAT” QUIETLY DEMOTES EGRESS.PY FOR THE MAINTENANCE PATH / SEVERITY: HIGH (coherence) / residual if honest
**WHERE:** Goal/non-goal + meta-question  
**CONCRETE:** egress.py is not just destination fidelity — it is HTTPS-only GET/POST, no redirects, target length cap, response ceiling, body cap/hash, no model `Authorization`, channel-integrity records.  
maintproxy: after CONNECT 200, **raw bidirectional splice** to an allowlisted host. A human-approved shell can:
- `git push` arbitrary repos,
- exfil whole FS to `net.maint:github.com` / vendor CDN,
- pull and run attacker content from that host,

with **no** method/body/quota/audit of bytes. That is a deliberate demotion of the maintenance path relative to Tier-1/2. It is coherent **only** if the security story is “human is the TCB for maintenance content; we only constrain *where*.” It is **not** “egress.py fidelity” as ADR 0005’s successor phrasing suggests — it is **destination fidelity alone**.

**ANOTHER CONTROL:** human approval of `run_command`; full-FS laundering already exists.  
**FIX:** Rename the claim (“destination-allowlist pipe,” not “egress.py brain parity”). Prefer a **mediated fetch tool** (`maint_fetch` → egress.py → write file) for the proprietary-driver case; keep shell routeless.

---

### F-10 / FAIL-CLOSED LADDER — HONEST IF GUARD IS MANDATORY; ONE OPEN EXIT CLASS / SEVERITY: MEDIUM
**WHERE:** §2c route rung  
**CONCRETE:** `PROXIED_ROUTE → ROUTELESS → UNISOLATED` is the right monotonic shape. “Never routed-but-unmediated” holds **iff**:
- no code path starts the sandbox in the prepared netns without nft+proxy+guard,
- helper failure does not leave bwrap on host netns with `HTTPS_PROXY` set (env without isolation = footgun for tools that honor proxy — actually that would egress via host network **unmediated** if sandbox lost netns isolation!).

**Critical ladder bug pattern:** helper fails `setns`/netns, bwrap still runs **without** `--unshare-net` → **host netns shell with full FS** = worse than today’s default routeless. Guard must prove prepared netns **before** exec; on any failure, caller must use `wrap_maintenance(..., unshare_net=True)` / `wrap_no_network`, never “bare.”

**ANOTHER CONTROL:** token whitelist on caller.  
**FIX:** Two-phase: (A) helper readiness, (B) only then build argv without `--unshare-net`. Any exception → explicit ROUTELESS wrap. Integration test: kill nft mid-setup → no run / no host-netns fallthrough.

---

### F-11 / PROXY-ENV RELIANT + HTTPS-ONLY — DOCUMENTED RESIDUALS / SEVERITY: LOW (as residual)
**WHERE:** Non-goals  
**CONCRETE:** Tools ignoring `HTTPS_PROXY` fail closed (good). Interactive apt/git need extra config (`Acquire::http::Proxy`, `git config http.proxy`). Non-443/SSH git denied in v0. These are product breaks, not bypasses.  
**ANOTHER CONTROL:** default-deny nft.  
**FIX:** Document; optional `GIT_SSL_NO_VERIFY` is a footgun — do not set.

---

### F-12 / ASYNC LAUNDERING UNCHANGED / SEVERITY: LOW (documented residual)
**WHERE:** maintain.py full-FS rw + ADR non-goal  
**CONCRETE:** cron/systemd/`authorized_keys` still egress outside the chokepoint. Proxy does not change this. Claiming “controlled network” without restating this would be overclaim.  
**ANOTHER CONTROL:** none synchronous.  
**FIX:** Keep residual table honest.

---

### F-13 / IPV6 / ESTABLISHED / LOOPBACK IN NETNS / SEVERITY: LOW–MEDIUM
**WHERE:** §2b nft + disable_ipv6  
**CONCRETE:** disable_ipv6 in netns is correct; without caps cannot re-enable if netns init-owned. `ct state established,related` + lo accept are fine if output policy drop and only new traffic to proxy:port. Ensure **no** `ip6` accept paths; prefer `inet` family table with explicit no-v6 or kill v6 addresses on veth.  
**ANOTHER CONTROL:** host-side veth filter (F-02).  
**FIX:** assert no v6 route/addr in guard.

---

### F-14 / BWRAP JOIN SEMANTICS / SEVERITY: MEDIUM
**WHERE:** §2c sandbox join vs maintain.py  
**CONCRETE:** Proxied path drops `--unshare-net` but must **keep** `--unshare-user --cap-drop ALL --disable-userns` deputy masks and the cap/NNP proof. Replacing the net check with “prepared netns token” is mandatory — reusing `parent_ino != self` is wrong (you want equality with helper netns, not inequality with host).  
**ANOTHER CONTROL:** SALIENT_PROXY_VERIFIED.  
**FIX:** single `_guarded_script_proxied(...)`; never weaken cap/mask checks to “get network.”

---

## Attack checklist (panel questions → caught by?)

| # | Attack | Breaks arch? | Caught by? |
|---|--------|--------------|------------|
| 1 | `nft flush` / re-route in netns | **No**, if init-userns-owned netns + no caps | Kernel userns ownership; bwrap cap-drop; canary if nft absent |
| 1b | Same on user-owned netns | **YES, CRITICAL** | Only if you mis-implement helper |
| 1c | Direct egress with ip_forward=1, nft down | **YES** until guard | Canary/token; **not** after exec if rules editable |
| 2 | Helper sudo/fd/drop bugs | **YES** | Little else — TCB |
| 3 | CONNECT smuggle / pin race / unsafe IP | Pin race no; parse maybe; IP mostly no | `canonical_host` + `is_safe_public_ip` if wired exactly |
| 3b | Host/LAN clients use proxy | **Policy bypass / extra clients** | Not today — need accept filter |
| 3c | SSRF to metadata via CONNECT | **No** if pin+safe IP | `is_safe_public_ip` |
| 4 | Model influences allowlist | **No** if derivation pure | Governance |
| 4b | Content demotion | **Coherence yes** | Human gate only |
| 5 | Routed-but-unmediated | **If setns skipped** | Guard must block; ladder fallback |
| 5b | Token belief-only | **No** if canary is real connect | Behavior bind |

---

## META-QUESTION: Is this worth building vs network-less?

**Mostly no — not as the default answer to “proprietary drivers.”**

What you gain:
- Interactive HTTPS tools in the maintenance shell to a small operator-signed host set.
- No MITM, no SNI dependency, no `ip_forward` requirement on the happy path.
- Data path can stay unprivileged; crux is **sealable** with correct netns ownership + host-side veth filter.

What you pay:
- A **new root helper** on the hottest boundary (F-03) — larger TCB than the feature’s frequency justifies.
- A second mediated path that is **not** egress.py-class content control (F-09), i.e. a blessed bidirectional exfil/supply-chain pipe to every `net.maint:` host.
- Operational fragility: proxy-env, 443-only, canary false fails, per-run netns/veth/nft.
- Residual async laundering unchanged — so you did not buy “maintenance can’t leak,” only “synchronous non-allowlisted IP is harder.”

**Better alternatives (ordered):**
1. **Keep maintenance network-less** + vendor/cache artifacts; for rare proprietary blobs, an operator-side or **`maint_fetch` tool through egress.py** (destination + bounds + audit) writing into the workspace. Covers the stated trigger without a shell proxy.
2. If interactive shell network is non-negotiable: **host-filtered veth + unprivileged CONNECT proxy**, but treat helper as a full security project — or **pasta/slirp** usermode stack to shrink netns/nft root surface (you still want the CONNECT allowlist process; you may still want a small helper).
3. TLS-terminating proxy: **worse trade** here (trust-store injection into full-FS maintenance, breaks pinning, huge privacy/complexity) unless you truly need content inspection.

Building ADR 0006 is justified only if interactive `apt`/`git`/`curl` to a **tiny** signed host set is a hard requirement and the org accepts “human shell + allowlisted pipe” as the security ceiling.

---

## STEELMAN

The CONNECT design correctly avoids ADR 0005’s forwarding/SNI/masquerade pathologies: terminate in an unprivileged proxy, reuse egress.py’s canonical-host and safe-IP pin, derive allowlists only from signed `net.maint:` caps, and fail closed with a behavioral proof token. If the netns is init-userns-root-owned and the host end of the veth is filtered, in-netns mapped-root cannot edit the default-deny, and “no second unmediated exit” is real rather than theatrical. That is a coherent, minimal-privilege shape for destination-only maintenance networking.

---

## VERDICT: **SERIOUS_FLAWS**

Technically **fixable**, but as specified it overclaims “egress.py fidelity,” under-specifies the load-bearing netns-ownership invariant and host-side veth filter, and adds a root helper TCB that is a poor trade for the stated use case.

**Single highest-value change:** **Do the alternative: keep maintenance network-less and add a mediated `maint_fetch` (egress.py) path for non-vendorable artifacts.**  
If you insist on shell network: make **host-owned egress filter on the veth** + **init-userns netns ownership asserts** the primary control, and rewrite the ADR claim from “egress.py brain” to “signed destination pipe, no content guarantee.”
