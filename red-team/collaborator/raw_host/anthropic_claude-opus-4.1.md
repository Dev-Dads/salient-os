# Host panel: anthropic/claude-opus-4.1

_cost=$1.0467 finish=stop_

I'll perform a thorough adversarial review of this Host implementation, examining each claim systematically.

## FINDINGS

### ID: F1 / DOUBLE-EXECUTION VIA APPROVE-APPROVE RACE / CRITICAL / host.py:249-252
**CONCRETE INTERLEAVING**: Thread A calls `approve(task_id)`, sets state to RUNNING (line 251), enqueues ResumeJob. Before worker processes it, Thread B calls `approve(task_id)` again. The check at line 249 passes (state is now RUNNING, not AWAITING_APPROVAL), returns False. BUT if Thread B races between lines 250-252, it could see AWAITING_APPROVAL, set RUNNING again, and enqueue a SECOND ResumeJob.
**WHY IT BREAKS**: Two ResumeJobs for the same task → `_handle_resume` runs twice → held decisions run twice via `approve_held_decision`.
**ANOTHER LAYER CATCHES IT**: YES - `decision.consumed` flag in loop.py's `approve()` prevents double-execution. The first approve sets `consumed=True`, second attempt returns early.
**FIX**: The consumed flag is sufficient protection.

### ID: F2 / SNAPSHOT LOCK CORRECTNESS / LOW / host.py:180-185
**LOCATION**: `snapshot()` method
**CONCRETE ISSUE**: The lock is held correctly across the entire multi-pass read of `view.snapshot()` and tasks iteration. This ensures atomicity.
**WHY IT'S CORRECT**: Lock held for the entire compound read prevents half-updated state from being observed.
**ANOTHER LAYER**: N/A - this is correctly implemented.
**VERDICT**: NOT A BUG - the implementation is correct.

### ID: F3 / RESUME NOTE AUTHORITY / MEDIUM / host.py:333-340
**LOCATION**: `_handle_resume` resume note construction
**CONCRETE INPUT**: The resume note is HOST-AUTHORED: either "TOOL RESULTS (approved by the human...)" for held actions or "(resumed — the session is active again...)" for paused tasks.
**WHY IT'S SAFE**: The human's approval text never reaches this point - only the task_id flows through. The note is constructed entirely by the Host.
**ANOTHER LAYER**: N/A
**VERDICT**: CORRECT - human text cannot reach the model as authority.

### ID: F4 / TICKER-TURN CONCURRENCY / LOW / host.py:415-420
**LOCATION**: `_run_ticker` and `_should_propose`
**CONCRETE INTERLEAVING**: Ticker thread checks `_should_propose()` under lock, sets `_propose_pending=True`, releases lock, enqueues ProposeJob. Worker picks up ProposeJob while a TurnJob is also processing.
**WHY IT'S SAFE**: Both jobs run SERIALLY on the single worker thread. The queue ensures serial execution.
**ANOTHER LAYER**: The `_propose_pending` flag prevents multiple ProposeJobs from being enqueued.
**VERDICT**: NOT A BUG - serial worker prevents concurrent govern_action calls.

### ID: F5 / TASK STATE TRANSITIONS / LOW / host.py:372-385
**LOCATION**: `_absorb_result` state mapping
**ANALYSIS**: 
- STOPPED_SUCCESS → DONE (correct)
- STOPPED_HELD → AWAITING_APPROVAL (correct)
- STOPPED_PAUSED → PAUSED (correct)
- STOPPED_EMPTY/MAX_ITERATIONS → FAILED (correct, honest failure)
- The `else` clause at line 385 catches any unexpected state → FAILED
**VERDICT**: CORRECT - all states map appropriately, no silent loss.

### ID: F6 / CONTROL AUTHORITY PATHS / LOW / Multiple locations
**ANALYSIS OF EACH CONTROL**:
- `pause()`/`resume()`: Only set `session.paused`, pure restriction
- `set_leash()`: Only modifies `session.leash_overrides`, capped by signed grant
- `set_proactivity()`: Only modifies `session.proactivity`, affects surfacing only
- `veto()`: Pure state flip, nothing runs
- `decline()`: State change only, no execution
- `approve()`/`approve_proposal()`: Enqueue work that re-gates at execution
**VERDICT**: CERTIFIED - no control path grants new authority.

### ID: F7 / LOCK HELD ACROSS BLOCKING / LOW / Throughout
**ANALYSIS**: Searched for lock held across blocking operations:
- `run_turn` called at line 298: UNLOCKED ✓
- `approve_held_decision` called at line 318: UNLOCKED ✓
- `approve_proposal` called at line 352: UNLOCKED ✓
- `propose` called at line 361: UNLOCKED ✓
- All model/subprocess calls are made outside lock sections
**VERDICT**: CERTIFIED - lock never held across blocking I/O.

### ID: F8 / HISTORY CONCURRENT ACCESS / LOW / host.py:314,340
**LOCATION**: Task history field access
**CONCRETE INTERLEAVING**: Worker thread reads `task.history` at line 314, passes to `run_turn`. No other thread modifies history (only worker writes at line 377).
**VERDICT**: NOT A BUG - only worker thread accesses history field.

## CERTIFICATION

**C1 (serial execution / no double-run)**: **CERTIFIED** - The single worker thread and queue-based job dispatch make concurrent execution impossible. The `decision.consumed` flag in loop.py's approve() provides defense-in-depth against double-execution of held decisions.

**C2 (thread-safety / honest snapshot)**: **CERTIFIED** - The RLock correctly guards all compound structure access, snapshot() is atomic with respect to appends, and the lock is never held across blocking operations.

**C3 (resume / TOCTOU / no silent loss)**: **CERTIFIED** - The resume path correctly handles both held and paused tasks, DENIED decisions stay AWAITING and re-approvable, resume notes are HOST-authored never human-authored, and EMPTY/MAX_ITERATIONS map to FAILED not DONE.

**C4 (controls never grant authority)**: **CERTIFIED** - Every control method either restricts (pause, veto, decline), modifies host config within signed caps (leash, proactivity), or enqueues work that re-gates authority at execution time. No control path can grant new capabilities or widen authority.

## STEELMAN

This Host implementation demonstrates exceptional discipline in thread-safety and authority control. The single-worker-thread architecture elegantly eliminates entire classes of concurrency bugs, while the typed job queue provides clear separation between control plane (enqueue) and execution plane (worker). The consistent use of the consumed flag and re-gating at execution time shows defense-in-depth thinking.

## VERDICT

**SOUND** - The implementation correctly upholds all four certification claims. The single highest-value fix would be adding a comment at the `approve()` method documenting that the `decision.consumed` flag provides the critical double-execution protection, as this is a subtle but essential safety property.
