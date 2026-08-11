# PR#39 contained certification panel: qwen/qwen3-max

_finish=stop seconds=22.1 cost=$0.1047 usage={'prompt_tokens': 61320, 'completion_tokens': 1153, 'total_tokens': 62473, 'cost': 0.1046526, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1046526, 'upstream_inference_prompt_cost': 0.0956592, 'upstream_inference_completions_cost': 0.0089934}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

**FINDINGS**

---

**ID**: F1  
**TITLE**: `wrap_contained` can return `(argv, False, True)` when bwrap is absent but `pairs` is non-empty  
**SEVERITY**: HIGH  
**LOCATION**: `collaborator/contained.py:168`  
**CONCRETE INPUT OR BYPASS**: On Linux with no `bwrap` binary (`_BWRAP_BIN` points to a non-existent file), and with at least one valid code root (so `pairs` is non-empty), `wrap_contained` returns `(argv, unshare_net, True)`.  
**WHY IT BREAKS A GUARANTEE**: The function claims `protected=True` even though no containment was applied — the returned `argv` is the original command. This violates the invariant that `protected=True` iff the command will run under verified containment. An autonomous run_command would then be allowed to execute **uncontained**, breaking Harm A.  
**WHETHER ANOTHER CHECK CATCHES IT**: **NO**. The caller in `_exec_command` checks `if not protected: return DENIED`, but here `protected=True`, so it proceeds to run the uncontained command. The `containment_available()` probe correctly returns `False` in this case (since it checks `os.path.isfile(_BWRAP_BIN)`), so govern-time gating would block autonomy. However, a direct call to `_exec_command(..., require_code_protection=True)` (e.g., via a future refactor or test) would bypass govern-time checks and run unprotected.  
**FIX**: In `wrap_contained`, move the `os.path.isfile(_BWRAP_BIN)` check before the `protected = True` assignment. Return `(argv, False, False)` if bwrap is missing, regardless of `pairs`.

---

**ID**: F2  
**TITLE**: `_guarded_script`'s mountinfo parsing is vulnerable to multiple mounts or malformed entries  
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/contained.py:103`  
**CONCRETE INPUT OR BYPASS**: If a protected root path appears as a substring in another mount point (e.g., protecting `/usr` while `/usr/local` is also mounted), the `awk '$5==m {print $6}'` may match the wrong line. Also, if multiple mounts exist for the same path, `tail -n1` picks the last, which may not be the relevant one.  
**WHY IT BREAKS A GUARANTEE**: The guard may incorrectly conclude a root is read-only when it is actually writable (or vice versa), leading to a false `code_protected` claim or an unnecessary downgrade.  
**WHETHER ANOTHER CHECK CATCHES IT**: **PARTIALLY**. The append-write test (`: >> "$f"`) is a stronger check and would catch writability even if the mountinfo check passes spuriously. However, if the mountinfo check fails spuriously (e.g., due to parsing error), it could cause a false negative (downgrade) even when the root is truly ro. This is fail-safe (no execution with false protection), so severity is reduced.  
**FIX**: Use a more robust method to find the exact mount: e.g., `findmnt -n -o OPTIONS --target "$r"` or parse `/proc/self/mountinfo` with proper field splitting and exact path matching.

---

**ID**: F3  
**TITLE**: `_RO_SYSTEM` and `_MASK` computed at import may omit critical dirs on some systems, weakening view  
**SEVERITY**: LOW  
**LOCATION**: `collaborator/contained.py:47-52`  
**CONCRETE INPUT OR BYPASS**: On a system where `/etc` exists but none of `/etc/shadow`, `/etc/gshadow`, etc., exist, `_MASK` is empty, so secrets are not masked. Similarly, if `/lib64` is missing (as noted), it’s omitted from `_RO_SYSTEM`.  
**WHY IT BREAKS A GUARANTEE**: The deny-by-default view is slightly less strict than intended, potentially exposing files that should be masked or omitted. However, since `$HOME` and other sensitive areas are never bound, and capabilities are dropped, this is defense-in-depth only.  
**WHETHER ANOTHER CHECK CATCHES IT**: **YES** — capability dropping and lack of binding for sensitive paths already prevent access; masking is belt-and-suspenders.  
**FIX**: None required (by design: "decide the view ONCE at import from what actually EXISTS"). Acknowledged as safe.

---

**CERTIFICATION LINE**  
**NOT-CERTIFIED** — because `wrap_contained` can falsely report `protected=True` when bwrap is absent, allowing uncontained execution on the autonomy path if called directly.

---

**STEELMAN**  
The PR correctly implements a split-path design that avoids nesting bwrap under netns, uses a per-run guard to verify read-only mounts, and requires both a signed capability and verified containment for autonomy. The human path remains unfenced by design, and the preview honestly reports lack of protection. Most edge cases are handled with fail-safe downgrades.

---

**VERDICT**  
**SERIOUS_FLAWS** — due to the possibility of `protected=True` being returned without actual containment when bwrap is missing. The highest-value fix is to ensure `wrap_contained` returns `protected=False` whenever bwrap is not available, regardless of other conditions.
