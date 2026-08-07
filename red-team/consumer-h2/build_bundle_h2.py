"""Assemble the PR-H2 CONSUMER red-team bundle: the compute-budget consumer
(bounded_iterations) + its call site + tests, plus the vendored judgment APIs it
reads (Directive/interpret/policy/bus) and the host budget it drives."""

import pathlib

HERE = pathlib.Path(__file__).parent
QA = pathlib.Path(r"D:\Repo\quorum-agent")
SOS = pathlib.Path(r"D:\Repo\salient-os")

# Whole files (the reviewed surface + the vendored APIs the consumer reads).
WHOLE = [
    (QA, "hermes_cli/observability/salience_observer.py"),
    (QA, "tests/hermes_cli/test_salience_consumer.py"),
    (SOS, "salienceos/interpreter/directive.py"),
    (SOS, "salienceos/interpreter/interpreter.py"),
    (SOS, "salienceos/interpreter/policy.py"),
    (SOS, "salienceos/interpreter/bus.py"),
]

# Line-range excerpts (1-indexed, inclusive) for host call-site + ordering context.
EXCERPTS = [
    (QA, "agent/turn_context.py", 438, 502,
     "turn_id mint (:439-447) + the consumer call site + IterationBudget rebuild (:491)"),
    (QA, "agent/turn_context.py", 1050, 1067,
     "pre_llm_call window-open (fires AFTER :491 — proves the open window at :491 is turn N-1's)"),
    (QA, "agent/iteration_budget.py", 1, 60, "IterationBudget.max_total (the propagation target)"),
    (QA, "cli-config.yaml.example", 1690, 1720, "salience config block (enabled + consume_compute)"),
    (QA, "tests/conftest.py", 443, 470, "suite-wide observer disable/reset guard"),
]

CONTEXT = """
PR-H2 adds the FIRST behavior-changing CONSUMER of the SalienceOS judgment system to
quorum-agent (a fork of hermes) as a TEST RIG. Background: PR-H1 (already merged, and
NOT under review here) is a produce-only observer that records, per turn, a `Directive`
onto a per-session hash-named JSONL audit bus under <hermes_home>/salience/. THIS PR
reads that directive and applies its `compute_budget` to the host's per-turn iteration
budget.

THE CONSUMER: `bounded_iterations(session_id, default)` in salience_observer.py, called
from ONE guarded line in agent/turn_context.py `build_turn_context`, immediately before
`agent.iteration_budget = IterationBudget(agent.max_iterations)` (:491). It reads the
directive recorded for the PRIOR turn and returns its compute_budget for THIS turn.

Ordering (verify against the excerpts): within build_turn_context, the turn_id is minted
at :439-447 and the consumer runs at :491 — BEFORE pre_llm_call opens THIS turn's window
at :1054. So at :491 the only open window is turn N-1's; finalize-on-read closes it and
reads its directive (turn N applies turn N-1 — A3).

Guarantees under review:
1. FAIL-OPEN / NEVER-BREAK-THE-TURN. ANY failure, absence, deny-shaped directive,
   switched-off subsystem, kill-switch, missing session, or exception (incl. SystemExit
   from a host API) MUST return `default` unchanged. The consumer must NEVER crash the
   host turn and must NEVER return a budget < 1 (that would brick the agent at
   max_iterations 0).
2. DENY-SHAPED GUARD (A5). A hard-deny directive withholds subject/policy_id and carries
   compute_budget 0; a non-int / bool / sub-1 budget is malformed. `_directive_budget`
   must treat all of these as ABSENT (=> default). It CONSUMES the withhold markers; it
   must not re-derive a decision.
3. CONSUMER, NOT DECIDER (Finding D). The recorded, policy-clamped compute_budget is
   applied VERBATIM — never re-clamped against `default`/config, never recomputed from
   raw salience.
4. A3 — TURN N APPLIES TURN N-1. Finalize-on-read must close the prior window before
   reading; it must not read turn N's own (not yet open) directive, nor a 2-turns-stale
   one. The close is idempotent and monotonic.
5. A4 — OPERATOR-BUDGET BINDING. The finalize-on-read close binds the policy floor to the
   caller-passed `default` (this turn's resolved budget). In the v0 config the policy pins
   min==max==floor and ATTENTION is unmapped, so the directive echoes the operator budget:
   consumption is BEHAVIOR-PRESERVING by construction (the point of H2 is to make the wire
   load-bearing, not yet to move the budget). Flag any docstring/config text that hides or
   overstates this.
6. RESTART FALLBACK INTEGRITY (grok-F8). When the in-memory cache is empty (fresh process
   over an existing session), the last budget is recovered from the session JSONL. This
   MUST go through the replay-verifying SalienceBus so a corrupt/tampered tail RAISES
   (=> default), never feeding an unverified value. Hunt for a path that returns an
   unverified on-disk value, or a TOCTOU between the verify and the read.
7. CONCURRENCY. All registry read-modify-write is under a single non-reentrant _LOCK.
   `_operator_budget()` is only safe under _LOCK. Hunt for a lock escape or a re-entrant
   re-acquire deadlock (bounded_iterations -> _resolve_bounded[holds _LOCK] ->
   _close_locked / _bus_for / _budget_from_disk — do any re-acquire _LOCK?).
8. KILL SWITCHES / LEAK. `salience.consume_compute` (default ON) gates consumption,
   independent of master `salience.enabled`. The per-session directive cache
   (_LAST_DIRECTIVE) must be freed on session close like _BUSES (no per-session leak).

VENDORED CODE NOTE: salienceos/ (directive/interpreter/policy/bus) is a VERBATIM vendored
copy of already-reviewed code — do NOT red-team its internals; it is included ONLY so you
can verify the CONSUMER reads/uses those APIs correctly (Directive fields, interpret()'s
clamping, _hard_deny's blank-subject/zero-budget shape, SalienceBus.directives_for and its
replay-on-open verification). The PRODUCE path of the observer (open/record/close of
windows, signal mapping) was reviewed under PR-H1 and is out of scope EXCEPT where the
consumer changes it (the new `budget` arg to _close_locked, the _LAST_DIRECTIVE cache, the
_close_session free).

ALREADY REVIEWED by 2 internal passes AND a 5-model general red-team panel; this is the
SECOND gauntlet's coding-specialist pass. The following are ALREADY FIXED — do NOT re-report
them (only report if a fix is INCORRECT or introduced a new defect):
(a) restart fallback gated to the COLD path only (`session_id not in _BUSES`); the last
directive is read from the bus's VERIFIED in-memory store after replay (no second file parse,
no subject-selection TOCTOU); (b) a failed finalize (`_close_locked` except) now pops
`_LAST_DIRECTIVE` so a failed close fails OPEN to default rather than applying a 2-turns-stale
directive; (c) the cold recovery is PROMOTED into `_LAST_DIRECTIVE` so it is not one-shot;
(d) `_directive_budget` treats a hard-deny / non-int / bool / sub-1 budget as absent
(⇒ default) — the budget<1 check applies to ALL directives, not just hard-deny; (e) a
`recorded > default` test kills a `min(budget, default)` down-clamp; a non-tail wrong-hash
tamper test proves the bus replay-verify is the integrity gate; a live-path second-read test
(cached 20 vs new default 99) distinguishes cache-read from default (v0 otherwise makes them
equal); (f) the `verify_policy` probe is a one-time well-formedness check of the hardcoded
template (NOT config validation — no template knob is config-wired); (g) docs corrected: the
consumer is behavior-preserving in v0 because BOTH the policy window is pinned (min==max) AND
ATTENTION is unmapped — moving the budget needs a widened window AND a mapped facet. Also
note KeyboardInterrupt is DELIBERATELY not caught (Ctrl+C must reach the host) — do not
report that as a fail-open gap. YOUR VALUE is code-level defects those reviews missed:
language/stdlib footguns, the vendored-API usage (esp. reading `bus._directives[-1][1]`
private state, and the promoted raw dict reference), int/bool/type edge cases, and any
mutation-blind test.
"""

parts = [f"\n\n########## CONTEXT ##########\n{CONTEXT}"]
for base, rel in WHOLE:
    text = (base / rel).read_text(encoding="utf-8")
    tag = rel if base is QA else f"[vendored api] {rel}"
    parts.append(f"\n\n########## {tag} ##########\n\n{text}")
for base, rel, a, b, label in EXCERPTS:
    lines = (base / rel).read_text(encoding="utf-8").splitlines()
    excerpt = "\n".join(lines[a - 1:b])
    parts.append(f"\n\n########## {rel} (lines {a}-{b} — {label}) ##########\n\n{excerpt}")

bundle = "".join(parts)
(HERE / "bundle_consumer.txt").write_text(bundle, encoding="utf-8")
print(f"bundle: {len(bundle):,} chars (~{len(bundle) // 4:,} tokens)")
