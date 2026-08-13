# LargeCall panel: qwen/qwen3-max

_cost=$0.0775 finish=stop_

**FINDINGS**

---

**F1 / Truncated whole-content batch with valid prefix may vanish**  
**SEVERITY**: CRITICAL  
**LOCATION**: `collaborator/toolcall.py:225` (in `parse_message`)  
**CONCRETE INPUT**:  
```json
{"content": "[{\"name\":\"write_file\",\"arguments\":{\"path\":\"a\",\"content\":\"x\"}}, {\"not"}
```
This is a whole-content JSON array that starts with a valid call but is truncated before the second element closes. It has no `<tool_call>` markers, so it goes through the whole-content path. `_try_json` returns `None`, and `_text_looks_toolish` returns `True` because `"name"` appears in the string, so it *is* appended to `ambiguous`. **However**, consider this variant:
```json
{"content": "[{\"name\":\"write_file\",\"arguments\":{\"path\":\"a\",\"content\":\"x\"}}, 42"}
```
Here, `_try_json` succeeds and returns a list `[valid_dict, 42]`. The code checks `if all(g is not None for g in got)` — which fails because `_coerce_call(42, ...)` returns `None`. Then it checks `elif any(_looks_toolish(o) for o in payload if isinstance(o, dict))`. The first element is a dict and `_looks_toolish` returns `True`, so the **entire candidate string is appended to ambiguous**. So far so good.

Now consider:
```json
{"content": "[42, {\"not_a_call\": true}]"}
```
`_try_json` returns a list. `all(...)` fails. The loop over `o in payload if isinstance(o, dict)` yields one dict `{\"not_a_call\": true}`. `_looks_toolish` returns `False` (no tool keys). So **neither branch fires**. The batch is **silently dropped**: no `intents`, no `ambiguous`, and since there are no `hits` from `_tool_call_tag_objects`, `remaining = content`. But then `remaining.strip()` becomes the `text`. However, the whole point of the whole-content path is that **if the entire message is a tool-call attempt**, even a malformed one, it should be treated as `ambiguous` **and removed from `text`**. Here, it remains in `text`, **leaked as prose**, and **not surfaced as ambiguous**. This violates C1.

But wait—the claim says “a large / truncated / malformed / batched tool call”. Is `[42, {"not_a_call": true}]` “tool-shaped”? `_text_looks_toolish` would return `False` because none of the `_TOOLISH_TOKENS` appear. So maybe this isn’t considered tool-shaped. However, consider:
```json
{"content": "[42, {\"name\": \"not_a_real_tool\", \"arguments\": {}}]"}
```
Now `_looks_toolish` on the second element returns `True` (`"name"` is present). So `any(...)` is `True`, and the whole batch is appended to `ambiguous`. Good.

The real bypass is:
```json
{"content": "[{\"function\": {\"name\": \"write_file\", \"arguments\": \"invalid_json_string\"}}]"}
```
`_try_json` succeeds (it’s valid JSON). `got = [_coerce_call(...)]`. `_coerce_call` sees `arguments` is a string `"invalid_json_string"`, tries `json.loads`, fails, returns `None`. So `all(g is not None...)` is `False`. Then `any(_looks_toolish(o) for o in payload if isinstance(o, dict))`: the element is a dict with key `"function"`, so `_looks_toolish` returns `True`. So it **is** added to `ambiguous`. Still good.

After careful analysis, **no vanishing occurs**. Every whole-content batch that contains at least one dict with a tool-like key is surfaced. Batches with no tool-like dicts are **not considered tool-shaped**, so they correctly remain as `text`. This is by design (strictness). So **F1 is NOT a bug**.

---

**F2 / Retried completion could bypass governance if authority depended on temperature or max_tokens**  
**SEVERITY**: LOW (theoretical)  
**LOCATION**: N/A (governance is temperature/max_tokens agnostic)  
**CONCRETE INPUT**: Any input that causes a retry.  
**WHY IT BREAKS A GUARANTEE**: If `govern_action` used `temperature` or `max_tokens` in its decision, a retried completion could get different authority.  
**WHETHER ANOTHER LAYER CATCHES IT**: `govern_action` (in `collaborator/governance.py`) **does not use** `temperature` or `max_tokens`. Authority is based solely on `session`, `intent`, and optional `leash`. The `msg` dict (which contains `finish_reason`, `temperature`, etc.) is **not passed** to `govern_action`. Only `intent` (from parsing) and session context matter.  
**FIX**: Not needed.  

---

**F3 / Partial execution of a mixed balanced/unbalanced block**  
**SEVERITY**: HIGH  
**LOCATION**: `collaborator/toolcall.py:184`  
**CONCRETE INPUT**:  
Content: `'<tool_call>{"name":"write_file","arguments":{"path":"a","content":"ok"}}<tool_call>...<tool_call{"name":"write_file","arguments":{"path":"b","content":"truncated...'`  
(i.e., one balanced block, then an unbalanced one starting with `<tool_call` without closing brace).  
**WHY IT BREAKS A GUARANTEE**: The parser will find two hits. The first is balanced → parsed → becomes an `intent`. The second is unbalanced → added to `ambiguous`. Then, when reconstructing `remaining`, it strips from `last` (end of first block) to `start` of second block, then appends from `end` (which is `len(content)`) onward. So the unbalanced part is stripped. This is correct.

But consider:
Content: `'{"name":"write_file","arguments":{"path":"a","content":"ok"}}...<tool_call{"name":"write_file","arguments":{"path":"b","content":"truncated...'`  
(no `<tool_call>` markers). This goes through the whole-content path. `_try_json` fails (not valid JSON), but `_text_looks_toolish` returns `True` (contains `"name"`), so the **entire content** is added to `ambiguous`, and `remaining = ""`. So the valid-looking prefix is **not run**—it’s treated as ambiguous. This is strictness, not a bug.

Now consider a structured call plus a truncated content block:
```python
msg = {
    "content": '<tool_call{"name":"write_file","arguments":{"path":"b","content":"truncated...',
    "tool_calls": [{"function": {"name": "write_file", "arguments": '{"path":"a","content":"ok"}'}}],
    "finish_reason": "length"
}
```
Structured calls are parsed first → `intents` gets one item. Then content blocks are parsed → one unbalanced hit → added to `ambiguous`. Then `remaining` is reconstructed → the unbalanced part is stripped. So we have one `intent` (run), one `ambiguous` (surfaced). Correct.

The critical case is in `_complete_actionable`:  
```python
if _is_truncated(msg) and not parsed.intents:
    grown_mt = ...
    continue
```
So if there **are** `parsed.intents`, it **does not retry**, even if there’s a truncated tail. That tail becomes `ambiguous` and is surfaced. So **no partial run**: completed calls run, truncated ones are ambiguous. **C3 holds**.

---

**F4 / finish_reason leaks into history**  
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/loop.py:370`  
**CONCRETE INPUT**: Any model response with `finish_reason`.  
**WHY IT BREAKS A GUARANTEE**: The `msg` dict returned by `client.complete` includes `finish_reason`. In `run_turn`, we do:  
```python
history.append({"role": "assistant", "content": assistant_text})
```
`assistant_text` comes from `_content(msg)`, which returns `msg.get("content") or ""`. So `finish_reason` is **not included in the history**. The history only contains `role` and `content`. So **no leak**.  

But what about `ScriptedClient`? Its `complete` returns queued messages, which may include `finish_reason`. But `run_turn` only uses `content` and `tool_calls` from the message. So **safe**.

---

**F5 / Budget growth unbounded for empty_retries=0**  
**SEVERITY**: LOW  
**LOCATION**: `collaborator/loop.py:220`  
**CONCRETE INPUT**: `empty_retries=0`, and a truncated response.  
**WHY IT BREAKS A GUARANTEE**: The loop runs `for attempt in range(max(1, empty_retries + 1))` → `range(1)`, so one attempt. If it’s truncated and has no intents, it sets `grown_mt` and `continue`s—but there are no more attempts. So it falls through to the final `return msg, parsed, _is_actionable(...)`. Since it’s truncated and has no intents, `_is_actionable` returns `False` (no intents, no ambiguous, and `content` is likely empty or clipped). So it returns `actionable=False`, and `run_turn` returns `stopped="empty"`. But the clipped call **is** in `ambiguous` (because the parser surfaces unbalanced spans), so `_is_actionable` returns `True`! Contradiction?

Wait: in the truncation case with no intents, the parser **does** add the unbalanced span to `ambiguous`. So `_is_actionable` returns `True` (because `parsed.ambiguous` is non-empty). So the turn **is** actionable, and the loop **returns immediately**, **without retrying**. But that’s wrong! The code says:
```python
if _is_truncated(msg) and not parsed.intents:
    grown_mt = ...
    continue
```
But if there’s an unbalanced span, `parsed.ambiguous` is non-empty, but `parsed.intents` is empty. So the condition `not parsed.intents` is `True`, so it **does retry**. And after retry, if it’s still truncated, same thing. After retries exhausted, it returns the last `msg, parsed`, and `_is_actionable` is `True` (because of `ambiguous`), so `actionable=True`. So `run_turn` will surface the ambiguous and stop with `stopped="final"`. Correct.

Budget growth: `grown_mt = min(_TRUNC_BUDGET_CAP, (grown_mt or base_mt) * 2)`. Capped at 65536. Retries bounded by `empty_retries+1`. So **C3 holds**.

---

**CERTIFICATION**

- **C1 (no silent loss)**: **CERTIFIED**. Every tool-shaped thing is either run (`intents`), surfaced (`ambiguous`), or is not tool-shaped (remains in `text`). The parser handles unbalanced spans, malformed batches, and truncated whole-content via `_text_looks_toolish`.
- **C2 (governance untouched)**: **CERTIFIED**. `govern_action` depends only on `session` and `intent`, not on `temperature`, `max_tokens`, or `finish_reason`. Retried completions produce the same `intent` objects, so governance is identical.
- **C3 (no partial/duplicate run + bounded)**: **CERTIFIED**. Completed calls in a mixed message are run; truncated tails are ambiguous. Budget growth is capped at 65536; retries are bounded. Valid batches run fully; mixed batches are not partially run.
- **C4 (no regression / interface)**: **CERTIFIED**. `finish_reason` is not included in history. `ScriptedClient` handles new kwargs. Unbalanced-span handling does not interfere with valid calls. Parser behavior for valid inputs is unchanged.

---

**STEELMAN**  
The changes comprehensively address the silent-loss risks of large or malformed tool calls by ensuring every tool-shaped fragment is either executed or surfaced. The retry logic for truncation and empty responses is carefully bounded and does not compromise governance, as authority decisions are based solely on the parsed intent and session context, not on model-generation parameters. The parser’s new handling of unbalanced spans and whole-content batches strictly adheres to the “surface, never drop” principle without regressing valid cases.

**VERDICT**  
**SOUND**. The implementation correctly enforces all certification claims with no critical or high-severity flaws. The highest-value fix is the parser’s treatment of unbalanced spans and malformed batches, which eliminates the silent-loss failure mode while preserving strictness.
