# LargeCall panel: qwen/qwen3-max

_cost=$0.0626 finish=stop_

## FINDINGS

### F1 / Truncated Span Stripping May Leak Unbalanced Content into Prose
**SEVERITY**: HIGH  
**LOCATION**: `collaborator/toolcall.py:197`  
**CONCRETE INPUT**: `'abc<tool_call{"name":"x","arguments":{"content":"{unclosed"}}'` (no closing `}`)  
**WHY IT BREAKS A GUARANTEE**: The unbalanced span `(start=3, end=len(content), balanced=False)` gets added to `ambiguous`, but the text-stripping loop uses `max(last, end)` where `end=len(content)`. This correctly strips the span, BUT if there are *multiple* markers where only the *last* is unbalanced, earlier balanced spans might extend `last` beyond the unbalanced span's start, causing the unbalanced content to remain in `text`.  
**ANOTHER LAYER CATCHES IT**: NO — this directly violates C1 by leaking tool-shaped content into prose without surfacing it as ambiguous or running it.  
**FIX**: In the text-stripping loop, process hits in order and ensure unbalanced spans are always stripped regardless of `last`.

### F2 / Whole-Content Batch with Mixed Valid/Invalid May Partially Run
**SEVERITY**: CRITICAL  
**LOCATION**: `collaborator/toolcall.py:225`  
**CONCRETE INPUT**: `'["valid_call", "invalid_element"]'` as whole content  
**WHY IT BREAKS A GUARANTEE**: The parser checks `any(_looks_toolish(o))` for the entire batch. If ANY element is toolish, the WHOLE batch goes to `ambiguous`. However, if the batch contains both valid AND invalid elements, the valid ones should NOT run (strictness), but the current logic doesn't prevent partial execution because the valid elements pass `_coerce_call` individually before the batch check.  
**ANOTHER LAYER CATCHES IT**: NO — this violates C3's strictness preservation guarantee.  
**FIX**: For whole-content batches, if ANY element fails coercion, reject the entire batch immediately without processing any elements.

### F3 / Truncation Retry May Execute Same Action Twice
**SEVERITY**: HIGH  
**LOCATION**: `collaborator/loop.py:230`  
**CONCRETE INPUT**: A completion that is BOTH actionable AND truncated (e.g., contains valid tool calls but gets cut off mid-prose)  
**WHY IT BREAKS A GUARANTEE**: The current logic checks `_is_truncated(msg)` BEFORE `_is_actionable(msg, parsed)`. If a message is truncated but still contains valid tool calls, it will be retried, potentially executing the same valid calls again in the retry.  
**ANOTHER LAYER CATCHES IT**: PARTIALLY — `govern_action` has idempotency via `action_id`, but duplicate governance decisions could occur.  
**FIX**: Check `_is_actionable` BEFORE deciding to retry on truncation. Only retry if NOT actionable.

### F4 / finish_reason Attachment May Leak to Wire
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/model_client.py:60`  
**CONCRETE INPUT**: Any model response  
**WHY IT BREAKS A GUARANTEE**: The code attaches `finish_reason` to the message dict returned by `complete()`. While the comment claims this is "never sent back on the wire", if any code path accidentally reuses this dict as input to another `complete()` call, the `finish_reason` could leak.  
**ANOTHER LAYER CATCHES IT**: YES — `run_turn` builds fresh assistant turns, so normal operation is safe.  
**FIX**: Return a copy of the message dict with `finish_reason` attached, never modify the original.

## CERTIFICATION

**C1 (no silent loss)**: NOT-CERTIFIED — Finding F1 shows unbalanced content can leak into prose without being surfaced.  
**C2 (governance untouched)**: CERTIFIED — Authority decisions depend only on parsed intents, which are unchanged by retries; temperature/max_tokens don't affect governance.  
**C3 (no partial/duplicate run + bounded + strictness preserved)**: NOT-CERTIFIED — Finding F2 violates strictness preservation; Finding F3 risks duplicate execution.  
**C4 (no regression / interface)**: CERTIFIED — The changes don't break existing interfaces; ScriptedClient handles new parameters; finish_reason attachment is isolated.

## STEELMAN

The reliability fix correctly addresses the core concern of silent loss for large/truncated tool calls by ensuring unbalanced spans are surfaced as ambiguous. The retry logic for empty and truncated responses is well-designed with proper bounds, and the governance boundary remains intact since all parsed intents flow through `govern_action` regardless of retry attempts. The parser's handling of mixed batches generally preserves strictness by surfacing malformed batches rather than dropping them entirely.

## VERDICT

**SERIOUS_FLAWS** — The implementation has critical gaps in maintaining strictness for mixed batches and potential for content leakage from unbalanced spans, violating the core guarantee against silent loss. The highest-value fix is to ensure whole-content batches are atomically validated before any element is processed, and to correct the text-stripping logic to handle multiple markers with unbalanced spans properly.
