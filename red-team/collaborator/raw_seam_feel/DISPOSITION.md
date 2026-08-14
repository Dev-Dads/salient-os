# ② "make Sal talk back" feel PR (#59) — external review disposition

5-vendor OpenRouter CODE panel (`redteam_seam_feel.py`) + focused 2-model C3 re-cert
(`redteam_seam_feel_c3recert.py`). Per the standing rule (external review on EVERY non-doc PR).

## Panel result (5 vendors)

| Claim | Result | Notes |
|---|---|---|
| **C1 — P-01 preserved (authority)** | **CERTIFIED ×5** | None of the six feel changes create/widen an authority path; threaded history, workspace filenames, `target`, and the clock only affect what the model KNOWS or the page SHOWS. `govern_action` unchanged & sole gate. |
| **C2 — render safety / no XSS** | **CERTIFIED ×5** | Every new node sinks model/snapshot strings through `textContent`/`createTextNode` via `el()`; no `innerHTML`, attribute, or eval sink. CSP still strict. |
| **C3 — memory-threading soundness** | **NOT-CERTIFIED ×5 → CERTIFIED after fix** | Two findings; both closed in commit `4636244`, re-certified by the two finders. See below. |
| **C4 — context surfacing / no leak** | **CERTIFIED ×5** | `target`/grounding/clock add no egress and surface no raw write content; credential-shaped args in `target` are acceptable under the single-user auth-walled threat model. |

Verdict: **MINOR_ISSUES ×5** (the only substantive issue was C3).

The "CRITICAL/HIGH" severity headers in qwen's F1/F2/F4 were self-refuted in the same finding
("WHETHER ANOTHER LAYER CATCHES IT: YES … None needed") — blocked-attack confirmations, not real
breaks; gpt-5.1 walked each identically and concluded "None needed". Reproduced: not real.

## C3 findings & disposition (commit 4636244)

- **A — unbounded `self._history` growth** (all 5, unanimous highest-value fix). **FIXED**: new
  `_trim_history` bounds the running history to a rolling char window (`_HISTORY_CHAR_BUDGET=60000`,
  most-recent tail, always ≥1) before every `run_turn`; `run_turn` re-asserts system[0] so dropping
  an old system message is safe. Stored history is O(budget)+one turn (bounded, gpt-5.1 confirmed).
- **B — HELD + intervening submit + approve clobber/interleave + list aliasing** (grok F1, MEDIUM).
  **FIXED**: `_absorb_result` now advances `self._history` only on TERMINAL (DONE/FAILED); a
  HELD/PAUSED side-quest keeps its history on `task.history` for resume. `_trim_history` always
  returns a FRESH list, de-aliasing `task.history`/`self._history` from the list `run_turn` mutates.
- **F3 (grok, LOW)** — `_fail_active` doesn't advance memory on a hard exception. **DOCUMENTED as
  intentional**: no coherent `result.history` on an exception; the conversation stays at the last
  good state; the failed task is still shown honestly.
- **F4 / C4-3 (LOW)** — `target` may surface credential-shaped command/URL args. **ACCEPTED
  RESIDUAL**: panel-certified acceptable (operator-only, auth-walled `/state`, no new egress, never
  raw write content). Optional userinfo-stripping left as a future UX hardening.
- **F6 (qwen, LOW)** — optimistic-echo reconciliation edge. **ACCEPTED**: transient UI-only artifact
  that self-heals on the next poll when the real task appears; no state impact.

## C3 re-cert (gpt-5.1 + grok, the two F1 finders)

Both: **findings A and B RESOLVED, no new issue, C3 CERTIFIED.** gpt-5.1 traced the full
submit-A/hold-B/submit-C/approve-B sequence through the patched control flow and could not
reproduce the clobber; grok could not break the clobber or aliasing. Residual noted by both
(a char-boundary trim can cut mid-turn) is cosmetic — degraded context, not unsound threading.

## API cost

| Phase | Cost |
|---|---|
| 5-vendor panel | $1.5553 (opus $1.0647, grok $0.1556, gemini $0.1341, gpt-5.1 $0.1026, qwen $0.0983) |
| C3 re-cert (gpt-5.1 + grok) | $0.0489 |
| **TOTAL** | **$1.6042** |
