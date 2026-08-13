# LoopFix panel: x-ai/grok-4.5

_cost=$0.0855 finish=stop_

# Adversarial Review: Empty-Completion Fix

## Findings

### F1 / Whitespace-only content treated as non-actionable (correct) — NON-FINDING
**SEVERITY:** n/a (blocked attack)  
**LOCATION:** `collaborator/loop.py` `_is_actionable`  
**PROBE:** `content="   \n\t  "`, `tool_calls=None`  
**RESULT:** `(parsed.text or _content(msg)).strip()` → `""` → not actionable → retry or `stopped="empty"`. Cannot become `stopped="final"`.  
**OTHER LAYER:** N/A — this *is* the gate.  
**FIX:** None.

### F2 / Empty `tool_calls=[]` list — NON-FINDING
**SEVERITY:** n/a (blocked)  
**LOCATION:** `toolcall.py` `_from_structured` + `_is_actionable`  
**PROBE:** `{"content": "", "tool_calls": []}`  
**RESULT:** `_from_structured` iterates empty list → no intents/ambiguous; text empty → not actionable. Same path as `tool_calls=None`.  
**OTHER LAYER:** Parser returns empty intents independently.  
**FIX:** None.

### F3 / `content=None` with no tools — NON-FINDING
**SEVERITY:** n/a  
**LOCATION:** `_content` / `parse_message`  
**PROBE:** `{"content": None, "tool_calls": None}`  
**RESULT:** `_content` → `""`; `parse_message` → `content=""`; not actionable.  
**FIX:** None.

### F4 / Path to `stopped="final"` with empty reply after actionable gate — NON-FINDING (boundary OK)
**SEVERITY:** n/a  
**LOCATION:** `run_turn` after `_complete_actionable` returns actionable  
**PROBE:** Only reaches `if not parsed.intents: return ... stopped="final"` when `_is_actionable` was True, which requires non-empty stripped text OR intents OR ambiguous.  
**Edge:** Ambiguous-only (`tool_calls` malformed) is actionable → continues → `not parsed.intents` → `stopped="final"` with whatever text remains (possibly empty) but `ambiguous` populated. Claim C1 scopes “empty completion (no content, no tool_calls, no ambiguous)”; ambiguous-only is explicitly “TRIED something” and is a legitimate terminal surface, not a silent no-op.  
**FIX:** None for C1. Optional nit: ambiguous-only + empty text could use a clearer `stopped` than `"final"` — **stated non-goal / pre-existing shape**, not introduced by this fix.

### F5 / Negative `empty_retries` still one attempt — LOW (nit)
**SEVERITY:** LOW  
**LOCATION:** `loop.py` `_complete_actionable`: `range(max(1, empty_retries + 1))`  
**INPUT:** `empty_retries=-5` → `max(1, -4)=1` → single attempt, then `stopped="empty"` with reply claiming “empty response -4 times” (`empty_retries + 1` in the f-string is wrong for negatives).  
**WHY:** Does not break C1 (still not `"final"`). Budget still bounded. Misleading reply arithmetic only.  
**OTHER LAYER:** Callers pass default 3 or tests use ≥0.  
**FIX:** `n = max(0, empty_retries); attempts = n + 1`; reply uses `attempts`.

### F6 / Retry temperature / attempt never reach `govern_action` — NON-FINDING
**SEVERITY:** n/a (blocked — C2 holds)  
**TRACE:**  
1. `_complete_actionable` only passes `history`, `tools=openai_tools()`, optional `temperature` into `client.complete`.  
2. On success: `run_turn` appends assistant turn, then `for intent in parsed.intents: govern_action(session, intent, importance=importance, risk=risk)` — **no** temperature, attempt, or retry flag.  
3. `govern_action` signature: `(session, intent, importance, risk, *, leash=None)`. Authority from session caps, directive, workspace fence, seals — none read client sampling state.  
4. Tools advertised: `tools = openai_tools()` once per `_complete_actionable` call; identical every attempt. History is **not** mutated on empty attempts (empties not appended) — same prompt bytes each try.  
**CONCRETE:** Retried completion at `temp=1.0` producing `write_file` → same `govern_action` path as attempt 0; decision depends only on session+intent.  
**Double-run / held slip:** Empty attempts produce no intents → no `govern_action`. Only the actionable msg is governed once per outer iteration. Held/denied still only from seam.  
**FIX:** None.

### F7 / Unbounded spin / re-roll valid final — NON-FINDING
**SEVERITY:** n/a  
**LOCATION:** `_complete_actionable` + `run_turn`  
**Budget:** `empty_retries ∈ {0,1,3}` → attempts `max(1, e+1)` ∈ {1, 2, 4}. Default 3 → 4 calls max per outer iteration. Outer `max_iterations` unchanged.  
**Legit final:** `content="The answer is 42."` → actionable on attempt 0 → return immediately (test pins `len(client.seen)==1`).  
**Ambiguous-only:** actionable → no retry.  
**Decisions preserved:** `return TurnResult(..., decisions=decisions, ...)` on empty uses the list accumulated earlier in the turn; empties never clear it. (Mid-turn empty after prior tool iterations keeps prior decisions.)  
**No call storm:** finite nested loops only.  
**FIX:** None.

### F8 / `stopped="empty"` caller contract — LOW (interface awareness)
**SEVERITY:** LOW  
**LOCATION:** `TurnResult.stopped` comment; no exhaustive matcher in shipped loop callers shown  
**PROBE:** Comment updated to include `"empty"`. Tests assert it. Material does not show UI/CLI branching only on `{final,held,paused,max_iterations}` that would mis-label empty as success.  
**RISK:** A host that treats “any `stopped != held/paused` as done OK” could still mis-handle — **caller discipline**, not a loop lie: reply is explicit and `stopped="empty"` ≠ `"final"`.  
**FIX:** Document in host-facing API; grep callers if any assume a closed enum.

### F9 / Optional `temperature` / ScriptedClient / propose path — NON-FINDING
**SEVERITY:** n/a  
**LOCATION:** `model_client.py`  
**ScriptedClient:** `temperature=None` default; records `temps`; old `complete(messages, tools=None)` still works.  
**OllamaClient:** `temp = self.temperature if temperature is None else temperature`.  
**propose.py:** Not in delta; still `complete()` without temperature → default. Interface additive.  
**Resume/history:** `run_turn` system re-assert block unchanged; empty path returns same `history` object (no blank assistant rows for discarded empties — tested).  
**FIX:** None.

### F10 / `_retry_temperature` monotonic + capped — NON-FINDING (with note)
**SEVERITY:** n/a  
**VALUES:** attempt 1 → 0.7; 2 → 0.85; 3 → 1.0; 4+ → 1.0. Monotonic non-decreasing, capped at 1.0.  
**Note:** attempt 0 never calls it (client default). Schedule optimality out of scope.

### F11 / `max_tokens` 1024→4096 — NON-FINDING (stated safe / empirical)
**SEVERITY:** n/a for guarantee break  
**EFFECT:** Fewer truncations/empties; more tokens/cost per call. Empirical synthesis supports 4096. Not an authority issue. Residual cost is operational, not C1–C3.

### F12 / Reply count vs attempts when `empty_retries=0` — NON-FINDING
**SEVERITY:** n/a  
**INPUT:** `empty_retries=0` → 1 attempt; reply “empty response 1 times” — correct.

### F13 / Same action twice via retry — NON-FINDING
Empty retries do not append history or call `govern_action`. Only one actionable completion per outer iteration is processed. No double execute from this path.

---

**No CRITICAL/HIGH/MEDIUM guarantee breaks found.** Only LOW nits (F5 negative `empty_retries` reply math; F8 host enum awareness).

---

## Certification

**C1: CERTIFIED** — Every path with no intents, no ambiguous, and whitespace-empty text/content fails `_is_actionable` and either retries or returns `stopped="empty"`; the `stopped="final"` branch is unreachable for that class of completion.

**C2: CERTIFIED** — Temperature/attempt/retry state never enter `govern_action`; tools and history are identical across attempts; authority remains session+intent only; empties cannot run or slip held/denied actions.

**C3: CERTIFIED** — Actionable finals and ambiguous completions exit on attempt 0; empty budget is `max(1, empty_retries+1)` (correct for 0/1/3); outer `max_iterations` still bounds the turn; prior `decisions` are preserved on empty terminal return.

**C4: CERTIFIED** — Optional `temperature` is backward-compatible; `_retry_temperature` is monotonic and capped in [0.7, 1.0]; max_tokens bump does not touch the seam; `stopped="empty"` is an explicit non-success terminal (hosts must not equate all non-held stops with success — documentation nit only).

---

## STEELMAN

The fix directly closes the live failure mode: greedy deterministic reasoning-only stops were narrated as `final` with zero decisions. Separating “actionable” from “no intents,” bounding temperature-escalated retries without mutating history or the seam, and failing open as `stopped="empty"` preserves the loop’s honesty invariant while leaving governance byte-identical. Tests pin retry hygiene, non-retry of legit finals, budget exactness, and non-`"final"` empty exhaustion.

## VERDICT

**SOUND** — C1–C4 hold under adversarial probing; no path turns silence into a successful finished turn or lets retry widen authority.  

**Highest-value fix (optional polish):** clamp `empty_retries = max(0, int(empty_retries))` before budget/reply so negative values cannot produce a nonsensical “N times” string (F5).
