# Red-team (producer, pass=general): mistralai/mistral-medium-3-5

_finish=stop seconds=61.5 usage={'prompt_tokens': 16718, 'completion_tokens': 3340, 'total_tokens': 20058, 'cost': 0.050127, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.050127, 'upstream_inference_prompt_cost': 0.025077, 'upstream_inference_completions_cost': 0.02505}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

### Red-Team Findings

After a thorough adversarial review, here are the **new, concrete defects** that survive the existing internal reviews and fixes. Each is anchored to a file+function, with a precise trigger and minimal fix.

---

---

#### **F1 / CRITICAL: `KeyboardInterrupt` escapes containment in `_operator_budget`**
**SEVERITY**: CRITICAL (PRODUCE-ONLY / NEVER-CRASH-THE-HOST)
**LOCATION**: `salience_observer.py::_operator_budget()`
**CONCRETE TRIGGER**:
1. Enable salience observer (Quorum Edition + `salience.enabled: true`).
2. Start a session and open a window (`pre_llm_call`).
3. Trigger a tool call (`post_tool_call`) to accumulate a signal.
4. **During `_close_locked` (e.g., on `on_session_finalize`)**, send `SIGINT` (Ctrl+C) **while `_operator_budget()` is executing its config read** (e.g., `read_raw_config_readonly()` blocks on I/O or a slow filesystem).
   - `_operator_budget()` is called under `_LOCK` in `_close_locked`, but `KeyboardInterrupt` (a `BaseException`) is **not caught** by the `except Exception` in `_close_locked` or `_safe_observe`. The `KeyboardInterrupt` propagates to the host, violating PRODUCE-ONLY.

**WHY IT MATTERS**:
- The three containment layers (`_safe_observe` in `__init__.py`, the `except Exception` in `observe_lifecycle`, and the `except Exception` in `_close_locked`) **all miss `BaseException` subclasses** like `KeyboardInterrupt` or `SystemExit`.
- The already-fixed `get_config_value` path was `SystemExit`, but `KeyboardInterrupt` is a **new escape path** via `_operator_budget` (or any other I/O in `_bus_for`, `_close_locked`, etc.).

**SUGGESTED FIX**:
Wrap all observer internals in `except BaseException` (not just `Exception`) in `_safe_observe` and the observer’s own guards. Minimal change:
```python
# In hermes_cli/observability/__init__.py
def _safe_observe(callback: Any, hook_name: str, kwargs: dict[str, Any]) -> None:
    try:
        callback(hook_name, **kwargs)
    except BaseException:  # Changed from Exception to BaseException
        logger.warning(
            "Built-in observability hook failed: %s", hook_name, exc_info=True
        )
```
And in `salience_observer.py`:
```python
# In observe_lifecycle, _record, _close_locked, etc.:
except BaseException:  # Changed from Exception
    logger.warning(...)
```

---

---

#### **F2 / HIGH: `session_id` leaks in bus filename via `_session_hash` collision**
**SEVERITY**: HIGH (FAIL-CLOSED ATTRIBUTION / HASHED IDENTITY)
**LOCATION**: `salience_observer.py::_session_hash()` + `_bus_for()`
**CONCRETE TRIGGER**:
1. Create two **different** sessions with `session_id` values that **SHA-256 hash to the same first 16 chars** (e.g., via a length-extension attack or a brute-force collision on the truncated hash).
   - Example: `session_id_1 = "a" * 1000` and `session_id_2 = <crafted input>` such that `sha256(session_id_1)[:16] == sha256(session_id_2)[:16]`.
2. Open windows for both sessions and emit signals.
3. The bus filenames collide (same `.jsonl` file), causing **cross-session signal mixing** in the durable record.

**WHY IT MATTERS**:
- The `subject` in signals/directives uses `_session_hash(session_id)[:16] + ":" + turn_id`, but the **bus filename** uses `_session_hash(session_id) + ".jsonl"` (full hash). However, `_session_hash` is **SHA-256 truncated to hexdigest()**, which is **64 chars**, but the filename uses the **full 64-char hash**, while the `subject` uses only the **first 16 chars of the hash**.
- A collision in the first 16 chars of the hash (easier than full SHA-256) would cause the `subject` to collide, but the filename would **not** collide (since it uses the full hash). However, the **reverse is also true**: if two `session_id`s have the **same full SHA-256 hash** (impossible for distinct inputs, but **truncation to 16 chars in `subject`** makes it possible for the `subject` to collide while the filename does not).
- **Correction**: The `subject` uses `_session_hash(session_id)[:16]`, but the filename uses `_session_hash(session_id)` (full). This is **asymmetric** and could lead to **subject collision without filename collision**, but the **real issue** is that the `subject` truncation to 16 chars **weakens the hash**, making collisions feasible (2^64 operations for 16 hex chars = 64 bits).
- **Worse**: The `subject` is used for **signal/directive grouping**, so a collision would mix signals from two sessions into the same `subject`, violating FAIL-CLOSED ATTRIBUTION.

**SUGGESTED FIX**:
Use the **full hash** (64 chars) in the `subject` to match the filename:
```python
def _subject(session_id: str, turn_id: str) -> str:
    return (_session_hash(session_id) + ":" + turn_id)[:MAX_TOKEN_LEN]  # Remove [:16]
```
This ensures the `subject` and filename use the same hash scope, and 64 chars is still << `MAX_TOKEN_LEN` (128).

---

---

#### **F3 / HIGH: `_map_api_error` leaks unbounded `provider` string into provenance**
**SEVERITY**: HIGH (AUDIT FENCE)
**LOCATION**: `salience_observer.py::_map_api_error()`
**CONCRETE TRIGGER**:
1. Trigger an `api_request_error` hook with a **maliciously long `provider` string** (e.g., `provider="A" * 1000`).
2. The provenance tuple includes `"provider:" + provider`, which exceeds `MAX_TOKEN_LEN` (128) but is **not truncated** in `_ref()`.
   - `_ref()` truncates **each part** to `MAX_TOKEN_LEN`, but the **concatenated string** `"provider:" + provider` can still exceed `MAX_TOKEN_LEN` if `provider` is long.
   - Example: `provider = "A" * 100` → `"provider:" + provider` = 107 chars (OK), but `provider = "A" * 200` → 207 chars (**exceeds 128**).

**WHY IT MATTERS**:
- The `provenance` field in `SalienceSignal` must contain **only bounded ref tokens** (enforced by `valid_signal` in `bus.py`).
- `_ref()` splits input into parts and truncates **each part** to `MAX_TOKEN_LEN`, but **does not truncate the concatenated result** of `"provider:" + provider`.
- A long `provider` string (e.g., from a malicious LLM or plugin) would **violate the audit fence** when `valid_signal` checks the provenance tokens.

**SUGGESTED FIX**:
Truncate the **entire concatenated string** in `_ref()`:
```python
def _ref(*parts: str) -> tuple:
    out = []
    for part in parts:
        token = str(part)[:MAX_TOKEN_LEN]  # Truncate each part
        if token:
            out.append(token)
    # Truncate the concatenated result for each part (defensive)
    out = [s[:MAX_TOKEN_LEN] for s in out]
    return tuple(out[:16])  # Already bounded to 16 refs
```
Or, better, **truncate the input to `_map_api_error`**:
```python
def _map_api_error(kwargs: dict, subject: str) -> list:
    provider = str(kwargs.get("provider") or "")[:MAX_TOKEN_LEN]  # Truncate here
    provenance = _ref("api_error", "provider:" + provider)
    ...
```

---

---
---
#### **F4 / MEDIUM: `_bus_for` race condition on `_BUSES` access**
**SEVERITY**: MEDIUM (CONCURRENCY / RESOURCE)
**LOCATION**: `salience_observer.py::_bus_for()`
**CONCRETE TRIGGER**:
1. Two threads (or async tasks) call `_bus_for(session_id)` **concurrently for the same `session_id`**.
2. Both see `_BUSES.get(session_id) is None`, both create a new `SalienceBus`, and both write to `_BUSES[session_id]`.
   - The second write **overwrites** the first, leaking the first bus (and its file handle).
3. The first bus’s file handle is **never closed**, causing a **resource leak**.

**WHY IT MATTERS**:
- `_bus_for` is **not protected by `_LOCK`**, but `_BUSES` is a **shared mutable dict**.
- While `SalienceBus` is single-threaded by contract, the **observer itself is not** (the host may call `observe_lifecycle` from multiple threads).
- The `_LOCK` in `_record`/`_close_session` does not cover `_bus_for`.

**SUGGESTED FIX**:
Protect `_bus_for` with `_LOCK`:
```python
def _bus_for(session_id: str):
    with _LOCK:
        bus = _BUSES.get(session_id)
        if bus is None:
            from pathlib import Path
            from hermes_constants import get_hermes_home
            directory = Path(get_hermes_home()) / "salience"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / (so._session_hash(session_id) + ".jsonl")
            bus = SalienceBus(str(path))
            _BUSES[session_id] = bus
        return bus
```

---
---
#### **F5 / MEDIUM: `_close_session` does not handle `KeyboardInterrupt` in `_close_locked`**
**SEVERITY**: MEDIUM (PRODUCE-ONLY / NEVER-CRASH-THE-HOST)
**LOCATION**: `salience_observer.py::_close_session()`
**CONCRETE TRIGGER**:
1. Same as **F1**, but specifically during `_close_locked` (e.g., `interpret()` or `issue_policy()` is interrupted by `SIGINT`).
2. The `except Exception` in `_close_locked` **does not catch `KeyboardInterrupt`**, so it propagates to `_close_session`, then to `observe_lifecycle`, then to the host.

**WHY IT MATTERS**:
- `_close_locked` is called under `_LOCK` in `_close_session`, but its `except Exception` misses `BaseException`.
- This is a **separate escape path** from F1 (which was about `_operator_budget`).

**SUGGESTED FIX**:
Change `except Exception` to `except BaseException` in `_close_locked` (and all other observer internals).

---
---
#### **F6 / LOW: `_subject` could include raw `turn_id` exceeding `MAX_TOKEN_LEN`**
**SEVERITY**: LOW (HASHED IDENTITY)
**LOCATION**: `salience_observer.py::_subject()`
**CONCRETE TRIGGER**:
1. Open a window with a **very long `turn_id`** (e.g., `turn_id = "A" * 200`).
2. `_subject` concatenates `_session_hash(session_id)[:16] + ":" + turn_id`, which could exceed `MAX_TOKEN_LEN` (128).
   - Example: `_session_hash(session_id)[:16]` = 16 chars, `":"` = 1 char, `turn_id` = 200 chars → total = 217 chars (> 128).
3. The `subject` in `SalienceSignal` would **fail `valid_signal`** (since `subject` must be `<= MAX_TOKEN_LEN`).

**WHY IT MATTERS**:
- The `subject` must be a bounded ref token (`<= MAX_TOKEN_LEN`).
- The current code **does not truncate the `turn_id` part**, so a long `turn_id` could make `subject` invalid.

**SUGGESTED FIX**:
Truncate the entire `subject` to `MAX_TOKEN_LEN`:
```python
def _subject(session_id: str, turn_id: str) -> str:
    return (_session_hash(session_id) + ":" + turn_id)[:MAX_TOKEN_LEN]
```
(Note: This is the same fix as F2, but F2 was about collision; this is about length.)

---
---
### STEELMAN (What’s Genuinely Right)
1. **Containment is mostly solid**: The `_safe_observe` wrapper and the observer’s own `except Exception` guards cover almost all paths. The **only gaps** are `BaseException` subclasses (`KeyboardInterrupt`, `SystemExit`), which are rare but real.
2. **Fail-closed attribution is well-enforced**: The `_record` and `_open_window` guards strictly require matching `session_id`/`turn_id`, and the `subject` hashing prevents raw `session_id` leakage in most cases.
3. **Audit fence is structural**: The `SalienceSignal` and `Directive` validators in `bus.py` enforce bounded ref tokens, and the observer’s `_ref()` and `_signal()` mostly comply (except for the `provider` leak in F3).
4. **Seam safety is preserved**: The observer **only** handles `post_tool_call` and `api_request_error` (previously dead hooks), and `pre_llm_call` is used **only** to open windows (no return value consumed). The `has_hook` gate ensures no change to existing dispatch behavior.

---
---
### VERDICT
**SERIOUS_FLAWS**: The **CRITICAL** `KeyboardInterrupt` escape (F1) and **HIGH** audit fence leak (F3) are **real, exploitable** defects that violate core guarantees. The **HIGH** hash collision risk (F2) is theoretically exploitable with effort, and the **MEDIUM** race condition (F4) and `BaseException` gaps (F5) are latent bugs. The **LOW** `turn_id` truncation (F6) is a minor edge case.

**Summary**: The code is **not sound** as-is; F1 and F3 **must** be fixed before merging. F2, F4, and F5 should be addressed in the same PR. F6 is a low-priority cleanup.
