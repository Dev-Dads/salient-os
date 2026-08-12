# provenance-store code panel: x-ai/grok-4.5

_finish=stop seconds=198.7 cost=$0.1100 usage={'prompt_tokens': 25128, 'completion_tokens': 9989, 'total_tokens': 35117, 'cost': 0.1099724, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1099724, 'upstream_inference_prompt_cost': 0.0500384, 'upstream_inference_completions_cost': 0.059934}, 'completion_tokens_details': {'reasoning_tokens': 8269, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

### F1 / `_assert_store_outside_workspace` checks resolved target only; stores unresolved path / HIGH  
**Location:** `collaborator/session.py` — `_assert_store_outside_workspace` (~lines with `sp = Path(store_path).resolve()` / `ws in sp.parents`); `__init__` assignment `self._provenance_store = store_path` (unresolved)

**Concrete case:**
1. Host: `ws/prov → /var/lib/collab/prov.json` (symlink), `Session(workspace=ws, provenance_store=ws/prov)`.
2. `resolve()` → `/var/lib/.../prov.json` (outside) → assert **passes**.
3. `save()` uses `p = ws/prov`, writes `ws/prov.tmp`, `os.replace` → **replaces the symlink with a regular file inside the workspace**.
4. Contained agent: `rm ws/prov` (workspace write).
5. Next session `load()`: `not p.exists()` → `(set(), False, True)` — **trusted clean**, not degraded.
6. With default `policy_key=b"collab-policy-key"`, agent can instead write a fully valid empty MAC’d store → trusted clean with `ok=True`.

**Why it breaks the goal:** The guard’s job is “no store the agent can write.” Lexically-in-workspace paths (symlink-to-outside) are agent-reachable; delete maps to **missing = fresh trusted**, i.e. silent taint erase / forge-clean. Unresolved stored path can also diverge from the path that was checked (CWD / symlink swap on a host-controlled component).

**Caught elsewhere?** Containment helps only for *outside* targets. After `os.replace`, the live store is in-workspace — bwrap will allow tamper. HMAC does **not** help on delete-to-missing.

**Fix:** Resolve once at construction; persist the resolved path. Refuse if the store path (or any intermediate) is under the workspace **before** trusting resolve-only, e.g. walk parents / use both lexical and resolved `is_relative_to(ws)`. Ideal: require absolute path outside `ws` with no symlink component under `ws`.

---

### F2 / `save()` failure ignored → next session trusts stale store as complete / MEDIUM  
**Location:** `session.py` `_persist_provenance` (return value of `provenancestore.save` discarded); callers `note_autonomous_authorship` / `clear_autonomous_authorship` / `mark_tracking_incomplete`

**Concrete case:** Session notes `evil.sh`; in-memory has it; `save` returns `False` (disk full, perm, parent mkdir fail). This session still warns. New session loads previous file (no `evil.sh`) with valid MAC → `ok=True`, `incomplete=False` — **trusted under-recording**, no degraded bit.

**Why fail-silent:** Durability loss is indistinguishable from “no new taints.” Violates fail-honest for cross-session trust (“surface when tracking can’t be trusted”), even though in-session correctness holds.

**Caught elsewhere?** No. Preview only sets `provenance_tracking_incomplete` from the loaded flag.

**Fix:** On `save(...) is False`, set `_autonomous_tracking_incomplete = True` (and retry persist once if useful). Optionally refuse to claim clean durability in preview when last persist failed.

---

### F3 / Concurrent RMW on one store: last `os.replace` wins, taints dropped, still `ok` / MEDIUM  
**Location:** `provenancestore.save` (`os.replace`); Session load-at-init + later `_persist_provenance` with no lock/version

**Concrete case:** S1 and S2 both load `{a.sh}`. S1 notes `b.sh` → saves `{a,b}`. S2 notes `c.sh` → saves `{a,c}`. `b.sh` taint gone forever; MAC verifies; `incomplete=False`.

**Why:** Atomic replace ≠ atomic read-modify-write. Silent loss of provenance, not degraded.

**Caught elsewhere?** No. Agent doesn’t need store write if the host runs overlapping sessions on one store (CI / parallel workers).

**Fix:** File lock around load/merge/save, or monotonic generation/counter inside the MAC’d body and refuse/merge on stale write; on conflict set `incomplete=True`.

---

### F4 / `missing → trusted fresh` is correct for “no file yet,” but pairs badly with F1 / (amplifies F1; not separate product bug under stated docs)  
**Location:** `provenancestore.load` — `if not p.exists(): return set(), False, True`

Per your scope, human `rm` of an **unreachable** store is documented honest reset. **Not** re-filed alone. It becomes agent-exploitable **only** if F1 (or any reachability hole) holds — then delete is a no-key forge of clean state. Fix F1 (and/or treat “existed this inode/path before” is out of scope unless you add a host-side beacon).

---

### Non-findings (sound under stated model)

| Area | Assessment |
|------|------------|
| **HMAC forge without `policy_key`** | `_canonical` + `hmac.compare_digest`; agent cannot mint `ok=True` without key. |
| **`policy_key` reach** | Host in-process secret; not written by this store; contained run is workspace-only. (Default key is public — fine **iff** store is unwritable.) |
| **Subject binding** | Subject is inside MAC **and** checked against `workspace_subject(workspace)`; cross-workspace replay → `ok=False` + degraded. |
| **Tamper/corrupt/wrong key** | All → `(∅, True, False)`; Session sets `incomplete = incomplete or not ok`. No fake-clean on bad MAC. |
| **Missing vs present-bad** | Missing distinguished; present-bad degraded. |
| **load/save totality** | Broad `except Exception`; save best-effort; govern hooks swallow note/clear/mark errors. Assert **ValueError** on in-workspace store is intentional fail-loud. |
| **Rollback of old same-store versions** | No freshness counter; **out of scope** if agent cannot write the store (human restore = trusted hand). |
| **Poison/bloat via junk paths** | Reference-gated advisory set; junk does not blanket-warn. Unbounded accumulation = documented posture. |
| **Torn read vs `os.replace`** | Same-FS replace is atomic; reader sees old or new full file. Partial live file from this save path is not the design. |

---

## STEELMAN

For the intended deployment — host-chosen absolute store **outside** the workspace, contained agent, non-default or at least non-writable store, single session per store — the design matches its goal: MAC + subject binding blocks forge/replay, load fails honest on untrusted bytes, save is total, and Session maps `not ok` into degraded tracking. The main integrity boundary is correctly “agent cannot write the store,” with HMAC as the second factor against confused-host or bitrot.

## VERDICT: **MINOR_ISSUES**

Primary threat path is sound; the serious hole is defense-in-depth path hygiene (F1) plus silent durability loss (F2/F3), not a break of HMAC math or load’s tamper branches on an unreachable store.

**Single highest-value fix:** At construction, `store = Path(provenance_store).resolve()`, reject unless the store path is **outside** the workspace including “path lives under `ws`” (symlink-in-ws) cases, assign `self._provenance_store = store` (resolved only), and on any `save is False` sticky-set `_autonomous_tracking_incomplete = True` before continuing.
