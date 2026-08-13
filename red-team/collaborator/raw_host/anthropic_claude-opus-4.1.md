# Host panel: anthropic/claude-opus-4.1

_cost=$1.0196 finish=stop_

I'll perform a thorough adversarial review of this Host implementation, focusing on the threading and authority guarantees.

## FINDINGS

### ID: C1-001
**TITLE**: ProposeJob Can Execute Concurrently with TurnJob  
**SEVERITY**: HIGH  
**LOCATION**: host.py:395-402 (_run_ticker) and host.py:257-260 (_handle_turn)  
**CONCRETE INTERLEAVING**:
1. Worker thread starts executing `_handle_turn` for a TurnJob
2. Worker reaches line 260: `run_turn(self.session, self.doer_client, job.user_message)` - UNLOCKED execution
3. While `run_turn` is executing (can take seconds), ticker thread runs `_should_propose`
4. Ticker sees `self._worker_busy = True` but this doesn't prevent enqueueing
5. Ticker enqueues ProposeJob at line 402
6. If the TurnJob completes quickly, worker picks up ProposeJob and runs `_handle_propose`
7. Line 339: `propose(self.session, self.proposer_client, ctx)` executes
8. **RESULT**: Two govern_action drivers running concurrently (run_turn driving govern_action for the turn, propose driving govern_action for proposals)

**WHY IT BREAKS**: Violates C1 - two concurrent paths through govern_action could race on session state mutations
**ANOTHER LAYER CATCHES IT**: NO - the queue serializes job START but not job EXECUTION. Once a job releases the lock to call run_turn/propose, another job can start.
**FIX**: The worker_busy flag should prevent enqueueing new jobs while one is executing, not just mark busy status.

### ID: C2-001  
**TITLE**: Task History Read/Write Race
**SEVERITY**: MEDIUM
**LOCATION**: host.py:268 (_handle_resume) and host.py:355 (_absorb_result)
**CONCRETE INTERLEAVING**:
1. Thread A (worker) is in `_handle_resume`, reads `task.history` at line 268 WITHOUT lock
2. Thread B (a hypothetical second worker or direct caller) simultaneously writes `task.history` at line 355 in `_absorb_result`
3. **RESULT**: Thread A passes stale/partial history to run_turn

**WHY IT BREAKS**: Violates C2 - task.history can be read by one thread while written by another
**ANOTHER LAYER CATCHES IT**: PARTIALLY - single worker prevents this specific race, but the read at line 268 is outside the lock
**FIX**: Read task.history under the lock

### ID: C2-002
**TITLE**: Snapshot Can See Partial Ledger State
**SEVERITY**: LOW  
**LOCATION**: host.py:177-183 (snapshot) and host.py:323 (_handle_approve_proposal)
**CONCRETE INTERLEAVING**:
1. Worker thread is in `_handle_approve_proposal`, executes line 321: `d = approve_proposal(self.session, p)` UNLOCKED
2. Then enters lock at line 322 to call `self.ledger.record_decision(d)`
3. Between these two lines, reader thread calls `snapshot()` at line 180
4. **RESULT**: Snapshot sees the proposal still as PROPOSED (not yet APPROVED) but the action may have already executed

**WHY IT BREAKS**: Violates C2 - snapshot not atomic with respect to the actual execution
**ANOTHER LAYER CATCHES IT**: NO - the gap between execution and recording is unprotected
**FIX**: Record the decision atomically with status change

### ID: C3-001
**TITLE**: Double Resume Possible Through Race
**SEVERITY**: MEDIUM
**LOCATION**: host.py:198-206 (resume) and host.py:264-275 (_handle_resume)  
**CONCRETE INTERLEAVING**:
1. Task is PAUSED
2. Thread A calls `resume()`, sets task state to QUEUED (line 202), enqueues ResumeJob
3. Before worker processes the ResumeJob, Thread B calls `resume()` again
4. Thread B sees task as QUEUED (not PAUSED), but line 201 check already passed for Thread A's resumable list
5. Thread B enqueues ANOTHER ResumeJob for the same task
6. **RESULT**: Two ResumeJobs for same task, potentially double-executing held decisions

**WHY IT BREAKS**: Violates C3 - task can be resumed twice
**ANOTHER LAYER CATCHES IT**: PARTIALLY - `loop.approve`'s consumed flag prevents double-execution of held decisions, but the task still processes twice
**FIX**: Mark task with a "resume_pending" flag under lock

### ID: C4-001
**TITLE**: No Authority Granted But Leash Validation Gap
**SEVERITY**: LOW
**LOCATION**: host.py:208-212 (set_leash)
**CONCRETE INTERLEAVING**: 
The set_leash validates the leash value but there's a TOCTOU between validation and the actual use in govern_action. However, this doesn't grant authority because:
1. Invalid leash in govern_action fails closed to PROPOSE_FIRST
2. The capability gate is unchanged
3. No path sets autonomous/intent.source

**WHY IT BREAKS**: Doesn't break C4 - just a validation inconsistency
**ANOTHER LAYER CATCHES IT**: YES - govern_action fails closed on invalid leash
**FIX**: None required for security, though consistency would be better

## CERTIFICATION

**C1 (serial execution / no double-run)**: NOT CERTIFIED - ProposeJob can run propose() while TurnJob is mid-flight in run_turn, creating two concurrent govern_action drivers.

**C2 (thread-safety / honest snapshot)**: NOT CERTIFIED - Task history can be read outside lock during resume; snapshot can observe partial state between action execution and recording.

**C3 (resume / TOCTOU / no silent loss)**: CERTIFIED with caveat - The consumed flag prevents double-execution of held decisions, but the resume path can enqueue duplicate ResumeJobs causing unnecessary reprocessing.

**C4 (controls never grant authority)**: CERTIFIED - No control path grants capabilities, sets autonomous, modifies intent.source, or triggers unauthorized emissions. All controls either restrict or express host config.

## STEELMAN

The Host achieves its core goal of assembling the Collaborator components into a single governed worker with reasonable thread safety. The single worker thread for all job execution prevents most concurrency issues, and the consumed flag in Decision objects prevents the most dangerous double-execution scenarios. The lock discipline around compound structures is generally sound, with only specific gaps rather than systemic flaws.

## VERDICT

**MINOR_ISSUES** - The Host has real concurrency bugs but they're bounded in scope and don't compromise the fundamental authority model. The single highest-value fix is preventing ProposeJob enqueueing while a TurnJob is executing, which would close the concurrent govern_action driver issue.
