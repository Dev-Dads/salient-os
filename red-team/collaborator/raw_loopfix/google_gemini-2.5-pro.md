# LoopFix panel: google/gemini-2.5-pro

_cost=$0.1065 finish=length_

This is a high-quality, targeted fix for a critical silent-failure bug. The core logic is sound, and the separation between the recovery mechanism and the governance seam is well-maintained. My review found one medium-severity integration risk and confirmed the robustness of the core claims.

### FINDINGS

---

**ID:** C4-1
**TITLE:** Unhandled `stopped="empty"` State May Break Callers
**SEVERITY:** MEDIUM
**LOCATION:** `collaborator/loop.py:127` (definition), and any un-updated caller of `run_turn`.

**CONCRETE INPUT OR BYPASS:**
Any user prompt that is deterministically empty-prone for the model will trigger this. For example, using the provided `_EmptyAtLowTempClient` and calling `run_turn` with `empty_retries` set high enough to exhaust the client's scripted empty responses.

```python
# In a hypothetical caller UI module:
result = run_turn(session, client, "do something that will be empty")

if result.stopped == "final":
    display_final_answer(result.reply)
elif result.stopped == "held":
    display_held_actions(result.decisions)
# ... other elifs for 'paused', 'max_iterations'
# NO ELSE CASE!
# In this scenario, if result.stopped is "empty", the UI does nothing.
# The user sees a blank screen or no update, and the informative message in
# result.reply is lost.
```

**WHY IT BREAKS A GUARANTEE:**
This breaks the C4 claim of "no regression / interface" and that "`stopped="empty"` ... is a value callers can handle". While the loop itself behaves correctly, adding a new value to an enumerated status string (`stopped`) creates a risk that existing callers who use `if/elif` chains without a final `else` will fail to handle the new state. This can lead to a silent failure at the UI/integration layer, where the user is not shown the informative error message (`"(no action taken...)"`), recreating a silent no-op problem at a higher level.

**WHETHER ANOTHER LAYER CATCHES IT:**
No. This is an interface contract violation. The caller is the "last layer" for this aspect of the `TurnResult` object.

**FIX:**
Audit all callers of `run_turn` to ensure they have a handler for the `stopped="empty"` case, or a default `else` block that displays `result.reply`. The fix is in the calling code, not the reviewed code, but the responsibility for ensuring this falls on the author of the interface change.

---

### CERTIFICATION

**C1 (no silent no-op):** CERTIFIED. The `_is_actionable` check, specifically `(parsed.text or _content(msg)).strip()`, correctly identifies all forms of empty or whitespace-only content as non-actionable, and the `run_turn` logic explicitly returns `stopped="empty"` if the retry budget is exhausted, preventing a silent `stopped="final"` result.

**C2 (governance untouched):** CERTIFIED. The retry and temperature escalation logic is fully contained within `_complete_actionable`. The `msg` and `parsed` objects it returns carry no state about the attempt number or temperature. `govern_action` is called with the same signature and depends only on the `intent` and `session`, neither of which are affected by the retry mechanism. The authority decision for a given `intent` is therefore byte-identical regardless of which retry attempt produced it.

**C3 (termination + bounded cost):** CERTIFIED. The retry loop in `_complete_actionable` is bounded by `range(max(1, empty_retries + 1))`, which is robust and correct. A legitimate final answer is correctly identified as actionable on the first attempt and is not re-rolled. Decisions accumulated in a prior iteration of the main `run_turn` loop are correctly preserved in the `decisions` list if a subsequent iteration fails with an empty completion.

**C4 (no regression / interface):** NOT-CERTIFIED. While the `temperature` kwarg, `_retry_temperature` function, and `max_tokens` bump are safe, the introduction of the new `stopped="empty"` status creates a medium-severity risk of breaking callers that do not exhaustively handle all `stopped` states (see finding C4-1).


