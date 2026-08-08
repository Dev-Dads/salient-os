# ③ Signed PolicyCaps — design for review

*The deferred hardening (both the Collaborator plan and the resource governor punted it):
give the Collaborator's authority a **signed artifact** the judgment host verifies, instead
of mutable host config. "Policy authorizes" made cryptographic — the config and the Step-2
control surface can only ever operate **within** the signed grant. This doc is the design
put to the panel BEFORE implementation; judge its threat model and honesty.*

## The gap it closes

Today the Collaborator's authority lives in **mutable session config**:

- `session.capabilities` — a plain tuple. Anything that mutates it (a bug, an injected
  value) silently widens what the worker may touch.
- the leash — `session.leash_overrides`, which the Step-2 judgment view's `set_leash`
  writes. A misbehaving or compromised control surface could **loosen** `run_command` from
  `propose_first` to `act_then_report` and auto-run shell commands.

Nothing binds "this worker was granted exactly these capabilities and no looser than these
leashes, by this authority." That binding is what ③ adds.

## PolicyCaps: a signed grant

    PolicyCaps = { capabilities: (…), leash_caps: {tool: max_looseness},
                   issuer: str, subject: str }         # subject binds it to ONE workspace
    SignedPolicyCaps = { caps: PolicyCaps, signature: HMAC-SHA256(canonical(caps), caps_key) }

A **policy authority** mints a signed grant with `caps_key`. A `Session` may carry the
`SignedPolicyCaps` and the key it verifies against. Then, **every governed action**:

1. **verifies** the grant (signature over the canonical caps, and `caps.subject` ==
   this session's workspace — no replay onto another session). Invalid/absent-when-required
   ⇒ **fail closed**: zero capabilities, strictest leash.
2. sources the capability set from the **verified** caps (not the mutable tuple), then
   feeds that to the existing core `issue_policy`/`interpret` capability gate.
3. resolves the leash as today, then **caps** it: `effective = stricter(host_leash,
   leash_cap)`. The host (or the view) may **tighten** but never loosen past the signed cap.

So the two layers are clean: **PolicyCaps is the grant** (what the authority permitted);
the per-action **policy is the request** (core-signed, already exists). The request can
only ever ask for what the grant allows.

## The properties it must hold

- **No widening.** With a signed grant present, mutating `session.capabilities` cannot add
  a capability (the verified caps are authoritative), and `set_leash`/`leash_overrides`
  cannot loosen a tool past its `leash_cap`.
- **Tamper-evident, fail-closed.** Any edit to the caps without a valid re-signature is
  detected → the grant is rejected → zero capabilities + strictest leash. Absent key when a
  grant is present, wrong subject, malformed caps → same.
- **Backward compatible.** A session with **no** PolicyCaps behaves exactly as today
  (legacy plain-config path) — the signed path is opt-in hardening, so ④/①/② stay green.
- **Provenance.** The grant carries an `issuer`; an audit can confirm authority, not
  conjure it.

## Leash ordering (the cap is a floor on strictness)

    act_then_report (0, loosest)  <  propose_first (1)  <  notify_only (2, strictest)
    effective_rank = max(host_rank, cap_rank)     # cap = "no looser than this"

A `leash_cap` of `propose_first` for `run_command` means the view may set it to
`propose_first` or `notify_only`, never `act_then_report`.

## Honest scope — single trust domain (read this before crying "theater")

The signing is **symmetric HMAC** (stdlib only; the core is `hashlib`/`hmac`-limited and we
add no dependency). The session that verifies holds the same `caps_key` that signs, so
within one process a fully-adversarial component that holds the key could re-sign. What ③
therefore delivers, honestly:

- **integrity + tamper-evidence:** authority is a verified artifact; a non-crypto mutation
  of capabilities or a leash-loosen by the control surface is **detected and fails closed**
  — the realistic threat (a bug or an injected config flip in a *different* component that
  doesn't go through `mint`) is stopped.
- **provenance** and a **principled home** matching the rest of salienceos (signed
  policies/receipts).
- It is **not** a hard boundary against a fully-compromised in-process re-signer. A separate
  authority process and/or asymmetric keys (ed25519) is the real cross-domain boundary, and
  is the deliberate next step — consistent with ADR 0002 (salience persistence stays in a
  single trust domain).

This is the same honesty the whole system insists on: name the boundary, don't oversell it.

## What gets built

    collaborator/policycaps.py   PolicyCaps, SignedPolicyCaps, mint(), verify(),
                                 granted_capabilities(session), leash_cap(session, tool),
                                 apply_cap(leash, cap)
    collaborator/session.py      + optional policy_caps + caps_key (opaque; verified per action)
    collaborator/governance.py   govern_action + reauthorized_or_denied source caps from the
                                 verified grant and cap the leash
    tests/test_collaborator_policycaps.py
    red-team/collaborator/policycaps_proof.py (+ output)

## The proof

1. **No widening (capability):** a session whose signed grant lacks `shell.exec` — set
   `session.capabilities = (…, "shell.exec")` by hand → `run_command` is still **DENIED**
   (the grant is authoritative).
2. **No loosening (leash):** a grant caps `run_command` at `propose_first`; the view calls
   `set_leash(run_command, act_then_report)` → the action is still **HELD**, not auto-run.
3. **Tamper → fail closed:** flip a capability in the caps without re-signing → verify
   fails → every action DENIED, leash strictest.
4. **Replay blocked:** a grant minted for workspace A, presented on workspace B → rejected.
5. **Legacy unchanged:** no PolicyCaps → behaves exactly as today.
6. **Tighten still works:** the view can still tighten within the grant.

## The decisions that stay the host's

The authority key management (who is the policy authority, how `caps_key` is provisioned)
is host/ops policy, not code — v0 takes the key as host input, exactly as the executor key
does today.

## Panel outcome (5-model external review, post-review revisions)

Verdict: **SERIOUS_FLAWS** (grok, glm, mistral, kimi) / MINOR_ISSUES (deepseek) — no single
CRITICAL, but a cluster of real **fail-open** gaps the design/implementation had to close.
All fixed and each pinned by a test in `tests/test_collaborator_policycaps.py`:

- **Grant-stripping fails open** (consensus HIGH): nulling `session.policy_caps` at runtime
  reverted to the mutable tuple. **Fixed:** a **sticky `enforce_caps`** flag set at
  construction — a session built with a grant fails closed when the grant is later
  absent/invalid, never reverts to legacy. Legacy only when constructed with no grant.
- **Unlisted tool fails open** (HIGH): a granted tool with no `leash_caps` entry was
  uncapped. **Fixed:** under a grant, an unlisted tool defaults to **`notify_only`**
  (strictest — silence never confers looseness).
- **Subject not normalized** (MEDIUM): relative/symlink replay. **Fixed:** `subject` is the
  **resolved absolute path** (`workspace_subject`), resolved on both mint and verify.
- **`verify()` not total** (HIGH per mistral): **Fixed:** it now catches everything and
  returns `False` — never raises, never a pass.
- **Leash cap only at origination** (glm/mistral): **Fixed:** the cap is also applied in
  `execute_and_verify` (the terminal enforcement point) so the recorded leash is effective
  and no future caller reaches it uncapped.
- **View showed mutable config** (grok/kimi): **Fixed:** the judgment view renders
  `granted_capabilities` + capped effective leashes, not the raw tuple/overrides.

Already sound in the implementation (verified against the code, not re-litigated): the
`caps_key` has **no default** (unlike `policy_key`/`executor_key`) so absent-key genuinely
fails closed; canonicalization is sorted-keys typed JSON (collision-safe — pinned by a
golden test); the leash rank ordering is correct. **Honestly deferred** (named, not fixed):
no expiry/nonce — an older validly-signed grant for the same subject stays valid until
**key rotation**; version/expiry and a separate authority process arrive with asymmetric
keys (ADR 0002).
