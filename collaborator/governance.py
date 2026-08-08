"""The governance seam — every tool action mediated by the salienceos core.

Per action (panel gap #6: each tool call is its own governed action, never a
per-turn average):

    mint action_id  ->  used as BOTH the salience subject AND the verifier
                        envelope_id (the binding key: decide() binds only when
                        directive.subject == verdict.envelope_id)
    issue_policy     (authority: the capabilities/budgets you granted, host-signed)
    signals          (influence only: host-computed ATTENTION + RISK; bounded
                      ref-tokens — a signal cannot carry args/body by construction)
    interpret        -> Directive
    CAPABILITY GATE  the one core-enforced authority check: run only if
                     directive.grants_capability(tool.capability). Salience cannot
                     add capability; the model cannot talk past it (panel gap #2).
    LEASH            a second, Collaborator-enforced axis (propose_first / etc.),
                     sourced from host config only.
    execute + VERIFY mutating tools run supervised; the world is observed
                     independently and govern() clears (or fails) the claim.

Fails closed (panel gap #3): any error producing the directive, an ungranted
capability, or a workspace-escaping path DENIES the action — it is never run to
keep the conversation moving.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from salienceos.control import govern, stakes_for
from salienceos.interpreter import Facet, SalienceSignal, interpret, issue_policy
from salienceos.verifier import issue_envelope, issue_receipt
from salienceos.verifier.observers import observe_action, snapshot_tree

from collaborator.tools import (
    NOTIFY_ONLY,
    PROPOSE_FIRST,
    Tool,
    ToolResult,
    WorkspaceError,
    execute_tool,
    get_tool,
    resolve_in_workspace,
)
from collaborator.toolcall import ToolIntent

COLLABORATOR_GOVERNANCE_VERSION = "0.1.0"

# Decision statuses.
RAN = "ran"          # executed and cleared
FAILED = "failed"    # executed but did not clear (verification failed / tool error)
HELD = "held"        # propose_first: awaiting your approval, not run
DENIED = "denied"    # capability not granted, path escaped, or governance error
NOTIFIED = "notified"  # notify_only: surfaced, not run
UNKNOWN_TOOL = "unknown_tool"

# Host-computed risk per tool (INFLUENCE only — drives verification depth, never
# authority). Not model-selectable.
_TOOL_RISK = {"read_file": 0.0, "write_file": 0.1, "run_command": 0.5}


@dataclass
class Decision:
    action_id: str
    tool: str
    status: str
    reason: str
    leash: str
    cleared: bool = False
    result: "ToolResult | None" = None
    directive: object = None
    outcome: object = None
    preview: "dict | None" = None
    args: dict = field(default_factory=dict)

    def summary(self) -> str:
        """The honest, human-facing line — derived from the real decision/result,
        never from the model's narration (panel gap #4)."""
        if self.status == RAN:
            out = (self.result.output if self.result else "") or "(no output)"
            return f"[{self.tool} ✓ ran, verified] {out}"
        if self.status == FAILED:
            err = (self.result.error if self.result else "") or "did not clear verification"
            return f"[{self.tool} ✗ FAILED — not verified] {err}"
        if self.status == HELD:
            return f"[{self.tool} ⏸ HELD for your approval] would run: {self.preview}"
        if self.status == NOTIFIED:
            return f"[{self.tool} · notify-only] worth doing: {self.args}"
        if self.status == DENIED:
            return f"[{self.tool} ⛔ DENIED] {self.reason}"
        return f"[{self.tool}] {self.reason}"


def _leash_for(session, tool: Tool) -> str:
    overrides = getattr(session, "leash_overrides", None) or {}
    return overrides.get(tool.name, tool.default_leash)


def _emit_signals(session, action_id: str, importance: float, risk: float):
    sigs = [
        SalienceSignal("collaborator", action_id, Facet.ATTENTION, importance, 1.0, ()),
        SalienceSignal("collaborator", action_id, Facet.RISK, risk, 1.0, ()),
    ]
    bus = getattr(session, "bus", None)
    if bus is not None:
        for s in sigs:
            try:
                bus.publish(s)
            except Exception:  # audit is best-effort; never break the action
                pass
    return sigs


def govern_action(session, intent: ToolIntent, importance: "float | None" = None,
                  risk: "float | None" = None) -> Decision:
    """Mediate one tool intent through the full salienceos flow. Never raises."""
    tool = get_tool(intent.name)
    if tool is None:
        return Decision(action_id="", tool=intent.name, status=UNKNOWN_TOOL,
                        reason=f"no such tool: {intent.name}", leash="", args=intent.args)

    action_id = "act-" + uuid.uuid4().hex[:16]
    leash = _leash_for(session, tool)
    imp = session.default_importance if importance is None else importance
    rk = _TOOL_RISK.get(tool.name, 0.3) if risk is None else risk

    # --- interpret (fail closed: any error here denies) ----------------------
    try:
        policy = issue_policy(
            "collab-policy", action_id, tuple(session.capabilities),
            10, 1000, 0, 3, "semantic",
            bool(getattr(session, "allow_adaptation", False)), 2, 0.4, False,
            session.policy_key,
        )
        signals = _emit_signals(session, action_id, imp, rk)
        directive = interpret(policy, signals, session.policy_key)
        bus = getattr(session, "bus", None)
        if bus is not None:
            try:
                bus.emit(directive)
            except Exception:
                pass
    except Exception as exc:  # governance unreachable/errored -> deny, never run
        return Decision(action_id=action_id, tool=tool.name, status=DENIED,
                        reason=f"governance error: {type(exc).__name__}", leash=leash,
                        args=intent.args)

    # --- CAPABILITY GATE (the one core-enforced authority) -------------------
    if not directive.grants_capability(tool.capability):
        return Decision(action_id=action_id, tool=tool.name, status=DENIED,
                        reason=f"policy does not grant '{tool.capability}'", leash=leash,
                        directive=directive, args=intent.args)

    # --- workspace pre-check for path tools (deny before running) ------------
    if intent.name in ("write_file", "read_file"):
        try:
            resolve_in_workspace(session.workspace, str(intent.args.get("path") or ""))
        except WorkspaceError as exc:
            return Decision(action_id=action_id, tool=tool.name, status=DENIED,
                            reason=str(exc), leash=leash, directive=directive, args=intent.args)

    # --- LEASH (second axis) --------------------------------------------------
    if leash == NOTIFY_ONLY:
        return Decision(action_id=action_id, tool=tool.name, status=NOTIFIED,
                        reason="notify-only leash", leash=leash, directive=directive,
                        args=intent.args)
    if leash == PROPOSE_FIRST:
        return Decision(action_id=action_id, tool=tool.name, status=HELD,
                        reason="propose-first leash: awaiting approval", leash=leash,
                        directive=directive, args=intent.args,
                        preview={"tool": tool.name, "args": intent.args,
                                 "verification_depth": directive.verification_depth})

    # leash == ACT_THEN_REPORT -> run it
    return execute_and_verify(session, tool, directive, action_id, intent.args)


def execute_and_verify(session, tool: Tool, directive, action_id: str, args: dict) -> Decision:
    """Run a permitted, unleashed action and (for mutating tools) verify the claim
    against the observed world. Used both for act_then_report and for an approved
    propose_first action."""
    leash = _leash_for(session, tool)
    # Read-only: gate already passed; execute and report (nothing is mutated).
    if tool.verify_mode == "none":
        try:
            execution = execute_tool(tool, session.workspace, args)
        except WorkspaceError as exc:
            return Decision(action_id, tool.name, DENIED, str(exc), leash, directive=directive, args=args)
        ok = execution.result.ok
        return Decision(action_id, tool.name, RAN if ok else FAILED,
                        "read" if ok else "read failed", leash, cleared=ok,
                        result=execution.result, directive=directive, args=args)

    # Command with no declared artifact: clearance is the SUPERVISOR's exit code
    # (its own view of the child, not the tool's self-report) — a nonzero exit
    # can't be narrated as success.
    if tool.verify_mode == "exit":
        try:
            execution = execute_tool(tool, session.workspace, args)
        except WorkspaceError as exc:
            return Decision(action_id, tool.name, DENIED, str(exc), leash, directive=directive, args=args)
        cleared = bool(execution.result.ok)
        return Decision(action_id, tool.name, RAN if cleared else FAILED,
                        "supervised exit 0" if cleared else f"exit {execution.exit_code}",
                        leash, cleared=cleared, result=execution.result, directive=directive, args=args)

    # verify_mode == "artifact": build envelope BEFORE running, snapshot, execute
    # receipt from the REAL result, observe world independently, govern.
    execution = None
    try:
        env_stakes = stakes_for(directive, tool.base_stakes)
        env_args = ({"path": str(args.get("path") or ""), "content": str(args.get("content") or "")}
                    if tool.op == "file.write"
                    else {"command": args.get("command"), "declared_outputs": []})
        env = issue_envelope(action_id, tool.op, env_args, "project_mutation", env_stakes,
                             "collab-policy", session.policy_key)
        pre = snapshot_tree(session.workspace)
        execution = execute_tool(tool, session.workspace, args)
        receipt = issue_receipt(
            f"r-{action_id}", action_id, int(execution.exit_code or 0),
            execution.artifact_hashes, tuple(execution.write_set),
            bool(execution.result.ok), session.executor_id, session.executor_key,
        )
        world = observe_action(env, session.workspace, pre, execution.supervised)
        outcome = govern(session.verifier, directive, env, receipt, world)
    except WorkspaceError as exc:
        return Decision(action_id, tool.name, DENIED, str(exc), leash, directive=directive, args=args)
    except Exception as exc:  # verification plumbing errored AFTER the act -> report honestly
        return Decision(action_id, tool.name, FAILED,
                        f"verification error: {type(exc).__name__}", leash,
                        result=(execution.result if execution is not None else None),
                        directive=directive, args=args)

    cleared = bool(outcome.cleared)
    return Decision(action_id, tool.name, RAN if cleared else FAILED,
                    "verified" if cleared else "not verified", leash, cleared=cleared,
                    result=execution.result, directive=directive, outcome=outcome, args=args)
