# Red-team (producer, pass=coding): x-ai/grok-4.5

_finish=stop seconds=472.9 usage={'prompt_tokens': 18991, 'completion_tokens': 27004, 'total_tokens': 45995, 'cost': 0.1997884, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1997884, 'upstream_inference_prompt_cost': 0.0377644, 'upstream_inference_completions_cost': 0.162024}, 'completion_tokens_details': {'reasoning_tokens': 24920, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

### F1 / SystemExit containment does not cover the gate / `has_hook` path / LOW
**Location:** `salience_observer.observe_lifecycle` (gate check *before* `try`); `salience_observer.handles_hook` → `salience_enabled` → `_config_flag`; `lifecycle.has_hook` / `observability._safe_observe` (`except Exception` only); `model_tools._emit_post_tool_call_hook` (`except Exception` only)

**Concrete trigger:** Any `SystemExit` raised from a host API on the *gate* path — e.g. `read_raw_config_readonly()` or `from product_identity import IS_QUORUM_EDITION` — during:
1. `has_hook("post_tool_call")` inside `_emit_post_tool_call_hook`, or  
2. `handles_hook(...)` at the top of `observe_lifecycle` (outside its `try`).

`SystemExit` is a `BaseException`. It bypasses every `except Exception` on the emitter → `lifecycle.has_hook` → `observability.handles_hook` chain and is **not** inside `observe_lifecycle`’s `except (Exception, SystemExit)`.

**Why it matters:** The fixed `get_config_value`/`sys.exit` hole was on the finalize path (now inside the `try`). The same class of failure on the *gate* path still takes down the host on the tool-call hot path. Today’s APIs are *believed* not to exit, but containment is inconsistent with the stated “never let SystemExit reach the host” guarantee and with the care taken inside the handler body. No test exercises SystemExit on `has_hook`/`salience_enabled`.

**Suggested fix (minimal):**  
- Move the `handles_hook` check inside the existing `try` in `observe_lifecycle`.  
- Catch `(Exception, SystemExit)` in `_safe_observe` and in `lifecycle.has_hook` (still do **not** catch `KeyboardInterrupt`).  
- Optionally memoize `salience_enabled()` so the gate is not a live config import on every tool call.

---

### F2 / Audit-fence tests are mutation-blind to args/result/CoT leakage / MEDIUM (TEST HONESTY)
**Location:** `tests/hermes_cli/test_salience_observer.py` — `test_mapping_by_facet`, `test_e2e_through_real_tool_dispatch` (and absence of any raw-bus content assertion); production mapper is currently clean: `_map_tool_call` / `_map_api_error`

**Concrete trigger (sabotage the code under test):**
```python
# in _map_tool_call
provenance = _ref(
    "tool:" + tool_name,
    "status:" + status,
    "args:" + str(kwargs.get("args") or ""),
    "result:" + str(kwargs.get("result") or ""),
    "err:" + str(kwargs.get("error_message") or ""),
)
```
E2E still passes real `function_args={"path": "x"}` and `error_message="boom"`. Facets, directive count, `verify_chain`, subject hashing, and `len(p) <= MAX_TOKEN_LEN` all stay green. Nothing asserts `"path"`, `"boom"`, or other payload fragments are absent from the JSONL.

**Why it matters:** Guarantee 4 / Finding G is “never put tool args, results, or prose on the bus,” not merely “truncate to 128.” Structural `valid_signal` still passes for truncated arg snippets, so the bus becomes a silent exfil channel and every listed test remains green.

**Suggested fix (minimal):** In E2E (and/or a unit test that publishes then reads the file), assert the durable payload is a deny-list of hook fields:
```text
args / result / error_message / user_message / conversation_history / middleware_trace
```
never appear as substrings in the JSONL, and provenance is only `tool:…` / `status:…` / `api_error` / `provider:…` shaped.

---

### F3 / Gate config read is unmemoized on the tool-call hot path / LOW
**Location:** `salience_observer.salience_enabled` / `_config_flag`; callers: `handles_hook` ← `lifecycle.has_hook` ← `_emit_post_tool_call_hook` (and again from `observe_lifecycle`)

**Concrete trigger:** Quorum Edition, `salience.enabled` default ON. One agent turn with N tool calls → ≥N× `read_raw_config_readonly()` from `has_hook`, plus another per fired `observe_lifecycle`. Contrast `_operator_budget`, which was already fixed to memoize.

**Why it matters:** When the observer is ON it deliberately flips `has_hook("post_tool_call")` true; the old zero-cost path is gone. Unbounded repeated config I/O is avoidable process-global work on the hottest emit site. Not a decision-path fork, but a real resource regression the budget memoization shows the authors already care about.

**Suggested fix (minimal):** Process-level cache for `salience_enabled()` (invalidate in `_reset_for_tests` only), same shape as `_OPERATOR_BUDGET_CACHE`.

---

### F4 / SEAM “return value / effect unchanged” is untested / LOW (TEST HONESTY)
**Location:** `lifecycle.invoke_hook` + tests (no assertion); `agent/turn_context.py` pre_llm_call consumer of `_pre_results`

**Concrete trigger:** Sabotage:
```python
def invoke_hook(hook_name, **kwargs):
    observe_lifecycle(hook_name, **kwargs)
    obs = [...]  # or merge a non-[] observer result
    return obs + plugins.invoke_hook(hook_name, **kwargs)
```
No salience test reads `invoke_hook`’s return. E2E only checks bus side-effects. A regression that feeds observer output into pre_llm_call context injection would stay green under this suite.

**Why it matters:** Guarantee 6 is load-bearing for PRODUCE-ONLY (context injection must stay plugin-only). Code currently does the right thing (observer return discarded; plugins result returned unchanged) but the suite cannot see a break.

**Suggested fix (minimal):** One test: with gate ON and a plugin registered on `pre_llm_call` that returns a sentinel, assert `invoke_hook("pre_llm_call", ...)` equals the plugin-only result (same object list / same sentinel), and that turning salience OFF does not change it.

---

### F5 / Raw `turn_id` is written into the durable subject (A11 edge) / LOW
**Location:** `salience_observer._subject`

**Concrete trigger:**
```text
pre_llm_call(session_id="secret-sess-id", turn_id="secret-sess-id")
→ subject == "<16hex>:secret-sess-id"
```
Bus filename is full SHA-256 (safe). Subject head hashes `session_id`, but the tail is raw `turn_id` whenever `len(turn_id) ≤ room`. If the host ever echoes `session_id` (or any confidential correlator) as `turn_id`, the durable record contains it. Tests only assert a *literal* sample session string is absent and that short turn ids stay readable.

**Why it matters:** A11 / ADR 0002 is stated as “raw session_id must never reach the durable record.” This is a host-dependent hole, not a default-path leak — hence LOW — but the absolute wording is not enforced by construction.

**Suggested fix (minimal):** Always use a hash-derived tail (or hash when `turn_id == session_id` / when `session_id in turn_id`), accepting loss of readable short turn ids in the subject; keep readability in non-durable logs only if needed.

---

### Not reported (reviewed & clear / out of scope)
- Attribution guards (`_record` / `_open_window` turn_id + closed checks), session registry free-on-close, A3 rollover finalize, hashed bus filenames, mapper not reading `args`/`result`/`user_message`, `invoke_hook` return currently plugin-only, `_LOCK` covering all bus/registry access, SystemExit from `get_hermes_home` inside the handler body (tested), vendored `valid_signal` / `SalienceBus` internals, ADR 0001 exclusions.

---

## STEELMAN

The produce path is genuinely well-shaped: fail-closed attribution is simple and tested with mutation-aware cases; session registries are freed on close; SystemExit from the previously hot `get_hermes_home` finalize path is contained and regression-tested; the audit edge only constructs bounded ref tokens; and the seam discards observer returns so pre_llm_call injection stays plugin-owned. Gating (default ON, kill switch, unreadable → OFF, non-Quorum → dark) is coherent and covered.

## VERDICT

**MINOR_ISSUES** — Production code meets PRODUCE-ONLY / fail-closed / hashed bus identity under normal host APIs; residual gaps are incomplete SystemExit wrapping on the *gate* path, unmemoized hot-path config I/O, and test suite blindness to audit-fence content leakage and seam return-value isolation.
