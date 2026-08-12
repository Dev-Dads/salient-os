# Backlog — decisions that need the operator

Items I've deliberately **not** built autonomously because they either reverse a recorded decision or
require an infrastructure/authority choice only the operator can make. Each has the honest options and a
recommendation. (Self-contained follow-ups that I *can* judge are being built directly with the usual
build → external-panel → merge → heartbeat loop; they are not listed here.)

---

## 1. Tier-3 authorized-offense UNLOCK — needs a real second trust domain (or an explicit override)

**Ask:** "go for the tier 3 offense artifact."

**Why it's here, not built.** The Tier-3 *unlock* (ADR 0003 revisit #3 — an out-of-band `offense:<target>`
scope artifact) was already **design-paneled 4/4 SERIOUS_FLAWS** and **formally deferred by ADR 0004**.
The unanimous finding: on a **single node** the operator provisions the trust-anchor set AND (by
generating a keypair) the signing key, so *"rooted in a key the operator does not hold"* is not achieved
— it is **operator self-issuance with Ed25519 ceremony**, the exact laundering ADR 0003 forbade. A
second panel point: a human-approved `run_command` already reaches a third party (`nc victim 443`,
`/dev/tcp`) with no artifact at all, so a Tier-3 gate over a single probe adds dual-use surface without
adding real control on one node. Building the single-node version tonight would knowingly ship the thing
the panel + ADR rejected — a reversal that needs your explicit call, not my best judgement.

**What IS already done (the honest Tier-3 posture today).** The *structural lock* (ADR 0004 / revisit
#4, PR #41) is shipped: the `offense:` namespace is **un-grantable by core construction**. I
re-certified it internally tonight and it is **sound** — four independent layers, no bypass:
1. `policycaps.mint` rejects an `offense:` cap loudly at authoring;
2. `issue_policy` strips the namespace before signing (`policy.py:117`);
3. `Directive.grants_capability` refuses it **unconditionally before the membership check**
   (`directive.py:61`) — and it is the **sole** authority chokepoint (every authority decision in
   `governance.py` routes through it; there is **no** direct `cap in allowed_capabilities` authority
   read anywhere else);
4. `granted_capabilities` strips it on **both** the legacy and signed read paths (`policycaps.py:163,167`).
   Matching is NFKC + casefold, so full-width (`ｏｆｆｅｎｓｅ：`) and case (`OFFENSE:`) variants can't slip.
   *Residual (documented, non-exploitable):* NFKC does not fold cross-script homoglyphs (Cyrillic `о`) or
   strip zero-width/space variants — but no offense **consumer** exists, so such a cap authorizes nothing,
   and it would not match a future consumer's canonical form either. Not worth a change today.

**The decision I need from you.** To honestly unlock Tier 3, pick the trust root (or override):

- **(A) Build the "third agent" / separate maintenance trust domain first** (ADR 0002's out-of-process
  limit). A real second domain is the prerequisite the panel named; once it exists, the scope-artifact
  unlock stops being ceremony. **Biggest, most honest, most reusable** (it also unblocks the F2 laundering
  residual and the maintenance-trust separation). — *my recommendation, if you want Tier 3 for real.*
- **(B) Provide an external signer / offline-ceremony / target-owner-challenge** whose anchors this
  runtime cannot author, and I build only the *verifier* against it. Needs you to stand up (or specify)
  that external root; without it the verifier is ceremony.
- **(C) Override the panel and build the single-node version anyway**, accepting on the record that it is
  operator self-issuance with ceremony (I'd want that acceptance explicit, given ADR 0004).
- **(D) Leave Tier 3 locked** (today's state) and treat "authorized offense" as out of scope for this
  system. The lock already makes it structurally impossible; a human-approved shell remains the only
  third-party reach, governed by the isolation arc.

I recommend **(A)** if authorized offense is a real goal, else **(D)**. Either way, no offense executor
gets built until a real second domain exists.

---

## 2. DNS-aware content-mediating egress proxy (ADR 0005 follow-on) — needs a design pass + your steer

ADR 0005 rejected the dest-only observed route and named the coherent evolution: an SNI/allowlist,
DNS-aware, **content-mediating** proxy (drifting toward a userspace TLS-terminating proxy) if
network-for-maintenance ever becomes a hard requirement. This is a large privileged build with real
design forks (where the proxy runs, TLS interception posture, allowlist authority). Backlogged for a
design pass + your direction; not an overnight best-judgement build.

---

## 3. "Third agent" — separate maintenance trust domain (ADR 0002) — architectural, needs your steer

The out-of-process separate trust domain that would let governance survive a compromised tool process
(and would be the enabling root for item 1(A)). Big ADR-0002-scale work; needs your direction on the
process/isolation model before any build.

---

_Overnight autonomous progress + the self-contained follow-ups I'm building directly are tracked in the
PRs and in memory; this file is only the operator-decision queue._
