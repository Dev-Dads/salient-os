# Host panel: x-ai/grok-4.5

_cost=$0.1408 finish=stop_

# Adversarial Review: `collaborator/host.py`

## Findings

### F1 / Double-approve races a second `_ResumeJob` that can re-enter `run_turn` with a stale history / empty held set
**SEVERITY:** MEDIUM  
**LOCATION:** `collaborator/host.py:274-284` (`approve`), `collaborator/host.py:218-230` (`resume`), `collaborator/host.py:336-377` (`_handle_resume`)

**CONCRETE INTERLEAVING:**
1. Task T is `AWAITING_APPROVAL` with `held=[D]`.
2. Thread A: `approve(T)` → under lock sets `state=RUNNING`, enqueues `_ResumeJob(T)`.
3. Worker starts `_handle_resume`: copies `held=[D]`, clears path, runs `approve_held_decision(D)` → `RAN`, records, then blocks in `run_turn(..., history=H1)`.
4. Meanwhile T is mid-resume (`RUNNING`). A second caller cannot `approve` (state≠AWAITING) — OK.
5. **Worse path:** T is `AWAITING`. Thread A `approve(T)` → RUNNING + job1. Before worker runs, something fails? Actually the double-approve on AWAITING is blocked after first approve flips state.

**Real double-enqueue path via `resume` + `approve` confusion is weaker.** Stronger bug:

**CONCRETE (decline vs approve race):**
1. T is `AWAITING_APPROVAL`.
2. Thread A: `approve(T)` passes check, sets `RUNNING`, **releases lock**, not yet `put`.
3. Thread B: `decline(T)` — state is already `RUNNING`, so decline returns False. OK.

**CONCRETE (double approve before state flip visible):** Both need the lock for the state check; second loses. Single-worker + state gate holds for double approve.

**Revised real issue — concurrent `resume()` while task is `PAUSED`, plus duplicate `_ResumeJob`:**
1. T is `PAUSED` (no held).
2. `resume()` collects T, sets `QUEUED`, enqueues `_ResumeJob`.
3. Worker runs resume with `held=[]`, note="(resumed…)", enters `run_turn`.
4. If pause happens again mid-turn → PAUSED; resume again — fine serially.

**Actual MEDIUM — `approve` does not claim/clear `held` under the same critical section that enqueues, and `_handle_resume` always re-runs `run_turn` even when approvals partially apply:**

If `held = [D1, D2]` and D1→RAN, D2→DENIED:
- `ran_ok = [D1]` (non-empty) → does **not** restore held
- `task.held = []` (D2 dropped from held!)
- resumes with only D1's summary
- D2 is recorded as DENIED in ledger/decisions but **silently dropped from `task.held`** and never retryable via the task approval UX

**WHY IT BREAKS:** C3 claims DENIED stays retryable / AWAITING. That is only true when **all** approvals DENY. Partial deny loses the denied held decision from `task.held` while still continuing the turn — outcome mis-statement + silent loss of a held action.

**OTHER LAYER:** `loop.approve` correctly doesn't consume on DENIED; the Host drops the reference. Seam does not restore Host task.held.

**FIX:** Keep per-decision accounting:
```python
still_held = [d for d in held if corresponding result is DENIED/still HELD]
task.held = still_held
if still_held and not ran_ok:
    task.state = AWAITING_APPROVAL; return
if still_held and ran_ok:
    # either stay AWAITING after recording ran_ok, or resume only after all clear — policy choice
```
Prefer: if any still held/denied-retryable, stay AWAITING after recording RAN ones; only resume when `held` is fully cleared.

---

### F2 / `approve()` / `resume()` can enqueue work for a task that `decline` cannot cancel once RUNNING; more importantly **double `_ResumeJob` via approve while already queued**
**SEVERITY:** LOW (nit / residual)  
**LOCATION:** `host.py:274-284`

After `approve` sets RUNNING and enqueues, a second `approve` fails. Good.

But **`submit` does not prevent overlapping tasks on the same session**: two `submit()`s enqueue two `_TurnJob`s. Worker runs them **serially**, not concurrently — C1 serial execution holds. However both share `self.session` (paused, leashes, capabilities). That is by design (one session). **NON-finding for double-exec of one action.** Two turns can still both run shell commands one after another — not a Host invariant break.

**Blocked attack (C1):** Controls never call `loop.approve` / `run_turn` inline. Verified: `approve` → queue; `approve_proposal` → queue; ticker → queue only.

---

### F3 / `_should_propose` uses `not self._jobs.empty()` — unreliable; ProposeJob can be enqueued while a TurnJob is mid-flight? 
**SEVERITY:** LOW (mitigated)  
**LOCATION:** `host.py:419-424`, `host.py:428-447`

**Analysis:**
- `_worker_busy` is True for the whole `_dispatch`, including `run_turn`.
- Ticker checks `_worker_busy or not self._jobs.empty()` under lock.
- Race: Worker finishes job, sets `_worker_busy=False` in `finally`, **before** taking next job. Between `finally` and next `queue.get`, ticker may see busy=False and empty queue and enqueue ProposeJob. That's correct idle.
- Race: Worker about to set busy=True after get — ticker could enqueue Propose between jobs. Serial worker still runs Propose then Turn one-at-a-time. **Two `govern_action` drivers at once? NO** — single worker.

`queue.empty()` is unreliable in general but here only avoids unnecessary Propose enqueue; worst case extra ProposeJob runs when not idle-ideal. `_propose_pending` + cooldown + "no PROPOSED" limit stacking.

**C2 claim "ticker cannot run propose while TurnJob mid-flight" as concurrent drivers:** CERTIFIED blocked by single worker. Propose while another task QUEUED is blocked by state check.

**NON-finding** for dual govern drivers.

---

### F4 / Lock never held across `run_turn` / `approve` / `propose`
**SEVERITY:** n/a (positive)  
**LOCATION:** `host.py:320-333`, `336-377`, `379-386`, `388-398`

Verified: lock only around state prep and absorb/record. `snapshot()` holds lock only for view+task projection — not across IO.

**Blocked attack:** reader frozen for minutes — **NON-finding**.

---

### F5 / `snapshot()` atomicity vs ledger appends
**SEVERITY:** n/a (positive) / minor nit  
**LOCATION:** `host.py:196-202`, `view.py: snapshot`, ledger appends under same `self._lock` in Host

All Host ledger writes go through worker paths that take `self._lock`. `JudgmentView.snapshot()` multi-pass over `ledger.decisions` / `proposals` runs entirely inside Host.snapshot's lock. 

**Is every append under the same lock?** Host-mediated ones yes. If external code called `ledger.record_*` without the lock — out of contract. Within Host: yes.

**Display reads `ledger.proposals` not ProposalPool** — good; pool dict iteration crash avoided.

**Nit:** `counts["held"]` uses `d.status not in (RAN, FAILED)` which counts DENIED/HELD/NOTIFIED/PAUSED as "held" — display imprecision, pre-existing view semantics, not Host-introduced guarantee break.

**C2:** largely holds.

---

### F6 / Task.history read/write by two threads?
**SEVERITY:** LOW (blocked in practice)  
**LOCATION:** `host.py:336-344`, `400-418`

`history` is written in `_absorb_result` under lock; read in `_handle_resume` under lock into local `history` then passed to `run_turn` unlocked (local list reference).

**Mutation during run_turn:** `run_turn` mutates the list it was given. Only worker calls `run_turn` with that list. No other thread writes `task.history` until absorb replaces it under lock.

**Reader:** `task.view()` does not expose raw history. Good.

**Blocked:** concurrent history RW — **NON-finding**.

---

### F7 / DENIED-all path restores held but recorded DENIED decisions accumulate; re-approve calls `approve` on same Decision objects
**SEVERITY:** LOW (works due to seam)  
**LOCATION:** `host.py:350-361`, `loop.approve`

On all-DENIED: `task.held = held` (original HELD decisions). `loop.approve` did not set `consumed`. Re-approve works. Test covers this.

**Partial DENIED:** F1 above — real bug.

---

### F8 / Human free-text as authoritative tool result?
**SEVERITY:** n/a (positive)  
**LOCATION:** `host.py:362-372`, `submit` vs resume notes

- Approve resume note is HOST-authored from `d.summary()` for RAN only.
- Pause resume note is fixed host string.
- `submit(user_message)` passes user text as a normal user message (not labeled tool-result authority beyond normal turn). C3 asks specifically about approve/resume path — human text does **not** become the post-approve TOOL RESULTS note.

**Blocked attack.** NON-finding.

---

### F9 / EMPTY / MAX_ITERATIONS → DONE?
**SEVERITY:** n/a (positive)  
**LOCATION:** `host.py:400-418`, `loop.py` STOPPED_* 

```python
if result.stopped in STOPPED_SUCCESS:  # only FINAL
    DONE
elif in STOPPED_AWAITING: ...
else:
    FAILED
```
EMPTY/MAX_ITERATIONS → FAILED. Exception → `_fail_active` → FAILED. Test pins empty→FAILED.

**Blocked.** NON-finding.

---

### F10 / `loop.approve` twice for one decision (double run)?
**SEVERITY:** n/a (blocked)  
**LOCATION:** single worker serializes `_handle_resume`; `decision.consumed` in loop.approve

Even if two ResumeJobs were queued for same task:
1. Job1: approve D → consumed=True, RAN, held cleared, run_turn...
2. Job2: held=[] (cleared), takes pause-style resume note, **another** `run_turn` — does **not** re-run D, but **does** start a second continuation turn unexpectedly.

**When can two ResumeJobs queue?**
- `approve` only if AWAITING — sets RUNNING before enqueue. Second approve fails.
- **`resume()` only selects PAUSED.** Approve path doesn't use PAUSED.
- Unless: Job1 all-DENIED restores AWAITING; user approves again — second job intentional.
- **Race:** `approve` sets RUNNING and enqueues. Worker all-DENY restores AWAITING. User approves again quickly — two jobs both legitimate serial attempts.

**Can `resume()` fire for AWAITING?** No.

**Double run of action:** blocked by `consumed` + held clear.  
**Spurious second `run_turn`:** only if duplicate jobs; approve gate mostly prevents.

**C1 double-exec of same action: CERTIFIED blocked.** Spurious extra continuation is hard to hit; LOW if at all.

---

### F11 / C4 — controls grant authority?
**SEVERITY:** one real concern MEDIUM-LOW  

**`set_leash(tool, leash)`** (`host.py:236-240` → `view.set_leash`):
- Writes `session.leash_overrides` without applying signed `leash_cap` at set time.
- Seam applies `apply_cap(leash, leash_cap(...))` at govern/execute — **cannot loosen past signed cap**.
- Host may set ACT_THEN_REPORT in overrides; effective leash still capped. View snapshot shows effective leashes.

**Does set_leash "loosen past signed cap"?** Config can store a looser override; **effective** authority cannot. Claim C4 says "leash capped by the signed grant" — true at seam.

**`set_proactivity`:** surfacing only; propose still govern_action + approve re-gate.

**`approve` / `approve_proposal`:** re-gate via `loop.approve` / `approve_proposal` → `reauthorized_or_denied`. No capability add.

**No path sets `autonomous=True`, `intent.source`, or calls `emit`.** Host does not touch emit.

**`approve_proposal`:** enqueues; worker calls `approve_proposal(session, p)` which uses fixed proposal decision; no request-derived leash keyword.

**Propose trigger:** fail-closed; `_propose_pending` prevents stack enqueue; ledger PROPOSED check prevents stack surface.

**Nit:** `set_leash` allows any tool name string including unknown tools — harmless.

**C4:** no control widens capabilities. **CERTIFIED** with note that leash override storage can be "looser" than cap but seam enforces cap (stated design).

---

### F12 / `approve` sets RUNNING before worker runs — snapshot lies "running" while still waiting in queue
**SEVERITY:** LOW  
**LOCATION:** `host.py:274-284`

UX inaccuracy, not a safety break. Decline won't cancel (state≠AWAITING). User cannot decline after approve clicked even if worker hasn't started — **by design-ish**, but if they mis-clicked, too late without stop. Acceptable; not guarantee-breaking.

---

### F13 / `_handle_approve_proposal` TOCTOU on status
**SEVERITY:** LOW (caught by propose layer)  
**LOCATION:** `host.py:379-386`

```python
with lock:
    p = get; if not PROPOSED: return
# unlock
d = approve_proposal(session, p)
```
Between unlock and approve, `veto` can set VETOED + `consumed=True`. `approve_proposal` checks `status != PROPOSED` and returns without running. **Caught by propose layer.**

Double `approve_proposal` enqueue: both may pass Host check (Host does **not** flip proposal status before enqueue!).

**CONCRETE:**
1. P is PROPOSED.
2. Thread A & B both `approve_proposal(pid)` → both see PROPOSED, both enqueue (Host only `_touch`, **no status claim**).
3. Worker job1: approve → RAN, status=APPROVED, consumed.
4. Worker job2: `approve_proposal` sees status≠PROPOSED → returns held decision, still records via `ledger.record_decision(d)` **again**?

```python
d = approve_proposal(...)  # returns proposal.decision unchanged (still old HELD object?)
ledger.record_decision(d)  # records duplicate HELD decision into ledger!
```

On second call, `approve_proposal` returns `proposal.decision` without running — status may already be APPROVED from first call; decision may be RAN object if same reference updated... Actually first approve replaces flow: `d = approve(session, proposal.decision)` returns **new** Decision from execute path; `proposal.status = APPROVED` but `proposal.decision` is still the **original HELD** object (approve doesn't replace proposal.decision with RAN result!). 

Look at `approve_proposal`:
```python
d = approve(session, proposal.decision)
...
if d.status != DENIED:
    proposal.status = APPROVED
return d
```
First call: `loop.approve` sets `decision.consumed=True` and returns RAN Decision (**may be new object** from execute_and_verify). Original `proposal.decision` has consumed=True, status still HELD on the old object!

`loop.approve` on HELD with consumed:
```python
if getattr(decision, "consumed", False):
    return decision  # still HELD status, consumed
```

Second approve_proposal: status is APPROVED → returns `proposal.decision` (HELD+consumed) without calling approve. Host still `record_decision` that HELD decision again — **duplicate ledger noise**, not double execution.

**C1 double-exec:** blocked by status + consumed.  
**Ledger double-record:** LOW nit.

**FIX:** Claim proposal under lock (`status` sentinel or "approving" flag) before enqueue; skip record if not newly run.

---

### F14 / CRITICAL hunt: execution off worker thread?
Pure-state controls: pause, set_leash, veto, decline — no execute.  
`veto_proposal` only flips status + consumed.  
**No off-worker execution. C1 path claim holds.**

---

### F15 / `_absorb_result` STOPPED_HELD filters `result.decisions` for HELD — includes decisions from whole turn, OK

If approve path ran D then run_turn holds D2: absorb sets held correctly. Good.

---

### F16 / Worker exception during `_handle_resume` after partial approve
**SEVERITY:** MEDIUM  
**LOCATION:** `host.py:350-377`, `host.py:420-428`

**CONCRETE:**
1. `held=[D]`, approve runs D → RAN, lock: record, `task.held=[]`.
2. `run_turn(note, history)` raises (e.g. client error — out of scope as general follow-up, but Host handles via `_fail_active`).
3. `_fail_active` sets FAILED. Action **already ran**; task FAILED after side effect — honest enough.
4. If exception **during** `approve_held_decision` list comp mid-way: D1 ran, D2 not; exception → FAILED; D1 ran with held cleared only if exception after the record block...

If exception inside list comp after D1 RAN:
- held not yet cleared in task (clear is after full list)
- `_fail_active` → FAILED
- D1 **did run** (side effect), task FAILED, D1 may not be in ledger if exception before record block

```python
ran = [approve_held_decision(self.session, d) for d in held]  # D1 executed
# exception on D2
# never reaches record_decisions
_fail_active → FAILED
```
**Lost audit record for D1 that already mutated the world.** Severity MEDIUM for audit/honesty; consumed=True on D1 so no re-run. Not double-exec; **silent loss of ledger record**.

**FIX:** approve+record one decision at a time under try/finally per decision.

---

### F17 / `stop()` does not join ticker; daemon OK

Nit only.

---

## Summary of REAL vs NON-findings

| ID | Issue | Real break? |
|----|--------|-------------|
| F1 | Partial DENIED drops held + continues | YES — C3 |
| F16 | Exception mid-multi-approve loses ledger | YES — C3 audit |
| F13 | Double approve_proposal enqueue | Nit — no double run |
| F3 | Propose concurrent with turn | NON-finding (serial worker) |
| C1 double exec / off-thread | | BLOCKED |
| C2 lock across IO / snapshot tear | | BLOCKED |
| C3 human text as tool result / EMPTY→DONE | | BLOCKED |
| C4 capability widen | | BLOCKED |

---

## Findings (formal list)

### F1 / Partial approval DENIED drops held decisions and still resumes
**SEVERITY:** MEDIUM  
**LOCATION:** `collaborator/host.py:350-366`  
**INPUT:** Task with `held=[D_shell, D_write]` where re-gate DENIES D_shell (cap revoked) but RAN D_write.  
**INTERLEAVING:** single `approve` → `_handle_resume` builds `ran_ok=[D_write]` non-empty → `task.held=[]` → `run_turn` continues.  
**WHY:** C3 requires DENIED remain retryable / not silent loss; only the all-denied branch restores held.  
**OTHER LAYER:** seam correctly leaves DENIED unconsumed; Host drops UX reference.  
**FIX:** Partition ran / still_held; never clear still_held; only resume when held empty; if any still_held after partial run, set AWAITING (or document all-or-nothing approve).

### F2 / Multi-held approve is not atomic per decision for audit on exception  
**SEVERITY:** MEDIUM  
**LOCATION:** `collaborator/host.py:350-355`, `420-428`  
**INPUT:** `held=[D1,D2]`; D1 executes RAN; D2's `approve` raises.  
**WHY:** World mutated; ledger may lack D1; task FAILED — C3 honest outcome/record gap.  
**OTHER LAYER:** `consumed` prevents re-exec; does not fix audit.  
**FIX:** per-decision try/record before next approve.

### F3 / `approve_proposal` does not claim proposal before enqueue (duplicate jobs / duplicate ledger rows)
**SEVERITY:** LOW  
**LOCATION:** `collaborator/host.py:286-296`, `379-386`  
**INPUT:** two concurrent `approve_proposal(pid)`.  
**WHY:** no double-run (`propose.approve_proposal` + `consumed`); possible duplicate `record_decision` of non-RAN.  
**OTHER LAYER:** catches double-exec.  
**FIX:** under lock, only enqueue if PROPOSED and set transient claim or rely on single-flight set.

---

## Certification

**C1 — CERTIFIED.**  
All `run_turn` / `loop.approve` / `approve_proposal` / `propose` execution is confined to the single worker via queued jobs; controls do not execute inline; `decision.consumed` + serial worker block double-execution of a held action. No concrete interleaving achieves off-thread or double run of the same action.

**C2 — CERTIFIED.**  
`snapshot()` and ledger/task compound mutations share one RLock for micro-sections only; lock is not held across model I/O; multi-pass view reads cannot tear against Host appends; proposal display uses ledger list not live pool iteration. No freeze-the-reader or dict-mutation crash path found in Host.

**C3 — NOT-CERTIFIED.**  
All-DENIED→AWAITING, host-authored resume notes, EMPTY/MAX→FAILED, and single-action approve paths hold and are tested, but partial multi-held approval clears `task.held` and resumes whenever any decision RAN (F1), and a mid-list approve exception can leave executed work unrecorded (F2) — silent loss / mis-stated task continuation.

**C4 — CERTIFIED.**  
Controls only pause/tighten/veto/decline or set host config; `set_leash` cannot widen past signed cap at the seam; approve paths re-gate; no control sets capabilities, `autonomous`/`emit`, or `intent.source`; propose trigger is fail-closed and non-stacking under the pending/ledger guards.

---

## STEELMAN

The linchpin design is right: a single worker queue is the correct structural fix for double-turn / double-govern races, and the code consistently enqueues executing controls instead of running them on the request thread. Lock scope is disciplined (no IO under lock), resume notes are host-authored, and the STOPPED_* mapping with an explicit else→FAILED closes the classic “empty looks like done” hole. Authority stays in `govern_action` / `loop.approve`; the Host is genuinely a worker, not a second policy engine.

## VERDICT: **MINOR_ISSUES**

Serial execution and P-01 hold under adversarial threading; the only guarantee-level cracks are in multi-held resume accounting (partial DENIED + exception audit), not in the linchpin concurrency model.

**Highest-value fix:** In `_handle_resume`, approve and ledger-record **one held decision at a time**, keep DENIED/unrun decisions in `task.held`, and call `run_turn` only when `held` is empty (otherwise return to `AWAITING_APPROVAL`).
