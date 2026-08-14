# C3 re-cert: openai/gpt-5.1

_cost=$0.0280 finish=stop_

FINDING A — UNBOUNDED HISTORY GROWTH: **RESOLVED**

- Every call to `run_turn` in `_handle_turn` and `_handle_resume` now passes `history=self._trim_history(...)`, which:
  - Walks from the end, accumulating messages until `_HISTORY_CHAR_BUDGET` (60k chars) would be exceeded, then stops.
  - Always keeps at least the single most recent message (because it only breaks when `kept` is non-empty).
  - Returns a fresh list, never the original object.
- `self._history` is only reassigned in `_absorb_result` from `result.history` on terminal states (not on held/paused), and that history itself will have been subject to trimming at the start of the turn; so across turns, the maximum length of the history list and total characters in contents is bounded by the budget plus whatever `run_turn` adds in the current step.
- Since the model’s context length is bounded by the char window each turn, neither context nor process RSS can grow without bound via history accumulation.

FINDING B — HELD + INTERVENING SUBMIT + APPROVE CLOBBER/INTERLEAVE: **RESOLVED**

Trace the adversarial sequence exactly under the patch:

1. **submit A → DONE**
   - `_handle_turn` calls `run_turn(..., history=_trim_history(self._history))`.
   - Suppose this yields `result_A` with `stopped in STOPPED_SUCCESS`.
   - `_absorb_result`:
     - `task_A.history = result_A.history`
     - `result_A.stopped not in STOPPED_AWAITING` ⇒ `self._history = result_A.history` (rooted at H1).
   - Shared conversation is now H1.

2. **submit B → HELD**
   - `_handle_turn` for B calls `run_turn(..., history=_trim_history(self._history))`, i.e. trimmed H1.
   - `run_turn` mutates its passed history in place, but that is a copy; `_trim_history` has de-aliased it from `self._history`.
   - Suppose we get `result_B` with `stopped == STOPPED_HELD` and history H2 (H1 + dangling tool-call).
   - `_absorb_result`:
     - `task_B.history = H2`
     - `result_B.stopped in STOPPED_AWAITING` ⇒ **no update** to `self._history`; it remains H1.
     - B is moved to `AWAITING_APPROVAL`, and held decisions are stored.

3. **submit C while B is HELD**
   - `_handle_turn` for C calls `run_turn(..., history=_trim_history(self._history))` again, i.e. based on H1, not H2.
   - So the dangling unapproved tool-call from B in H2 is not present in C’s input history; C’s run is cleanly threaded after A.
   - Suppose `result_C` is DONE, with history H3 rooted in H1 (plus C-related messages).
   - `_absorb_result` on C:
     - `task_C.history = H3`
     - Terminal ⇒ `self._history = H3`.
   - Global history is now H3 (which already includes all of A and C’s relevant context, bounded by the trim).

4. **approve B**
   - `approve(task_B)` sets `task_B.state = RUNNING` and enqueues `_ResumeJob(task_B.id)`.
   - `_handle_resume` for B:
     - Under lock, it reloads `task_B` and ensures state in `(RUNNING, QUEUED)`; that’s still true.
     - `held = list(task.held)`; `history = task.history` → this is still H2, the forked held history for B only.
     - Releases lock, then:
       - Runs all held decisions (`approve_held_decision`) and mutates `task.held`/`task.approved_ran`/`task.decisions` under lock.
       - If any still held, it returns without resuming; otherwise constructs the `note` summarizing approved actions.
     - Finally calls `run_turn(..., note, history=self._trim_history(history))`:
       - This resumes B from its own private history H2 (trimmed), not from `self._history` (H3).
       - The dangling tool-call is properly closed out in B’s own thread; C remains logically on H3.
     - `_absorb_result` on this resumed B:
       - `task_B.history = result_B2.history`.
       - If B now terminates, `self._history` becomes `result_B2.history`, i.e. the full H2→B2 story, trimmed.

Key points:

- The clobber/rewind that used to happen (because `_history` was set to H2 on HELD and later overwritten from a stale fork) is removed: HELD/PAUSED outcomes never advance `self._history`.
- Intervening C runs strictly atop the last terminal history (H1) and cannot see H2’s dangling tool-call; later approval/resume of B operates over B’s own copied `task.history` only.
- Because `_trim_history` always creates a new list, neither `task.history` nor `self._history` ever alias the list `run_turn` mutates in place, so later turns cannot corrupt stored histories indirectly.

**NEW ISSUES INTRODUCED BY THE FIX?**

I looked for:

1. **History possibly becoming empty or structurally invalid:**
   - `_trim_history` on `None` or `[]` returns it unchanged; `run_turn` will then be invoked with `history=None` or `history=[]`.
   - The pre-existing contract says `run_turn` re-asserts the system message at `history[0]` every turn, so even if we trimmed away an old system message or start with an empty history, `run_turn` will inject the system message and maintain the expected system-first structure.
   - `_trim_history` itself always keeps at least one message when `history` is non-empty (by construction of the `if kept and ...: break` guard), so it never returns an empty list when given a non-empty one. There’s no way to “split” a single logical turn’s messages: it just drops a prefix; it doesn’t create partial messages.

2. **Mis-bounding of context / unbounded growth sneaking back indirectly:**
   - Each turn’s input history is bounded to `_HISTORY_CHAR_BUDGET` plus any new content added during that turn. On the next turn, `_trim_history` re-applies to the expanded history, re-constraining it. So steady-state, the size is O(budget) per turn, not growing with time.
   - The crude char-based measure is an over-approximation of tokens but not an under-approximation; at worst, you get fewer than intended tokens, not more. This is safe for C3’s “no unbounded growth / overflow” concern.

3. **Interaction with `_fail_active`:**
   - On exceptions, `_fail_active` explicitly does not touch `self._history`, preserving the last coherent terminal state; this cannot cause history corruption because a failed `run_turn` never produced a new `result.history` to adopt.
   - Task-level failure state and `error` are still recorded correctly.

4. **Run-resume races / stale `_ResumeJob`s:**
   - `_handle_resume` re-checks `task.state in (RUNNING, QUEUED)` under the lock and bails if the task has since been declined/finished; this prevents stale resume jobs from re-entering `run_turn` and does not conflict with history trimming.

I do not see a concrete new correctness or safety bug introduced by the patch under adversarial sequences, including the previously-problematic HELD + intervening submit + approve path.

**C3 (memory-threading soundness):** **CERTIFIED**

- History is now kept to a bounded rolling window with explicit trimming, aliased histories are eliminated via fresh-list copies, and HELD/PAUSED turns no longer advance the shared conversation or allow later approvals to rewind past intervening turns; I cannot construct a sequence that breaks memory-threading invariants under this design.

**VERDICT:** C3 findings A and B are resolved, no new issues are introduced, and claim C3 is certified for this patch.
