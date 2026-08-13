# LargeCall panel: x-ai/grok-4.5

_cost=$0.0877 finish=stop_

# Adversarial Review: Large/Batched Tool-Call Reliability Fix

## Findings

### F1 / Truncation retry discards a completed sibling call / MEDIUM  
**Location:** `collaborator/loop.py` `_complete_actionable` (~lines with `if _is_truncated(msg): ... continue`)

**Concrete input:**
```python
msg = {
  "content": '<tool_call>{"name":"write_file","arguments":{"path":"a.txt","content":"ok"}}</tool_call>'
             '<tool_call>{"name":"write_file","arguments":{"path":"b.txt","content":"AAAA',
  "finish_reason": "length",
}
```
Parse yields: intent `write_file(a.txt)` + ambiguous truncated second call. `_is_actionable` is True, but truncation is checked **first** and `continue`s — the valid intent is never returned to `run_turn` / `govern_action`. On retry the model may emit only the second call (or something else); `a.txt` never runs from this completion.

**Why it breaks a guarantee:** C3 says “a completed call plus a retry” must not lose work / must not partially mishandle; C1’s “not run → ambiguous or empty” is satisfied for the clip, but a **fully valid** call is discarded solely because a sibling was truncated. That is silent loss of a runnable intent from an obtained completion (retry may not reproduce it).

**Another layer?** No — `govern_action` never sees the dropped intent. Parser correctly extracted it.

**Fix:** On truncation, if `parsed.intents` is non-empty, either (a) return that completion and surface remaining ambiguous (accept partial progress), or (b) stash intents and merge with the retry. Do not `continue` away runnable intents.

---

### F2 / Whole-content list where no element `_looks_toolish` still vanishes / LOW (nit / edge)  
**Location:** `collaborator/toolcall.py` `parse_message` whole-content list branch

**Concrete input:**
```json
[{"foo": 1}, {"bar": 2}]
```
as entire content. `all(g is not None)` is false; `any(_looks_toolish(...))` is false → nothing in intents, ambiguous, or stripped specially; full JSON remains in `text`.

**Why:** C1 says “tool-shaped” things must not vanish. These are not tool-shaped by `_looks_toolish`, so this is largely a **stated non-goal** / strictness edge. Pre-fix also left them as text. Not a regression.

**Another layer?** N/A — not tool-shaped.

**Fix:** Optional only: if whole content is a JSON array of objects, always surface as ambiguous. Not required for the shipped claim if “tool-shaped” is defined by `_looks_toolish`.

---

### F3 / Unbalanced span swallows trailing prose after a truncated marker / LOW  
**Location:** `collaborator/toolcall.py` `_tool_call_tag_objects` unbalanced branch: `hits.append((m.start(), len(content), content[start:], False))` + strip loop

**Concrete input:**
```text
Intro.
<tool_call>{"name":"write_file","arguments":{"path":"a","content":"aa
and then the model also said: please check disk.
```
Unbalanced hit spans to EOF → ambiguous gets the JSON prefix; trailing prose is stripped from `text` (not in intents/ambiguous/text as readable reply prose). The truncated call is in `ambiguous` (C1 OK for the call). Prose is lost from `text`.

**Why:** Mild C1/C4 tension for **non-tool** text after an unclosed marker. Truncation at max_tokens usually means little meaningful prose after the clip; still a real strip behavior.

**Another layer?** Surfacing ambiguous preserves the call; prose loss is UX.

**Fix:** Prefer spanning unbalanced only through the JSON-ish run, or keep a copy of post-marker prose when finish_reason is length (hard). Acceptable as LOW.

---

### F4 / Nested / second marker inside unbalanced span / LOW  
**Location:** same strip loop: `last = max(last, end)` with unbalanced `end = len(content)`

**Concrete input:** two markers where the first never closes; second marker appears later in the same content. First unbalanced hit consumes to EOF; second hit may still be found by `finditer` and processed for ambiguous, but strip already consumed everything — OK for ambiguous append order; possible **duplicate** ambiguous slices or odd overlap. Valid-first + truncated-second is OK when first is balanced (F1 is the loop issue, not parser span overlap).

**Valid `<tool_call>` + prose:** balanced end stops at closing brace; prose remains — **C4 parser claim holds**.

**Another layer?** N/A.

---

### F5 / `finish_reason` on message dict — wire leak / NON-FINDING  
**Location:** `model_client.py` attaches `finish_reason` on returned msg; `run_turn` builds `history.append({"role":"assistant","content": assistant_text})` with **only** role+content.

**Bypass attempt:** Subsequent `client.complete(history)` — history has no `finish_reason`. ScriptedClient/propose path: `complete(..., max_tokens=None)` default OK.

**Blocked.** C4 wire claim holds.

---

### F6 / Grown max_tokens / temperature alter authority / NON-FINDING  
**Trace:** `_complete_actionable` → `parse_message` → `run_turn` → `for intent in parsed.intents: govern_action(session, intent, importance=..., risk=...)`. No `finish_reason`, `max_tokens`, or temperature enters `govern_action`. Ambiguous never iterated as intents. Authority byte-identical for retried completions.

**Blocked.** C2 holds. (F1 is loss of intent before govern, not extra authority.)

---

### F7 / Partial write of clipped call / duplicate run / budget bounds / NON-FINDING (with F1 caveat)  
- Clipped `<tool_call>` → `balanced=False` → ambiguous only → not in `parsed.intents` → no execute. **No partial write.**  
- Retry replaces prior completion (empties/truncations not appended to history until success). **No double run of the same completion.**  
- `grown_mt = min(65536, (grown_mt or base_mt) * 2)` → bounded.  
- `range(max(1, empty_retries + 1))` → empty_retries ∈ {0,1,3} → 1 / 2 / 4 attempts.  
- Valid whole-content batch: unchanged `all(g is not None)` → all run.  
- Mixed batch: `elif any(_looks_toolish)` → ambiguous only, **no partial run.**

F1 is the only related hole (drop valid sibling on trunc), not partial execute of clipped JSON.

---

### F8 / ScriptedClient / propose / resume / NON-FINDING  
ScriptedClient accepts `max_tokens=None`. propose.py not in delta; `complete` kwargs optional. Resume uses same `run_turn` + fresh assistant turns. **No break found.**

---

### F9 / Last truncated-but-actionable honored / intentional  
After budget, `return msg, parsed, _is_actionable(msg, parsed)` — persistent trunc with ambiguous returns actionable True → `stopped=final` with ambiguous surfaced (pinned test). Matches claim; out-of-scope note about ambiguous-only `final` acknowledged.

---

### F10 / Marker without `{`/`[` within 40 chars / NON-FINDING for C1  
`<tool_call` with no JSON start → no hit → may remain in text after replace of literal `<tool_call`. Not a closed tool-shaped payload; pre-existing.

---

## Certification

| Claim | Status | Sentence |
|-------|--------|----------|
| **C1** | **NOT-CERTIFIED** | Truncated/malformed single calls and mixed toolish batches are surfaced, but a **valid intent co-emitted with a truncated sibling is dropped on truncation retry** (F1), which is silent loss of a tool-shaped runnable call with no trace in the turn that obtained it. |
| **C2** | **CERTIFIED** | Retries and `finish_reason`/`max_tokens` never enter `govern_action`; ambiguous is never executed; authority path is unchanged for whatever intents actually reach the loop. |
| **C3** | **NOT-CERTIFIED** | No partial execute of clipped JSON, no double-run from history replay, bounds and batch strictness hold — but truncation `continue` **drops already-parsed complete calls** (F1), violating “no lost completed work / clean retry” spirit of C3. |
| **C4** | **CERTIFIED** | Optional kwargs, ScriptedClient, assistant history without `finish_reason`, and balanced-span validity+prose behavior hold; unbalanced EOF strip is a minor prose nit, not an interface break. |

---

## STEELMAN

The fix correctly targets the real failure mode: clipped `<tool_call>` JSON and mixed whole-content arrays used to disappear from both execution and ambiguous. Parser unbalanced spans + toolish batch surfacing, paired with bounded max_tokens growth on `finish_reason==length`, are the right levers and are well pinned by tests. Governance remains untouched; clipped calls never become intents.

## VERDICT

**MINOR_ISSUES** — Core no-silent-drop for the clipped-only and mixed-batch cases is real and tested; the highest-value fix is **F1: do not discard non-empty `parsed.intents` when `finish_reason==length`** (return and govern them, or merge across retry).
