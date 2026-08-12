# Durable emission-quota store + durablestate substrate — disposition (ADR 0003 residual-sweep follow-up)

5-vendor external CODE panel on the SHIPPED durable emission-quota counter store + the shared
`durablestate` substrate (branch `feat/durable-emission-quota`) BEFORE merge. Lean pass — reuses the
already-vetted provenance-store pattern, so it targeted the NEW surface: the substrate extraction, the
FAIL-CLOSED direction for a restrictive bound, and domain separation.

## Verdicts + cost

| model | verdict | cost |
|---|---|---|
| openai/gpt-5.1 | SOUND (LOW: shared error text) | $0.0251 |
| google/gemini-2.5-pro | SERIOUS_FLAWS (verify-then-filter negative counts) | $0.0775 |
| anthropic/claude-opus-4.1 | MINOR_ISSUES (partial-write / persist-fail) | $0.2963 |
| x-ai/grok-4.5 | (persist-side) HIGH save-fail + MED self-heal; load airtight | $0.1089 |
| qwen/qwen3-max | load fail-closed SOUND; subject-collision HIGH (refuted) | $0.0192 |
| **TOTAL** | | **$0.5269** |

## What the panel AGREED is sound (verify-before-trust)

The **load path is airtight** (grok, qwen, gpt): every present-but-untrusted case (bad/missing MAC,
wrong subject, corrupt, truncated, wrong shape) returns `ok=False` → `_emission_store_untrusted=True` →
`emission_allowed` denies any finite-limit host; a MISSING store is a fresh trusted allow; None/unlimited
hosts unaffected. **Domain separation is real** — distinct labels through `durablestate.mac`; a
provenance store cannot be replayed as an emission store (pinned), and the provenance refactor kept its
domain label so its MAC is byte-identical. **Restart-reset is closed** on the happy path (pinned e2e).

## Fixes applied (the real issues were on the PERSIST side + a load discrepancy)

1. **Save-failure was best-effort, not fail-closed (grok F1 HIGH, opus F3).** For a RESTRICTIVE bound a
   silent persist failure degrades to session-lived, so a restart reloads a stale lower count and
   refreshes the budget — the exact gap this closes. **FIXED:** `_persist_emission_counts` now trips
   `_emission_store_untrusted=True` on a failed save (deny limited hosts + signal), the fail-closed
   direction (mirrors the provenance store's fail-honest, but stricter for a bound). Pinned:
   `test_save_failure_trips_fail_closed_deny`.
2. **Self-heal of an untrusted store (grok F2 MED).** While untrusted, a consume (e.g. an unlimited
   host) would overwrite the corrupt store with a clean low-count one → next session trusts a
   near-fresh budget without operator action. **FIXED:** `_persist_emission_counts` NO-OPs while
   untrusted — only the operator resolving the store clears it. Pinned:
   `test_untrusted_store_is_not_self_healed_by_a_consume`.
3. **Verify-then-filter on negative counts (gemini CRITICAL — sev really LOW, host-key-required).** load
   validated the MAC over the full body then silently FILTERED negatives, returning trusted data ≠ the
   signed data. Only forgeable WITH the host key (not the agent), so not an agent exploit — but a real
   discrepancy. **FIXED:** a negative/non-integer count now makes the whole store UNTRUSTED (fail
   closed), no silent filter. Pinned: `test_negative_count_is_untrusted_not_silently_filtered`.

## Reproduced-and-REFUTED / documented

- **Cross-workspace subject collision (qwen HIGH)** — REFUTED: `workspace_subject` is the RESOLVED
  ABSOLUTE path, unique per directory; a store for workspace A never verifies for B (subject mismatch).
- **`bytes(domain)` type confusion (opus LOW)** — REFUTED: domains are `bytes` literals; a `str` domain
  would RAISE (`bytes(str)` needs an encoding), caught → untrusted, never a silent collision.
- **Partial/truncated write loads as trusted-empty (opus/qwen)** — REFUTED: `os.replace` is atomic (a
  reader sees whole-old or whole-new); a truncated/invalid file fails json/MAC → untrusted, not
  trusted-empty. qwen self-corrected ("no fail-open in load").
- **Shared `_assert_store_outside_workspace` error text says "provenance_store" (gpt/grok LOW)** —
  cosmetic (operator message), no security effect; left as a documented nit.
- Concurrency (sequential-session design; file-lock a follow-up) and the public-default-key degradation
  (unreachability is the real property) carry over from the provenance-store disposition.

## Net

The layer's load path + domain separation + restart-close were sound; the real issues were persist-side
fail-direction (save-failure + self-heal) and a load discrepancy (negative counts), all fixed to fail
CLOSED for the restrictive bound. Pure-Python + stdlib; 743 tests green cross-platform, no Sparky needed.
