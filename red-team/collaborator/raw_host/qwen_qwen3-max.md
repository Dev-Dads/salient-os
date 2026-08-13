# Host panel: qwen/qwen3-max

_cost=$0.0971 finish=stop_

## FINDINGS

### F1 / DOUBLE-EXECUTION OF HELD DECISIONS VIA RACE BETWEEN approve() AND decline()
**SEVERITY**: CRITICAL  
**LOCATION**: collaborator/host.py:260-275 (`approve` method) and lines 280-290 (`decline` method)

**CONCRETE INTERLEAVING**: 
1. User submits task that produces a HELD decision → task.state = AWAITING_APPROVAL
2. Thread A calls `approve(task_id)` → acquires lock, verifies state==AWAITING, sets state=RUNNING, releases lock, enqueues _ResumeJob
3. Thread B calls `decline(task_id)` → acquires lock AFTER Thread A released it but BEFORE worker processes _ResumeJob → sees state=RUNNING (not AWAITING) → returns False (correctly)
4. Worker processes _ResumeJob → runs held decisions via `approve_held_decision` → marks decisions.consumed=True
5. BUT: If Thread B called `decline()` *between* Thread A's state change and the worker's consumption, no issue. However, consider this variant:
   - Thread A calls `approve()` → sets state=RUNNING, enqueues job
   - Worker starts processing _ResumeJob, gets `held = list(task.held)` 
   - Thread B calls `decline()` → sees state=RUNNING (not AWAITING) → correctly fails
   - Worker continues, runs approvals → OK

Wait, actually the real race is elsewhere. Let me reconsider...

Actually, the critical race is in `_handle_resume` itself when all approvals are DENIED:

**REAL CONCRETE INTERLEAVING**:
1. Task has held decisions, state=AWAITING_APPROVAL
2. `approve()` called → state set to RUNNING, _ResumeJob enqueued
3. Worker processes _ResumeJob:
   - Takes lock, copies `held = list(task.held)`, sets state=RUNNING
   - Releases lock
   - Runs `approve_held_decision` on each → all return DENIED (e.g., capability revoked)
   - Takes lock again, tries to restore: `task.held = held; task.state = AWAITING_APPROVAL`
4. **MEANWHILE**: Another `approve()` call happens after the first approve() set state=RUNNING but before the worker restores AWAITING_APPROVAL
   - Second `approve()` sees state=RUNNING → returns False (correctly blocked)

Hmm, this seems safe. Let me check the consumed flag...

The real issue is: **What prevents double-execution if approve() is called twice rapidly?**

Looking at `approve()` method (lines 260-275):
- It checks `if t.state != AWAITING_APPROVAL: return False`
- Sets `t.state = RUNNING`
- Enqueues _ResumeJob

In `_handle_resume`:
- If held decisions exist, runs `approve_held_decision` on each
- `approve_held_decision` checks `decision.consumed` flag and won't re-run

So the consumed flag in the Decision object itself prevents double-execution, not just the state machine.

But what about the window between `approve()` setting state=RUNNING and the worker actually consuming the decision?

Actually, this appears to be handled correctly. The state machine prevents multiple approve() calls from queuing multiple jobs, and the consumed flag prevents actual double-execution.

Let me look for other races...

### F2 / TASK HISTORY RACE CONDITION DURING RESUME
**SEVERITY**: HIGH  
**LOCATION**: collaborator/host.py:320-325 (`_handle_resume` method)

**CONCRETE INTERLEAVING**:
1. Task is running, has history = [msg1, msg2, msg3]
2. Task hits a HELD decision → `_absorb_result` sets task.history = [msg1, msg2, msg3, assistant_msg, tool_results]
3. User calls `approve()` → enqueues _ResumeJob
4. Worker in `_handle_resume`:
   - Acquires lock, reads `history = task.history` (line 322)
   - Releases lock
   - Processes held decisions (may take time due to I/O)
5. **MEANWHILE**: Another turn completes and `_absorb_result` updates `task.history` 
6. Worker continues with stale `history` from step 4, calls `run_turn(..., history=history)`
7. The resumed turn uses outdated history, potentially missing recent decisions

**WHY IT BREAKS A GUARANTEE**: Breaks C2 (honest snapshot) and C3 (resume integrity). The resume operation must use the current, complete history to maintain conversation coherence. Using stale history could cause the model to repeat actions or lose context.

**WHETHER ANOTHER LAYER CATCHES IT**: No. The loop's `run_turn` trusts the passed history parameter completely. There's no validation that the history matches the current ledger state.

**FIX**: Move the history read inside the same critical section as the held decisions processing, or better yet, don't pass history explicitly to `run_turn` during resume—let it reconstruct from the ledger.

Actually, looking more carefully: `_absorb_result` is only called by the worker thread, so step 5 above cannot happen! The worker is single-threaded, so while `_handle_resume` is running, no other job can modify the task's history.

This race doesn't exist due to the single worker thread guarantee.

### F3 / PROPOSAL TRIGGER CAN RUN propose() CONCURRENTLY WITH OTHER OPERATIONS
**SEVERITY**: MEDIUM  
**LOCATION**: collaborator/host.py:420-430 (`_handle_propose` method) and lines 385-395 (`_should_propose`)

**CONCRETE INTERLEAVING**:
1. Ticker thread checks `_should_propose()` under lock:
   - Sees no running/queued tasks
   - Sees no pending proposals  
   - Sets `_propose_pending = True`, releases lock
   - Enqueues _ProposeJob
2. **MEANWHILE**: User submits a new task via `submit()`:
   - Creates task, adds to `_tasks` with state=QUEUED
3. Worker processes _ProposeJob:
   - Reads `self.ledger.decisions[-8:]` to build recent actions
   - Calls `propose()` (which may take significant time)
4. During `propose()` execution, the submitted task starts running via _TurnJob
5. The proposal is generated based on stale ledger state that doesn't include the newly running task

**WHY IT BREAKS A GUARANTEE**: Breaks C2 (honest snapshot consistency). The proposal context should reflect the true current state, but it's built from a snapshot taken before the concurrent task submission.

**WHETHER ANOTHER LAYER CATCHES IT**: Partially. The proposal itself is harmless (it's just a suggestion), but it could propose redundant or conflicting actions. The governance layer will still enforce capabilities, so no security breach.

**FIX**: The ticker should hold the lock longer, or the proposal generation should be more defensive about concurrent changes.

But wait—the design explicitly allows this! The comment says: "The scalar steering flags (session.paused / session.proactivity) are read lock-free inside govern_action — that live read IS the pause/steer feature." And proposals are meant to be best-effort suggestions.

This might be acceptable by design.

### F4 / MISSING VALIDATION IN approve_proposal JOB HANDLING
**SEVERITY**: LOW  
**LOCATION**: collaborator/host.py:345-355 (`_handle_approve_proposal` method)

**CONCRETE INPUT**: Call `approve_proposal()` with a proposal_id that was vetoed between the initial check and job execution.

**INTERLEAVING**:
1. User calls `approve_proposal(pid)` → acquires lock, verifies p.status == PROPOSED, releases lock, enqueues job
2. Another thread calls `veto(pid)` → acquires lock, sets p.status = VETOED, marks p.decision.consumed = True
3. Worker processes _ApproveProposalJob:
   - Acquires lock, checks p.status == PROPOSED → now VETOED → returns early (line 348)
   - **BUT**: This is correct behavior! It returns early and doesn't run anything.

Actually, this is handled correctly. The job handler re-checks the status under lock before proceeding.

After careful analysis of all code paths, I cannot find any actual concurrency bugs that break the core guarantees. The design appears sound:

- Single worker thread ensures serial execution of all governance operations
- RLock properly protects compound data structures
- Consumed flags prevent double-execution at the Decision level
- State machine prevents invalid transitions
- All I/O operations happen outside locks

The tests also validate the key scenarios including TOCTOU (test_approve_denied_stays_awaiting) and concurrency (HostConcurrency test).

## CERTIFICATION

**C1 (serial execution / no double-run)**: CERTIFIED. The single worker thread fed by a job queue ensures all governance operations run serially. The Decision.consumed flag provides defense-in-depth against double-execution even if the state machine were bypassed.

**C2 (thread-safety / honest snapshot)**: CERTIFIED. The RLock guards all compound data structure access, and snapshots are atomic with respect to worker appends. Locks are never held across I/O operations, preventing reader freezes.

**C3 (resume / TOCTOU / no silent loss)**: CERTIFIED. Held→approve→resume correctly runs actions once, records them, and uses host-authored notes. DENIED approvals leave tasks AWAITING. EMPTY/MAX_ITERATIONS correctly map to FAILED state.

**C4 (P-01 / controls never grant authority)**: CERTIFIED. All controls either restrict (pause, veto, decline) or express host configuration (leash, proactivity). Authority is always re-gated through govern_action/approve at runtime.

## STEELMAN

The Host module demonstrates exceptional attention to concurrency safety and authority boundaries. By confining all governance operations to a single worker thread and using job queues for coordination, it eliminates entire classes of race conditions that commonly plague multi-threaded systems. The defense-in-depth approach—combining state machine validation with Decision-level consumed flags—ensures robust protection against double-execution even under adversarial timing.

## VERDICT

**SOUND**. The implementation correctly enforces all certification claims through careful thread discipline, proper locking strategies, and layered validation. The single highest-value aspect is the job queue architecture that serializes all governance operations, which eliminates the most dangerous concurrency vectors while maintaining responsive control plane operations.
