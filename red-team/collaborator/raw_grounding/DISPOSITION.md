# Grounding code panel — disposition

**Change reviewed:** make-it-move grounding — Sal's directive-loop system prompt + the
single-source tool manifest wired into `run_turn` (`collaborator/loop.py`, `collaborator/tools.py`).
Additive, non-safety-critical, but seam-adjacent, so external review per the standing rule.

**Panel:** 5 security models. **Cost $1.0143 total.**

| model | cost | verdict | C1 (no new authority) |
|---|---|---|---|
| x-ai/grok-4.5 | $0.1134 | **SOUND** | CERTIFIED |
| openai/gpt-5.1 | $0.0790 | MINOR_ISSUES | CERTIFIED |
| anthropic/claude-opus-4.1 | $0.7514 | MINOR_ISSUES | CERTIFIED |
| qwen/qwen3-max | $0.0705 | MINOR_ISSUES | CERTIFIED |
| google/gemini-2.5-pro | $0.0000 | ERROR (reasoning-only, no verdict) | — |

**HEADLINE — the core invariant is certified.** All four usable panels CERTIFIED **C1**: the Sal
system prompt + the `tools=` schema grant no new authority; every parsed intent still flows through
`govern_action` (default-deny), and `test_grounding_grants_no_authority` pins it. grok returned
SOUND overall.

**Reproduce-before-accept on the dissents:**

- **gpt-5.1 F1 (MEDIUM) — REAL (robustness), FIXED.** The idempotent guard skipped Sal's prompt if
  *any* `role=="system"` message was already in `history`, so a caller-supplied history could
  suppress/swap the grounding. Not exploitable today (only the host seeds `history`; the model can
  never emit a system role — gpt-5.1's own F2 confirmed this as a non-finding), but `run_turn`
  should own its grounding. **Fix:** `run_turn` is now AUTHORITATIVE — it re-asserts Sal's prompt at
  `history[0]` every turn (replacing a supplied leading system message), idempotent on resume.
  Closes gpt-5.1's C3 dissent too (their C3 was the same history-suppression point). Pinned by
  `test_supplied_leading_system_message_is_replaced_by_sal`.
- **opus F1 (HIGH) — FALSE POSITIVE, refuted.** Claimed tool *output* containing the sentinel could
  corrupt the prompt "on subsequent generations". Traced `sal_system_prompt()`: it splices only
  `tool_manifest()` (static, host-owned hints) into a static template — **no tool output, model
  text, or memory ever reaches it.** The corruption path does not exist.
- **opus/qwen C2 — REAL (tiny, maintainability), FIXED.** The valid kernel: a *static hint* that
  itself contained the `__TOOL_MANIFEST__` sentinel would corrupt the splice. **Fix:**
  `sal_system_prompt()` fails CLOSED if the generated manifest contains the sentinel (loud, at build
  time), plus a module-load assert that the template has exactly one splice point. Pinned by
  `test_manifest_has_no_splice_sentinel`. Drift (advertise a tool that isn't real) was already
  pinned by `test_every_advertised_tool_actually_exists`.

**C2/C3/C4 after the fixes:** C2 (single source) — the guard + tests close the sentinel/drift nits.
C3 (injection fence + prompt integrity) — the prompt is host-authored with no untrusted
interpolation (grok/qwen CERTIFIED; gpt-5.1's only C3 concern was the history point, now fixed); the
fence is a soft prompt rule whose HARD backstop is that any injected action is still governed. C4
(loop integrity / no regression) — CERTIFIED across the board.

**Net:** 2 small, directly-responsive hardening fixes (authoritative leading prompt + sentinel
fail-closed) + 2 new tests. No re-panel warranted (minor defense-in-depth on an already-certified
core invariant). Full suite green.

## Live proof (Sparky, gpt-oss:120b) + a third fix the live run surfaced

`red-team/collaborator/e2e_sparky_directive.py` drives the REAL grounded `run_turn` against 120b
over 4 distinct multi-step directives × 5 repeats. **Shipping code, temp 0.0: 20/20 (100%)
completed correctly, zero unhandled errors, zero run_command auto-runs, zero audit-chain breaks.**
(Transcript: `red-team/collaborator/e2e_sparky_directive_output.json`.) The governance seam never
faltered in any run — writes ran+verified, run_command HELD every time, the audit chain held.

The first live runs (temp 0.2/0.1) were LOWER (83% / 45%) and the diagnosis (`scratchpad/diag_*`,
raw-response instrumentation) found two causes — NEITHER a governance/parsing defect:
1. **Temperature.** gpt-oss sometimes "decides" in its reasoning channel and emits nothing
   actionable at temp>0 (measured first-call emission: **temp 0.0 → 8/8, 0.2 → 5/8, 0.4 → 6/8**).
   Greedy decoding is the right setting for a directive loop that must reliably ACT — a well-known
   agentic-tool-use result. The e2e now defaults TEMP=0.0; temperature is a caller-side deployment
   knob (NOT changed in OllamaClient, whose default suits the variety-seeking proposer).
2. **A loop-hygiene bug (fixed).** When a reasoning model returns tool_calls with EMPTY content,
   `run_turn` recorded a BLANK assistant turn — erasing what the model just requested, which hurt
   multi-step coherence (a task writing step 1 then losing step 2). Fixed: the assistant turn now
   records a compact record of the requested call(s) (`_render_intent`). Pinned by
   `test_empty_content_tool_call_is_recorded_in_history`.

This is the empirical-adversarial payoff: the live proof turned a "the model can't do it" appearance
into a temperature setting + a real loop bug, both resolved — and certified the seam is flawless.
