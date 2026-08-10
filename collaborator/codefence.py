"""Structural protection of the Collaborator's OWN code from ``run_command`` (F-6 "Harm A").

`run_command` has **no filesystem fence** — ``write_file``/``read_file`` are workspace-fenced
(``tools.resolve_in_workspace``), but ``_exec_command`` runs arbitrary argv with no containment. A
governed shell could therefore overwrite the Collaborator's own governance code and neuter **F1**
("the model can never self-originate an autonomous emission"): F1 holds only as long as the model
never gets autonomous shell. This module is the single source of truth for "the Collaborator's own
code root", plus the cheap, cross-platform layers that keep that code un-rewritable:

  * ``PROTECTED_ROOTS`` — the ``collaborator/`` + ``salienceos/`` package dirs, resolved ONCE at
    import (mirrors ``netns._resolve``: fixed at import, before any run_command could move things).
  * ``disjoint_from_code()`` — a Session's workspace may never overlap a protected root (BOTH
    directions), so the already-fenced ``write_file``/``read_file`` can never even name the code,
    and the (future) read-only-bind protection can never foot-gun a workspace-inside-code layout.
  * ``names_code_root()`` — a POROUS, best-effort recognizer used for a proposer hard-deny and to
    surface a ⚠ in the human approval preview. **NOT a boundary** (a shell reaches the code without
    naming it literally — a variable, base64, a symlink, ``python -c "open(p,'w')"``); labelled DiD.
  * ``code_protection_available()`` — the govern-time predictor of whether ``run_command`` runs
    with its write-reach to the code STRUCTURALLY prevented. **Returns False in this build**:
    OS-level prevention (a read-only bind-mount of ``PROTECTED_ROOTS`` in a nested user namespace,
    or bubblewrap — CVE-adjacent, see the F-6 plan) is a deferred follow-up. While it is False the
    seam WITHHOLDS ``run_command`` autonomy (floors ``act_then_report`` → ``propose_first``), so an
    unprotected shell can never AUTO-run — it always gets a human hand. When the follow-up lands
    this becomes a real, cached host probe (mirroring ``netns.netns_available()``) and
    ``run_command`` earns autonomy on hosts where protection verifies.

Cross-platform, stdlib-only, NO new dependency. ``salienceos/`` core is untouched — this is a
collaborator-side guard consulted by the seam; it merely *locates* the core to protect it.
"""

from __future__ import annotations

from pathlib import Path

import salienceos

COLLABORATOR_CODEFENCE_VERSION = "0.1.0"


def _resolved_roots() -> "tuple[Path, ...]":
    """The Collaborator's own code roots — ``collaborator/`` (this package) and ``salienceos/``
    (the interpreter/verifier core the seam calls into). The F1 guarantee spans BOTH, so both are
    protected. Resolved once at import; a module whose path can't be resolved is skipped (the guard
    is a cheap layer, not the boundary — it fails open rather than raising at import)."""
    roots: list[Path] = []
    for mod_file in (__file__, getattr(salienceos, "__file__", None)):
        if not mod_file:
            continue
        try:
            p = Path(mod_file).resolve().parent
        except (OSError, ValueError, RuntimeError):
            continue
        if p.is_dir() and p not in roots:
            roots.append(p)
    return tuple(roots)


PROTECTED_ROOTS: "tuple[Path, ...]" = _resolved_roots()


class WorkspaceOverlapsCodeError(ValueError):
    """A Session workspace equals/contains/is-contained-by the Collaborator's own code root.

    A ``ValueError`` so it composes with the other fail-loud construction checks in ``Session``.
    """


def disjoint_from_code(workspace) -> None:
    """Raise ``WorkspaceOverlapsCodeError`` if ``workspace`` equals, contains, or is contained by
    any protected code root. Enforces the invariant ``tools.py`` already ASSUMES ("the Collaborator's
    wiring lives OUTSIDE the workspace root"), so the fenced ``write_file``/``read_file`` can never
    reach the code. Checked at Session construction — fail LOUD, like the leash/proactivity checks."""
    try:
        ws = Path(workspace).resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise WorkspaceOverlapsCodeError(f"unresolvable workspace: {workspace!r}") from exc
    for root in PROTECTED_ROOTS:
        if ws == root or root in ws.parents or ws in root.parents:
            raise WorkspaceOverlapsCodeError(
                f"workspace {ws} overlaps the Collaborator's own code root {root} — choose a "
                "workspace disjoint from the collaborator/ and salienceos/ packages (a governed "
                "shell must never be able to reach the rules it runs under)")


def names_code_root(command) -> str:
    """POROUS, best-effort tag of a ``run_command`` whose ``command`` literally names a protected
    code root. Returns the matched root path(s) joined by ``,`` (empty string = no literal match).

    DEFENCE-IN-DEPTH ONLY — **NOT a boundary.** A shell can reach the code without naming it
    literally (a variable, base64, a symlink, ``python -c "open(p,'w')"``), so this MUST NOT be
    relied on to contain ``run_command``. It exists to hard-deny an obvious PROPOSER-authored
    self-rewrite and to surface a ⚠ in the human approval preview. The real prevention is OS-level
    and deferred (see ``code_protection_available``). Matching is a substring test on the
    separator-normalized command text against each root's POSIX form, so it catches the naive
    ``python -c "open('/abs/…/collaborator/governance.py','w')"`` and a bare path token alike."""
    if isinstance(command, (list, tuple)):
        text = " ".join(str(c) for c in command)
    else:
        text = str(command or "")
    if not text:
        return ""
    hay = text.replace("\\", "/")
    matched: list[str] = []
    for root in PROTECTED_ROOTS:
        needle = root.as_posix()
        if needle and needle in hay and str(root) not in matched:
            matched.append(str(root))
    return ",".join(matched)


def code_protection_available() -> bool:
    """Govern-time predictor: does ``run_command`` run with its write-reach to the Collaborator's
    own code STRUCTURALLY prevented?

    **False in this build.** OS-level prevention (a read-only bind-mount of ``PROTECTED_ROOTS`` in a
    nested user namespace, or bubblewrap ``--ro-bind``) is a deferred follow-up (the single-level
    bind is defeatable by the mapped-root child — CVE-2014-5206 / ``MNT_LOCK_READONLY``; see the
    F-6 plan). While this is False the seam withholds ``run_command`` autonomy so an unprotected
    shell can never auto-run — it always gets a human hand. When the follow-up lands this becomes a
    real, cached host probe (mirroring ``netns.netns_available()``), and ``run_command`` earns
    autonomy on hosts where the read-only bind is verified. Kept as a function (not a constant) so
    that landing is a one-body change and every call site is already wired."""
    return False
