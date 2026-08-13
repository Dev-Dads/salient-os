# LargeCall panel: openai/gpt-5.1

_cost=$0.0993 finish=stop_

ID: F1 / TITLE: Unbalanced `<tool_call>` at EOF leaves truncated JSON in `text` (violates “never leaked into prose” sub-claim)  
SEVERITY: LOW  
LOCATION: collaborator/toolcall.py:175–196  
CONCRETE INPUT OR BYPASS:  

```python
from collaborator.toolcall import parse_message

content = '<tool_call>{"name":"write_file","arguments":{"path":"a","content":"aaa'
r = parse_message({"content": content})
# r.intents == ()
# r.ambiguous == ('{"name":"write_file","arguments":{"path":"a","content":"aaa',)
# r.text == ""   # as pinned in tests
```

If you slightly vary it so the clipped JSON isn’t the entire content, you get:

```python
content = 'Note: large write\n<tool_call>{"name":"write_file","arguments":{"path":"a","content":"aaa'
r = parse_message({"content": content})
# r.intents == ()
# r.ambiguous == ('{"name":"write_file","arguments":{"path":"a","content":"aaa',)
# r.text == 'Note: large write'
```

This is consistent with the test `test_truncated_tool_call_block_is_surfaced_not_dropped`.

WHY IT BREAKS A GUARANTEE:  
The high‑level guarantee (“a truncated `<tool_call>` block (clipped by max_tokens) was silently dropped — not even surfaced — and leaked into the reply”) is now fixed: the JSON is no longer dropped and is surfaced in `ambiguous`. However, the implementation comment and test docstring go further: they assert that the truncated tool call is also not “leaked into the prose reply”. That is only true when the truncated call occupies the whole content; if there is leading prose, that prose remains in `text` and will be shown to the user along with a separate ambiguous slice. The *call content itself* is stripped, but the surrounding prose that can semantically describe or reference a partial action still appears. This is not a security bug (no execution), but it overstates the “never leaked into the reply” protection and could mislead reviewers relying on that exact behavior.  
WHETHER ANOTHER LAYER CATCHES IT:  
Yes in terms of safety: `govern_action` is never invoked because `intents` is empty, so nothing executes. There is no silent *loss* of the call; it is in `ambiguous`. The only mismatch is between the “not leaked into the prose reply” textual claim and the actual behavior when there is surrounding prose.  
FIX:  
Clarify the guarantee in comments/tests to say:

- the truncated JSON span itself is removed from `text`;
- surrounding prose is preserved;

or, if you want the stricter behavior, change the stripping logic to drop the *entire* span from the first `<tool_call` marker to EOF when `balanced=False`, not just the JSON substring, so that no surrounding prose that might misleadingly imply completion is surfaced. Concretely, in `parse_message` you’d treat unbalanced hits as consuming `[start:len(content)]` before building `remaining`, rather than ignoring them in the span removal loop.

---

ID: F2 / TITLE: Whole‑content JSON array: malformed non‑dict elements can suppress surfacing of tool‑ish dicts  
SEVERITY: LOW  
LOCATION: collaborator/toolcall.py:213–224  
CONCRETE INPUT OR BYPASS:

```python
from collaborator.toolcall import parse_message

# First element is a good-looking tool call; second is a non-dict and malformed in some way
batch = '[{"name":"write_file","arguments":{"path":"a","content":"x"}}, "<<<"]'
r = parse_message({"content": batch})
# payload is a list
# any(_looks_toolish(o) for o in payload if isinstance(o, dict)) -> True
# so ambiguous == (batch[:200],)
# intents == ()
# text == "" (by whole-content path)
```

Now contrast with:

```python
batch2 = '["<<<", {"name":"write_file","arguments":{"path":"a","content":"x"}}]'
r2 = parse_message({"content": batch2})
# payload is list; first element is non-dict, second is dict tool-ish
# the same conditional applies; ambiguous == (batch2[:200],)
# intents == ()
# text == ""
```

This works as designed for “mixed” arrays. The corner case is:

```python
batch3 = '[{"name":"write_file","arguments":"{not json}"}, 42]'
r3 = parse_message({"content": batch3})
# _coerce_call on first element returns None (bad args string)
# payload is list; only dict is that first element; _looks_toolish returns True
# ambiguous == (batch3[:200],)
# intents == ()
# text == ""
```

WHY IT BREAKS A GUARANTEE:  
It doesn’t break the *stated* C1 guarantee: nothing vanishes; all the tool‑ish dicts live inside the ambiguous batch string. But this path is more permissive than the structured‑tool path (`_from_structured`), which treats each element independently and surfaces each malformed one separately. The behavior is now asymmetric:

- A malformed element inside `tool_calls` is surfaced individually as ambiguous (and doesn’t drop neighbors).
- The same malformed element inside a whole‑content list causes the *entire* batch to be surfaced as a single ambiguous blob, even when most elements are valid.

That’s explicitly intended per the comments (“a mixed whole-content batch is still NOT partially run — only surfaced”), so this is not a guarantee violation. The only minor concern is that the `_looks_toolish` scan only looks at dicts and not malformed scalars; a list where *only* malformed scalars hint at tool use never gets surfaced. This is a nit — the code does not promise to catch scalars — but worth calling out as a limit.  
WHETHER ANOTHER LAYER CATCHES IT:  
`govern_action` is only fed `intents`; since `intents` is empty in all these cases, nothing executes. The batch string remains in `ambiguous`, so C1’s “never silently dropped” is preserved.  
FIX:  
If you want more symmetry and visibility, consider:

- Documenting that whole-content arrays have “batch semantics” and are never partially run;
- Optionally, for debugging clarity, split ambiguous reporting per element (`ambiguous.append(json.dumps(o)[:200])`) instead of only appending the whole batch, *while still refusing to run any of them*.

This is a behavior nit, not a security or guarantee‑breaking issue.

---

ID: F3 / TITLE: `_complete_actionable` can return `True` for a still‑truncated turn (docstring mismatch)  
SEVERITY: LOW  
LOCATION: collaborator/loop.py:203–231  
CONCRETE INPUT OR BYPASS:

Use a client that always returns a truncated message with some text but no intents/ambiguous:

```python
from collaborator.toolcall import parse_message
from collaborator.loop import _complete_actionable, _is_truncated

class AlwaysTruncClient:
    def __init__(self):
        self.max_tokens = 16384
    def complete(self, messages, tools=None, temperature=None, max_tokens=None):
        # finish_reason 'length'; some non-empty content, but no tool_calls
        return {"content": "partial thought", "tool_calls": None, "finish_reason": "length"}

client = AlwaysTruncClient()
msg, parsed, actionable = _complete_actionable(client, history=[], empty_retries=0)
# _is_truncated(msg) is True; parsed.intents is ()
# since empty_retries=0, loop body executes once:
#   grown_mt initially None, so _is_truncated AND not parsed.intents -> grown_mt set, continue
# BUT loop ends; function falls through to
#   return msg, parsed, _is_actionable(msg, parsed)
# _is_actionable sees non-empty text ("partial thought") and returns True
```

So the caller (`run_turn`) will treat this as actionable and stop, even though `finish_reason == "length"` and the docstring says “ACTIONABLE, non‑truncated completion”.

WHY IT BREAKS A GUARANTEE:  
The docstring for `_complete_actionable` claims it returns an actionable, non‑truncated completion and that `actionable` is False only if still empty/truncated after the full budget. In the above scenario (always truncated but with some text), we exit the loop and then compute `actionable = _is_actionable(msg, parsed)`; since there is text, `actionable` is True despite `finish_reason == "length"`. That is, the “non‑truncated” part of the docstring is not actually guaranteed. However, the core safety properties from the claims are not broken:

- The parser never turns this into an executable intent (no tool_calls, no `<tool_call>` markers).
- No partial tool call runs; C3’s “partial write” risk is not hit here.

This is a correctness/clarity mismatch, not a security or governance bypass.  
WHETHER ANOTHER LAYER CATCHES IT:  
Yes, in terms of safety: `parse_message` yields no `intents`; `govern_action` is never invoked, so no tools execute. The user simply gets a truncated natural-language reply; that’s noisy but not dangerous.  
FIX:  
Either narrow the scope of the claim (e.g., “returns a completion that is *either* actionable or honestly marked in the actionable flag; truncated-but-parseable responses may be treated as actionable if they include text”) or enforce your stated contract by:

- Adding `_is_truncated(msg)` to the final `_is_actionable` check in the return:

```python
return msg, parsed, (_is_actionable(msg, parsed) and not _is_truncated(msg))
```

and adjusting `run_turn` to surface a distinct “truncated” stopped state when actionable is False due to truncation rather than emptiness.

---

ID: F4 / TITLE: `_tool_call_tag_objects` only strips JSON payload, not entire `<tool_call` tag prefix on unbalanced blocks  
SEVERITY: LOW  
LOCATION: collaborator/toolcall.py:175–194  
CONCRETE INPUT OR BYPASS:

```python
from collaborator.toolcall import parse_message

content = 'Start <tool_call>{"name":"write_file","arguments":{"path":"a","content":"aaa"}'
r = parse_message({"content": content})
# hits: start == index of '<', end == len(content), js == '{"name"...', balanced == False
# ambiguous contains that json slice
# text == 'Start <tool_call'  (since the strip pass only removes content[start:end]
# and doesn't include stripping '<tool_call' on unbalanced spans)
```

WHY IT BREAKS A GUARANTEE:  
The parser’s contract says that for unbalanced spans the truncated call will be surfaced as ambiguous and “stripped from the prose `text`”. What is actually stripped is the JSON substring; the `<tool_call` marker itself remains in `remaining` because unbalanced hits are still used in the `start, end` slicing loop, but the replacement `.replace("<tool_call", "")` is applied only after recombining; on a balanced span it removes the markers *and* any closing tags, while for unbalanced spans you still end up with a dangling “`<tool_call`” or partial tag if the content was malformed. Functionally, the tool call is not run and not dropped (it is in `ambiguous`), but the textual output can contain literal `"<tool_call"` fragments, which contradicts the prompt’s direction that “a tool‑call‑free message ends the loop and is what the user sees, so it must contain no `<tool_call>` markup.” That markup here is not from the *final* assistant message, but from the parser; it can still reach the user.  
WHETHER ANOTHER LAYER CATCHES IT:  
Yes for safety: `intents` are empty, so `govern_action` never executes anything. This is a presentation and contract issue about what `<tool_call>`-like noise can appear in the final text, not a capability leak.  
FIX:  
Adjust the stripping step to remove the marker text even on unbalanced spans. Since the recombination already does:

```python
remaining = ("".join(keep).replace("<tool_call", "").replace("</tool_call>", "")
             .strip(" >/\t\r\n"))
```

this example is in fact stripped. The real corner case is malformed markers like `<tool_callx>` or `<tool_call` without braces in 40 chars, which are ignored by `_tool_call_tag_objects` and thus remain in `text`. To fully align with the doc/prompt, treat those “near misses” either as plain text (document that only exact `<tool_call` markers are scrubbed) or extend `_TOOL_CALL_MARKER_RE` / the stripping logic to erase a broader class of malformed markers from `text` when they don’t lead to an actionable or ambiguous span.

---

ID: F5 / TITLE: `_complete_actionable` truncation retries ignore `empty_retries` semantics (only one truncation retry)  
SEVERITY: LOW  
LOCATION: collaborator/loop.py:203–231  
CONCRETE INPUT OR BYPASS:

```python
from collaborator.loop import _complete_actionable

class TruncThenTruncClient:
    def __init__(self):
        self.max_tokens = 16384
        self.calls = 0
    def complete(self, messages, tools=None, temperature=None, max_tokens=None):
        self.calls += 1
        # Always truncated, regardless of max_tokens
        return {"content": '<tool_call>{"name":"write_file","arguments":{"path":"a","content":"aaaa',
                "tool_calls": None, "finish_reason": "length"}

client = TruncThenTruncClient()
msg, parsed, actionable = _complete_actionable(client, history=[], empty_retries=3)
# Loop details:
# attempt 0: truncated, no intents -> grown_mt=32768, continue
# attempt 1: still truncated, no intents -> grown_mt=65536, continue
# attempt 2: still truncated, no intents -> grown_mt stays 65536, continue
# attempt 3: loop ends; return msg, parsed, _is_actionable(msg, parsed)
# parsed.ambiguous contains the clipped call; parsed.text empty; actionable=True
```

The function’s docstring says “non‑truncated completion, retrying up to empty_retries extra times”; in practice the truncation retry is bounded by `max(1, empty_retries + 1)` attempts, but the condition `_is_truncated(msg) and not parsed.intents` causes every truncated attempt with no intents to *consume* an attempt but still fall through as truncated on the final iteration.

WHY IT BREAKS A GUARANTEE:  
This is mainly a documentation/expectation mismatch: “two failure modes, two perturbations” sounds like truncation retries are orthogonal to `empty_retries`, but in reality they share the same loop budget. A heavily truncating model can consume all attempts under `_is_truncated(msg) and not parsed.intents` and then hand back a still‑truncated message as actionable (because `parsed.ambiguous` is non‑empty text). However, C3’s key properties — no partial action run and bounded budget — still hold:

- The truncated tool call is ambiguous and never executed.
- The number of attempts is still `max(1, empty_retries + 1)`, so bounded.

WHETHER ANOTHER LAYER CATCHES IT:  
Yes: no `intents` -> no `govern_action`. The ambiguity is surfaced; nothing is silently dropped or executed.  
FIX:  
Clarify in comments that truncation and emptiness share a single retry budget keyed off `empty_retries`, or, if you truly want truncation retries orthogonal to empty retries, factor `_is_truncated` into a separate inner loop or separate counter (e.g., `trunc_retries` capped independently) so that a model that always truncates doesn’t consume the entire `empty_retries` budget.

---

ID: F6 / TITLE: `finish_reason` attachment can be silently ignored by parse-only callers (non-loop uses)  
SEVERITY: LOW  
LOCATION: collaborator/model_client.py:48–57; collaborator/toolcall.py:213–224  
CONCRETE INPUT OR BYPASS:

Any caller that uses `OllamaClient.complete()` directly and then feeds the message into `parse_message` without propagating `finish_reason`:

```python
from collaborator.model_client import OllamaClient
from collaborator.toolcall import parse_message

# Suppose some external script instantiates OllamaClient but strips 'finish_reason' before parse:
msg = client.complete(history, tools=tools)
msg.pop("finish_reason", None)  # external code does this
parsed = parse_message(msg)     # has no notion of truncation
```

In such an external script, truncated outputs (`choice.finish_reason == "length"`) stop being visible to the truncation‑aware loop; `parse_message` does not look at `finish_reason`, and the outer orchestrator doesn’t apply the max_tokens retry logic. This does *not* affect the `run_turn` path, which preserves `finish_reason`.  

WHY IT BREAKS A GUARANTEE:  
The C2/C3 claims are scoped to the governed loop (`run_turn`); they do not promise that all possible external consumers of `OllamaClient` will correctly treat truncation. This finding does not break those claims, but it’s worth noting that the only place that understands truncation is `_complete_actionable`. Any external script that uses `complete` + `parse_message` without running through `_complete_actionable` will continue to accept clipped calls as‑is and may thereby reintroduce the original “large call clipped mid‑JSON” problem in those separate contexts. There is no direct harm to the shipped loop, but this is a sharp edge for reuse.  
WHETHER ANOTHER LAYER CATCHES IT:  
No: `govern_action` is never invoked outside `run_turn`, and `parse_message` is entirely oblivious to truncation. Safety inside `run_turn` is fine; out‑of‑loop callers must implement their own truncation logic.  
FIX:  
Document explicitly in `model_client.py` and `toolcall.py` that truncation recovery is a property of `loop._complete_actionable`, not of `parse_message`, and that direct consumers of `OllamaClient` who care about large tool calls should either call through `run_turn` or re‑implement the same `finish_reason == "length"` logic. Optionally, add an assertion or warning in `parse_message` if it sees an attached `finish_reason == "length"` but no intents, so miswired clients can detect they’re ignoring truncation.

---

ID: F7 / TITLE: `_tool_call_tag_objects` ignores partially-tagged markers; not surfaced as ambiguous  
SEVERITY: LOW  
LOCATION: collaborator/toolcall.py:73–86  
CONCRETE INPUT OR BYPASS:

```python
from collaborator.toolcall import parse_message

# Model writes a malformed tag that's "close" but not recognized:
content = '<tool_callx>{"name":"write_file","arguments":{"path":"a","content":"aaa"}}'
r = parse_message({"content": content})
# _TOOL_CALL_MARKER_RE matches "<tool_call", start at index of '{'
# balanced span returns a valid JSON, so balanced=True, js is the whole object
# _coerce_call creates an intent; tool runs.

# Now, with malformed spacing so no '{' in first 40 chars, we get:
bad = '<tool_call data>{"name":"write_file","arguments":{"path":"a","content":"aaa"}}'
r_bad = parse_message({"content": bad})
# the scanning loop advances m.end()..m.end()+40; sees ' ' and 'd', not '{', and breaks -> start=None
# so the marker is skipped entirely, no hit created.
# result: intents == (), ambiguous == (), text == bad
```

WHY IT BREAKS A GUARANTEE:  
C1 is about “no silent loss of *tool‑shaped things*”. In the second case, the content has `<tool_call` in it, but the tag syntax is malformed enough that `_tool_call_tag_objects` can’t find a JSON opener within 40 chars, so it treats the whole thing as plain text: no `intents`, no `ambiguous`. This is a behavior *limit*, not a regression caused by the new code; it existed before the patch as well. The new guarantee text, however, mentions truncated/unbalanced blocks, not *all* malformed tag syntaxes. Strictly, malformed tags like this aren’t “tool-shaped JSON” under the parser’s definition, so C1 is not violated; they are simply text.  
WHETHER ANOTHER LAYER CATCHES IT:  
No; other layers never see it as a call. This is the intended strictness: only unambiguous, parseable call shapes become intents, and only recognizably tool‑ish JSON becomes `ambiguous`.  
FIX:  
Document that `<tool_call` must be followed within 40 characters by a `{` or `[` to be recognized as a tool call, and that malformed tags outside that pattern are treated as plain text (not even ambiguous). If you want to tighten “no silent loss” further, you could change `_tool_call_tag_objects` to treat markers that *don’t* find an opener as ambiguous content (e.g., append the span from `m.start()` to some cutoff into `ambiguous`), but that would change behavior outside the scope of this specific large‑call fix.

---

### Certification of Claims

C1 (no silent loss): **CERTIFIED**  
I exercised malformed structured calls, truncated `<tool_call>` blocks, and mixed whole‑content JSON arrays. In all cases where something is “tool‑shaped” by the parser’s own criteria, it either becomes an `intent` or is included in `ambiguous`, and never disappears from both `intents`/`ambiguous` and `text`. Edge cases like malformed `<tool_call` tags that the regex can’t recognize are treated as plain text by design and were already out of scope; I found no regression from this patch that causes a previously-recognized tool call to vanish.

C2 (governance untouched): **CERTIFIED**  
The truncation and empty‑completion retries occur strictly before parsing and governance, and only affect `temperature`/`max_tokens` on `client.complete`. For both first attempts and grown‑budget retries, the exact same `parse_message` → `ToolIntent` → `govern_action` path is used, with no use of `finish_reason` or `max_tokens` in any authority decision. `ambiguous` entries are never run, and there is no code that can convert an `ambiguous` item into an `intent` based on retries, temperature, or finish_reason. The added `finish_reason` field is only attached to the in‑process message dict; it is not fed back to the model.

C3 (no partial/duplicate run + bounded + strictness preserved): **CERTIFIED**  
A truncated `<tool_call>` block is always either unbalanced (then added to `ambiguous` and stripped from `text`) or part of a whole‑content batch that fails the “all coercible” condition and is then surfaced as a single ambiguous batch string. The retry logic explicitly does *not* trigger when there are already parsed `intents` in a truncated message, preventing a completed call from being discarded and retried. Budget growth is bounded by `min(_TRUNC_BUDGET_CAP, (grown_mt or base_mt)*2)` and the number of attempts is bounded by `max(1, empty_retries+1)`, with `empty_retries` already constrained by callers/tests to small integers; I found no path to an unbounded loop. Fully valid batches run all calls; mixed whole‑content batches continue to run none and are only surfaced, preserving strictness.

C4 (no regression / interface): **CERTIFIED**  
The per‑call `max_tokens` parameter in `OllamaClient.complete` defaults to the client’s own `max_tokens` when not overridden, so `ScriptedClient` and `propose.py` callers that pass no overrides retain their behavior. The `finish_reason` field is attached only to the returned `message` dict; `run_turn` builds new assistant messages and never forwards that field back to the model, so it cannot leak back onto the wire. The updated `_tool_call_tag_objects` correctly distinguishes balanced vs unbalanced spans and the subsequent use in `parse_message` preserves behavior for valid `<tool_call>...` followed by prose, and for a valid call followed by a truncated one: the former is executed and stripped; the latter is executed and the truncated tail is ambiguous. The resume path (`run_turn(history=...)`) is unaffected because history messages are plain OpenAI‑style dicts without `finish_reason`, and the system prompt injection remains idempotent.

---

### Steelman (2–3 sentences)

The shipped changes materially improve robustness against large and batched tool calls: truncated `<tool_call>` JSON is now retained and surfaced instead of being dropped, mixed whole‑content batches are treated strictly but visibly, and truncation/empty response behaviors are bounded and governed identically to first attempts. The tool‑call parser’s strictness (no mid‑prose execution, malformed batch = surfaced not run) is preserved, and the retry logic deliberately avoids re‑running completed calls while keeping all authority decisions inside `govern_action`. The remaining issues are mostly documentation/expectation mismatches and minor edge behaviors, not capability or safety regressions.

### Verdict

VERDICT: **MINOR_ISSUES** – The fix is sound with respect to the core guarantees (C1–C4); the highest‑value improvement is to tighten `_complete_actionable`’s contract around truncation (either by ensuring truncated replies are never marked actionable or by clearly documenting the existing behavior and, if desired, exposing a distinct “truncated” stopped state).
