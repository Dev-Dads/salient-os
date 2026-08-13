# Host panel: openai/gpt-5.1

_cost=$0.1053 finish=stop_

ID: F1 / TITLE: Approving a task can enqueue multiple resume jobs and cause surprising re-entry / SEVERITY: LOW  
LOCATION: collaborator/host.py:176-187 (approve), 237-278 (_handle_resume)  
CONCRETE INTERLEAVING OR INPUT:  
- Task T has a held decision and is in `AWAITING_APPROVAL`.  
- User A calls `approve(T)` twice in quick succession on different threads (or via a buggy UI double-click).  
- First call under the lock sees `state == AWAITING_APPROVAL`, sets `state = RUNNING`, enqueues `_ResumeJob(T)`.  
- Second call, still under the lock, also sees `state == AWAITING_APPROVAL` is now false (since first set it to RUNNING), so it returns False and does not enqueue.  
- On the worker: `_handle_resume` copies `held`, runs `approve_held_decision` on the held list once, records decisions, empties `held`, and resumes the task via `run_turn`.  

WHY IT BREAKS A GUARANTEE:  
This interleaving actually demonstrates that `approve` is idempotent and *does not* double-run the action: the second call is rejected. The important point is that the host relies both on the `state` check and on the lower-layer `Decision.consumed` guard in `loop.approve()`. The guarantee under C3 is that "loop.approve's `consumed` flag + the single worker prevent a held decision running twice." In this scenario, even if there were a bug in the host's `state` handling, the decision-layer `consumed` flag would still stop double execution. I could not construct an input where the same held decision is run twice.  

WHETHER ANOTHER LAYER CATCHES IT:  
Yes: `collaborator.loop.approve` marks a held decision `consumed = True` just before calling `execute_and_verify`, and any subsequent approval path that touches the same decision returns it without executing it again. The single worker thread ensures there is no concurrent double-call into `approve` for the same decision.  

FIX:  
No fix required for correctness; at most, you could tighten the host-side guard by also checking that `task.held` is non-empty at `approve` time and maybe storing a monotonic "resume_generation" counter to ignore stale resume jobs, but these are defensive nits, not necessary to uphold the guarantee.


---

ID: F2 / TITLE: Snapshot of tasks and ledger is atomic and thread-safe / SEVERITY: LOW  
LOCATION: collaborator/host.py:112-121 (snapshot), 212-234 (_run_worker and _dispatch), collaborator/view.py:74-96 (JudgmentView.snapshot)  
CONCRETE INTERLEAVING OR INPUT:  
- Worker thread is in `_absorb_result` updating `ledger.decisions` and a `Task`’s state under `self._lock`.  
- Concurrently, a UI thread calls `snapshot()`, enters `with self._lock` after the worker releases it, then calls `self.view.snapshot()` which iterates `ledger.decisions` and `ledger.proposals` and computes counts, then enumerates `self._tasks.values()` to build `tasks`.  
- The ticker thread may also be running `_run_ticker`, but `_should_propose` and any modification of `_propose_pending` or `_tasks` is under `self._lock`.  

WHY IT BREAKS A GUARANTEE:  
It does not. I attempted to construct an interleaving where:  
- `JudgmentView.snapshot` iterates `ledger.proposals` while `_handle_propose` appends to it; or  
- `tasks` dict is mutated while iterating it for `t.view()`.  
In all cases, those sequences are guarded by the *same* `self._lock` in `Collaborator`. The worker holds the lock for the minimum time it updates state and never across any `run_turn` or `client.complete` call (which are explicitly labeled `# UNLOCKED`). I could not create a `RuntimeError` from dict/list mutation during iteration nor an inconsistent snapshot of counts vs decisions.  

WHETHER ANOTHER LAYER CATCHES IT:  
The JudgmentLedger itself is not internally locked, but in this host the only mutating access paths are under `Collaborator._lock`, and all snapshot reads are also taken under that lock. There is no second writer thread.  

FIX:  
No change required. As a minor hardening, you could document that the ledger is single-threaded and only to be mutated under the host’s lock, but that is already how this module uses it.


---

ID: F3 / TITLE: ProposeJob cannot interleave with a TurnJob to drive two governance paths concurrently / SEVERITY: LOW  
LOCATION: collaborator/host.py:286-309 (_run_ticker, _should_propose), 214-234 (_run_worker)  
CONCRETE INTERLEAVING OR INPUT:  
- Session `proactivity="eager"`, `idle_seconds=0`, `propose_cooldown=0`.  
- No tasks yet; `_worker_busy=False`, `jobs.empty() is True`, no `RUNNING/QUEUED` tasks, no pending proposals → `_should_propose()` returns True; ticker sets `_propose_pending = True` under the lock and enqueues `_ProposeJob`.  
- User submits a task concurrently: `submit()` enqueues `_TurnJob` then returns; `_worker` thread processes queue serially (either `_TurnJob` then `_ProposeJob`, or vice versa).  
- Suppose `_TurnJob` is handled first: worker sets `_worker_busy=True` under `_lock`, releases it, runs `run_turn` (long blocking), then `_absorb_result` and clears `_worker_busy=False`. During this window, `_should_propose` will always see `_worker_busy` as True and refuse to enqueue *new* proposals even if the ticker fires. The existing `_ProposeJob` is just in the queue, to be processed after the turn.  

WHY IT BREAKS A GUARANTEE:  
It does not. An idle-triggered `_ProposeJob` can be queued just before or after a `_TurnJob`, but only one job executes at a time on the worker thread. `propose()` runs only from `_handle_propose` on the worker, and `run_turn()` is also only called from `_handle_turn` or `_handle_resume`. There is never a concurrent call to `govern_action` or `execute_and_verify` across these; the "two govern_action drivers" condition cannot arise.  

WHETHER ANOTHER LAYER CATCHES IT:  
Yes: beyond the host’s checks, the single worker thread is the structural enforcement. Even if `_should_propose` were buggy and enqueued multiple `_ProposeJob`s while a turn is running, they'd still execute serially, not concurrently.  

FIX:  
No fix required. The existing test `test_should_not_propose_while_busy_or_running` already pins the `_should_propose` gating at the host level.


---

ID: F4 / TITLE: Task lifecycle correctly distinguishes FAILED from DONE and keeps AWAITING on denied approvals / SEVERITY: LOW  
LOCATION: collaborator/host.py:319-346 (_absorb_result), 248-281 (_handle_resume), tests/test_collaborator_host.py:118-128, 141-173  
CONCRETE INTERLEAVING OR INPUT:  
- Model returns a `TurnResult` with `stopped == STOPPED_EMPTY` or `STOPPED_MAX_ITERATIONS`: `_absorb_result` takes the `else` branch, setting `task.state = FAILED`.  
- For a held turn: `stopped == STOPPED_HELD`, host extracts `HELD` decisions into `task.held` and sets `state = AWAITING_APPROVAL`.  
- Between hold and approve, capabilities are revoked so `approve_held_decision` returns a `DENIED` decision. `_handle_resume` records the `RAN`/`DENIED` decisions, computes `remaining` (held items for which `d.status != RAN`), and if `remaining`, sets `task.state = AWAITING_APPROVAL` and returns *without* resuming the loop.  

WHY IT BREAKS A GUARANTEE:  
It does not. Tests explicitly cover:  
- `test_empty_completion_task_is_FAILED_not_done`: EMPTY goes to FAILED, not DONE.  
- `test_approve_denied_stays_awaiting`: revoking `shell.exec` after HOLD and then approving leaves the task in `AWAITING_APPROVAL` with a `DENIED` decision recorded.  
- `test_partial_deny_keeps_the_denied_held_action_retryable`: partial ran/denied set is handled, with only the denied decision remaining in `held`.  
I could not find a path where a `DENIED` approval is mis-reported as DONE or where EMPTY/MAX_ITERATIONS gets mapped to DONE.  

WHETHER ANOTHER LAYER CATCHES IT:  
Yes: the STOPPED_* enums in `loop.py` are precise, and the host’s mapping uses explicit `in STOPPED_SUCCESS`, `in STOPPED_AWAITING`, else FAILED. Even if the loop gains new "failed" reasons, they will fall into the `else` → FAILED bucket.  

FIX:  
No change required. For robustness, you might consider asserting that new STOPPED_* constants get added to one of the frozensets (as `loop` already does in comments), but behavior is already fail-closed to FAILED.


---

ID: F5 / TITLE: Controls cannot widen authority or bypass capability/leash caps / SEVERITY: LOW  
LOCATION: collaborator/host.py:137-197 (pause, resume, set_leash, set_proactivity, veto, decline, approve, approve_proposal), collaborator/view.py:99-115, collaborator/governance.py:231-334, collaborator/propose.py:175-247  
CONCRETE INTERLEAVING OR INPUT:  
- A caller invokes `set_leash("run_command", ACT_THEN_REPORT)` or `set_proactivity("eager")` or `approve_proposal(pid)` with hostile data coming from a web surface.  
- `set_leash` calls `view_set_leash`, which validates leash ∈ {ACT_THEN_REPORT, PROPOSE_FIRST, NOTIFY_ONLY} and updates `session.leash_overrides`. At execution time, `_resolve_leash` and then `apply_cap(leash, leash_cap(...))` clamp it to the signed cap; model cannot inject the leash because `govern_action` takes it as a keyword-only argument and no control ever forwards untrusted text into that position.  
- `approve_proposal` under the host simply enqueues an `_ApproveProposalJob` which calls `approve_proposal(session, p)`; that calls `loop.approve` on the underlying decision, which re-derives capability from the current session, applies leash caps, checks seals, etc.  
- No control can write to `session.capabilities` except host code; and `approve` / `resume` only ever call into `loop.approve` and `run_turn` on the worker thread.  

WHY IT BREAKS A GUARANTEE:  
It does not. I attempted to find:  
- A path where a control writes `intent.source` to "host" or "direct" to trick the seam; but none of the host methods touch `intent.source` at all.  
- A path where user-provided text reaches a `leash=` argument; but all `govern_action` calls from the host either omit `leash` (for loop-driven tool calls) or supply a constant like `PROPOSE_FIRST` in the propose channel.  
- A path where proactivity or a proposal approval expands capabilities beyond the signed cap; but `granted_capabilities` and `apply_cap` enforce caps at governance time and at execute time.  

WHETHER ANOTHER LAYER CATCHES IT:  
Yes: the seam in `govern_action` / `execute_and_verify` is the single gate, and both the view and the host merely adjust host-owned config that is itself capped by `leash_cap` and the signed cap. `emit()` is the sole entry point that can request autonomous emission, and it is host-only, not reachable from `run_turn` or from any host control in this module.  

FIX:  
None needed in this module. The safety story for P‑01 is primarily in governance.py and session.py, which are already wired correctly here.


---

ID: F6 / TITLE: Worker lock is not held across model calls; readers can’t be frozen / SEVERITY: LOW  
LOCATION: collaborator/host.py:214-281 (_run_worker, _handle_turn, _handle_resume, _handle_approve_proposal, _handle_propose)  
CONCRETE INTERLEAVING OR INPUT:  
- A `Slow` client (see tests) holds `client.complete` for milliseconds or seconds.  
- While a turn is running, a UI thread hammers `snapshot()` in a tight loop.  
- `_run_worker` sets `_worker_busy=True` under the lock, then calls `_dispatch` which in turn calls `run_turn` and `approve_held_decision` and `propose`. All those calls are made *after* releasing `self._lock`; `_lock` is only held around cheap state updates before/after.  
- `snapshot()` acquires `_lock` for each call, but never blocks on the model I/O because `_lock` is not held over `run_turn` / `complete`.  

WHY IT BREAKS A GUARANTEE:  
It does not. The tests explicitly exercise this via `HostConcurrency.test_snapshot_never_crashes_while_a_turn_runs`, and inspection confirms that every `run_turn`, `approve_held_decision`, `approve_proposal`, and `propose` call is tagged `# UNLOCKED` and is indeed outside any `with self._lock` block. I could not construct an interleaving where the lock is held for the duration of I/O, nor any deadlock between ticker and worker, because `_run_ticker` only takes the lock for `_should_propose` and queueing a job (micro-sections).  

WHETHER ANOTHER LAYER CATCHES IT:  
The RLock is only in this module, so the guarantee is enforced entirely here. The queue and single worker design ensure no other path executes `run_turn` off-thread.  

FIX:  
No change required.


---

ID: F7 / TITLE: Task.history and Task.held are never concurrently read/written by two threads / SEVERITY: LOW  
LOCATION: collaborator/host.py:160-170 (get_task & Task.view), 248-281 (_handle_resume), 319-346 (_absorb_result)  
CONCRETE INTERLEAVING OR INPUT:  
- Worker in `_handle_resume` reads `task.history` and `task.held` under `self._lock`, copies `held = list(task.held)`, then releases the lock, operates on the copies, and later reacquires the lock to write back `task.history`, `task.held` and other fields.  
- Concurrently, a UI thread calls `get_task(task_id)` or `snapshot()` which call `Task.view()` inside a `with self._lock` block, iterating over `task.held` and reading `task.history` only while holding the lock.  
- Because `Task.view()` is only ever called with `_lock` held, there's no interleaving where `task.held` or `task.history` is mutated during iteration.  

WHY IT BREAKS A GUARANTEE:  
It does not. There is no unsynchronized access to the compound task fields: every read is guarded by the same `_lock` that guards writes. The only place history is passed to `run_turn` is via a *local* `history` variable copied under the lock; the model never sees a concurrently-mutated history list. I could not find an interleaving that causes data races on task state or history.  

WHETHER ANOTHER LAYER CATCHES IT:  
No other layer is needed; this is purely host-side state. The tests verify that snapshot counts are internally consistent under concurrent reads, which implicitly exercises this as well.  

FIX:  
No fix required.


---

CERTIFICATION LINES

C1 (serial execution / no double-run): CERTIFIED – All calls to `run_turn`, `govern_action`, `execute_and_verify`, `approve_held_decision`, and `propose` are executed only on the single worker thread, via the job queue, and the `Decision.consumed` flag in `loop.approve` prevents any held or proposal decision from being executed twice even under hostile input.  

C2 (thread-safety / honest snapshot): CERTIFIED – `snapshot()` and all reads of `_tasks`, `ledger.decisions/proposals`, and `_proposals` are taken under the same `RLock` that guards all writes; the lock is never held across blocking model calls, so there is no inconsistent view, concurrent-mutation crash, or long freeze for readers.  

C3 (resume / TOCTOU / no silent loss): CERTIFIED – The held→approve→resume path runs each held decision at most once, records every `RAN`/`DENIED` decision in the ledger, leaves tasks in `AWAITING_APPROVAL` on any `DENIED` re-gate, resumes only after all held actions have cleared, and resumes with a host-authored note; `EMPTY` and `MAX_ITERATIONS` are always mapped to `FAILED`, not `DONE`.  

C4 (P‑01 / controls never grant authority): CERTIFIED – All host controls either tighten or express host configuration (pause, leash, proactivity, veto/decline) and never bypass `govern_action` or its capability/leash caps; no control path can set `autonomous`, change `intent.source`, grant new capabilities, or trigger emissions outside the signed, host-only `emit()` entry point.  


STEELMAN

The Host module’s design is cohesive and disciplined: a single worker thread and a tightly-scoped RLock enforce serialization of all governance-related operations and state updates, while leaving model I/O non-blocking for readers. The task lifecycle, approval, and proposal paths have been wired to respect the seam’s guarantees, including re-gating at approval time, capability caps, and honest failure mapping, and the tests directly exercise core interleavings and TOCTOU scenarios. From a concurrency and authority perspective, the module does not introduce any new avenues to bypass existing guards, and it effectively closes the “hand-wired” gaps it set out to solve.

VERDICT: SOUND – The Host as shipped maintains the existing safety and authority guarantees, with no guarantee-breaking bugs found; the single highest-value improvement would be purely hardening/defensive (e.g., adding explicit assertions or small invariants around task states and resume jobs) rather than fixing a concrete vulnerability.
