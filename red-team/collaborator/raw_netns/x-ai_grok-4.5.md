# netns code panel: x-ai/grok-4.5

_finish=stop seconds=309.5 cost=$0.1245 usage={'prompt_tokens': 17378, 'completion_tokens': 15000, 'total_tokens': 32378, 'cost': 0.1245384, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1245384, 'upstream_inference_prompt_cost': 0.0345384, 'upstream_inference_completions_cost': 0.09}, 'completion_tokens_details': {'reasoning_tokens': 13118, 'image_tokens': 0, 'audio_tokens': 0}}_

# Adversarial code review: `run_command` network isolation

## Findings

### F1 / Bare `sh` after absolute-`unshare` hardening / LOW  
**Location:** `collaborator/netns.py` — `wrap_no_network` (`return [*_UNSHARE, "sh", "-c", _LO_UP_THEN_EXEC, "sh", *argv], True`)

**Concrete bypass:** None for IP egress. `unshare` enters `USER+NET` **before** `execvp("sh", …)`. A PATH-planted `sh` (workspace/`./sh` if `PATH` has `.` or a user-writable prefix) still runs **inside** the empty netns.

**Why it matters:** Residual of the fixed PATH-shadow on `unshare`. Probe path is `[*_UNSHARE, sys.executable, "-c", "pass"]` (no `sh`); wrap path depends on bare `sh`. Asymmetry is a footgun, not an egress break. Same-uid plant can swap the shell for UX/confusion, not host IP.

**Other control:** Netns already applied before `sh`; UNIX-socket residual unchanged.

**Fix:** Use the same import-time absolute resolve pattern as `_UNSHARE_BIN` (e.g. `/bin/sh` → `/usr/bin/sh`), and optionally probe `[*_UNSHARE, abs_sh, "-c", ":" ]`.

---

### F2 / `network_isolated=True` means “wrap applied”, not “child netns proven” / LOW  
**Location:** `collaborator/netns.py` `wrap_no_network` + `collaborator/tools.py` `_exec_command` (`run_argv, isolated = wrap_no_network(argv)` → `network_isolated=isolated`)

**Concrete bypass:** None. If `unshare` fails at run (userns exhaustion, transient EPERM) after a successful cached probe, user argv never runs → no egress, but flag stays `True`.

**Why flag honesty is slightly soft:** Flag is “we composed the isolated argv”, not “supervisor observed a netns join”. Failures fail closed on connectivity; the green 🔒 can still appear on a no-op failure.

**Other control:** Nonzero exit; no IP path opens.

**Fix:** Treat run as isolated only if you care about strict honesty—e.g. compare `/proc/self/ns/net` in a tiny pre-exec helper—or document flag as “isolation attempted/structurally applied” (already close to current docs).

---

### F3 / Env not scrubbed for the wrap (`BASH_ENV` / `ENV` / `LD_*`) / LOW  
**Location:** `salienceos/verifier/observers.py` `run_supervised` (inherits full parent env); wrap uses `sh -c`

**Concrete bypass:** Model **cannot** set parent env via `run_command` args (list/`exec "$@"`; no `env=` API). Requires pre-set `BASH_ENV`/`LD_PRELOAD` in the Collaborator process pointing at a writable path, then overwriting that file. Niche host misconfig, not a default model path.

**Other control:** Default Collaborator env is clean; `close_fds=True` (Python 3 default) drops inherited net sockets.

**Fix:** `env=sanitized` in `run_supervised` for isolated runs (minimal `PATH`, `HOME`, `LANG`; drop `BASH_ENV`/`ENV`/`LD_*`/`PYTHON*`).

---

### F4 / DGX/HPC residual: RDMA / vsock device paths / LOW (env-specific)  
**Location:** Mechanism scope (`netns` only); host device nodes

**Concrete bypass:** On some VM/HDFS/DGX setups, `AF_VSOCK` or RDMA verbs can leave the host without using the netns IP stack, **if** device nodes are usable to the same uid. Not generic Linux desktop/CI.

**Other control:** Device permissions / not exposing `/dev/infiniband/*` / container device policy. Observers comment cites DGX Spark—worth an explicit scope line next to UNIX-socket residual.

**Fix:** Document beside pathname-UNIX residual; tighten device cgroup if DGX is in threat model.

---

## Valuable NON-findings (attacks blocked or out of documented scope)

| Attack | Result |
|--------|--------|
| **setns/nsenter → host netns** via `/proc/<pid>/ns/net` | Blocked. Shared PID ns ⇒ fds visible; `setns(CLONE_NEWNET)` needs `CAP_SYS_ADMIN` in the **target** netns’s owning userns (`init_user_ns`). Child only has caps in its **own** userns → `EPERM`. |
| **`ip netns exec` / `/var/run/netns/*`** | Same setns cap model; mounts/named ns not usable as host-IP escape. |
| **Second `unshare --net`** | New empty netns; no host route. |
| **ptrace/CRIU/process_vm_*** into host-netns same-uid procs | Blocked across userns (`CAP_SYS_PTRACE` in target userns). Yama further restricts. |
| **PATH-shadow `unshare`** | Fixed: `_UNSHARE_BIN` absolute, import-time; `argv[0]` has `/`. Child cannot replace `/usr/bin/unshare` (DAC, userns root ≠ host root). |
| **PATH-shadow `sh` → host IP** | **Non-finding for isolation:** netns already active pre-exec (see F1). |
| **argv/shell injection into `sh -c`** | Script is a constant; user tokens are separate argv after `$0` sentinel; `exec "$@"` does not resplit. `shlex.split` only for string form; list path is deliberate. `--` ends unshare options. |
| **Metachar / `LD_PRELOAD=… curl` as argv** | No shell; `LD_PRELOAD=…` would be argv[0] path, not assignment. |
| **Force `netns_available()=False` via model input** | Probe takes no command input; cache is host property. Fail-open is **honest** (`isolated=False`, summary `⚠ net NOT isolated`). |
| **Probe vs wrap disagreement → silent non-isolation** | Same `_UNSHARE` tuple; isolation is structural `--net`. Probe success + wrap = real empty netns. |
| **FD inheritance of connected TCP sockets** | `subprocess.run` defaults `close_fds=True` on POSIX. |
| **Daemonize past supervisor** | Double-fork child keeps the **empty** netns. |
| **Pathname UNIX (docker.sock, resolved, D-Bus, local proxy)** | Reachable (shared mount ns). **Documented residual**, not a code bug. Abstract UNIX **are** netns-isolated. |
| **Flag drop on `verify_mode=="exit"`** | `Execution.network_isolated` → `Decision.network_isolated` in `execute_and_verify` exit branch; `summary()` prints 🔒/⚠. `run_command` is `verify_mode="exit"` only. HELD pre-run has `None` (not a lie). Re-gate/`approve` path not fully shown; production path is `execute_and_verify` (flag set at real run). |

---

## STEELMAN

The design is the right shape for an unprivileged Collaborator: `unshare --map-root-user --net`, absolute `unshare`, no `shell=True`, `exec "$@"` so argv is not re-interpreted, and fail-open only with `network_isolated=False`. Capability/userns semantics actually do block the usual “open host `/proc/…/ns/net` and setns” escape stories; the internal PATH-shadow fix on `unshare` was the real flag-lying bug, and it is closed. Remaining gaps are hardening consistency (bare `sh`), documented non-IP residuals (pathname sockets / exotic devices), and flag meaning = wrap composition rather than post-hoc ns proof.

---

## VERDICT: **SOUND**

No concrete IP-egress break and no path where the flag claims isolation while the user command runs in the host netns under the intended Linux/userns model; highest-value fix is absolute-path `sh` (and env scrub) for defense-in-depth parity with the `unshare` fix—not because the current bare `sh` restores host IP.
