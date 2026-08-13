# LargeCall panel: qwen/qwen3-max

_cost=$0.0715 finish=stop_

**FINDINGS**

---

**F1 / Truncated whole-content batch with valid prefix silently drops valid calls**  
**SEVERITY**: CRITICAL  
**LOCATION**: `collaborator/toolcall.py:225` (in `parse_message`, whole-content JSON handling)  
**CONCRETE INPUT**:  
```json
{"content": "[{\"name\":\"write_file\",\"arguments\":{\"path\":\"ok.txt\",\"content\":\"hi\"}}, {\"not\":\"valid\"}"}
```
This is a **truncated** (unbalanced) whole-content JSON array — missing closing `]`. It passes the `_FENCE_RE` and `_try_json` fails, so it falls through to the whole-content block. Because it’s not a `dict` or a fully valid `list`, the code skips both branches. **Result**: `intents` remains empty, `ambiguous` is never appended (the `elif isinstance(payload, list)` guard fails because `payload` is `None`), and the entire content leaks into `remaining.text`.

**WHY IT BREAKS A GUARANTEE**: Violates **C1** — a valid tool call (`write_file`) vanishes with no trace in `intents`, `ambiguous`, or stripped `text` (it appears verbatim in `text`). Silent loss.

**ANOTHER LAYER CATCHES IT?**: No. `run_turn` sees only prose; govern_action never sees the intent.

**FIX**: In the whole-content block, after `payload = _try_json(candidate)`, if `payload is None` **and** `candidate.strip().startswith(("[", "{"))`, then check if any substring looks toolish. Simpler: treat any failed-parse of a bracket-starting string as ambiguous:
```python
if payload is None and candidate.strip()[:1] in "[{":
    ambiguous.append(candidate[:200])
    remaining = ""
```

---

**F2 / Unbalanced <tool_call> followed by valid <tool_call> causes span overlap → valid call dropped**  
**SEVERITY**: HIGH  
**LOCATION**: `collaborator/toolcall.py:197` (`keep` reconstruction loop)  
**CONCRETE INPUT**:  
```text
<tool_call>{"name":"a","args":{}}<tool_call>{"name":"b","args":{"x":"y"
```
First call is balanced; second is unbalanced (no closing brace). `_tool_call_tag_objects` returns:
- `(start1, end1, js1, True)`
- `(start2, len(content), js2, False)`

In the `keep` reconstruction:
```python
for start, end, _js, _balanced in hits:
    keep.append(content[last:start])
    last = max(last, end)
```
For the second (unbalanced) hit, `end = len(content)`. So `last = len(content)`. The first hit’s `end1` is < `len(content)`, but `max(last, end)` becomes `len(content)`. **Result**: The text between `end1` and `start2` is correctly excluded, but the **first call’s span is NOT removed from `remaining`** because `last` jumps past it. Worse: the first call **is parsed and added to `intents`**, but its markup **remains in `remaining`**, causing duplication or leakage.

Wait — actually, the bigger issue: the **first call is run**, but its `<tool_call>...<tool_call>` markers are only partially stripped. However, the **real bug** is that the **unbalanced span consumes everything to EOF**, so if a **valid call appears BEFORE an unbalanced one**, it's still parsed and run — which is correct per the comment in `_complete_actionable`: “complete calls already parsed in this same message must NOT be discarded”.

But what if the **unbalanced span starts first**?
Input:
```text
<tool_call>{"name":"a","args":{"x":"y"<tool_call>{"name":"b","args":{}}
```
Now:
- Hit1: unbalanced, `end = len(content)`
- Hit2: balanced, but `start2 > start1`, and when processing hit2, `last = max(0, len(content)) = len(content)`, so `content[last:start2]` is empty (since `start2 < len(content)` is false? No, `start2 < len(content)` is true, but `last = len(content) > start2`, so `content[len(content):start2]` is empty.

But more critically: **the second (valid) call may not even be found** because `_TOOL_CALL_MARKER_RE.finditer` will find both `<tool_call>` markers. So both are in `hits`.

However, during `keep` reconstruction:
- After hit1 (unbalanced, end=L), `last = L`
- For hit2 (start=S2 < L, end=E2 < L), `content[last:S2]` is `content[L:S2]` → empty
- Then `last = max(L, E2) = L`
- Final `keep.append(content[L:])` → empty

So the **valid second call’s markup is NOT stripped from `remaining`**, so it appears in `text`. But it **was run** (added to `intents`). So you get **both**: the call runs **and** its markup appears in the reply. That’s a **leak**, not a drop.

But **C1** is about vanishing — this is duplication/leakage, not silent loss. So maybe not C1-breaking.

However, consider: what if the **unbalanced span overlaps the valid one** due to greedy matching? Unlikely because each `<tool_call>` is matched independently.

**Conclusion**: This is a **LOW** issue (markup leakage), not silent loss. Not C1-breaking.

---

**F3 / finish_reason attached to message dict could leak into next turn if history reused incorrectly**  
**SEVERITY**: LOW  
**LOCATION**: `collaborator/model_client.py:60`  
**CONCRETE INPUT**: None — theoretical.  
**WHY**: `msg["finish_reason"] = ...` mutates the dict returned by the model server. If a caller reuses this dict in a subsequent `history`, the `finish_reason` would be sent back to the model.

**ANOTHER LAYER CATCHES IT?**: **YES**. `run_turn` **builds fresh assistant messages**:
```python
history.append({"role": "assistant", "content": assistant_text})
```
It never appends the raw `msg` dict. So `finish_reason` is never on the wire.

**FIX**: None needed. Claim C4 holds.

---

**F4 / Budget growth unbounded for empty_retries=0**  
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/loop.py:230`  
**CODE**: `for attempt in range(max(1, empty_retries + 1)):`  
If `empty_retries=0`, attempts = `[0]` → one try.  
But if that try is **truncated**, the code does:
```python
grown_mt = min(_TRUNC_BUDGET_CAP, (grown_mt or base_mt) * 2)
continue
```
Then the loop ends (only one iteration). So **no retry happens**. The truncated turn is returned as-is, and since it has no `intents`, `_is_actionable` checks `ambiguous` or `text`. If the truncation produced an unbalanced span, `ambiguous` is non-empty → actionable = True → **the turn is accepted**, and the clipped call is surfaced. That’s correct.

But what if the truncation produces **no ambiguous** (e.g., clipped mid-prose, not in a tool block)? Then `actionable=False`, and it returns `stopped="empty"`. Also correct.

So **C3 holds**: retries are bounded. Budget growth only occurs **within the retry loop**, and the loop runs at most `max(1, empty_retries+1)` times. And `grown_mt` is capped at 65536.

**No bug**.

---

**F5 / Whole-content single dict malformed → not surfaced**  
Already handled by existing code:
```python
elif isinstance(payload, dict):
    it = _coerce_call(...)
    if it: ...
    elif _looks_toolish(payload): ambiguous.append(...)
```
So if it’s a dict but not coercible, and looks toolish, it’s ambiguous. Good.

---

**CERTIFICATION**

- **C1 (no silent loss)**: **NOT-CERTIFIED**. F1 shows a truncated whole-content batch starting with `[` but not closing causes **silent loss** of valid prefix calls.
- **C2 (governance untouched)**: **CERTIFIED**. Retried completions are parsed identically; `govern_action` is called per-intent regardless of retry path; `finish_reason`/`max_tokens` never influence authority.
- **C3 (no partial/duplicate run + bounded)**: **CERTIFIED**. Guard `not parsed.intents` prevents retry from discarding completed calls; budget capped at 65536; retries bounded; mixed batches are not partially run (whole-content strictness preserved).
- **C4 (no regression / interface)**: **CERTIFIED**. `finish_reason` attachment doesn’t leak; `ScriptedClient` updated; valid calls before/after unbalanced spans are handled correctly (run + stripped or surfaced).

---

**STEELMAN**  
The fix robustly addresses the two live-found failure modes (empty streaks and truncation) without compromising governance. The parser now surfaces all tool-shaped artifacts, and the retry logic carefully avoids discarding completed work. The change is minimal, well-scoped, and covered by targeted tests.

---

**VERDICT**  
**SERIOUS_FLAWS** — due to **F1**, which breaks the core guarantee of no silent loss for a plausible model output (a large batch call truncated by max_tokens).  
**Highest-value fix**: In `parse_message`, when whole-content JSON parsing fails but the content starts with `[` or `{`, append it to `ambiguous` to prevent silent dropping.
