# ③ Signed PolicyCaps — synthesis

*The last stage, and the deferred hardening both the Collaborator plan and the resource
governor punted: bind the Collaborator's authority to a **signed grant** the host verifies
every action, instead of mutable config. "Policy authorizes" made cryptographic. Designed →
5-model external panel → fixed → proven. The panel earned its keep: it found a cluster of
real fail-open gaps in the implementation.*

## What shipped

- `collaborator/policycaps.py`: `PolicyCaps` (capabilities + per-tool `leash_caps` + issuer
  + subject), `SignedPolicyCaps`, `mint()` / `verify()` (HMAC-SHA256 over a canonical,
  sorted, typed serialization), `workspace_subject()`, and the seam helpers
  `granted_capabilities` / `leash_cap` / `apply_cap`.
- `Session` gains optional `policy_caps` + `caps_key` and a **sticky `enforce_caps`** flag.
- The seam (`govern_action` + `reauthorized_or_denied`) sources capabilities from the
  **verified** grant and caps the leash at `stricter(host_leash, leash_cap)` — on both the
  act path and the approve/re-gate path.

## The property

**With a grant, the config and the Step-2 control surface can only TIGHTEN, never widen.**
Mutating `session.capabilities` cannot add a capability (the verified caps are
authoritative); `set_leash` cannot loosen a tool past its cap. Tamper, strip, wrong subject,
absent key, or an unlisted tool all **fail closed** (zero capabilities, strictest leash). A
session with no grant is unchanged (legacy).

## Honest scope (stated up front, not buried)

Symmetric HMAC, **single trust domain**: the verifier holds the signing key, so this is
**tamper-evidence + provenance + fail-closed integrity** against non-crypto mutation — the
realistic threat (a bug or an injected config flip in a component that doesn't hold the key)
— **not** a hard boundary against a fully in-process re-signer. A separate authority process
/ asymmetric keys is the deliberate next step, consistent with ADR 0002. No expiry/nonce in
v0: an older validly-signed grant for the same subject stays valid until **key rotation**.

## The panel (5 models) — SERIOUS_FLAWS on a cluster of fail-open gaps, all fixed

No single CRITICAL, but four of five returned SERIOUS_FLAWS on real fail-open holes I then
closed (reproduce-before-accept: I checked each against the code, fixed the genuine ones,
and confirmed which were already handled):

| Finding | Severity | Fix (pinned by a test) |
|---|---|---|
| **Grant-stripping** reverts to mutable config | consensus HIGH | sticky `enforce_caps` — stripped ⇒ fail closed, not legacy |
| **Unlisted tool** uncapped | HIGH | unlisted-under-grant defaults to `notify_only` (strictest) |
| **Subject** unresolved (relative/symlink replay) | MEDIUM | `workspace_subject` = resolved absolute path, both sides |
| **`verify()`** not total | HIGH (mistral) | wraps everything → `False`, never raises |
| **Leash cap** only at origination | glm/mistral | also applied in `execute_and_verify` (terminal point) |
| **View** shows mutable config | grok/kimi | renders `granted_capabilities` + capped effective leashes |

Already sound (verified, not re-litigated): `caps_key` has **no default** (so absent-key
genuinely fails closed — unlike `policy_key`/`executor_key`); canonicalization is
sorted-keys typed JSON (collision-safe — golden test); leash rank ordering correct. Deferred
honestly: expiry/nonce/rollback → key rotation now, asymmetric later.

`tests/test_collaborator_policycaps.py` (16 tests); full suite **290 green**.

## Proven — `policycaps_proof.py`, 9/9

a grant confers exactly its caps @ its leash; mutating `session.capabilities` **can't
widen**; the view **can't loosen** past the cap; an **unlisted tool never runs**; **stripping**
the grant **fails closed** (sticky); **tamper** fails closed; a **foreign-workspace** grant
is rejected; **approval re-gates** against the current grant (strip-while-held ⇒ DENIED at
approve); a **legacy** session is unchanged.

## Honest scope — what ③ is and isn't

- It is the authority made a verified, signed, fail-closed artifact within one trust domain —
  the principled home the earlier stages deferred, and the enforcement that makes the Step-2
  control surface safe (it can steer, never exceed the grant).
- It is not a cross-process/asymmetric boundary (named as next), and it is opt-in: a session
  built without a grant gets no new protection (documented, not silent).
