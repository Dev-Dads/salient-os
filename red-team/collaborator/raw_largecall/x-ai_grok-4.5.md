# LargeCall panel: x-ai/grok-4.5

_cost=$0.0922 finish=stop_

# Adversarial Review: Large/Batched Tool-Call Reliability Fix

## Findings

### F1 / Unbalanced span swallows trailing prose after a truncated marker / LOW  
**Location:** `collaborator/toolcall.py:80-86`, `194-201`  
**Concrete input:**
```text
Here is context.
<tool_call>{"name":"write_file","arguments":{"path":"a","content":"aa
AND more prose the model emitted after the clip point that is NOT part of the call.
```
(Unbalanced path: `end = len(content)`.)  
**Why:** The unbalanced hit spans to EOF, so the strip loop removes everything from the marker to EOF. Trailing prose is neither in `text` nor (beyond `js[:200]`) fully in `ambiguous`.  
**Guarantee:** Does **not** break C1 for the *tool-shaped* thing (it is in `ambiguous`). It can drop non-tool prose from `text` — a presentation nit, not silent tool loss. Stated non-goal relative to C1’s “tool-shaped” wording.  
**Other layer:** N/A (parser-only).  
**Fix (optional):** Cap unbalanced end at a secondary marker / newline heuristic, or keep `content[end:]` in `remaining` when `balanced=False` if you care about prose fidelity.

### F2 / Whole-content path skipped when structured intents already present / LOW (nit / pre-existing shape)  
**Location:** `collaborator/toolcall.py:207` (`if not intents:`)  
**Concrete input:** Message with a valid structured `tool_calls` entry **and** whole-content body  
`[{"name":"write_file",...}, {"not":"a call"}]` or a clipped whole-content batch.  
**Why:** Branch 3 only runs when `not intents`. A mixed structured+content message can leave the content batch unexamined. Pre-existing precedence (“structured wins”), not introduced by this diff; C1’s new surfacing only applies when branch 3 runs.  
**Other layer:** Loop still governs structured intents; content batch is ignored (same as before for valid whole-content alongside structured).  
**Fix (if desired):** Run whole-content ambiguous surfacing even when structured intents exist, without promoting content to intents.

### F3 / `_text_looks_toolish` false negative without quoted keys / LOW  
**Location:** `collaborator/toolcall.py:247-255`  
**Concrete input:** Whole-content clipped JS-ish  
`[{name: "write_file", arguments: {path: "a", content: "cli`  
(no `"` around keys).  
**Why:** Tokens are only `'"name"'`, `'"arguments"'`, etc. Unparseable + no double-quoted keys → not ambiguous, full string stays in `text`.  
**Guarantee:** Edge of C1 for “tool-shaped.” Real model JSON almost always uses `"name"`. Weak relative to stated failure mode (clipped JSON batches).  
**Other layer:** None.  
**Fix:** Also match unquoted forms or a broader `name`/`arguments` pattern.

### F4 / Truncation retry continues even when only `ambiguous` (no intents) — by design, not a bug / NON-FINDING  
**Location:** `collaborator/loop.py` `_complete_actionable`: `if _is_truncated(msg) and not parsed.intents`  
**Probe:** Truncated message with only unbalanced `<tool_call>` → `ambiguous` non-empty, `intents` empty → retry grows budget (does not return early as actionable).  
**Why this is OK:** Avoids accepting a clipped call as the final turn when more budget might complete it. After budget exhausts, last parse’s `ambiguous` is returned and is actionable → surfaced. No partial run.  
**Blocked attack:** Treating “retry past ambiguous truncation” as double-exec or authority bypass — **does not hold**.

### F5 / `grown_mt` persists across empty attempts after a truncation / NON-FINDING (nit at most)  
**Location:** `loop.py` `_complete_actionable` loop  
Once set, `grown_mt` stays for later attempts (including empty escapes). Budget still `min(65536, …)`; does not affect governance.  
**Not a guarantee break.**

---

## C1 probes (vanishing tool-shaped input)

| Probe | Result |
|--------|--------|
| Unbalanced `<tool_call>` alone | → `ambiguous`, stripped from `text`. **No vanish.** |
| Valid `<tool_call>` + unbalanced trailing | Valid → `intents`; tail → `ambiguous`; strip by spans. **No vanish.** Pinned: `test_truncation_never_discards_a_complete_call_parsed_in_the_same_turn`. |
| Multiple markers, one truncated | Same hit list; each classified. **No vanish.** |
| Whole-content list, one bad element | `any(_looks_toolish)` → whole batch `ambiguous`, no intents. **No vanish.** Strict: no partial run. |
| Whole-content list, none toolish e.g. `[{"a":1}]` | Not tool-shaped; stays parseable JSON non-call → no intents/ambiguous; content may remain as text via `remaining=content` (branch 3 didn’t clear). **OK.** |
| Nested braces in clipped span | Unbalanced to EOF; `js` surfaced `[:200]`. **No vanish.** |
| Clipped whole-content no marker | `_text_looks_toolish` → `ambiguous`. **No vanish** (unless F3). |
| `remaining` strip of unbalanced | Tool JSON removed from prose (F1: trailing prose can go too). Tool itself in `ambiguous`. |

**No path found** where a clearly tool-shaped construct is absent from `intents`, `ambiguous`, **and** `text` simultaneously, except the deliberate “not tool-shaped” cases and the F3 fringe.

---

## C2 trace (governance / retries)

```
_complete_actionable
  → client.complete(..., max_tokens=grown_mt?, temperature=?)
  → parse_message(msg)  # finish_reason not used in parse
  → return msg, parsed, actionable
run_turn
  → history.append(assistant content only)  # no finish_reason on wire
  → for intent in parsed.intents:
        govern_action(session, intent, importance=..., risk=...)
  → ambiguous only extended / reported in TOOL RESULTS — never govern_action
```

- `govern_action` signature unchanged; no `max_tokens` / `temperature` / `finish_reason`.  
- Retry only changes sampling kwargs on `complete`; intents are still plain `ToolIntent`s.  
- Authority path is byte-identical for first-shot vs grown-budget completions.  
- `ambiguous` never enters the `for intent in parsed.intents` loop.

**Blocked:** grown budget / higher temp / `finish_reason` changing capability or leash, or running `ambiguous`.

---

## C3 probes

| Claim | Evidence |
|--------|----------|
| No partial write of clipped call | Unbalanced → not `_coerce_call` → not in `intents`. Pinned. |
| No duplicate (complete + retry) | Retry only if `_is_truncated and not parsed.intents`. If any intent exists, fall through, run once, no continue. Pinned grok F1. |
| Budget bound | `grown_mt = min(65536, (grown_mt or base_mt) * 2)` → ≤65536. |
| Retry bound | `range(max(1, empty_retries+1))` → empty_retries 0→1, 1→2, 3→4 attempts. |
| Valid whole-content batch runs all | Unchanged `all(g is not None)` path. Pinned. |
| Mixed batch not partially run | `elif any(_looks_toolish)` → ambiguous only. Pinned. |

**Note:** Truncation retries share the `empty_retries` budget (not a separate pool). Bounded; orthogonal empty vs trunc perturbations still hold. Not a C3 break.

**Persistent truncation with only ambiguous:** After exhausted continues, `return msg, parsed, _is_actionable(...)` → True via ambiguous → `stopped=final` with ambiguous surfaced (out-of-scope pre-existing shape). No run of clipped call.

---

## C4 probes

| Probe | Result |
|--------|--------|
| ScriptedClient | `complete(..., max_tokens=None)` + `max_tokens_seen`. Compatible. |
| propose.py / callers with no overrides | Defaults `None` → client defaults. |
| Resume path | `run_turn` rebuilds assistant turns from `_content(msg)` / synthesized request text only — no `finish_reason` in history. |
| `finish_reason` on wire later | Ollama attaches on returned msg; history uses fresh `{"role","content"}` only. **No leak.** |
| Valid `<tool_call>` + prose | Balanced hit ends at JSON end; prose in `keep`. **OK.** |
| Valid + truncated | Two hits; no harmful overlap (`last = max(last,end)`). **OK.** |
| Structured / content-block / whole-content object | Unchanged success paths; additive ambiguous branches only. |

**Blocked:** interface regression and finish_reason wire leak as claimed.

---

## Certification

**C1: CERTIFIED** — No concrete input found where a tool-shaped call vanishes from `intents`, `ambiguous`, and `text`; truncated tags, mixed batches, and clipped whole-content are surfaced (F3 is a narrow false-negative nit).

**C2: CERTIFIED** — Retries only perturb `complete` sampling; every intent still hits `govern_action` unchanged; `ambiguous` is never executed; `finish_reason`/budgets never enter the seam.

**C3: CERTIFIED** — Clipped calls are not executed; complete+truncated co-emission runs complete once and surfaces the tail; budget ≤65536; attempts ≤ `max(1, empty_retries+1)`; valid batches run fully; mixed batches stay all-or-nothing surface.

**C4: CERTIFIED** — ScriptedClient/optional kwargs/resume remain compatible; `finish_reason` is not re-fed on the wire; balanced spans still isolate valid calls from prose/truncated siblings.

---

## STEELMAN

The fix correctly targets silent loss (truncated `<tool_call>` and partial whole-content batches) by forcing those shapes into `ambiguous`, and pairs that with a bounded max_tokens growth that refuses to discard already-parsed intents in the same truncated message. Governance stays a pure function of parsed `ToolIntent`s; sampling knobs and `finish_reason` never cross the seam. Strict all-or-nothing batch execution is preserved while the failure mode changes from drop to surface.

## VERDICT

**SOUND** — Claims C1–C4 hold under adversarial probing; remaining items are nits (EOF prose strip, `_text_looks_toolish` quote sensitivity, whole-content gated on `not intents`), not guarantee breaks.

**Single highest-value fix:** Broaden `_text_looks_toolish` (or add a structural `^[\[{]` + `name`/`arguments` heuristic) so clipped whole-content calls without canonical `"key"` quoting cannot fall through to unmarked prose.
