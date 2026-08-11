"""Contained execution for an AUTONOMOUS `run_command` — "protection earns autonomy" (F-6 / ADR 0003).

`run_command` has no filesystem fence. Harm A (a shell rewriting the Collaborator's OWN code) is closed
today only by WITHHOLDING autonomy: `codefence.code_protection_available()` is False, so every shell is
floored to a human hand. This module makes that predicate REAL — an autonomous shell may run only inside a
bubblewrap sandbox where the code roots (`codefence.PROTECTED_ROOTS`) are READ-ONLY, `$HOME`/secrets are
absent, and the network is isolated — so it can EARN autonomy by accepting containment. The human path is
untouched: a human-approved `run_command` keeps FULL filesystem reach (system maintenance must not break),
so containment applies ONLY to the autonomy-earning path (`require_code_protection=True`).

WHY bubblewrap (not `unshare --mount` by hand): a single-level read-only bind is defeatable by the
mapped-root child (CVE-2014-5206 / `MNT_LOCK_READONLY`). bwrap's ro-bind holds via TWO independent layers:
(1) it drops ALL capabilities before exec (`--cap-drop ALL`) and `--disable-userns` blocks regaining them,
so the payload has no `CAP_SYS_ADMIN` to `mount -o remount,rw` at all; (2) as a backstop, the kernel LOCKS
`MS_RDONLY` when a mount propagates to a less-privileged namespace (`mount_namespaces(7)`), so even a nested
namespace cannot remount-rw or unmount-to-reveal. bwrap does NOT itself call `MNT_LOCK_READONLY` (no
userspace API) — the guarantee is assembled from cap-drop + kernel semantics, which is EXACTLY why we do
not trust it: the in-child guard PROVES, per run, that each root is present, unwritable, and mounted ro.

CRITICAL — do NOT nest this under `netns` (`unshare --map-root-user`): that makes the child uid 0, and
bwrap's `drop_all_caps()` early-returns for real-uid 0, so the sandbox would INHERIT `CAP_SYS_ADMIN` and
the ro-bind would be a lie (bubblewrap#287). bwrap runs on its OWN path, as the real unprivileged uid, and
does BOTH mount + network containment itself; the certified `netns.py` path stays for the human/opted-in
shell only.

Linux-only, stdlib-only, NO new Python dependency (bwrap is a system binary resolved like `unshare`).
Where bwrap is absent / user namespaces are blocked / non-Linux, containment is UNAVAILABLE and the seam
withholds autonomy (honest flag, exactly today's behaviour) — never a fake fence.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

from collaborator import codefence, netns

COLLABORATOR_CONTAINED_VERSION = "0.1.0"

# ADR 0003 / F-6: the signed, DEFAULT-DENY opt-in that lets an AUTONOMOUS run_command EARN autonomy by
# running CONTAINED. Verified containment is necessary but NOT sufficient — the operator must ALSO sign
# this grant (mirrors shell.raw_network / net.post.auto:<host>). Absent it, a capable host still withholds
# run_command autonomy (a human hand). The single largest autonomy expansion in the system is thus
# operator-ratcheted, never lifted by the mere presence of bwrap on a host.
SHELL_CONTAINED_AUTONOMY_CAP = "shell.contained_autonomy"

# Sentinel + exit code the in-child guard prints when a protected root is NOT verifiably read-only, so the
# caller corrects code_protected -> False rather than falsely claiming protection. The NET half reuses
# netns's own sentinel/exit-44 so netns.isolation_unverified() keeps working unchanged.
_CODEFENCE_UNVERIFIED_SENTINEL = "SALIENT_CODEFENCE_UNVERIFIED"
_CODEFENCE_UNVERIFIED_EXIT = 45


def _resolve(candidates, name: str) -> str:
    """Absolute path to a system binary, resolved at import (before any run_command could plant a shadow),
    identical in spirit to netns._resolve — a bare name would be PATH-resolved at RUN time."""
    for p in candidates:
        if os.path.isfile(p):
            return p
    return shutil.which(name) or candidates[0]


_BWRAP_BIN = _resolve(["/usr/bin/bwrap", "/bin/bwrap", "/usr/local/bin/bwrap"], "bwrap")
_SH_BIN = _resolve(["/bin/sh", "/usr/bin/sh", "/system/bin/sh"], "sh")

# Decide the read-only system view ONCE at import from what actually EXISTS on this host — a hard
# `--ro-bind` of a missing dir aborts bwrap, and aarch64 (Sparky) has no /lib64. Deny-by-default: only
# these system dirs, the code roots (ro), and the workspace (rw) are ever in the view — never $HOME,
# /root, /run (docker.sock), /sys, or the host `/`.
_RO_SYSTEM = tuple(p for p in ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/etc")
                   if os.path.isdir(p))
# Belt over the whole-/etc ro-bind: shadow /dev/null over the highest-value secret files so even a read
# returns empty. (Under DAC these are root-only anyway, and we cap-drop; this is defence in depth.)
_MASK = tuple(p for p in ("/etc/shadow", "/etc/gshadow", "/etc/sudoers", "/etc/ssh")
              if os.path.exists(p))

_available = None  # cached host-property probe (None = not yet probed)


def _roots_with_witness():
    """(root_dir, witness_file) pairs for the contained guard, from codefence's single source of truth.
    The witness is the module __file__ each root was resolved from — guaranteed to exist INSIDE the root —
    so the guard can tell "ro-bound + unwritable" (protected) from "absent" (the wrapper silently didn't
    apply): absence must never count as proof of protection."""
    return codefence.protected_roots_with_witness()


def _guarded_script(parent_ino: "int | None", pairs, *, check_net: bool) -> str:
    """The inner `sh` script: VERIFY (fail CLOSED) then `exec "$@"`. NET half (only when check_net) reuses
    netns's inode check + sentinel/exit-44. CODE half proves, for each protected root R with witness F:
    (1) F is PRESENT (else absence masquerades as protection), (2) opening F for APPEND FAILS (a real
    write-block — writes nothing), (3) /proc/self/mountinfo shows R mounted `ro` (structural). Any failure
    -> sentinel + exit 45."""
    net = ""
    if check_net and parent_ino is not None:
        net = (f'ino=$(stat -Lc %i /proc/self/ns/net 2>/dev/null || echo x);'
               f'if [ "$ino" = "{parent_ino}" ]; then echo {netns._UNVERIFIED_SENTINEL} >&2; exit 44; fi;')
    specs = " ".join(f'"{r.as_posix()}|{w.as_posix()}"' for r, w in pairs)
    code = (
        f'for spec in {specs}; do '
        f'r=${{spec%%|*}}; f=${{spec#*|}}; '
        # (1) present
        f'[ -r "$f" ] || {{ echo {_CODEFENCE_UNVERIFIED_SENTINEL} >&2; exit {_CODEFENCE_UNVERIFIED_EXIT}; }}; '
        # (2) append must FAIL (writes nothing); if it SUCCEEDS the root is writable -> unverified
        f'if ( : >> "$f" ) 2>/dev/null; then echo {_CODEFENCE_UNVERIFIED_SENTINEL} >&2; '
        f'exit {_CODEFENCE_UNVERIFIED_EXIT}; fi; '
        # (3) structural: the mount for r carries the ro option
        f'opts=$(awk -v m="$r" \'$5==m {{print $6}}\' /proc/self/mountinfo | tail -n1); '
        f'case ",$opts," in *,ro,*) ;; *) echo {_CODEFENCE_UNVERIFIED_SENTINEL} >&2; '
        f'exit {_CODEFENCE_UNVERIFIED_EXIT} ;; esac; '
        f'done;'
    )
    return net + code + ' exec "$@"'


def _bwrap_argv(workspace, pairs, *, unshare_net: bool, parent_ino: "int | None", inner):
    """Assemble the full bwrap argv (order-sensitive: bwrap applies ops left-to-right)."""
    argv = [
        _BWRAP_BIN,
        # namespaces — EXPLICIT --unshare-user (NOT --unshare-all, whose -try variant fails OPEN)
        "--unshare-user", "--unshare-ipc", "--unshare-pid", "--unshare-uts", "--unshare-cgroup-try",
    ]
    if unshare_net:
        argv.append("--unshare-net")
    argv += [
        "--disable-userns", "--assert-userns-disabled",   # child cannot create a fresh userns to regain caps
        "--cap-drop", "ALL",                              # forces cap-drop even if a future caller sets uid 0
        "--die-with-parent", "--new-session",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
    ]
    for p in _RO_SYSTEM:
        argv += ["--ro-bind", p, p]
    for p in _MASK:                                       # /dev/null over secrets, AFTER the /etc bind
        argv += ["--ro-bind", "/dev/null", p]
    for root, _witness in pairs:                          # THE GUARANTEE — code roots read-only, identity-bound
        rp = str(root)
        argv += ["--ro-bind", rp, rp]
    ws = str(workspace)
    home = os.path.join(ws, ".sandbox-home")              # rw, persistent (warm caches), INSIDE the fence
    argv += [
        "--bind", ws, ws,
        "--chdir", ws,
        "--clearenv",
        "--setenv", "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "--setenv", "HOME", home,
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "LANG", "C.UTF-8",
        # git identity so a contained `git commit` works without a host ~/.gitconfig (no bind-back)
        "--setenv", "GIT_AUTHOR_NAME", "collaborator", "--setenv", "GIT_AUTHOR_EMAIL", "collaborator@localhost",
        "--setenv", "GIT_COMMITTER_NAME", "collaborator", "--setenv", "GIT_COMMITTER_EMAIL", "collaborator@localhost",
        # bwrap opts end; the guard runs as `sh -c <guard> sh <original argv…>` — the trailing _SH_BIN is
        # $0 (a name), the caller appends the original argv as $1..$n, and the guard's `exec "$@"` runs it.
        "--", _SH_BIN, "-c", inner, _SH_BIN,
    ]
    return argv


def wrap_contained(argv, workspace, *, roots_with_witness=None, unshare_net: bool = True):
    """Return ``(run_argv, isolated, protected)``. On Linux with bwrap present, ``run_argv`` runs the
    command inside a contained view (code roots ro, no $HOME/secrets, tmpfs /tmp, cleared env, and — when
    ``unshare_net`` — a fresh routeless netns), with an in-child guard that fails CLOSED if the code root
    is not verifiably read-only (exit 45) or the netns is not fresh (exit 44). ``isolated``/``protected``
    are the pre-run BELIEFS (verified/downgraded by the caller via ``*_unverified``). Where containment is
    unavailable (non-Linux, no bwrap, no roots) returns ``(argv, False, False)`` — the caller then refuses
    to run an autonomous shell, so nothing executes uncontained."""
    argv = [str(a) for a in (argv or [])]
    pairs = _roots_with_witness() if roots_with_witness is None else tuple(roots_with_witness)
    if not argv or sys.platform != "linux" or not os.path.isfile(_BWRAP_BIN) or not pairs:
        return argv, False, False
    parent_ino = netns._netns_ino() if unshare_net else None
    inner = _guarded_script(parent_ino, pairs, check_net=unshare_net)
    run_argv = _bwrap_argv(workspace, pairs, unshare_net=unshare_net, parent_ino=parent_ino, inner=inner)
    run_argv += argv  # the guard's `exec "$@"` receives the original argv as $1..$n after argv0=sh
    return run_argv, unshare_net, True


def protection_unverified(returncode: int, stderr) -> bool:
    """True iff a contained run failed CLOSED because a code root was not verifiably read-only (the per-run
    guard tripped, exit 45). The caller then reports code_protected=False honestly — protection was
    attempted but not verified, and the command did NOT run, so no unprotected shell executed."""
    text = stderr if isinstance(stderr, str) else (stderr or b"").decode("utf-8", "replace")
    return returncode == _CODEFENCE_UNVERIFIED_EXIT and _CODEFENCE_UNVERIFIED_SENTINEL in text


def setup_failed(returncode: int, stderr) -> bool:
    """True iff bwrap itself failed during SETUP (the payload never ran). Safety is intact (nothing ran),
    but the negative-sentinel convention can't distinguish it, so the caller DOWNGRADES protected->False
    (fail-safe only; never used to upgrade) — mirrors the netns deferred-hardening note in ADR 0003."""
    text = stderr if isinstance(stderr, str) else (stderr or b"").decode("utf-8", "replace")
    return returncode != 0 and text.startswith("bwrap:")


def containment_available(roots_with_witness=None) -> bool:
    """True iff an autonomous run_command can ACTUALLY run with PROTECTED_ROOTS read-only on THIS host —
    VERIFIED, not merely "bwrap exists": a probe child runs the SAME argv template and its in-child guard
    proves each root is present, unwritable, and ro-mounted, and the netns is fresh. Cached — a property
    of the host, not of any call. Mirrors netns.netns_available()."""
    global _available
    if _available is not None:
        return _available
    pairs = _roots_with_witness() if roots_with_witness is None else tuple(roots_with_witness)
    if sys.platform != "linux" or not os.path.isfile(_BWRAP_BIN) or not pairs:
        _available = False
        return _available
    tmp = tempfile.mkdtemp(prefix="salient-contain-probe-")
    try:
        run_argv, isolated, protected = wrap_contained([_SH_BIN, "-c", "exit 0"], tmp,
                                                        roots_with_witness=pairs, unshare_net=True)
        if not protected:
            _available = False
            return _available
        r = subprocess.run(run_argv, capture_output=True, timeout=20, check=False)
        # rc==0 only if the guard verified BOTH halves (fresh netns AND every root ro) and the payload ran.
        _available = (r.returncode == 0 and isolated)
    except (OSError, subprocess.SubprocessError):
        _available = False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return _available


def _reset_probe_cache_for_tests():
    """Test hook: forget the cached probe so a test can re-evaluate under a patched platform/bwrap."""
    global _available
    _available = None
