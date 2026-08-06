# Red-team (producer, pass=general): x-ai/grok-4.5

_finish=stop seconds=336.7 usage={'prompt_tokens': 17539, 'completion_tokens': 17896, 'total_tokens': 35435, 'cost': 0.1422364, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1422364, 'upstream_inference_prompt_cost': 0.0348604, 'upstream_inference_completions_cost': 0.107376}, 'completion_tokens_details': {'reasoning_tokens': 16184, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

### F1 / Durable subject truncation aliases distinct turns / MEDIUM
**Location:** `hermes_cli/observability/salience_observer.py` — `_subject`, used by `_open_window` / `_signal`  
**Concrete trigger:**
```text
session_id = "s"
turn_a = "x"*111 + "A"    # len 112
turn_b = "x"*111 + "B"
# both subjects become sha256("s")[:16] + ":" + ("x"*111)  after [:MAX_TOKEN_LEN]
so._open_window({"session_id":"s","task_id":"t","turn_id":turn_a})
so._record({...,"turn_id":turn_a,"tool_name":"write_file","status":"ok"}, so._map_tool_call)
so._open_window({...,"turn_id":turn_b})   # finalizes A
so._record({...,"turn_id":turn_b,"tool_name":"write_file","status":"ok"}, so._map_tool_call)
so._close_session({"session_id":"s"})
# bus.signals_for(subject) and directives_for(subject) now mix turn A and B
```
**Why it matters:** In-memory `_record` still matches `window.turn_id`, but the durable arbitration key is no longer 1:1 with turns. Audit/attribution over the JSONL (and any later consumer keyed by `subject`) cross-contaminates turns whenever `turn_id` exceeds ~111 chars. That is a real fail-closed/attribution hole in the durable record, not just a cosmetic bound.  
**Suggested fix:** Hash the turn as well (e.g. `hash(session)[:16] + ":" + hash(turn)[:16]`), or reject/open-fail when `len(turn_id) > MAX_TOKEN_LEN - 17` instead of silent truncate-and-alias.

---

### F2 / `_LOCK` held across bus disk I/O + logging (re-entrancy deadlock) / LOW
**Location:** `salience_observer.py` — `_record`, `_close_locked`, `_bus_for` (all under `_LOCK`)  
**Concrete trigger:** Host (or a logging/file-watch plugin) synchronously handles writes under `<hermes_home>/salience/*.jsonl` by calling `lifecycle.invoke_hook(...)` on the same thread. Sequence: `_record` → `SalienceBus.publish` → `open(...).write` → watcher → `invoke_hook` → `observe_lifecycle` → `_LOCK.acquire` → deadlock. Same pattern if a logging handler invoked from `logger.warning(..., exc_info=True)` re-enters the observer while the lock is held in the `except` paths.  
**Why it matters:** A produce-only observer must not freeze the agent thread. `threading.Lock` is non-reentrant; I/O and logging inside the critical section are the classic trigger.  
**Suggested fix:** Build/publish/finalize with local refs outside the lock (or copy-out, I/O, copy-in); never log while holding `_LOCK`. Keep only registry mutations under the lock.

---

### F3 / Kill-switch is `is not False`, not boolean-false / LOW
**Location:** `salience_observer.py` — `_config_flag`  
**Concrete trigger:** Config `salience.enabled: "false"` or `0` or `null` (depending on loader) → `salience.get("enabled") is not False` is **True** → observer stays on. Only exact `False` disables.  
**Why it matters:** Operators who set a falsey non-bool (YAML/JSON quirks, env-bridged strings) believe the kill switch is engaged; the produce path and `has_hook("post_tool_call")` remain live.  
**Suggested fix:** Treat only `True`/`False` as definitive; e.g. `v is True` for on when key present, or explicitly accept common falsey forms and fail closed on non-bool.

---

### F4 / No test would catch BaseException escaping the three `except Exception` layers / LOW (TEST HONESTY)
**Location:** Guarantees for produce-only / never-crash; tests in `tests/hermes_cli/test_salience_observer.py`; guards in `observability/__init__._safe_observe`, `salience_observer.observe_lifecycle`, `lifecycle.invoke_hook` / emitters  
**Concrete trigger (mutation):** Patch `get_hermes_home` or `SalienceBus.publish` to `raise SystemExit(1)` (the class of bug already fixed for `get_config_value`). Entire suite stays green — nothing asserts that host dispatch continues or that `SystemExit` is contained.  
**Why it matters:** The highest-severity class of host-break in the brief is exactly “BaseException sails past `except Exception`.” Regression protection for the already-fixed `get_config_value` pattern is absent, so the next `sys.exit` host API reintroduces a CRITICAL with a green suite.  
**Suggested fix:** One unit test that monkeypatches a host API used on the produce path (`get_hermes_home` / bus write / config read) to raise `SystemExit` and asserts `observe_lifecycle` / `invoke_hook` / `_emit_post_tool_call_hook` do not propagate it (and ideally that plugins still run).

---

### F5 / Cross-session isolation not mutation-tested / LOW (TEST HONESTY)
**Location:** `_WINDOWS` keyed by `session_id` in `_record` / `_open_window`; tests only cover wrong `turn_id` and empty ids  
**Concrete trigger (mutation):** Change registry to a single global `_WINDOW` (ignore session key). `test_records_only_against_matching_open_window` and friends stay green; no test opens `s1` and records on `s2`.  
**Why it matters:** Fail-closed attribution explicitly includes cross-session; the suite does not pin it.  
**Suggested fix:** Assert record on `session_id="s2"` against an open `s1` window does not publish.

---

**Not reported (honest negatives):**  
No concrete `SystemExit`/`BaseException` path from the *shown* host APIs (`read_raw_config_readonly`, mkdir/open, salienceos calls) — the `get_config_value` footgun is gone; anything further needs `get_hermes_home` / `product_identity` source not in the material. No seam change to `invoke_hook`’s return value or plugin dispatch order. No raw `session_id` in subject/filename. Mapper outputs are structurally `valid_signal`-safe under normal host ids. Vendored bus full-history RAM use is by bus contract, not observer misuse. ADR 0001/0002 exclusions left alone.

---

## STEELMAN

The produce path is genuinely well-shaped: fail-closed id gates, hashed bus identity, triple `Exception` isolation at seam + observer + several emitters, lock around the single-threaded bus, session-end free of `_WINDOWS`/`_BUSES`, and an E2E that drives real `has_hook`→emitter→`invoke_hook` wiring rather than calling the observer under the gate. Prior review items (budget memoization, session free, directive assertions) are actually fixed and covered.

## VERDICT

**MINOR_ISSUES** — No CRITICAL/HIGH host-break or produce-only bypass with a concrete in-material trigger; the durable subject truncation alias (F1) is the one substantive correctness gap prior reviews missed, with smaller lock/kill-switch/test-honesty nits around it.
