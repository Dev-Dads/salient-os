"""The Collaborator session — the standing, host-set context every action is
governed against.

Everything here is AUTHORITY or configuration, set by you (the host), never chosen
by salience or the model: which capabilities the worker may use, the policy-signing
key, the executor identity the verifier trusts, the workspace root, and the per-tool
leash. Salience (importance) is supplied per action as influence only.
"""

from __future__ import annotations

from pathlib import Path

from salienceos.interpreter.bus import SalienceBus
from salienceos.verifier import Verifier

# Least privilege by default: file read/write in the workspace, but NOT shell
# execution — a session must explicitly grant "shell.exec" to enable run_command.
DEFAULT_CAPABILITIES = ("fs.read:project", "fs.write:project")


class Session:
    def __init__(
        self,
        workspace,
        capabilities=DEFAULT_CAPABILITIES,
        policy_key: bytes = b"collab-policy-key",
        executor_id: str = "collab-exec",
        executor_key: bytes = b"collab-exec-key",
        leash_overrides: "dict | None" = None,
        allow_adaptation: bool = False,
        default_importance: float = 0.3,
        bus: "SalienceBus | None" = None,
        verifier: "Verifier | None" = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.capabilities = tuple(capabilities)
        self.policy_key = policy_key
        self.executor_id = executor_id
        self.executor_key = executor_key
        self.leash_overrides = dict(leash_overrides or {})
        self.allow_adaptation = bool(allow_adaptation)
        self.default_importance = float(default_importance)
        # An in-memory audit bus by default; pass SalienceBus(path=...) to persist.
        self.bus = bus if bus is not None else SalienceBus()
        self.verifier = verifier if verifier is not None else Verifier(
            policy_key, {executor_id: executor_key})
