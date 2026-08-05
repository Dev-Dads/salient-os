# ADR 0001 — Salience bus audit-chain integrity scope (`verify_chain`)

- **Status:** Accepted — 2026-08-05
- **Scope:** `salienceos/interpreter/bus.py` (`SalienceBus.verify_chain`)
- **Related:** `red-team/interpreter/00-REDTEAM-SYNTHESIS.md` (Grok F3), and the
  verifier's equivalent boundary in `00-VERIFIER-FINAL-RECOMMENDATION-v3.md` §1.

## Context

The salience bus is an append-only, hash-chained audit surface. `verify_chain()`
recomputes each entry's hash, checks `prev` linkage, and compares the last hash to
the stored head.

The interpreter red-team (Grok F3) correctly observed that this detects accidental
corruption, truncation, and reordering — but **not** a fully-consistent malicious
rewrite. An adversary who can write the store can rewrite every entry, recompute
each hash and `prev`, update the head, and pass verification, because the check
recomputes the same function over adversary-controlled data and trusts its own
head. A hash chain is tamper-evident only relative to a **trusted anchor**; the
current implementation has none outside the store.

## Decision

**Leave `verify_chain()` as-is for now.** This is a deliberate scoping decision,
not an oversight.

The binding threat model (inherited from the verifier work) scopes **in** a
wrong/misfiring subsystem, a buggy publisher, and non-malicious corruption; a
malicious actor with write access to the store is **out of scope** — the same
boundary the verifier draws for a kernel-capable executor. Under that model,
`verify_chain()` already meets the requirement. The `bus.py` module docstring
states this limit; do not claim compromise-resistance we do not have.

## Deferred hardening (if a malicious store-writer becomes in scope)

Graduated, cheapest first; each rung closes a specific attacker capability:

1. **Verify against an externally-held anchor**, not `self._head` — change the
   signature to `verify_chain(trusted_head)`. Zero-dependency. Detects a
   wholesale-but-internally-consistent rewrite whenever the caller holds a
   last-known-good head from an out-of-band channel. (Cheapest real improvement;
   fixes the "trusts its own head" defect directly.)
2. **Sign the head** (one signature authenticates the whole prefix, since each
   entry commits to `prev`). HMAC keeps the stdlib-only discipline and stops a
   store-writer who lacks the key; Ed25519 is strictly stronger (verification
   needs only the public key, so the private key can live in a separate
   signer/HSM) but adds a crypto dependency — an explicit discipline break to
   decide, not default.
3. **Anchor the signed head** in a trust domain the writer cannot rewind
   (append-only/WORM medium, a witness/second node, or a TPM monotonic counter)
   to close rollback/truncation. Requires a second trust domain — the rung
   software-on-one-node cannot self-provide.
4. **Authentic history vs a root-capable actor** = hardware root of trust
   (measured launch / TPM / enclave) — the same out-of-scope workstream as the
   verifier.

## Consequences

- The durable audit record is trustworthy against corruption and bugs, **not**
  against a malicious actor with write access to the store.
- **Revisit when:** (a) the bus persists across trust domains — shared/exported
  storage, backups treated as authoritative, or multi-node; or (b) the product
  threat model adds a malicious store-writer. At that point start with rungs 1–2
  (both small and mostly in-discipline) and add rung 3 when there is a second
  trust domain to anchor to.
