# provenance-store code panel: google/gemini-2.5-pro

_finish=stop seconds=67.0 cost=$0.1027 usage={'prompt_tokens': 26136, 'completion_tokens': 7005, 'total_tokens': 33141, 'cost': 0.10272, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.10272, 'upstream_inference_prompt_cost': 0.03267, 'upstream_inference_completions_cost': 0.07005}, 'completion_tokens_details': {'reasoning_tokens': 5323, 'image_tokens': 0, 'audio_tokens': 0}}_

Excellent. As a senior security engineer, I will review this persistence layer with an adversarial mindset, assuming the author is wrong until proven otherwise. My focus is on whether the implementation fails its own goals or breaks hard invariants.

Here is my review.

***

### Overall Assessment

The core integrity mechanism (HMAC over canonical data) and the fail-honest logic for corrupt or tampered files are well-designed and robustly implemented. The out-of-workspace check is also strong against common bypasses. However, the implementation contains two serious flaws that lead to silent provenance loss, directly undermining the feature's durability and integrity goals.

---

### Finding 1

*   **ID:** F-PROV-1
*   **TITLE:** Concurrent Sessions Race on `save()`, Causing Silent Provenance Loss
*   **SEVERITY:** CRITICAL
*   **LOCATION:** `collaborator/provenancestore.py:101` (the `save` function)
*   **CONCRETE CASE:**
    1.  A user starts two Collaborator processes (e.g., in two separate terminal windows) pointing to the same workspace and the same `provenance_store` file.
    2.  Process A's `Session` loads the store (e.g., empty).
    3.  Process B's `Session` loads the same empty store.
    4.  In Process A, an autonomous action drops `a.sh`. `note_autonomous_authorship` calls `save()` with the set `{"a.sh"}`.
    5.  In Process B, an autonomous action drops `b.sh`. `note_autonomous_authorship` calls `save()` with the set `{"b.sh"}`.
    6.  Process A successfully writes `prov.json.tmp` and atomically replaces `prov.json`. The file on disk now correctly contains the taint for `a.sh`.
    7.  Process B, which read the *original* empty state, now writes its own `prov.json.tmp` containing only `{"b.sh"}` and atomically replaces `prov.json`.
    8.  **Result:** The final state of `prov.json` contains only the taint for `b.sh`. The provenance for `a.sh` has been silently lost. A new session will not know `a.sh` was agent-authored.
*   **WHY IT FORGES/TAMPERS/FAILS-SILENT/BREAKS-TOTALITY:** This is a classic read-modify-write race condition that causes a **silent data loss**, breaking the **durability** and **integrity** goals of the persistence layer. The `os.replace` call ensures the write itself is atomic, preventing torn reads, but it does not protect the overall read-modify-write sequence from concurrency issues. This is a fail-silent condition, not a fail-honest one.
*   **WHETHER ANOTHER CONTROL CATCHES IT:** No. This is a fundamental race condition in the persistence layer's design. No other control in the system is aware of or can mitigate this.
*   **FIX:** Implement file-level locking. Before any modification, the process must acquire an exclusive lock on the store file. The `save` function's logic must change to:
    1.  Acquire lock.
    2.  **Re-read** the current state from the store file inside the locked section.
    3.  Merge the in-memory changes (`self._autonomous_authored`) with the state just read from disk.
    4.  Write the new, merged state to the temporary file and perform the atomic replace.
    5.  Release lock.
    The `load` function should also acquire a shared lock to prevent reading a file that is in the process of being replaced if `os.replace` atomicity is not guaranteed on all target platforms.

---

### Finding 2

*   **ID:** F-PROV-2
*   **TITLE:** Agent-Induced Rollback Attack Erases Provenance
*   **SEVERITY:** HIGH
*   **LOCATION:** `collaborator/provenancestore.py` (conceptual: lack of freshness/monotonicity)
*   **CONCRETE CASE:**
    1.  The `provenance_store` is initially empty. A backup is made: `cp store.json store.json.bak`.
    2.  An autonomous run drops a malicious file, `evil.sh`. The store is saved and now correctly contains a taint for `evil.sh`. The HMAC is valid.
    3.  The agent, knowing the backup exists, constructs a `run_command` to restore it: `run_command("mv /path/to/store.json.bak /path/to/store.json")`.
    4.  A deceived human approves this command. The store file is now replaced with the old, validly-signed, empty version.
    5.  A new `Session` starts. It loads `store.json`, which verifies perfectly (correct HMAC, correct subject) but is empty. The provenance for `evil.sh` is gone.
    6.  The agent now proposes `run_command("sh ./evil.sh")`. The `_references_autonomous` check in `governance.py` finds no taint. The human is shown no `⚠` warning and approves the command, running the malicious script without the intended advisory control.
*   **WHY IT FORGES/TAMPERS/FAILS-SILENT/BREAKS-TOTALITY:** This allows the agent to **erase provenance**, defeating the store's primary goal. While the agent cannot *forge* a new store, it can cause a valid *old* store to be replayed. This is a form of tampering by rollback, which the current HMAC-only scheme does not prevent as it provides integrity but not freshness.
*   **WHETHER ANOTHER CONTROL CATCHES IT:** No. The attack vector is an approved `run_command`. While `names_sensitive_path` could theoretically flag the store's path if it's in a sensitive directory, this is not a reliable or intended defense. The system has no mechanism to detect that the provenance store itself is being manipulated.
*   **FIX:** Incorporate a strictly monotonic counter or a high-resolution timestamp into the HMAC-protected payload in `_canonical`. The `load` function must then reject any store whose counter/timestamp is not strictly greater than the last one successfully loaded. For cross-session protection, the last-seen counter would need to be stored, either in memory for the process lifetime or in a separate, tiny, un-tamperable file. A version counter is the standard defense against such replay attacks.

---

### STEELMAN & VERDICT

*   **STEELMAN:** The author correctly identified the most direct threat—an agent within its container rewriting the store—and built a robust defense with the out-of-workspace guarantee and HMAC. The fail-honest behavior on corruption is exemplary. The identified race condition and rollback attack require either concurrent processes or the agent successfully deceiving a human into running a specific file operation, scenarios that may have been considered secondary to the primary threat of direct forgery. The use of `os.replace` shows an understanding of atomic file operations to prevent data corruption from partial writes.

*   **VERDICT:** **SERIOUS_FLAWS.** While the cryptographic integrity and basic path safety are sound, the system fails its core durability and integrity goals in two realistic scenarios: concurrent operation and agent-induced rollback. These are not minor edge cases but fundamental flaws in the persistence strategy that lead to silent provenance loss.

    **The single highest-value fix is to implement file-level locking to resolve the concurrency race condition (F-PROV-1), as this represents an immediate data loss bug in a likely usage pattern.**
