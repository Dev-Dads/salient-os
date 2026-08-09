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

from salienceos.consumers import consume
from salienceos.control import govern, stakes_for
from salienceos.interpreter import Facet, SalienceSignal, interpret, issue_policy
from salienceos.verifier import issue_envelope, issue_receipt
from salienceos.verifier.observers import observe_action, snapshot_tree

from collaborator import egress
from collaborator.tools import (
    ACT_THEN_REPORT,
    NOTIFY_ONLY,
    PROPOSE_FIRST,
    Tool,
    ToolResult,
    WorkspaceError,
    execute_tool,
    flag_offense_shape,
    get_tool,
    is_controlled_location,
    resolve_in_workspace,
)
from collaborator.policycaps import apply_cap, granted_capabilities, leash_cap
from collaborator.toolcall import ToolIntent

COLLABORATOR_GOVERNANCE_VERSION = "0.1.0"

# Decision statuses.
RAN = "ran"          # executed and cleared
FAILED = "failed"    # executed but did not clear (verification failed / tool error)
HELD = "held"        # propose_first: awaiting your approval, not run
DENIED = "denied"    # capability not granted, path escaped, or governance error
NOTIFIED = "notified"  # notify_only: surfaced, not run
PAUSED = "paused"    # the host paused the session from the judgment view — not run
UNKNOWN_TOOL = "unknown_tool"

# Host-computed risk per tool (INFLUENCE only — drives verification depth, never
# authority). Not model-selectable.
_TOOL_RISK = {"read_file": 0.0, "write_file": 0.1, "run_command": 0.5, "web_fetch": 0.2}


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
    # Stage-4-live: the two learning channels' records for this action (only when
    # the session allows adaptation). disagreement == weight BLOCKS + memory RETAINS
    # as a non-decaying inhibitor (the same risky event refused as a skill, kept as
    # a permanent warning).
    adaptation: object = None
    memory: object = None
    disagreement: bool = False
    learning_error: "str | None" = None  # consume() failed — an inhibitor MAY be lost
    # Provenance (audit only, never authority): who ORIGINATED this action — "direct"
    # for a user turn, "collaborator" for a proposal the Collaborator raised itself. So a
    # forensic reader can tell "the user asked for this" from "the agent suggested it and
    # the user approved" (panel F5/F6). Bus-level provenance tagging is a later add.
    origin: str = "direct"
    # Single-use guard for a HELD decision (red-team: a pooled/held decision was re-runnable
    # via approve()). Set True the moment approve() runs it OR a veto retires it, so no path —
    # a second approve(), or approve() on a vetoed decision — can re-execute it or reuse its
    # action_id. A DENIED re-gate does NOT consume it (it stays retryable once authority holds).
    consumed: bool = False
    # ADR 0003: the channel-integrity egress record for a net.get (audit only), and an
    # AUDIT-ONLY offense-shape tag (never a deny — the boundary is structural default-deny).
    egress: object = None
    offense_flag: str = ""

    def summary(self) -> str:
        """The honest, human-facing line — derived from the real decision/result,
        never from the model's narration (panel gap #4)."""
        if self.disagreement:
            tail = "  ⟂ LEARNING BLOCKED + RETAINED AS INHIBITOR (channels disagree)"
        elif self.learning_error:
            tail = f"  ⚠ LEARNING ERROR — inhibitor may be lost: {self.learning_error}"
        else:
            tail = ""
        if self.offense_flag:  # ADR 0003 audit-only tag — recorded, never a deny
            tail += f"  ⚑ offense-shape audit: {self.offense_flag}"
        if self.status == RAN:
            out = (self.result.output if self.result else "") or "(no output)"
            return f"[{self.tool} ✓ ran, verified] {out}{tail}"
        if self.status == FAILED:
            err = (self.result.error if self.result else "") or "did not clear verification"
            return f"[{self.tool} ✗ FAILED — not verified] {err}{tail}"
        if self.status == HELD:
            return f"[{self.tool} ⏸ HELD for your approval] would run: {self.preview}{tail}"
        if self.status == NOTIFIED:
            return f"[{self.tool} · notify-only] worth doing: {self.args}{tail}"
        if self.status == DENIED:
            return f"[{self.tool} ⛔ DENIED] {self.reason}"
        return f"[{self.tool}] {self.reason}"


_VALID_LEASHES = (ACT_THEN_REPORT, PROPOSE_FIRST, NOTIFY_ONLY)


def _leash_for(session, tool: Tool) -> str:
    overrides = getattr(session, "leash_overrides", None) or {}
    return overrides.get(tool.name, tool.default_leash)


def _resolve_leash(session, tool: Tool, override: "str | None") -> str:
    """Per-task leash. ``override`` is HOST authority (a UI/proposer caller), never
    threaded from model output. None -> today's resolution (unchanged behaviour). A
    value must be one of the three defined levels; an invalid one fails CLOSED to
    propose_first (held, awaiting approval) rather than ever running unleashed. A leash
    only chooses among the levels — it can never widen the capability gate."""
    if override is None:
        return _leash_for(session, tool)
    if override in _VALID_LEASHES:
        return override
    return PROPOSE_FIRST


def _emit_signals(session, action_id: str, importance: float, risk: float, learning_eligible: bool):
    sigs = [
        SalienceSignal("collaborator", action_id, Facet.ATTENTION, importance, 1.0, ()),
        SalienceSignal("collaborator", action_id, Facet.RISK, risk, 1.0, ()),
    ]
    # Stage-4-live: request learning ONLY for tools whose outcome is actually
    # consumed (artifact-verified) — otherwise the ADAPTATION signal is a promise
    # the wiring can't keep, since exit/read tools produce no GovernedOutcome to
    # consume() (panel MEDIUM). The weight gate + memory governor then decide, and
    # for an over-cap risk they DISAGREE (block the skill, keep the warning).
    if getattr(session, "allow_adaptation", False) and learning_eligible:
        sigs.append(SalienceSignal("collaborator", action_id, Facet.ADAPTATION, importance, 1.0, ()))
    bus = getattr(session, "bus", None)
    if bus is not None:
        for s in sigs:
            try:
                bus.publish(s)
            except Exception:  # audit is best-effort; never break the action
                pass
    return sigs


def govern_action(session, intent: ToolIntent, importance: "float | None" = None,
                  risk: "float | None" = None, *, leash: "str | None" = None) -> Decision:
    """Mediate one tool intent through the full salienceos flow. Never raises.

    ``leash`` is an optional HOST-supplied per-task override (Step 1), KEYWORD-ONLY so it
    can never be threaded in positionally from model-derived args. It is caller authority
    — the propose channel / a UI host — never sourced from model output (panel F4).
    Omitted, behaviour is exactly as before (session/tool default). A host leash may
    tighten OR loosen relative to the tool default (host authority over their own leash),
    but it can never widen the capability gate, and an invalid value fails closed."""
    tool = get_tool(intent.name)
    if tool is None:
        return Decision(action_id="", tool=intent.name, status=UNKNOWN_TOOL,
                        reason=f"no such tool: {intent.name}", leash="", args=intent.args)

    action_id = "act-" + uuid.uuid4().hex[:16]
    leash = _resolve_leash(session, tool, leash)
    # ③ a signed grant caps how loose the leash may get: the host/view can tighten but
    # never loosen past the cap (fail-closed if the grant is present but invalid).
    leash = apply_cap(leash, leash_cap(session, tool.name))
    # A PROPOSER-originated shell command OR network egress must never AUTO-run: floor it at
    # propose_first so it is always surfaced for a human hand, whatever the host leash config.
    # run_command is the unbounded-reach mutator (writes anywhere, reaches outside the machine,
    # verify_mode="exit" gives it no write-set floor); an egress tool EMITS off the owned domain
    # (ADR 0003 treats even a GET as an exfil channel). Both are the consequential emission the
    # human must gate — a loosened leash config must not let either auto-fire.
    if (getattr(intent, "source", "") == "proposed" and leash == ACT_THEN_REPORT
            and (intent.name == "run_command" or getattr(tool, "egress", False))):
        leash = PROPOSE_FIRST
    imp = session.default_importance if importance is None else importance
    rk = _TOOL_RISK.get(tool.name, 0.3) if risk is None else risk

    # The judgment view's pause control (Step 2): while paused, the agent's action
    # stream is HELD — nothing runs — until the host resumes. Fail-safe (holds, never
    # runs). The host's own explicit approve()/veto of a specific item is unaffected.
    if getattr(session, "paused", False):
        return Decision(action_id=action_id, tool=tool.name, status=PAUSED,
                        reason="session paused by host", leash=leash, args=intent.args)

    # --- interpret (fail closed: any error here denies) ----------------------
    try:
        policy = issue_policy(
            "collab-policy", action_id, granted_capabilities(session),
            10, 1000, 0, 3, "semantic",
            bool(getattr(session, "allow_adaptation", False)), 2, 0.4, False,
            session.policy_key,
        )
        signals = _emit_signals(session, action_id, imp, rk, tool.verify_mode == "artifact")
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

    # --- CAPABILITY (ADR 0003: an egress tool DERIVES its capability from the destination) ---
    # For an egress tool the authority is net.get:<canonical-host>, computed from the request —
    # a "capability = f(intent)" shape the static gate does not have. Canonicalization is
    # load-bearing: the SAME canonical host is the capability key here AND the connect host in
    # egress.fetch, so authorize-one/connect-another cannot diverge. An ineligible URL (not
    # https, userinfo, non-443 port, bad host) yields no capability -> DENY (fail closed).
    if getattr(tool, "egress", False):
        required_cap = egress.required_capability(str(intent.args.get("url") or ""))
        if required_cap is None:
            return Decision(action_id=action_id, tool=tool.name, status=DENIED,
                            reason="ineligible egress URL (need https://, a plain host, no "
                                   "credentials, port 443)", leash=leash, directive=directive,
                            args=intent.args)
    else:
        required_cap = tool.capability

    # --- CAPABILITY GATE (the one core-enforced authority) -------------------
    if not directive.grants_capability(required_cap):
        return Decision(action_id=action_id, tool=tool.name, status=DENIED,
                        reason=f"policy does not grant '{required_cap}'", leash=leash,
                        directive=directive, args=intent.args)

    # --- workspace pre-check for path tools (deny before running) ------------
    if intent.name in ("write_file", "read_file"):
        try:
            resolve_in_workspace(session.workspace, str(intent.args.get("path") or ""))
        except WorkspaceError as exc:
            return Decision(action_id=action_id, tool=tool.name, status=DENIED,
                            reason=str(exc), leash=leash, directive=directive, args=intent.args)

    # --- controlled-location hard-deny for PROPOSER-originated writes ---------
    # Hard-deny-and-stage: the PROPOSER may not self-originate a write into a controlled
    # location (CI/hooks — repo-level authority). Such a proposal is refused so the proposer
    # stages the artifact to reachable scratch instead; the PLACEMENT into the controlled
    # location is a separate action a human approves and the Collaborator executes. Keyed on
    # the proposer origin (intent.source == "proposed", which propose.py hardcodes — the model
    # cannot forge it), so a user-directed or approved placement is deliberately unaffected.
    if (intent.name == "write_file" and getattr(intent, "source", "") == "proposed"
            and is_controlled_location(session.workspace, str(intent.args.get("path") or ""),
                                       tuple(getattr(session, "controlled_paths", ()) or ()))):
        return Decision(action_id=action_id, tool=tool.name, status=DENIED,
                        reason=("controlled location: the proposer must stage to scratch; "
                                "placement here requires explicit approval"),
                        leash=leash, directive=directive, args=intent.args)

    # --- LEASH (second axis) --------------------------------------------------
    offense_flag = flag_offense_shape(intent.name, intent.args)  # audit-only tag, never a deny
    if leash == NOTIFY_ONLY:
        return Decision(action_id=action_id, tool=tool.name, status=NOTIFIED,
                        reason="notify-only leash", leash=leash, directive=directive,
                        args=intent.args, offense_flag=offense_flag)
    if leash == PROPOSE_FIRST:
        return Decision(action_id=action_id, tool=tool.name, status=HELD,
                        reason="propose-first leash: awaiting approval", leash=leash,
                        directive=directive, args=intent.args, offense_flag=offense_flag,
                        preview={"tool": tool.name, "args": intent.args,
                                 "verification_depth": directive.verification_depth})

    # leash == ACT_THEN_REPORT -> run it
    return execute_and_verify(session, tool, directive, action_id, intent.args)


def reauthorized_or_denied(session, tool: Tool, action_id: str, args: dict, leash: str,
                           directive) -> "Decision | None":
    """Re-gate a held action at the MOMENT OF USE (panel F1 / TOCTOU). A held decision —
    especially an originated proposal, which is designed to linger — may have sat while
    the session's capabilities changed. Approval must therefore re-check authority
    against the CURRENT session, not trust the directive minted at origination: the
    capability must STILL be granted, and a path tool must STILL resolve in the
    workspace. Returns a DENIED Decision if authority no longer holds, else None.

    (Salience/verification depth still come from the origination directive; only the
    authority gate is re-derived. Signals are built locally and NOT re-published, so the
    re-check does not duplicate the origination's audit records.)"""
    try:
        policy = issue_policy(
            "collab-policy", action_id, granted_capabilities(session),
            10, 1000, 0, 3, "semantic",
            bool(getattr(session, "allow_adaptation", False)), 2, 0.4, False,
            session.policy_key,
        )
        signals = [
            SalienceSignal("collaborator", action_id, Facet.ATTENTION,
                           float(getattr(session, "default_importance", 0.3)), 1.0, ()),
            SalienceSignal("collaborator", action_id, Facet.RISK,
                           _TOOL_RISK.get(tool.name, 0.3), 1.0, ()),
        ]
        current = interpret(policy, signals, session.policy_key)
    except Exception as exc:  # can't establish current authority -> deny
        return Decision(action_id, tool.name, DENIED,
                        f"re-gate governance error: {type(exc).__name__}", leash,
                        directive=directive, args=args)
    # ADR 0003: an egress tool re-derives its capability from the destination frozen in the
    # held decision, and re-checks the allowlist against CURRENT caps — so a host removed from
    # the signed allowlist between stage and approval is denied at the moment of use (the
    # panel's emission-TOCTOU), and a destination made ineligible fails closed.
    if getattr(tool, "egress", False):
        required_cap = egress.required_capability(str(args.get("url") or ""))
        if required_cap is None:
            return Decision(action_id, tool.name, DENIED,
                            "ineligible egress URL at approval time", leash,
                            directive=directive, args=args)
    else:
        required_cap = tool.capability
    if not current.grants_capability(required_cap):
        return Decision(action_id, tool.name, DENIED,
                        f"capability '{required_cap}' not granted at approval time",
                        leash, directive=directive, args=args)
    if tool.name in ("write_file", "read_file"):
        try:
            resolve_in_workspace(session.workspace, str(args.get("path") or ""))
        except WorkspaceError as exc:
            return Decision(action_id, tool.name, DENIED, str(exc), leash,
                            directive=directive, args=args)
    return None


def execute_and_verify(session, tool: Tool, directive, action_id: str, args: dict) -> Decision:
    """Run a permitted, unleashed action and (for mutating tools) verify the claim
    against the observed world. Used both for act_then_report and for an approved
    propose_first action."""
    # The signed leash cap applies here too (the terminal enforcement point), so the
    # recorded leash is the EFFECTIVE (capped) one and no future caller can reach this
    # path with an un-capped leash (panel: leash cap must hold at the moment of use).
    leash = apply_cap(_leash_for(session, tool), leash_cap(session, tool.name))
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

    # ADR 0003 egress: the mediated client already enforced the transport safety contract
    # (canonical host == connect host, no redirect, IP-pinned + private-range blocked, HTTPS,
    # bounds). "Clearance" here is the channel-integrity record's ok flag — NOT independent
    # world-observation (the client both makes and records the request; same channel). The
    # egress record is carried on the Decision for audit.
    if tool.verify_mode == "egress_log":
        execution = execute_tool(tool, session.workspace, args)
        rec = execution.egress
        ok = bool(execution.result.ok)
        reason = (f"egress {rec.canonical_dest} [{rec.status}]" if ok
                  else (rec.error if rec is not None else "egress failed"))
        return Decision(action_id, tool.name, RAN if ok else FAILED, reason, leash,
                        cleared=ok, result=execution.result, directive=directive, args=args,
                        egress=rec)

    # Command with no declared artifact: clearance is the SUPERVISOR's exit code
    # (its own view of the child, not the tool's self-report) — a nonzero exit
    # can't be narrated as success.
    if tool.verify_mode == "exit":
        offense_flag = flag_offense_shape(tool.name, args)  # audit-only tag, never a deny
        try:
            execution = execute_tool(tool, session.workspace, args)
        except WorkspaceError as exc:
            return Decision(action_id, tool.name, DENIED, str(exc), leash, directive=directive,
                            args=args, offense_flag=offense_flag)
        cleared = bool(execution.result.ok)
        return Decision(action_id, tool.name, RAN if cleared else FAILED,
                        "supervised exit 0" if cleared else f"exit {execution.exit_code}",
                        leash, cleared=cleared, result=execution.result, directive=directive,
                        args=args, offense_flag=offense_flag)

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
    # Stage-4-live: consume the outcome through BOTH learning channels. For an
    # over-cap-risk (RISK_EXCEEDED) action the weight gate refuses to learn it
    # while the memory governor retains it as a non-decaying inhibitor — the
    # already-built disagreement, now fired by a real governed action.
    adaptation = memory = None
    disagreement = False
    learning_error = None
    if getattr(session, "allow_adaptation", False):
        try:
            now_days = float(getattr(session, "now_days", 0.0) or 0.0)
            adaptation, memory = consume(outcome, now_days)
            disagreement = ((not adaptation.nominated) and adaptation.handoff is not None
                            and bool(memory.inhibitor))
        except Exception as exc:
            # The memory gate RAISES rather than lose an inhibitor (fail-CLOSED on a
            # warning). Surface that — never silently equate "consume failed" with
            # "no disagreement", which would re-introduce the fail-open the gate
            # exists to prevent (panel HIGH). The action's own report is unaffected.
            learning_error = f"{type(exc).__name__}: {exc}"
            adaptation = memory = None
            disagreement = False
    return Decision(action_id, tool.name, RAN if cleared else FAILED,
                    "verified" if cleared else "not verified", leash, cleared=cleared,
                    result=execution.result, directive=directive, outcome=outcome, args=args,
                    adaptation=adaptation, memory=memory, disagreement=disagreement,
                    learning_error=learning_error)
