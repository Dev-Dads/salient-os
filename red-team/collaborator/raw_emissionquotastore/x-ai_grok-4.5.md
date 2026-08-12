# emission-quota-store code panel: x-ai/grok-4.5

_finish=stop seconds=270.9 cost=$0.1089 usage={'prompt_tokens': 12608, 'completion_tokens': 13986, 'total_tokens': 26594, 'cost': 0.1089144, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1089144, 'upstream_inference_prompt_cost': 0.0249984, 'upstream_inference_completions_cost': 0.083916}, 'completion_tokens_details': {'reasoning_tokens': 12045, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

### F1 / SAVE FAILURE SILENTLY REOPENS RESTART-RESET / HIGH
**Location:** `collaborator/session.py` — `_persist_emission_counts` (return value discarded); `consume_emission` → `_persist_emission_counts`; contrast `_persist_provenance` which *does* honor save failure.

**Concrete case:**
1. Host sets `emission_quota={"api.example": 3}` and `emission_quota_store=/var/lib/collab/emq.json` on the **same filesystem** as the workspace (common).
2. Session loads missing/fresh `counts={}`, `ok=True`.
3. Agent fills the workspace until `ENOSPC`.
4. Agent emits 3× `net_post` to `api.example`. Each `consume_emission` increments memory and calls `save`; `atomic_write` hits `ENOSPC`, returns `False`; caller ignores it.
5. Session ends. Disk still has empty/stale store.
6. New session loads `{}` / stale low count → full budget again. Repeat forever.

**Why it fails the layer’s own goal:** The point of this store is that a restart must not refresh a drip budget, and the agent must not be able to reset/loosen it. The agent cannot forge the MAC, but it **can** prevent every durable write by exhausting shared FS space. The counter then degrades to session-lived with **no signal**, which is exactly the gap ADR residual-sweep claimed to close. Provenance treats save failure as degraded; emission quota (restrictive) does not — wrong direction for a bound.

**Other control?** No. Caps/byte limits do not bound cross-session count. Untrusted-deny never trips (file is absent or still old+valid MAC).

**Fix:** When a store is configured, persistence failure must fail closed:
- `_persist_emission_counts` returns `bool`; on `False` set `_emission_store_untrusted = True`.
- Prefer consume-before-send at the dispatch point: if store configured and persist fails, do **not** emit (and roll back or keep the sticky untrusted deny).
- Optional init probe write so a non-writable store starts untrusted rather than “fresh allow.”

---

### F2 / CONSUME WHILE UNTRUSTED HEALS STORE TO NEAR-ZERO / MEDIUM
**Location:** `collaborator/session.py` — `consume_emission` / `_persist_emission_counts`; `emission_allowed` untrusted branch; `emissionquotastore.load` returns `({}, False)` on untrusted.

**Concrete case:**
1. Store corrupt → load `({}, False)` → `_emission_store_untrusted=True` → limited hosts denied. Good for this session **if** `emission_allowed` is always consulted.
2. Any path calls `consume_emission(host)` without that gate (bug, future refactor, dual dispatch): memory becomes `{host: 1}`, `save` writes a **valid** MAC’d store with count `1`.
3. This session still has `_emission_store_untrusted=True` (sticky in memory only).
4. **Next** session loads trusted `{host: 1}` → budget refreshed from unknown prior truth.

**Why it fails-open:** Fail-closed is only an in-memory flag; persist is still allowed and overwrites the untrusted bytes with a clean low counter. Restrictive store should not self-heal to “almost fresh” without operator action.

**Other control?** Only call-discipline (`emission_allowed` before `consume`). No defense-in-depth on the write path. Tests never pin “no save while untrusted.”

**Fix:** In `_persist_emission_counts` / `consume_emission`, no-op (or refuse) when `_emission_store_untrusted`; only the operator replacing/deleting the store should clear the condition.

---

### F3 / `_assert_store_outside_workspace` ERRORS ALWAYS SAY `provenance_store` / LOW
**Location:** `collaborator/session.py` — `_assert_store_outside_workspace` (shared by provenance + emission quota).

**Concrete case:** `Session(..., emission_quota_store="./relative.json")` raises `ValueError: provenance_store './relative.json' must be an absolute path`.

**Why it matters:** Not a fail-open; operator confusion only. Same function correctly returns the resolved path for emission I/O (symlink fix is real).

**Other control?** N/A.

**Fix:** Parameterize the config name in the message (`store_label`).

---

### F4 / UNBOUNDED `read_text` ON STORE LOAD / LOW
**Location:** `collaborator/emissionquotastore.py` — `load` (`p.read_text` + `json.loads`); same pattern in `provenancestore.load`.

**Concrete case:** Multi‑GB/corrupt blob at the host store path → memory spike / init pain. Caught as `Exception` → untrusted if the process survives; OOM killer is worse.

**Why:** Agent cannot write the out-of-workspace path; this is host/disk DoS, not agent reset. Totality is “no raise,” not “bounded resources.”

**Other control?** Process limits / operator FS permissions.

**Fix:** Cap read size before parse; over-cap → `({}, False)`.

---

## Fail-direction / domain / restart / totality / regression (attack traces)

| Attack | Result |
|--------|--------|
| **1. Fail-direction** | **Load path is airtight for present-but-untrusted.** `ok=False` → `_emission_store_untrusted=True` → `emission_allowed` returns `False` for any finite limit; `limit is None` / `host is None` still allow; missing file → `({}, True)` fresh allow. Truncated/corrupt/empty/wrong-shape → exception or MAC fail → untrusted deny, not trusted-empty. No untrusted path that allows a limited host **from load alone**. Residual fail-open is **F1/F2** (persist side), not load. |
| **2. Domain separation / substrate** | **Real.** `_DOMAIN` emission `b"salient-emission-quota-store-v1"` ≠ provenance `b"salient-provenance-store-v1"`; `durablestate.mac` derives `HMAC(key, domain)` then `HMAC(store_key, payload)`. Cross-kind load pinned in tests. Provenance domain string unchanged (refactor claim of byte-identical MAC is consistent with this code; no golden vector in-diff). `bytes(key)`/`bytes(domain)` OK for normal `bytes` keys; failures → total untrusted/False. `atomic_write`: same-dir `.tmp` + `os.replace` is correct; errors → `False`. |
| **3. Restart-reset** | **Happy path closes the gap** (load into `_emission_counts`, `< limit` boundary preserved; e2e test). **Broken when saves fail (F1)** or untrusted heal (F2). Off-by-one across reload is correct when persist succeeded. |
| **4. Reachability / totality** | Store path host-only + outside workspace (resolved). Agent cannot rewrite store bytes. `load`/`save`/`atomic_write` never raise. `consume`/`_persist` total. `_assert_store_outside_workspace` fail-**loud** at init (operator config) — fine. Govern path should not see raises from this layer. Model cannot touch `_emission_*` without in-process escape (out of tool threat model). |
| **5. Regression** | `emission_quota_store is None`: store fields idle, in-memory quota unchanged. Unlimited quota: allow before untrusted check. Persist is best-effort and non-raising — **no emission throw** — but “best-effort” is the wrong default for a restrictive cross-session bound when failure is silent (**F1**). |

---

## STEELMAN

Fail-closed load for a restrictive quota is implemented carefully and opposite provenance on purpose: missing ≠ untrusted, MAC/subject/corrupt all deny limited hosts, unlimited hosts stay open, and wiring + tests pin restart inheritance and cross-domain MAC rejection. The substrate’s domain-separated derive-then-HMAC and `os.replace` atomic write are the right primitives and look correctly factored without changing the provenance domain label.

## VERDICT: **SERIOUS_FLAWS**

Load-time fail-closed and domain separation are sound, but the layer **does not meet its primary goal against the agent**: ignored persist failure (easy via same-FS disk fill) silently restores session-lived restart refresh, with no sticky deny comparable to provenance’s save-failure handling.

**Single highest-value fix:** On configured-store save failure, set `_emission_store_untrusted = True` and deny the emission at the dispatch point unless the updated counter was durably written (consume/persist before send; never persist while already untrusted).
