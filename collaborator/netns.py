"""Network isolation for `run_command` (ADR 0003 revisit #1).

`run_command` is the Collaborator's last UNMEDIATED path off the machine: a shell can open a
raw socket, `curl`, `git push` — none of which the mediated egress client (`web_fetch`) sees.
We close that by executing `run_command` inside a fresh NETWORK NAMESPACE with no route out, so
`egress.py` becomes the **sole IP-network path** off the machine. Once that holds, the
"same-channel" egress log is sound for IP egress — there is no other IP channel to leave by.

Scope (honest): a network namespace isolates raw/TCP/UDP and abstract-namespace UNIX sockets,
but NOT *pathname* UNIX sockets (those are mount-namespace objects, and we deliberately do not
`--mount`). So a local network-capable daemon reachable by a filesystem socket (a docker
socket, a local proxy) is a residual confused-deputy path on hosts that run one — out of scope
for this control, closed by not exposing such sockets to the workspace.

Linux-only, UNPRIVILEGED: `unshare --map-root-user` creates a user namespace mapping the
current user to root inside (implies `--user`), which grants CAP_NET_ADMIN in the new `--net`
namespace. A fresh net namespace has only a `lo` interface (down) and **no route** — so any
external connect fails closed. Loopback is brought up best-effort so local-only work still
runs; the security property (no external egress) does not depend on that.

Where netns is unavailable — non-Linux, no `unshare`, or user namespaces disabled — we FALL
BACK to running unisolated and report ``isolated=False``: an HONEST flag, never a silent claim
of isolation (ADR 0003: "do not claim global egress mediation" until this holds).

This is a Collaborator-side argv transform — `run_supervised` runs whatever argv it is given,
so `salienceos/` core is untouched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

COLLABORATOR_NETNS_VERSION = "0.1.0"


def _resolve_unshare() -> str:
    """Absolute path to the REAL `unshare`, resolved at import — before any `run_command` could
    plant a shadow. A bare name would be PATH-resolved by `subprocess` at RUN time using the
    inherited `$PATH`, and a run_command child (userns-root in a SHARED mount namespace) can plant
    a fake `unshare` in a user-writable PATH dir — or trivially in the workspace cwd if PATH has
    an empty/`.` element — that ignores the namespace flags and egresses while we falsely report
    `isolated=True` (red-team F1). Preferring fixed system locations over `$PATH`, and giving
    `argv[0]` a slash so `subprocess` runs it via execv (no PATH lookup), closes that."""
    for p in ("/usr/bin/unshare", "/bin/unshare", "/usr/sbin/unshare", "/sbin/unshare"):
        if os.path.isfile(p):
            return p
    return shutil.which("unshare") or "/usr/bin/unshare"


# Unprivileged fresh USER + NET namespace. `--map-root-user` implies `--user` and gives
# CAP_NET_ADMIN inside; a fresh `--net` namespace has only `lo` (down) and no route → no egress.
# The binary is an ABSOLUTE path (shared by the probe and the wrap) so it cannot be PATH-shadowed.
_UNSHARE_BIN = _resolve_unshare()
_UNSHARE = (_UNSHARE_BIN, "--map-root-user", "--net", "--")
# Bring loopback up best-effort, then exec the real command. In `sh -c SCRIPT sh a b c`, the
# args after the SCRIPT's `$0` sentinel (`sh`) become `$@` = [a, b, c]; `exec "$@"` runs them
# WITHOUT re-splitting (each stays one argv element). `ip` may be absent → `lo` stays down,
# still no external network.
_LO_UP_THEN_EXEC = 'ip link set lo up 2>/dev/null; exec "$@"'

_available = None  # cached host-property probe (None = not yet probed)


def netns_available() -> bool:
    """True iff an unprivileged network namespace can be created with the EXACT `unshare`
    invocation `wrap_no_network` uses. Cached — the answer is a property of the host, not of
    any single call. Non-Linux, a missing `unshare`, or disabled user namespaces → False."""
    global _available
    if _available is not None:
        return _available
    if sys.platform != "linux":
        _available = False
        return _available
    try:
        # A python no-op inside the namespace: confirms the unshare itself succeeds (python is
        # certainly present — us). Isolation is then structural (the fresh netns has no route).
        r = subprocess.run([*_UNSHARE, sys.executable, "-c", "pass"],
                           capture_output=True, timeout=15, check=False)
        _available = (r.returncode == 0)
    except (OSError, subprocess.SubprocessError):
        _available = False
    return _available


def wrap_no_network(argv):
    """Return ``(argv2, isolated)``. On Linux with unprivileged netns available, ``argv2`` runs
    the command inside a fresh network namespace with no route out (loopback best-effort up), so
    it cannot egress; otherwise ``argv2 == argv`` and ``isolated`` is False (run unisolated,
    honestly flagged). Pure/deterministic given ``netns_available()`` — the argv composition is
    unit-testable on every platform."""
    argv = [str(a) for a in (argv or [])]
    if not argv or not netns_available():
        return argv, False
    return [*_UNSHARE, "sh", "-c", _LO_UP_THEN_EXEC, "sh", *argv], True


def _reset_probe_cache_for_tests():
    """Test hook: forget the cached probe so a test can re-evaluate under a patched platform."""
    global _available
    _available = None
