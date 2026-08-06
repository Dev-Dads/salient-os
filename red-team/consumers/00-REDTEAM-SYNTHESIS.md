# Red-team synthesis — consumer gates (build stage 4)

Target: branch `feat/consumer-gates` — the `salienceos/consumers/` package
(memory-retention governor + weight-adaptation gate), the interpreter's recorded
`AdaptationRationale`, the self-describing `GovernedOutcome`, and the bus
reader/replay.

## Process

Per the house pattern (see the interpreter and control-seam rounds): core built
solo → two internal subagent reviews → external five-model panel → every finding
reproduced against source before acceptance (the standing "verify before trust"
rule). Panel: the general roster (deepseek-v4-pro, grok-4.5, mistral-medium-3-5,
kimi-k3, glm-5.2), chosen on capability, catalog re-checked at run time.

Baseline before review: 137 tests. After all accepted fixes: **203 tests, 1
skipped, green** on 3.11/3.12/3.13.

## Internal reviews (2)

**Correctness / fail-closed.** Proved the rationale refactor behavior-preserving
by a differential sweep of 18,816 policy×signal combinations against the original
conjunction — zero mismatches. Eleven-for-eleven on its own mutation probes. One
real bug: a malformed rationale could crash `nominate()` instead of refusing
(fixed at the seam boundary + a gate belt). Plus replay hardening (exact key-set
fence, non-dict payload rejection) and honest narrowing of the `verify_chain`
docstring re: tail truncation across a reopen (an ADR 0001 exclusion).

**Design-faithfulness / test-honesty.** 42 sabotage mutations; 33 caught. One
HIGH: the rationale priority chain's ordering was unpinned, and a reorder could
*manufacture* inhibitors (marking "not requested" or "under-verified" as an
incident) — pinned in both directions. Plus `directives_for` ordering made
observable, bound-denial rationale coverage, and the stale roadmap reconciled.

## External panel — outcome

**grok-4.5 (MINOR_ISSUES)** was the sharpest: it found the directive half of the
audit fence was missing — signals are structurally body-free but directive
`emit`/replay had neither ref-length caps nor a payload allowlist, so
prompt-sized content could become durable *inside* the payload. Accepted and
fixed (bounded `emit`, exact-key-set + bounded replay, unknown-kind rejection).
Also: negative `reinforcement_sum` could null an inhibitor's pin (fixed);
`adaptation_eligibility` now type-checked symmetrically with the rationale.

**kimi-k3** was the only other review to attack rather than verify. Its KI-3,
self-rated LOW for a crash symptom, was materially worse on reproduction: the
seam's binding fence never checked that `directive.subject` is a `str`, so a
hand-built directive with an always-equal subject bound to a verdict for a
*different* action and reported `cleared=True`. Fixed (`isinstance(subject, str)`
in `_valid_directive`). Its other in-scope hits (unknown replay kind, directive
payload fence, negative reinforcement, the missing no-handoff pin) overlapped
grok's and were fixed in the same pass.

**Minor:** replay coerced `provenance` before validating it, accepting shapes
`publish()` can't produce — fixed to validate the persisted value first.

**deepseek-v4-pro** and **glm-5.2** were verification passes that self-rejected
every candidate before the token cap: zero net findings.

**mistral-medium-3-5 (SERIOUS_FLAWS)** — all six of its CRITICAL/HIGH findings
were rejected on reproduction: three required hand-forged `GovernedOutcome`
records (explicitly out of scope — equivalent to bypassing the verifier), and
three (F-004 deep copy, F-005 off-ladder floor, F-007 negative reinforcement)
pasted the code's *existing* behavior as their proposed fix. A clean example of
why findings are reproduced before acceptance.

## Net changes accepted

| Source | Severity | Fix |
|---|---|---|
| internal-correctness | MED | malformed rationale denied at seam + gate belt |
| internal-design | HIGH | rationale priority ordering pinned (no manufactured inhibitors) |
| grok-4.5 | HIGH | directive half of the audit fence (emit + replay bounds) |
| grok-4.5 / kimi | LOW→ | negative `reinforcement_sum` rejected (pin can't be nulled) |
| kimi-k3 (KI-3) | LOW→MED | seam validates `subject` is a str (binding-fence bypass) |
| grok-4.5 | LOW | `adaptation_eligibility` type-checked symmetrically |
| glm/kimi | MINOR | replay validates persisted `provenance` shape |
| both internal | — | ~12 mutation-honesty test additions |

Raw panel outputs: `raw/cs_*.md`. Bundle regenerable via `build_bundle.py`
(git-ignored, matching the control-seam round).
