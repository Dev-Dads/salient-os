# LargeCall panel: google/gemini-2.5-pro

_cost=$0.1105 finish=length_

This is a certification review of the submitted reliability fix.

### FINDINGS

---
**ID:** C4-DATA-CORRUPTION-1
**TITLE:** Over-aggressive Text Stripping Corrupts Prose Containing Marker-like Strings
**SEVERITY:** MEDIUM
**LOCATION:** `collaborator/toolcall.py:236`
**CONCRETE INPUT OR BYPASS:**
Provide the following string as the message `content`:
`"The user asked about the literal string `<tool_call>`. I will now run a tool. <tool_call>{\"name\":\"read_file\",\"arguments\":{\"path\":\"a.txt\"}}</tool_call>"`

**WHY IT BREAKS A GUARANTEE:**
This breaks the correctness guarantee (part of C4) that prose is preserved. The parser correctly identifies the single valid `<tool_call>` block and extracts it as an intent. However, the logic for reconstructing the remaining text is flawed. After correctly assembling the text portions outside the tool call span, it performs a blanket `replace("<tool_call", "")`.

1.  The `keep` list will correctly contain `['The user asked about the literal string `<tool_call>`. I will now run a tool. ', '']`.
2.  `"".join(keep)` results in `'The user asked about the literal string `<tool_call>`. I will now run a tool. '`.
3.  `replace("<tool_call", "")` is then called on this string, which corrupts the legitimate prose into: `'The user asked about the literal string ``. I will now run a tool. '`.

The model's textual reply, which is returned to the user, is silently corrupted. This is a data-corruption bug and a regression.

**WHETHER ANOTHER LAYER CATCHES IT:**
No. This is a data corruption issue in the final prose output. No other layer inspects or validates the `text` field of the `ParseResult`. The tool call itself is parsed and governed correctly.

**FIX:**
Remove the over-aggressive `.replace()` calls. The span-based reconstruction is sufficient and correct. The `replace` calls are a flawed attempt at cleanup that introduces this bug.

Change `collaborator/toolcall.py:236-237`:
```python
# FROM:
remaining = ("".join(keep).replace("<tool_call", "").replace("</tool_call>", "")
             .strip(" >/\t\r\n"))
# TO:
remaining = "".join(keep).strip()
```
The existing `strip()` is sufficient to clean up leftover whitespace from around the tags.

---
### CERTIFICATION

**C1 (no silent loss):** CERTIFIED. Every tested input, including mixed valid/invalid batches, unbalanced tags, truncated content, and nested structures, results in a tool-shaped artifact being either run as an `intent`, surfaced in `ambiguous`, or (if not tool-shaped) left in `text`; nothing vanishes.

**C2 (governance untouched):** CERTIFIED. The retry mechanisms (temperature escalation and budget growth) do not alter the authority decision in `govern_action`. The `intent` object passed to `govern_action` does not contain `finish_reason` or `max_tokens`, and while a retry might produce a *different* intent, that new intent is still governed identically to how a first-attempt one would be. `ambiguous` items are never executed.

**C3 (no partial/duplicate run + bounded + strictness preserved):** CERTIFIED. The truncation retry is explicitly blocked by the `and not parsed.intents` guard in `loop.py:261`, preventing a duplicate run of an already-completed call. Clipped calls are parsed as `ambiguous` and never run partially. Budget growth (`min`) and retry counts (`max`) are correctly bounded. Strictness for whole-content batches (all-or-nothing) is preserved.

**C4 (no regression / interface):** NOT-CERTIFIED. While the `complete()` signature change is handled correctly by `ScriptedClient` and is backward-compatible for other callers, and `finish_reason` does not leak, the parser change introduces a `MEDIUM` severity data corruption bug (C4-DATA-CORRUPTION-1)
