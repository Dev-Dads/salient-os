# SalienceOS Control Seam — Design-Faithfulness & Test-Honesty Review

Scope: `salienceos/control/{govern,outcome,__init__}.py`, `tests/test_control.py`,
`tests/test_control_e2e.py`; context from `interpreter/{directive,policy}.py`,
`verifier/{envelope,composer,pipeline,verdict}.py`, and Design Review v0.2 (P-01, Findings B/C).

The seam's job: reconcile the interpreter's `verification_depth` (0-3) with the verifier's
`Stakes` (LOW..CRITICAL) and emit a governed outcome (`cleared` + `adaptation_allowed`).
`decide()` is the pure gate; `govern()` runs the verifier then calls `decide()`.

Verified empirically: all 23 control tests pass as-is. A mutant that strips `govern()`'s
escalation (see F1) also passes all 23 — the finding is confirmed, not inferred.

---

## What is genuinely good (calibration)

- **`decide()` is genuinely pure.** `govern.py:59-98` reads only its args, calls the pure
  `achieved_level()`, builds reason strings, returns a frozen `GovernedOutcome`. No clock, no
  I/O, no globals, no randomness. `GOVERNOR_VERSION` is not even referenced inside it. The one
  impure part is `govern()` (`:101-111`), whose impurity is entirely inside `verifier.verify()`
  (append-only store mutation). The docstring's "`decide()` is the pure spine; `govern()` is the
  thin orchestration" is accurate.

- **"Only FULL escalates" is correct, not a bug.** The composer collapses stakes into exactly two
  rigor tiers: `_required_sources()` (`composer.py:141-142`) returns 1 for LOW/NORMAL and 2 for
  HIGH/CRITICAL, and every VERIFIED already requires ≥1 executor-independent world fact
  (`_agreement_gap`, `composer.py:145-180`) regardless of stakes. So the *only* behavioral
  boundary is the 1→2-source jump at HIGH, and FULL(3) is the only depth that needs it.
  Escalating INDEPENDENT to a HIGH floor would be *wrong* — it would demand two sources for a
  level whose meaning is one. `escalation_for` returning `None` for NONE/RECEIPT/INDEPENDENT is
  the faithful choice. **INDEPENDENT should NOT imply a floor.**

- **Adaptation's triple gate is exemplary mutation discipline.** `adaptation_allowed = cleared AND
  eligibility is CANDIDATE AND status is VERIFIED` (`:84-88`). `test_attested_but_not_verified_
  blocks_adaptation` builds the one fixture that reaches the wrong answer — an action that *is*
  `cleared` (at RECEIPT, via ATTESTED) and *is* CANDIDATE, so only the `status is VERIFIED` clause
  denies it. Delete that clause and the test goes RED. This is precisely the Finding-C invariant
  ("nothing is learned from an unverified action") and it is killed by a real fixture.

- **FAILED-never-clears is tested at the one load-bearing point.** `achieved_level(FAILED)=NONE(0)`,
  so for `required=NONE(0)` the comparison `0>=0` would clear *without* the explicit
  `status is not FAILED` guard. `test_failed_never_clears` uses `required=NONE` — exactly the
  fixture where the guard is load-bearing. Precise.

- **Idempotence / no trust in the envelope.** If the same directive both signs (`stakes_for`,
  `:36-41`) and governs, `effective = max(max(floor,esc), esc) = envelope.stakes` — consistent.
  And `govern()` re-derives `escalation_for()` itself rather than trusting the envelope to have
  honored `stakes_for`, so a FULL directive still forces HIGH at verify time even if the envelope
  was signed lower. (This robustness is real in code but untested — see F1.)

- **`achieved_level()` is total and fail-closed** (`:44-56`): the final `return NONE` catches
  FAILED, plain UNVERIFIED, and RECEIPT_ENVELOPE_MISMATCH.

---

## Findings

### F1 — HIGH — `govern()`'s escalation has NO killing test (test honesty)
**What.** The core "salience escalates verification at verification time" invariant lives in
`govern.py:108-110`:
```
escalate = escalation_for(directive.verification_depth)
verdict = verifier.verify(envelope, receipt, world_evidence, escalate_to=escalate)
effective_stakes = max_stakes(envelope.stakes, escalate)
```
No test exercises the case where this escalation actually *raises* stakes above the envelope's
signed value. Empirically confirmed: replacing these lines with `escalate = None` /
`effective_stakes = envelope.stakes` passes all 23 tests.

**Why it slips through.** In `test_control_e2e.py`, `_act()` signs every envelope with
`stakes_for(directive, NORMAL)` (`:67`). For a FULL directive that is already HIGH, so
`test_high_salience_needs_two_sources` (`:89-101`) would pass even if `govern()` did no escalation
— the envelope itself carries HIGH. The only test that signs an envelope independently
(`test_salience_cannot_lower_envelope_floor`, `:118-135`, envelope HIGH) pairs it with a *low*-depth
directive, so `escalate` is `None` there. Net: no fixture has `directive.depth == FULL` **and**
`envelope.stakes < HIGH` at the same time — the only configuration where `govern()`'s escalation
changes the answer.

This also answers the sub-question "is the verifier `escalate_to`/`max_stakes` change covered by a
test that proves it can't lower stakes?": `max_stakes` **yes** (`test_max_stakes_is_upward_only`,
unit-kills a min-mutation). The `escalate_to` plumbing inside `verify()` (`pipeline.py:88`) is only
reachable through `govern()`, and by the same gap **no test makes `escalate_to` raise the effective
stakes above the envelope floor** — deleting `verify()`'s `escalate_to` handling also passes.

**Why it matters.** This is exactly the invariant Finding B says to protect with mutation
discipline ("break the invariant in source, confirm red"). The escalation defends the case where
the envelope was signed *before* salience raised the required depth (Finding F's between-turn
reconfiguration) or by a caller that didn't use `stakes_for`. That defense is currently unguarded.

**Fix.** Add a decide-independent govern test with the missing configuration:
directive `depth=FULL`, envelope signed at `Stakes.NORMAL` (do **not** route through `stakes_for`),
one world source. Assert `out.effective_stakes is Stakes.HIGH`, `out.verdict.status is not VERIFIED`,
`out.cleared is False`. Deleting either the `max_stakes(envelope.stakes, escalate)` on `:110` or the
`escalate_to=escalate` on `:109` must flip it RED. (A pure-`decide` unit test cannot cover this —
the escalation lives in `govern`, so the killer must go through `govern`.)

---

### F2 — MED — `decide()`'s `envelope_stakes` parameter is dead
**What / where.** `decide(directive, envelope_id, envelope_stakes, verdict, effective_stakes)`
(`govern.py:59`) never references `envelope_stakes` in its body. `govern()` threads
`envelope.stakes` into it (`:111`) but it is ignored.

**Why it matters.** A parameter that looks load-bearing but isn't is a place bugs hide (a caller
may believe passing a different `envelope_stakes` changes the outcome), and it is a missed chance
to enforce the floor invariant at the pure gate.

**Fix — pick one.** (a) *Use it*: assert the floor was never lowered —
`if STAKES_ORDER.index(effective_stakes) < STAKES_ORDER.index(envelope_stakes): reasons.append(
"floor_lowered"); cleared=False` — turning the "upward-only" property into a fail-closed check
inside the pure gate (and then F1's killer could be a pure-`decide` test). (b) *Drop it* from the
signature and the `govern()` call site. (a) is preferable: it moves the escalation invariant into
the mutation-test target.

---

### F3 — MED — CRITICAL stakes is undistinguished across the whole seam (mapping completeness)
**What / where.** `Stakes` has four rungs but the seam collapses HIGH and CRITICAL everywhere:
- `escalation_for()` (`:29-33`) tops out at HIGH — a directive can **never** demand CRITICAL.
- `achieved_level()` (`:53`) maps `VERIFIED + (HIGH or CRITICAL) → FULL` identically.
- the composer's `_required_sources()` (`composer.py:141-142`) treats both as 2 sources.

So CRITICAL is reachable only as an envelope's signed floor and behaves exactly like HIGH.

**Why it matters.** *Today this is faithful, not a defect* — the seam mirrors the verifier, which
also doesn't distinguish CRITICAL. The risk is latent coupling: the unified depth scale has 4 rungs
that map onto only 2 verifier rigor tiers ({LOW,NORMAL}→1 source, {HIGH,CRITICAL}→2 source). If
CRITICAL is ever meant to mean *more* (three sources, human sign-off), `escalation_for` (capped at
HIGH) and `achieved_level` (HIGH≡CRITICAL) would silently fail to honor it, and no test would
notice.

**Fix.** Make the equivalence explicit rather than incidental: either document in
`composer._required_sources` and `achieved_level` that "HIGH and CRITICAL share the two-source
tier by design," or, if CRITICAL is intended to be stronger, introduce the third tier in
`_required_sources`, let `escalation_for` reach CRITICAL, and split `achieved_level`'s FULL branch.
At minimum add an `achieved_level(VERIFIED, CRITICAL)==FULL` assertion with a comment so the
collapse is a decision, not an accident. (`test_verified_two_source_is_full` already asserts the
CRITICAL case at `test_control.py:69` — good — but nothing documents *why* it equals HIGH.)

---

### F4 — LOW (fail-closed sign) — achieved_level is non-monotone at RECEIPT
**What.** With an authentic receipt: **0** world facts → composer attaches INTEGRITY_ATTESTED →
`achieved_level` returns RECEIPT(1) → a `required=RECEIPT` action **clears**. But **1** world fact
that is insufficient for a HIGH-signed envelope → `INSUFFICIENT_CHANNELS`, and the composer
*suppresses* INTEGRITY_ATTESTED in that case (`composer.py:87-93`, `no_usable_world` excludes
INSUFFICIENT_CHANNELS) → `achieved_level` falls through to NONE(0) → the same requirement **denies**.

**Why it matters.** Adding partial independent corroboration *lowers* the achieved level
(RECEIPT→NONE). The direction is safe (more evidence → less clearance is fail-closed, never a
leak), but it is counter-intuitive and worth a comment so nobody "fixes" it into a leak.

**Fix.** Document the intended non-monotonicity at `achieved_level`, or have the composer still
surface INTEGRITY_ATTESTED as a secondary sub-code alongside INSUFFICIENT_CHANNELS so a partial-
corroboration action can still reach RECEIPT. No code change is required for safety.

---

### F5 — LOW — `decide()` does not range-check `required`
**What / where.** `required = directive.verification_depth` (`:68`) is trusted as a valid 0-3
depth. From `interpret()` it always is (clamped into `[min_v, max_v] ⊆ [0,3]`, and `_hard_deny`
sets FULL). But `decide()` is a public pure function: a `required` of 5 fails closed (never clears,
good), whereas a **negative** `required` clears unconditionally (`achieved >= -1` always true).

**Why it matters.** Small, and unreachable via the current interpreter — but the asymmetry (fail-
closed above range, fail-*open* below range) is exactly the kind of thing a future refactor could
expose.

**Fix.** One line: clamp or guard `required` to `[NONE, FULL]` at entry, or assert it, so the pure
gate is self-defending regardless of caller.

---

### F6 — LOW — `required_level` in the outcome can understate what was enforced
**What.** `required=INDEPENDENT(2)` with an envelope signed HIGH forces two sources (the envelope
floor governs via `effective_stakes=HIGH`), yet `GovernedOutcome.required_level` reads 2, whose
semantic meaning is "one source." The truth is carried by `effective_stakes` (HIGH), so it is
discoverable — just note the field pairing so consumers read `effective_stakes`, not
`required_level` alone, to know the enforced rigor.

**Fix.** Documentation only: clarify in `outcome.py` that `required_level` is the *directive's*
demand and `effective_stakes` is what the verifier actually ran at (which may be stricter, per the
policy floor).

---

## Per-invariant test-honesty ledger (question 4)

| Invariant | Clause (govern.py) | Killing test | Verdict |
|---|---|---|---|
| salience-escalation-only — `escalation_for` shape | `:29-33` | `test_only_full_escalates` | KILLED |
| salience-escalation-only — `stakes_for` never lowers | `:36-41` | `test_stakes_for_never_lowers_the_floor` | KILLED |
| salience-escalation-only — `max_stakes` upward | `envelope.py:26-36` | `test_max_stakes_is_upward_only` | KILLED |
| **salience-escalation-only — `govern()` applies it** | **`:108-110`** | **none** | **GAP (F1)** |
| **`verify()` `escalate_to` raises effective stakes** | **`pipeline.py:88`** | **none** | **GAP (F1)** |
| fail-closed clearance (`achieved>=required`) | `:80-82` | `test_under_verification_denies_clearance` | KILLED |
| FAILED-never-clears | `:80` (`status is not FAILED`) | `test_failed_never_clears` (at required=NONE) | KILLED |
| subject-binding | `:71,80` (`bound`) | `test_subject_mismatch_denies` | KILLED |
| adaptation-needs-VERIFIED | `:84-88` (`status is VERIFIED`) | `test_attested_but_not_verified_blocks_adaptation` | KILLED |

Note on `test_under_verification_denies_clearance`: it feeds `decide()` a govern-unreachable state
(FULL directive + NORMAL effective stakes) to isolate the gate — legitimate for a pure-gate unit
test, but it is *not* a substitute for the govern-level escalation test F1 asks for.

---

## Answers to the four framing questions

1. **Mapping coherence/completeness.** Consistent and total *as a partial order*: all four depths
   are handled by `achieved >= required`; `achieved_level` is total. RECEIPT(1) **is** reachable
   and satisfiable (ATTESTED → RECEIPT; `test_receipt_requirement_accepts_attested`) — no depth is
   unsatisfiable. The real gaps are (F3) CRITICAL is an undistinguished rung — the 4-rung stakes
   scale collapses onto 2 verifier tiers — and (F4) a fail-closed non-monotonicity at RECEIPT.

2. **Consistency between the two moments.** `stakes_for` (sign-time) and `govern` (verify-time)
   **cannot disagree unsafely**: `govern` uses `envelope.stakes` as a floor and only ever adds via
   `max_stakes`, and re-derives escalation itself rather than trusting the envelope. Both raise-then-
   drop and drop-then-raise re-interpretation scenarios keep the verifier at ≥ the signed floor.
   `escalation_for` (only FULL) is **correct** given the composer's 1-vs-2-source semantics —
   INDEPENDENT should not imply a floor. The weakness is not in the logic but in F1: this
   consistency is unproven by tests.

3. **Purity.** `decide()` is genuinely pure; `govern()` is the only impure part (store mutation in
   `verify()`). One wart: `decide()`'s `envelope_stakes` parameter is dead (F2).

4. **Test honesty.** Four of five invariants have precise killing tests, several with the exact
   fixture that reaches the wrong answer (adaptation-at-ATTESTED, FAILED-at-NONE). The fifth —
   salience-escalation-*applied-in-govern* — has **no killing test** (F1), confirmed by a passing
   mutant. `max_stakes` is unit-proven upward-only; `escalate_to` is not.
