# Host panel: qwen/qwen3-max

_cost=$0.0945 finish=stop_

## FINDINGS

### F1: **ResumeJob can run concurrently with TurnJob for same task**  
**SEVERITY**: HIGH  
**LOCATION**: `collaborator/host.py:298-305` (`_handle_resume`) and `collaborator/host.py:278-284` (`_handle_turn`)  
**INTERLEAVING**:  
1. Thread A calls `submit("task")` → creates Task(id=T1, state=QUEUED)  
2. Worker processes `_TurnJob(T1)` → sets state=RUNNING (line 281)  
3. Thread B calls `pause()` → sets session.paused=True  
4. `run_turn` returns with STOPPED_PAUSED → `_absorb_result` sets state=PAUSED (line 398)  
5. Thread C calls `resume()` → under lock: sets session.paused=False, finds T1.state==PAUSED, sets T1.state=QUEUED, enqueues `_ResumeJob(T1)`  
6. Worker processes `_ResumeJob(T1)` → in `_handle_resume`: reads task.state (PAUSED→QUEUED transition already happened), sets state=RUNNING (line 299)  
7. Meanwhile, Thread D calls `submit("another task")` which somehow reuses T1 ID (unlikely but possible via UUID collision) OR more realistically: the original task gets re-queued via external mechanism  
**WHY IT BREAKS**: The resume path assumes it exclusively controls task state transitions from PAUSED→RUNNING, but there's no protection against a new TurnJob being created for the same task ID. While UUID collision is improbable, the real issue is that `_handle_resume` doesn't validate that the task is still in a resumable state after acquiring the lock. If between steps 5-6 the task was deleted or reassigned, the resume could operate on stale data.  
**CAUGHT BY OTHER LAYER**: Partially - the task lookup in `_handle_resume` (line 296) would return None if the task was deleted, but if the task exists in an invalid state, it proceeds. The single worker prevents true concurrency but not logical state corruption.  
**FIX**: In `_handle_resume`, after acquiring the lock and getting the task, verify that `task.state in (PAUSED, AWAITING_APPROVAL)` before proceeding. Add explicit state validation.

### F2: **Partial approve leaves task in inconsistent state during concurrent decline**  
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/host.py:307-325` (`_handle_resume` partial deny handling)  
**INTERLEAVING**:  
1. Task T1 has two held actions [A, B]  
2. Thread A calls `approve(T1)` → enqueues `_ResumeJob(T1)`  
3. Worker starts `_handle_resume`: runs approve on [A, B] → A succeeds (RAN), B fails (DENIED)  
4. Worker acquires lock, updates task: decisions=[A,B], approved_ran=[A.summary()], held=[B], then checks `if remaining` → yes  
5. Before setting state=AWAITING_APPROVAL (line 323), Thread B calls `decline(T1)`  
6. `decline` acquires lock, sees task.state==RUNNING (not AWAITING_APPROVAL), returns False  
7. Worker continues, sets state=AWAITING_APPROVAL  
**WHY IT BREAKS**: The decline operation fails because it sees RUNNING state, but the user expects to be able to cancel a task that's partially approved. The task remains in AWAITING_APPROVAL with one action executed and one still held, but the user cannot decline it because the state transition happens atomically only at the end. This creates a confusing UX where decline appears to fail even though the task isn't fully running.  
**CAUGHT BY OTHER LAYER**: No - this is a UX/state machine issue. The single worker prevents data corruption but not logical inconsistencies in the control flow.  
**FIX**: Allow decline to work on tasks in RUNNING state that have held actions. Modify `decline` to check if the task has any held actions regardless of current state, or add a transitional state like PARTIALLY_APPROVED.

### F3: **ProposeJob can run while TurnJob is mid-flight despite guard**  
**SEVERITY**: LOW  
**LOCATION**: `collaborator/host.py:418-420` (`_should_propose`) and ticker logic  
**INTERLEAVING**:  
1. `_worker_busy = False`, jobs queue empty  
2. Ticker thread checks `_should_propose()` → all conditions pass, sets `_propose_pending = True`, enqueues `_ProposeJob()`  
3. Immediately after, main thread submits a new task → enqueues `_TurnJob()`  
4. Worker processes `_ProposeJob()` first → `_worker_busy = True` during execution  
5. While propose is running, `_TurnJob` waits in queue  
**WHY IT BREAKS**: The `_should_propose` check uses `_worker_busy` and `not self._jobs.empty()` as guards, but there's a race condition between the ticker checking these conditions and actually enqueueing the job. If a TurnJob is enqueued immediately after the check but before the ProposeJob is processed, both will run sequentially but the propose happens when a turn is about to start, violating the "idle only" contract.  
**CAUGHT BY OTHER LAYER**: Yes - the worker processes jobs serially, so no actual concurrency occurs. The propose will complete before the turn starts, so governance integrity is maintained. This is more of a policy violation than a security issue.  
**FIX**: Add a stricter check in `_handle_propose` to verify the system is still idle before executing, or use a generation counter to ensure proposals only run in truly idle states.

## CERTIFICATION

**C1 (serial execution / no double-run)**: CERTIFIED  
The single worker thread with job queue ensures all govern_action/run_turn/propose executions are serial. Controls only enqueue jobs, never execute inline. The `consumed` flag in Decision prevents double-execution of held actions.

**C2 (thread-safety / honest snapshot)**: CERTIFIED  
The RLock guards all compound data structure access. Snapshot holds the lock for the entire read, ensuring atomicity. Lock is never held across IO/model calls as verified by UNLOCKED comments and test coverage.

**C3 (resume / TOCTOU / no silent loss)**: NOT-CERTIFIED  
While most cases are handled correctly, the partial deny scenario (F2) creates a state where users cannot decline partially-approved tasks, leading to potential confusion. However, human free-text never reaches the model as authority, and DENIED actions correctly remain retryable.

**C4 (P-01 / controls never grant authority)**: CERTIFIED  
All controls either restrict (pause, veto, decline) or express host config (leash, proactivity). Approve re-gates capability at runtime. No control path widens model capabilities or allows request data to influence authority grants.

## STEELMAN

The Host implementation correctly isolates all governance-critical operations to a single worker thread, preventing concurrency issues that could compromise security guarantees. The lock discipline is sound, with careful attention to not holding locks across blocking operations. The task lifecycle management handles edge cases like partial denials and proper state transitions, while the proposal trigger includes comprehensive guards against running during active tasks.

## VERDICT

**MINOR_ISSUES**  
The implementation is largely sound with strong concurrency controls and proper authority gating. The identified issues are primarily UX/state machine inconsistencies rather than security vulnerabilities. The highest-value fix is **F1**: adding explicit state validation in `_handle_resume` to ensure tasks are in expected states before processing, which would prevent potential logical errors during complex interleavings.
