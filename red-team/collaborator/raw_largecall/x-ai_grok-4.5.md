# LargeCall panel: x-ai/grok-4.5

_cost=$0.0934 finish=stop_

# Adversarial Review: Large/Batched Tool-Call Reliability Fix

## Findings

### F1 / truncated whole-content JSON array vanishes from intents, ambiguous, AND text / MEDIUM / `collaborator/toolcall.py:213-232`

**Concrete input:**
```python
parse_message({"content": '[{"name":"write_file","arguments":{"path":"a","content":"x"}},{"name":"write_file","arguments":{"path":"b","content":"'})
# finish_reason would be "length"; content is a clipped JSON array (no <tool_call> markers)
```

**Why it breaks a guarantee:** C1 says every tool-shaped thing that is not RUN is in `ambiguous` or `text` (or empty→`stopped="empty"`). Path:
1. No structured `tool_calls`.
2. No `<tool_call` markers → `_tool_call_tag_objects` returns `[]`; the unbalanced-span path never fires.
3. Whole-content branch: `_try_json(candidate)` fails on truncated JSON → `payload is None`.
4. Neither the `dict` nor `list` arm runs. `ambiguous` stays empty. `remaining` stays the full clipped content → it lands in `text`.

So the call does **not** vanish from the triple entirely — it remains in `text`. But it is **not** classified as tool-shaped/`ambiguous`. The human sees raw half-JSON in the reply with no `[ambiguous — NOT run]` marker. The truncation-retry path in `_complete_actionable` only continues when `_is_truncated and not parsed.intents`. Here `parsed.intents` is empty and if `finish_reason=="length"`, it **does** retry (good). After budget exhaustion, `_is_actionable` is True because `parsed.text` is non-empty → `stopped="final"` with garbage prose, not a clear “clipped tool call” surface.

**Another layer:** Truncation retry may still grow budget and recover on a later attempt. If every attempt is clipped the same way, the loss-of-classification stands. `govern_action` never sees it (correct — nothing to run). This is a **real C1 gap for the non-tag whole-content truncated form**, weaker than total vanish but weaker than the claimed “surfaced as ambiguous” contract. The pinned tests only cover truncated `<tool_call>` tags and well-formed mixed batches.

**Fix:** If `finish_reason=="length"` (parser would need the flag, or loop post-check) and whole-content looks like an incomplete JSON object/array (`strip` starts with `{`/`[` and `_try_json` fails), append `candidate[:200]` to `ambiguous` and clear it from `text`. Alternatively, in `_complete_actionable`, treat truncated + no intents + text-that-looks-like-json as non-terminal ambiguous.

---

### F2 / whole-content list of dicts where NONE `_looks_toolish` still drops the batch with no ambiguous / LOW / `collaborator/toolcall.py:222-232`

**Concrete input:**
```python
parse_message({"content": '[{"foo":1},{"bar":2}]'})
```

**Why:** `all(g is not None for g in got)` is False; `any(_looks_toolish(...))` is False → nothing added to intents or ambiguous; full JSON remains in `text`. C1 probe asked for “whole-content list of dicts none of which `_looks_toolish`”. Strictly, these are not tool-shaped, so “not silent loss of a tool call” holds. Stated concern was “batch the model MEANT as calls”. **Non-finding against C1** if we require tool-shape; note only.

**Another layer:** N/A. Nit / non-goal.

---

### F3 / unbalanced `<tool_call>` + valid call: span-to-EOF can shadow a later valid call’s prose handling, but does not drop the valid call if it appears FIRST / LOW (blocked) / `collaborator/toolcall.py:80-91,194-201`

**Concrete input:**
```text
<tool_call>{"name":"write_file","arguments":{"path":"a","content":"x"}}</tool_call>
prose here
<tool_call>{"name":"write_file","arguments":{"path":"b","content":"yyyy
```

Hits: (1) balanced valid, (2) unbalanced to EOF. Valid → intent; unbalanced → ambiguous; strip uses `last = max(last, end)` so prose between is kept, truncated tail stripped. **C1 holds.**

Reverse order (truncated marker first, then more text that contains another `{...}` without a new marker): second call never scanned past EOF span — but there is no second marker content after EOF. If two markers and first is unbalanced, first span is `(m1.start, len(content))` and second marker is still found by `finditer` on full content:

```text
<tool_call>{"name":"write_file","arguments":{"path":"a","content":"aa
<tool_call>{"name":"read_file","arguments":{"path":"b"}}
```

Both markers match. First unbalanced → js from first `{` to EOF (includes second tag). Second: may find `{` of read_file, balance it → valid intent **and** ambiguous blob that duplicates/superset. Valid call still runs; truncated not lost. Over-strip: `last = max(last,end)` → remaining empty. **No vanish. No double-run of truncated.** Acceptable.

**C4 span-overlap concern:** VALID call followed by prose — balanced end stops at closing brace; prose preserved. **Blocked attack.**

---

### F4 / `grown_mt` doubles only on truncation continue; temperature still applied on truncation retries / LOW (nit) / `collaborator/loop.py:218-236`

Truncation retries set both `temperature=_retry_temperature(attempt)` (for attempt>0) and grown `max_tokens`. C2 asks whether temp/budget change authority — they do not enter `govern_action`. **Non-finding for C2.** Orthogonal coupling (temp raised when only budget needed) is a quality nit, not a guarantee break.

---

### F5 / retry bound `max(1, empty_retries+1)` and budget `min(65536, …)` / NON-FINDING / `collaborator/loop.py:218-234,171-175`

| `empty_retries` | attempts |
|-----------------|----------|
| 0 | 1 |
| 1 | 2 |
| 3 | 4 |

Growth: `base_mt=16384` → 32768 → 65536 → 65536. Cap holds. **C3 bounds certified.**

---

### F6 / partial write / duplicate action on truncation retry / NON-FINDING / `collaborator/loop.py:227-234` + parser

- Clipped `<tool_call>` → `balanced=False` → ambiguous only → never `_coerce_call` success → never `govern_action`.
- Guard `not parsed.intents` prevents discarding a complete sibling call; fall-through runs complete intents once; no second complete of same message.
- Retry replaces message; does not re-execute prior turn’s intents (prior truncated-only attempt `continue`s without appending history).

Pinned test `test_truncation_never_discards_a_complete_call_parsed_in_the_same_turn` matches. **C3 partial/duplicate: blocked.**

---

### F7 / mixed whole-content batch strictness / NON-FINDING / `collaborator/toolcall.py:222-232`

Mixed batch → no intents, one ambiguous. All-valid → all intents. **C3 strictness preserved.**

---

### F8 / governance path for retried completion / NON-FINDING / `loop.py:run_turn` + `govern_action`

Trace (grown-budget retry):
1. `_complete_actionable` returns `(msg, parsed, True)` after retry with `max_tokens=grown_mt`.
2. `run_turn` appends assistant text from content/intents only — **not** `finish_reason`.
3. `for intent in parsed.intents: govern_action(session, intent, importance=..., risk=...)`.
4. No `max_tokens`, `temperature`, or `finish_reason` parameters on `govern_action`.
5. `ambiguous` only extended onto `TurnResult` / TOOL RESULTS line — never iterated as intents.

Authority is byte-identical to a first-shot parse of the same intent bytes. **C2 certified.**

---

### F9 / `finish_reason` on wire / resume / ScriptedClient / NON-FINDING / `model_client.py` + `loop.py`

- `history.append({"role":"assistant","content": assistant_text})` — fresh dict, no `finish_reason`, no `tool_calls` passthrough of raw msg.
- `ScriptedClient.complete(..., max_tokens=None)` accepts kwargs; records `max_tokens_seen`.
- `propose.py` / callers using `complete(messages)` unchanged (defaults).
- Resume uses `history=` built the same way.

**C4 interface: blocked attacks.**

---

### F10 / `_EmptyAtLowTempClient` in tests lacks `max_tokens` kwarg / LOW (test-only) / `tests/test_collaborator_loop.py` (~`_EmptyAtLowTempClient.complete`)

```python
def complete(self, messages, tools=None, temperature=None) -> dict:
```

Production loop now calls `complete(..., max_tokens=...)` only when `grown_mt is not None`. Empty-temp tests never set `grown_mt`, so still work. If a future test combines empty+truncation against this helper, it TypeErrors. **Not a production C4 break**; ScriptedClient was updated correctly.

---

### F11 / persistently truncated with only ambiguous: actionable True → `stopped="final"` / LOW (stated non-goal adjacent) / `loop.py:237-240, run_turn`

Docstring says truncated-but-parseable surfaces ambiguous as actionable; run_turn with no intents returns `stopped="final"`. Out of scope notes already call ambiguous-only `stopped="final"` pre-existing. **Not rated as regression.**

---

### F12 / nested braces in clipped span / NON-FINDING / `toolcall.py:_balanced_span` + unbalanced branch

Clipped content with braces inside strings: balance never returns; unbalanced hit to EOF; `ambiguous.append(js[:200])`; stripped from text. **C1 holds for tag form.**

---

## Certification lines

| Claim | Verdict | One sentence |
|-------|---------|--------------|
| **C1** | **NOT-CERTIFIED** | Truncated **tag**-shaped and mixed **whole-content** batches are surfaced, but a **truncated whole-content JSON** (no `<tool_call>`, `_try_json` fails) never enters `ambiguous` and is only residual `text` / possible `final` garbage — silent loss of *tool-call classification* (F1). |
| **C2** | **CERTIFIED** | Retries only change client sampling kwargs; intents still only execute via `govern_action`; `ambiguous` never runs; `finish_reason`/budget/temp never touch the seam. |
| **C3** | **CERTIFIED** | Clipped calls are ambiguous not executed; complete+clipped same message runs complete once; budget ≤65536; attempts = `max(1, empty_retries+1)`; all-valid batches run all; mixed batches run none. |
| **C4** | **CERTIFIED** | ScriptedClient/OllamaClient signatures are backward-compatible; assistant history is rebuilt without `finish_reason`; balanced spans do not swallow following prose; structured/content-json paths unchanged. |

---

## STEELMAN

The change correctly targets the two real silent-loss bugs (unbalanced `<tool_call>` dropped by the old balancer, and mixed whole-content arrays dropped when any element failed coerce) and pairs them with a bounded truncation retry that refuses to throw away already-complete intents in the same clipped message — the subtle failure mode that naive “always retry on length” would introduce. Governance stays out of the reliability path by construction: parser classification and `run_turn`’s intent loop are unchanged as authority boundaries. For the dominant model path (`<tool_call>` / structured calls), the no-silent-loss + no-partial-run story is tight and well pinned by tests.

---

## VERDICT

**MINOR_ISSUES** — Core tag/batch/retry/governance guarantees hold under adversarial probe; the residual hole is truncated **marker-free** whole-content JSON never promoted to `ambiguous` (F1), so C1 cannot be fully certified.

**Single highest-value fix:** In `parse_message` whole-content branch (or in `_complete_actionable` when `finish_reason=="length"` and `not intents`), if content strips to a `{`/`[` prefix and `json.loads` fails, push `candidate[:200]` into `ambiguous` and clear it from `text` so clipped hermes-style / bare-JSON large calls get the same surface contract as clipped `<tool_call>` blocks.
