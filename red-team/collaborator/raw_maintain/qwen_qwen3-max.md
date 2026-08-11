# deputy-seal code panel: qwen/qwen3-max

_finish=stop seconds=38.6 cost=$0.0287 usage={'prompt_tokens': 24468, 'completion_tokens': 2472, 'total_tokens': 26940, 'cost': 0.02872584, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.02872584, 'upstream_inference_prompt_cost': 0.01908504, 'upstream_inference_completions_cost': 0.0096408}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

**Finding 1: Setuid Binary Execution Bypasses Cap-Drop Defense**  
- **ID**: MAINT-SETUID-BYPASS  
- **Title**: Full `/` bind allows execution of host setuid binaries, regaining privileges to umount masks  
- **Severity**: CRITICAL  
- **Location**: `collaborator/maintain.py:wrap_maintenance` (lines assembling `--bind / /`) and `_guarded_script` cap check logic  
- **Concrete Bypass**:  
  The sandbox uses `--bind / /`, giving the payload full read-write access to the host filesystem **without `nosuid`**. Although capabilities are dropped (`--cap-drop ALL`), the payload runs as UID 0 inside a user namespace that maps back to the unprivileged real UID. However, **Linux allows execution of setuid-root binaries from within a user namespace if the binary’s owner matches the global UID mapped to the userns root** — which it does here (`--uid 0` maps real UID → 0 in userns). Thus, executing a host binary like `/usr/bin/su`, `/bin/mount`, or `/usr/bin/fusermount` (if setuid) will **regain full root privileges in the userns**, including `CAP_SYS_ADMIN`, enabling `umount` of the deputy socket masks.  

  Example payload:
  ```sh
  /usr/bin/su -c 'umount /var/run/docker.sock'
  ```
  Even if `su` fails due to lack of password, many distributions ship `fusermount` (for FUSE) as setuid-root. If present, `fusermount -u /var/run/docker.sock` would succeed because the process has effective UID 0 in the userns and the mount is visible.

  The guard’s cap check (`CapEff`/`CapPrm == 0`) runs **before** the payload, so it cannot detect privilege escalation via setuid exec. Once the payload execs a setuid binary, it gains caps and can umount.

- **Why It Breaks the Seal**: The core assumption — that cap-drop makes masks unremovable — fails because **file capabilities and setuid binaries are not neutralized by `--cap-drop ALL` when the filesystem is fully exposed without `nosuid`**. This directly defeats the "un-removable mask" guarantee.

- **Does Another Control Catch It?** **NO.** The guard runs before payload execution and only checks initial caps. The token is emitted pre-exec, so even if the payload later regains caps and umounts, the run is still marked `network_isolated=True`.

- **Fix**: Add `--dev-bind / /` **is not sufficient**. Instead, **avoid binding the entire host root**. But maintenance requires full FS access. Therefore, the only safe fix is to **mount `/` with `nosuid`** — but bubblewrap doesn’t support mount options on `--bind`. Alternative: **do not use `--uid 0`**. Run as the real UID (non-zero in userns), so setuid binaries won’t activate (since the effective UID ≠ file owner). However, this breaks "appears as root" parity. Better: **use `--bind` but remount `/` as `nosuid` inside the sandbox** — but that requires `CAP_SYS_ADMIN`, which is dropped. **Real fix**: Do not bind `/` directly. Instead, use a **recursive bind mount of `/` into a tmpfs overlay with `nosuid`**, but bwrap doesn’t support overlays. **Practical fix**: **Remove setuid/setcap binaries from view** via additional `--ro-bind /dev/null` over known setuid paths (`/usr/bin/su`, `/bin/mount`, `/bin/umount`, `/usr/bin/fusermount`, etc.), similar to deputy masking. This is a blocklist, but narrow and critical.

---

**Finding 2: Real-UID-0 Collaborator Early-Return Not Fully Handled**  
- **ID**: MAINT-REAL-ROOT-EARLY-RETURN  
- **Title**: On real UID 0, bwrap skips cap-drop, but fallback may not trigger if `maintenance_available()` probe passes spuriously  
- **Severity**: HIGH  
- **Location**: `collaborator/maintain.py:maintenance_available` and `wrap_maintenance`  
- **Concrete Bypass**:  
  When the collaborator runs as **real UID 0**, bubblewrap (per issue #287) **skips dropping capabilities** even with `--cap-drop ALL`. The guard should catch this via the `CapEff` check and fail closed. However, the `maintenance_available()` probe runs `exit 0` in the sandbox. On a real-UID-0 host, **bwrap may still succeed in creating the sandbox and running the command**, but with full caps. The guard’s cap check **should fail**, causing `verified_ok()` to return false, so `maintenance_available()` returns false, triggering fallback.  

  **But**: what if the host has **no deputy sockets**? Then `_present_deputy_sockets()` returns empty. The guard script still checks caps. So it should still fail. **However**, if the **probe itself is run as real UID 0**, and bwrap **does not drop caps**, then `/proc/self/status` will show non-zero `CapEff`, so the guard exits 47, `verified_ok` returns false, and `maintenance_available()` correctly returns false. So this seems handled.

  **Subtler issue**: What if the system has **ambient capabilities** or **file capabilities on the shell** (`/bin/sh`)? The guard checks `CapEff` and `CapPrm`, but **not ambient or inheritable**. However, with `--cap-drop ALL`, ambient should be cleared. But on real UID 0, cap-drop is skipped, so ambient caps may persist. Still, the `CapEff` check should catch any effective caps.

  **Conclusion**: This path appears **correctly handled** — the cap check in the guard ensures real-UID-0 causes failure, leading to fallback. **Not a bypass**.

- **Verdict**: **NOT A FINDING** — the design accounts for this via the in-child cap verification.

---

**Finding 3: Abstract UNIX Sockets and Loopback TCP Not Masked**  
- **ID**: MAINT-NET-RESIDUALS  
- **Title**: Deputy daemons reachable via abstract sockets or loopback TCP evade pathname masking  
- **Severity**: MEDIUM  
- **Location**: `_DEPUTY_SOCKETS` list in `collaborator/maintain.py`  
- **Concrete Bypass**:  
  The sandbox only masks **pathname UNIX sockets** listed in `_DEPUTY_SOCKETS`. However:
  - **Docker** also listens on **abstract socket** `@/containerd/containerd.sock` (or similar) — not masked.
  - **systemd-resolved** listens on **127.0.0.53:53** — a loopback TCP/IP address. Since the sandbox uses `--unshare-net`, **loopback is isolated**, so `127.0.0.53` is unreachable. ✅
  - But **dockerd** may listen on **TCP://127.0.0.1:2375** if configured. Again, `--unshare-net` creates a new netns with its own loopback, so **host loopback services are unreachable**. ✅

  However, **abstract UNIX sockets** live in a namespace **outside the filesystem**, and **are NOT isolated by network namespaces** — they are tied to the **mount namespace** or global kernel namespace. Since the sandbox **does not unshare the UTS or IPC namespaces**, and **shares the host’s abstract socket namespace**, a payload could connect to abstract sockets.

  Example: If containerd listens on abstract socket `@io.containerd.cri.v1.cri`, the payload can:
  ```python
  import socket
  s = socket.socket(socket.AF_UNIX)
  s.connect("\0io.containerd.cri.v1.cri")  # note leading null
  ```

  This is **not blocked** by the current mask.

- **Why It Breaks the Seal**: The confused-deputy hole remains open via non-pathname sockets.

- **Does Another Control Catch It?** **NO.** The network isolation (`--unshare-net`) does not affect abstract sockets. The mask only covers pathnames.

- **Fix**: Document this residual, or extend deputy enumeration to include **known abstract sockets** and block them via seccomp (deny `connect` to abstract paths) — but bwrap doesn’t support seccomp. Alternatively, **unshare IPC namespace** (`--unshare-ipc`), which **does isolate abstract sockets** on modern kernels. Add `--unshare-ipc` to the bwrap args.

  **Check**: Does `--unshare-ipc` break maintenance? Unlikely — IPC objects (shared memory, semaphores) are rarely needed in maintenance scripts. Adding `--unshare-ipc` would close this hole.

---

**Finding 4: Token Forgery via Early stderr Write**  
- **ID**: MAINT-TOKEN-FORGERY  
- **Title**: Payload cannot forge token, but what if guard emits it prematurely?  
- **Severity**: LOW  
- **Location**: `_guarded_script` in `collaborator/maintain.py`  
- **Analysis**: The guard script does all checks, then `echo TOKEN >&2; exec "$@"`. Since `exec` replaces the process, the payload **cannot write to stderr before the token**. And the token is written **only after all checks pass**. So **no forgery possible**.

- **Verdict**: **SOUND** — no issue.

---

**Finding 5: TOCTOU in Deputy Socket Presence**  
- **ID**: MAINT-DEPUTY-TOCTOU  
- **Title**: Deputy socket appears between `maintenance_available()` probe and real run  
- **Severity**: LOW  
- **Concrete Bypass**:  
  `maintenance_available()` calls `_present_deputy_sockets()` during probe. Later, during real `wrap_maintenance`, it calls it again. So if a daemon starts **between probe and real run**, the real run will mask it, but the probe didn’t see it — **no problem**, because the real run still masks it. Conversely, if a socket disappears, the real run won’t mask it, but it’s gone anyway. **No bypass**.

- **Verdict**: **SOUND**.

---

### STEELMAN
The maintenance sandbox correctly uses bubblewrap to drop capabilities and mask known deputy sockets, with an in-child guard that verifies critical preconditions before emitting a positive proof token. The fallback to netns isolation is honest, and the design acknowledges documented residuals like blocklist incompleteness and async laundering. The cap check covers both effective and permitted sets, preventing `capset`-based escalation.

### VERDICT
**SERIOUS_FLAWS** — the full `/` bind without `nosuid` allows setuid binary execution to regain `CAP_SYS_ADMIN` and umount masks, completely breaking the seal.  

**Highest-value fix**: **Add masking of critical setuid binaries** (e.g., `--ro-bind /dev/null /usr/bin/su`, `/bin/mount`, `/bin/umount`, `/usr/bin/fusermount`, etc.) in `_bwrap_argv`, treating them as "deputy-like" paths that must be neutralized. This is a small, high-leverage blocklist that closes the most likely privilege escalation path.
