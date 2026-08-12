# provenance code panel: anthropic/claude-opus-4.1

_finish=stop seconds=102.8 cost=$0.7085 usage={'prompt_tokens': 40500, 'completion_tokens': 1347, 'total_tokens': 41847, 'cost': 0.708525, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.708525, 'upstream_inference_prompt_cost': 0.6075, 'upstream_inference_completions_cost': 0.101025}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

I'll conduct a thorough adversarial review of this autonomy-authorship provenance control.

## FINDINGS

### 1. RECORDING FALSE-NEGATIVE (CRITICAL)
**ID:** RFN-1  
**TITLE:** Autonomous write_file with verification failure bypasses recording  
**SEVERITY:** CRITICAL  
**LOCATION:** collaborator/governance.py:1066-1071  
**CONCRETE CASE:** An autonomous write_file that fails verification (e.g., disk full, permission denied) never reaches the recording branch because it's gated on `cleared`. The file may still exist on disk (partial write, pre-existing file) but won't be recorded in `_autonomous_authored`.
```python
# Only records on CLEARED write
if cleared and getattr(tool, "op", "") == "file.write":
    if human_gated:
        _clear_autonomous_authorship(session, execution.write_set)
    else:
        _record_autonomous_authorship(session, execution.write_set)
```
**WHY IT FAILS:** The control's goal is to make autonomous authorship VISIBLE, but a failed-but-present file that a human later runs would have no provenance warning.  
**OTHER CONTROLS:** None - the snapshot-diff for run_command won't catch a pre-existing write_file attempt.  
**FIX:** Record autonomous write attempts regardless of verification status, clear only on human-approved verified writes.

### 2. MODEL REACHABILITY (LOW)
**ID:** MR-1  
**TITLE:** Model can poison manifest with massive path sets  
**SEVERITY:** LOW  
**LOCATION:** collaborator/session.py:244-250  
**CONCRETE CASE:** A malicious autonomous run_command that generates thousands of files (e.g., `for i in {1..10000}; do touch file$i; done`) will record all paths, making the manifest unwieldy and the preview unreadable.
**WHY IT FAILS:** While it doesn't bypass recording, it noise-blinds the human by making the warning useless.  
**OTHER CONTROLS:** The per-action nature limits damage; session restart clears the manifest.  
**FIX:** Cap the number of paths recorded per action or total manifest size.

### 3. FALSE-POSITIVE (MEDIUM)
**ID:** FP-1  
**TITLE:** Symlink target changes not detected in snapshot diff  
**SEVERITY:** MEDIUM  
**LOCATION:** salienceos/verifier/observers.py:1226-1236  
**CONCRETE CASE:** An autonomous run changes a symlink's target without changing its name. The snapshot records `"symlink:<target>"` but `observed_write_set` only compares keys and values - if a symlink `build.sh -> safe.sh` becomes `build.sh -> evil.sh`, this is detected. But if the symlink itself isn't touched and only the target file's content changes, it won't be in the write_set.
**WHY IT FAILS:** A human running `./build.sh` (a symlink) wouldn't get a warning if only the target changed.  
**OTHER CONTROLS:** The recognizer would still match if the symlink name is referenced.  
**FIX:** Already handled correctly - symlink target changes ARE detected by the value comparison.

### 4. LAUNDERING VIA CLEAR (LOW)  
**ID:** LVC-1  
**TITLE:** Race condition in clear-then-reauthor sequence  
**SEVERITY:** LOW  
**LOCATION:** collaborator/governance.py:1068-1071  
**CONCRETE CASE:** 
1. Autonomous writes `build.sh` (tainted)
2. Human approves write to `build.sh` (clears taint) 
3. Autonomous immediately rewrites `build.sh`
4. Human runs `sh build.sh` - no warning shown

**WHY IT FAILS:** The unconditional add in `note_autonomous_authorship` does re-taint, but there's a TOCTOU window.  
**OTHER CONTROLS:** Requires precise timing; human would see multiple actions in history.  
**FIX:** Acceptable as documented - human vetting creates trust.

### 5. TOTALITY (MEDIUM)
**ID:** TOT-1  
**TITLE:** Snapshot can raise on filesystem race  
**SEVERITY:** MEDIUM  
**LOCATION:** salienceos/verifier/observers.py:1234  
**CONCRETE CASE:** During `os.walk`, if another process deletes a file between directory listing and `read_bytes()` call, `FileNotFoundError` propagates up, breaking the "never raises" promise.
**WHY IT FAILS:** The try/except in `_record_autonomous_authorship` (governance.py:929) catches this, but the snapshot itself isn't wrapped.  
**OTHER CONTROLS:** The governance wrapper catches and swallows the exception.  
**FIX:** Wrap `p.read_bytes()` in try/except, treat racing deletions as absent.

## STEELMAN

The control achieves its stated goal effectively: autonomous file authorship is recorded and surfaced to humans at approval time through a simple, cross-platform mechanism. The porous token-matching approach is appropriate for an advisory control, avoiding the complexity of full dataflow tracking while catching the common dangerous patterns. The clear-on-human-write design correctly prioritizes avoiding false positives over perfect recall.

## VERDICT

**MINOR_ISSUES** - The control successfully makes autonomous authorship visible as designed, with one critical issue around verification failures that could leave dropped files untracked. The single highest-value fix: Record autonomous write_file attempts regardless of verification status to ensure failed writes that leave files on disk are still tracked.
