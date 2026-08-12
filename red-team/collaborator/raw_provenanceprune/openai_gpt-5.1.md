# prune code panel: openai/gpt-5.1

_finish=stop cost=$0.0327 usage={'prompt_tokens': 10442, 'completion_tokens': 1961, 'total_tokens': 12403, 'cost': 0.0326625, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0326625, 'upstream_inference_prompt_cost': 0.0130525, 'upstream_inference_completions_cost': 0.01961}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID 1 / PRUNE USES RAW RECORDED KEYS, NOT NORMALIZED / SEVERITY: LOW / LOCATION: collaborator/session.py:_prune_stale_provenance (around line with `for rel in tuple(self._autonomous_authored):`)

CONCRETE CASE  
- Suppose an earlier, buggy version of the code (or a future change elsewhere) adds raw paths to `_autonomous_authored` that are not in the normalized workspace-relative POSIX form assumed everywhere else (e.g. `"./build.sh"` or `"sub\\x.sh"` instead of `"build.sh"` and `"sub/x.sh"`).  
- Those entries are stored durably by `provenancestore.save` and later loaded into `_autonomous_authored` by `provenancestore.load` without further normalization.  
- On load, `_prune_stale_provenance` iterates those raw entries and checks `(self.workspace / rel).exists()` directly.

WHY THIS MATTERS  
- The core invariant is: pruning should only remove taints for ABSENT files and never drop a live warning.  
- That invariant is expressed in terms of the *intended* representation: workspace-relative normalized POSIX paths (the same form `norm_rel` produces and that the recognizer expects).  
- If `_autonomous_authored` ever contains keys that are logically “the same file” but textually different due to normalization issues (e.g. `"./build.sh"` recorded vs actual file at `build.sh`), then:
  - `(self.workspace / "./build.sh").exists()` will still work on normal platforms, so you’re probably safe for common variants like `./`.  
  - However, the assumption that `_autonomous_authored` is always in the canonical form is not enforced at the load site. If a caller or a future refactor accidentally writes truly malformed entries, `_prune_stale_provenance`’s behavior might diverge from what the recognizer sees.
- The current provenance path normalization is applied in `note_autonomous_authorship` and `clear_autonomous_authorship`, but not at the durable load boundary; `provenancestore.load` returns whatever it deserializes. There is no local guarantee here that all paths are re-normalized before pruning.  
- Practically: today, with the shipped code paths, this is unlikely to create a concrete *security* failure (because:
  - storage tests enforce sets of simple strings that came from `note_autonomous_authorship`, and  
  - `norm_rel` is already used on the write path).  
  But the prune step is written as if `_autonomous_authored` is guaranteed normalized, without asserting or re-establishing that invariant.

WHETHER ANOTHER CONTROL CATCHES IT  
- Indirectly mitigated:  
  - `note_autonomous_authorship` uses `norm_rel`, so normal runtime writes keep the set normalized.  
  - The tests for `provenancestore` write simple canonical strings; they don’t inject tricky forms.  
- However, nothing at the Session/provenance boundary *enforces* or re-normalizes after `provenancestore.load`, so if another entry point (tests, maintenance tools, or a future migration) writes non-normalized values into the durable store, `_prune_stale_provenance` will operate on whatever is there.

FIX  
- Cheap, defensive hardening at the load site before pruning, to restore the invariant that `_autonomous_authored` holds only normalized workspace-relative POSIX paths:

```python
# After load, before _prune_stale_provenance()
from collaborator.provenance import norm_rel
authored, incomplete, ok = provenancestore.load(
    self._provenance_store, workspace_subject(self.workspace), self.policy_key
)

# Re-normalize defensively; skip anything that no longer normalizes to a valid rel path.
normalized = set()
for p in authored:
    r = norm_rel(p)
    if r:
        normalized.add(r)
self._autonomous_authored = normalized
```

- Optionally, if any entries change during normalization, you may want to treat this as a state change and re-persist — but that’s not strictly required for safety; it just cleans the store.


STEELMAN (ARGUE FOR THE AUTHOR)

- The author’s main invariants are:
  - Pruning only removes taints for ABSENT files (never a present one).  
  - An autonomous re-create gets re-recorded and thus re-tainted.  
  - A human re-create is considered human-authored and not flagged.  
  - The prune is load-only and best-effort; errors conservatively keep taints; `Session.__init__` must not raise because of bad stat.  

- On those points, the implementation is strong:

  - **No live-warning drop on normal paths**  
    - `_prune_stale_provenance` only calls `.discard` on paths for which `(self.workspace / rel).exists()` returned `False` (and only inside a try/except that treats *any* `OSError` as “keep the taint”).  
    - There is no code path that removes an entry if existence is uncertain; unknown or error → conservatively keep.  
    - Tests explicitly cover:
      - A present file remains tainted across sessions (`test_present_file_is_kept_across_sessions`).  
      - A deleted file is pruned and that prune persists (`test_deleted_file_is_pruned_at_load_and_repersisted`).  
      - A present autonomous file still yields a warning via the recognizer after pruning (`test_prune_never_drops_a_live_warning`).

  - **No laundering path via provenance_store**  
    - `_assert_store_outside_workspace` enforces:
      - store path must be absolute (no cwd ambiguity),  
      - both resolved and lexical paths must be outside the workspace (to avoid symlink “resolves outside but lives inside” tricks).  
    - The store is integrity-protected (HMAC + workspace_subject) and:
      - on bad MAC / wrong subject / corrupt JSON, the load returns `(set(), True, False)` and the session flags `_autonomous_tracking_incomplete`.  
      - untrusted stores never contribute taints; more importantly, they never create a fake-clean: you get degraded tracking instead of “everything is fine.”
    - `_persist_provenance` is best-effort but fail-honest: a failure sets `_autonomous_tracking_incomplete`, so you don’t later silently trust an incomplete store.
    - The pruning step never lets the agent *write* the store: it runs in `Session.__init__`, using the same resolved path returned from `_assert_store_outside_workspace`, and only from the trusted host process.

  - **Totality and error handling**  
    - `_prune_stale_provenance` catches `OSError` on `exists()` and simply continues, keeping taint entries, ensuring:
      - no crash on bad paths or transient I/O failures,  
      - “bad path/stat” cannot break `Session.__init__` because of pruning.  
    - Both recognizer functions (`_tokenize`, `_rel_candidates`, `references_autonomous_file`) and `norm_rel` are total by design and extensively guarded with broad `except Exception` returning safe defaults.

  - **Re-creation semantics**  
    - Autonomous re-creation:
      - Uses `note_autonomous_authorship` with `norm_rel`, so any new drop refreshes the taint explicitly.  
      - Because pruning only removes entries when the file is *currently* absent at load-time, any autonomous re-create after a delete will be re-recorded correctly.
    - Human re-creation:
      - `clear_autonomous_authorship` also uses `norm_rel`; a human-approved write will discard the taint both in-memory and durably (via `_persist_provenance`), aligning with the invariant that a human-vetted version should not surface a stale warning.

  - **Prune timing**  
    - The prune runs once per `Session` construction right after loading the durable manifest, never mid-session, preserving “no mid-session surprise” semantics.

VERDICT  

Overall assessment against the stated invariants (never drop a live warning; don’t open a laundering path; stay total):

- The pruning logic is conservative on errors and only clears entries when existence checks say “definitely absent.”  
- The out-of-workspace + MAC + subject binding design for the store continues to prevent laundering, and pruning does not add a new path for the agent to manipulate the durable record.  
- `Session.__init__` remains total in the presence of bad stored paths or FS errors during prune.

The only identified issue is a **minor robustness gap** around re-normalizing loaded paths; it does not, under the current code paths, create a concrete violation of the invariants, but tightening it would future-proof the invariant that the manifest is always in canonical form.

VERDICT: **MINOR_ISSUES**

Highest-value fix:  
- Re-normalize `authored` entries with `norm_rel` immediately after `provenancestore.load` and before calling `_prune_stale_provenance`, discarding any entries that no longer normalize to a valid workspace-relative path. This makes the representation invariant explicit and protects against future misuse of the durable store.
