# deputy-seal code panel: x-ai/grok-4.5

_finish=stop seconds=260.6 cost=$0.1269 usage={'prompt_tokens': 26308, 'completion_tokens': 12420, 'total_tokens': 38728, 'cost': 0.1269184, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1269184, 'upstream_inference_prompt_cost': 0.0523984, 'upstream_inference_completions_cost': 0.07452}, 'completion_tokens_details': {'reasoning_tokens': 10090, 'image_tokens': 0, 'audio_tokens': 0}}_

# Adversarial Code Review: Maintenance Sandbox / Deputy Seal

## Findings

### F1 / `/proc/<pid>/root` cross-mount-ns unmask / CRITICAL
**Location:** `collaborator/maintain.py` `_bwrap_argv` ~lines assembling bwrap flags (no `--unshare-pid`; has `--proc /proc` + `--bind / /`)

**Concrete bypass** (uid 1000, CapEff=CapPrm=0, umount blocked — live preconditions held):

```bash
# Payload inside the maintenance sandbox. Shared PID ns + fresh procfs
# exposes every same-uid host process's mount namespace through /proc/PID/root.
# Those mnt ns views do NOT have the /dev/null ro-bind masks.
for p in /proc/[0-9]*; do
  s="$p/root/run/docker.sock"
  [ -S "$s" ] || s="$p/root/var/run/docker.sock"
  if [ -S "$s" ]; then
    curl -sS --unix-socket "$s" http://localhost/_ping && echo BYPASS:$s && break
  fi
done
# Often simply:
curl -sS --unix-socket /proc/$PPID/root/var/run/docker.sock http://localhost/version
# or the Collaborator Python itself (same real uid, dumpable, host mnt ns).
```

**Why it breaks the seal / flag honesty:**  
The mask is a path-based bind in *this* mount ns only. Without `--unshare-pid`, `--proc /proc` shows the host PID namespace. For any dumpable same-uid process (Collaborator Python, bwrap parent, siblings), `ptrace_may_access(..., PTRACE_MODE_READ_FSCREDS)` allows traversing `/proc/<pid>/root/...`, which is that process’s root in the **host** mount ns — where `docker.sock` is a live socket. No `CAP_SYS_ADMIN`, no umount, no userns, no setuid required. Guard still sees `[ -c /run/docker.sock ]`, CapEff/CapPrm=0, fresh netns → emits `SALIENT_MAINT_VERIFIED` → `network_isolated=True` while dockerd egresses on the payload’s behalf. This is defeat of a **listed** deputy, not the documented blocklist residual.

**Other control catch?** No. Yama `ptrace_scope` does **not** block `PTRACE_MODE_READ` same-uid `/proc/pid/root` walks (it mainly restricts `ATTACH`). Netns does not affect pathname AF_UNIX. Cap-drop is irrelevant to path lookup. `contained.py` correctly uses `--unshare-pid`; maintain forgot it.

**Fix:** Mirror contained’s namespace set at least for PID (and preferably IPC):

```python
"--unshare-user", "--unshare-pid", "--unshare-ipc",
# keep --unshare-net as today
```

With PID unshare, procfs only shows sandbox tasks; every `/proc/*/root` is the *masked* mnt ns. Add a live test: `curl --unix-socket /proc/$PPID/root/run/docker.sock` must fail; direct masked path stays a chardev.

---

### F2 / Guard `stat`/`awk` not absolute + no `--clearenv` / MEDIUM
**Location:** `maintain.py` `_guarded_script` (uses bare `stat`, `awk`); `_bwrap_argv` (no `--clearenv`, unlike `contained.py`)

**Concrete bypass (real-uid-0 collaborator path; also DiD for uid 1000):**

1. Real uid 0: bwrap `#287` leaves caps present.  
2. Parent env `PATH=/evil:$PATH` or `LD_PRELOAD=/evil/lie.so` (service file, wrapper, prior host compromise).  
3. `/evil/awk` always prints `0000000000000000` for CapEff/CapPrm; or LD_PRELOAD hooks `open`/`read` on `/proc/self/status`.  
4. `maintenance_available()` probe **passes** → sandbox reported AVAILABLE (opposite of design intent).  
5. Guard emits `SALIENT_MAINT_VERIFIED`; payload runs **with CapEff≠0** and `umount`s the mask.

On uid 1000 with true cap-drop this does not restore `CAP_SYS_ADMIN`, but it still means the “verify the precondition, don’t trust the flag” guard is not closed under env influence. `--chdir <workspace>` makes a hostile workspace `./awk` live if PATH has `.` or workspace.

**Other control catch?** Real-uid-0 was supposed to fail closed via CapEff check then fall back to netns. Env-subverted check defeats that tripwire. No second control.

**Fix:**  
- `--clearenv` + minimal `--setenv PATH /usr/bin:/bin` (as `contained.py`).  
- Absolute paths in the guard: `/usr/bin/stat`, `/usr/bin/awk` (or busybox-known paths resolved at import like `_SH_BIN`).  
- Optionally assert `NoNewPrivs: 1` and `CapBnd: 0` in the same caps loop.

---

### F3 / Probe ≠ run deputy set is OK; probe cache hides later bwrap breakage / LOW
**Location:** `maintain.py` `maintenance_available` (`_available` cache); `tools.py` `_exec_command` human branch

**Concrete scenario:** Probe succeeds once at process start. Later `/usr/bin/bwrap` is replaced or userns policy flipped (admin/update). Cached `True` still selects `wrap_maintenance`; per-run guard/token usually fail-closes (`verified_ok` → `network_isolated=False`, command not exec’d on guard trip). So this is **not** a silent “ran outside sandbox with True flag” except if a malicious bwrap emits the sentinel and runs the payload unisolated — requires write to the absolute bwrap path (outside unprivileged model).

**Other control catch?** Per-run positive token mostly catches it.  

**Fix (hardening):** Treat cache as TTL/host-property only; document that absolute `_BWRAP_BIN` is TCB. Not load-bearing.

---

### F4 / Fallback is honestly weaker (not fall-open fake seal) / NON-FINDING (validated)
**Location:** `tools.py` `_exec_command` `else` branch; `maintain.maintenance_available`

Forcing `maintenance_available()=False` yields `wrap_no_network` — today’s mapped-root netns + documented docker.sock residual, `network_isolated` only if netns guard passes. No claim of deputy seal. Real-uid-0 without F2: CapEff trip → unavailable → same fallback; guard exits 47 before exec if sandbox ever entered. **No-worse-than-today holds** when F2 is fixed.

---

### F5 / setuid / fcap regain via full rw `/` without nosuid / NON-FINDING (against live + kernel semantics)
**Location:** `_bwrap_argv` `--bind / /` (no `nosuid`); payload could exec `mount`, `fusermount`, `su`, `newuidmap`

bwrap 0.9 sets `PR_SET_NO_NEW_PRIVS` before exec and `--cap-drop ALL` clears the bounding set. With CapPrm=CapEff=0 (guard-verified) and empty CapBnd, setuid-root and file capabilities cannot raise `CAP_SYS_ADMIN`. `--disable-userns` blocks nested userns regain. Live proof already shows umount blocked. **Not a break** on the stated bwrap 0.9 + uid 1000 baseline. Residual: guard does not *assert* NoNewPrivs/CapBnd (DiD only — see F2 fix).

---

### F6 / Abstract sockets / TCP loopback deputies / NON-FINDING
`--unshare-net` puts abstract AF_UNIX and TCP (incl. `127.0.0.1:2375`, `127.0.0.53`) in a fresh routeless netns (lo not even up in maintain guard). Pathname sockets remain the residual class — by design. Unlisted pathname deputies (`$SSH_AUTH_SOCK`, rootless docker under `/run/user/<uid>/...`) are **documented blocklist**, not new holes.

---

### F7 / Argv / `exec "$@"` / bind order / metachar belt / NON-FINDING
- Masks appended **after** `--bind / /` (`_bwrap_argv` loop) — order correct; tests pin it.  
- `run_argv += argv` after `sh -c guard sh` → `exec "$@"` does not resplit.  
- `_present_deputy_sockets` realpath + metachar drop prevents guard word-list corruption.  
- Token emitted once pre-exec; payload forging presence is moot; absence cannot be forged after emit. Whitelist in `tools.py` (`isolated = isolated and maintain.verified_ok(...)`) fails closed on guard trip.

---

### F8 / Async laundering / deceived-human / NON-FINDING (documented)
Full rw `/` → cron/systemd/`authorized_keys` still in scope as accepted residual. Out of mechanism attack surface per brief.

---

## STEELMAN

The load-bearing idea is right: path masks are worthless under mapped-root `CAP_SYS_ADMIN`, so the seal must make umount impossible and **prove** CapEff∧CapPrm=0 plus chardev masks before a positive whitelist token; that plus absolute bwrap, fail-closed fallback to certified netns, and non-nesting under `unshare --map-root-user` is real engineering, not theater. The in-child guard and `verified_ok` whitelist correctly avoid blacklist fragility on setup errors. The miss is incomplete namespace isolation relative to `contained.py`: full `/` + shared PID + procfs re-opens the host mnt ns by reference, which is a standard footgun the cap checks cannot see.

## VERDICT

**SERIOUS_FLAWS** — F1 fully defeats the deputy seal for enumerated sockets while `network_isolated=True`.

**Single highest-value fix:** add `--unshare-pid` (and `--unshare-ipc`) to `maintain._bwrap_argv`, and pin a live test that `/proc/$PPID/root/.../docker.sock` is unreachable while the in-ns masked path remains a chardev and CapEff/CapPrm stay zero.
