# netns code panel: openai/gpt-5.1

_finish=stop seconds=34.7 cost=$0.0608 usage={'prompt_tokens': 16100, 'completion_tokens': 4065, 'total_tokens': 20165, 'cost': 0.060775, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.060775, 'upstream_inference_prompt_cost': 0.020125, 'upstream_inference_completions_cost': 0.04065}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID 1 / NETNS PROBE–RACE MISFLAG / SEVERITY: MEDIUM  
LOCATION: collaborator/netns.py:29–45, 60–76; collaborator/tools.py:143–168

CONCRETE BYPASS  
- Host with `/usr/bin/unshare` present and functional at import time, but where unprivileged user namespaces are later disabled or `unshare` is removed or replaced before a given `run_command` call.  
- Sequence:
  1. Process imports `collaborator.netns`; `_UNSHARE_BIN` is set to `/usr/bin/unshare`, and `netns_available()` runs once, successfully unsharing and caching `_available = True`.
  2. After that first probe (but before some later `run_command`), a privileged actor on the host:
     - removes or chmods `/usr/bin/unshare` so the target user cannot exec it, or
     - changes kernel `userns` constraints (e.g., via sysctl/sysfs or cgroup constraints) so this unprivileged invocation now fails, or
     - replaces `/usr/bin/unshare` with a different binary that exits non‑zero or behaves incorrectly for this user.
  3. A subsequent `run_command` call goes through `_exec_command` → `wrap_no_network`. Because `_available` is cached True, `wrap_no_network` unconditionally composes `[ _UNSHARE_BIN, "--map-root-user", "--net", "--", "sh", "-c", ... ]` and marks `isolated=True`.
  4. `run_supervised` runs `unshare`:
     - If `/usr/bin/unshare` was removed or made non-executable, `execve` fails with `OSError`. In CPython `subprocess.run` at the parent level, that is raised in the parent, not returned as an exit code, so `_exec_command` never runs and this *particular* call does not misflag.
     - More subtle: if `/usr/bin/unshare` now exits quickly with status 0 **but does not actually unshare a netns** (e.g., a replacement binary that just invokes the inner command without namespaces, or one compiled without `--user` support but ignoring flags), `netns_available()` stays True and future `wrap_no_network` calls set `isolated=True` while the child actually runs in the host network namespace with full egress. Because `netns_available()` never re-probes, the flag never corrects.

WHY IT BREAKS ISOLATION OR HONESTY  
- The invariant documented is: “`isolated=True` means this run actually occurred in a fresh netns with no route out.” That’s enforced only by a one-time probe whose truth is presumed global and stable. If an operator or automation changes `/usr/bin/unshare` after import—but before or during later runs—`netns_available()` keeps returning True while the real `unshare` invocation no longer isolates.  
- Under a malicious or broken replacement `/usr/bin/unshare` that honors the same argv and exits 0 but omits `CLONE_NEWNET`, a `run_command` caller can reach the network (e.g., `curl https://example.com`) while the governance summary prints “🔒 net-isolated”. That is a silent lie about network isolation.  
- This is not just an admin‑misconfiguration corner case: the code explicitly trusts that “the answer is a property of the host, not of any single call,” which is not generally true over the lifetime of long-running processes or mutable deployments.

WHETHER ANOTHER CONTROL CATCHES IT  
- No code-level control detects “`unshare` succeeded but didn’t actually create a new netns.” The tests only assert that a single probe and a single later `run_command` behave as expected on CI; they do not re-validate per call.  
- Higher-level governance (capabilities, leashes) do not know about namespaces at all; they trust the `network_isolated` bool coming from `_exec_command → wrap_no_network`. If `unshare` is malicious-but-silent, everything above assumes isolation is real.

FIX  
- Do not treat netns availability as an immutable host property. Options, in increasing robustness:  
  1. **Reprobe per run**: replace the cache with a short-lived check at each `wrap_no_network` call (or at least periodically) and treat any failure of `unshare` as “unavailable → run unisolated, isolated=False”.  
  2. **Command-line self-check**: after `unshare` + `--net`, have the wrapper validate that it is in a new network namespace compared to the parent—for example, check `/proc/self/ns/net` inode vs. a value passed from the parent. If the check fails, abort the job with an error and set `network_isolated=False`.  
  3. **Stronger: structural guard**: instead of caching a boolean, embed the isolation proof directly into the wrapper: the parent can read its own `/proc/self/ns/net` inode and pass it via an envvar; the child, as the first action, asserts that its ns inode differs; if not, it prints a loud failure and exits non-zero. This catches a tampered `unshare` even if it still exits 0.

STEELMAN  
- The current design assumes that `unshare` is a stable, trusted part of the platform and that user-namespace availability cannot change at runtime, which is often true on well-managed hosts and in CI containers. Under those assumptions, caching the probe result is a reasonable optimization and avoids per-command overhead, and if `/usr/bin/unshare` is entirely absent or non-executable, `subprocess.run` will throw and the job fails rather than silently misflagging isolation.  
- In a controlled deployment where `/usr/bin/unshare` is part of an immutable base image and not modifiable by tenants, this race is practically unlikely, and the risk of a root-level operator maliciously swapping `unshare` without altering the rest of the stack may be considered out-of-scope.

---

ID 2 / SUBSTITUTE-UNSHARE BEHAVES INCORRECTLY BUT PASSES PROBE / SEVERITY: HIGH  
LOCATION: collaborator/netns.py:36–45, 60–76; tests/test_netns.py:89–115

CONCRETE BYPASS  
- Scenario: `/usr/bin/unshare` is replaced with a binary that:  
  - accepts the same arguments (`--map-root-user --net --`),  
  - for the probe invocation `[unshare, --map-root-user, --net, --, python, -c, pass]`, still exits with status 0 (for example, by just exec-ing `python -c pass` in-place, ignoring the `--net` and `--map-root-user` flags),  
  - and similarly for the wrapped run, just `exec`s the provided `"sh"` plus arguments **without actually creating a new user or network namespace**.  
- Because the probe uses exactly the same `_UNSHARE` tuple, `netns_available()` caches `_available = True`.  
- Any later `run_command` call composes the wrapper argv, sets `isolated=True`, and the inner process runs in the host netns with full outbound network capabilities. A collaborator can now run `run_command: "python -c 'import socket; socket.create_connection((\"1.1.1.1\",443),5)'"` and the TCP connect will succeed while `Execution.network_isolated` and `Decision.network_isolated` both say `True`.

WHY IT BREAKS ISOLATION OR HONESTY  
- The security proof in comments (“isolation is structural; a fresh netns has no route”) silently assumes that the `unshare` binary is honest and does what the flags say. The code never validates that a new netns actually exists, only that the child command exits with status 0.  
- A compromised/mis-compiled `unshare` that throws away the namespace flags but faithfully proxies argv will trivially pass the probe and every subsequent call, producing `isolated=True` while the child has raw network reach. This collapses the core guarantee of ADR 0003: “egress.py becomes the sole IP-network path” is false in the most dangerous way (network egress exists but is logged as net-isolated).  

WHETHER ANOTHER CONTROL CATCHES IT  
- Nothing above this layer checks kernel namespace state. The tests in `tests/test_netns.py` only verify that on *this* host, connecting to `1.1.1.1:443` fails inside the wrapper. Once `unshare` is replaced, those tests would fail, but that’s a deployment-time issue, not a runtime defense; the code path itself has no guard.  
- Capability gates, leashes, and `egress` tooling do not verify actual kernel-level isolation; they rely on this module’s correctness.

FIX  
- Add a cheap, in-band verification of netns creation that does not trust `unshare`’s semantics:  
  - For example, before invoking `unshare`, parent process reads its `/proc/self/ns/net` inode and passes it as an environment variable (or argument). In `_LO_UP_THEN_EXEC`, insert an early guard script:  

    ```python
    _CHECK_NETNS_SCRIPT = r'''
    if [ -n "$PARENT_NETNS_INO" ]; then
        self_ino=$(stat -Lc '%i' /proc/self/ns/net 2>/dev/null || echo unknown)
        if [ "$self_ino" = "$PARENT_NETNS_INO" ]; then
            echo "netns check failed: still in parent netns" >&2
            exit 127
        fi
    fi
    ip link set lo up 2>/dev/null
    exec "$@"
    '''
    ```

    and set `PARENT_NETNS_INO` in the parent before calling `unshare`.  
  - Alternatively, replace shell wrapper with a tiny, static launcher binary you build/ship yourself that calls `unshare(CLONE_NEWUSER|CLONE_NEWNET|...)` via the kernel API and then performs the `lo` setup—removing trust in the system `unshare` and eliminating this class of substitute.  
- On failure of the netns self-check, treat the environment as “unisolated” and either: (a) refuse to run `run_command` at all (safer), or (b) run it but mark `network_isolated=False` so the governance layer doesn’t make false claims.

STEELMAN  
- The authors explicitly document that they assume an honest, system-provided `unshare` and emphasize that this is an “unprivileged” mechanism, suggesting the threat model is “untrusted model code, trusted base OS.” Under that model, a root-compromised `unshare` is arguably equivalent to a fully compromised host, and most controls would be moot; ensuring netns correctness when core OS utilities are replaced might be out of scope.  
- In controlled CI/container environments (which the comments highlight as the proof location), `unshare` is often part of an immutable base image, and deploying a malicious `unshare` would be caught by higher-level supply-chain checks rather than this code path.

---

ID 3 / MISLEADING FLAG ON NON-LINUX OR KERNEL FAILURE PATHS IS LARGELY WELL-HANDLED / SEVERITY: LOW (NON-FINDING)  
LOCATION: collaborator/netns.py:69–76; collaborator/tools.py:143–168; tests/test_netns.py:119–131

CONCRETE ATTEMPTED BYPASS  
- Try to force `netns_available()` to misrepresent isolation by manipulating inputs under model control:
  - Provide bizarre `command` arguments (including empty argv, very large argv, or env modification) to see if flag toggles even without netns,  
  - Exploit probe timeout or errors to get `_available` stuck in a wrong state, then compare `Execution.network_isolated` against reality.  

ANALYSIS / WHY IT DOES *NOT* BREAK ISOLATION OR HONESTY  
- When `sys.platform != "linux"`, `netns_available()` unconditionally returns False → `wrap_no_network` passes through argv and returns `isolated=False`. The decision summary then prints “⚠ net NOT isolated (raw reach)”. This is honest: no isolation is claimed.  
- If `unshare` or the probe command fails with any `OSError` or `SubprocessError`, `_available` is set to False; there is no path where an exception during probe results in `_available=True`. Timeout behaves similarly by raising `subprocess.TimeoutExpired` (a `SubprocessError`), also mapped to False. As a result, probe *fail-open* to “unisolated with honest flag” is avoided.  
- On each actual command, `_exec_command` uses `wrap_no_network(argv)` directly and assigns `Execution.network_isolated = isolated` from that call; the tests assert that this matches `netns_available()` for a no-op command. There is no evidence of divergence due to argv content.

WHETHER ANOTHER CONTROL CATCHES IT  
- The test `test_exec_command_flag_is_honest` in `tests/test_netns.py` anchors that `Execution.network_isolated` equals the current `netns_available()` for a simple command. Although it doesn’t assert deeper kernel state, it prevents obvious flag‑plumbing mistakes.  
- Governance’s `Decision` simply copies `execution.network_isolated` through; there is no additional transformation that could corrupt the flag.

FIX (MINOR)  
- To make the behavior more testable and robust, consider adding an explicit regression test where `netns_available()` is monkeypatched to flip between True and False over time, asserting that `_exec_command`’s flag always agrees per call. This doesn’t close a security hole, but improves confidence.  
- Optionally, explicitly handle `subprocess.run` errors inside `_exec_command` so that if `run_supervised` ever raises due to `unshare` exec failure, you can surface a more specific “netns setup failed” error instead of a generic tooling failure.

STEELMAN  
- The design’s “fall back to unisolated + honest flag” behavior is intentional and documented; it ensures that on hosts without userns/netns support, the system doesn’t misrepresent network mediation but still provides functionality. The probe implementation is defensive: any error whatsoever marks the host as non-supporting and avoids false positives.  
- Given the threat model and the stated ADR constraint (“do not claim global egress mediation until this holds”), preferring a fail-closed flag on any uncertainty is appropriate and well implemented; this portion of the code is sound.

---

ID 4 / SHELL WRAPPER AND ARGV HANDLING / SEVERITY: LOW (NON-FINDING)  
LOCATION: collaborator/netns.py:51–59, 78–79; collaborator/tools.py:143–167; tests/test_netns.py:53–69

CONCRETE ATTEMPTED BYPASS  
- Try to inject shell metacharacters or environment tricks so that the wrapper’s `sh -c 'ip link set lo up ...; exec "$@"' sh *argv` would:
  - break out of `exec "$@"` and run attacker-supplied text through an extra shell parse,  
  - alter what binary is executed as `argv[0]` in a way that bypasses netns,  
  - or smuggle in `;` / `$(...)` / `|` etc. in the original `command` argument and have them get re‑parsed by a shell.  

WHY IT DOES *NOT* BREAK ISOLATION OR HONESTY  
- For string commands: `_exec_command` uses `shlex.split(cmd)` to build `argv`. That split is applied once in the parent; the wrapper never uses shell=True in `subprocess.run`.  
- The wrapper `argv` is:

  ```python
  [ _UNSHARE_BIN, "--map-root-user", "--net", "--",
    "sh", "-c", 'ip link set lo up 2>/dev/null; exec "$@"', "sh", *argv ]
  ```

  Inside that shell, `$@` is the list `["sh"] + argv`, and `exec "$@"` replaces the shell with a new process whose `argv[0]` is the first element of that list. Since every `argv` element came from `shlex.split` or explicit stringification (for list input), there is no place where shell metacharacters are re-split; they are passed as opaque argv elements.  
- The tests explicitly pin that “the original tokens survive intact as trailing argv — exec "$@" does not re-split them, so a command with spaces stays one argv element (no shell-injection via the wrapper).” This aligns with standard POSIX shell semantics; adversarial strings like `"echo hi; curl http://..."` become a single argv element to `exec`, not shell syntax.

WHETHER ANOTHER CONTROL CATCHES IT  
- Additional controls (no `shell=True`, absolute `_UNSHARE_BIN`, shlex splitting) already harden the execution path. There is no separate “catcher” for shell injection because the wrapper doesn’t introduce an injection surface in the first place.  

FIX (MINOR)  
- If you want to further constrain behavior, you could replace the inner `sh -c` entirely with a small helper binary that brings `lo` up and then `execve`s the target argv; that would remove shell dependence altogether. This is more a hardening/simplification than a security bug fix.  
- Alternatively, assert in tests that commands containing characters like `;`, `|`, `$(` still execute as expected (no splitting) to guard against future refactors that might reintroduce `shell=True`.

STEELMAN  
- The authors are explicit about the `exec "$@"` semantics and have a unit test verifying no re-splitting; they’re relying on well-understood POSIX behavior and not on fragile string concatenation. This is a reasonable, straightforward approach for a thin wrapper whose only task is to bring `lo` up and then delegate.  
- Given that both the outer `subprocess.run` and the inner `exec` avoid implicit shell parsing of user-supplied text, and that argv is fully under the tool’s control, the shell integration is acceptable as implemented.

---

OVERALL VERDICT: SERIOUS_FLAWS — The netns isolation relies on a cached, opaque `unshare` probe and never verifies that a new network namespace is actually in effect; a malicious or misconfigured `unshare` can give full network egress while the system claims `🔒 net-isolated`.  
SINGLE HIGHEST-VALUE FIX: Introduce an explicit runtime verification that each wrapped command truly runs in a different network namespace from the parent—either by checking `/proc/self/ns/net` in the child against a parent-provided value or by replacing reliance on the system `unshare` binary with a minimal, audited launcher that calls the kernel namespace APIs directly and fails closed (no run, `network_isolated=False`) if isolation cannot be guaranteed.
