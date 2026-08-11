# PR#39 contained certification panel: google/gemini-2.5-pro

_finish=stop seconds=156.2 cost=$0.1699 usage={'prompt_tokens': 69352, 'completion_tokens': 8324, 'total_tokens': 77676, 'cost': 0.16993, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.16993, 'upstream_inference_prompt_cost': 0.08669, 'upstream_inference_completions_cost': 0.08324}, 'completion_tokens_details': {'reasoning_tokens': 5864, 'image_tokens': 0, 'audio_tokens': 0}}_

This is a comprehensive adversarial review and certification of the provided change.

### Security Findings

---

#### **ID: BUG-01**
*   **TITLE:** Shell Injection in Guard Script via Newline in Protected Path
*   **SEVERITY:** **HIGH**
*   **LOCATION:** `collaborator/contained.py:108`
*   **CONCRETE INPUT OR BYPASS:**
    An attacker engineers a situation where a protected code root path contains a newline character. For example, if `codefence.protected_roots_with_witness()` returns a root path like `"/opt/salience/a\nb/collaborator"`. The `_guarded_script` function will construct the following shell code:
    ```sh
    for spec in "/opt/salience/a
    b/collaborator|/opt/salience/a
    b/collaborator/x.py"; do ...
    ```
    The shell will interpret this as `for spec in "/opt/salience/a`, followed by a new command `b/collaborator|...`. This breaks the `for` loop syntax, causing the guard's verification checks to be skipped for some or all protected roots. The `sh` process will likely exit with a non-zero status due to the syntax error, but it will not be exit code 45 and the stderr will not contain the `bwrap:` prefix.
*   **WHY IT BREAKS A GUARANTEE:**
    The downgrade logic in `tools.py:409-412` checks for `protection_unverified` (rc=45), `isolation_unverified` (rc=44), or `setup_failed` (rc!=0 + `bwrap:` prefix). A shell syntax error produces a different exit code and no `bwrap:` prefix, so none of these conditions trigger. The `protected` flag is never downgraded to `False`. This breaks the core guarantee of "verified-not-trusted containment," as the system will report `code_protected=True` for a run where the read-only guarantee was never actually verified due to the script bypass.
*   **WHETHER ANOTHER CHECK CATCHES IT:** No. This is a failure within the verifier itself. The supervisor will report a non-zero exit code, but the logic in `_exec_command` will misinterpret it as a payload failure rather than a guard failure, and will fail to downgrade the `protected` flag.
*   **FIX:**
    Avoid constructing shell scripts via raw string formatting of potentially untrusted inputs. The most robust fix is to pass the root/witness pairs through a file or environment variables that the script then parses. A minimal fix is to use `shlex.quote()` on each `spec` before joining them, which would correctly handle newlines and other special characters, although this still carries the risks of complex shell scripting.

---

#### **ID: BUG-02**
*   **TITLE:** Symlinked `__file__` Causes Protection of Incorrect Directory
*   **SEVERITY:** **CRITICAL**
*   **LOCATION:** `collaborator/codefence.py:128`
*   **CONCRETE INPUT OR BYPASS:**
    An attacker with filesystem access (even pre-run) replaces a module file with a symlink to a file outside the code root. For example:
    ```sh
    # In the collaborator code root
    mv collaborator/codefence.py collaborator/codefence.py.real
    ln -s /tmp/fake_codefence.py collaborator/codefence.py
    ```
    When `protected_roots_with_witness()` runs, `Path(mod_file).resolve()` will resolve `collaborator/codefence.py` to `/tmp/fake_codefence.py`. The logic then takes `witness.parent`, which is `/tmp`, as the directory to protect. The `bwrap` command will then execute with `--ro-bind /tmp /tmp`, leaving the actual code root `.../collaborator/` completely unprotected and writable, while still claiming `protected=True`.
*   **WHY IT BREAKS A GUARANTEE:**
    This is a classic TOCTOU attack on the file path. The system identifies what to protect based on a resolved path that an attacker controls. It breaks the headline guarantee completely: an autonomous shell runs with the belief that its code is protected, but its actual code root is fully writable, allowing it to neuter all security controls. This is a full bypass of the F-6 Harm A mitigation.
*   **WHETHER ANOTHER CHECK CATCHES IT:** No. The in-child guard script will receive the *wrong* root (`/tmp`) and will correctly verify that `/tmp` is read-only (if the bind succeeded), giving a false sense of security. The system has no other mechanism to realize it is protecting the wrong directory.
*   **FIX:**
    The logic in `protected_roots_with_witness` is flawed. It must not derive the root-to-protect from the resolved witness. It should iterate through the already-known `PROTECTED_ROOTS` and find the corresponding witness file for each, ensuring the witness resolves to a path *inside* that root.
    ```python
    # In codefence.py
    def protected_roots_with_witness() -> "tuple[tuple[Path, Path], ...]":
        # ... (docstring) ...
        out: list[tuple[Path, Path]] = []
        # _RESOLVED is already the single source of truth for (name, resolved_root_path)
        slots_by_root = {p: n for n, p in _RESOLVED}
        files_by_slot = {n: f for n, f in _code_slots()}
    
        for root in PROTECTED_ROOTS:
            slot_name = slots_by_root.get(root)
            mod_file = files_by_slot.get(slot_name)
            if not mod_file:
                continue
            try:
                witness = Path(mod_file).resolve()
                # CRITICAL: Verify witness is INSIDE the root it claims to witness.
                if root in witness.parents and witness.is_file():
                    out.append((root, witness))
            except (OSError, ValueError, RuntimeError):
                continue
        return tuple(out)
    ```

---

#### **ID: NIT-01**
*   **TITLE:** Payload Can Forge "Unverified" Sentinel to Force False Negative Report
*   **SEVERITY:** **LOW**
*   **LOCATION:** `collaborator/contained.py:190`, `collaborator/tools.py:409`
*   **CONCRETE INPUT OR BYPASS:**
    A contained command (the payload) can intentionally mimic the guard's failure signal to trick the caller.
    **Command:** `echo "SALIENT_CODEFENCE_UNVERIFIED" >&2; exit 45`
    This command runs successfully inside a correctly configured and verified sandbox. However, the `_exec_command` wrapper sees `rc=45` and the sentinel in stderr. The `protection_unverified()` check returns `True`, causing the `protected` flag to be downgraded to `False`.
*   **WHY IT BREAKS A GUARANTEE:**
    It doesn't break a safety guarantee but rather an *honesty* guarantee. The system falsely reports `code_protected=False` for a run that was, in fact, successfully protected. This is a fail-safe condition (a false negative is better than a false positive), but it constitutes a denial-of-service on the system's ability to accurately report its security posture. This could mislead learning mechanisms or operators.
*   **WHETHER ANOTHER CHECK CATCHES IT:** No, this is an inherent ambiguity in using stderr for control signaling.
*   **FIX:**
    No fix is strictly necessary given the low severity and fail-safe nature. A more robust (and complex) solution would involve the guard script communicating its status over a dedicated file descriptor that the payload does not inherit, but this is likely over-engineering for the risk. Acknowledging this as a known limitation is sufficient.

---

### Certification

*   **CERTIFICATION LINE:** **NOT-CERTIFIED.** The headline claims are not met due to two distinct high-severity bugs (BUG-01, a shell injection, and BUG-02, a symlink confusion) that can cause the system to falsely claim `code_protected=True` on an autonomous run that was not verifiably contained, defeating the core "protection earns autonomy" guarantee.
*   **Blocked Attacks / Non-Findings (Certified as Secure):**
    *   The `AND` gate for autonomy (`verified containment` + `signed cap`) is robustly enforced at both govern-time and execute-time; it cannot be failed-open by model-controlled inputs. **CERTIFIED.**
    *   The `code_protected` flag is correctly bound to the executor's real, post-run, downgraded result, preventing a belief-vs-behaviour split where the caller trusts a stale pre-run belief. **CERTIFIED.**
    *   The split between the `bwrap` (autonomous) and `netns` (human) paths is clean; `bwrap` is never nested under `netns`'s `--map-root-user`, correctly avoiding the `CAP_SYS_ADMIN` inheritance vulnerability. **CERTIFIED.**
    *   The human-path `wrap_no_network` wrapper is byte-identical and has not regressed. **CERTIFIED.**
    *   The MINOR-B `approved==executed` seal for HELD actions is orthogonal to the autonomous path and has not regressed. **CERTIFIED.**
    *   Off-Linux, the system correctly reports containment as unavailable and refuses to auto-run a shell, failing safe as designed. **CERTIFIED.**

---

### Steelman & Verdict

*   **STEELMAN (BEST CASE FOR THE AUTHOR):**
    The design correctly identifies the critical threat model: an autonomous shell rewriting its own rules. It rightly concludes that trust is insufficient and implements a "verify-then-exec" guard that runs inside the sandbox, which is the correct architectural pattern. The design also correctly identifies and splits paths to avoid the known `bwrap`+`userns` uid 0 vulnerability, and laudably binds the final `code_protected` report to the executor's real, verified result, not a govern-time belief.

*   **VERDICT:** **SERIOUS_FLAWS.** While the high-level architecture is sound and several critical pitfalls are correctly avoided, the implementation contains two guarantee-breaking bugs in the verification logic itself. The symlink confusion bug (BUG-02) in particular represents a fundamental flaw in identifying *what* to protect, allowing an attacker to completely misdirect the containment, rendering it useless while the system still believes it is secure. The system cannot be certified until these flaws are remediated.

*   **HIGHEST-VALUE FIX:**
    Fixing **BUG-02 (Symlink Confusion)** is the single most important action. The logic for identifying the protected roots and their witnesses must be made robust against path manipulation, ensuring the system always protects the intended code directories and not an attacker-controlled location.
