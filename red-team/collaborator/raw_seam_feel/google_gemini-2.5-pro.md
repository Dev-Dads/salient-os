# Seam feel code panel: google/gemini-2.5-pro

_cost=$0.1341 finish=stop_

Here is the adversarial code review and certification.

***

### Security Review Findings

**ID:** C3-01
**TITLE:** Unbounded Conversation History Growth Leads to Denial of Service and Context Window Overflow
**SEVERITY:** MEDIUM
**LOCATION:** `host.py:407` (`self._history = result.history`)
**CONCRETE SEQUENCE:**
1.  An operator starts a `Collaborator` session and interacts with it via the surface.
2.  With each `/submit`, a turn is executed. The `run_turn` function in `loop.py` appends the user message and the model's response(s) to the history.
3.  In `host.py:_absorb_result`, the entire, now-longer `result.history` is assigned back to `self._history`.
4.  This cycle repeats. The `self._history` list grows linearly with the number of conversational turns.
5.  After a sufficient number of turns (hundreds or thousands, depending on message length), one of two failures will occur:
    *   **Context Overflow:** The history list becomes so large that its token count exceeds the model's context window. The next call to `client.complete(history, ...)` in `loop.py` will fail, likely with an error from the model backend. This will cause the turn to fail, breaking the agent's functionality.
    *   **Denial of Service (Memory):** The `self._history` object in the `Collaborator` process will grow without bound, consuming increasing amounts of memory. For a very long-lived session, this can lead to excessive memory usage and potential process termination by the OS.
**WHICH CLAIM IT BREAKS:** C3 (memory-threading soundness). While the threading is sound, the lifecycle management is not. The claim implies history does not corrupt the *next* turn, but unbounded growth corrupts *all future* turns past a certain point.
**WHETHER ANOTHER LAYER CATCHES IT:** No. The model client will be the first thing to fail when the context window is exceeded, but this is the failure mode, not a mitigation. There is no trimming or summarization layer.
**FIX:** Implement a history-trimming strategy in `host.py` before passing `self._history` to `run_turn`, or within `run_turn` itself. A common strategy is to always keep the system prompt (`history[0]`) and the `N` most recent user/assistant message pairs. For example, in `_handle_turn`:
```python
# host.py:_handle_turn
MAX_HISTORY_PAIRS = 20  # Configurable
# ...
history_to_send = self._history
if history_to_send and len(history_to_send) > (MAX_HISTORY_PAIRS * 2 + 1):
    # Keep system prompt + N most recent pairs
    history_to_send = [history_to_send[0]] + history_to_send[-(MAX_HISTORY_PAIRS * 2):]
result = run_turn(self.session, self.doer_client, job.user_message,
                  history=history_to_send)
# ...
```

***

### Certification

**C1 (AUTHORITY): CERTIFIED.** The six changes exclusively affect what the model KNOWS or what the surface SHOWS; none creates a new path for data to influence authority. Hostile inputs from `self._history`, workspace filenames, the `target` field, or the clock string are successfully confined to the DATA side of the DATA/AUTHORITY seam, influencing model proposals but never bypassing or altering the `govern_action` gate.

**C2 (RENDER / XSS): CERTIFIED.** The new conversational UI and context fields are rendered with strict discipline. All untrusted strings from the snapshot (`prompt`, `reply`, `target`, `rationale`, `held` summaries, and optimistic echos) are sunk exclusively into `element.textContent` or `document.createTextNode` via the `el()` helper and related functions. No path for `innerHTML` injection, attribute/style injection, or string-to-JS-eval was found.

**C3 (MEMORY): NOT-CERTIFIED.** While the serial worker invariant correctly prevents race conditions and turn-to-turn state corruption from failed/held states is handled, the memory implementation lacks any trimming mechanism, leading to unbounded growth of `self._history` and a guaranteed eventual DoS/context-overflow failure (see finding C3-01).

**C4 (LEAK): CERTIFIED.** The new context-surfacing features (`target`, workspace grounding, clock) do not introduce new egress channels or leak raw file content or secrets to unauthorized parties. The `target` field may surface command-line arguments containing credential-like strings, but this is to the operator's own authenticated, auth-walled view, which is consistent with the surface's purpose as a watch-and-steer dashboard.

***

### Steelman & Verdict

**STEELMAN:** This PR successfully transforms the user experience from a sterile dashboard into a genuine conversational partner, directly addressing the core "feel" problem. It does so by correctly leveraging the system's existing safety guarantees: the `govern_action` boundary contains any model behavior influenced by the new memory, and the surface's `textContent`-only rendering discipline safely displays the new conversational context. The changes are surgically precise, achieving their UX goals without regressing the critical P-01 and XSS security postures of the certified system.

**VERDICT: MINOR_ISSUES.** The changes are fundamentally sound and preserve the core security guarantees, but the lack of a history-trimming mechanism is a significant availability and correctness flaw that will break any long-running session.

**Highest-Value Fix:** Implement a bounded history-trimming strategy (see C3-01) to prevent the inevitable context window overflow and memory exhaustion, making the conversation memory feature robust for real-world use.
