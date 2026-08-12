"""Autonomy-authorship provenance for workspace files (ADR 0003 residual sweep — F2 dropper).

The workspace is SHARED read-write between the autonomy-earning CONTAINED run (``contained.py``)
and the UNCONTAINED human maintenance run (``netns``/``maintain``, ``cwd=workspace``, full FS reach
by design). So a NOT-human-approved (autonomous) action can DROP an executable file into the
workspace that a human later approves and runs uncontained — ``sh ./build.sh`` — with the human's
own reach. ``contained.py:31-38`` documented this as an open axis (an autonomous ``write_file`` is
already a workspace dropper; the contained run is a second one).

This module does NOT fence it — the human deliberately keeps full reach for maintenance (the same
stance as ``sensitivepaths``). It FLAGS it: it records which workspace files an autonomous action
authored, and the seam surfaces "authored by an autonomous run — not reviewed by you" in the human
approval preview + an audit tag on execution, so the provenance is VISIBLE at the moment of the hand.

DEFENCE-IN-DEPTH / ADVISORY ONLY — **NOT a boundary**, exactly like ``codefence.names_code_root`` and
``sensitivepaths.names_sensitive_path``. The human can still approve; this only makes provenance
legible. POROUS by construction: the recognizer matches argv TOKENS against the recorded rel-paths, so
a file reached WITHOUT naming it literally (a glob, a shell variable, ``cat f | sh``, a symlink) is out
of scope — recall loss is acceptable; a FALSE ⚠ that noise-blinds the approving human, or a stale ⚠
after a human re-vets the bytes, is the real failure, so it errs toward precision and is CLEARED when a
human-approved write re-authors the path. Stdlib-only, cross-platform, TOTAL (never raises).
"""

from __future__ import annotations

import posixpath
import shlex

COLLABORATOR_PROVENANCE_VERSION = "0.1.0"


def norm_rel(path) -> str:
    """Normalize a path to the workspace-relative POSIX form used everywhere here — so a write_file
    ``rel`` (raw, possibly ``\\``-separated or ``./``-prefixed), a ``snapshot_tree`` key (already
    posix), and a recognizer candidate all land in ONE comparable form. Returns "" for anything that
    isn't a workspace-relative file path (empty, ``.``, or an escaping ``..`` prefix). Never raises."""
    try:
        s = str(path or "").replace("\\", "/").strip()
    except Exception:  # noqa: BLE001 — a hostile __str__ must fail closed to "" (no record/match)
        return ""
    if not s:
        return ""
    s = posixpath.normpath(s)
    if s in (".", "") or s == ".." or s.startswith("../"):
        return ""
    return s.lstrip("/") or ""


def _tokenize(command) -> list:
    """Argv tokens of a run_command. A list/tuple is already tokenized; a string is shlex-split
    (posix) so quotes/escapes resolve, falling back to a whitespace split on a parse error
    (unbalanced quote). Total — never raises."""
    if isinstance(command, (list, tuple)):
        try:
            return [str(c) for c in command]
        except Exception:  # noqa: BLE001
            return []
    try:
        s = str(command or "")
    except Exception:  # noqa: BLE001
        return []
    if not s:
        return []
    try:
        return shlex.split(s, posix=True)
    except Exception:  # noqa: BLE001 — an unbalanced quote must not break the recognizer
        return s.split()


def _rel_candidates(token, workspace) -> set:
    """The workspace-relative posix form(s) an argv token could denote. Skips flags (``-x``) and the
    empty token. Yields the relative form (``./build.sh`` -> ``build.sh``) and, when the token is an
    ABSOLUTE path textually under ``workspace``, the relativized form. Purely textual (no filesystem
    resolve) so it stays total and can't be walked out of the workspace by a symlink."""
    out: set = set()
    try:
        t = str(token or "")
    except Exception:  # noqa: BLE001
        return out
    if not t or t.startswith("-"):
        return out
    rel = norm_rel(t)
    if rel:
        out.add(rel)
    if workspace is not None:
        try:
            ws = str(workspace).replace("\\", "/")
            ws = posixpath.normpath(ws).rstrip("/")
            ta = posixpath.normpath(t.replace("\\", "/"))
            if ws and (ta == ws or ta.startswith(ws + "/")):
                inner = norm_rel(ta[len(ws):])
                if inner:
                    out.add(inner)
        except Exception:  # noqa: BLE001
            pass
    return out


def references_autonomous_file(command, authored, workspace) -> str:
    """POROUS, best-effort tag: does ``command``'s argv reference a workspace file that an autonomous
    action authored (``authored`` = the session's recorded rel-path set)? Returns the matched rel-path(s)
    joined by ``,`` (empty string = no match). ADVISORY ONLY — never a deny (module docstring).

    TOTAL — never raises (govern_action/approve promise never to raise): any error fails closed to ""."""
    try:
        authored_set = set(authored or ())
    except Exception:  # noqa: BLE001
        return ""
    if not authored_set:
        return ""
    matched: list = []
    for tok in _tokenize(command):
        for cand in _rel_candidates(tok, workspace):
            if cand in authored_set and cand not in matched:
                matched.append(cand)
    return ",".join(matched)
