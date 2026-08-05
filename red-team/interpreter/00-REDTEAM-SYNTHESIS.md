# Interpreter Red-Team — Synthesis (v0.1)

**Date:** 2026-08-05
**Under review:** `salienceos/interpreter` (salience bus + central interpreter;
implements `SalienceOS_Design_Review_v0.2.md` Part 4 #2, invariant P-01).
**Two-stage review:** an internal pass by two subagents (correctness/fail-closed;
design-faithfulness/test-honesty), then an external five-model panel outside the
big three — DeepSeek R1, xAI Grok-4.5, Qwen3-Coder, Moonshot Kimi-K2-Thinking,
Zhipu GLM-4.6 — run blind on the post-internal-fix code.

**Method (same discipline as the verifier).** Every load-bearing finding was
reproduced against the code before being accepted (`verify_interp_findings.py`).
Raw internal reviews: `internal_review_*.md`. Raw panel critiques: `raw/`.
Panel verdicts: 3× SERIOUS_FLAWS (Grok, Qwen, Kimi), 2× MINOR_ISSUES (DeepSeek,
GLM) — again driven by **one** real defect (the zero-confidence inversion) plus a
crop of misreads.

---

## Resolution status — all confirmed findings fixed

Full suite: **98 tests green** (1 symlink test skipped where the OS forbids it).
Regressions in `tests/test_interp_review_fixes.py`; reproduction script now shows
every confirmed finding no longer reproduces.

### Internal review → fixed
| Finding | Sev | Fix |
|---|---|---|
| Verification semantics contradiction (a low-influence signal dropped verification below the uninformed default) | MED | Risk drives depth from the policy floor up; **absent risk = maximal caution** (`interpret`) |
| **Adaptation `min_verification` gate had no test** — deleting the clause left all adaptation tests green | MED | Coherent-policy gate test isolating the clause (`AdaptationVerificationGate`) |
| Finding-G "no durable bodies" was overstated (only field-name-based) | MED | Bounded ref-shaped tokens in `valid_signal` (`MAX_TOKEN_LEN`, `MAX_PROVENANCE_REFS`) — a body/CoT cannot validate |
| Hash chain built but never verifiable | MED | `SalienceBus.verify_chain()` + tamper/reorder tests |
| `verify_policy` didn't type-check bool/int cap fields | LOW/MED | `isinstance` checks for switches and numeric caps |
| `build`/iterator count wrong for a one-shot iterator | LOW | Materialize `signals` once |
| `_hard_deny` echoed untrusted identifiers | LOW | Blank `subject`/`policy_id` on hard deny |

### External panel → fixed
| # | Sev | Issue | Fix |
|---|---|---|---|
| P1 | **HIGH** | **Zero-confidence signal inversion** — `_aggregate` inserted `0.0` for an all-zero-confidence facet, so `agg.get(RISK, 1.0)` returned `0.0` (permissive) instead of the cautious absent-default; a misfiring publisher could lower verification and open the adaptation risk gate | Omit zero-weight facets from aggregation (treated as absent) |
| P2 | MED | Throwing iterator crashed the choke point (`except TypeError` too narrow) | Broadened to `except Exception` → fail-closed defaults |
| P3 | LOW | `adaptation_min_verification > max_verification` accepted (silently un-satisfiable adaptation) | Coherence check in `verify_policy` |
| P4 | LOW | Unknown/capability-shaped facets aggregated (inert, but incidental) | Filter aggregation to `KNOWN_FACETS` — "unknown grants nothing" is now structural + tested |
| P5 | LOW | Banker's rounding dropped verification at exact halves | Round-half-up for verification (cautious bias) |
| P6 | LOW | `emit` accepted any object; bus integrity/authority claims overstated | Type-guard `emit`; documented bus-records-≠-authorizes and `verify_chain`'s scope limit |

P1 was a regression introduced by the internal-review verification fix — caught
only because the external panel ran on the post-internal-fix code, which is the
whole reason for reviewing in that order.

---

## Rejected after reproduction *(stated, per the anti-manufactured-consensus rule)*

- **Qwen SAL-01/SAL-02 (CRITICAL "capability laundering via facet strings" / "adaptation without the policy switch"): REJECTED.** No knob reads capabilities from signals; `allowed_capabilities` is a pure policy pass-through; adaptation short-circuits on `policy.allow_adaptation`. Reproduced: caps stay `()`, adaptation stays `NONE`. These "CRITICAL"s are exactly the severity inflation the discipline guards against.
- **DeepSeek F1 / Qwen F3 (signature bypass via tampered fields): REJECTED.** `signed_payload()` covers all 12 substantive fields; post-signing tampering of `granted_capabilities` hard-denies (reproduced: caps `()`).
- **DeepSeek F4 (inverted budget window accepted): REJECTED.** `verify_policy` already rejects `min_budget > max_budget`.
- **DeepSeek F2 (adaptation gated on ceiling not "earned" depth): REJECTED.** The gate uses the *actual applied* `v_depth`; reaching it requires genuinely verifying that deeply (the safe direction) plus low risk plus the policy switch.
- **DeepSeek F3 (hard-deny subject leaks via `emit`): REJECTED.** `emit` records the directive, whose subject is already blanked.
- **GLM FC-2 (NaN `adaptation_max_risk` accepted): REJECTED.** The existing `0.0 <= x <= 1.0` range check already rejects NaN/inf (NaN comparisons are False), even though `_is_num` allows the type.
- **Qwen F6 (NaN/inf signal values): REJECTED.** `_unit` already rejects them.
- **Qwen F7 (race condition): OUT-OF-SCOPE.** The system is synchronous/single-threaded by discipline.

---

## Honest limits / design notes carried forward

- **Bus integrity (Grok F3):** `verify_chain()` detects accidental corruption,
  truncation, and reordering (the in-scope non-malicious-corruption case). A
  fully consistent malicious rewrite of every entry *and* the head is out of
  scope and needs a signed/anchored head under an audit key — the same threat
  boundary the verifier draws. Documented in `bus.py`, deferred.
- **Cross-subsystem influence comparability** is a convention (all emit `[0,1]`),
  not interpreter-enforced; mixing a binary and a continuous scorer on one facet
  lets the binary dominate. Documented in `scorers.py`; mitigation is
  per-publisher confidence, not interpreter magic.
- **Volume weighting:** one subsystem publishing many signals biases the
  confidence-weighted mean (bounded by policy caps, so not a P-01 issue). Left as
  a future arbitration-fairness decision.

---

## Bottom line

The P-01 fence held under both review stages: no path turns salience into
authority, `allowed_capabilities` is a pure policy pass-through, and adaptation is
conjunctively gated on the signed switch. The real defects were fail-closed
*semantics* (the zero-confidence inversion, the throwing-iterator crash) and
policy/audit coherence — all in the arbitration leaves and glue, not the fence.
Fixed and locked with regressions; the interpreter is the directive analog of the
verifier composer, and now as hardened.
