# Host panel (② Stage A) — disposition

`collaborator/host.py` — the Host that owns loop+propose+view+ledger on one serial worker.
Two 5-vendor panels (`redteam_host.py`, ~$2.87). Reproduce-before-accept on every finding.

## C4 (P-01 / controls never grant authority): CERTIFIED by ALL reporters, both rounds.
The authority model is sound: controls only restrict or express host config; `approve` re-gates
via `loop.approve`→`reauthorized_or_denied`; no request data reaches `leash=`/`intent.source`/emit.

## Bug found + FIXED (reproduced first)
- **grok F1 (MEDIUM): partial-deny dropped a held action.** A turn holding MULTIPLE actions where
  one later DENIED cleared `task.held` entirely, losing the denied (still-retryable) one — so
  "DENIED stays retryable" only held when ALL denied. Fix: run all held; record all; keep the
  DENIED ones as still-held and stay AWAITING; accumulate RAN summaries on the task so the
  eventual resume note (once every held action clears) covers all approved actions. Regression
  test `test_partial_deny_keeps_the_denied_held_action_retryable`. Round-2 re-panel: grok SOUND,
  gemini explicitly cites the fix test as proving C3.

## Defensive hardening added (panel gpt-5.1 "highest-value = invariants around task states" + qwen F1)
- `_handle_resume` now re-validates `task.state in (RUNNING, QUEUED)` under the lock before
  resuming — a stale/duplicate ResumeJob can never re-enter `run_turn` on a declined/finished task.

## Findings REFUTED by reproduction / reading
- opus C1 (round 1) "ProposeJob runs concurrently with a TurnJob → two govern_action drivers" —
  the ticker ENQUEUES; `propose` runs on the ONE serial worker, and `_should_propose` is gated on
  `_worker_busy` + no RUNNING/QUEUED task (`test_should_not_propose_while_busy_or_running`).
  Round-2 opus reversed this to "NON-finding — serial worker prevents concurrent govern_action."
- opus C2 (round 1) "task.history read races / lying snapshot" — history is worker-only
  (`task.view()` never exposes it); the execute-then-record window is benign eventual-consistency,
  the snapshot stays internally consistent. Round-2 opus: "only worker thread accesses history."
- opus F1 (round 1) "unbounded retry" / double-execute — the single worker + `decision.consumed`
  block double-run; grok round-2 "double-execution blocked, NON-finding."
- gemini errored in round 1 (empty final, `finish=error`); round 2 it returned and certified C1–C4.

## Accepted nit (not fixed, by design)
- qwen F2 (MEDIUM→nit): `decline` during the brief `_handle_resume` window (task momentarily
  RUNNING) returns False; once the task settles back to AWAITING_APPROVAL a retried decline works.
  A transient UX timing thing, no safety/correctness impact (qwen affirms DENIED stays retryable
  and human text never reaches the model as authority). A PARTIALLY_APPROVED state would be more
  machinery than the nit warrants.

## Final: gpt-5.1 SOUND, gemini all-certified, opus all-certified, grok SOUND, qwen MINOR_ISSUES
(the sole withhold is qwen's C3 over the F2 UX nit above; the substantive C3 guarantees are met).
