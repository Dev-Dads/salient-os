# LargeCall panel: anthropic/claude-opus-4.1

_cost=$0.7317 finish=stop_

I'll conduct a thorough adversarial review of this reliability fix, examining each claim systematically.

## FINDINGS

### F1: CRITICAL - Truncation Retry Can Execute Partial Writes
**SEVERITY**: CRITICAL  
**LOCATION**: loop.py:227-233  
**CONCRETE INPUT**: A message with a complete call followed by a truncated one where `_is_truncated(msg)` is True but `parsed.intents` is non-empty
**BYPASS**: 
```python
msg = {
    "content": '<tool_call>{"name":"write_file","arguments":{"path":"a.txt","content":"data"}}</tool_call>',
    "finish_reason": "length"
}
# This has parsed.intents=[write_file(a.txt)] AND _is_truncated=True
# Line 227: if _is_truncated(msg) and not parsed.intents: # FALSE - falls through
# The complete call RUNS, then continues the retry loop
# Next iteration could run THE SAME call again if client returns it
```
**WHY IT BREAKS**: C3 guarantee violated - a completed call in a truncated message can run, then the retry could run it AGAIN if the client repeats it
**ANOTHER LAYER**: No - govern_action doesn't track already-executed calls across retries
**FIX**: Track executed action_ids within _complete_actionable and skip already-run calls

### F2: HIGH - Unbalanced Span Text Stripping is Incorrect
**SEVERITY**: HIGH  
**LOCATION**: toolcall.py:194-199  
**CONCRETE INPUT**: 
```python
content = 'Text before <tool_call>{"name":"read_file","arguments":{"path":"truncated'
# Produces hits = [(12, 76, '{"name":...truncated', False)]
# Lines 197-199 strip content[12:76], but this removes "Text before " too
```
**WHY BREAKS**: C1 guarantee - prose text "Text before" vanishes from both `text` and `ambiguous`
**ANOTHER LAYER**: No - the text is lost at parse time
**FIX**: Only strip from the <tool_call marker start, not from hit[0]

### F3: MEDIUM - Budget Growth Not Actually Bounded
**SEVERITY**: MEDIUM  
**LOCATION**: loop.py:232  
**CONCRETE INPUT**: If base_mt=0 (from a misconfigured client), then `(grown_mt or base_mt) * 2` = 0
**BYPASS**: `grown_mt = min(65536, 0 * 2)` = 0, infinite retry loop at 0 tokens
**WHY BREAKS**: C3 guarantee - budget growth supposedly bounded but can get stuck at 0
**ANOTHER LAYER**: No
**FIX**: Use `max(base_mt, 1)` to ensure minimum growth

### F4: LOW - ScriptedClient Doesn't Handle finish_reason
**SEVERITY**: LOW  
**LOCATION**: model_client.py:73-81  
**ISSUE**: ScriptedClient.complete() never sets finish_reason in returned messages
**WHY BREAKS**: C4 - ScriptedClient tests can't properly test truncation scenarios
**ANOTHER LAYER**: Yes - real tests would use OllamaClient
**FIX**: ScriptedClient should check if queued messages contain finish_reason

### F5: LOW - Mixed Valid/Invalid Batch Ambiguous Reporting
**SEVERITY**: LOW  
**LOCATION**: toolcall.py:225-227  
**CONCRETE INPUT**: `[{"name":"write_file","arguments":{"path":"a","content":"x"}}, {"malformed":true}]`
**ISSUE**: Only checks if ANY element `_looks_toolish`, but the valid first element would pass `_coerce_call` - inconsistent criteria
**WHY BREAKS**: Minor C1 issue - ambiguous detection uses different criteria than intent parsing
**ANOTHER LAYER**: Yes - still surfaced as ambiguous correctly
**FIX**: Check `_coerce_call` success directly rather than `_looks_toolish`

## CERTIFICATION

**C1 (no silent loss)**: NOT CERTIFIED - F2 shows prose text can vanish when an unbalanced span is processed

**C2 (governance untouched)**: CERTIFIED - Traced the path and confirmed that grown max_tokens/raised temperature never affect govern_action's authority decisions, which depend only on session capabilities and tool requirements

**C3 (no partial/duplicate run + bounded)**: NOT CERTIFIED - F1 shows a complete call in a truncated message can run and potentially be duplicated; F3 shows budget growth can fail with base_mt=0

**C4 (no regression/interface)**: CERTIFIED with minor issue - The core interfaces work but ScriptedClient doesn't properly support finish_reason testing

## STEELMAN

The fix successfully addresses the core reliability concern: truncated and malformed tool calls are now surfaced rather than silently dropped, and the retry mechanism with budget growth allows large calls to complete. The governance boundary remains properly isolated from the retry logic, maintaining security guarantees while improving reliability.

## VERDICT

**SERIOUS_FLAWS** - The truncation retry can execute duplicate calls (F1) and text can vanish during unbalanced span processing (F2).

**Single highest-value fix**: Add action_id tracking in _complete_actionable to prevent duplicate execution of already-run calls across retry iterations.
