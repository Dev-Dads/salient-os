# ADR 0003 — Outbound authority: a tiered egress ladder with structural un-grantability

- **Status:** Proposed — v2 (post-panel), 2026-08-09. Supersedes the pre-panel v1
  draft of this file (two-layer "authority + prohibition-recognizer" framing),
  which the review retired.
- **Scope:** The Collaborator's boundary with domains it does not own — the
  network egress it may perform (`collaborator/`) and the class of third-party
  actions no in-band authority may grant. Introduces one new capability
  *namespace*, one new *gate shape* (a capability derived from the request), and a
  mediated egress client. Core (`salienceos/`) is untouched in v0.
- **Related:** ADR 0001/0002 (trust-domain scoping — this ADR draws a *new* trust
  boundary and inherits their "state the limit, don't claim what we don't have"
  discipline). The **staging subsystem** (PR #26): controlled-location hard-deny +
  proposal stage pool — the *inward* half of the seam. The **run_command floor**
  (`governance.py:193`): a proposer-originated shell command is floored to
  `propose_first` — Tier 2 of this ladder, already shipped. `collaborator/research.py`
  — the `web_research` trust level this ADR lifts. **Design review:** external
  5-vendor panel + two internal adversarial passes (runner:
  `red-team/collaborator/redteam_outbound_authority.py`, raw at `raw_outbound/`);
  every reviewer steelmanned the direction and returned SERIOUS_FLAWS on the v1
  specifics. This v2 folds in what survived reproduce-before-accept.

## Context

Everything the Collaborator does *inside* its workspace is governed: the capability
gate is the one core-enforced authority (`directive.grants_capability`,
`governance.py:228`), the leash is the second axis, the workspace fence bounds
paths, and the verifier's "hands can't lie" property checks a tool's claimed side
effects against the independently-observed world. The staging subsystem closed the
last *inward* gap.

But **inside-system authority is governed while outside-system reach is not.** The
three real tools are all local — `fs.write:project`, `fs.read:project`,
`shell.exec` (`tools.py:77`). There is **no egress capability**; the only way off
the machine is `run_command`, which is why it was floored to `propose_first`. That
floor is a stopgap: it makes the raw-reach tool ask for a hand, but it does not
*model* what reaching outside costs, does not default-deny destinations, and says
nothing about the class of outside action that no approval can make legitimate.

### The throughline, and the axis the enforcer can actually see

The principle proven inward generalizes outward: **produce freely in the space you
can reach; gate the consequential placement or emission into a domain you do not
own.** A controlled-location write and a network emission are the same seam pointed
opposite directions.

The review sharpened *how* to draw the outward gate. The tempting frame — a
"grantable authority" layer beside an "un-grantable prohibition" layer that
*recognizes* infiltration/offense — fails, because the boundary between those two
sets is **target ownership / third-party consent**, and that is precisely the
property the enforcer cannot observe at runtime. A predicate can see a tool name, a
destination string, an argument shape; it can never see whether the target is
*yours to authorize*. Drawing the boundary where the enforcer is blind yields a
recognizer that is near-vacuous against real offense (which lives in semantics and
in the aggregate of individually-innocuous steps) yet false-positives your own
authorized lab box. So the enforceable control cannot be a content recognizer. It
must be **capability-shaped and default-deny**: the prohibited set is enforced not
by *spotting* offense but by there being **no grantable capability that names a
destination you cannot prove authority over, and no in-band code path to mint one.**
Unreachable, not un-recognized.

## Decision

Replace the two-layer split with **one capability model on a tiered authority
ladder.** Every outbound action sits on exactly one rung, and the rungs are ordered
by how much of a human hand and how strong an out-of-band root they require.

### Tier 1 — Mediated allowlisted egress (default-deny, signed, exact-host)

- **A per-destination capability, default-deny.** `net.get:<canonical-host>` is
  granted only if the operator's **signed** `PolicyCaps` names that exact string
  (`granted_capabilities`, `policycaps.py:128`); absent it, the gate denies exactly
  as for any ungranted capability. The allowlist is *signed authority*, not mutable
  session config — so it cannot be widened at runtime and is re-checkable at
  approval time.
- **A new gate shape: capability derived from the request.** The current gate reads
  a *static* `tool.capability` and never inspects `intent.args`
  (`directive.py:56` is exact membership; `governance.py:228`). Egress needs
  `required = "net.get:" + canonical_host(intent.args)`, gated on *that*. This
  `capability = f(intent)` step is the one genuinely new mechanism; the carrier
  (signed caps) and exact-match least-privilege are reused unchanged. **Core stays
  untouched** — the derivation lives in the Collaborator seam.
- **Exact host, never a pattern.** `grants_capability` is exact membership, which
  is a *security asset*: `net.get:evil.com` does not authorize
  `net.get:secret.evil.com`, so subdomain/DNS-label exfil is blocked by
  construction — **but only while the allowlist is exact.** Wildcards/prefixes are
  therefore **forbidden**, not merely undesigned.
- **The mediation contract (this is where allowlists actually fail).** One mediated
  client (`collaborator/egress.py`) is the only thing that touches the network, and:
  1. **One canonical parse builds the capability key *and* is the connect host.**
     `urllib.parse` → reject/normalize userinfo → IDNA/punycode → NFC + casefold →
     strip port, strip trailing dot. The authorizing host and the connected host
     are the *same* string from the *same* parse — closing authorize-one /
     connect-another (`user@allowed.com` connecting to the userinfo host, case/IDN
     divergence).
  2. **Fail closed on redirects.** `urllib` follows 3xx by default and re-checks
     nothing; a 302 to `http://169.254.169.254/` or any un-allowlisted host would
     silently escape. The client sets **max-redirects = 0**; a redirect is surfaced
     as a *new* egress intent that re-runs the full gate against its own host.
  3. **Pin the IP; block the private ranges.** Resolve once, connect to the pinned
     IP, reject loopback / RFC-1918 / link-local / `169.254.169.254` / metadata
     ranges — closing DNS-rebind and SSRF-to-metadata (the allowlist checks the
     *name*; the socket uses the *IP*).
  4. **HTTPS only; bounded.** Response byte ceiling, connect/read timeouts, a
     per-session egress quota (no unbounded stream into the proposer context, no
     covert-channel/DoS via volume).
- **GET is an exfiltration channel — say so.** v0 wires only `net.get`, but "GET =
  no side effect" is false: query/path/headers carry bytes *out*. The design drops
  that claim, treats every allowlisted GET as an **outbound-data event**, hashes the
  full request target (path+query) and a normalized header set — not a nonexistent
  GET body — caps query length, and forbids model-supplied `Authorization`/`Cookie`
  headers. Preventing exfiltration of any secret the model can read is **best-effort,
  not guaranteed** (see non-goals).

### Tier 2 — Human-gated raw reach (already shipped, extended)

- A **proposer-originated** (`source=="proposed"`) `run_command` **or egress tool**
  is floored to `propose_first` (`governance.py`) — the unbounded-reach tool and any
  off-domain emission always need a hand, whatever the leash config.
- **`net.post` / any side-effecting emission** defaults to `propose_first`. **SHIPPED
  (revisit #2, `collaborator/egress.post` + the `net_post` tool):** a mediated POST reusing the
  whole Tier-1 transport contract (canonical host == connect host, IP-pin + private/CGNAT/metadata
  block, HTTPS, no-redirect-fail-closed — now also so the body **and** a credential can never be
  re-sent to a redirect). Authority is a **separate namespace** `net.post:<host>` (a `net.get:<host>`
  read grant does **not** confer emit). It is FLOORED to `propose_first` and lifts a host to
  `act_then_report` ONLY when THREE positive, non-model-reachable conditions all hold: (1) the session
  is under a **signed** grant (not mutable legacy caps); (2) that grant names the exact host
  `net.post.auto:<host>`; and (3) the **host is directing this specific call** via the keyword
  `leash=act_then_report` (caller authority — `run_turn` and the tool-call parser never set it).
  **The red-team corrected a critical mistake here:** the first cut lifted on `source != "proposed"`,
  but `source` is a *parse-channel* label (`"structured"`/`"content_block"`/…), not provenance — so
  every model-emitted tool call looked "operator-directed", and a model steered by injected web
  content could self-originate a credentialed autonomous emission. Gating on the keyword leash means a
  **model-emitted emission is ALWAYS human-gated even to an auto host**; autonomy takes a deliberate
  host-directed call. (Josh's steer — "loosen only the standard stuff I direct, like an OpenRouter
  panel; the rest propose-first" — is honored by making 'I direct' an explicit host signal, not one
  inferred from model output.) An explicit host tighten and a signed `leash_cap` still win.
  Credentials are **host-injected** from host config (`session.egress_credentials`, keyed by canonical
  host) — never from model args, never logged. The audit is **body-free for autonomous** emissions and
  keeps a **bounded body preview for human-gated** ones. A held emission has **no verifier**, so its
  consequential payload (dest + target + content-type + body) is **sealed at hold time** and
  re-checked at approval — a payload mutated after the human saw it is refused (`approved == sent` is
  bound, not observed). Both red-team passes ran (external 5-vendor panel + two internal adversarial,
  reproduce-before-accept); findings converged on the sealed-payload gap (closed), a leash-dispatch
  **fail-open on any unrecognised leash string** (closed — allowlist dispatch + validated leashes +
  `apply_cap` never returns an unknown), the **`source`-as-provenance mistake above** (closed), a
  content-type covert/unaudited channel (closed — ASCII-only + recorded), a lone-surrogate-body crash
  (closed — refusal + backstop), and smaller audit/leash-hygiene items.
- **Perception egress (`web_research`) is *surfaced + bounded*, not held by default.**
  The v0 build (code-panel decision) routes a research GET through the one governance
  gate as a **governed, audited `web_fetch` Decision** (surfaced on the bus), default-
  deny, **request-target-capped**, its body **`_neutralize`d before the model sees it**
  (redacting secret-shaped tokens) and UNTRUSTED-tagged. It runs autonomously so it can
  ground a proposal — the injection/exfil risk to an *allowlisted-for-read* host is
  **bounded** by default-deny + the target cap + neutralization + audit rather than by a
  pre-emption hold. An operator who wants the strict hold **can** cap `web_fetch` to
  `propose_first` in the signed grant, which holds research egress too. (The earlier
  "floored to `propose_first`" framing was tightened to this after the build red-team
  showed a strict hold makes autonomous research near-redundant with proposing a
  `web_fetch`; the honest bound is structural, not a hold.)

### Tier 3 — Scope-gated high-impact ops (designed here, unlocked later)

Actions affecting a third party (offense, authorized testing) are **unreachable in
v0**: no capability names a non-consented target, and there is no in-band path to
mint one. When a legitimate authorized-offense workflow is actually needed, the
*only* unlock is an out-of-band **target-scope artifact** — and its trust root is
stated now even though the format is deferred:

> The artifact is legitimate **only if rooted in a key the operator does not hold**
> (the target owner's signature, or a mutually-trusted engagement authority). An
> operator-minted "I authorize X" is the operator manufacturing third-party consent
> from *inside* the trust domain — that is an ordinary in-band grant with ceremony,
> **not** legitimacy, and must never be accepted as the artifact.

This keeps Tier 3 honest: either it stays locked (third-party offense prohibited),
or it unlocks through a *different trust domain*, never by laundering an in-band
grant.

### The prohibition, done structurally (not by a recognizer)

The un-grantable class is enforced **by construction**: (a) egress is default-deny;
(b) the allowlist can hold only operator-owned/consented hosts; (c) there is no
in-band code path to add a non-consented host (Tier 3's artifact is the only door,
and it is out-of-band by trust root). An injected or mis-salienced proposal toward a
non-allowlisted target is denied by **absence of capability**, not by a guess about
the agent's intent. A best-effort **string recognizer** for obvious offense
tool-shapes (`sqlmap`, `nmap`, exploit signatures in a `run_command`) is kept **as
audit-only defense-in-depth — it records, it does not deny** — so it never
false-positives your own authorized box and never provides false assurance as a
boundary.

### `web_research` lift, with injection floors

`web_research` (`research.py`) lifts from DEFERRED to **allowlisted read-only GET**
through the mediated client. But the review confirmed, against the code, that
fetched content is an origination vector: `run_research` folds findings into the
proposer context (`research.py:143-145`) → the proposer's user message
(`propose.py:183-184`) → `_candidate_from_response` stamps `source="proposed"`
(`propose.py:161`). So web content **can** induce the proposer to originate a
`source="proposed"` action. Therefore:

- Research egress runs through the **one governance gate** (not a parallel authority
  check) as a **surfaced, audited, bounded** `web_fetch` (see Tier 2): default-deny,
  request-target-capped, `_neutralize`d, UNTRUSTED-tagged. An injected next-step is
  bounded by those structural controls; it can still emit to an *allowlisted-for-read*
  host (a stated non-goal), which is why the bound is default-deny + cap + neutralize +
  audit rather than a claim that it "cannot auto-emit."
- Fetched content is tagged **adversarial-provenance** (a distinct render class from
  workspace reads) and recorded in the audit, so a forensic reader can see what
  drove a proposal.
- **The system is not injection-resistant, and this ADR does not claim it is.**
  Web content is adversary-controlled; workspace content is operator-controlled;
  treating them as equivalent perception was the v1 error.

## Consequences — honest non-goals

- **P-01 is a *capability* invariant, not an *exfil* invariant.** A correction to
  v1: a *deny-only* classifier shrinks the action set and never grants, so it would
  not violate P-01 — the reason to avoid a recognizer *as the boundary* is that
  offense is unobservable from intent-shape, not P-01. And note the residual: two
  allowlisted-for-read GETs can exfiltrate a secret without widening any capability
  — P-01 holds while the security goal is still defeated. The structural model
  bounds *reach*, not *what rides an authorized channel*.
- **Same-channel egress is tamper-evident *logging*, not verification.** It is
  renamed accordingly; it does **not** carry the "hands can't lie" property (the FS
  verifier re-observes independently of the executor; here the observer *is* the
  executor). Independent observation is a revisit trigger, not a v0 claim.
- **`run_command`'s raw network reach is now ISOLATED on Linux (revisit #1 shipped).**
  `run_command` executes inside a fresh, unprivileged **network namespace** with no route
  out (`collaborator/netns.py`), so a raw socket / `curl` / `git push` fails closed and
  **`egress.py` is the sole IP-network path off the machine** — which is what makes the
  same-channel egress log *sound* for IP egress (no other IP channel for bytes to leave by).
  Isolation is **verified, not trusted** — the probe and each run confirm the child is in a
  genuinely distinct netns (`/proc/self/ns/net` inode), so a substituted / broken /
  `LD_PRELOAD`-hooked `unshare` that exits 0 without isolating is caught (fail closed + honest
  flag), never reported as isolated. It is **OS-gated**: where netns is unavailable (non-Linux,
  no `unshare`, userns disabled) the shell runs unisolated and the run is **honestly flagged**
  `network_isolated=False` — never a silent claim. Scope limit: a network namespace isolates
  the *network*, not the filesystem or *pathname* UNIX-socket IPC — so a network-capable local
  daemon socket (notably a Docker socket, `systemd-resolved`, a local proxy) is a residual
  confused-deputy path on hosts that expose one; `--mount`/seccomp is the follow-up hardening.
- **Known seam-fallers the tiered model does not fully close** (documented, not
  hidden): exfil of the operator's *own* secret to an allowlisted-for-read host (the
  missing axis is *what leaves*, not *where to*); and offense *through* a sanctioned
  channel (harassment/spam via an allowlisted API — grantable reach, third-party
  harm). Both are best-effort-mitigated (request-target cap, fixed header set,
  `_neutralize` on research findings, and the `propose_first` floor on
  proposer-originated emission), not solved.
- **The new-channel flow is an operator caps re-mint, not a Collaborator emission.**
  The v1 "identical to controlled-location staging" claim was wrong: inward,
  `fs.write:project` is coarse and `is_controlled_location` denies a sub-region
  *after* the gate; outward, the destination *is* the capability, so un-allowlisted
  egress hard-denies *at* the gate. Adding a host is an operator/authority action
  (re-mint the signed caps), and the proposer's "staging" is merely a scratch note
  proposing it — different trust actor, different mechanism.
- **Auditability.** Each egress decision (canonical destination, method, allow/deny,
  request-target hash, bytes) and each audit-only recognizer hit is recorded like
  every other governed action; bodies are not logged raw by default (hash + length +
  provenance), keeping the audit body-free in line with the bus (ADR 0001/0002).

## Design (v0 shape) — implementation sketch

Host-side, core untouched.

1. **`collaborator/egress.py` (new).** The single mediated client implementing the
   Tier-1 mediation contract (canonical parse, no-redirect, IP-pin + private/CGNAT/
   metadata block, HTTPS, request-target + response bounds). Returns an `EgressRecord`
   (canonical dest, method, request-target hash, response hash/length, bytes, status).
   Only this module touches the network. The record is **channel-integrity logging**
   (as-built it lives collaborator-side — **core untouched in v0**), explicitly not the
   independent-world observation used for `verify_mode="artifact"`; graduating it into
   `salienceos/verifier/observers.py` is a revisit item.
2. **`collaborator/tools.py`.** Register `web_fetch` (`op="net.get"`,
   `verify_mode="egress_log"`, `egress=True`, an **un-grantable sentinel** static
   capability so a dropped `egress=True` can never fall back to a wildcard). Keep the
   audit-only offense recognizer here as a predicate that *tags*, never denies.
3. **`collaborator/governance.py`.** Add the **capability-derivation step** for
   egress tools (compute `net.get:<canonical-host>` from `intent.args`, gate on it),
   and extend `reauthorized_or_denied` (`:306`) with an egress branch that
   re-derives the capability from the *current, frozen* destination and re-checks
   the signed allowlist at approval time (closing the emission TOCTOU).
4. **`collaborator/policycaps.py` + `session.py`.** `net.get:<host>` capabilities
   ride the existing signed-caps path; the allowlist *is* those caps (default empty
   = default-deny). No mutable session allowlist.
5. **`collaborator/research.py`.** `web_research` performs a read-only GET by routing
   through the **one governance gate** (`govern_action` on a `web_fetch` intent) — a
   surfaced, audited, bounded Decision, not a parallel authority check. Findings render
   through `_neutralize` (redacting secret-shaped tokens before the model sees them) and
   carry the UNTRUSTED tag applied at the tool.

## Verification (when it builds — both passes are a gate, not a suggestion)

This is authority-floor code plus a new trust boundary: internal adversarial
subagents **and** an external multi-vendor panel on the *shipped modules*,
reproduce-before-accept. Attack surface: canonicalization divergence
(authorize-vs-connect host), redirect/rebind/SSRF-to-metadata, GET exfil via
query/header, the capability-derivation step (can a crafted arg mint or dodge a
capability?), emission TOCTOU across the re-gate, the audit-only recognizer's
false-assurance, and the web-content → proposer injection path end to end. Unit
tests: default-deny (no host reachable without a named signed cap); capability
derived == canonical connect host; redirect/private-IP fail closed; approval re-gate
re-derives and re-checks the allowlist; audit-only recognizer never denies.

## Revisit triggers

1. **netns for `run_command` — DONE on Linux (`collaborator/netns.py`).** `run_command`
   now runs in a fresh network namespace with no route out, so `egress.py` is the sole
   IP-network path off the machine and the "bytes left by another path" gap is closed for
   IP egress. Remaining under this trigger: (a) the same guarantee on non-Linux hosts
   (netns is Linux-only; today those honestly flag `network_isolated=False`); (b) the
   *further* hardening of an **independent** egress observer (a proxy outside the executor)
   — now optional rather than prerequisite, since with `run_command` isolated the
   same-channel log has no other channel to miss.
2. **Side-effecting egress (`net.post`) — DONE (`collaborator/egress.post` + `net_post`).**
   The Tier-2 emission flow shipped: separate `net.post:<host>` authority, human-gated by default
   with a signed per-host `net.post.auto:<host>` autonomy lift (operator-directed only), host-injected
   credentials, a body-free-vs-preview audit split, and a hold-time payload seal (no verifier exists
   for an emission, so approved==sent is bound). Remaining under this trigger: `net.put`/`net.delete`
   and other verbs (only `net.post` was needed); a richer per-destination emission quota.
3. **A legitimate authorized-offense workflow is actually requested** — design the
   Tier-3 target-scope artifact, honoring the trust-root invariant (rooted in a key
   the operator does not hold). Until then Tier 3 stays locked.
4. **Structural un-grantability graduates to core** — move it into `salienceos/` as
   P-01's sibling: a signed `PolicyCaps` that *structurally cannot express* a
   capability naming a non-consented destination, so the prohibition is enforced in
   core rather than by the Collaborator declining to mint one.
