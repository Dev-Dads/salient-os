"""Host-side WORLD observers — the cheap executor-independent facts (spec M2).

Always-on, CPU-only, millisecond-scale:
  - supervisor exit status   (the process supervisor's own view, not the
                              receipt's self-reported code)
  - host-namespace re-hash   (declared artifacts re-read from outside the
                              executor after it has finished)
  - write-set diff           (pre/post snapshot of the workspace: declared
                              paths changed, no undeclared paths changed)

On the DGX Spark target these become container-runtime queries and
bind-mount re-hashes read from outside the sandbox mount namespace; the
evidence contract is identical, which is why the composer never needs to
know which implementation produced a fact. Only this module (and future
observer modules) may construct WorldEvidence.

All observations happen after executor teardown; nothing here mutates the
workspace (the verifier is side-effect-free by design).
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from salienceos.verifier.contract import obligation_id, write_set_value
from salienceos.verifier.evidence import WorldEvidence
from salienceos.verifier.signing import sha256_bytes


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _resolve_within(root, path: str):
    """Resolve `path` under `root`, following symlinks, and return the resolved
    Path only if it stays inside the workspace root; else None.

    This is the workspace-escape guard (spec §1 in-scope: a wrong/misfiring
    model or buggy policy that authorizes an absolute, `..`-bearing, or
    symlinked path). An escaping path resolves to None and its observers report
    "absent", which cannot agree with an envelope-derived content hash — so the
    obligation fails closed rather than observing an unintended host file.
    """
    root = Path(root).resolve()
    try:
        resolved = (root / path).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if resolved == root or root in resolved.parents:
        return resolved
    return None


def snapshot_tree(root) -> dict:
    """Map of workspace-relative posix path -> content marker.

    Files map to their sha256; directories to the marker "dir"; symlinks to
    "symlink:<target>" WITHOUT being followed. Recording symlinks by target
    (rather than hashing through them) keeps a symlinked path from masquerading
    as content that matches a declared hash, and makes directory creation
    visible to the write-set boundary check (spec M2) — without which an honest
    `dir.make` produces an empty write-set and can never verify.
    """
    root = Path(root)
    snap = {}
    # followlinks=False (the default): os.walk will not recurse into symlinked
    # directories, so we cannot be walked out of the workspace.
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames:
            p = Path(dirpath) / name
            snap[_rel(root, p)] = ("symlink:" + os.readlink(p)) if p.is_symlink() else "dir"
        for name in filenames:
            p = Path(dirpath) / name
            if p.is_symlink():
                snap[_rel(root, p)] = "symlink:" + os.readlink(p)
            else:
                snap[_rel(root, p)] = sha256_bytes(p.read_bytes())
    return snap


def observed_write_set(pre: dict, post: dict, exempt=()) -> list:
    """Paths added, removed, or changed between two snapshots.

    ``exempt`` drops paths that are ENTAILED by a declared side effect (the ancestor
    directories of a declared write path) rather than undeclared mutations — see
    ``entailed_ancestors`` and ``observe_action``.
    """
    exempt = set(exempt)
    changed = []
    for path in set(pre) | set(post):
        if path in exempt:
            continue
        if pre.get(path) != post.get(path):
            changed.append(path)
    return sorted(changed)


def entailed_ancestors(rel_path: str) -> list:
    """The strict ancestor directories of a workspace-relative path, as posix rel-paths.

    ``a/b/c.txt`` -> ``["a", "a/b"]``; a flat path -> ``[]``. These are the directories a
    write of ``rel_path`` necessarily brings into being — creating ``a/b/c.txt`` cannot
    happen without ``a/`` and ``a/b/`` — so they are entailed by declaring the write, not a
    separate hidden side effect. Excludes the full path itself (that IS the declared subject).
    """
    rel = str(rel_path or "").replace("\\", "/").strip("/")
    if not rel:
        return []
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def rehash(root, path: str) -> str:
    resolved = _resolve_within(root, path)
    if resolved is None:
        return "absent"  # escapes the workspace (absolute/../symlink) → fail closed
    # Reject symlinks explicitly: is_file() follows them, which would let a
    # symlinked declared path read content from elsewhere in the workspace.
    lp = (Path(root) / path)
    if lp.is_symlink() or not resolved.is_file():
        return "absent"
    return sha256_bytes(resolved.read_bytes())


def path_state(root, path: str) -> str:
    resolved = _resolve_within(root, path)
    if resolved is None:
        return "absent"
    lp = (Path(root) / path)
    if lp.is_symlink():
        return "absent"
    if resolved.is_dir():
        return "present:dir"
    if resolved.is_file():
        return "present:file"
    return "absent"


@dataclass(frozen=True)
class SupervisedResult:
    """Exit status as observed by the supervisor that ran the job."""

    returncode: int
    stdout: bytes
    stderr: bytes


def run_supervised(argv, cwd, timeout_seconds: int = 120) -> SupervisedResult:
    """Run a tool job and observe its exit status from the supervisor side.

    Local analog of reading the container runtime's exit status: the value
    comes from this process's wait() on the child, not from anything the
    child wrote.
    """
    completed = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    return SupervisedResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


# --- WorldEvidence constructors ------------------------------------------------

EXIT_CHANNEL = "host.supervisor"
REHASH_CHANNEL = "host.rehash"
SNAPSHOT_CHANNEL = "host.snapshot"
STAT_CHANNEL = "host.stat"


def exit_evidence(envelope_id: str, result: SupervisedResult, provenance: str) -> WorldEvidence:
    return WorldEvidence(
        obligation_id=obligation_id(envelope_id, "exit_status"),
        kind="exit_status",
        value=str(result.returncode),
        failure_mode="supervisor_exit",
        channel=EXIT_CHANNEL,
        provenance=provenance,
    )


def artifact_evidence(envelope_id: str, root, path: str, provenance: str) -> WorldEvidence:
    return WorldEvidence(
        obligation_id=obligation_id(envelope_id, "artifact_hash", path),
        kind="artifact_hash",
        value=rehash(root, path),
        failure_mode="host_rehash",
        channel=REHASH_CHANNEL,
        provenance=provenance,
    )


def write_set_evidence(envelope_id: str, pre: dict, post: dict, provenance: str,
                       exempt=()) -> WorldEvidence:
    return WorldEvidence(
        obligation_id=obligation_id(envelope_id, "write_set"),
        kind="write_set",
        value=write_set_value(observed_write_set(pre, post, exempt)),
        failure_mode="host_snapshot_diff",
        channel=SNAPSHOT_CHANNEL,
        provenance=provenance,
    )


def path_state_evidence(envelope_id: str, root, path: str, provenance: str) -> WorldEvidence:
    return WorldEvidence(
        obligation_id=obligation_id(envelope_id, "path_state", path),
        kind="path_state",
        value=path_state(root, path),
        failure_mode="host_stat",
        channel=STAT_CHANNEL,
        provenance=provenance,
    )


# --- Reference observation orchestration ---------------------------------------

_SIDE_EFFECT_OBSERVERS = {
    # op -> (side-effect kind, list of subject paths from the envelope args)
    "file.write": lambda a: ("artifact", [a["path"]]),
    "shell.run": lambda a: ("artifact", list(a.get("declared_outputs", []))),
    "dir.make": lambda a: ("path_state", [a["path"]]),
    "file.delete": lambda a: ("path_state", [a["path"]]),
}


def observe_action(envelope, root, pre_snapshot, supervised_result, provenance: str = "obs"):
    """Always-on host-side WORLD observation for one completed action.

    Emits the exit-status and write-set facts for every op, plus the
    op-appropriate side-effect fact (artifact re-hash for writes/commands,
    path-state stat for directory/deletion ops). This is the reference wiring
    the composer expects; a caller may supply richer/stakes-scaled world
    evidence, but this floor is what makes each op class verifiable on day one.
    """
    eid = envelope.envelope_id
    post = snapshot_tree(root)
    builder = _SIDE_EFFECT_OBSERVERS.get(envelope.op)
    kind, subjects = (None, [])
    if builder is not None:
        kind, subjects = builder(envelope.args)
    # A directory that is an ANCESTOR of a declared subject path is entailed by that
    # declaration (you cannot write `a/b/c.txt` without `a/` and `a/b/`), so it is not an
    # undeclared mutation — exempt it from the write-set boundary. Bounded and safe: only the
    # declared path's own parent chain is exempt (each subject separately re-hashed/stat'd), so
    # nothing outside that chain can be hidden. Without this, an honest nested write false-fails
    # the boundary because the auto-created parents show up as "extra" changed paths.
    exempt = set()
    for sp in subjects:
        exempt.update(entailed_ancestors(sp))
    world = [
        exit_evidence(eid, supervised_result, provenance),
        write_set_evidence(eid, pre_snapshot, post, provenance, exempt=exempt),
    ]
    for path in subjects:
        if kind == "artifact":
            world.append(artifact_evidence(eid, root, path, provenance))
        else:
            world.append(path_state_evidence(eid, root, path, provenance))
    return world
