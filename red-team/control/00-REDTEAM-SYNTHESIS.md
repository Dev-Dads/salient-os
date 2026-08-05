# Control Seam Red-Team — Synthesis (v0.1)

**Date:** 2026-08-05
**Under review:** `salienceos/control` (the interpreter↔verifier seam) plus its small
verifier extension (`Verifier.verify(escalate_to=…)`, `max_stakes`, self-describing
`Verdict`).
**Process:** the most heavily reviewed component so far — **three internal subagent
reviews and two external five-model panels**, because it modifies the verifier (the
most load-bearing, previously-hardened component) and adds authority-adjacent gating.
Panel = DeepSeek R1, Grok-4.5, Qwen3-Coder, Kimi-K2-Thinking, GLM-4.6.

**Method (unchanged):** every load-bearing finding reproduced against the code before
acceptance (`verify_interp_findings.py`-style checks inline). Rejections are recorded
alongside confirmations.

---

## The three seam invariants (all hold in the final code)

1. **Salience only ESCALATES verification** — `max_stakes(envelope.stakes, escalate_to)`
   is upward-only; a directive can demand stricter verification than the envelope
   signed, never weaker.
2. **Fail-closed clearance** — an action clears only if `achieved >= required` and the
   verdict is not a conclusive failure; `required` is floored by BOTH the directive
   depth AND the verdict's effective stakes.
3. **Adaptation is a sealed learning gate** — allowed only when the directive deemed it
   eligible AND the verifier returned a real `VERIFIED`.

---

## Round-by-round

### Internal review 1 (two subagents) → fixed
- Correctness pass: all five invariants held; surfaced **OBS-1** — `decide()` ignored the
  envelope's own signed stakes as a clearance floor (the `envelope_stakes` param was
  dead). Fixed: `required = max(depth, _stakes_floor(envelope_stakes))`.
- Design/test-honesty pass: **empirically proved the escalation invariant had NO killing
  test** — a mutant disabling escalation passed all 23 tests, because no fixture paired a
  FULL directive with an envelope signed below HIGH. Fixed: added a NORMAL-envelope
  escalation test (mutation-checked to go red), plus a direct `escalate_to` test.

### External panel round 1 → fixed
- **HIGH (Grok F1/F2/F3, Kimi GOV-001, Qwen F1/F3):** the exported `decide()` trusted free
  `envelope_id`/`effective_stakes` parameters. A caller could desync them from the verdict
  to launder a one-source `VERIFIED` into a FULL clear, or clear action A with action B's
  verdict. **My own round-1 defensive clamp made it worse** (confirmed reproduction).
  → **Root fix: self-describing verdicts.** `verify()` stamps `envelope_id` and the
  `effective_stakes` it actually ran at onto the `Verdict`; `decide(directive, verdict)`
  binds `directive.subject == verdict.envelope_id` and reads the stakes from the verdict.
  No free params remain to desync. Closes the whole class at the root.
- **LOW:** `max_stakes` crashed on non-Stakes input (→ fail-safe rank-based version);
  `decide` crashed on None (→ guard).
- Rejected with reproductions: Qwen's two "CRITICAL" P-01 claims (caps/adaptation are
  gated), DeepSeek's signature-bypass and inverted-budget (verify_policy rejects them),
  GLM's NaN cap (range check already rejects it).

### Internal review 2 (validator on the refactor) → caught a regression I introduced
- Confirmed the verifier change is **additive** (composer untouched; stamping via
  `dataclasses.replace` can't alter status/reasons) and all invariant mutations turn the
  intended tests red.
- Its denylist-drift note led to finding a **real bug the refactor introduced**:
  `achieved_level`'s `_HARD_FAILURE_REASONS` denylist wrongly included `NO_WORLD_FACT`,
  which **always accompanies a genuine clean attestation** — so a real attested verdict
  mapped to NONE and RECEIPT-level clearance broke for low-stakes/artifact-less actions.
  No test caught it because the test verdicts were hand-built with only `INTEGRITY_ATTESTED`.
  → Fixed with a drift-safe **allowlist** (`_ATTESTATION_COMPATIBLE`); locked with a test
  that drives a **real** attested verdict through the verifier.

### External panel round 2 → SOUND / fail-closed hardening
- Grok: **SOUND**; DeepSeek: **SOUND**; Qwen: MINOR_ISSUES; Kimi: flagged malformed-input
  **crashes** (non-int `verification_depth`, unhashable `effective_stakes` → `TypeError`
  instead of deny). Reproduced and fixed: a `_denied()` fail-closed path, depth/stakes type
  guards, a `govern()` guard, a `STAKES_ORDER` completeness test, and a mismatch
  info-leak fix (`required_level` no longer surfaces another action's stakes floor).
- Rejected with reasons: Qwen's "depth=999 bypass" (clamp-to-FULL is the strictest, not a
  bypass), "is vs ==" (the code already uses `is`), and "use `==` not `<=`" for attestation
  (subset `<=` is correct — `==` would re-break real attested verdicts). GLM produced no
  output round 2 (exhausted its token budget on reasoning).

---

## Resolution

**All confirmed findings fixed; final suite 137 tests green** (1 symlink test skipped where
the OS forbids it). The seam has no surviving false-clear, false-adapt, or
scrutiny-lowering path under the stated adversary; malformed inputs now deny rather than
crash. Regressions cover: the escalation killer, verdict-binding desync, the envelope
stakes floor, the real-attested RECEIPT case, and every malformed-input path.

**The most useful process lesson:** an internal fix (round 1) introduced a regression
(the denylist) that only the *extra* validation pass caught — the same pattern the panel
caught in round 1 (my clamp). Fixes to a fail-closed gate need their own adversarial pass;
"it passes the tests" is not enough when the tests were written by the same hand as the fix.
