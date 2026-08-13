# Host panel: x-ai/grok-4.5

_cost=$0.1397 finish=stop_

# Adversarial Review: `collaborator/host.py`

## Findings

### F1 / DOUBLE-APPROVE RACE ENQUEUES TWO RESUME JOBS / MEDIUM  
**Location:** `host.py:268-277` (`approve`), `host.py:218-230` (`resume`), `host.py:323-370` (`_handle_resume`)

**Concrete interleaving:**
1. Task T is `AWAITING_APPROVAL` with held decision D.
2. Thread A: `approve(T)` — under lock sees AWAITING, sets `RUNNING`, releases lock, about to `put(_ResumeJob)`.
3. Thread B: `approve(T)` — under lock sees `RUNNING` → returns False. **OK for double-approve.**
4. Alternate path: Thread A `approve(T)` sets RUNNING and enqueues ResumeJob-1. Worker starts `_handle_resume`, copies `held`, runs `approve_held_decision` → RAN, clears held, calls `run_turn(...)`. Mid-turn the model hits another HELD → `_absorb_result` sets AWAITING again.
5. More interesting race with **decline + approve**:
   - T is AWAITING.
   - Thread A: `approve(T)` sets RUNNING, releases lock, not yet enqueued.
   - Thread B: `decline(T)` — state is RUNNING, not AWAITING → decline returns False. **OK.**
6. **Real bug — approve then worker-partial-deny path is fine; the actual issue is concurrent `resume()` + `approve()` is N/A for same state.**

**Actual double-resume via approve:**
- `approve` only succeeds when `state == AWAITING_APPROVAL`, and **eagerly** sets `RUNNING` before enqueue. Second `approve` fails. Single worker drains jobs serially. `loop.approve`'s `consumed` flag blocks double-exec of the same Decision.
- **When held is empty** (PAUSED path): `resume()` collects PAUSED tasks, sets QUEUED, enqueues ResumeJobs. Two rapid `resume()` calls:
  1. First resume: finds PAUSED, sets QUEUED, enqueues Job1.
  2. Second resume: no PAUSED tasks → nothing. **OK.**
- **Race:** Task is PAUSED. Resume sets QUEUED and will enqueue. Before enqueue, could something else happen? Worker won't pick it until enqueued. When Job runs, held is empty → "(resumed)" note. Only one job. **OK.**

**Re-checked double-approve after DENIED stays AWAITING:**
1. approve → RUNNING → ResumeJob.
2. Worker: approve_held → DENIED, remaining non-empty → state = AWAITING.
3. Second approve → RUNNING → another ResumeJob. `consumed` is False on DENIED → retries. **By design, not double-run.**

**Verdict on F1:** Double-execution of a held action is **blocked** by (a) eager state flip to RUNNING, (b) single worker, (c) `decision.consumed`. **NON-finding for C1 double-run.**

---

### F2 / DECLINE DURING IN-FLIGHT APPROVE LOSES THE RACE CLEANLY — NON-FINDING  
**Location:** `host.py:256-265`, `host.py:323-350`

If decline runs while state is still AWAITING (before approve flips to RUNNING):
1. decline sets CANCELLED.
2. approve sees CANCELLED ≠ AWAITING → False.

If approve flips to RUNNING first, decline fails. Worker still runs held actions. **No cancel-during-approve** — stated non-goal / acceptable. Decline does not dequeue. **Nit at most** (no way to cancel an in-flight approve job).

---

### F3 / HISTORY SHARING BY REFERENCE ACROSS RESUME / LOW (nit)  
**Location:** `host.py:328-329`, `host.py:368`

```python
history = task.history  # reference, not copy
...
result = run_turn(..., history=history)
```

`run_turn` does `history = list(history or [])` (loop.py) — **copies on entry**. So the worker's mutation is on a private list; `_absorb_result` writes `task.history = result.history` under lock. **No cross-thread history mutation.** Snapshot/`view()` does not expose raw history. **NON-finding.**

---

### F4 / PROPOSEJOB CAN BE ENQUEUED WHILE QUEUE BECOMES NON-EMPTY — NON-FINDING FOR DOUBLE GOVERN  
**Location:** `host.py:419-428`, `host.py:430-448`

Ticker under lock: `_should_propose` checks `_worker_busy`, `_jobs.empty()`, no RUNNING/QUEUED, not `_propose_pending`, no PROPOSED proposals.

Race:
1. Ticker: `_should_propose` True → `_propose_pending=True`, enqueue=True, releases lock.
2. Client: `submit()` → adds QUEUED task, `put(TurnJob)`.
3. Ticker: `put(ProposeJob)`.
4. Worker runs ProposeJob first (if ordered that way) **or** TurnJob first.

If ProposeJob runs first: `propose()` → `govern_action` on worker alone; then TurnJob runs. **Still serial on one worker.** Two `govern_action` drivers do **not** run concurrently. Claim C1 is about concurrent execution — **held**.

If TurnJob runs first: during turn `_worker_busy` True; ProposeJob runs after turn completes. `_handle_propose` clears `_propose_pending` at start. Fine.

Stacking: `_propose_pending` + ledger PROPOSED check prevents stacking. **C4 propose trigger fail-closed: holds.**

**Caveat (nit):** ProposeJob can run “while a task is QUEUED” if submit wins the race after the ticker check — idle heuristic is slightly racy, **not** a dual-driver or authority bug.

---

### F5 / `set_leash` CAN LOOSEN HOST OVERRIDE UP TO SIGNED CAP / NOT A BUG (C4)  
**Location:** `host.py:236-240`, `view.py:set_leash`

`set_leash` writes `session.leash_overrides` without applying `leash_cap` at write time. Effective leash at the seam is `apply_cap(override, leash_cap(...))`. Host may set `act_then_report`; signed cap still floors it. Design explicitly allows host to loosen relative to tool default within the cap. **Does not grant capability.** Snapshot shows effective leashes. **NON-finding for C4.**

---

### F6 / APPROVE_PROPOSAL DOES NOT FLIP STATUS BEFORE ENQUEUE — TOCTOU DOUBLE ENQUEUE / LOW  
**Location:** `host.py:279-289`, `host.py:372-379`, `propose.py:approve_proposal`

```python
def approve_proposal(self, proposal_id):
    with self._lock:
        p = self._proposals.get(proposal_id)
        if p is None or p.status != PROPOSED:
            return False
        self._touch()
    self._jobs.put(_ApproveProposalJob(proposal_id))
```

**Interleaving:**
1. Thread A and B both see PROPOSED, both enqueue ApproveProposalJob.
2. Worker job1: `approve_proposal` → runs decision, sets APPROVED, `consumed=True`.
3. Worker job2: `proposal.status != PROPOSED` → returns held decision unchanged; Host still `ledger.record_decision(d)` on the **same** HELD decision again?

```python
d = approve_proposal(self.session, p)  # returns proposal.decision unchanged if not PROPOSED
with self._lock:
    self.ledger.record_decision(d)  # records HELD again!
```

**Breaks:** 
- **Not** double-execution (`consumed` + status check in `approve_proposal`).
- **Does** double-record the same HELD decision in the ledger → inflated `counts["governed"]` / `"held"`, lying dashboard.

**Another layer:** `loop.approve` consumed flag prevents double-run. Ledger integrity is Host responsibility — **not** caught elsewhere.

**Severity:** LOW (honesty/telemetry, not authority).  

**Fix:** Only record if `d.status == RAN` (or `!= HELD` / job-local “did work” flag); and/or mark proposal “pending approve” under lock before enqueue (like task RUNNING).

---

### F7 / LOCK NOT HELD ACROSS `run_turn` / SNAPSHOT ATOMICITY / NON-FINDING (C2 core)  
**Location:** `host.py:190-196`, `host.py:311-316`, `host.py:381-408`

- `snapshot()` holds `_lock` for entire `view.snapshot()` + task views.
- Ledger appends only under `_lock` in `_absorb_result`, `_handle_resume`, `_handle_propose`, `_handle_approve_proposal`.
- `run_turn` / `approve` / `propose` / `client.complete` called **unlocked**.

Multi-pass view reads cannot tear vs appends. No dict iteration on `ProposalPool` under concurrent add — Host uses `ledger.proposals` list + `_proposals` dict mutated only under lock. **C2 lock-across-IO freeze: NON-finding.**

**Minor C2 nit:** `view.snapshot()` counts `"held": sum(1 for d in ds if d.status not in (RAN, FAILED))` lumps DENIED/HELD/NOTIFIED/PAUSED — pre-existing view semantics, not Host-introduced lie about atomicity.

---

### F8 / TASK.HELD READ DURING `task.view()` WHILE WORKER MUTATES — PROTECTED  
**Location:** `host.py:82-93`, `host.py:190-196`

`t.view()` calls `d.summary()` on held decisions; only called from `snapshot`/`get_task` under lock; worker mutates `task.held` under lock. **NON-finding.**

---

### F9 / HUMAN FREE TEXT AS TOOL RESULT ON APPROVE PATH / NON-FINDING (C3)  
**Location:** `host.py:355-361`, `host.py:268-277`

`approve(task_id)` takes no user message. Resume note is host-authored from `decision.summary()` lines. PAUSED resume uses fixed `"(resumed — ...)"`.  
`submit(user_message)` puts text in `_TurnJob.user_message` as a normal user turn — correct. **Human text never injected as authoritative TOOL RESULTS on approve/resume.**

---

### F10 / EMPTY / MAX_ITERATIONS → FAILED / NON-FINDING (C3)  
**Location:** `host.py:396-407`

```python
if result.stopped in STOPPED_SUCCESS:  # only FINAL
    task.state = DONE
elif result.stopped in STOPPED_AWAITING:
    ...
else:
    task.state = FAILED
```

EMPTY/MAX_ITERATIONS ⊂ else → FAILED. Exception → `_fail_active` → FAILED. **Holds.**

---

### F11 / DENIED APPROVE LEAVES AWAITING, NOT DONE / NON-FINDING (C3)  
**Location:** `host.py:340-350`

`remaining` kept for `status != RAN`; if remaining: `AWAITING_APPROVAL` and return **without** `run_turn`. Test covers capability revoke. **Holds.**

**Partial deny:** RAN summaries accumulated in `approved_ran`; remaining held kept; eventual full clear sends combined host note. Correct.

---

### F12 / `loop.approve` NEVER CALLED ON REQUEST THREAD / NON-FINDING (C1)  
Controls only enqueue `_ResumeJob` / `_ApproveProposalJob`. Pure-state controls (`pause`, `veto`, `decline`, `set_leash`, `set_proactivity`) do not execute actions. **Holds.**

---

### F13 / CONCURRENT SUBMIT OF TWO TASKS — SERIAL BUT SHARED SESSION / LOW (stated boundary)  
Two `submit`s → two TurnJobs. Worker runs serially; both use same `session` and ledger. No concurrent `govern_action`. Interleaved **tasks** (task2 runs after task1 completes or awaits) can interleave ledger history — **by design** of one worker one session. Not dual-drive. **Not a C1 break.**

**Gap:** While task1 is AWAITING, task2 can still be submitted and run — host allows parallel task **lifecycle** but serial execution. Could surprise operators; **not** in certification claims as forbidden.

---

### F14 / `approve()` SETS RUNNING BEFORE WORKER COPIES HELD — DECLINE GAP / LOW  
After approve sets RUNNING, decline won't cancel. If process kills worker mid-approve, state stuck RUNNING until restart. Operational nit.

**Worse variant:** `approve` sets RUNNING; worker `_handle_resume` gets task; if `task is None` return — N/A. If held list empty **incorrectly** (bug elsewhere) would resume with "(resumed)" without running approvals. Only if `held` cleared without running — decline doesn't clear held; only sets CANCELLED.  

**Bug:** `decline` sets CANCELLED but **leaves `task.held` populated**. Stale held on CANCELLED task. If something set state back to AWAITING, could re-approve. Nothing does that. **Nit.**

**Real bug — approve after decline is false; but ResumeJob already queued:**

1. State AWAITING.  
2. `approve`: set RUNNING, enqueue ResumeJob.  
3. There is no cancel. Worker runs actions. **OK.**

What if:
1. AWAITING.  
2. Something enqueues ResumeJob via `resume()` — only for PAUSED, not AWAITING. **OK.**

---

### F15 / RACE: `resume()` ON PAUSED WHILE WORKER STILL IN `_absorb_result`? / NON-FINDING  
Pause is lock-free read in `govern_action`. Turn returns STOPPED_PAUSED; `_absorb_result` sets PAUSED under lock. `resume()` reads PAUSED under lock. Ordering is coherent. **OK.**

---

### F16 / HOST `approve` OF TASK DOES NOT PASS THROUGH `reauthorized_or_denied`? / NON-FINDING  
Worker calls `approve_held_decision` = `loop.approve` → re-gates. **OK.**

---

### F17 / CONTROLS GRANT AUTHORITY? / C4  

| Control | Effect | Widens authority? |
|---------|--------|-------------------|
| pause | `session.paused=True` | No (restricts) |
| resume | unpause + ResumeJob | No (continues) |
| set_leash | override via view | Only within signed cap at seam |
| set_proactivity | dial | Surfacing only |
| veto | veto_proposal | Restricts |
| decline | CANCELLED | Restricts |
| approve | enqueue resume + loop.approve | Re-gates |
| approve_proposal | enqueue + approve_proposal | Re-gates |
| submit | user turn | Normal doer path through govern_action |

No control calls `emit`, sets `autonomous=True`, passes `leash=` into `govern_action` from request data, or sets `intent.source`. Propose path uses `leash=PROPOSE_FIRST` default inside `propose()`. **C4 holds.**

`set_leash(..., ACT_THEN_REPORT)` can widen host config vs default `propose_first` for tools — still capped by signed grant; still not a new capability. Claim: *"no control adds a capability; no request data reaches a leash= keyword or intent.source/emit"* — **holds.**

---

### F18 / SNAPSHOT `busy` FLAG vs ACTUAL EXECUTION / LOW nit  
`_worker_busy` True only around `_dispatch`. Between jobs False even if queue non-empty. Snapshot can show `busy=False` with pending work. Display nit, not safety.

---

### F19 / CRITICAL LOOK: `_handle_resume` RECORDS DENIED DECISIONS AND EXTENDS `task.decisions` WITH NEW DECISION OBJECTS / NON-FINDING  
`loop.approve` on DENIED returns a **new** Decision (not mutating to consume). Good. On RAN, consumes original. Host records `ran` list. Good.

---

### F20 / WORKER EXCEPTION DURING APPROVE MID-LIST / MEDIUM  

**Location:** `host.py:333-350`

```python
ran = [approve_held_decision(self.session, d) for d in held]
```

If decision 1 RAN (consumed, side effect done), decision 2 raises inside `approve` (promised not to raise — loop says never raise; execute paths catch) — practically OK.

If `_absorb_result` or ledger throws after partial approve: `except` in `_run_worker` → `_fail_active` sets FAILED. **Already-executed held actions stay executed; task FAILED; remaining held may be lost from retry UX** (state FAILED, not AWAITING).  

**Concrete:** two held; first RAN; `ledger.record_decisions` throws (bug); task → FAILED; second never attempted; first already ran.  

**Severity:** LOW/MEDIUM — depends on ledger reliability. Another layer: physical world already changed; task state dishonest relative to partial progress. **Fix:** mark progress under lock per decision; on failure keep AWAITING with remaining, don't blanket FAILED if held remain.

---

### F21 / `stop()` DOES NOT JOIN TICKER / LOW  
Ticker is daemon; `_stopping` set; may enqueue ProposeJob after stop sentinel drained? Order: `_stopping.set()`, `put(_STOP)`. Ticker may still `put(ProposeJob)` after _STOP is queued → worker already exited → ProposeJob orphaned in queue. No execution. **OK.** Ticker thread not joined — process exit / daemon. Nit.

---

## Real findings summary (guarantee-relevant)

| ID | Title | Sev |
|----|-------|-----|
| F6 | Double `approve_proposal` enqueues twice → duplicate ledger record of HELD (no double-exec) | LOW |
| F4-nit | ProposeJob can slip in after submit races ticker idle check (still serial) | LOW |
| F20 | Exception after partial held-approve → task FAILED may strand remaining held UX | LOW |
| F18 | `busy` false with non-empty queue | LOW |

No CRITICAL/HIGH break of C1–C4 linchpin invariants found.

---

## Certification

**C1 — CERTIFIED.**  
All `run_turn` / `loop.approve` / `approve_proposal` / `propose` / `govern_action` execution is confined to the single worker via queued jobs; controls only enqueue or flip pure state; `decision.consumed` + proposal status block double-exec; no concrete interleaving runs an action on the request thread or twice.

**C2 — CERTIFIED.**  
Ledger appends and `view.snapshot()` + task projections share one `RLock` for the full multi-pass read; lock is not held across model I/O; Host proposal index and ledger lists avoid pool-dict iteration crashes; concurrent snapshot test matches the design. (Nits: `busy` flag, view “held” count taxonomy — not atomicity failures.)

**C3 — CERTIFIED.**  
held→approve→resume runs `loop.approve` once per non-consumed decision on the worker, records results, resumes only with host-authored notes; DENIED leaves AWAITING and retryable; PAUSED resumes without approval path; EMPTY/MAX_ITERATIONS/exception map to FAILED not DONE; human free text does not become TOOL RESULTS on the approve path.

**C4 — CERTIFIED.**  
Controls only restrict or set host config (leash overrides still seam-capped; proactivity is surfacing-only); approve/approve_proposal re-gate at run time; no control adds capabilities, reaches `emit`/`autonomous`, or threads request data into `leash=` / `intent.source`; propose trigger is fail-closed with pending/cooldown/idle gates and cannot stack concurrent proposers on the worker.

---

## STEELMAN

The Host’s linchpin — one worker queue as the only path into the seam — is implemented consistently: eager state transitions (`AWAITING`→`RUNNING` before enqueue), unlocked I/O, and locked compound reads/writes match the threat model. Resume authorship (host notes only), DENIED→stay AWAITING, and FAILED mapping for empty/max-iter show deliberate C3 care, including the partial-deny `approved_ran` accumulation. P-01 is preserved: the Host is a scheduler/recorder, not a second authority path.

---

## VERDICT: **SOUND**

No guarantee-breaking hole in C1–C4; residual issues are ledger double-record on double `approve_proposal` enqueue and minor idle/busy telemetry races.

**Single highest-value fix:** In `approve_proposal` (Host), claim the proposal under the lock before enqueue (e.g. set a non-`PROPOSED` in-flight marker or only enqueue once), and in `_handle_approve_proposal` record to the ledger only when the returned decision is a real run outcome (`RAN`/`FAILED`/`DENIED` from this attempt), not a no-op replay of the original HELD decision.
