# PR-H2 (SalienceOS compute-budget consumer) — red-team synthesis

PR-H2 wires the FIRST behavior-changing consumer of the judgment system into
quorum-agent (the test rig): `bounded_iterations(session_id, default)` reads the
directive the produce-only observer (PR-H1) recorded for the **prior** turn and
applies its `compute_budget` to the host's per-turn iteration budget, via one guarded
line before the `IterationBudget` rebuild. v0 is behavior-preserving by construction
(the policy pins min==max==operator budget and ATTENTION is unmapped, so the directive
echoes the operator's own budget — the wire becomes load-bearing without yet moving
anything). Reviewed under Josh's authorized **double-pass** policy: 2 internal + external
general panel (pass 1), then 2 internal + external coding panel (pass 2),
reproduce-before-accept throughout. Target: Quorum-Agent/hermes-agent, merged as **#32**
(supersedes #31, whose CI run was zombied by the 2026-08-06 GitHub Actions outage).

## Verdict

One HIGH production defect was caught externally — the general panel's failed-close
stale cache (a 2-turns-stale budget applied after a failed finalize) — plus a MEDIUM
recovery gap; everything else accepted was hardening, honesty, and test-mutation
coverage. The coding panel found **no surviving production correctness defect**; its
value was a structural A3 test pin and an out-of-contract-input cleanup. The distinct
H2 theme: v0's behavior-preserving design makes naive tests vacuous (directive budget
== default by construction), so much of the gauntlet's real work was making tests able
to tell "applied the directive" from "fell back to default". Every accepted finding is
fixed and mutation-verified (the test goes red when its production line is broken).
64 salience tests (31 observer + 33 consumer); ruff + ty clean. Ship — shipped.

## Internal pass 1 (2 reviewers, on abf36856)

- **Restart-fallback gating:** `_budget_from_disk` ran on every cache miss, so a
  cached (already-verified-once) bus could feed a later-tampered tail, and a failed
  close could resurrect a stale budget from disk. Gated to the COLD path only
  (`session_id not in _BUSES`); the value now comes from the bus's replay-**verified**
  in-memory store, not a second raw file parse.
- **Test honesty:** added the `recorded > default` case (kills a `min(budget, default)`
  down-clamp — the Finding D violation — mutation-verified); a **non-tail** wrong-hash
  tamper that only the bus replay-verify can catch (proves the integrity gate is the
  bus, not the consumer's own parsing); a cached-bus guard test; a real close + loud-log
  assertion in the template-validation test.
- **Honesty:** `verify_policy` probe re-documented as a one-time well-formedness check
  of the hardcoded template (no template knob is config-wired); corrected the overclaim
  that mapping a facet alone moves the budget (the pinned min==max window must also
  widen); "first governed knob" softened to "wired live; inert in v0".
- Fixes: c510915d.

## External pass 1 — general panel (2026-08-06)

deepseek-v4-pro, x-ai/grok-4.5, mistral-medium-3-5, kimi-k3, glm-5.2. 5/5 returned;
three (deepseek, kimi-k3, glm) exhausted the 12k budget mid-reasoning (`finish=length`)
and delivered raw analysis dumps rather than structured reports — still mineable, and
each contributed to an accepted finding. Grok and mistral returned clean reports.

### Accepted (fixed in 565aad8d)

- **[HIGH, grok F1 + kimi] failed-close stale cache.** After a failed finalize on a
  later turn, `_LAST_DIRECTIVE` still held the PRIOR turn's directive and the consumer
  applied it — a 2-turns-stale budget, violating A3. Now `_close_locked`'s except pops
  the cache: a failed close fails OPEN to default. Mutation-verified test.
- **[MEDIUM, grok F2] one-shot cold recovery.** The cold disk recovery wasn't promoted
  to `_LAST_DIRECTIVE`, so a second read before the next close silently dropped a
  verified budget back to default. Now promoted. Mutation-verified test.
- **[LOW, grok F3 + deepseek] redundant re-read / TOCTOU.** `_budget_from_disk`
  re-parsed the JSONL to find the last subject, which could select a stale directive if
  the file changed between replay and re-read. The second read is gone — last directive
  comes from the verified in-memory store.
- **[MEDIUM, glm, test honesty] the v0-equality blind spot, live.** The
  "`_close_locked` stops caching" mutation survived the whole suite because v0 makes
  the directive budget equal the default. Added a live-path second-read test (cached 20
  vs new default 99) that actually distinguishes cache-read from default.
- **[LOW, grok F5]** call-site comment: say "consumer", note the import is separately
  guarded.

### Rejected with evidence

- **mistral's CRITICAL/HIGH cluster (its SERIOUS_FLAWS verdict), all misreads:**
  F1 BaseException escape — KeyboardInterrupt is *deliberately* not caught (Ctrl+C must
  reach the host), and the SystemExit half is already inside the guard; F3 deny-shaped
  budget-0 acceptance — the budget<1 guard applies to ALL directives, not just
  hard-denies (pre-panel fix, stated in the bundle); F5 `_LOCK` re-acquire deadlock via
  `issue_policy` — the claimed inner acquire doesn't exist; the whole resolve holds the
  one lock. Its F2 (invalid-default seam) circled a real seam with an inverted
  mechanism — the code did NOT skip the close, it finalized with a manufactured floor;
  the genuine (much smaller) issue there was caught cleanly by the coding panel below.
- **kimi-k3's TOCTOU deep-dive self-refuted in its own reasoning:** the unverified
  `last_subject` was only ever a lookup KEY into the verified store, never a returned
  value — no unverified budget could be fed. (The redundant read was still removed, per
  grok F3.)

## Internal pass 2 (2 reviewers, on 565aad8d)

Both confirmed the pass-1 fixes correct as landed; residual items:

- **De-alias the cold-recovery promote (LOW, correctness):** `_budget_from_disk`
  cached the SAME dict the bus holds in its verified store, so a future consumer
  mutating the cache could corrupt `verify_chain()`. Now deep-copied (matching
  `directives_for`'s own copy protection). Mutation-verified test.
- **Pin newest-directive ordering (LOW-MEDIUM, test honesty):** nothing distinguished
  `directives[-1]` from `[0]` on the cold path (v0 equality again, and recovery tests
  used a single directive). Added a two-distinct-budget test asserting the tail wins;
  mutation-verified.
- **Honesty:** stale `_DEFAULT_BUDGET` comment corrected ("nothing consumes it yet" —
  this is the PR that consumes it); A3 wording softened to "the most recently RECORDED
  turn" (a turn aborting before opening its window records nothing); the promote
  side-effect documented.
- Fixes: b707c80e.

## External pass 2 — coding-specialist panel (2026-08-06)

qwen3-coder-plus, kat-coder-pro-v2.5, kimi-k2.7-code, laguna-s-2.1, grok-4.5 (anchor,
locked per Josh). 4/5 returned — kat-coder again exhausted its budget on reasoning with
empty output, exactly as in H1. kimi-k2.7 returned a `finish=length` reasoning dump;
qwen and laguna clean; the grok anchor went deep (21k completion tokens). Run on the
post-pass-1 code.

### Accepted (fixed in 42952ac8) — all carried by the grok anchor

- **[test honesty, grok F1] A3 ordering not structurally pinned.**
  `test_call_site_precedes_budget_rebuild` pinned consumer-before-`IterationBudget` but
  NOT consumer-before-`pre_llm_call` — a refactor moving the call site past the
  window-open would make turn N read its OWN directive (self-read, A3 broken) while the
  whole suite stayed green. Now the test also asserts the consumer call precedes the
  `pre_llm_call` dispatch.
- **[LOW, grok F3 + laguna + kimi] out-of-contract default manufactured a budget.**
  A non-positive int default (out of contract, but reachable) got a manufactured
  operator floor instead of being left alone — the consumer inventing a budget for a
  bad host value. Now a non-int / bool / non-positive default is returned UNTOUCHED
  with no finalize-on-read. Test extended (0, -5).
- **[LOW, grok F4, honesty]** documented that a resumed session's first turn reapplies
  the LAST RECORDED budget, which can differ from an operator setting changed while the
  process was down.
- **Defensive test:** object-source sub-1 budget ⇒ None (pins that the guard covers
  both `Directive` objects and dicts).

### Rejected with evidence

- **qwen CRITICAL deadlock in `_close_locked`:** the function acquires no lock (the
  `_locked` suffix means "caller must hold it") — the claimed re-acquire doesn't exist.
  Its SERIOUS_FLAWS verdict rested on this.
- **qwen HIGH SystemExit escape in disk ops:** the whole resolve body sits inside the
  outer `except (Exception, SystemExit)` — already contained.
- **laguna TOCTOU reading `_directives`:** that is the immutable in-memory store built
  at replay time, not the file — there is no second read to race.
- **laguna object-source budget<1 gap:** the guard sits AFTER the dict/object split
  and applies to both branches (now also pinned by the defensive test above).
- **grok F2 restart-recovery mutation-blindness:** grok itself flagged it "partially
  mitigated" — the pass-2 internal two-distinct-budget cold-recovery test already kills
  the "ignore disk, use operator budget" mutation.

## Outcome

Four internal reviews and nine returned external model-runs across two complementary
rosters. Net: one real HIGH (failed-close stale cache), one MEDIUM recovery gap, a
structural A3 test pin, an out-of-contract-input cleanup, and a cluster of
test-honesty fixes targeting exactly the blind spot v0's behavior-preserving design
creates — while every louder external CRITICAL dissolved under reproduce-before-accept
(a deadlock on a lock never taken, a SystemExit already contained, a TOCTOU on an
in-memory store). Fail-open, A3, deny-shaped-⇒-default, verbatim application, and
verified restart recovery all hold and are mutation-pinned. Merged as
Quorum-Agent/hermes-agent#32. The knob is live and inert; moving the budget requires a
separately-reviewed change that both maps a budget-moving facet and widens the policy
window.
