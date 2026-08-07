"""Assemble the PR-H3 red-team bundle: the headroom-widened producer + the
synthesized-ATTENTION close, the call-site no-ratchet fix, the new tests, and
the vendored judgment APIs the rig drives (interpret's budget math, policy
signing/verification, the bus fences)."""

import pathlib

HERE = pathlib.Path(__file__).parent
QA = pathlib.Path(r"D:\Repo\quorum-agent")
SOS = pathlib.Path(r"D:\Repo\salient-os")

# Whole files (the changed surface + the vendored APIs it drives).
WHOLE = [
    (QA, "hermes_cli/observability/salience_observer.py"),
    (QA, "tests/hermes_cli/test_salience_headroom.py"),
    (SOS, "salienceos/interpreter/interpreter.py"),
    (SOS, "salienceos/interpreter/policy.py"),
    (SOS, "salienceos/interpreter/signal.py"),
    (SOS, "salienceos/interpreter/bus.py"),
]

# Line-range excerpts (1-indexed, inclusive).
EXCERPTS = [
    (QA, "agent/turn_context.py", 480, 515,
     "the consumer call site WITH the no-ratchet baseline fix + IterationBudget rebuild"),
    (QA, "agent/turn_context.py", 1070, 1092,
     "pre_llm_call window-open (fires AFTER the consumer — the open window at the call site is turn N-1's)"),
    (QA, "tests/hermes_cli/test_salience_consumer.py", 411, 447,
     "the source-text pin: call-site shape + ordering + the no-ratchet default"),
    (QA, "cli-config.yaml.example", 1690, 1740, "salience config block (enabled + consume_compute + compute_headroom)"),
    (QA, "box/settings.yaml", 36, 75, "box opt-in: floor 25 + headroom 15 + prose"),
    (QA, "box/server.py", 142, 165, "box ferry: settings -> runtime hermes config"),
    (QA, "tests/conftest.py", 443, 470, "suite-wide observer disable/reset guard"),
]

CONTEXT = """
PR-H3 makes the FIRST governed knob of the SalienceOS judgment system GENUINELY GOVERN in
quorum-agent (the test rig). Background, all already merged and NOT under review except
where THIS PR changed it: PR-H1 (produce-only observer) records per-turn signals + one
Directive onto a per-session hash-named JSONL audit bus; PR-H2 (consumer) reads the PRIOR
turn's directive and applies its compute_budget to the host's per-turn iteration budget,
verbatim, fail-open, via one guarded call in agent/turn_context.py. H2 was
behavior-preserving by construction (pinned policy window, no ATTENTION signal).

THIS PR (H3), producer-side only:
1. The produce policy window becomes [floor, floor + salience.compute_headroom]. The new
   config knob is a plain int >= 0, DEFAULT 0 (= pinned window = exactly the H2 shape),
   fail-inert on bad values (bool/negative/float/garbage => 0; digit-strings honored;
   capped at 1,000,000 so an absurd YAML int cannot overflow the interpreter's float
   scaling). Widening happens in ONE shared _issue_template(subject, floor) at
   issue_policy (signing) time; the one-time verify_policy probe goes through the same
   helper, so it validates the exact shape the producer issues.
2. The _Window counts its ATTRIBUTED produce events (post_tool_call + api_request_error
   that passed the turn-id guard, INCLUDING unmapped read-only tool calls). At finalize,
   if events > 0, _close_locked synthesizes ONE ATTENTION signal: influence =
   min(1, events/8), confidence 1.0, provenance ("attention", "events:<n>"), and
   PUBLISHES it to the bus BEFORE interpreting — a signal that moves the budget must be
   ON the audit record; a failed publish DROPS the signal (absent ATTENTION => floor),
   never interprets off-record. events == 0 => no signal => directive exactly at floor.
3. The vendored interpreter (unchanged) turns that into
   budget = floor + round_half_up(attention * headroom), clamped to [floor, floor+H].
   allow_immediate_reconfigure stays False (Finding F): high ATTENTION cannot flip
   mid-turn reconfigure timing.
4. _operator_budget now also reads agent.max_turns / max_turns (the host's REAL operator
   chain) after the rig keys — pre-H3 a stock config floored produce-path closes at the
   25 fallback while the real budget was e.g. 500.
5. THE RATCHET FIX (turn_context.py): the host assigns the governed value back to
   agent.max_iterations (the conversation loop's guard reads it, so raises must
   propagate). Flooring the next turn on that SAME value would compound (25 -> 40 -> 55
   -> ... no decay, survives restart). The call site now captures the operator's
   PRISTINE budget once per agent (_salience_operator_iterations) and ALWAYS passes it
   as the consumer's default, so every window is [operator, operator + headroom]: busy
   turns raise within it, quiet turns decay back to the operator floor.

Guarantees under review:
G1. BOUNDED ESCALATION. Within a process the applied budget never exceeds
    operator + headroom and never falls below the operator floor; nothing compounds
    across turns; a quiet turn decays to the floor. The ONLY cross-restart carry is the
    documented resume caveat (first resumed turn reapplies the last RECORDED budget).
G2. FAIL-OPEN / NEVER-BRICK (unchanged from H2, re-verify against the NEW code): any
    failure, bad knob value, absent directive, kill switch => the caller's default,
    never < 1, never an exception into the turn. KeyboardInterrupt is DELIBERATELY not
    caught (Ctrl+C must reach the host) — do not report that.
G3. A4 FLOOR: a zero-event window's directive is EXACTLY the floor; the finalize-on-read
    floor is the pristine operator default passed by the call site.
G4. A3 WITH MOVEMENT: turn N applies the most recently RECORDED turn's directive
    (normally N-1). Budgets now genuinely differ, so a stale/self-read is a REAL wrong
    number.
G5. AUDIT HONESTY / FENCE (Finding G): every signal that informed a directive is on the
    record; provenance stays ref-shaped (<=128-char tokens, no tool payload); the
    synthesized signal carries confidence 1.0 and exactly ("attention","events:<n>").
G6. CONSUMER, NOT DECIDER (Finding D, unchanged): the recorded budget is applied
    verbatim — never re-clamped. The consumer read path has ZERO diff in this PR.
G7. HONESTY: docstrings/config/box prose must neither overstate nor understate the
    movement, its bounds, the saturation constant, the default posture, or the resume
    caveat.

ALREADY REVIEWED by 2 internal passes AND a 5-model general panel on this PR; the
following are ALREADY FIXED — do NOT re-report them (only report if a fix is INCORRECT
or introduced a new defect):
(a) the compounding ratchet above (fix: pristine-baseline default at the call site,
captured once per agent under an is-None guard; pinned by a source-text test that
asserts EXACTLY ONE baseline assignment guarded by the is-None check and bans
default=agent.max_iterations, plus a chained-cadence test 25->40->40->25 that also
pins the durable bus record u2==40/u3==25); (b) misattributed events cannot count (the
events increment sits AFTER the turn-id guard; mutation-verified test); (c) the
headroom sanity cap (1e6) against float overflow making every close fail; (d) the box
ferry mirrors the observer's FULL parse contract (bool/float/negative/garbage => 0,
ints and digit-strings honored, capped at 1e6) — NOT int()-coercion; (e) quiet-turn
test also asserts an empty signal record; bad-headroom test asserts the floor directive
IS on the record (inert distinguishable from a swallowed raise); confidence==1.0
pinned; (f) docs: box README/HANDOFF/STATUS present-tense prose updated, digit-string
honesty, sharpened resume caveat, within-a-process qualification on the floor/ceiling
bound, "activity is a v1 ATTENTION proxy" phrasing; (g) produce-path (session-end)
close floors at the CONFIG-derived operator budget which may differ from the live
agent's constructor/env value — consumed only via the documented resume path, stated
in _close_locked's docstring. Also known and out of scope: headroom is deliberately
operator-uncapped below 1e6 (their compute); the activity count is a deliberately
coarse v1 proxy (like the tool-name heuristics); the resume caveat itself (last
RECORDED budget reapplied on a fresh process); ADR 0001/0002 exclusions (no
cross-process authenticity, consistent-malicious-rewrite, tail-truncation-across-
reopen); the vendored salienceos/ internals (flag only the RIG MISUSING those APIs).

YOUR VALUE: what both internal reviews missed. Hunt hardest for: any OTHER feedback
path that re-anchors the floor to a governed output (getattr defaults, resume paths,
the box's own bounded_iterations call in server.py, _budget_from_disk promotion);
arithmetic edge cases in the influence/scale round-trip; a window/counter attribution
hole; a way the synthesized publish can double-count or split a window; test blindness
where an expected value coincides with a wrong implementation.
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
(HERE / "bundle_h3.txt").write_text(bundle, encoding="utf-8")
print(f"bundle: {len(bundle):,} chars (~{len(bundle) // 4:,} tokens)")
