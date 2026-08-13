# Host panel: openai/gpt-5.1

_cost=$0.0950 finish=stop_

ID: H1 / TITLE: Possible stale task resume after DECLINE or stop() / SEVERITY: LOW  
LOCATION: collaborator/host.py:140-145, 213-234  

CONCRETE INTERLEAVING OR INPUT:  
1. User submits a task; worker runs `_handle_turn`, enters `run_turn`, and eventually returns `STOPPED_HELD`, populating `task.held`.  
2. Before the user inspects and declines the task, the host process calls `stop()` (e.g., application shutdown) while `_run_worker` is blocked inside a model call. `_stopping` is set and `_STOP` is queued, but `_handle_turn` will still complete later (if the worker isn’t joined in time or join_timeout is short).  
3. After restart, an external controller that has persisted task IDs re-attaches to a new Collaborator instance with the same `Session` and could (buggy) call `approve(task_id)` based on old state. The new host instance, having no task with that ID in `_tasks`, will simply ignore it; however, the old worker thread (if still alive due to a race on process teardown or embedding environment) might still complete the prior `_handle_turn` and call `_absorb_result`, changing the state of the now-orphaned task.  

WHY IT BREAKS A GUARANTEE:  
This is a lifecycle nit rather than a correctness break: the design does not persist or transfer task ownership across host instances, and the API makes no promise about task IDs surviving restart. There is, however, a small discrepancy between the narrative (“one presence that owns the loop”) and the fact that stop() does not strictly guarantee that no more `_absorb_result` calls will ever run if the embedding application fails to join or terminates oddly. It does not let any control race an append or cause off‑thread execution; it just leaves a theoretical window where an old worker instance could still mutate an in‑memory task structure that callers no longer consider live.  

WHETHER ANOTHER LAYER CATCHES IT:  
Yes; in practice the Python process teardown and the fact that `_tasks` is private to a single Collaborator instance confine any such mutation to memory that no longer has live references from the embedding surface. There is no cross‑Session or cross‑process sharing, and no path for that stale write to affect authority or egress.  

FIX:  
Clarify in documentation that `Collaborator.stop()` is a best‑effort in‑process shutdown and that tasks are not durable across process boundaries; if you want a hard guarantee that no more state changes will occur, ensure the embedding process joins the worker thread successfully before reusing the `Session` object elsewhere. Optionally, strengthen `stop()` by (a) looping to drain any remaining work items created before `_stopping` was set, or (b) storing an internal generation token and having `_absorb_result` bail out if it’s called after a stop generation change. This would make the “one presence” ownership model more obviously true even under unusual teardown.

---

ID: H2 / TITLE: Private attributes exposed to tests (`h._tasks`, `_proposals`) / SEVERITY: LOW  
LOCATION: tests/test_collaborator_host.py:74, 129; collaborator/host.py:63, 89  

CONCRETE INTERLEAVING OR INPUT:  
The tests directly access `h._tasks[tid].decisions` and `h._proposals` to inspect internal state. A naive future maintainer might mirror this pattern in production integration code, accessing private structures without the lock and risking races (e.g., iterating `h._tasks` while the worker thread updates it).  

WHY IT BREAKS A GUARANTEE:  
As written, the shipped module plus tests do not break the C1–C4 guarantees. The risk is social/maintenance: external callers might incorrectly treat these private structures as part of the supported concurrency contract and read them without taking `_lock`, leading to snapshots that are not necessarily atomic with respect to ledger appends. That would violate the “snapshot is atomic” guarantee, but only because caller code stepped outside the provided API.  

WHETHER ANOTHER LAYER CATCHES IT:  
Yes; the host’s public API (`snapshot()`, `get_task()`) enforces locking, and nothing in the module’s exported surface requires or encourages use of the private attributes. This is therefore a non‑bug from the host’s perspective, but worth flagging as a potential future foot‑gun.  

FIX:  
Document clearly in `Collaborator`’s docstring that `_tasks`, `_proposals`, and `_jobs` are internal implementation details not safe to use concurrently and that callers must use `snapshot()` / `get_task()`. Optionally, rename them to `__tasks` / `__proposals` to make accidental external use even less likely, or add a thin locked accessor for introspection tests.

---

ID: H3 / TITLE: `approve()` sets task state to RUNNING before enqueue, but state is re‑set in worker / SEVERITY: LOW  
LOCATION: collaborator/host.py:197‑206, 229‑246  

CONCRETE INTERLEAVING OR INPUT:  
1. Task T is in `AWAITING_APPROVAL` with one HELD decision.  
2. User calls `approve(T)`. Under `_lock`, host checks state, sets `t.state = RUNNING`, and calls `_touch()`, then enqueues `_ResumeJob(T)`.  
3. A concurrent UI thread calls `snapshot()` between `approve()` returning and the worker picking up the job. It sees the task in state `RUNNING`, but no additional decisions yet.  
4. The worker processes `_ResumeJob`: under the lock it again sets `task.state = RUNNING` and then, if all approvals DENY, sets `task.state = AWAITING_APPROVAL` later.  

WHY IT BREAKS A GUARANTEE:  
This is a tiny semantic quirk, not a correctness break: for a brief period after `approve()` returns but before the worker actually runs the approval(s), a dashboard may optimistically show state `RUNNING` even if, after re‑gating, no action can run and the state snaps back to `AWAITING_APPROVAL`. The C3 contract (“DENIED leaves the task AWAITING and retryable”) is still met; there is just a short optimistic window.  

WHETHER ANOTHER LAYER CATCHES IT:  
Yes. `_handle_resume` re‑gates, records decisions, and explicitly repairs the state to `AWAITING_APPROVAL` if all approvals come back DENIED, so the durable state is correct. The single worker thread plus locking ensure there is no double approval or off‑thread execution.  

FIX:  
If you want stricter semantics, change `approve()` so it does not mutate `task.state` to `RUNNING` on the caller thread. Instead, leave it `AWAITING_APPROVAL` and let `_handle_resume` own the state transition entirely based on the result of `approve_held_decision`. This makes the UI always reflect “awaiting” until actual execution begins, but requires no change to the worker’s logic.

---

ID: H4 / TITLE: `pause()` and `resume()` use a single scalar flag for an entire Session / SEVERITY: LOW  
LOCATION: collaborator/host.py:159‑177; collaborator/governance.py:227‑237  

CONCRETE INTERLEAVING OR INPUT:  
1. Multiple tasks are queued or running concurrently over the same `Session` (which is a documented pattern: `Collaborator` is per‑session, so this is equivalent to sequential tasks, not true concurrency).  
2. The host calls `pause()`, which sets `session.paused = True`. The next governed action in any future `run_turn` call will be given status `PAUSED` and the TurnResult will have `stopped=STOPPED_PAUSED`. `_absorb_result` then sets the corresponding task state to `PAUSED`.  
3. Host `resume()` always scans all tasks and treats any with state `PAUSED` as resumable, enqueuing them. If a caller had conceptually intended to pause a single task rather than the entire Session, this is a mis‑match.  

WHY IT BREAKS A GUARANTEE:  
It doesn’t break any of the stated C1–C4 guarantees; it is a design choice. The contract text is clear that the pause is at the Session/view level (“the session was paused mid‑turn”; “the judgment view’s pause control”), not per‑task. The only risk is a caller misunderstanding and assuming per‑task granularity.  

WHETHER ANOTHER LAYER CATCHES IT:  
Yes; the loop’s `PAUSED` status is only ever derived from `session.paused`, and the tests assert the behavior for pause/resume across a single task. No other layer assumes per‑task pausing.  

FIX:  
No code change required for correctness. You might expand the docstring on `pause()` and `resume()` to explicitly state that the pause is session‑global and will eventually affect all tasks sharing this `Session`, so callers who want per‑task pausing should model that externally rather than reuse a single Session.

---

ID: H5 / TITLE: `snapshot()` returns `self._worker_busy` state that may lag queued jobs / SEVERITY: LOW  
LOCATION: collaborator/host.py:119‑126, 186‑201  

CONCRETE INTERLEAVING OR INPUT:  
1. The worker is idle (`_worker_busy = False`), and `_jobs` is empty.  
2. A caller submits a new task or an approval; `submit()` / `approve()` enqueue a job after updating `_tasks`, but do not update `_worker_busy`.  
3. A concurrent `snapshot()` call happens after the enqueue but before the worker thread picks up the job and sets `_worker_busy = True`.  
4. `snapshot()` shows `busy = False` even though there is work queued.  

WHY IT BREAKS A GUARANTEE:  
C2’s “honest snapshot” guarantee is about internal consistency of multi‑pass ledger reads and avoiding crashes under mutation, not about providing a strict queued‑vs‑busy state machine. Here, `busy` is defined as “worker currently executing a job,” not “there is work either running or queued”, so this does not violate the stated guarantee. The only possible confusion is a caller mis‑interpreting `busy` as “no work pending at all.”  

WHETHER ANOTHER LAYER CATCHES IT:  
Yes; `_worker_busy` is consistently set under the lock at the point of job execution and cleared afterwards, and `_should_propose()` uses both `_worker_busy` and `_jobs.empty()` under the same lock for its decisions. There is no safety impact.  

FIX:  
Document that `busy` means “currently executing a job on the worker thread,” not “no jobs queued.” If needed, add another snapshot field (e.g., `queued_jobs`) computed as `not self._jobs.empty()` under the lock to support UIs that want to differentiate queued vs. executing work.

---

CERTIFICATION OF CLAIMS
------------------------

C1 (serial execution / no double‑run): CERTIFIED  
All paths that touch `run_turn`, `approve_held_decision`, `propose`, or `approve_proposal` run only on the single worker thread via the `_jobs` queue. Controls (`submit`, `approve`, `approve_proposal`, `resume`) only enqueue jobs and never invoke these functions inline. The worker loop is strictly serial, and `loop.approve`’s `consumed` flag plus the proposal veto path guarantee a held decision or proposal cannot be run twice. No interleaving exists that runs a governed action off the worker thread or double‑executes it.

C2 (thread‑safety / honest snapshot): CERTIFIED  
All ledger writes and task/proposal mutations happen under `self._lock`; `snapshot()` and `get_task()` also take the same lock and do not call out into model clients or `run_turn`, so they neither see partial updates nor hold the lock across I/O. The ticker only computes `_should_propose()` and `_propose_pending` under the lock, then enqueues `_ProposeJob` for the worker, ensuring that no propose/turn interleave on the authority path. The tests explicitly hammer `snapshot()` from multiple threads while a turn runs, and no inconsistent counts or crashes were found.

C3 (resume / TOCTOU / no silent loss): CERTIFIED  
The held→approve→resume path takes each held Decision, runs it exactly once via `loop.approve` on the worker, records the resulting Decisions into the ledger, and (only if at least one RAN) resumes the task with a host‑authored note summarizing tool results; the human’s free text never flows as an authoritative tool result. If all approvals come back DENIED, `_handle_resume` restores `task.held` and `task.state = AWAITING_APPROVAL`, leaving the task re‑approvable and not mis‑marked as DONE. PAUSED tasks resume via the separate `(resumed…)` note path, and both EMPTY and MAX_ITERATIONS correctly map to `FAILED`, never `DONE`.

C4 (controls never grant new authority): CERTIFIED  
Host controls (`pause`, `resume`, `set_leash`, `set_proactivity`, `veto`, `approve`, `approve_proposal`, `decline`) only mutate Session steering state or enqueue worker jobs; none sets capabilities, `autonomous` flags, `intent.source`, or any emission body. Leash changes are bounded by the signed `leash_cap` in the seam; proactivity only affects whether proposals are surfaced (never run). `approve` and `approve_proposal` both ultimately go through `govern_action`/`approve()` in the seam, which re‑gates capability, leash caps, and TOCTOU conditions before execution. No request‑derived data is ever threaded into a `leash=` keyword or used to mark intents as `source="host"`.

---

STEELMAN
--------

The Host module takes a deliberately conservative concurrency stance: a single worker thread owns all execution, with a simple job queue and a single RLock guarding all compound shared state. This architecture sacrifices some parallelism but sharply limits the surface for races and authority confusion, and the tests explicitly exercise concurrent `snapshot()` during long turns. The resume/approval semantics lean heavily on the already‑audited seam (`govern_action`/`approve`), so the Host itself introduces no new authority paths, only orchestration.

VERDICT: SOUND  
The implementation upholds claims C1–C4 under realistic interleavings; no guarantee‑breaking races, double‑execution paths, or authority‑widening controls were found. The single highest‑value fix is non‑functional: clarify in the Host’s documentation that `busy` and `pause` are session‑global, and that callers must not touch private attributes (`_tasks`, `_proposals`) directly, so future integrators don’t accidentally bypass the carefully enforced locking model.
