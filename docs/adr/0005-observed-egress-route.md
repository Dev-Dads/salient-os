# ADR 0005 — Forced observed egress route for the maintenance sandbox (ADR 0003 revisit #1c)

**Status:** **REJECTED** by an external design panel (5/5 SERIOUS_FLAWS, unanimous "not worth building —
keep maintenance network-less"), 2026-08-11. NOT built. Preserved as the record of a considered-and-
declined design (as ADR 0004 preserved the scope-artifact). Maintenance stays network-less: the routeless
maintenance sandbox (ADR 0003 revisit #1b, PR #42) keeps `egress.py` the sole IP path, and the deputy
seal made that state sound. If network-for-maintenance ever becomes a hard requirement, the panel's
"correct" evolution is a CONTENT-MEDIATING proxy (SNI/allowlist, at `egress.py`'s fidelity), not this
dest-only route.

## Panel outcome (why this is not built)

Pressure-tested BEFORE any code (empirical-adversarial discipline). 5-vendor panel ($0.72): gpt-5.1,
gemini-2.5-pro, opus-4.1, grok-4.5, qwen3-max — **all SERIOUS_FLAWS**, all judging the trade **not worth
it** vs the honest default. Consensus reasons:

1. **It demotes the core guarantee.** Routeless made `egress.py` the SOLE IP path; the observed route is
   a SECOND IP path that logs destinations but NOT content — a human-approved shell could POST to a
   non-allowlisted host, observed-but-not-blocked (what `egress.py` refuses). grok: "the awkward middle —
   maximum kernel coupling, minimum policy fidelity."
2. **The observation is incomplete.** IP FRAGMENTS carry no L4 header, so a `meta l4proto` FORWARD hook
   logs the first fragment and silently forwards the rest while the guard still emits a positive
   "observed" token (a false claim). And masquerade is POSTROUTING (after the FORWARD verdict), so
   FORWARD-accept does not stop the shell pivoting to the LAN / docker networks / cloud metadata
   (`169.254.169.254`) — traffic that never touches the intended uplink.
3. **The privileged helper is a fragile new TCB** for, at best, a convenience middle-ground; needs a
   parent/child split (no exec-after-drop in one process), strict sudoers, CSPRNG names, NNP-before-drop.

Alternatives named by the panel: keep network-less (the honest default, 3 votes); `pasta` userspace
networking if network is a hard requirement (gemini — unprivileged, reuses the existing observer, but a
new dependency the operator previously declined); a content-mediating proxy (the coherent-but-bigger
evolution); or the route ONLY for dedicated operator break-glass hosts with the fixes (grok). Operator
decision (2026-08-11): **accept the panel — drop the observed route, keep maintenance network-less, and
proceed to the residual hardening sweep.** Panel outputs + disposition in
`red-team/collaborator/raw_observedroute_design/`.

---

_The original design brief follows, preserved for the record._

**Status (as proposed):** design brief — pressure-tested and declined.

## Context

Today a human/opted-in `run_command` runs in the bwrap **maintenance sandbox** (ADR 0003 revisit #1b,
`collaborator/maintain.py`): full read-write host view, egress-deputy sockets un-removably masked, and
a **routeless** netns (`--unshare-net`). Routeless means `egress.py` (the mediated HTTP client) is the
**sole IP-network path** off the machine — which is what makes the same-channel egress log sound. It
also means the maintenance shell has **no network at all**: `git push` / `apt` / `ssh` fail.

Operator direction: **return maintenance network, but through a single OBSERVED route** — so those
commands work again while every destination is recorded and there is no *unobserved* second exit. The
operator has chosen (given the ladder below) to pay for it with a **privileged host helper** rather
than a new dependency.

This ADR is the enforced-chokepoint "revisit #1c". It is deliberately posted for an external design
panel BEFORE code, because it introduces a **new privileged trust surface** (a root helper) and
**re-opens an IP egress channel** that the routeless design had closed.

## Decision (proposed architecture)

A human/opted-in `run_command`, when the observed route is available and enabled, runs in the maintenance
sandbox **joined to a privileged-helper-prepared routed + observed network namespace** instead of a fresh
routeless one.

### 1. The privileged host helper (`salient-route-helper`, out of the Python import path)

Invoked as `sudo -n /usr/local/lib/salient/salient-route-helper <fixed args>` (the same `sudo -n`
posture `egressobserver.py` already relies on). It is the **privileged launcher**: it performs all
root-only setup with FIXED, model-input-free parameters, then **drops privileges and execs the sandbox**.
Per run, in order:

1. Create a fresh netns with a reserved per-run name `salient_route_u<uid>_<runid>` (idempotent;
   torn down on exit; only ever touches its own reserved names — never `ip netns delete` anything else,
   never `nft flush ruleset`).
2. `veth` pair `sr<runid>H` (host) ↔ `sr<runid>C` (moved into the netns); address a `/30`
   (`10.<a>.<b>.1` host, `.2` netns); default route in the netns via the host end; `lo` + veth up.
3. Host side: `sysctl net.ipv4.ip_forward=1`; an nft masquerade rule scoped to the `/30` out the
   uplink; the **observer FORWARD hook** (below); a host `INPUT` drop from the veth (so the shell can't
   reach host-local services on the veth subnet, which are INPUT not FORWARD); host `FORWARD`
   **policy drop** with explicit accept only for observed protocols (TCP+UDP established/new, ICMP for
   PMTU) so the route is the sole, constrained exit.
4. In the netns: disable IPv6 (`net.ipv6.conf.all.disable_ipv6=1`) unless a symmetric v6 route +
   `dests6` FORWARD rule is set up — never a half-configured v6 second exit.
5. **`setns` into the prepared netns**, then **drop privileges** (`setgroups([])`, `setgid`, `setuid`
   to the collaborator's real uid, verify the drop), then **`exec` bwrap** with the PR-1 maintenance-
   sandbox flags but **WITHOUT `--unshare-net`** — so bwrap *inherits* the prepared routed netns. bwrap
   still does its own `--unshare-user` + `--cap-drop ALL` + `--disable-userns` + the full-`/` view + the
   deputy masks, so the payload runs unprivileged, cap-dropped, un-removably masked, in a routed +
   observed netns.

**The load-bearing safety property:** every PRIVILEGED step (1–4) uses FIXED args derived from
`(uid, runid)`, never from the request; the human-approved payload `argv` is passed **only** to the
post-drop bwrap (step 5), which runs it unprivileged and cap-dropped. The payload never executes with
any privilege the current routeless path doesn't already grant.

### 2. The rebuilt observer (host `FORWARD` / `iifname`)

The shipped `egressobserver.py` hook matches `meta skuid` on `OUTPUT` — behind NAT it sees nothing
(forwarded packets have no local socket; they hit the host `FORWARD` chain, not `OUTPUT`). So the
observer is rebuilt as a host **`FORWARD` hook keyed by the veth `iifname`** — every packet ingressing
`sr<runid>H` is, by construction, this shell's traffic, recorded as `ip daddr . dport` (the true intended
dest; `FORWARD` runs before masquerade, which rewrites source not dest). `iifname` on a dedicated veth is
*stronger* attribution than `skuid` (no co-tenant noise), and the shell — privileged only inside its own
netns — cannot touch a hook in the host netns. All-protocol **recorder** semantics (no `EgressRecord` to
reconcile against; a human-approved maintenance shell has no claim — we RECORD where it went, for audit).
The dest set is attached to the `Execution`/`Decision` for the audit trail. EVIDENCE, not a gate.

### 3. Verification + the honest fallback ladder (monotonic, fail-closed)

`observed_route_available()` is a verified host probe (helper reachable via `sudo -n`; veth+NAT+observer
installable-and-readable, then torn down; cached positive-only, like `containment_available()`). The
per-run in-child guard emits a POSITIVE token only after it verifies the netns is the prepared one AND
the observer is live; the caller whitelists on it (fail closed). The ladder, never routed-but-unobserved:

`OBSERVED_ROUTE` (route + verified observer) → `SANDBOX_ROUTELESS` (PR 1: bwrap, deputies masked, no
network) → `NETNS_ROUTELESS` (the certified `wrap_no_network`) → `UNISOLATED` (honest flag). Each rung
is strictly weaker and honestly reported; the observed route is opt-in host config (never model-chosen).

## The trade this ADR makes (state it plainly — attack it)

1. **It re-opens an IP egress channel.** Routeless made `egress.py` the *sole* IP path; the observed
   route is a **second** IP path. It is OBSERVED (every dest ip:port recorded, unbypassable off the one
   veth) but **not content-mediated** — unlike `egress.py`, it does not see the URL, the method, the
   body, or apply per-host `net.get`/`net.post` caps. So "egress.py is the sole IP path" weakens to
   "egress.py is the sole *content-mediated* path; the observed route is a dest-logged bulk path for
   human-approved maintenance." The egress log stays sound for `egress.py`; the observed route's audit is
   dest-set only.
2. **It adds a privileged root helper that execs payloads.** New trust surface. Mitigated by fixed
   privileged args + privilege-drop-before-payload, but it is genuinely more attack surface than the
   unprivileged routeless path.
3. **It observes *where*, not *what*.** DNS query names and UDP/QUIC payloads are not captured; the
   deputy mask is still a blocklist; the full-FS write view still permits async laundering. A DNS-aware
   *enforcing* chokepoint is a further follow-up, not this ADR.

## Questions for the design panel (attack hardest)

- **The privileged helper.** Is the "fixed privileged setup → drop privileges → exec payload" boundary
  sound? Arg-injection into steps 1–4; the privilege-drop sequence (setgroups/setgid/setuid order, cap
  and no-new-privs state, no leftover fds/env); a race between netns setup and the `setns`+exec; reserved-
  name collisions or a symlink/rename attack on `/run/netns/`; can the payload influence anything before
  the drop? Is a root helper that ultimately execs the human payload *worth it* vs. leaving maintenance
  network-less?
- **The re-opened channel.** Is trading "egress.py is the sole IP path" for "sole content-mediated path +
  a dest-logged observed route" acceptable, or does it undermine the same-channel egress-log soundness in
  a way that matters? Can the observed route be (mis)used to exfil what `egress.py` would have blocked
  (e.g. POST to a non-allowlisted host) — and is "observed but not blocked" the right posture for a
  human-approved command?
- **Observer completeness.** Does the `FORWARD`/`iifname` hook truly see EVERY egress off the one veth
  (TCP/UDP/ICMP/other l4proto; fragmented; IPv6 if not disabled)? Any packet that leaves the veth
  unrecorded while the guard still emits the positive token = a false "observed". Is `policy drop` +
  explicit-accept the right belt, and is its accept set complete for real maintenance (DNS, TCP, PMTU)?
- **The join mechanism.** Is "helper setns's into the netns then drops+execs bwrap (no `--unshare-net`)"
  correct and safe, given an unprivileged process cannot `setns` into a root-owned netns directly? Does
  bwrap inheriting the netns (rather than creating one) interact badly with `--unshare-user`/`--cap-drop`?
- **Fail-closed.** Any path to routed-but-UNOBSERVED (observer install fails but the route comes up; the
  probe passes but a real run's observer isn't live; teardown leaves a route up for the next run)? Is the
  positive-token whitelist airtight here as it is for the sandbox?

## Alternatives considered

- **Do nothing (keep maintenance network-less).** Zero new surface; `egress.py` stays the sole IP path;
  maintenance that needs network is done by the operator or via mediated `net.post`. The honest default
  if the panel finds the trade not worth it.
- **Userspace networking (pasta/slirp4netns).** Unprivileged, and the existing `skuid` observer would
  work unchanged — but a NEW external dependency (breaks the stdlib-only/system-binaries-only discipline)
  and a userspace stack in the sole egress data path. Rejected earlier by the operator in favour of the
  helper.
- **A content-mediating transparent proxy (SNI/allowlist).** Richer (hostnames, per-dest policy) but far
  more code in the egress data path (TLS ClientHello parsing, SO_ORIGINAL_DST, concurrency). Deferred as
  a later evolution if dest-only observation proves insufficient.
