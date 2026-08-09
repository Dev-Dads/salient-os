"""The Collaborator's small, honest, workspace-fenced toolset.

Every tool is confined to a declared workspace root. The Collaborator's own wiring
(its config, policy key, audit trail) lives OUTSIDE that root, so a governed tool
can never rewrite the rules it runs under (panel gap #1). Paths are resolved and
containment-checked BEFORE execution (defence in depth — the verifier's observers
also fail closed on an escaping path, but we refuse to even run one).

Mutating tools run under the verifier's supervisor (``run_supervised``) so the
world can be observed independently of what the tool reports — the basis for the
"hands can't lie" property: a claimed write whose bytes don't match the real file
fails verification. Each tool reports what it truly did; a tool that didn't run
says so (never a fabricated success).
"""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

from salienceos.verifier import Stakes
from salienceos.verifier.observers import SupervisedResult, run_supervised
from salienceos.verifier.signing import sha256_bytes

COLLABORATOR_TOOLS_VERSION = "0.1.0"

# Leash levels — the second, Collaborator-enforced control axis (panel gap #2).
ACT_THEN_REPORT = "act_then_report"
PROPOSE_FIRST = "propose_first"
NOTIFY_ONLY = "notify_only"


class WorkspaceError(Exception):
    """A tool path escapes the declared workspace root."""


@dataclass(frozen=True)
class Tool:
    name: str
    capability: str      # the policy capability this tool needs (authority gate)
    mutating: bool       # mutating -> executes a side effect; read -> gate only
    op: str              # verifier envelope op ("file.write" / "shell.run")
    base_stakes: Stakes  # verification floor for the envelope
    default_leash: str   # host-config default; never chosen by salience/model
    # How this action's claim is checked against reality:
    #   "artifact" — full verifier pipeline (re-hash the produced file); the
    #                strongest "can't lie about what was written".
    #   "exit"     — supervised exit code (the supervisor's own view, not the
    #                tool's self-report); for commands with no declared artifact.
    #   "none"     — read-only; nothing is mutated, nothing to verify.
    verify_mode: str = "none"


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str = ""
    error: str = ""


@dataclass
class Execution:
    """A tool's real result plus the inputs the verifier needs to check it."""

    result: ToolResult
    supervised: "SupervisedResult | None" = None
    exit_code: "int | None" = None
    write_set: tuple[str, ...] = ()
    artifact_hashes: dict = field(default_factory=dict)


_TOOLS: dict[str, Tool] = {
    "write_file": Tool("write_file", "fs.write:project", True, "file.write",
                       Stakes.NORMAL, ACT_THEN_REPORT, verify_mode="artifact"),
    "read_file": Tool("read_file", "fs.read:project", False, "file.read",
                      Stakes.LOW, ACT_THEN_REPORT, verify_mode="none"),
    "run_command": Tool("run_command", "shell.exec", True, "shell.run",
                        Stakes.NORMAL, PROPOSE_FIRST, verify_mode="exit"),  # strictest leash
}


def get_tool(name: str) -> "Tool | None":
    return _TOOLS.get(name)


def toolset() -> dict[str, Tool]:
    return dict(_TOOLS)


def resolve_in_workspace(workspace, rel: str) -> Path:
    """Resolve ``rel`` under ``workspace`` and refuse anything that escapes it."""
    root = Path(workspace).resolve()
    if not isinstance(rel, str) or not rel:
        raise WorkspaceError("empty path")
    try:
        target = (root / rel).resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise WorkspaceError(f"unresolvable path: {rel!r}") from exc
    if target != root and root not in target.parents:
        raise WorkspaceError(f"path escapes workspace: {rel!r}")
    return target


# --- executors ---------------------------------------------------------------

def _exec_write(workspace, args: dict) -> Execution:
    rel = str(args.get("path") or "")
    content = str(args.get("content") or "")
    target = resolve_in_workspace(workspace, rel)  # raises WorkspaceError -> denied upstream
    # Create the parent directory (WITHIN the resolved workspace path, so still fenced) so a
    # write to a nested path like `.github/workflows/ci.yml` succeeds instead of failing on a
    # missing dir — a real failure class found live (the proposer correctly wanted to write a
    # CI workflow but write_file couldn't create the dir, so it retried 9× and failed).
    target.parent.mkdir(parents=True, exist_ok=True)
    # A real child process performs the write, so the supervisor observes an exit
    # status this process did not author. We write raw UTF-8 BYTES (not write_text)
    # so the file on disk is byte-for-byte the content we hashed — text mode would
    # translate "\n"->"\r\n" on Windows, diverging the disk bytes from the artifact
    # hash and false-failing verification on every multi-line write (a real bug found
    # by the live task-scale run; Linux CI never saw it).
    script = ("import sys,pathlib;"
              "pathlib.Path(sys.argv[1]).write_bytes(sys.argv[2].encode('utf-8'));"
              "sys.exit(0)")
    res = run_supervised([sys.executable, "-c", script, str(target), content], cwd=workspace)
    ok = res.returncode == 0
    return Execution(
        result=ToolResult(ok=ok, output=(f"wrote {rel} ({len(content)} bytes)" if ok else ""),
                          error=("" if ok else (res.stderr or b"").decode("utf-8", "replace"))),
        supervised=res, exit_code=res.returncode,
        write_set=(rel,), artifact_hashes={rel: sha256_bytes(content.encode("utf-8"))},
    )


def _exec_read(workspace, args: dict) -> Execution:
    rel = str(args.get("path") or "")
    target = resolve_in_workspace(workspace, rel)
    if not target.is_file():
        return Execution(result=ToolResult(ok=False, error=f"no such file: {rel}"))
    return Execution(result=ToolResult(ok=True, output=target.read_text(encoding="utf-8", errors="replace")))


def _exec_command(workspace, args: dict) -> Execution:
    cmd = args.get("command")
    if isinstance(cmd, str):
        argv = shlex.split(cmd)
    elif isinstance(cmd, (list, tuple)):
        argv = [str(c) for c in cmd]
    else:
        return Execution(result=ToolResult(ok=False, error="command must be a string or list"))
    if not argv:
        return Execution(result=ToolResult(ok=False, error="empty command"))
    res = run_supervised(argv, cwd=workspace)
    ok = res.returncode == 0
    out = (res.stdout or b"").decode("utf-8", "replace")
    err = (res.stderr or b"").decode("utf-8", "replace")
    return Execution(
        result=ToolResult(ok=ok, output=out, error=err),
        supervised=res, exit_code=res.returncode,
        write_set=(),  # nothing declared; observe_action's write-set diff catches undeclared writes
        artifact_hashes={},
    )


_EXECUTORS = {"write_file": _exec_write, "read_file": _exec_read, "run_command": _exec_command}


def execute_tool(tool: Tool, workspace, args: dict) -> Execution:
    """Run a resolved tool. Raises WorkspaceError on an escaping path (the caller
    turns that into a DENY); other failures come back as ``ok=False`` results."""
    return _EXECUTORS[tool.name](workspace, args)
