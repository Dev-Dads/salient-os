# emission-quota-store code panel: google/gemini-2.5-pro

_finish=stop seconds=60.8 cost=$0.0775 usage={'prompt_tokens': 12845, 'completion_tokens': 6141, 'total_tokens': 18986, 'cost': 0.07746625, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.07746625, 'upstream_inference_prompt_cost': 0.01605625, 'upstream_inference_completions_cost': 0.06141}, 'completion_tokens_details': {'reasoning_tokens': 4720, 'image_tokens': 0, 'audio_tokens': 0}}_

Excellent. This is a high-quality target for an adversarial review. The design intent is clear, the threat model is explicit, and the code is dense with security-critical logic. I will proceed with the review, assuming the author is wrong until proven otherwise.

***

### Overall Assessment

The durable state substrate (`durablestate.py`) is sound, correctly implementing domain-separated MACs and atomic writes. The wiring in `Session` correctly interprets the `ok` flag from the store's `load` function to implement the fail-closed policy for emission quotas.

However, a critical vulnerability in the emission quota store's `load` function subverts this entire control structure. It allows a maliciously crafted (but correctly signed) store file to be parsed as "trusted" while silently resetting the emission counters, completely defeating the anti-restart protection.

***

### Finding 1: Critical Fail-Open on Negative Counts

*   **ID:** EQ-1
*   **TITLE:** Fail-Open on Negative Counts in Store Resets Emission Budget
*   **SEVERITY:** CRITICAL
*   **LOCATION:** `collaborator/emissionquotastore.py:101-104`
*   **CONCRETE CASE:**
    1.  A session runs with a quota of `{"api.example": 10}` and a durable store. It makes 9 emissions to `api.example`. The store is persisted with `{"counts": {"api.example": 9}}`.
    2.  An operator (or a process with permission to edit the store file) modifies the store on disk to contain a negative count: `{"counts": {"api.example": -999}}`.
    3.  The operator (or a compromised signing oracle) re-signs this modified file, producing a valid MAC for the body containing the negative count. The `save` function would never produce this, but `load` must defend against it.
    4.  A new `Session` is instantiated. It calls `emissionquotastore.load`.
    5.  `load` parses the file. The `body` contains `{"counts": {"api.example": -999}}`.
    6.  `load` computes the `payload` for MAC verification using `_canonical`, which includes the negative count. The MAC check at line 97 passes, because the file was correctly signed for this malicious content.
    7.  The code proceeds, believing the file is trusted.
    8.  At lines 101-104, `load` iterates through the counts to build the dictionary to return. The check `if iv >= 0:` filters out the `-999` entry.
    9.  `load` returns `({}, True)`.
    10. `Session.__init__` receives `counts = {}` and `ok = True`. It sets `self._emission_counts = {}` and `self._emission_store_untrusted = False`.
    11. The session now believes the emission count for `api.example` is 0. The budget of 10 has been silently and completely refreshed. The next 10 emissions will be allowed.

*   **WHY IT FAILS-OPEN:** The function validates the MAC against data *before* it filters for semantic validity (non-negative counts). It then returns the *filtered* data while reporting that the *unfiltered* data was valid. This discrepancy allows an attacker to craft a file that passes integrity checks but yields a trusted, empty result, violating the primary goal of preventing budget refreshes on restart.
*   **WHETHER ANOTHER CONTROL CATCHES IT:** No. This vulnerability directly subverts the MAC integrity check, which is the sole control responsible for trusting the persisted state. The `Session`'s fail-closed logic is never triggered because `load` incorrectly returns `ok=True`.
*   **FIX:** The data must be validated *before* the MAC is checked, or the validation failure must cause the function to return `ok=False`. The simplest fix is to check for negative counts and immediately return untrusted if any are found.

    ```python
    # collaborator/emissionquotastore.py

    def load(path, subject, key):
        # ... (try/except blocks as before)
        try:
            # ... (json.loads, get body/mac as before)
            
            # NEW: Validate counts before trusting the payload
            raw_counts = body.get("counts") or {}
            if not isinstance(raw_counts, dict) or any(not isinstance(v, int) or v < 0 for v in raw_counts.values()):
                return {}, False

            payload = _canonical(body.get("subject"), raw_counts) # Use validated counts
            if not hmac.compare_digest(_mac(key, payload), mac):
                return {}, False                                   # tampered / wrong key -> untrusted
            if str(body.get("subject")) != str(subject):
                return {}, False                                   # a store for a DIFFERENT workspace
            
            # Counts are already validated, just need to stringify keys
            counts = {str(k): v for k, v in raw_counts.items()}
            return counts, True
        except Exception:  # noqa: BLE001
            return {}, False
    ```

***

### Steelman & Verdict

**STEELMAN:** The author has clearly separated concerns, creating a robust `durablestate` substrate for security primitives that is used correctly by both stores. The fail-direction logic in `Session` is explicit and correctly implemented: an untrusted store for a restrictive bound (quota) correctly fails closed, while an untrusted store for an advisory bound (provenance) correctly fails honest. The code is designed to be total, preventing crashes from corrupted state files, and correctly handles the "missing file" case as a fresh start.

**VERDICT:** **SERIOUS_FLAWS.** The implementation contains a single but critical vulnerability that completely undermines the security goal of the emission quota store.

While the surrounding architecture is sound, the `load` function can be tricked into treating a maliciously crafted file as trusted, allowing an attacker with write access to the store file (and signing ability) to silently reset the emission budget. This directly violates the core requirement to prevent restarts from refreshing a drip budget.

**Highest-Value Fix:** Implement validation of count non-negativity within `emissionquotastore.load` and ensure that any validation failure results in the store being treated as untrusted (`ok=False`), as detailed in finding EQ-1.
