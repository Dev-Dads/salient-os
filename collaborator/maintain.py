"""The MAINTENANCE SANDBOX for a HUMAN-approved / opted-in `run_command` (ADR 0003 revisit #1b,
the "deputy seal").

The human `run_command` path keeps FULL filesystem reach by design (system maintenance must not
break). Network is isolated by a routeless netns (`netns.py`) so `egress.py` is the sole IP path off
the machine — but that netns has a HOLE: it must run as MAPPED-ROOT (to get CAP_NET_ADMIN to create
the namespace), so the shell holds CAP_SYS_ADMIN over its OWN mount namespace. That lets it reach a
host daemon's *pathname* UNIX socket (`/var/run/docker.sock`, containerd, podman, ...) and have THAT
daemon egress on its behalf — a confused-deputy path AROUND the netns — and a naive `/dev/null` mask
does not help: a mapped-root child can just `umount` the mask to reveal the real socket (proven on a
live host: `after-umount: socket`).

We close that ROBUSTLY by running the human command inside a bubblewrap sandbox with a FULL
read-write host view (so maintenance keeps its reach) in which:

  * the enumerated deputy sockets are bound to `/dev/null` (`--ro-bind`), and
  * ALL capabilities are dropped (`--cap-drop ALL` + `--disable-userns`), so the payload has NO
    CAP_SYS_ADMIN to `umount` the mask — the seal is un-removable (proven: `umount: blocked`), and
  * the network is a fresh routeless netns (`--unshare-net`), the same guarantee as `netns.py`.

Two escape vectors an external panel raised were REPRODUCED-then-refuted on a live host, and the
preconditions are now VERIFIED per run rather than trusted: (a) a setuid-root binary is reachable in the
full-/ view (`su`/`mount`/`fusermount`/`pkexec` are NOT masked), but bwrap sets `NoNewPrivs=1`, so the
kernel ignores setuid AND file capabilities on execve — no escalation (the guard asserts NoNewPrivs=1 and
an empty bounding set); (b) `/proc/<pid>/root` of a host process would reach the UNMASKED host socket, but
bwrap runs the sandbox in a CHILD userns, and ptrace-access from a child userns to a parent-userns process
is denied, so every host process's `/proc/<pid>/root` is EACCES (verified) — no `--unshare-pid` needed
(host process visibility is kept for maintenance). Abstract-namespace sockets and host-loopback TCP
(e.g. dockerd on 127.0.0.1:2375) are cut by the fresh `--unshare-net` netns.

It runs as `--uid 0`/`--gid 0` — the same "appears as root" view as the current mapped-root netns
path (files it creates are owned by the collaborator's REAL uid via the userns map), so maintenance
behaviour is unchanged EXCEPT that caps are dropped and `/dev` is a fresh minimal devtmpfs (host
`/dev` bound rw leaves `/dev/null` unwritable inside a userns, breaking every `2>/dev/null`). The
in-child guard PROVES, per run, that (1) the netns is fresh, (2) all four cap sets are all-zero AND
NoNewPrivs is 1 (so the mask is un-removable — no cap to umount or raise, and setuid/fcap binaries in the
full-/ view are defanged), and (3) each deputy socket is masked — emitting a POSITIVE proof token only then,
immediately before exec. Verification is WHITELISTED on that token: any setup/guard failure leaves no
token and fails CLOSED. In particular, a real-uid-0 collaborator — where bwrap's cap-drop early-returns
(bubblewrap#287) and the mask WOULD be umountable — trips the `CapEff` check, so the sandbox reports
UNAVAILABLE and the caller falls back to the routeless netns (honest, no worse than today).

BLOCKLIST, honestly: a deputy socket not enumerated here (rootless docker at
`/run/user/<uid>/docker.sock`, `$SSH_AUTH_SOCK`, a DB socket that can `COPY … PROGRAM`, an arbitrary
local proxy) still slips; and because the view is full-FS WRITE, an approved-but-adversarial command
can still LAUNDER egress asynchronously (plant a cron job / systemd unit / `~/.ssh/authorized_keys` /
a script a later un-isolated process runs). This seal raises the SYNCHRONOUS-egress bar to the
(higher) laundering bar; it does not make the deliberately-trusted maintenance path leak-proof — the
laundering residual is the same class as the deceived-human + unfenced-maintenance residuals
documented elsewhere.

Linux-only, stdlib-only, NO new dependency (bwrap is a system binary, resolved by `contained.py`).
Where bwrap is unavailable the caller falls back to `netns.wrap_no_network` — today's certified
routeless wrap plus the documented `docker.sock` residual — never a fake seal.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile

from collaborator import contained, netns

COLLABORATOR_MAINTAIN_VERSION = "0.1.0"

# Known network-capable local daemons reachable by a *pathname* UNIX socket — a confused-deputy egress
# path (docker/containerd/podman pull & push images; the DBus system bus reaches NetworkManager/systemd;
# systemd-resolved is DNS). Canonicalized + de-duped at wrap time; only PRESENT sockets are masked.
# BLOCKLIST (see the module docstring): a socket not listed still slips.
_DEPUTY_SOCKETS = (
    "/run/docker.sock", "/var/run/docker.sock",
    "/run/containerd/containerd.sock", "/var/run/containerd/containerd.sock",
    "/run/podman/podman.sock", "/var/run/podman/podman.sock",
    "/run/dbus/system_bus_socket", "/var/run/dbus/system_bus_socket",
    "/run/systemd/resolve/io.systemd.Resolve",
)

# The POSITIVE proof the in-child guard emits AFTER every check passed (netns fresh AND caps all-dropped
# AND every deputy masked), immediately before exec. Verification is WHITELISTED on this token: the
# caller keeps network_isolated=True ONLY if it is present, so ANY setup/guard failure fails CLOSED by
# construction. A payload cannot forge its ABSENCE (written before the payload runs) nor forge PRESENCE
# to any effect (the payload runs only if the guard already passed and emitted it).
_MAINT_VERIFIED_SENTINEL = "SALIENT_MAINT_VERIFIED"

# Negative sentinels + exit codes (diagnostics; the AUTHORITATIVE signal is the positive token above).
# NET reuses netns's own sentinel + exit 44 so netns.isolation_unverified() semantics are shared.
_CAPS_PRESENT_SENTINEL = "SALIENT_MAINT_CAPS_PRESENT"    # a cap set nonzero / NoNewPrivs unset -> umountable
_CAPS_PRESENT_EXIT = 47
_MASK_UNVERIFIED_SENTINEL = "SALIENT_MAINT_MASK_UNVERIFIED"  # a present deputy socket is not masked
_MASK_UNVERIFIED_EXIT = 46

_available = None  # cached host-property probe (None = not yet probed)


def _present_deputy_sockets():
    """The subset of `_DEPUTY_SOCKETS` that exist as sockets on THIS host right now, canonicalized
    (realpath, so a `/var/run` -> `/run` symlink collapses to one target) and de-duplicated. A path
    carrying a shell metacharacter is dropped — impossible for these fixed system paths, a belt like
    `contained._pairs_shell_safe` so a pathological target can never corrupt the guard's quoted word
    list. Computed at WRAP time (not import) so a daemon started after import is still masked."""
    out, seen = [], set()
    for p in _DEPUTY_SOCKETS:
        try:
            rp = os.path.realpath(p)
            st = os.stat(rp)  # follows symlinks; rp is already canonical
        except OSError:
            continue
        if not stat.S_ISSOCK(st.st_mode) or rp in seen:
            continue
        if any(c in rp for c in '\n\r\t "\'\\$`;|&<>*?()[]{}'):  # metachar belt — never in a real path
            continue
        seen.add(rp)
        out.append(rp)
    return tuple(out)


def _guarded_script(parent_ino: "int | None", deputies) -> str:
    """The inner `sh` script: VERIFY (fail CLOSED) then `exec "$@"`. (1) NET: the netns is fresh (reuses
    netns's inode check + sentinel/exit-44). (2) NO-ESCALATION: all four cap sets (Eff/Prm/Bnd/Amb) are
    all-zero AND NoNewPrivs is 1 — so the payload has no effective cap to `umount` the mask, none permitted
    to raise, an empty bounding set, and setuid/fcap binaries in the full-/ view are defanged (kernel
    ignores setuid on execve when NoNewPrivs=1); a real-uid-0 host where bwrap's cap-drop early-returns
    trips here. (3) MASK: each present deputy socket is now a character device (bound to /dev/null). Only
    after all pass does it emit the POSITIVE token, immediately before exec."""
    net = ""
    if parent_ino is not None:
        net = (f'ino=$(stat -Lc %i /proc/self/ns/net 2>/dev/null || echo x);'
               f'if [ "$ino" = "{parent_ino}" ]; then echo {netns._UNVERIFIED_SENTINEL} >&2; exit 44; fi;')
    # VERIFY the "no privilege escalation possible" precondition — the load-bearing reason the mask is
    # un-removable — rather than TRUST bwrap set it up (external-panel hardening; even the dissent that
    # correctly refuted the setuid escape noted the guard only relied on, never asserted, these):
    #   * all four cap sets (Eff, Prm, Bnd, Amb) are all-zero: no EFFECTIVE cap to `umount`, none
    #     PERMITTED to `capset` into effective, an empty BOUNDING set so a file-capability binary can add
    #     nothing, and no AMBIENT cap carried across an exec; and
    #   * NoNewPrivs is 1: the kernel then IGNORES setuid bits AND file capabilities on every execve, so a
    #     setuid-root binary reachable in the full-/ view (`su`/`mount`/`fusermount`/`pkexec`, none masked)
    #     CANNOT regain privilege (reproduced live: NoNewPrivs=1, the escape is defanged).
    # `--cap-drop ALL` zeroes the cap sets and bwrap sets NoNewPrivs; any host/bwrap where that does not
    # hold (e.g. a real-uid-0 collaborator whose cap-drop early-returns) trips here and fails CLOSED.
    caps = (
        f'for c in CapEff CapPrm CapBnd CapAmb; do '
        f'v=$(awk -v k="$c:" \'$1==k{{print $2}}\' /proc/self/status 2>/dev/null);'
        f'case "$v" in ""|*[!0]*) echo {_CAPS_PRESENT_SENTINEL} >&2; exit {_CAPS_PRESENT_EXIT};; esac; '
        f'done;'
        f'nnp=$(awk \'$1=="NoNewPrivs:"{{print $2}}\' /proc/self/status 2>/dev/null);'
        f'[ "$nnp" = "1" ] || {{ echo {_CAPS_PRESENT_SENTINEL} >&2; exit {_CAPS_PRESENT_EXIT}; }};'
    )
    mask = ""
    if deputies:
        specs = " ".join(f'"{d}"' for d in deputies)
        mask = (f'for d in {specs}; do [ -c "$d" ] || '
                f'{{ echo {_MASK_UNVERIFIED_SENTINEL} >&2; exit {_MASK_UNVERIFIED_EXIT}; }}; done;')
    return net + caps + mask + f' echo {_MAINT_VERIFIED_SENTINEL} >&2; exec "$@"'


def _bwrap_argv(workspace, deputies, *, unshare_net: bool, inner: str):
    """Assemble the full bwrap argv (order-sensitive: bwrap applies path ops left-to-right)."""
    argv = [
        contained._BWRAP_BIN,
        # Appear as root (maintenance parity with the mapped-root netns path); files map to the real uid.
        "--uid", "0", "--gid", "0",
        "--unshare-user",
    ]
    if unshare_net:
        argv.append("--unshare-net")                     # fresh routeless netns — egress.py stays sole IP path
    argv += [
        "--disable-userns", "--assert-userns-disabled",  # child cannot create a fresh userns to regain caps
        "--cap-drop", "ALL",                             # THE umount-defense: no CAP_SYS_ADMIN => masks stick
        "--die-with-parent", "--new-session",
        "--bind", "/", "/",                              # FULL read-write host view (maintenance keeps reach)
        # fresh /dev (host /dev bound rw leaves /dev/null unwritable in a userns) + fresh /proc.
        "--dev", "/dev", "--proc", "/proc",
    ]
    for d in deputies:                                   # mask each present deputy socket, AFTER the full bind
        argv += ["--ro-bind", "/dev/null", d]
    ws = str(workspace)
    argv += [
        "--chdir", ws,
        # bwrap opts end; the guard runs as `sh -c <guard> sh <original argv…>` — trailing _SH_BIN is $0,
        # the caller appends the original argv as $1..$n, and the guard's `exec "$@"` runs it unsplit.
        "--", contained._SH_BIN, "-c", inner, contained._SH_BIN,
    ]
    return argv


def wrap_maintenance(argv, workspace, *, unshare_net: bool = True):
    """Return ``(run_argv, isolated, sandboxed)``. On Linux with bwrap present, ``run_argv`` runs the
    command in the maintenance sandbox (full-FS rw view, deputy sockets masked, caps dropped, routeless
    netns when ``unshare_net``), with an in-child guard that fails CLOSED (no positive token) if the
    netns is not fresh, caps are not all-dropped, or a deputy is not masked. ``isolated`` is the pre-run
    network BELIEF (verified/downgraded by the caller via ``verified_ok``); ``sandboxed`` is True iff the
    command was wrapped. Where the sandbox is unavailable (non-Linux / no bwrap / empty argv) returns
    ``(argv, False, False)`` — the caller then falls back to the routeless netns wrap."""
    argv = [str(a) for a in (argv or [])]
    if not argv or sys.platform != "linux" or not os.path.isfile(contained._BWRAP_BIN):
        return argv, False, False
    deputies = _present_deputy_sockets()
    parent_ino = netns._netns_ino() if unshare_net else None
    inner = _guarded_script(parent_ino, deputies)
    run_argv = _bwrap_argv(workspace, deputies, unshare_net=unshare_net, inner=inner)
    run_argv += argv  # the guard's `exec "$@"` receives the original argv as $1..$n after argv0=sh
    return run_argv, unshare_net, True


def verified_ok(returncode: int, stderr) -> bool:
    """AUTHORITATIVE, WHITELIST check that a maintenance run was genuinely sandboxed AND network-isolated:
    True iff the in-child guard emitted its POSITIVE proof token — which it does ONLY after the netns was
    verified fresh, ALL caps were verified dropped (so the deputy mask is un-removable), and every deputy
    socket was verified masked, immediately before exec. RETURNCODE-INDEPENDENT — a genuinely-sandboxed
    payload may exit non-zero (a failing maintenance command) yet was still isolated; the token, written
    before exec, proves it. Any setup/guard failure leaves no token and fails CLOSED."""
    text = stderr if isinstance(stderr, str) else (stderr or b"").decode("utf-8", "replace")
    return _MAINT_VERIFIED_SENTINEL in text


def maintenance_unverified(returncode: int, stderr) -> bool:
    """True iff a wrapped run failed CLOSED because the in-child guard tripped (netns not fresh / caps
    not dropped / a deputy not masked). The command did NOT run (the guard trips before exec), so no
    egress occurred. Retained for diagnostics; the AUTHORITATIVE signal is the positive verified_ok()."""
    text = stderr if isinstance(stderr, str) else (stderr or b"").decode("utf-8", "replace")
    return returncode in (44, _MASK_UNVERIFIED_EXIT, _CAPS_PRESENT_EXIT) and any(
        s in text for s in (netns._UNVERIFIED_SENTINEL, _MASK_UNVERIFIED_SENTINEL, _CAPS_PRESENT_SENTINEL))


def maintenance_available() -> bool:
    """True iff a human run_command can ACTUALLY run in the maintenance sandbox on THIS host — VERIFIED,
    not merely "bwrap exists": a probe child runs the SAME argv template and its in-child guard proves
    the netns fresh, caps all-dropped, and every present deputy masked. Cached (a host property). A
    real-uid-0 collaborator (where cap-drop early-returns, leaving the mask umountable) fails the CapEff
    check here and the sandbox is reported UNAVAILABLE, so the caller falls back to the routeless netns."""
    global _available
    if _available is not None:
        return _available
    result = False
    if sys.platform == "linux" and os.path.isfile(contained._BWRAP_BIN):
        tmp = tempfile.mkdtemp(prefix="salient-maint-probe-")
        try:
            run_argv, isolated, sandboxed = wrap_maintenance(
                [contained._SH_BIN, "-c", "exit 0"], tmp, unshare_net=True)
            if sandboxed:
                r = subprocess.run(run_argv, capture_output=True, timeout=20, check=False)
                # The positive token proves the guard verified fresh netns + caps dropped + deputies masked.
                result = verified_ok(r.returncode, r.stderr) and isolated
        except (OSError, subprocess.SubprocessError):
            result = False
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    _available = result
    return result


def _reset_probe_cache_for_tests():
    """Test hook: forget the cached probe so a test can re-evaluate under a patched platform/bwrap."""
    global _available
    _available = None
