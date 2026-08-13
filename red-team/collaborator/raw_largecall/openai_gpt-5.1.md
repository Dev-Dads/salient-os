# LargeCall panel: openai/gpt-5.1

_cost=$0.0866 finish=stop_

ID: F1 / TITLE: Unbalanced `<tool_call>` Can Leak Back Into `text` / SEVERITY: MEDIUM  
LOCATION: collaborator/toolcall.py:175-196  

CONCRETE INPUT OR BYPASS  
```python
content = 'Intro <tool_call>{"name": "write_file", "arguments": {"path": "a", "content": "x"}'
# Note: no closing brace, no second marker, then trailing prose
content += ' ...still talking about things here.'
r = parse_message({"content": content})
```

Observed behavior (given current code):

- `_tool_call_tag_objects` sees `<tool_call` and no balanced span, so it appends  
  `(m.start(), len(content), content[start:], False)`.
- In the first loop in `parse_message`, this hit is `balanced == False`, so:  
  `ambiguous.append(js[:200])` gets the entire tail (from `{` to end), including the trailing prose.
- In the second loop, `keep` gets `content[0:start]` and then `content[last:]` where `last` is set to `len(content)` (the unbalanced span end); so `remaining` becomes just `"Intro"` (stripped) and everything after the `{` (including the user's trailing prose) is removed.
- `text` is therefore `"Intro"`; `" ...still talking about things here."` is neither in `intents` nor in `ambiguous` nor in `text`.

WHY IT BREAKS A GUARANTEE  

- C1 promises: “Find any path where a call vanishes with NO trace in intents, ambiguous, OR text.”  
  Here the *tool-shaped* span is surfaced (good), but arbitrary trailing non-tool prose that happens to come after an unbalanced JSON span is silently stripped from the message.  
- That prose is not “tool-shaped” but is still user-visible assistant text. The guarantee describes “EVERY tool-shaped thing that is not RUN is either surfaced as ambiguous or (empty) surfaced as stopped='empty'” and “never silently dropped”; here we are silently dropping real text that is not clearly tool-related.
- This can materially alter what the user sees (e.g. “Intro <tool_call>{...unbalanced... next, I’ll explain the plan...” → only “Intro” shows up).

WHETHER ANOTHER LAYER CATCHES IT  

- No. `run_turn` consumes `parsed.text` as the reply when there are no `intents`. It has no way to recover the stripped prose; `ambiguous` contains only the JSON substring, not the surrounding text.  
- Governance is never invoked in this path.

FIX  

- When handling an unbalanced span, restrict the removal to just the JSON-like part, not “to end-of-content”. Options:
  - In `_tool_call_tag_objects`, instead of `hits.append((m.start(), len(content), content[start:], False))`, find a better `end` boundary. For example, stop at the next occurrence of `"</tool_call>"` or the next `<tool_call` (if present), or simply keep the end index at `start + len(js)` where `js` is just the JSON-ish region, and leave trailing prose intact.
  - Alternatively, in `parse_message`, when building `keep`, treat unbalanced spans specially: for `(start, end, js, balanced=False)` only remove `js` from `content` rather than `content[start:end]` if the text before the `{` might hold non-tool prose.  
- Minimal safe change: in `_tool_call_tag_objects`, for unbalanced hits return `(start, start + len(js), js, False)` and have `js` be limited to the substring starting at `{` and ending right before any clearly non-JSON punctuation or whitespace run; leave subsequent prose in `remaining`.


---

ID: F2 / TITLE: Truncation Retry Loop May Spin on Mixed Empty/Truncated Replies / SEVERITY: LOW  
LOCATION: collaborator/loop.py:203-236  

CONCRETE INPUT OR BYPASS  

Session with `empty_retries=2`, client returns:

1. Attempt 0: `{ "content": "", "tool_calls": null, "finish_reason": "length" }`  
2. Attempt 1: `{ "content": "", "tool_calls": null, "finish_reason": "length" }`  
3. Attempt 2: `{ "content": "", "tool_calls": null, "finish_reason": "length" }`

All of these parse as `parsed.intents == ()`, `parsed.text == ""`.

Current behavior:

- `base_mt` computed from `client.max_tokens` (say 16384).
- Attempt 0: no kwargs; `grown_mt` is `None`. Message is truncated, no intents → `grown_mt` becomes `32768`, `continue`.
- Attempt 1: kwargs include `temperature=_retry_temperature(1)` and `max_tokens=32768`; response truncated, parsed.intents empty → `grown_mt` becomes `65536`, `continue`.
- Attempt 2: kwargs include `temperature=_retry_temperature(2)`, `max_tokens=65536`; response truncated, parsed.intents empty → `grown_mt` clamps at `65536`, `continue`, but loop ends (`attempt` loop exhausted).
- Return `msg, parsed, _is_actionable(msg, parsed)`. `_is_actionable` sees `parsed.intents == ()`, `parsed.ambiguous == ()`, and no text → `False`.

This is logically correct but exposes a subtle mismatch relative to the documented “non-truncated” guarantee in the docstring: `_complete_actionable` claims to return “actionable, non-truncated” unless still empty/truncated, but its caller (`run_turn`) cannot distinguish “truncated-only” failure from “empty-only” failure; both become `stopped="empty"`. That makes user- and host-facing reporting for “clipped at cap” indistinguishable from reasoning emptiness, despite tests asserting different semantics for persistent truncation.

WHY IT BREAKS A GUARANTEE  

- The *intended* claim in C3 and the `_complete_actionable` docstring is that truncation is handled distinctly from emptiness; persistent truncation should surface its ambiguous snippet, not silently fall into the same “empty step” bucket.
- The tests in `TruncatedTurnGrowsBudgetAndRetries.test_persistent_truncation_surfaces_ambiguous_never_silently_lost` rely on the *parser* surfacing the truncated `<tool_call>` as ambiguous. However, in the constructed “no content, no markers, only finish_reason=length” case, there is nothing tool-shaped and no ambiguous snippet. The loop then emits an “empty response … times” message with `stopped="empty"`, which conflates truncation-caused loss with pure emptiness.
- This doesn’t violate *safety* but weakens the observability/differentiation promise in the comment: a host cannot tell whether the model hit a cap or merely refused to act.

WHETHER ANOTHER LAYER CATCHES IT  

- No; the parser has no signal for “this would have been a tool call but was entirely clipped at the server boundary”.
- Governance is not involved.

FIX  

- Tighten the contract between `_is_truncated` and `_complete_actionable` so that `run_turn` can distinguish “persistently truncated with no intents” from “empty”. For example:
  - Have `_complete_actionable` return a 4th flag `truncated_only`, or
  - Encode a sentinel in `msg` (e.g. `msg["_collab_truncated_only"] = True` when `_is_truncated` and there was no ambiguous or text), and have `run_turn` map that to a new stopped state such as `"truncated"` with a more accurate reply message.
- At minimum, adjust the docstring of `_complete_actionable` to stop promising “non-truncated completion” when persistent truncation with no tool markers is collapsed into the empty case. This keeps the safety story true while making the guarantee text honest.


---

ID: F3 / TITLE: `_DEFAULT_CLIENT_MAX_TOKENS` Mismatch Can Cause Unbounded Double-Growth Relative to Actual Model Cap / SEVERITY: LOW  
LOCATION: collaborator/loop.py:178-186, 208-214  

CONCRETE INPUT OR BYPASS  

A host wires a custom `client` object that:

- Exposes `max_tokens` attribute = `4096` (true model cap).
- Underlying model actually has a lower or different cap, or ignores the provided `max_tokens`, always truncating at, say, 2048 tokens and returning `finish_reason="length"` regardless.

Behavior:

- `base_mt = getattr(client, "max_tokens", _DEFAULT_CLIENT_MAX_TOKENS) or _DEFAULT_CLIENT_MAX_TOKENS` → `base_mt = 4096`.
- On first truncation, `grown_mt = min(65536, 4096 * 2) = 8192`; second truncation → `16384`, then `32768`, etc., up to `65536`.
- However, if the underlying backend ignores `max_tokens` (common for some “compat” wrappers) and keeps truncating at 2048, the code will keep marking these as truncated-with-no-intents and keep increasing `grown_mt` each time it happens, *even across turns* (because client’s own default is unchanged, but `_complete_actionable` restarts `grown_mt` per turn).

WHY IT BREAKS A GUARANTEE  

- C3 asserts “Budget growth is bounded (<= 65536) and retries are bounded (empty_retries).” That’s mathematically correct, but the *intent* is that throwing more budget at truncation is meaningful and that silent failure is avoided.  
- In environments where `max_tokens` is advisory or ignored, `_complete_actionable` becomes a no-op mitigation: the budget grows while the server’s real cap stays fixed. This can yield repeated truncated, tool-less replies and eventually `stopped="empty"`, even if the model was in fact trying to emit a huge `<tool_call>`; from the host’s perspective, the “large call clipped mid-JSON can complete” mitigation has silently degraded.
- This is a robustness and observability nit rather than a direct safety bug, but it does undercut the reliability fix being certified.

WHETHER ANOTHER LAYER CATCHES IT  

- No; the model client is assumed to honor `max_tokens`, and there is no runtime check that `choice["usage"]["completion_tokens"]` or similar matches expectations.
- Parser still works as designed, but never sees a completed call.

FIX  

- Document and, if possible, assert the expectation that `client.max_tokens` is a *real* output budget and that the server respects the `max_tokens` override; where the protocol supports it, inspect `resp.usage` in `OllamaClient.complete` and log or flag if truncation keeps happening despite raised caps.
- Optionally add a “hard stop” condition: if `_is_truncated(msg)` repeats for all attempts *and* `max_tokens` was already at `_TRUNC_BUDGET_CAP`, treat this as a distinct `stopped="truncated"` failure mode in `run_turn` so hosts can see “the model kept hitting its cap” rather than “empty response”.
- For custom clients, recommend or enforce implementing a `max_tokens` that is at least monotonic with the true cap, or expose a method to query the actual cap rather than assuming 16384 as fallback.


---

ID: F4 / TITLE: `_tool_call_tag_objects` May Mis-Treat `<tool_call>` Followed by Raw JSON Array as a Single Span / SEVERITY: LOW  
LOCATION: collaborator/toolcall.py:62-85, 175-196  

CONCRETE INPUT OR BYPASS  

```python
content = '<tool_call>[{"name":"write_file","arguments":{"path":"a","content":"x"}},' \
          '{"name":"write_file","arguments":{"path":"b","content":"y"}}] trailing prose here.'
r = parse_message({"content": content})
```

Behavior:

- `_TOOL_CALL_MARKER_RE` finds `<tool_call`.
- `_tool_call_tag_objects` scans after `m.end()` for the first `{` or `[`; here it finds `[` then `_balanced_span` over the full JSON array `[ ... ]`, returning `end` at the matching closing `]`.
- This gives one hit with `balanced=True`, covering from `<` up through `]`, not including trailing prose.
- `parse_message` then:
  - `_try_json(js)` returns the array; `objs` is the list of dicts; `_coerce_call` returns 2 valid intents.
  - `keep` is built: `content[0:start]` is `""`, then `content[last:end]` is the trailing `" trailing prose here."` and becomes `remaining` (after marker-strip).
- This is *correct*.

The potential mis-handling arises when the trailing prose includes another literal `{` or `[` in text:

```python
content = '<tool_call>[{"name":"write_file","arguments":{"path":"a","content":"x"}}] ' \
          'Here is an example JSON: {"foo": "bar"}'
```

- `_balanced_span` on the initial `[` will span both the tool-call array *and* the later example `{"foo": "bar"}` if the closing `]` is missing or malformed, since the algorithm operates by brace/bracket depth across the whole string, not bounded to a `</tool_call>`.

WHY IT BREAKS A GUARANTEE  

- In a malformed/unbalanced `<tool_call>` that actually *does* close, this behaves well. But if the JSON array is truncated or misnested and the following prose contains `{` or `[`, `_balanced_span` may treat the prose braces as part of the same JSON “span”, leading to:
  - A much larger `js` slice than intended.
  - The whole tail of content marked as `unbalanced` (if depth never returns to 0), added to `ambiguous` and stripped entirely from `text`.
- This is a variant of F1 and can cause “valid prose containing braces after a clipped tool call” to vanish from `text`, breaching the “no silent loss of tool-shaped things” ethos with collateral loss of non-tool text.

WHETHER ANOTHER LAYER CATCHES IT  

- No; same as F1, `run_turn` trusts `ParseResult.text`.

FIX  

- Bound `_balanced_span` to end either at depth 0 *or* at the next `</tool_call>` or `<tool_call` to avoid swallowing unrelated braces outside the current tag context.
- Alternatively, in `_tool_call_tag_objects`, when `end == -1`, limit the unbalanced `js` to `content[start:m2.start()]` where `m2` is the next `<tool_call` marker, leaving later prose intact.


---

ID: F5 / TITLE: `finish_reason` Attachment Could Be Reused if Upstream Message Dict Is Mutated in Place / SEVERITY: LOW  
LOCATION: collaborator/model_client.py:48-59; collaborator/loop.py:263-276  

CONCRETE INPUT OR BYPASS  

A host-supplied client that:

- Reuses the same dict object for multiple replies (e.g. returns a reference it later mutates for streaming or logging).
- `OllamaClient.complete` currently does `msg = choice.get("message") or {}; if isinstance(msg, dict) and "finish_reason" not in msg: msg["finish_reason"] = choice.get("finish_reason")`.

If a host or test code later reuses `msg` (e.g. puts it back into a queue for `ScriptedClient` or uses it as a template), that `finish_reason` key will remain present, even though the new semantic content is different.

WHY IT BREAKS A GUARANTEE  

- C2 asserts that `finish_reason` never enters an authority decision and never leaks onto the wire; both are true. But the code *does* mutate the message dict from the underlying model JSON, and that dict is not defensively copied.
- In the contrived “dict reused” scenario, later calls to `_is_truncated` will consult a stale `finish_reason="length"` from a previous completion, potentially causing `_complete_actionable` to treat a non-truncated reply as truncated and spuriously grow `max_tokens` and continue.
- Governance remains unaffected, but this could cause unexpected retries and confusing behavior in scripted tests or custom clients that reuse `message` objects.

WHETHER ANOTHER LAYER CATCHES IT  

- No; `_is_truncated` trusts `msg["finish_reason"]`.
- Parser is oblivious.

FIX  

- In `OllamaClient.complete`, always copy the message dict before decoration:

  ```python
  raw_msg = choice.get("message") or {}
  msg = dict(raw_msg) if isinstance(raw_msg, dict) else {}
  if "finish_reason" not in msg:
      msg["finish_reason"] = choice.get("finish_reason")
  return msg
  ```

- Similarly, encourage or enforce that `ScriptedClient` returns fresh dicts (it currently pops from `_queue` and returns the stored object directly). Document that queued messages should be treated as immutable; optionally wrap `pop(0)` with `copy.deepcopy` in tests to prove there’s no aliasing hazard.


---

### CERTIFICATION LINES

C1 (no silent loss): **NOT-CERTIFIED** – unbalanced `<tool_call>` spans can strip trailing non-tool prose from `text` (F1/F4), so some user-visible content can still vanish.  

C2 (governance untouched): **CERTIFIED** – all tool intents, including those from grown-budget or raised-temperature retries, flow through `govern_action` unchanged; `ambiguous` items are never run, and neither `finish_reason` nor `max_tokens` participates in any authority decision.  

C3 (no partial/duplicate run + bounded + strictness preserved): **CERTIFIED** – a clipped call is never executed (only surfaced as ambiguous), already-complete calls in a mixed truncated turn run once and are not discarded on retry, max_tokens growth is bounded by `_TRUNC_BUDGET_CAP` and retries by `max(1, empty_retries+1)`, and whole-content batches still either run all valid calls or surface as ambiguous when mixed.  

C4 (no regression / interface): **MINOR-NOT-CERTIFIED** – ScriptedClient and propose/resume paths are functionally intact, but the in-place `finish_reason` attachment plus unguarded dict aliasing is a minor interface hazard, and the unbalanced-span handling can, in malformed cases, co-scope valid prose with a truncated call and drop that prose from `text`.  


### STEELMAN

The shipped change substantially strengthens reliability around large and batched tool calls: unbalanced `<tool_call>` blocks are now surfaced instead of silently dropped, mixed whole-content JSON batches no longer vanish, and truncation and “reasoning-only” emptiness are actively mitigated via bounded budget and temperature retries. The authority seam remains clean: all execution still gates through `govern_action`, and the new metadata (`max_tokens`, `finish_reason`) is correctly treated as transport detail, not as part of any capability decision. The remaining issues are primarily around edge-case parsing of malformed or clipped content and subtle interface hygiene, not around executing unintended tools or bypassing governance.

### VERDICT

VERDICT: **MINOR_ISSUES** – the core safety and authority guarantees hold, and the reliability improvements do what they claim for well-formed large/batched tool calls; the highest-value fix is to tighten `_tool_call_tag_objects` and the `remaining` reconstruction so that unbalanced spans cannot strip or co-swallow trailing non-tool prose, eliminating the last class of “vanishing” content.
