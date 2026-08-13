# Host panel: google/gemini-2.5-pro

_cost=$0.1382 finish=length_

Here is a senior application-security and concurrency engineering review and certification of the provided `Collaborator` Host.

***

### ADVERSARIAL REVIEW FINDINGS

#### ID: HOST-01 / Race Condition in `resume()` Can Defeat an Interleaved `pause()`
*   **SEVERITY:** MEDIUM
*   **LOCATION:** `collaborator/host.py:265-272`
*   **CONCRETE INTERLEAVING:**
    1.  A task is in the `PAUSED` state.
    2.  **Thread A** calls `host.resume()`. It acquires `self._lock`.
    3.  Inside the lock, it identifies the `PAUSED` task, adds its ID to the local `resumable` list, and changes the task's state to `QUEUED`. (`host.py:268`)
    4.  **Thread A** releases `self._lock`. (`host.py:270`)
    5.  **CONTEXT SWITCH** to **Thread B** before Thread A enqueues the job.
    6.  **Thread B** calls `host.pause()`. It acquires `self._lock`, sets `self.session.paused = True`, and releases the lock. The system is now logically paused again.
    7.  **CONTEXT SWITCH** back to **Thread A**.
    8.  **Thread A** proceeds to execute the loop at `host.py:272`, calling `self._jobs.put(_ResumeJob(tid))` for the task that is now `QUEUED`.
*   **WHY IT BREAKS A GUARANTEE:** A `_ResumeJob` is enqueued and will be executed by the worker *after* the session has been paused again. This violates the user's expectation that `pause()` immediately halts all new work. While the action won't *run* (see below), the turn loop *does* start, which is contrary to the paused state's intent. The `resume()` operation is not atomic.
*   **WHETHER ANOTHER LAYER CATCHES IT:** **Yes.** The `govern_action` seam provides effective defense-in-depth. When the worker processes the `_ResumeJob`, it will call `run_turn`, which eventually calls `govern_action`. The check for `session.paused` at `governance.py:577` will fire, returning a `PAUSED` decision. The `Host`'s `_absorb_result` logic will then correctly transition the task state back to `PAUSED`. The bug causes a wasted worker cycle and a brief state flap (`PAUSED` → `QUEUED` → `RUNNING` → `PAUSED`), but does not lead to unauthorized execution. This reduces the severity from HIGH to MEDIUM.
*   **FIX:** Hold the lock across the job-enqueueing loop to make the entire resume operation atomic.

    ```python
    # collaborator/host.py:265
    def resume(self) -> None:
        with self._lock:
            self.session.paused = False
            # re-queue any task the pause halted so it continues
            resumable = [t.task_id for t in self._tasks.values() if t.state == PAUSED]
            for tid in resumable:
                self._tasks[tid].state = QUEUED
                self._jobs.put(_ResumeJob(tid)) # Move enqueue under the lock
            self._touch()
        # The loop that was here is now inside the lock context.
    ```

***

### CERTIFICATION OF CLAIMS

*   **C1 (serial execution / no double-run): CERTIFIED.** The single-worker-thread architecture with a job queue is fundamentally sound and correctly implemented. All executing controls (`approve`, `approve_proposal`, `resume`, `submit`) enqueue work rather than executing it inline. State checks under the lock prevent double-enqueue, and the `decision.consumed` flag in `loop.approve` provides an independent backstop against double-execution, which the single worker already prevents. The ticker's `_should_propose` logic correctly checks `_worker_busy` and `_jobs.empty()`, preventing a `ProposeJob` from running concurrently with another job.

*   **C2 (thread-safety / honest snapshot): CERTIFIED.** The `RLock` is correctly used to guard all compound state (`_tasks`, `_proposals`, `ledger`). The `snapshot()` method acquires this lock for its entire multi-pass read, making it atomic with respect to worker appends, which also occur under the same lock. The lock is **never** held across blocking I/O (`run_turn`, `client.complete`, `propose`, etc.), preventing a reader from freezing. This discipline is held consistently across all worker handlers.

*   **C3 (resume / TOCTOU / no silent loss): CERTIFIED.** The `_handle_resume` logic correctly handles partial denials: `approve_held_decision` returning `DENIED` correctly leaves the task in `AWAITING_APPROVAL` and the held action retryable, as proven by `test_partial_deny_keeps_the_denied_held_action_retryable`. The resume note passed to `run_turn` is always host-authored from trusted `Decision` summaries, never from user-submitted text. The `_absorb_result` state mapping correctly translates `STOPPED_EMPTY` and `STOPPED_MAX_ITERATIONS` from the loop into the honest `FAILED` task state, never a success-looking `DONE`.

*   **C4 (P-01 / controls never grant authority): CERTIFIED.** All controls are either restrictive (`pause`, `veto`, `decline`), host-configuration (`set_proactivity`), or trigger a re-gated execution. `set_leash` cannot loosen a leash beyond the signed capability grant because `governance.py:apply_cap` enforces the ceiling. The `approve` and `approve_proposal` paths correctly call `loop.approve`, which uses `reauthorized_or_denied` to re-check capabilities at the moment of use, closing the TOCTOU window. No request-derived data can set `autonomous`, `intent.source`, or bypass the governance seam.

***

### STEELMAN (BEST ARGUMENT FOR THE DESIGN)

The `Collaborator` Host's design is exceptionally strong due to its foundational choice of a single, serial worker thread fed by a job queue. This "linchpin invariant" elegantly sidesteps a vast class of complex concurrency bugs related to state management and execution control, making guarantees like "no double execution" provable by construction rather than by intricate locking. Furthermore, the disciplined use of a single `RLock`—held only for microsecond-scale updates to shared data structures and *never* across blocking I/
