# Loop robustness — the empty-completion no-op — synthesis

*Found 2026-08-13 by the FIRST integrated live run of the Collaborator (loop + propose +
view driven as ONE session against a real gpt-oss:120b on Sparky) — the empirical
"prove the surface live before planning it" pass. Every prior live proof exercised one
piece in isolation and never surfaced this.*

Runner: `e2e_sparky_integrated.py`. Evidence: `diag5.py` (empty-rate per directive),
`diag6.py` (which retry perturbation escapes the streak).

## The bug

`gpt-oss:120b` intermittently ends a turn after emitting **only its private reasoning
channel**: `finish_reason=stop`, `content==""`, **no tool_calls** — completion tokens far
under budget, so **not truncation**. `collaborator/loop.py run_turn` read "no parsed
intents" as `stopped="final"` and returned a success-looking `TurnResult` with **zero
decisions** — a **silent no-op narrated as a finished task**. That directly violates the
loop's own promise ("a step that failed can't be narrated as success").

## What the diagnostics established (not inference — measured)

| Observation | Evidence |
|---|---|
| Empties happen **only with the tool schema present**; without `tools=` the model always returns prose (often a *hallucinated* result — the exact lie the loop exists to catch). | `diag_p1` |
| At greedy temperature the empty is **deterministic and streaks** — a plain same-input retry never escapes it (0/6). | `diag6` plain control |
| A prompt **nudge** ("you returned nothing, act now") also fails to escape (0/6). | `diag6` nudge |
| **Raising the temperature** escapes it (temp 0.7 → 5/6 recovered). | `diag6` temp0.7 |
| Empty-proneness is **prompt-specific and stable across a model reload**: post-reload `notes` → 1/8 empty, but `script_run` and `one_file` → 8/8 empty. Even a *trivial* one-tool task can be deterministically empty. | `diag5` before/after reload |
| `max_tokens` matters: 2048 was materially worse than 4096. make-it-move's default was **1024**. | `diag3` |

This **refines the make-it-move "greedy is best (temp≈0)" lesson**: greedy yields clean
tool calls *when the model emits one*, but greedy also makes the *no-emit* (analysis-stop)
failure **deterministic** for prompts prone to it. make-it-move's "20/20" did not catch
this because its directive set happened to be empty-resistant ones like `notes` — a
hermetic/loose live measure masking a real hole a live *adversarial* run found at once.

## The fix (shipped in this PR)

Defense in depth in `run_turn`, governance untouched (retry affects only *whether* a
response was obtained; every response still flows through `govern_action`):

1. **An empty completion is not a finished turn.** `_is_actionable(msg, parsed)` — a
   completion counts only if it DID (tool intent), TRIED (ambiguous call, surfaced), or
   SAID (non-empty text) something.
2. **Retry escapes the streak by perturbing sampling.** `_complete_actionable` keeps the
   first attempt at the client's own (preferred, low) temperature, then escalates the
   temperature on each retry (0.7 → 0.85 → 1.0) — the only lever measured to work. Needs a
   per-call `temperature` override on the client interface (`model_client.py`).
3. **Unrecoverable silence is honest.** Still empty after the budget → `stopped="empty"`
   with an explicit reply, **never** a success-looking `"final"`.
4. `max_tokens` default 2048 → **4096** (`model_client.py`), fewer empties to recover from.

## Live result (post-fix, `e2e_sparky_integrated.py`, 7/8 phases)

- **P1 directive acts** ✓ — write + read-back, multi-step, on disk (was fully empty pre-fix).
- **P3 view reflects live** ✓ — 3 governed / 3 ran.
- **P4 "comes to you"** ✓ — the propose channel surfaced a governed proposal.
- **P5a pause gate** ✓ — the pause held a live action (only reachable once escalation got an
  action out at all).
- **P5b/c controls** ✓ — leash tighten + veto.
- **P2 seam holds shell** — step 1 (`write_file`) ran; step 2 ("run hello.py", an
  empty-prone prompt) exhausted the escalated budget and reported `empty` **honestly**. The
  true residual: some prompts deterministically decline to emit; the loop surfaces that
  truthfully rather than faking done.

## Residual / follow-up (backend, not the loop)

Fully overcoming a backend that deterministically refuses to emit a tool call on certain
prompts is not the loop's job. Candidate follow-ups: a small non-zero *first-attempt*
temperature (tension with make-it-move's greedy choice — an operator/panel call), a
`reasoning_effort`/harmony serving setting, or a different resident model. Recorded, not
built here.
