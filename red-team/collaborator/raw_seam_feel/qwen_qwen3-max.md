# Seam feel code panel: qwen/qwen3-max

_cost=$0.0983 finish=stop_

### FINDINGS

#### F1: AUTHORITY LEAK VIA THREADED HISTORY (C1)
- **ID**: F1
- **TITLE**: Threaded history could influence authority if system grounding is corrupted
- **SEVERITY**: CRITICAL
- **LOCATION**: host.py:324, loop.py:267
- **CONCRETE SEQUENCE**: 
  1. Attacker crafts a user message that includes a system-like message with malicious content
  2. This message gets added to history as a user message (role="user")
  3. On next turn, run_turn re-asserts system message at history[0] but preserves prior messages
  4. If the model outputs a system message (which it shouldn't), it could potentially be preserved in history
  5. However, the code explicitly only allows the host to set history[0] and replaces it with fresh sys_msg
- **WHICH CLAIM IT BREAKS**: C1 (authority path)
- **WHETHER ANOTHER LAYER CATCHES IT**: YES - The run_turn function explicitly replaces history[0] with a fresh system message on every turn (loop.py:267). The model cannot inject system messages since its outputs are always role="assistant" and tool results are role="user". The governance boundary remains intact.
- **FIX**: None needed - the current implementation correctly re-asserts the system message.

#### F2: WORKSPACE FILENAME INJECTION (C1, C4)
- **ID**: F2
- **TITLE**: Hostile workspace filenames could influence authority or leak information
- **SEVERITY**: HIGH
- **LOCATION**: propose.py:296, view.py:38
- **CONCRETE SEQUENCE**:
  1. Attacker creates a file with name containing malicious content like "../../../etc/passwd"
  2. This filename appears in workspace grounding (propose.py) and target field (view.py)
  3. However, these are display-only fields capped at 200 chars and neutralized
  4. The actual file operations go through resolve_in_workspace which prevents path traversal
- **WHICH CLAIM IT BREAKS**: C1 (authority), C4 (leak)
- **WHETHER ANOTHER LAYER CATCHES IT**: YES - File operations are governed by resolve_in_workspace which validates paths are within workspace. Display fields are neutralized and capped. The surface is auth-walled so operator sees their own files.
- **FIX**: None needed - existing protections are sufficient.

#### F3: CLOCK STRING INJECTION (C1)
- **ID**: F3
- **TITLE**: Current datetime string could influence authority decisions
- **SEVERITY**: MEDIUM
- **LOCATION**: loop.py:127
- **CONCRETE SEQUENCE**:
  1. Attacker manipulates system clock to create misleading datetime string
  2. This appears in system prompt but only as factual grounding
  3. Cannot influence authority since govern_action is unchanged
- **WHICH CLAIM IT BREAKS**: C1 (authority)
- **WHETHER ANOTHER LAYER CATCHES IT**: YES - The clock string is pure grounding data that only affects what the model knows, not what it's allowed to do. Authority remains gated by govern_action.
- **FIX**: None needed.

#### F4: TARGET FIELD XSS (C2)
- **ID**: F4
- **TITLE**: Target field could enable XSS if not properly escaped
- **SEVERITY**: HIGH
- **LOCATION**: surface.py:623, surface.py:636
- **CONCRETE SEQUENCE**:
  1. Attacker creates a file with name containing HTML/JS like "<script>alert(1)</script>"
  2. This appears in target field which is rendered via el("span", "prop-target", d.target)
  3. The el() function uses textContent, not innerHTML, preventing XSS
- **WHICH CLAIM IT BREAKS**: C2 (render safety)
- **WHETHER ANOTHER LAYER CATCHES IT**: YES - The el() function explicitly sets textContent, not innerHTML. All model/snapshot strings are rendered via textContent only.
- **FIX**: None needed.

#### F5: UNBOUNDED HISTORY GROWTH (C3)
- **ID**: F5
- **TITLE**: History grows unbounded across turns causing potential DoS
- **SEVERITY**: MEDIUM
- **LOCATION**: host.py:410
- **CONCRETE SEQUENCE**:
  1. User sends many messages in sequence
  2. Each turn appends to self._history without trimming
  3. Eventually history becomes very large, consuming memory and slowing model calls
- **WHICH CLAIM IT BREAKS**: C3 (memory-threading)
- **WHETHER ANOTHER LAYER CATCHES IT**: NO - No trimming mechanism exists
- **FIX**: Implement history trimming after N turns or when exceeding token limits.

#### F6: OPTIMISTIC ECHO RECONCILIATION (C3)
- **ID**: F6
- **TITLE**: Optimistic echo might not reconcile properly with real tasks
- **SEVERITY**: LOW
- **LOCATION**: surface.py:763
- **CONCRETE SEQUENCE**:
  1. User sends message "test"
  2. Optimistic echo shows "test" immediately
  3. Submit fails but error handling clears pendingEcho
  4. If submit succeeds but task_id doesn't match due to race condition, echo might persist
- **WHICH CLAIM IT BREAKS**: C3 (memory-threading)
- **WHETHER ANOTHER LAYER CATCHES IT**: PARTIALLY - The reconciliation logic checks if last task's prompt matches pendingEcho, but edge cases might exist
- **FIX**: Strengthen reconciliation by also checking task state or using unique message IDs.

### CERTIFICATION

**C1 (P-01 preserved)**: CERTIFIED. None of the six changes create or widen an authority path. The threaded history, workspace grounding, target field, and clock string only influence what the model knows or what is displayed, never what is allowed. The govern_action boundary remains intact and unchanged.

**C2 (render safety / no XSS)**: CERTIFIED. All model/snapshot strings are rendered via textContent only through the el() function. No innerHTML, attribute injection, or eval paths exist. The new conversation thread and target fields are safely rendered.

**C3 (memory-threading soundness)**: NOT-CERTIFIED. While the threading logic correctly maintains conversation memory across turns and handles failed/empty turns appropriately, the unbounded history growth presents a real DoS/context-overflow problem that should be addressed for production use.

**C4 (context surfacing doesn't leak)**: CERTIFIED. The target field, workspace grounding, and clock surface context only to the operator's auth-walled view. No new egress is created, raw content/secrets aren't exposed, and any credential-shaped values in targets are acceptable given the auth-walled context.

### STEELMAN

The PR successfully transforms the surface from a governance dashboard into a conversational partner while maintaining strict security boundaries. The conversation memory threading is carefully implemented with proper system message re-assertion, and all new display fields are safely rendered. The workspace grounding helps prevent phantom file proposals, and the clock anchoring improves temporal reasoning without compromising security.

### VERDICT

MINOR_ISSUES. The implementation is largely sound with strong adherence to P-01, but the unbounded history growth (F5) represents a real DoS/context-overflow concern that should be addressed. The highest-value fix is implementing history trimming to bound memory usage and context length while preserving recent conversation flow.
