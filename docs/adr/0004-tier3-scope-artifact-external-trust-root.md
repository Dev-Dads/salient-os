# ADR 0004 — Structural un-grantability of the prohibited capability namespace (Tier-3 stays locked)

- **Status:** Proposed — 2026-08-11. Advances ADR 0003 revisit-trigger #4 (graduate structural
  un-grantability into core). **Defers** revisit-trigger #3 (the Tier-3 authorized-offense unlock)
  after a design panel showed it cannot be honestly delivered on a single node.
- **Scope:** Make the PROHIBITED CLASS of ADR 0003 — a capability naming a third party the operator
  cannot prove authority over ("offense") — **un-expressable by construction in `salienceos/`
  core**, so the prohibition is enforced by the core capability invariant (P-01's sibling), not by
  the Collaborator declining to mint one. Tier 3 stays locked; no new authority, no offense
  executor, no new dependency.
- **Related:** ADR 0003 (the tiered ladder; the trust-root invariant §170-178; revisit #3 the
  offense unlock, revisit #4 "graduate structural un-grantability into core" §349-352). ADR 0002
  (single-trust-domain limit — the reason the Tier-3 unlock is deferred). The core capability path
  (`salienceos/interpreter/{policy.py,directive.py}`) and the signed HMAC grant
  (`collaborator/policycaps.py`). **Design review:** external multi-vendor panel on the *original*
  Tier-3-unlock design (`red-team/collaborator/redteam_scopeartifact.py`, raw at
  `raw_scopeartifact/`) — 4/4 SERIOUS_FLAWS, which produced this narrower, honest decision.

## Context

ADR 0003 built a default-deny egress ladder and left Tier 3 (high-impact ops against a THIRD PARTY —
authorized offense) **locked**: no capability names a non-consented target and there is no in-band
path to mint one. Two follow-ups were named: revisit #3 (design the out-of-band **target-scope
artifact** that could unlock Tier 3, "rooted in a key the operator does not hold") and revisit #4
(graduate the structural un-grantability into core).

We designed the revisit-#3 unlock first — an Ed25519 scope artifact verified against an external
authority's public key — and, per the empirical-adversarial discipline, ran an external design panel
on it **before building**. The panel (4/4 SERIOUS_FLAWS) converged on one decisive point:

> On a single node, the operator provisions the trust-anchor set, so the same principal controls the
> anchor registry AND (by generating a keypair) the signing key. "Rooted in a key the operator does
> not hold" is not achieved — it is operator self-issuance with Ed25519 ceremony. ADR 0003 forbade
> *laundering-with-ceremony*; the artifact design shipped *laundering-with-Ed25519-ceremony* and
> relabelled it a residual. Stating the residual honestly does not convert a violated invariant into
> a raised bar.

A second panel point sharpened it: a **human-approved `run_command` already reaches a third party**
(`nc victim 443`, bash `/dev/tcp`) with no artifact at all — so a Tier-3 gate over a single probe
adds dual-use surface without adding real control on one node.

**Conclusion:** unlocking Tier 3 on a single node is ceremony, not a trust boundary. The honest,
valuable move is the one ADR 0003 already named as the prerequisite (revisit #4): make the prohibited
class **structurally un-grantable in core**, so Tier 3 is locked *by construction* rather than by the
Collaborator's good behaviour — and defer the unlock until a real second trust domain exists.

## Decision

### A reserved, un-grantable capability namespace in `salienceos/` core

Define, in core, a reserved prefix set for the prohibited class and refuse it at the capability
invariant:

- **`salienceos/interpreter/policy.py`** — `RESERVED_UNGRANTABLE_PREFIXES = ("offense:",)` and a
  total helper `is_ungrantable_capability(cap) -> bool` (a string starting with a reserved prefix).
  Single source of truth for the prohibited namespace.
- **`Directive.grants_capability` (`directive.py:56`)** — refuse the namespace **unconditionally**:
  `if is_ungrantable_capability(capability): return False` *before* the membership check. This is the
  load-bearing structural guarantee — **no directive can grant an `offense:` capability regardless of
  what its `allowed_capabilities` contains**, even a hand-constructed or mis-wired one. The
  prohibited class is un-grantable, full stop (P-01's sibling: policy cannot authorize it).
- **`issue_policy` (`policy.py:66`)** — strip any reserved-namespace capability from
  `granted_capabilities` before signing, so a prohibited cap never even rides in a signed envelope
  (defense in depth + a clean audit trail; `grants_capability` refuses it regardless).
- **`collaborator/policycaps.py`** — `mint` rejects a reserved-namespace capability (fail LOUD, like
  the leash-cap validation) so the ordinary authoring path cannot produce such a grant; and
  `granted_capabilities` STRIPS the namespace on the read path, so even a grant hand-built OUTSIDE
  `mint` (a valid signature over offense caps — the operator holds the HMAC key) never rides its
  offense caps into the seam (external-panel grok/gpt: `mint` is only one construction path). Belts —
  the load-bearing guarantee is core's `grants_capability` refusal, which holds regardless.

The check is Unicode-normalized (NFKC + casefold) so a confusable — a full-width `ｏｆｆｅｎｓｅ：` or a
case variant `OFFENSE:` — cannot slip a variant past the reservation (external-panel gemini).

No existing capability (`fs.*`, `shell.exec`, `net.get:`, `net.post:`, `net.post.auto:`,
`shell.raw_network`, `shell.contained_autonomy`) uses the `offense:` prefix, so nothing in the system
changes behaviour — this only closes the door to expressing the prohibited class.

### What this achieves (honestly)

- **Tier 3 is locked by CORE construction.** The one capability that could unlock third-party offense
  (`offense:<target>`) cannot be expressed by any signed grant, cannot be carried by any directive,
  and cannot be minted by the operator's HMAC path. Not the model, not the operator, not a buggy or
  second Collaborator, not a mis-wired grant.
- **The laundering path is closed at the capability layer.** ADR 0003's "no in-band path to mint a
  non-consented capability" is now enforced *in core*, not by the Collaborator choosing not to.
- The audit-only offense recognizer (`flag_offense_shape`) stays **audit-only, never a boundary**.

### What this does NOT claim (the honest limit)

- It does **not** unlock authorized offense. A legitimate Tier-3 engagement remains impossible in
  band — deliberately.
- It does **not** manufacture a second trust domain. A future Tier-3 unlock must come through a
  **separate trust root** (an external-key scope artifact whose anchors are provisioned by a process
  the operator runtime does not control), **never** through this core capability path — which now
  structurally rejects the offense namespace. The scope-artifact design is preserved for that future
  (see Deferred), but is not built here because single-node it degrades to ceremony.
- It does **not** constrain a human-approved `run_command` from reaching a third party; the shell's
  raw reach is governed by the netns/contained isolation arc (Thread #1), not by this namespace.

## Deferred (the future Tier-3 unlock, when a real second domain exists)

The revisit-#3 unlock — an out-of-band target-scope artifact — is deferred, not discarded. When it is
built it must: be **rooted in anchors the operator runtime cannot author** (a real second trust
domain: an offline ceremony, a remote signer, or a target-owner challenge — not an operator-provisioned
dict); verify with `key_id` bound in the signed payload; consume a nonce bound to the workspace/
engagement to stop replay; enforce a maximum window and a revocation channel; and reach a target only
through the shared `canonical_host` + IP-pin + private/metadata blocks. Until then Tier 3 stays locked,
now **by core construction**.

## Non-goals / residuals

- No offense executor / probe in v0 (the design panel's dual-use + single-node-ceremony concerns).
- No `cryptography` dependency (stays stdlib-only; the asymmetric verifier is deferred with the unlock).
- The prohibited namespace is a *prefix reservation*, not a semantic offense detector — offense is
  unobservable from a capability string; this reserves the class that a future artifact would name,
  and forbids the in-band path to it. Semantic offense recognition stays audit-only.

## Revisit triggers

1. A real second trust domain becomes available (anchors the operator runtime cannot author) — build
   the deferred scope-artifact unlock on that root, honoring the spec above.
2. The prohibited namespace needs to grow beyond `offense:` (a new class of un-grantable capability) —
   extend `RESERVED_UNGRANTABLE_PREFIXES`.
