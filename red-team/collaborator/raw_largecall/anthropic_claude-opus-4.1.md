# LargeCall panel: anthropic/claude-opus-4.1

_cost=$0.7205 finish=stop_

I'll conduct a thorough adversarial review of this reliability fix, examining each claim systematically.

## FINDINGS

### F1: CRITICAL - Unbounded Retry Loop
**LOCATION**: collaborator/loop.py:234-240
**CONCRETE INPUT**: A client that returns `{"content": "", "tool_calls": None, "finish_reason": "length"}` indefinitely
**BYPASS**: The retry loop in `_complete_actionable` can run FOREVER when a completion is both empty AND truncated:
```python
for attempt in range(max(1, empty_retries + 1)):
    # ...
    if _is_truncated(msg):  # line 239
        grown_mt = min(_TRUNC_BUDGET_CAP, (grown_mt or base_mt) * 2)
        continue  # RESTARTS the loop without incrementing attempt!
```
The `continue` statement bypasses the loop counter - if every response is truncated, the loop never terminates.
**BREAKS**: C3 (retries not bounded for truncation)
**OTHER LAYERS**: No other layer catches this - it's a DoS vector
**FIX**: Track truncation retries separately: `if truncation_attempts > max(1, empty_retries): break`

### F2: HIGH - Double Execution on Truncation+Success
**LOCATION**: collaborator/loop.py:244-245
**CONCRETE INPUT**: Client returns truncated on attempt 0, then valid+actionable on attempt 1 with `_is_truncated(msg)=True` AND `_is_actionable(msg, parsed)=True`
**BYPASS**: Line 244 returns immediately after finding actionable, but line 245's comment claims "If the LAST attempt was actionable-but-truncated, honor it". The actual code at line 245 will NEVER execute because line 241 already returned.
**BREAKS**: C3 (logic error in handling actionable-but-truncated)
**OTHER LAYERS**: Parser would catch if the same call appeared twice
**FIX**: Check `if _is_actionable(msg, parsed) and not _is_truncated(msg):` before returning

### F3: MEDIUM - Vanishing Tool Call in Mixed Batch
**LOCATION**: collaborator/toolcall.py:226-228
**CONCRETE INPUT**: `{"content": '[{"name":"read_file","arguments":{"path":"a"}}, {"tool":"write_file","args":{"path":"b","content":"x"}}]'}`
**BYPASS**: When `_looks_toolish` returns True for the second dict but `_coerce_call` returns None (because it uses "tool"/"args" not "name"/"arguments"), the ENTIRE batch goes to ambiguous. The first VALID call vanishes from intents without appearing in ambiguous as an individual item.
**BREAKS**: C1 (valid call in batch vanishes)
**OTHER LAYERS**: The batch appears in ambiguous, but the valid call is lost
**FIX**: Add individual valid calls to intents before checking if whole batch failed

### F4: LOW - ScriptedClient Incompatibility
**LOCATION**: collaborator/model_client.py:73,79
**CONCRETE INPUT**: Existing test using ScriptedClient
**ISSUE**: ScriptedClient now requires handling `max_tokens` parameter but has no `max_tokens` attribute for `getattr(client, "max_tokens", _DEFAULT_CLIENT_MAX_TOKENS)` in loop.py:215
**BREAKS**: C4 (ScriptedClient regression)
**OTHER LAYERS**: Tests would fail immediately
**FIX**: Add `self.max_tokens = 16384` to ScriptedClient.__init__

### F5: LOW - Unbalanced Span Text Stripping
**LOCATION**: collaborator/toolcall.py:197-201
**CONCRETE INPUT**: `{"content": "Here's the call: <tool_call>{"name":"read","arguments":{"path":"truncated at max_tokens"}`
**ISSUE**: The unbalanced span from char 17 to end gets added to ambiguous, but line 200 strips from position 17 to END, losing "Here's the call: " prefix
**BREAKS**: C1 (prose text before truncated call vanishes)
**OTHER LAYERS**: Text still mostly preserved
**FIX**: Track unbalanced spans separately when building keep array

## CERTIFICATION

**C1 (no silent loss)**: NOT CERTIFIED - F3 shows a valid call in a mixed batch can vanish from both intents and individual ambiguous items

**C2 (governance untouched)**: CERTIFIED - The retry logic only affects completion parameters; all intents still flow through govern_action with identical authority checks regardless of temperature/max_tokens

**C3 (no partial/duplicate + bounded)**: NOT CERTIFIED - F1 shows retry loop is NOT bounded for truncation cases due to the continue statement bug

**C4 (no regression)**: NOT CERTIFIED - F4 shows ScriptedClient breaks with the new max_tokens parameter

## STEELMAN
The fix successfully addresses the core reliability concern of silently dropped large/truncated calls by surfacing them as ambiguous and implementing smart retry strategies. The temperature escalation for empty completions and budget growth for truncations are well-designed perturbations that solve real failure modes without compromising security.

## VERDICT
**SERIOUS_FLAWS** - The unbounded retry loop (F1) is a critical DoS vector that must be fixed immediately.

**Single highest-value fix**: Add a separate truncation retry counter to prevent infinite loops:
```python
truncation_retries = 0
for attempt in range(max(1, empty_retries + 1)):
    # ...
    if _is_truncated(msg):
        truncation_retries += 1
        if truncation_retries > empty_retries:
            break
        grown_mt = min(_TRUNC_BUDGET_CAP, (grown_mt or base_mt) * 2)
        continue
```
