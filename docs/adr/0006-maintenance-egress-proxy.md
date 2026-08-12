# ADR 0006 — Maintenance network for non-vendorable artifacts: `maint_fetch` (a mediated fetch), NOT a shell proxy

- **Status:** ACCEPTED, 2026-08-12. Decision: build **`maint_fetch`** — a human-gated, mediated artifact
  fetch through `egress.py` that stages a non-vendorable artifact into the workspace; the maintenance
  shell stays network-**less** (routeless). The **destination-allowlist CONNECT forward proxy** (the
  originally-scoped design, below) is **DEFERRED** as a considered alternative: sound but a poor trade
  for the stated need.
- **Trigger (operator, 2026-08-12):** maintenance can need non-replicable fetches (proprietary drivers,
  licensed binaries) that can't be vendored ahead. The human maintenance `run_command` shell runs
  network-less today (`maintain.py` `--unshare-net`; `netns.py` `--unshare --net`).
- **Related:** ADR 0005 (observed route, rejected — named a content-mediating proxy as successor). ADR
  0003 (egress ladder; egress.py the sole mediated IP path). `collaborator/egress.py` (`fetch_to_file`
  reuses the whole Tier-1 contract). `collaborator/tools.py` (the `maint_fetch` tool + executor + seal).

## Context — why `maint_fetch` and not the proxy

The operator settled three forks for a proxy (destination-allowlist / explicit CONNECT / signed
`net.maint:<host>` caps), and a 5-vendor external DESIGN panel reviewed that design
(`red-team/collaborator/redteam_maintproxy_design.py`, raw at `raw_maintproxy_design/`). **Verdict 5/5
SERIOUS_FLAWS.** Two results:

1. **The proxy is buildable, not theater.** The headline finding — "the maintenance shell holds
   CAP_NET_ADMIN in its own netns and can `nft flush` the default-deny" — was called CRITICAL by 4/5,
   but grok-4.5 dissented on the kernel semantics, and a **live reproduction on Sparky** (DISPOSITION.md;
   `scratchpad/crux.sh`) settled it: a netns created by **real root (init userns)** is **un-editable** by
   a bwrap child-userns mapped-root shell (`nft flush` → `Operation not permitted`; it cannot even *list*
   the ruleset; CapEff=0). A `--map-root-user` self-created netns (the four models' assumption) **is**
   editable — so netns *ownership* is the decisive factor. The design-as-written holds, given a
   host-side veth filter + an asserted init-userns ownership invariant.
2. **But it is the wrong tool for the stated need.** 4/5 independently recommended a **mediated
   `maint_fetch` through egress.py** over the proxy: the stated trigger is an *artifact fetch*, which
   `maint_fetch` serves fully **without** a new privileged root-helper TCB and **without** demoting
   egress.py's content guarantee to a raw uninspected byte pipe (the panel's "where, not what" concern).
   The proxy earns its TCB only if *interactive* networked maintenance (apt/git/pip driven live in the
   shell) is a hard requirement — a bigger capability than "fetch a driver." The operator chose
   `maint_fetch`.

## Decision — `maint_fetch`

A human-gated tool `maint_fetch(url, dest)`:

- **Authority:** a SEPARATE signed capability namespace **`net.maint:<canonical-host>`** — a read grant
  (`net.get:H`) does NOT confer it, nor does an emit grant (`net.post:H`). Derived from the URL via the
  SAME `canonical_host` used to connect (authorize == connect), at the SAME single derivation site
  (`egress.required_capability`) for both hold-time and approve-time, so they cannot diverge. Default-deny.
- **Transport:** `egress.fetch_to_file` reuses the WHOLE Tier-1 contract verbatim — HTTPS-only,
  canonical-host==connect-host, redirect FAIL-CLOSED, resolve-once-**pin** + `is_safe_public_ip` (blocks
  private/loopback/link-local/CGNAT/NAT64/mapped-v6/metadata), request-target bounds. It STREAMS the body
  to a caller-owned sink (an artifact is MB-scale; the bytes never enter model context).
- **Ceiling, fail-CLOSED:** an operator-set `maint_fetch_max_bytes` (default 100 MiB, host config,
  never model-reachable). OVER-CAP → non-ok record + the partial file DELETED, so an oversized/truncated
  artifact never masquerades as complete. Same for redirect / unsafe-IP / non-2xx / ineligible URL.
- **Human-gated:** `default_leash=PROPOSE_FIRST`, no auto-lift in v0 — every maintenance fetch takes a
  human hand (matching net_post and the maintenance-is-human-in-the-loop posture). No autonomous
  authorship in v0, so provenance-flagging the fetched artifact is a documented follow-on for if/when an
  operator ever enables autonomous maint_fetch.
- **approved==executed:** `maint_fetch` joins `SEALED_TOOLS`; its held approval is bound to the EXACT
  (url, dest) via `held_action_seal` + `freeze_args`, so a post-approval url/dest swap is refused — the
  same TOCTOU close as run_command / write_file / net_post.
- **Fence + atomic staging:** `dest` is workspace-fenced (`resolve_in_workspace`, which resolves symlinks
  then checks containment — an escaping path OR a pre-planted symlink pointing outside is a WorkspaceError
  → DENY; reproduced on Sparky). The executor stages to a `mkstemp` temp in the fenced parent, then
  `os.replace()` onto dest — it NEVER opens dest for writing, so a symlink raced into place after the fence
  check is REPLACED, never written THROUGH (the `open(dest,"wb")`-follows-symlink TOCTOU the code panel
  flagged, closed for the final component). Staging is atomic: dest is the complete artifact or untouched.

### Scope boundary (unchanged from the proxy's intent, now trivially true)
This governs only the Collaborator's own maintenance fetch. The maintenance shell stays routeless; there
is NO privileged helper, NO netns/veth, NO change to host routing/firewall/DNS. A human downloading a
browser and surfing is entirely untouched (the governed layer is the agent's hands, not the desktop).

### Honest residuals
- **Content is bounded but not semantically inspected** — egress.py hashes + size-caps but does not scan
  the artifact; a compromised signed host could serve malicious bytes (hence human-gated + operator-
  signed host).
- **Async use** — a staged artifact the human later runs is the human's call (maint_fetch is human-gated;
  the F2 provenance-flag follow-on covers an autonomous variant if ever enabled).
- **v0 is HTTPS/GET only** — no interactive apt/git/pip in the shell; that is the deferred proxy's domain.
- **Intermediate-component symlink TOCTOU** — the final-component write-through is closed (atomic staging),
  but an INTERMEDIATE directory component raced to a symlink still needs `openat2(RESOLVE_NO_SYMLINKS)` to
  fully close; this residual is SHARED with `write_file` (same `resolve_in_workspace` pattern), needs a
  concurrent workspace writer, and the workspace is human-gated + disjoint-from-code. A uniform fenced-writer
  hardening is a documented follow-on.

## Deferred — the destination-allowlist CONNECT forward proxy (retained for the record)

Sound but not built. If *interactive* networked maintenance ever becomes a hard requirement, the proxy
is the path, and the DESIGN panel's must-fix redesign is normative:
- **Host-side veth nft filter as the PRIMARY enforcement** (drop from `iifname veth_host` except to the
  proxy ip:port, in the HOST netns — unreachable by the sandbox regardless of userns ownership), with
  in-netns default-deny as a secondary belt.
- **Assert the init-userns netns-ownership invariant** (the reproduced crux depends on it).
- **Unprivileged CONNECT proxy** reusing egress.py's canonical_host + is_safe_public_ip + resolve-once-pin;
  strict CONNECT parse (no smuggling, reject IP-literal authorities, header budget + timeout); bind ONLY
  the veth host IP + SO_BINDTODEVICE (never a host-wide/LAN open proxy); per-connection byte/duration caps.
- **Minimal fixed-arg root helper** (setns via O_PATH|O_NOFOLLOW fd, fixed sudoers, drop-order +
  NNP-before-exec, CLOEXEC/closefrom, parent-only teardown, never `flush ruleset`).
- **Fail-closed ladder** PROXIED_ROUTE → ROUTELESS → UNISOLATED, never routed-but-unmediated; a positive
  proof token bound to a real blocked-direct-connect, not a belief.

See `docs/BACKLOG.md` item 2 (proxy = follow-on if interactive maintenance is needed).

## Non-goals / revisit triggers
- No privileged network code ships in v0 (the operator's choice + the panel's trade judgment).
- Revisit if interactive networked maintenance (live apt/git/pip in the shell) becomes a hard
  requirement — then build the deferred proxy with the redesign above.
- Revisit to add `net.maint.auto:<host>` + provenance-flagging if autonomous maint_fetch is ever wanted.
