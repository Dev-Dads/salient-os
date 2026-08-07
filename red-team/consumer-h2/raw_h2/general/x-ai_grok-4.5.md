# Red-team (consumer, pass=general): x-ai/grok-4.5

_finish=stop seconds=173.5 usage={'prompt_tokens': 25588, 'completion_tokens': 9012, 'total_tokens': 34600, 'cost': 0.1050304, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1050304, 'upstream_inference_prompt_cost': 0.0509584, 'upstream_inference_completions_cost': 0.054072}, 'completion_tokens_details': {'reasoning_tokens': 6920, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

### F1 / Failed finalize leaves prior `_LAST_DIRECTIVE` → 2-turns-stale budget / HIGH  
**Location:** `salience_observer.py` · `_close_locked`, consumed by `_resolve_bounded` → `bounded_iterations`

**Concrete trigger:**
1. Turn u1 opens, finalizes successfully → `_LAST_DIRECTIVE["s"] = d1` (e.g. budget 20).  
2. Turn u2 opens; signals recorded; window still open.  
3. Next `bounded_iterations("s", 30)` runs finalize-on-read → `_close_locked(u2)`.  
4. `window.closed = True` runs **before** the `try`; then `emit`/`interpret` fails (disk full, EACCES on JSONL append, bus fence rejection, etc.).  
5. `except` logs and returns; **`_LAST_DIRECTIVE` is not updated or cleared**.  
6. `_resolve_bounded` returns `_directive_budget(d1)` → **turn N applies turn N−2’s directive**.

```python
# _close_locked
if window.closed:
    return
window.closed = True          # ← committed even if finalize fails
try:
    ...
    _LAST_DIRECTIVE[window.session_id] = directive  # ← skipped on failure
except (Exception, SystemExit):
    logger.warning(...)
```

**Why it matters:** Direct A3 break. PR-H1 fix (a) only blocked the **disk** stale path when `session_id in _BUSES` and cache empty; the **in-memory** previous success still wins after a failed close. Window stays `closed`, so finalize will not retry. In v0 numbers often match the operator floor (easy to miss); once the policy window widens this silently applies the wrong governed budget. No test simulates “prior success + this close fails”.

**Minimal fix:** On finalize failure, clear the consumer cache (and keep `closed=True` to avoid hammering a broken bus), e.g. in the `except` of `_close_locked`:  
`_LAST_DIRECTIVE.pop(window.session_id, None)`  
so the consumer fail-opens to `default` instead of N−2. Optionally retry-friendly: only set `closed=True` after successful `emit`.

---

### F2 / Cold disk recovery not written to `_LAST_DIRECTIVE` → second resolve drops verified budget / MEDIUM  
**Location:** `salience_observer.py` · `_budget_from_disk` + `_resolve_bounded`

**Concrete trigger:**
1. Prior process persisted directive `compute_budget=7`, session closed; file on disk.  
2. Fresh process: `_BUSES` / `_LAST_DIRECTIVE` empty.  
3. `bounded_iterations("s", 10)` → cold `_budget_from_disk` → `SalienceBus` verify OK → returns `7`. Side effect: `_BUSES["s"]` is populated; **`_LAST_DIRECTIVE` stays empty**.  
4. Same process, **second** `bounded_iterations("s", 10)` with no successful `_close_locked` in between (turn aborted before `pre_llm_call`, double build, gateway retry, etc.):  
   - `_LAST_DIRECTIVE.get` → `None`  
   - `_budget_from_disk`: `if session_id in _BUSES: return None`  
   - → **`10` (default)**, not `7`.

**Why it matters:** Restart fallback is one-shot. Guard from fix (a) correctly blocks unverified/stale disk reads when a bus is warm, but nothing **promotes** the verified recovery into `_LAST_DIRECTIVE`, so the warm-bus guard erases the recovered value on the next read. `test_restart_recovers_budget_from_disk` calls the consumer once only — stays green under this bug.

**Minimal fix:** After a successful verified read, cache a value the consumer already accepts, e.g. assign `_LAST_DIRECTIVE[session_id]` from the verified payload/dict (or a tiny budget-only sentinel that `_directive_budget` understands) before returning.

---

### F3 / `_budget_from_disk` second pass over the file is redundant + TOCTOU-ish / LOW  
**Location:** `salience_observer.py` · `_budget_from_disk`

**Concrete trigger:** Under `_LOCK`, `SalienceBus(path)` already replay-verifies and fills `directives_for`. Code then **re-opens the same path** and `json.loads` every line only to recover `last_subject`, then trusts `directives_for(last_subject)[-1]`. An external rewrite between ctor and the second read can change which subject string is chosen (empty → default; mismatched subject → default). In-process writers are serialized by `_LOCK`; cross-process tail games are partly ADR-0001.

**Why it matters:** Not a clean “unverified budget returned” path (value still comes from the bus store), but it is extra fail-open surface and contradicts the comment “never a second independent parse of the file.” Integrity of the **scalar** is OK; selection of “which” directive is weaker than it needs to be.

**Minimal fix:** Drop the second file read. Track last directive subject during/after bus construction (e.g. walk `bus`’s verified directive list / head entry) entirely from in-memory verified state.

---

### F4 / Test honesty gaps for F1–F2 / MEDIUM (test)  
**Location:** `tests/hermes_cli/test_salience_consumer.py`

| Gap | Mutation | Predicted result |
|-----|----------|------------------|
| No “close throws after prior cached directive” | Keep `_LAST_DIRECTIVE` on failed `_close_locked` (current prod) or delete the `pop` fix | **GREEN** — nothing asserts stale-cache behavior |
| Restart test single-shot | Skip writing `_LAST_DIRECTIVE` in `_budget_from_disk` (current prod) | **`test_restart_recovers_budget_from_disk` GREEN**; second call untested |
| Many verbatim/deny tests seed `_LAST_DIRECTIVE` only | Break finalize-on-read wiring | Those tests **GREEN**; only `test_three_turns_*` / `test_finalize_on_read_*` would catch pure A3 delete — not failed-close |

**Why it matters:** Guarantees A3 + grok-F8 were “fixed” in review once; residual paths are mutation-blind.

**Minimal fix:** Add (1) monkeypatched `_close_locked`/`emit` failure after a successful u1 cache, assert second consume == `default` not u1 budget; (2) cold recover then immediate second `bounded_iterations` without open/close, assert budget stable.

---

### F5 / Call-site comment overstates containment / LOW (honesty)  
**Location:** `agent/turn_context.py` (~salience consumer block)

Comment: *“Fails open: the observer never raises and returns the operator's own value unchanged…”*  
Actual fail-open is inside `bounded_iterations` (`except (Exception, SystemExit)`). The excerpted `try:` around the import/call does not show a matching `except` in the provided material; import/`AttributeError` would depend on outer handling. Wording also says “observer” for the **consumer** path.

**Why it matters:** Operators/reviewers may believe the host site is fully sealed when only the library entrypoint is.

**Minimal fix:** Comment: consumer fails open; optionally `except Exception: pass` around the call site if not already present in full file.

---

### Not reported (checked, no concrete defect)
- Re-entrant `_LOCK` deadlock: `_resolve_bounded` → `_close_locked` / `_bus_for` / `_budget_from_disk` / `_operator_budget` do not take `_LOCK` again.  
- Deny-shaped / non-int / bool / `budget < 1` → `_directive_budget` → `None` → `default`; no re-clamp vs `default`.  
- Corrupt/tampered chain on cold open: `SalienceBus` raises before `_BUSES` publish; outer consumer → `default`.  
- `_LAST_DIRECTIVE` freed in `_close_session` with `_BUSES`/`_WINDOWS`.  
- v0 behavior-preserving design itself (in scope only if misdescribed — module docstring + `cli-config.yaml.example` state pinned window + inert consumption clearly).  
- Vendored `interpret` / bus chain internals.

---

## STEELMAN

The consumer boundary is carefully shaped: broad `except (Exception, SystemExit)`, deny-shaped withholding, kill switches, cold-path-only disk fallback through replay-verifying `SalienceBus`, and A3 finalize-on-read ordered before `pre_llm_call` are real, load-bearing structure. Prior review clearly removed the worst “cached bus + raw JSONL” restart hole and added anti-reclamp / non-tail hash tests. What remains is incomplete failure accounting around **cache lifetime** (failed close + post-recovery cache), not a missing fail-open wrapper on the happy path.

## VERDICT: **MINOR_ISSUES**

No concrete never-brick escape or unverified budget feed survived scrutiny; the serious residual is **A3/cache consistency after failed finalize (F1)** plus **one-shot cold recovery (F2)** with matching test blind spots — fix those before treating A3/grok-F8 as fully closed.
