# provenance code panel: qwen/qwen3-max

_finish=stop seconds=59.2 cost=$0.0775 usage={'prompt_tokens': 34425, 'completion_tokens': 3056, 'total_tokens': 37481, 'cost': 0.0775398, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0775398, 'upstream_inference_prompt_cost': 0.053703, 'upstream_inference_completions_cost': 0.0238368}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

**FINDING 1 / AUTONOMOUS run_command CAN WRITE WITHOUT BEING RECORDED (FALSE NEGATIVE)**  
**SEVERITY**: CRITICAL  
**LOCATION**: `collaborator/governance.py:2085–2095` (`execute_and_verify` exit-branch snapshot logic)  
**CONCRETE CASE**: An autonomous `run_command` executes **without** `require_code_protection=True`, yet still writes a file to the workspace. This occurs if `code_protection_available()` returns `True` but the session lacks the signed `shell.contained_autonomy` cap. In this case, `govern_action` floors autonomy (sets leash=`PROPOSE_FIRST`), so the action is held. But if later approved **with** `human_gated=True`, it runs uncontained — and that’s fine. However, consider the converse: an autonomous run where `code_protection_available()` is `True` **and** the signed cap **is** present → `require_code_protection=True`. Now suppose `snapshot_tree(session.workspace)` in the pre-execution hook **raises an exception** (e.g., due to a racing delete of the workspace root). The `_pre = None` fallback is taken. Post-execution, `_pre is not None` fails, so **no diff is computed and no paths are recorded**, even though the autonomous contained run **did write files**.  
**WHY IT FAILS ITS OWN GOAL**: The control’s stated recall boundary is: *“autonomous CONTAINED run_command: recorded via snapshot_tree pre/post diff”*. But here, an autonomous contained run **is not recorded** due to a benign I/O error during snapshotting. This violates the documented guarantee that all autonomous droppers are flagged.  
**ANOTHER CONTROL CATCHES IT?**: **No.** There is no fallback recording mechanism. The human may later run the dropped file with full reach and see **no warning**, defeating the entire provenance-flagging purpose.  
**FIX**: Move the snapshot try/except **inside** the `if require_code_protection:` block and **fail closed on recording failure** by denying the action. Alternatively, log the failure and **still record all observed writes from `execution.write_set`** — but `run_command` doesn’t populate `write_set`. So the only robust fix is: if pre-snapshot fails under `require_code_protection`, **deny the action** (since the system cannot fulfill its audit promise).

---

**FINDING 2 / MODEL CAN TRIGGER FALSE POSITIVE VIA ABSOLUTE PATH COLLISION**  
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/provenance.py:75–85` (`_rel_candidates`)  
**CONCRETE CASE**: Workspace is `/home/user/project`. Model runs autonomous `write_file` with `path="/tmp/evil.sh"`. This is **outside** the workspace, so `resolve_in_workspace` denies it — good. But separately, the model later proposes `run_command` with `command=["sh", "/home/user/project/../project/build.sh"]`. The absolute path resolves to `/home/user/project/build.sh` (valid). `_rel_candidates` computes `ta = posixpath.normpath("/home/user/project/../project/build.sh") = "/home/user/project/build.sh"`, which starts with `ws = "/home/user/project"`, so it relativizes to `"build.sh"`. Now suppose an autonomous action **did** write `build.sh` → it’s in `_autonomous_authored`. The match fires correctly. But consider: model proposes `run_command` with `command=["sh", "/opt/external/script.sh"]`. If by chance `/opt/external/script.sh` **does not exist**, nothing happens. But if the operator **symlinks** `/opt/external/script.sh → /home/user/project/malicious.sh`, then the command runs the workspace file. However, `_rel_candidates` sees an absolute path **not under workspace**, so **does not relativize it**, and **no match occurs** — a false negative (acceptable per design). The real issue is the opposite: suppose workspace is `/a`, and model proposes `run_command` with `command=["sh", "/a/b/../c/d.sh"]`. This normalizes to `/a/c/d.sh` → relativizes to `c/d.sh`. Now, if an autonomous action wrote a file at `b/../c/d.sh` (which resolves to `c/d.sh`), it would be recorded as `c/d.sh`. So match works. **But**: what if the model uses an absolute path that **textually contains the workspace prefix but points elsewhere**? E.g., workspace = `/app`, and command token = `/app-secret/config.sh`. `ta.startswith(ws + "/")` is **false** because `/app-secret` does **not** start with `/app/`. So no relativization → no false positive. However, consider Windows-style paths on Unix: `command=["sh", "C:\\fake\\build.sh"]`. `norm_rel` converts `\` to `/` → `C:/fake/build.sh`. If workspace is `/C:/fake`, then `ta.startswith(ws + "/")` could match, relativizing to `build.sh`. But `resolve_in_workspace` would have already denied such a path during write. So **no concrete false positive**.  
Wait — better case: workspace = `/safe`, and an autonomous action writes `safe_subdir/payload.sh`. Separately, the human has a file at `/safe_subdir/payload.sh` **outside the workspace**. The model proposes `run_command` with `command=["sh", "/safe_subdir/payload.sh"]`. `_rel_candidates` sees absolute path `/safe_subdir/payload.sh`. `ws = "/safe"`. Does `"/safe_subdir/payload.sh".startswith("/safe/")`? **No**. So no relativization. No match. Good.  
Actually, the **real false positive risk** is: a **benign token** like `echo build.sh` gets split into tokens `["echo", "build.sh"]`. `"build.sh"` is a candidate. If an autonomous action **happened to write** `build.sh`, it matches — but that’s **correct**, because the command **does reference** the file (even if just as an argument to `echo`). The doc says: *“POROUS argv-token recognizer”*. So this is **by design**, not a flaw.  
However, consider: `command=["git", "commit", "-m", "fix build.sh"]`. Token `"build.sh"` matches if autonomous wrote it → **false positive**, because the file isn’t **executed**. But the control’s goal is **not** to detect execution — it’s to flag **any reference** to an unreviewed autonomous-authored file. The doc says: *“references a workspace file an autonomous action authored”*. So even a mention in a commit message **should** flag. This is **intentional precision loss** for simplicity. Not a bug.  
**VERDICT ON FP**: **No actionable false positive** beyond documented porosity.

---

**FINDING 3 / STALE TAINT NOT CLEARED ON HUMAN write_file**  
**SEVERITY**: HIGH  
**LOCATION**: `collaborator/governance.py:2240–2245` (`execute_and_verify` artifact branch)  
**CONCRETE CASE**: An autonomous `write_file` writes `script.sh` → added to `_autonomous_authored`. Later, a **human-approved** `write_file` overwrites `script.sh` with reviewed content. The code clears the taint: `_clear_autonomous_authorship(session, execution.write_set)`. But what if the human-approved write **fails verification** (e.g., disk full, so file not actually written)? Then `cleared = False`, and the clear **still happens**! Because the clear is gated only on `cleared and ... op == "file.write"`, but the clear call is **inside the `if cleared:` block**. Wait — code:  
```python
if cleared and getattr(tool, "op", "") == "file.write":
    if human_gated:
        _clear_autonomous_authorship(session, execution.write_set)
    else:
        _record_autonomous_authorship(session, execution.write_set)
```  
So clear **only happens on a cleared (successful) human write**. Good.  
But consider: human-approved `write_file` **succeeds** (cleared=True), so taint is cleared. Then, **before the human runs it**, an **autonomous** `write_file` **overwrites** `script.sh` again. This adds it back to `_autonomous_authored`. Now when the human runs `sh script.sh`, it **is flagged** — correct.  
Now the real issue: what if the human-approved write **uses a different rel path representation**? E.g., autonomous wrote `./script.sh` → normalized to `script.sh` in manifest. Human approves `write_file` with `path="script.sh"` → normalized to `script.sh` → cleared. Good. What if human uses `path="./sub/../script.sh"`? `norm_rel` converts to `script.sh` → cleared. Good.  
But what if the human-approved write is **not a write_file**, but a **run_command** that overwrites the file? E.g., `sh -c 'echo safe > script.sh'`. This is a **human-approved run_command** (`human_gated=True`). It **writes** `script.sh`, but the system **does not clear** the taint, because clearing **only happens in the write_file artifact branch**. So the taint **remains**, and if the human later runs `sh script.sh`, they get a **stale warning** — even though the bytes were just overwritten by a human-approved action.  
**WHY IT FAILS ITS OWN GOAL**: The doc says: *“a HUMAN-approved (re-)write of a path CLEARS its taint”*. But a human-approved **run_command** that rewrites the file **does not clear** the taint. This violates the stated guarantee and trains humans to ignore warnings.  
**ANOTHER CONTROL CATCHES IT?**: **No.**  
**FIX**: In the `run_command` exit branch, after execution, if `human_gated=True`, compute the **post-execution snapshot**, diff against pre, and **clear** any paths in the diff that are in `_autonomous_authored`. But this is expensive and complex. Simpler: **don’t fix** — document that only **write_file** clears taint, and human maintenance should use `write_file` for vetting. But the current design **allows** human run_command to rewrite files, so this is a gap. Given the advisory nature, maybe acceptable. But it **does violate the stated clear condition**.

---

**FINDING 4 / MODEL CAN POISON MANIFEST VIA TOKEN SPLITTING**  
**SEVERITY**: LOW  
**LOCATION**: `collaborator/provenance.py:45–55` (`_tokenize`)  
**CONCRETE CASE**: Model emits `run_command` with `command="sh build.sh; rm -rf /"`. `_tokenize` splits on whitespace → tokens include `"build.sh;"`. `norm_rel("build.sh;")` → `"build.sh;"` (not empty). If no autonomous file is named `build.sh;`, no match. But if the model first does an autonomous `write_file` with `path="build.sh;"`, then this token **will match**. So the model can **deliberately name files with shell metacharacters** to later trigger matches. But this is **not poisoning** — it’s just using the system as intended. The manifest records actual paths written. The recognizer matches tokens. If the model writes a file named `"; rm -rf /"`, and later references it, it will match. This is **correct behavior**.  
Can the model cause **thousands of junk paths** to be recorded? Only by actually **writing** thousands of files autonomously. Which is already bounded by other controls (emission quota, etc.). So **no amplification**.  
**VERDICT**: **Not a flaw**.

---

**FINDING 5 / TOTALITY BREAK IN govern_action ON NON-STR COMMAND**  
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/governance.py:1960` (`_references_autonomous`)  
**CONCRETE CASE**: `session.workspace` is a `Path` object. In `_rel_candidates`, `workspace` is passed as-is. Inside `_rel_candidates`:  
```python
ws = str(workspace).replace("\\", "/")
```  
This is safe. But what if `command` is a dict? `_tokenize(command)` handles non-list/tuple by converting to string. `str({"command": "sh"})` → `"{'command': 'sh'}"`. Then `shlex.split` fails → falls back to whitespace split → tokens like `"{'command':"`. These won’t match any file path. So **no crash**, and **no false match**. Totality holds.  
What if `session._autonomous_authored` contains a non-string? `note_autonomous_authorship` calls `norm_rel(p)`, which converts to string. So manifest only contains strings.  
**VERDICT**: **Totality is preserved**.

---

### STEELMAN
The design correctly accepts porosity (token-only matching, no symlink resolution) as the price for a lightweight, non-blocking control. The core invariant — *make autonomous authorship visible at approval time* — is upheld for the primary dropper paths (autonomous write_file and contained run_command) under normal conditions. The clear-on-human-write semantic is sound for write_file, and the human maintenance path is deliberately excluded from tracking, preserving usability.

### VERDICT
**SERIOUS_FLAWS** — The snapshot-based recording for autonomous run_command has a critical false-negative hole when pre-snapshot fails, breaking the documented recall guarantee with no fallback. Additionally, human-approved run_command rewrites do not clear taint, leading to stale warnings that erode trust.  
**Highest-value fix**: In `execute_and_verify`, for autonomous contained run_command, **fail the action if pre-snapshot fails**, ensuring that whenever an autonomous run executes under `require_code_protection`, its outputs are always recorded.
