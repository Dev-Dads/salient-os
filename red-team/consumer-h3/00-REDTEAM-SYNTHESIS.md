# PR-H3 (SalienceOS compute-budget MOVES) — red-team synthesis

PR-H3 is the change that makes the first governed knob **genuinely govern**. PR-H1 wired
the produce-only observer; PR-H2 wired the compute-budget consumer but was
behavior-preserving by construction (the policy window was pinned `min==max` and no
ATTENTION signal was ever produced, so the directive echoed the operator's own budget).
H3 opens the window to `[floor, floor + salience.compute_headroom]` (new config knob,
**default 0** ⇒ still pinned ⇒ exactly the H2 shape) and synthesizes ONE ATTENTION signal
per turn from the window's attributed activity count, so a busy turn buys the NEXT turn up
to `headroom` extra iterations — conservatively (bounded by the signed window),
reversibly (decays to the floor when quiet), with an off switch (headroom 0 or
`consume_compute: false`). That is the Stage-2 acceptance bar. Producer-side only: zero
changes to the vendored `salienceos/` core and zero changes to the consumer read path.
Reviewed under Josh's authorized **double-pass** policy, reproduce-before-accept
throughout. Target: `Chance6706/quorum-salienceos-rig` (the SalienceOS rig fork — NOT
quorum main), merged as **#16** (squash `ea1c6693b`).

## Verdict

The one real defect was caught internally and independently reproduced by the general
panel: **the ratchet** — the governed value was fed forward into `agent.max_iterations`
and then used as the next turn's floor, so sustained activity compounded without bound
(25 → 115 in seven busy turns, no decay, surviving restarts). Fixed at the call site by
binding always against the operator's pristine budget. **No production correctness defect
survived either external panel** — the loudest pass-2 CRITICALs (a `_budget_from_disk`
cross-restart ratchet, a 10³⁰⁸-event overflow) dissolved under reproduction. The rest of
the gauntlet's value was test-honesty: turning the anti-ratchet defense from a bypassable
source-text pin into a **behavioral** test, and property-testing the box ferry. Every
accepted finding is fixed and mutation-verified. Live proof at the bottom: the budget
moves 25 → 40 → 33 → 25 → 40 on the real dispatch path, chain-verified, no compounding.
129 salience tests; ruff + ty clean. Shipped.

## Internal pass 1 (2 reviewers, on the initial implementation)

Both reviewers, working independently (correctness/fail-open + design/test-honesty),
**reproduced the same HIGH before fixing it**:

- **[HIGH] the ratchet.** `turn_context` assigned the governed value back to
  `agent.max_iterations` and passed *that* as the next turn's `default`, so the A4 floor
  re-anchored to the previous salience OUTPUT. With the box's headroom 15 over floor 25,
  a live reproduction showed **25 → 40 → 55 → 70 → 85 → 100 → 115** across seven busy
  turns, never decaying, and recovering **85** from disk after a mid-session crash — the
  ratchet survived restarts. The fix: capture the operator's pristine budget once per
  agent (`_salience_operator_iterations`) and always bound against it, so every window is
  `[operator, operator + headroom]`. The assign-back stays (the host's iteration loop
  reads `max_iterations`, so a raised budget must propagate).
- Plus: a misattribution test gap (the activity counter's fail-closed guarantee was
  unpinned), the `_MAX_HEADROOM` sanity cap (a 300-digit config int overflowed the
  interpreter's float scaling, making every close fail — contained but silently inert),
  and a docs-honesty sweep over every "inert in v0" claim.
- Fixes: rig commit `affedbbdc`.

## External pass 1 — general panel (2026-08-07)

deepseek-v4-pro, x-ai/grok-4.5, mistral-medium-3-5, kimi-k3, glm-5.2. 5/5 returned; three
(deepseek, kimi-k3, glm) exhausted the 16k budget mid-reasoning and delivered raw
analysis dumps — still mineable, each contributed. Run on the post-internal-fix code, so
the ratchet was already fixed; the panel attacked the fix.

### Accepted (fixed in `d6b6066e5`)

- **[LOW, convergent — grok F1/F4 + kimi K1] the anti-ratchet pin had holes.** The
  source-text pin banned `default=agent.max_iterations` but did not require the is-None
  guard nor the capture RHS, so re-capturing the baseline every turn — or an
  `or agent.max_iterations` fallback — would silently reintroduce the ratchet while the
  suite stayed green. Strengthened the pin (this hole was closed *properly* in pass 2 with
  a behavioral test).
- **[LOW, convergent — grok F3 + kimi K4 + glm] the box ferry was stricter than the
  observer on strings**, so a quoted `"15"` pinned the window in the box only. Aligned it
  to the observer's full parse contract.
- **[LOW, grok F2] produce-path floor source** — a session-end close floors at the
  config-derived operator budget, which can differ from the live agent's value; consumed
  only via the resume path. Documented in the close docstring.
- Cadence test hardened with durable-bus assertions.

### Rejected with evidence

- **mistral's SERIOUS_FLAWS (CRITICAL resume-ratchet + HIGH budget-0 underflow):** the
  resume-ratchet assumed the pre-fix floor (the fixed point is `operator + headroom`,
  re-anchored each restart to the constructor value); the budget-0 guard it "suggested"
  is verbatim the line already present. Both rejected.
- **deepseek's HIGH produce-path floor divergence:** glm's own ordering analysis refuted
  it (the rollover close is a no-op whenever consumption is on — finalize-on-read closes
  first); the "untested `_budget_from_disk`" claim was a bundle-visibility artifact.
- **kimi K2** self-downgraded (documented env non-goal).

## Internal pass 2 (2 reviewers, on the post-pass-1 code)

- **Reviewer A: SOUND.** Verified every pass-1 fix correct as landed — the ferry parse
  byte-for-byte equivalent to the observer's via a 26-value battery, the floor-source
  docstring true, the cap and lock discipline clean. One LOW: a frozen out-of-contract
  baseline (if `max_iterations ≤ 0` at first capture, a later host repair is clobbered) —
  only ever governs down, triggers on host state the contract disclaims.
- **Reviewer B: MINOR_ISSUES.** Ran every guarantee's sabotage in memory (all red
  correctly) and sharpened the pin finding: the ratchet defense was *entirely*
  source-text (a decoy helper bypasses it; `test_no_ratchet_across_busy_turns` drives
  `bounded_iterations` with a constant floor and never exercises the real capture-and-
  feed-forward path). Plus: the box ferry had zero tests and a hardcoded magic number;
  the close docstring's "never by an in-process turn" was too absolute (an
  `on_session_reset` with an open window can consume the config-floored directive
  in-process); a dead `_expected` test helper.

## External pass 2 — coding-specialist panel (2026-08-07)

qwen3-coder-plus, kat-coder-pro-v2.5, kimi-k2.7-code, laguna-s-2.1, grok-4.5 (anchor).
4/5 returned — kat-coder exhausted its budget on reasoning with empty output, **exactly
as in H1 and H2** (a consistent failure mode for this model on this task). kimi-k2.7
returned a `finish=length` reasoning dump. Run on the post-pass-1 code.

### Accepted (fixed in `3c485c4b5`) + internal-pass-2 findings

- **[LOW → behavioral fix, grok F1/F4 + internal-B F1] the pin is now a BEHAVIORAL
  test.** Extracted `salience_observer.govern_iterations(agent)`; the call site is a
  single guarded call. `test_govern_iterations_no_ratchet` drives the real helper across
  turns (25 → 40 → 40 → 25) and reds if the once-per-agent guard is dropped or the
  baseline is captured from the governed value — killing the decoy-helper / literal-RHS /
  or-fallback bypasses a regex pin cannot catch. The call-site test keeps only the
  ordering pins.
- **[LOW, internal-A] out-of-contract baseline self-heals** — captured whenever the
  stored value is not a positive int; the recapture can only pick up a host value, never
  a governed one.
- **[LOW, grok F2] attention publish soft-drops SystemExit too** — the arm now catches
  `(Exception, SystemExit)` (never KeyboardInterrupt), so a SystemExit-shaped host I/O
  helper drops only the ATTENTION signal, not the whole turn's directive.
- **[LOW/MED, internal-B F2] box ferry extracted + property-tested** — `_resolve_headroom`
  with `test_box_ferry.py` pinning that ferry-then-observer resolves identically to
  straight-through-observer (a regression to `int()` coercion that laundered `True`→1 /
  `3.7`→3 reds). Dropped the ferry's redundant cap (the observer re-caps).
- Docs: the close docstring softened; the config-example resume caveat now states a
  replayed turn can exceed the *current* `floor + headroom` after a headroom reduction;
  dead `_expected`/`_half_up` helpers deleted.

### Rejected with evidence (reproduce-before-accept earning its keep)

- **[qwen CRITICAL + laguna CRITICAL] `_budget_from_disk` cross-restart / cross-turn
  ratchet.** The claim: promoting the recovered directive into the cache "defeats the
  entire ratchet fix." **Reproduced against the module** — the promoted value is
  overwritten on the very next window close; a quiet turn decays to the floor (25); the
  in-process floor is always the pristine baseline; the recovered value is bounded by the
  prior process's signed window and can never compound to 55. Laguna's own text
  self-corrects toward this twice; kimi-k2.7 raised the same suspicion and refuted it;
  both internal reviewers traced it safe. **False positive.** (Removing the promotion, as
  they suggested, would reintroduce the H2 grok-F2 one-shot-recovery bug.)
- **[qwen HIGH] 10³⁰⁸-event float overflow** — `window.events` is incremented once per
  tool call; it is physically bounded by real tool calls in a turn and cannot approach
  that magnitude. No trigger.
- **[laguna HIGH] private `bus._directives` access** — pre-existing H2 code H3 never
  touched; out of scope (the buses are vendored together at a single version).
- kimi-k2.7 converged on "sound," refuting every serious candidate it raised (including
  the disk-promotion one) in its own reasoning.

Pass 3 was authorized (Josh raised the ceiling to three passes at discretion) but
**skipped** — no substantive production finding survived pass 2 to justify it.

## Pre-PR stale-documentation sweep

A dedicated sweep before opening the PR caught one live-UI contradiction: the dashboard
transparency-panel caption still said compute is "intentionally pinned in v0
(behavior-preserving) — no faked movement" while the panel's own JS renders the real
`compute_budget` moving 25 → 40. Fixed (`68b490cc3`). Frozen HANDOFF/STATUS build-log
entries recording "stayed 25" from pre-H3 runs were left intact as honest dated history.

## Live proof (Stage-2 artifact)

`live_proof.py` drives the REAL `lifecycle → observer → interpret → bus` path with the
box's exact config (floor 25, headroom 15) — real emitters (`model_tools.
_emit_post_tool_call_hook`, `lifecycle.invoke_hook`), real interpret, real audit bus, no
mocking of the observer. Only the tool events are injected rather than model-generated,
because of the box's known tool-exec gap (ollama returns tool calls as content; a natural
chat turn would not reliably fire them). Output (`live_proof_output.txt`):

```
  turn  tool events  applied budget   note
     1            -              25   first turn: operator floor
     2     8 (busy)              40   applies u1 -> saturates to ceiling
     3     4 (half)              33   applies u2 -> mid-window
     4    0 (quiet)              25   applies u3 -> decays to floor
     5     8 (busy)              40   applies u4 -> ceiling again (no compounding)

audit bus chain_verified = True
recorded directives: u1=40, u2=33, u3=25, u4=40
```

The budget moves within the signed `[25, 40]` window, scales with activity, decays to the
floor on a quiet turn, and returns to the ceiling on the next busy turn **without
compounding** — the anti-ratchet fix holding on the real path — with the audit chain
verifying.

## Outcome

Four internal reviews and nine returned external model-runs across two complementary
rosters (kat-coder DNF a third time). Net: one real HIGH (the ratchet), fixed at its root
and pinned behaviorally; a cluster of LOW test-honesty and doc items; and two loud
external CRITICALs that dissolved under reproduction. Bounded escalation with decay, A4,
A3, deny-shaped-⇒-default, verbatim application, audit honesty, and fail-open all hold and
are mutation-pinned. The knob is live: with the default `compute_headroom: 0` it is inert
(exactly H2); the box opts in at 15 and the budget genuinely moves. Merged as
`Chance6706/quorum-salienceos-rig#16`.
