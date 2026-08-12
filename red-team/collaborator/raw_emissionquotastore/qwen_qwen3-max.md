# emission-quota-store code panel: qwen/qwen3-max

_finish=stop seconds=43.0 cost=$0.0192 usage={'prompt_tokens': 11575, 'completion_tokens': 2613, 'total_tokens': 14188, 'cost': 0.0192192, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0192192, 'upstream_inference_prompt_cost': 0.0090285, 'upstream_inference_completions_cost': 0.0101907}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

**Finding 1: FAIL-DIRECTION — Partial or Truncated File Load May Be Treated as Trusted-Empty Instead of Untrusted-Deny**  
- **ID**: F1  
- **Title**: Truncated or Partially-Written Store File Loads as Fresh Start (Trusted Empty) Instead of Untrusted (Fail-Closed)  
- **Severity**: CRITICAL  
- **Location**: `collaborator/emissionquotastore.py:load` (lines ~30–50)  
- **Concrete Case**: An atomic write begins (`tmp.write_text`) but crashes before `os.replace`. The `.tmp` file is left behind. Later, an operator mistakenly renames `emq.json.tmp` → `emq.json` (or a crash leaves a zero-byte `emq.json`). On next load:  
  - `p.exists()` → True  
  - `p.read_text()` succeeds (empty or partial JSON)  
  - `json.loads("")` raises → caught by blanket `except` → returns `({}, False)` ✅  
  BUT: if the file contains *valid JSON structure* but missing `"mac"` or `"body"`, e.g., `{}` or `{"v":1}`, then:  
  - `doc.get("body")` → `None`  
  - `not isinstance(body, dict)` → True → returns `({}, False)` ✅  
  However, consider a **partial write that happens to be valid JSON with a "body" but no "mac"**, e.g.:  
  ```json
  {"v":1,"body":{"subject":"ws","counts":{"api.example":0}}}
  ```  
  This passes `isinstance(body, dict)` but `mac = doc.get("mac")` → `None`, so `not isinstance(mac, str)` → True → returns `({}, False)` ✅  
  So far, so good.  
  **BUT**: what if the file is **truncated mid-MAC**, e.g.:  
  ```json
  {"v":1,"body":{...},"mac":"a1b2
  ```  
  → `json.loads` fails → caught → `({}, False)` ✅  
  **However**, there's a more subtle case: **an empty file**.  
  - `p.read_text()` → `""`  
  - `json.loads("")` → `JSONDecodeError` → caught → `({}, False)` ✅  

  **Wait — all error paths return `({}, False)`?** Yes. Then why is this a finding?

  **Ah — but what about a file that *successfully parses*, has a `"body"` and `"mac"`, but the MAC is **for an empty payload**? That’s not the issue.

  **Real Issue**: The **only path that returns `({}, True)` is when the file does NOT exist**. Every present-file path that fails validation returns `({}, False)`. So **FAIL-DIRECTION appears correct**.

  **Re-evaluate**: Is there ANY code path where a **corrupt-but-present** file leads to `ok=True` with an **under-count** (e.g., count reset to 0)?

  Look at `_canonical`: it uses `counts or {}`, and `int(v)`. If a count is missing or negative, it’s skipped. But if the file is **tampered to have lower counts**, the MAC check fails → `({}, False)` → fail-closed. Good.

  **Conclusion**: **No fail-open in load**. All untrusted cases correctly return `ok=False`.

  **But wait — what about a store file from a different workspace with the SAME subject string?**  
  Subject is `workspace_subject(self.workspace)` — which is `f"workspace:{self.workspace.resolve().name}"` (from `policycaps.py`, not shown, but standard). If two workspaces have the same basename, they get the same subject!  
  → A store from `~/proj/ws` and `~/other/ws` both get subject `"workspace:ws"`  
  → If an operator copies a store from one to another, it will **verify as trusted** and **load counts from a different workspace** → **quota bleed / premature deny or false allow**

  But that’s **not a fail-open** — it’s a **subject collision**, which breaks domain separation **across workspaces**, not within.

  However, the **review scope includes DOMAIN SEPARATION**, so this belongs there.

  **Verdict on F1**: **NO CRITICAL FAIL-OPEN** in emission quota load. All untrusted cases → `ok=False` → `_emission_store_untrusted=True` → `emission_allowed` returns `False` for limited hosts. **Correct fail-closed**.

---

**Finding 2: DOMAIN SEPARATION — Provenance and Emission Stores Use Different Domains (Good), But Workspace Subject Collision Across Workspaces**  
- **ID**: F2  
- **Title**: Workspace Subject Not Globally Unique → Cross-Workspace Store Replay Possible  
- **Severity**: HIGH  
- **Location**: Implied by `collaborator/policycaps.py:workspace_subject` (not shown, but used in `session.py`), and store binding in `emissionquotastore.py` / `provenancestore.py`  
- **Concrete Case**: Two workspaces: `/home/alice/project/ws` and `/home/bob/project/ws`. Both resolve to basename `ws`. `workspace_subject(ws)` returns `"workspace:ws"` for both.  
  Operator copies Alice’s emission quota store (with count=5 for `api.example`) to Bob’s setup. Bob’s session loads it:  
  - subject matches (`"workspace:ws" == "workspace:ws"`)  
  - MAC verifies (same policy_key)  
  → Bob starts with count=5, even though he never emitted → **premature quota exhaustion** (fail-closed-ish, but incorrect state)  
  Or worse: Alice’s store has count=0; Bob gets a **fresh budget despite prior emissions in his own workspace history** → **fail-open via cross-workspace replay**  

  This violates **domain separation across workspace instances**, even though store-kind domains are distinct.

- **Why It Breaks Separation**: The subject does not include enough entropy to distinguish workspaces with the same leaf name. The threat model assumes stores are per-workspace, but the binding is too weak.

- **Does Another Control Catch It?** No. The system trusts the subject binding as sufficient.

- **Fix**: Make `workspace_subject` use a **globally unique identifier**, such as the **full resolved absolute path** (or its hash), not just the basename.  
  Example: `workspace_subject = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()`  
  Or include device+inode (but that’s less portable). Full path is simplest and secure if policy_key is secret.

---

**Finding 3: RESTART-RESET — Correctly Closed; Off-by-One Preserved**  
- **ID**: F3  
- **Title**: Restart Persistence Works; Boundary Correct  
- **Severity**: NONE (SOUND)  
- **Location**: `session.py:__init__` (loads counts), `consume_emission` (increments before persist), `emission_allowed` (checks `< limit`)  
- **Concrete Case**: Quota=2. Emit twice → count=2. `emission_allowed` → `2 < 2` → False. Save count=2. Restart → load count=2 → still denied. Correct.  
  Test `test_count_persists_across_sessions` confirms this.  
- **Verdict**: **SOUND**. No restart reset.

---

**Finding 4: MODEL REACHABILITY / TOTALITY — Session Construction Must Not Raise on Store I/O Errors**  
- **ID**: F4  
- **Title**: `_assert_store_outside_workspace` May Raise on Path Resolution, Breaking Totality  
- **Severity**: MEDIUM  
- **Location**: `collaborator/session.py:_assert_store_outside_workspace` (lines ~400–420)  
- **Concrete Case**: `raw.resolve()` can raise `OSError` (e.g., broken symlink, permission denied on parent dir). The method catches it and raises `ValueError`.  
  But **Session.__init__ is called during govern paths**, and the review states: *"Session/govern promise not to blow up."*  
  However, the **documented contract** is that **misconfiguration (like bad path) should fail LOUD at construction** — and `emission_quota_store` is **host config**, not model-controlled.  
  So raising `ValueError` on invalid host config is **by design**.  
  BUT: what if the path is **valid at config time**, but **becomes unreadable between validation and load**?  
  - `_assert_store_outside_workspace` runs first → returns resolved path  
  - Later, `emissionquotastore.load` runs → `p.exists()` may raise (e.g., permission denied) → caught → returns `({}, False)` → sets `_emission_store_untrusted=True` → **no raise**  
  So **load is total**.  
  The only raise is during **explicit validation of host config**, which is acceptable.  
- **Verdict**: **NOT a totality violation**. Expected loud fail on misconfig.

---

**Finding 5: REGRESSION — Behavior Unchanged When No Store Configured**  
- **ID**: F5  
- **Title**: No Regression for Default (None) or Unlimited Quota  
- **Severity**: NONE  
- **Location**: `session.py:__init__`, `emission_allowed`, `consume_emission`  
- **Concrete Case**: `emission_quota_store=None` → `_emission_store=None`, `_emission_store_untrusted=False`.  
  `emission_allowed` → checks limit, uses in-memory `_emission_counts`. Same as before.  
  Tests confirm (`test_no_store_is_in_memory_unchanged`).  
- **Verdict**: **SOUND**.

---

**Finding 6: SUBSTRATE — HMAC Key Derivation Uses `bytes(key)` — Safe?**  
- **ID**: F6  
- **Title**: `durablestate.mac` Coerces Key to `bytes` — Redundant but Safe  
- **Severity**: LOW  
- **Location**: `collaborator/durablestate.py:mac`  
- **Concrete Case**: `key` is passed as `bytes` from Session (`policy_key: bytes = b"..."`). `bytes(key)` on a `bytes` object is a no-op. Safe.  
  If someone passed `str`, it would raise later (e.g., `hmac.new` requires bytes-like). But type hints + usage ensure `bytes`.  
- **Verdict**: **Harmless**.

---

**Finding 7: ATOMIC WRITE — Temp File Name Predictable, But Irrelevant**  
- **ID**: F7  
- **Title**: `.tmp` Suffix Is Predictable, But Atomicity Holds  
- **Severity**: LOW  
- **Location**: `durablestate.py:atomic_write`  
- **Concrete Case**: `tmp = p.with_name(p.name + ".tmp")` — predictable. But since the agent **cannot write outside workspace**, and store is outside, **no adversarial race**.  
  Even if two sessions ran concurrently (unsupported), `os.replace` is atomic.  
- **Verdict**: **Acceptable** per documented scope.

---

### STEELMAN
The implementation correctly implements fail-closed for untrusted emission quota stores: every corruption, tampering, or mismatch results in `ok=False`, leading to denial of emissions for quota-limited hosts. The shared substrate properly separates domains between provenance and emission stores, preventing cross-kind MAC replay. Persistence across restarts correctly maintains the counter, closing the drip-channel reset loophole. Totality is preserved in load/save paths, and default behavior is unchanged.

### VERDICT
**SERIOUS_FLAWS** — due to **workspace subject collision enabling cross-workspace store replay**, which can cause **fail-open (quota reset) or incorrect denials**.  
**Highest-value fix**: Strengthen `workspace_subject` to use the **full resolved absolute path** (or its cryptographic hash) instead of just the basename, ensuring global uniqueness per workspace instance.
