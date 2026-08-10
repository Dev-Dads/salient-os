"""Network isolation for `run_command` (ADR 0003 revisit #1).

`run_command` is the Collaborator's last UNMEDIATED path off the machine: a shell can open a
raw socket, `curl`, `git push` — none of which the mediated egress client (`web_fetch`) sees.
We close that by executing `run_command` inside a fresh NETWORK NAMESPACE with no route out, so
`egress.py` becomes the **sole IP-network path** off the machine. Once that holds, the
"same-channel" egress log is sound for IP egress — there is no other IP channel to leave by.

Linux-only, UNPRIVILEGED: `unshare --map-root-user` creates a user namespace mapping the
current user to root inside (implies `--user`), which grants CAP_NET_ADMIN in the new `--net`
namespace. A fresh net namespace has only a `lo` interface (down) and **no route** — so any
external connect fails closed. Loopback is brought up best-effort so local-only work still runs.

VERIFIED, not trusted (red-team): we do not merely check that `unshare` exits 0 — we verify the
child is genuinely in a DIFFERENT network namespace (`/proc/self/ns/net` inode), both when
probing availability AND per run (fail closed). This catches a substituted / broken /
`LD_PRELOAD`-hooked `unshare` that returns success WITHOUT isolating, which would otherwise make
`network_isolated=True` a lie. Where netns is unavailable — non-Linux, no `unshare`, user
namespaces disabled, or the outcome can't be verified — we run UNISOLATED and report
`isolated=False`: an HONEST flag, never a silent claim of isolation.

Scope (honest): a network namespace isolates raw/TCP/UDP and abstract-namespace UNIX sockets,
but NOT the filesystem or *pathname* UNIX sockets (those are mount-namespace objects; we do not
`--mount`). So a network-capable local daemon reachable by a filesystem socket — most notably a
Docker socket (`/var/run/docker.sock`), also `systemd-resolved`, DBus, or a local forward proxy
— is a residual confused-deputy egress path on hosts that expose one. Out of scope for this
control (closed by not exposing such sockets to the workspace); `--mount`/seccomp AF_UNIX denial
is the follow-up hardening. Exotic non-IP fabrics (RDMA verbs, AF_VSOCK on some DGX/VM hosts)
are a similar device-scoped residual.

This is a Collaborator-side argv transform — `run_supervised` runs whatever argv it is given,
so `salienceos/` core is untouched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

COLLABORATOR_NETNS_VERSION = "0.3.0"

# ADR 0003 revisit #1(a): the signed, DEFAULT-DENY opt-in that ACCEPTS raw (un-isolated) network
# reach for run_command on a host where verified netns is unavailable. Absent it, an
# act_then_report run_command floors to a human hand off-Linux ("isolation earns autonomy" — the
# gate owns that floor). Granting it is the operator's explicit "I accept that this shell can reach
# the network raw on this host". A separate namespace from the egress caps (net.get/net.post) — this
# governs the SHELL's raw reach, not the mediated client's destinations.
SHELL_RAW_NETWORK_CAP = "shell.raw_network"

# Sentinel a per-run guard prints (with exit 44) when the child is NOT in a fresh netns, so the
# caller can correct the flag to isolated=False rather than falsely claiming isolation.
_UNVERIFIED_SENTINEL = "SALIENT_NETNS_UNVERIFIED"


def _resolve(candidates, name: str) -> str:
    """Absolute path to a system binary, resolved at import (before any run_command could plant a
    shadow). Prefer fixed system locations over `$PATH` — a bare name would be PATH-resolved by
    `subprocess` at RUN time, and a run_command child (userns-root, SHARED mount ns) can plant a
    fake binary in a user-writable PATH dir or the workspace cwd (empty/`.` PATH element).
    Giving argv[0] a slash means `subprocess` runs it via execv with no PATH lookup (red-team F1)."""
    for p in candidates:
        if os.path.isfile(p):
            return p
    return shutil.which(name) or candidates[0]


_UNSHARE_BIN = _resolve(["/usr/bin/unshare", "/bin/unshare", "/usr/sbin/unshare", "/sbin/unshare"],
                        "unshare")
_SH_BIN = _resolve(["/bin/sh", "/usr/bin/sh", "/system/bin/sh"], "sh")
# `--map-root-user` implies `--user` and grants CAP_NET_ADMIN inside; `--net` is a fresh netns
# with only `lo` (down) and no route → no egress. Both binaries are ABSOLUTE (probe and wrap
# share them) so neither can be PATH-shadowed.
_UNSHARE = (_UNSHARE_BIN, "--map-root-user", "--net", "--")

_available = None  # cached host-property probe (None = not yet probed)


def _netns_ino() -> "int | None":
    """The current process's network-namespace inode, or None where /proc is unavailable."""
    try:
        return os.stat("/proc/self/ns/net").st_ino
    except OSError:
        return None


def netns_available() -> bool:
    """True iff an unprivileged network namespace can be created AND VERIFIED — the probe checks
    the child is in a DIFFERENT netns (`/proc/self/ns/net` inode), not merely that `unshare`
    exits 0, so a substituted/broken/LD_PRELOAD-hooked `unshare` is caught here (→ unavailable,
    honest flag, never a false `isolated`). Cached — a property of the host, not of any call."""
    global _available
    if _available is not None:
        return _available
    if sys.platform != "linux":
        _available = False
        return _available
    parent = _netns_ino()
    if parent is None:
        _available = False
        return _available
    code = f"import os,sys;sys.exit(0 if os.stat('/proc/self/ns/net').st_ino!={parent} else 3)"
    try:
        r = subprocess.run([*_UNSHARE, sys.executable, "-c", code],
                           capture_output=True, timeout=15, check=False)
        _available = (r.returncode == 0)  # 0 only if the child was genuinely isolated
    except (OSError, subprocess.SubprocessError):
        _available = False
    return _available


def _guarded_script(parent_ino: "int | None") -> str:
    """The inner `sh` script: verify the child is in a fresh netns (fail CLOSED if not — so a
    silently-non-isolating `unshare` cannot egress), bring loopback up, then exec the command
    WITHOUT re-splitting (`exec "$@"`)."""
    guard = ""
    if parent_ino is not None:
        guard = (f'ino=$(stat -Lc %i /proc/self/ns/net 2>/dev/null || echo x);'
                 f'if [ "$ino" = "{parent_ino}" ]; then echo {_UNVERIFIED_SENTINEL} >&2; exit 44; fi;')
    return guard + 'ip link set lo up 2>/dev/null; exec "$@"'


def wrap_no_network(argv):
    """Return ``(argv2, isolated)``. On Linux with VERIFIED netns available, ``argv2`` runs the
    command inside a fresh network namespace with no route out (and fails closed if the namespace
    is not actually fresh); otherwise ``argv2 == argv`` and ``isolated`` is False (run unisolated,
    honestly flagged)."""
    argv = [str(a) for a in (argv or [])]
    if not argv or not netns_available():
        return argv, False
    return [*_UNSHARE, _SH_BIN, "-c", _guarded_script(_netns_ino()), _SH_BIN, *argv], True


def isolation_unverified(returncode: int, stderr) -> bool:
    """True iff a wrapped run failed CLOSED because the child was not in a fresh netns (the per-run
    guard tripped). The caller then reports network_isolated=False honestly — isolation was
    attempted but could not be verified, and the command did NOT run, so no egress occurred."""
    text = stderr if isinstance(stderr, str) else (stderr or b"").decode("utf-8", "replace")
    return returncode == 44 and _UNVERIFIED_SENTINEL in text


def _reset_probe_cache_for_tests():
    """Test hook: forget the cached probe so a test can re-evaluate under a patched platform."""
    global _available
    _available = None
