# emission-quota-store code panel: anthropic/claude-opus-4.1

_finish=stop seconds=101.6 cost=$0.2963 usage={'prompt_tokens': 13608, 'completion_tokens': 1229, 'total_tokens': 14837, 'cost': 0.296295, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.296295, 'upstream_inference_prompt_cost': 0.20412, 'upstream_inference_completions_cost': 0.092175}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

I'll perform a thorough adversarial review of this durable emission-quota store implementation, looking for fail-open conditions, domain separation issues, restart-reset vulnerabilities, model reachability problems, and regressions.

## FINDINGS

### 1. CRITICAL / PARTIAL WRITE LOADS AS TRUSTED-EMPTY / durablestate.py:52 / emissionquotastore.py:71

**CONCRETE CASE**: If `atomic_write` fails mid-write (disk full, process killed after truncating but before os.replace), the `.tmp` file exists but the main file is missing or truncated. On next load, `Path(path).exists()` returns False (line 71), so `load` returns `({}, True)` - a trusted fresh start. This silently RESETS the quota instead of failing closed.

**WHY IT FAILS-OPEN**: A quota-exhausted host gets its budget refreshed by a partial write failure, exactly the restart-reset vulnerability this aims to prevent.

**ANOTHER CONTROL**: No - this is the single persistence point.

**FIX**: Check for `.tmp` file existence in `load` and return `({}, False)` if present, indicating an incomplete write.

### 2. HIGH / MISSING STORE DIFFERENT FROM UNTRUSTED / session.py:244-248

**CONCRETE CASE**: When `emission_quota_store` path is configured but the file doesn't exist yet (first run), `emissionquotastore.load` returns `({}, True)`. This is correct. However, if the store file is later DELETED (by disk corruption, accidental rm, etc.), the next session loads it as `({}, True)` - a fresh trusted start that RESETS the counter to 0.

**WHY IT FAILS-OPEN**: An exhausted quota can be refreshed by deleting the store file between sessions. The distinction between "never existed" and "existed but was deleted" is lost.

**ANOTHER CONTROL**: No - the operator would need to notice the missing file.

**FIX**: After first successful save, create a sentinel file `.initialized`. In load, if sentinel exists but main file doesn't, return `({}, False)` as untrusted.

### 3. MEDIUM / CONSUME-BEFORE-PERSIST ALLOWS DOUBLE-SPEND / session.py:384-386

**CONCRETE CASE**: In `consume_emission`, the in-memory counter is incremented (line 385) before `_persist_emission_counts()` is called (line 386). If persistence fails (disk full, permission error), the in-memory count is still incremented for this session, but the next session will load the old count. With quota=5 and current count=4, if persist fails on the 5th emission, this session correctly denies the 6th, but a restart reloads count=4 and allows another emission.

**WHY IT FAILS-OPEN**: Persistence failure doesn't prevent the emission (best-effort), but the failed persist means the count isn't durable, allowing budget refresh on restart.

**ANOTHER CONTROL**: Partial - the emission still happens and is logged elsewhere.

**FIX**: Document this edge case explicitly, or attempt persist before incrementing in-memory (though this could deny on transient disk errors).

### 4. LOW / DOMAIN SEPARATION RELIES ON EXACT BYTES / durablestate.py:25-27

**CONCRETE CASE**: The domain separation in `mac()` uses `bytes(domain)` directly. If a future store accidentally passes a domain as `str` instead of `bytes`, Python's `bytes(str)` creates `b"<class 'str'>"` rather than encoding the string, potentially causing domain collision.

**WHY IT BREAKS-SEPARATION**: Type confusion could make different domains produce the same derived key.

**ANOTHER CONTROL**: Current callers pass bytes correctly.

**FIX**: Add `isinstance(domain, bytes)` assertion or explicitly handle str encoding.

### 5. LOW / ATOMIC_WRITE TMP NAME COLLISION / durablestate.py:36

**CONCRETE CASE**: The tmp filename is `p.name + ".tmp"`. If two processes try to write the same store simultaneously (documented concurrent session edge case), both write to the same `.tmp` file, causing a race where one's partial write could be replaced by the other's complete write, then both try os.replace.

**WHY IT BREAKS-TOTALITY**: Race condition in concurrent writes, though documented as unsupported.

**ANOTHER CONTROL**: Documented as sequential-session design.

**FIX**: Use a unique tmp name like `f"{p.name}.tmp.{os.getpid()}.{time.time()}"`.

## STEELMAN

The implementation correctly handles most security-critical paths: untrusted stores fail closed for limited hosts, domain separation prevents cross-store replay, and the persisted counter does survive restarts in the normal case. The atomic write pattern and HMAC integrity checking are sound, and the test coverage validates the core security properties including the fail-closed behavior for corrupted stores.

## VERDICT

**MINOR_ISSUES** - The core security model is sound but has edge cases around partial writes and missing stores that could allow quota refresh. The highest-value fix is detecting incomplete writes via `.tmp` file presence to prevent treating partial writes as trusted-empty.
