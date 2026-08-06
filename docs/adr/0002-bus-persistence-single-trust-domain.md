# ADR 0002 — Salience bus persistence stays in a single trust domain

- **Status:** Accepted — 2026-08-06
- **Scope:** How the host (quorum-agent, the test rig) persists the salience bus —
  `SalienceBus(path=...)` in `salienceos/interpreter/bus.py`. Not a change to
  `bus.py`; a decision about the storage it is pointed at.
- **Related:** ADR 0001 (`0001-verify-chain-integrity-scope.md`) — this ADR is the
  promised "revisit when the bus persists across trust domains" check, and finds it
  does not. `docs/ROADMAP-plain-language.md` Stage 2. Wiring plan PR-H0/H1/H2.

## Context

Stage 2 wires the salience bus into the host so real activity produces a durable,
turn-correlated audit record. That record has to live somewhere. PR-H1 persists it
as **one append-only JSONL file per session**, under the host's own home directory
(`get_hermes_home()/salience/`). A reopened bus (session resume, host restart)
continues its own chain via the replay-on-open already built in PR #5:
`SalienceBus._replay` rebuilds state from the file, verifies each entry while
loading, and fails closed on a corrupt, discontinuous, or key-smuggling tail
(`bus.py:106`). Without that, a second process would append after the existing
lines with `prev=""` and permanently break `verify_chain()` at the junction.

ADR 0001 accepted the bus's integrity scope — `verify_chain()` catches accidental
corruption, truncation, and reordering, but **not** a fully-consistent malicious
rewrite by an actor with write access to the store — and deferred the hardening
rungs (externally-anchored head, signed head, second-domain anchor). It named the
condition under which those rungs must be revisited: **"when the bus persists
across trust domains — shared/exported storage, backups treated as authoritative,
or multi-node."** Stage 2 is the first time the bus persists at all in a host, so
that condition has to be evaluated, not assumed.

## Decision

**The Stage-2 persistence stays inside one trust domain, so ADR 0001's deferred
rungs 1–2 remain deferred.** This is a deliberate finding, not an omission.

The per-session JSONL is written and read by one host, on one node, under that
host's own home directory, by the same process lineage across a resume. It is not
shared storage, not an exported/mounted volume, not multi-node, and backups of it
are not treated as an authoritative head. None of ADR 0001's revisit triggers are
met. The attacker who can consistently rewrite the file and its head is the same
malicious-store-writer ADR 0001 scopes **out** — the same boundary the verifier
draws for a root-capable executor. Replay-on-open closes the *accidental* junction
break (the in-scope case: resume/restart continuing an honest chain); it does not
claim to close the malicious-rewrite case, and this ADR does not either.

### The complement to quorum_dispatch, not a backdoor around it

The host already has a decision feed in `quorum_dispatch` that is deliberately
**non-durable, process-local, and uncorrelated** (its events are not persisted as
an authoritative cross-process record). The salience bus is the **durable,
turn-correlated complement** to that feed — it exists precisely to hold the record
quorum_dispatch intentionally does not. It must not become a backdoor that
re-durables what quorum_dispatch keeps ephemeral. Two constraints enforce that:

- **The audit fence (Finding G, already in `bus.py`).** Signals and directives
  carry only bounded, ref-shaped tokens — never prompts, bodies, args, or
  chain-of-thought. `valid_signal` and the directive payload fence reject anything
  prompt-sized at both emit and replay. The bus is structurally incapable of
  durably holding the content quorum_dispatch discards.
- **Hashed session component in durable subjects.** Subjects written to the bus
  carry a **hashed** session component, never a raw session id, so the durable
  record cannot be trivially re-associated back into the ephemeral, uncorrelated
  feed it complements.

## Consequences

- The Stage-2 audit record is trustworthy against corruption, bugs, and honest
  resume/restart — **not** against a malicious actor with write access to the
  host's home directory. That is the accepted, documented scope.
- **Rotation/retention posture (v0):** per-session files accumulate under
  `get_hermes_home()/salience/`; there is no rotation, expiry, or compaction in
  Stage 2. This is acceptable while the record is a debugging/acceptance artifact
  on the operator's own machine. A rotation/retention policy is required before any
  deployment where these files are shipped off-box or retained at scale — and
  shipping them off-box is itself an ADR 0001 revisit trigger (see below).
- **The disagreement property is library-real but product-dormant in v0.** Stage 2
  wires the host with `allow_adaptation=false`, so `interpret()` never stamps
  `RISK_EXCEEDED` and the memory/weight channels never actually disagree in
  production — no inhibitor is ever produced. The safety property proven by the
  consumer tests exists in the library; it does not fire in the v0 product until
  the memory consumer lands. Stated here so no one reads a Stage-2 session log as
  evidence the quarantine path works end to end in the host. See ROADMAP Stage 2.

## Revisit triggers (inherited from ADR 0001, made concrete for Stage 2)

Start with ADR 0001 rungs 1–2 (both small, mostly in-discipline) when any of these
becomes true:

1. The per-session JSONL is **shipped off the host** — shared/mounted/exported
   storage, cross-node aggregation, or a backup treated as an authoritative head.
2. The **product threat model adds a malicious store-writer** (e.g. the home
   directory is no longer a single-owner trust boundary).
3. The bus record becomes **load-bearing for a real decision that is hard to
   reverse** — Stage 2's one governed knob (compute budget) is conservative and
   reversible; a future consumer that gates something irreversible raises the value
   of an authentic head and should re-open this ADR.
