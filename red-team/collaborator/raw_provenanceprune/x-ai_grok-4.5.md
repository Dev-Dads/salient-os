# prune code panel: x-ai/grok-4.5

_finish=stop cost=$0.0648 usage={'prompt_tokens': 11500, 'completion_tokens': 7010, 'total_tokens': 18510, 'cost': 0.0648424, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0648424, 'upstream_inference_prompt_cost': 0.0227824, 'upstream_inference_completions_cost': 0.04206}, 'completion_tokens_details': {'reasoning_tokens': 5313, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

### F1 / `Path.exists()` turns stat failures into “absent” (false prune) / **HIGH**
**Location:** `collaborator/session.py` — `_prune_stale_provenance` (`if not (self.workspace / rel).exists()`)

**Concrete case:** Manifest contains `sub/evil.sh` (file still on disk). Parent dir `sub` is temporarily unreadable (`chmod 0`, mount glitch, ACL).  
- Py≥3.12: `Path.exists()` does `except (OSError, ValueError): return False` → path classified **stale** → taint discarded → `_persist_provenance()` writes the cleaned manifest.  
- Restore perms (or next session with perms fixed) → `sub/evil.sh` is live again with **no** ⚠.  
Same class: `ELOOP`/weird FS errors swallowed as False on 3.12+; on 3.10–3.11 some `OSError`s raise and are kept — **behavior is version-dependent**.

**Why:** Invariant is “remove taints **only** for absent files” and the docstring says “stat error → keep.” `exists()` does not mean “absent”; it means “stat didn’t succeed.” That both drops a live warning and is a **laundering** step if anything can make `stat` fail across a Session remount (autonomous `chmod`/`mv` of a parent, etc.), because prune **re-persists** the hole.

**Caught elsewhere?** No. Recognizer only sees the in-memory set; durable store now agrees with the wrong set. Human clear / re-`note_autonomous_authorship` only help if a tracked write happens again.

**Fix:** Don’t use `exists()`. Probe with `lstat()` (presence of the directory entry, including broken symlinks) and treat **only** “not found” as stale:

```python
def _prune_stale_provenance(self) -> None:
    stale = []
    for rel in tuple(self._autonomous_authored):
        try:
            p = self.workspace.joinpath(rel)  # see F2
            p.lstat()
        except FileNotFoundError:
            stale.append(rel)
        except (OSError, ValueError):
            continue  # keep taint; never raise
        except Exception:
            continue
    ...
```

---

### F2 / Join/`exists` can raise `ValueError` → `Session.__init__` not total / **MED**
**Location:** `collaborator/session.py` — `_prune_stale_provenance` loop body; call from `__init__` after durable load

**Concrete case:** An entry with an embedded NUL (or other value that makes `pathlib` reject the path) is in `_autonomous_authored` (survives `norm_rel`, can be saved/loaded under a valid MAC).  
`(self.workspace / rel)` or the probe raises `ValueError`. Only `OSError` is caught → exception escapes `__init__`.

**Why:** Stated invariant: “Session.__init__ must remain total (never raise on a bad path/stat).” Prune is on the load path, so one bad manifest entry bricks session construction.

**Caught elsewhere?** No. Store integrity does not imply path-string safety.

**Fix:** Same as F1 — broad except around probe; on any probe failure other than not-found, **keep** the entry. Optionally harden `norm_rel` / load to drop non-path strings so they never persist.

---

### F3 / Path-string prune + re-persist enables cross-session name laundering via rename/move / **MED**
**Location:** `collaborator/session.py` — `_prune_stale_provenance` + re-persist; interaction with path-keyed taints (not inode/content)

**Concrete case:**  
1. Autonomous authors `evil.sh` → taint `{evil.sh}`.  
2. Same (or later) autonomous run `mv evil.sh other.sh` **without** destination re-note (rename not going through `note_autonomous_authorship`). Mid-session, taint still names `evil.sh`; bytes live at `other.sh` untagged (already porous).  
3. Session ends. New `Session`: `evil.sh` missing → **prune removes `evil.sh` and re-persists**.  
4. `mv other.sh evil.sh`. Human `sh ./evil.sh` → recognizer miss; **no** re-taint.

**Without prune:** stale taint on `evil.sh` still fired after the rename-back.  
**With prune:** that residual tripwire is deleted from the durable manifest.

**Why:** Conflicts with “don’t open a laundering path.” Quality goal (human same-name recreate) is path-identity based; prune + persist makes “absent name” permanent even when the autonomous bytes only moved.

**Caught elsewhere?** Mid-session rename porosity already exists; this is **new durable cross-session** clearing of the original name. No inode/content binding. Shell/rename may be gated by caps, but write/delete + external rename is enough; invariant was not “only if shell off.”

**Fix (highest value is still F1):** Prefer F1’s conservative presence check first. If laundering must stay closed under rename, prune is the wrong sole tool: e.g. only prune when absent **and** no session-local reason to keep, or bind stronger identity; or accept rename as out-of-scope **explicitly** and don’t claim “no laundering path.” Minimal improvement: prune without treating “unstatable” as absent (F1) shrinks the easy chmod/EACCES launder.

---

### F4 / Dead “conservative” `except OSError` (false sense of totality) / **LOW**
**Location:** `collaborator/session.py` — `_prune_stale_provenance` `except OSError: continue`

**Concrete case:** Review/tests assume stat errors keep taints (`PruneStaleProvenance` only covers happy present/deleted). On 3.12+, `exists()` already ate the `OSError` → branch never runs; failures become prunes (F1).

**Why:** Control that was supposed to enforce the invariant is ineffective with the API chosen.

**Caught elsewhere?** Tests don’t pin EACCES/NUL/broken symlink.

**Fix:** Covered by F1/F2 rewrite; add tests: unreadable parent keeps taint; NUL entry doesn’t raise; broken symlink kept if using `lstat`.

---

## Steelman

The load-only, path-presence prune is a reasonable **quality** fix for durable advisory accumulation: present → keep, deleted → drop, autonomous rewrite → `note_*`, human rewrite → `clear_*`, untrusted store → empty+degraded, store outside workspace, init must not throw. Intent and call site (post-load, pre-use) match “never mid-session wipe.” Tests correctly pin the happy path (present kept, deleted pruned+re-persisted, recognizer still hits live file).

## Verdict

**SERIOUS_FLAWS** — not because prune is a boundary (it isn’t), but because the implementation **violates its own invariants**: it can drop taints for non-absent paths (`exists()`), can fail non-totally on bad path strings, and durable re-persist turns those mistakes into cross-session laundering.

### Single highest-value fix

Replace `(self.workspace / rel).exists()` with a **total** probe: `lstat`/`stat`, **only** `FileNotFoundError` (or equiv. errno `ENOENT`/`ENOTDIR`) → stale; **any** other `OSError`/`ValueError`/unexpected → keep entry; never raise out of `_prune_stale_provenance`. Add tests for unreadable parent, broken symlink, and bad path strings.
