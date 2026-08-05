# SalienceOS Interpreter — Faithfulness & Test-Honesty Review

Scope: `salienceos/interpreter/*.py`, `tests/test_interpret.py`, `tests/test_no_laundering.py`,
`tests/test_bus.py`, against `SalienceOS_Design_Review_v0.2.md` (Part 1 mechanism + thin-bus fork;
Findings D, F, G). All 26 interpreter/bus/no-laundering tests pass. Claims below were verified by
reading source and by direct probes (mutation simulation + generator/CoT probes).

Severity legend: [GOOD] / [LOW] / [MED] (no HIGH found).

---

## 1. Finding D — single fail-closed choke point, UI-independent

**[GOOD] The seam is genuinely single and pass-through-only.**
`interpret()` (`interpreter.py:40`) is pure (no I/O, clock, or globals) and `allowed_capabilities` is
literally `tuple(policy.granted_capabilities)` (line 108) with no code path from any signal to that
field. `_hard_deny` returns `()` caps. `Directive.grants_capability` (`directive.py:34`) is the only
capability accessor, so consumers can't infer authority from scalar knobs. There is no UI in the
module at all — enforcement cannot be disabled by "removing a dashboard."

**[GOOD] The interpreter re-verifies the policy signature itself.**
`interpret()` takes `policy_key` and calls `verify_policy(policy, policy_key)` (line 53) rather than
trusting a pre-validated flag. A forged/tampered/`None` policy yields the hardest deny (empty caps,
budget 0, FULL verification). This is the right shape for a choke point — authority cannot be smuggled
in by handing it a "trusted-looking" object.

**[LOW] `_hard_deny` echoes `subject`/`policy_id` from an *unverified* PolicyCaps.**
Where: `interpreter.py:115-119`. When `policy` is a `PolicyCaps` with a bad signature (tampered), the
deny directive still copies `policy.subject` and `policy.policy_id` into the emitted directive (only a
forged-*type* object gets blanked). These are not authority, so it is not a P-01 leak, but it lets an
untrusted source place arbitrary identifiers into the durable audit record of a deny.
Suggested change: blank `subject`/`policy_id` on hard-deny (or tag them `unverified:`), so the audit
trail never attributes a deny to attacker-chosen identifiers.

No enforcement logic leaks *out* of the seam: `scorers.py` clamping is publisher-side normalization,
and `bus.publish` re-running `valid_signal` is defense-in-depth, not a second enforcement point.

---

## 2. Finding F — between-turn preferred, immediate is the gated exception

**[GOOD] Correct default and double-gate.**
`Reconfigure.BETWEEN_TURN` is the default (`interpreter.py:93`), and IMMEDIATE requires BOTH
`policy.allow_immediate_reconfigure` AND confidence-weighted attention `>= 0.9`
(`IMMEDIATE_RECONFIGURE_THRESHOLD`, line 37/94). Hard-deny and no-signal paths both stay
BETWEEN_TURN. Threshold is high (0.9), correctly biasing toward between-turn. Using the
confidence-weighted mean (not raw max) means a low-confidence spike can't trip immediate — a good
choice. `test_immediate_reconfigure_requires_policy_and_high_attention` covers all three combinations.

**[LOW] Immediate-ness is policy-boolean only, not policy-tunable.**
The 0.9 threshold is a module global; a policy can allow/forbid immediate but cannot say *how* hot
attention must be. Acceptable simplification; note it if per-stream tuning is ever wanted (Finding F/G
argue reconfiguration cost is per-stream).

---

## 3. Finding G — bus as audit surface; "structurally incapable of durable bodies"

**[MED] The "structurally incapable of storing prompts/bodies/CoT ... by construction, not policy"
claim is overstated — `provenance` and `subject` are unbounded free-text.**
Where: `signal.py:37-63` (`SalienceSignal`, `valid_signal`); claim asserted in `bus.py:1-12` docstring.
`valid_signal` only checks `isinstance(p, str)` for each provenance entry and `isinstance(subject,
str) and bool(subject)` — no length cap, no charset/ref-shape constraint. Verified: a signal whose
`subject` and `provenance[0]` each carry ~8 KB of "chain of thought" text passes `valid_signal` and
would be written verbatim to the durable bus log by `bus.publish` (`asdict(signal)` → file append,
`bus.py:31,65`). So the guarantee is really "no *field named* prompt/body/args/CoT" — true — but the
durable record CAN hold exactly those things through `provenance`/`subject`. The design's own stance
("a total durable record is itself a liability ... handled by construction, not policy") is not met.
Suggested change: constrain provenance/subject in `valid_signal` to ref-shaped tokens (e.g. regex
`^[A-Za-z0-9_.:\-/]{1,128}$` per entry, bounded subject length). That makes the "refs + rationale
codes only, no bodies" property actually structural, and it is cheap and testable.

**[MED] The hash chain is built but never verified, and its linkage is untested.**
Where: `bus.py:58-67`. Each entry stores `prev: self._head` and `hash: digest(entry)`, but there is no
`verify_chain()` method anywhere, and nothing re-reads the file to confirm linkage/tamper-evidence.
"Append-only" is enforced only by the *absence* of a mutator on plain lists (`self._signals`), not by
any check. Verified test gap: `test_publish_grows_hash_chain` asserts only that heads differ — because
the two published signals have different payloads, their hashes differ *even if `prev` were removed
entirely*, so the chain-linkage invariant is not actually tested (probe: `digest(e1) != digest(e2)` is
True without any `prev`). Suggested change: add `verify_chain()` that recomputes each entry hash and
checks `prev` continuity to `head()`, plus a test that mutating/reordering one entry makes it return
False. Without this, "auditable contract / hash-chained" is aspirational.

**[GOOD]** The directive record in `emit()` (`bus.py:34-50`) stores only knob values, rationale-code
`reasons`, provenance-free capability list, and version — no bodies. The intent is right; only the
provenance/subject vector and the missing verifier undercut it.

---

## 4. Thin-contract fork — comparable influence + confidence + provenance + subsystem-id

**[GOOD] The contract is the right thinness and does not leak a scoring schema.**
`SalienceSignal` = `subsystem_id, subject, facet, influence[0,1], confidence[0,1], provenance`
(`signal.py:36-43`). That is exactly "comparable influence + confidence + provenance + subsystem-id"
plus the two arbitration keys the interpreter genuinely needs (`subject` to scope, `facet` to pick a
knob). No capability/grant/scope field exists — P-01 is structural at the type level, and
`test_signal_type_has_no_authority_field` locks it. Not too thick: `Facet` values are open strings
("an unknown facet is recorded and ignored"), so no publisher is forced into a schema. Not too thin:
`_aggregate` (confidence-weighted mean per facet) + `_routing_hint` give the interpreter enough to
arbitrate.

**[GOOD] Per-subsystem heterogeneity is real in `scorers.py`.**
`additive_scorer` (weighted diagonal sum, Finding A baseline) and `threshold_scorer` (hard step
function) are genuinely different internal shapes producing the identical thin `SalienceSignal`. The
contract module imports neither. This is a faithful realization of "each subsystem scores its own way;
the bus is all they share."

**[LOW] Comparability of `influence` across subsystems is convention, not enforced — and mixing
scorer shapes on one facet is silent.**
The contract enforces the `[0,1]` *range* but nothing makes subsystem A's 0.7 mean the same as B's
0.7 (the spec acknowledges this: "thin but real"). Concretely, on a single facet a binary
`threshold_scorer` (0/1) averaged with a continuous `additive_scorer` via confidence-weighted mean can
let the binary source dominate. Inherent to the design, not a bug — worth a one-line note in the bus
docstring so no one later reads `influence` as cross-subsystem-calibrated.

**[LOW] `scorers.py` has ZERO test coverage** (grep confirmed: no test references `additive_scorer`/
`threshold_scorer`). The heterogeneity claim is demonstrated by example but not *locked*: if someone
added a scoring-schema field to `SalienceSignal`, or broke a scorer's clamp, no test would go red.
Suggested change: one test that both scorers produce `valid_signal`-passing signals, that clamping
holds at the extremes, and that `interpret()` accepts both — cheap insurance for the fork's central
claim.

---

## 5. Test honesty (mutation discipline)

**[MED] The `adaptation_min_verification` gate is a fixture that cannot reach the wrong answer —
untested; the mutation stays green.**
Where invariant lives: `interpreter.py:87` (`and v_depth >= policy.adaptation_min_verification`).
Where it should be tested: `tests/test_interpret.py::AdaptationGate`,
`tests/test_no_laundering.py::AdaptationNeedsPolicySwitch`. Verified by mutation simulation: deleting
that clause leaves ALL four adaptation tests green —
- `test_candidate_requires_low_risk_and_enough_verification` uses VERIFICATION=1.0 → depth FULL(3) ≥ 2,
  so the clause is never the binding constraint;
- `test_absent_risk_blocks_adaptation` and `test_high_risk_blocks_adaptation` are blocked by the RISK
  clause regardless;
- `test_no_adaptation_without_policy_allow` is blocked by the policy switch regardless.

This is exactly the Finding B anti-pattern ("a fixture that cannot reach the wrong answer") on a
safety-relevant gate: the rule "don't let salience buy adaptation eligibility without *applied*
verification depth" has no test that would fail if it were removed.
Suggested change: add a test where adaptation=1.0, risk=low, policy allows, but the verification
window forces depth below the gate — e.g. `policy(max_v=1, adapt_min_v=2)` with a verification signal
→ depth clamps to 1 < 2 → assert `AdaptationEligibility.NONE`. (Confirmed by probe this yields NONE
with the clause present, CANDIDATE with it removed.)

**[GOOD] The P-01 no-laundering tests are genuine leak-locks.**
`test_maxed_out_signals_never_add_a_capability` floods every facet AND capability-named facet strings
(`shell.exec:root`, `fs.write:/`, `host_admin`) at influence 1.0 and asserts caps are unchanged — this
would fail against the obvious laundering mutation. `test_signal_cannot_grant_capability_not_in_policy`
and the structural `test_signal_type_has_no_authority_field` back it. Fail-closed defaults
(`test_no_signals_yields_min_budget_max_verification`, the three bad/None/tampered-policy tests) are
real and would catch broken defaults. `test_eligibility_never_exceeds_candidate` asserts the enum has
no `promoted`/`live` member — locks "no live self-modification" structurally.

**[MED] No interpreter mutation-fixture harness exists, despite the docstring's claim.**
`interpreter.py:1-6` calls `interpret()` "the primary mutation-test target — the directive analog of
the verifier's `compose()`." But there is no `tests/test_mutation_*` for the interpreter (the existing
`test_mutation_fixtures.py` covers only the verifier). The no-laundering tests function as property
leak-locks, which is good, but the specific discipline Finding B asks for ("break the invariant in
source, confirm red") is not realized for the interpreter — and the untested adaptation clause above is
the direct consequence. Suggested change: a small interpreter mutation test that, for each guarded
clause (cap pass-through, risk gate, verification gate, policy-switch gate, immediate-reconfigure
gate), asserts a known mutation would flip an output — or at minimum add the missing verification-gate
test above so every AND-clause in the adaptation predicate has a killing test.

**[LOW] Bus append-only / chain-linkage invariant has no killing test** (detailed in §3): the one bus
audit test passes even if `prev` linkage is removed.

**[LOW] Audit-trail corruption on generator input (`dropped_invalid_signals` goes negative).**
Where: `interpreter.py:48-51`. `valid = tuple(s for s in signals if valid_signal(s))` consumes
`signals`; then `dropped = len(tuple(signals)) - len(valid)` re-iterates the now-exhausted iterable.
Verified: passing a generator of `["not a signal", <valid attention 0.5>]` yields
`reasons=('dropped_invalid_signals=-1',)` and budget 505 — i.e. a *negative* dropped count is written
into the durable audit reasons (which the bus persists as ground truth). Lists work by luck of being
re-iterable. Suggested change: materialize once at the top: `signals = tuple(signals)`; then compute
`valid` and `dropped` from that. Cheap, and it removes a nonsensical value from the audit surface.

---

## Summary of concrete changes (highest leverage first)

1. Add the missing adaptation **verification-gate** test (`policy(max_v=1, adapt_min_v=2)` + risk-low +
   adaptation-high → NONE). Closes the one safety gate whose mutation currently stays green. (§5)
2. Constrain `provenance`/`subject` in `valid_signal` to bounded ref-shaped tokens so "no durable
   bodies/CoT" is structural, matching the bus docstring's promise. (§3, Finding G)
3. Add `SalienceBus.verify_chain()` + a tamper/linkage-killing test; today the chain is built but never
   audited and its linkage is untested. (§3/§5, Finding G)
4. Materialize `signals` once in `interpret()` to stop negative `dropped_invalid_signals` in the audit
   trail on generator inputs. (§5)
5. Add at least one test over `scorers.py` to lock the heterogeneity claim. (§4)
6. Minor: blank `subject`/`policy_id` on hard-deny of an unverified PolicyCaps. (§1)

Nothing here contradicts the v0.2 mechanism; the interpreter is a faithful realization of the
bus + central-choke-point design with a genuinely thin contract. The gaps are (a) one overstated
"by construction" claim on the audit surface that is really a naming convention, (b) an audit hash
chain that is constructed but never verified, and (c) one safety gate plus the scorers left without a
killing test.
