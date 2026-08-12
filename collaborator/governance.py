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
from salienceos.verifier.observers import observe_action, observed_write_set, snapshot_tree

from collaborator import egress, provenance
from collaborator.codefence import code_protection_available, names_code_root
from collaborator.sensitivepaths import names_sensitive_path
from collaborator.contained import SHELL_CONTAINED_AUTONOMY_CAP
from collaborator.netns import SHELL_RAW_NETWORK_CAP, netns_available
from collaborator.tools import (
    ACT_THEN_REPORT,
    NOTIFY_ONLY,
    PROPOSE_FIRST,
    Tool,
    ToolResult,
    WorkspaceError,
    execute_tool,
    flag_offense_shape,
    freeze_args,
    get_tool,
    held_action_seal,
    is_controlled_location,
    resolve_in_workspace,
)
from collaborator.policycaps import (
    apply_cap,
    enforced,
    granted_capabilities,
    leash_cap,
    workspace_subject,
)
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
# net_post is the least-reversible tool (an emission cannot be recalled and cannot be verified),
# so it scores the HIGHEST — never below run_command (red-team minor: an unlisted net_post defaulted
# to 0.3, i.e. less risky than run_command, understating the scrutiny/verification-depth it drives).
_TOOL_RISK = {"read_file": 0.0, "write_file": 0.1, "web_fetch": 0.2, "run_command": 0.5,
              "net_post": 0.6}


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
    # The workspace SUBJECT of the session that HELD this decision (red-team #5). A held emission
    # carries an injected credential at approve time; approving it under a DIFFERENT session would
    # silently send that session's credential + this payload. approve() refuses a cross-subject
    # approval when this is set, binding a held decision to the session that created it. Empty for a
    # decision built outside govern_action (then no cross-subject check applies).
    origin_subject: str = ""
    # Single-use guard for a HELD decision (red-team: a pooled/held decision was re-runnable
    # via approve()). Set True the moment approve() runs it OR a veto retires it, so no path —
    # a second approve(), or approve() on a vetoed decision — can re-execute it or reuse its
    # action_id. A DENIED re-gate does NOT consume it (it stays retryable once authority holds).
    consumed: bool = False
    # ADR 0003: the channel-integrity egress record for a net.get (audit only), and an
    # AUDIT-ONLY offense-shape tag (never a deny — the boundary is structural default-deny).
    egress: object = None
    # ADR 0003 #1b: the INDEPENDENT-vantage reconcile verdict for an egress action (tri-state, honest):
    # True = the strong (race-free) kernel vantage confirmed every observed destination is accounted for
    # by the record ("hands didn't lie"); False = a discrepancy was OBSERVED (a dest the record can't
    # explain — a second in-process client / wrong dest); None = unchecked (no vantage, or the racy /proc
    # fallback saw nothing — never a false "verified"). EVIDENCE, not a gate (P-01): it flags, never denies.
    egress_reconciled: "bool | None" = None
    egress_discrepancy: str = ""   # the observed-vs-claimed note when egress_reconciled is False
    offense_flag: str = ""
    # ADR 0003 revisit #1: whether a run_command ran network-isolated (netns). None for tools
    # that are not network-isolable; True/False honestly reflects whether isolation applied.
    network_isolated: "bool | None" = None
    # F-6 Harm A: whether a run_command ran with its write-reach to the Collaborator's OWN code
    # structurally prevented (None = not a code-reaching tool; False until OS-level protection ships).
    code_protected: "bool | None" = None
    # F-6 Harm A audit-only tag: the run_command's command literally named a protected code root —
    # a POROUS recognizer, never a deny; the boundary is structural (see codefence.names_code_root).
    code_touch: str = ""
    # PR 1a Harm B audit-only tag: the run_command literally named an operator sensitive host path
    # (SSH/cloud/OS creds) — POROUS, never a deny. Unlike code_touch there is NO structural boundary
    # here (the operator keeps full FS reach for an approved shell; see sensitivepaths).
    secret_touch: str = ""
    # ADR 0003 residual sweep (F2 dropper) audit-only tag: the run_command's argv references a workspace
    # file an AUTONOMOUS (not-human-approved) action authored — surfaced so the human holding the hand
    # sees the provenance BEFORE approving an uncontained run of un-reviewed bytes. POROUS, never a deny
    # (the human keeps full reach by design; see collaborator/provenance.py).
    provenance_touch: str = ""
    # ADR 0003 Tier 2 (panel: approved != sent): a seal over a HELD EMISSION's consequential
    # payload (canonical dest + request target + content-type + body), captured at hold time. An
    # emission has no verifier, so approval re-checks this seal and REFUSES if the held args were
    # mutated after the human saw them (a shared-by-reference dict). Empty for non-emissions.
    seal: str = ""

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
        if self.network_isolated is not None:  # ADR 0003 revisit #1 — honest per-run flag
            tail += "  🔒 net-isolated" if self.network_isolated else "  ⚠ net NOT isolated (raw reach)"
        if self.code_protected is not None:  # F-6 Harm A — honest per-run flag
            tail += "  🔒 code-protected" if self.code_protected else "  ⚠ code NOT protected (unfenced shell)"
        if self.code_touch:  # F-6 Harm A — audit-only tag (never a deny)
            tail += f"  ⚑ code-touch audit: {self.code_touch}"
        if self.secret_touch:  # PR 1a Harm B — audit-only tag (never a deny)
            tail += f"  ⚑ secret-touch audit: {self.secret_touch}"
        if self.provenance_touch:  # F2 dropper — audit-only tag (never a deny)
            tail += f"  ⚠ autonomy-authored file referenced (not reviewed by you): {self.provenance_touch}"
        if self.egress_reconciled is False:  # ADR 0003 #1b — an OBSERVED discrepancy (never a deny; evidence)
            tail += f"  ⚠ EGRESS DISCREPANCY (independently observed): {self.egress_discrepancy}"
        elif self.egress_reconciled is True:
            tail += "  🔒 egress world-observed (reconciled)"
        if self.status == RAN:
            out = (self.result.output if self.result else "") or "(no output)"
            # An egress action's honesty depends on the INDEPENDENT observer (ADR 0003 #1b): world-observed
            # + reconciled is the "hands can't lie" upgrade; a discrepancy is surfaced loudly; without a
            # vantage it is only channel-logged (the mediated client both makes AND records the request).
            # Never claim verification it doesn't have — this line is fed back into the model's history.
            if self.egress is None:
                claim = "ran, verified"
            elif self.egress_reconciled is True:
                claim = "ran, egress world-observed (reconciled)"
            elif self.egress_reconciled is False:
                claim = "ran, EGRESS DISCREPANCY independently observed"
            else:
                claim = "ran, channel-logged (independent egress observation unavailable here)"
            return f"[{self.tool} ✓ {claim}] {out}{tail}"
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


def _subject(session) -> str:
    """The workspace subject binding a held decision to its session (red-team #5). Empty if it
    can't be resolved — then no cross-subject approval check applies (fail-safe, never raises)."""
    try:
        return workspace_subject(getattr(session, "workspace", ""))
    except Exception:  # noqa: BLE001 — can't establish subject -> no binding
        return ""


def _references_autonomous(session, command) -> str:
    """POROUS F2 recognizer: does ``command`` reference a workspace file an autonomous action authored?
    getattr-guarded (a legacy/lightweight session with no manifest simply returns ""), total — a
    provenance recognizer must never break govern_action/approve (mirrors names_code_root's stance)."""
    authored = getattr(session, "_autonomous_authored", None)
    if not authored:
        return ""
    try:
        return provenance.references_autonomous_file(
            command, authored, getattr(session, "workspace", None))
    except Exception:  # noqa: BLE001 — advisory only; any failure fails closed to "no tag"
        return ""


def _record_autonomous_authorship(session, rel_paths) -> None:
    """Best-effort: record the workspace files an autonomous run authored (F2). getattr-guarded +
    total — recording is audit, never allowed to break the action that just ran."""
    note = getattr(session, "note_autonomous_authorship", None)
    if callable(note) and rel_paths:
        try:
            note(rel_paths)
        except Exception:  # noqa: BLE001
            pass


def _clear_autonomous_authorship(session, rel_paths) -> None:
    """Best-effort: a HUMAN-approved (re-)write / run vets those bytes, so drop any stale autonomy taint."""
    clear = getattr(session, "clear_autonomous_authorship", None)
    if callable(clear) and rel_paths:
        try:
            clear(rel_paths)
        except Exception:  # noqa: BLE001
            pass


def _mark_tracking_incomplete(session) -> None:
    """HONEST fail-flag (external panel, F2): an autonomous run whose write-set we could NOT observe
    means we can't enumerate its drops — so surface DEGRADED tracking to the human rather than
    fail-silent (under-record while implying full coverage). Best-effort + total."""
    try:
        setattr(session, "_autonomous_tracking_incomplete", True)
    except Exception:  # noqa: BLE001
        pass


def _leash_for(session, tool: Tool) -> str:
    overrides = getattr(session, "leash_overrides", None) or {}
    leash = overrides.get(tool.name, tool.default_leash)
    # Fail CLOSED on an unrecognised override (Session validates at construction, but a runtime
    # mutation or an odd construction path must never leak an unknown string that runs — red-team F0).
    return leash if leash in _VALID_LEASHES else PROPOSE_FIRST


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
    # The KEYWORD ``leash`` arg is CALLER authority (a host/UI directing this specific call) and is
    # never threaded from model output (panel F4). Capture whether the host is explicitly directing
    # THIS call to run autonomously BEFORE it is resolved/capped — it is the only positive
    # "the operator asked for this emission" signal the emission auto-lift will accept (red-team F1:
    # intent.source is a parse-channel label, NOT provenance, so it cannot stand in for it).
    host_directed_atr = (leash == ACT_THEN_REPORT)
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
        # Method-aware (ADR 0003): net.get:<host> for a read, net.post:<host> for an emission —
        # separate namespaces, so a read grant never confers emit authority to the same host.
        required_cap = egress.required_capability(
            str(intent.args.get("url") or ""), getattr(tool, "egress_method", "GET"))
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

    # --- code-root hard-deny for PROPOSER-originated run_command (F-6 Harm A) -
    # A proposer must NEVER self-originate a shell command targeting the Collaborator's own code
    # (governance/verifier/…) — the rulebook-rewrite path. Hard-denied outright (like the
    # controlled-location deny above), not merely floored, because there is no legitimate
    # proposer-authored reason to rewrite the rules it runs under. Keyed on the model-unforgeable
    # intent.source == "proposed". POROUS (names_code_root is a literal recognizer, NOT the
    # boundary) — defence-in-depth over the deferred OS-level structural protection.
    if (intent.name == "run_command" and getattr(intent, "source", "") == "proposed"
            and names_code_root(intent.args.get("command"))):
        return Decision(action_id=action_id, tool=tool.name, status=DENIED,
                        reason=("the proposer may not self-originate a command targeting the "
                                "Collaborator's own code"),
                        leash=leash, directive=directive, args=intent.args)

    # --- sensitive-host-path hard-deny for PROPOSER-originated run_command (PR 1a Harm B) ------
    # A proposer must never SELF-ORIGINATE a shell command naming the OPERATOR's sensitive host paths
    # (SSH/cloud/OS creds). Unlike the code-root deny there is SOMETIMES a legit reason to touch these
    # (e.g. `chmod 600 ~/.ssh/id_rsa` hardening) — but that is the HUMAN's to DIRECT, not the model's
    # to self-originate: a one-click-approvable command that reads the operator's private keys is the
    # confused-deputy surface this refuses (an adversarial / memory-poisoned proposer). DENY outright
    # (operator-confirmed), keyed on the model-unforgeable intent.source == "proposed". SCOPE (owned,
    # not hidden): ONLY the fully-autonomous PROPOSER channel is hard-denied. A USER/HOST-directed
    # command AND the model's in-turn channel (source in {structured, content_*}) are deliberately
    # UNAFFECTED here — byte-identical to the code-root deny's scope above. On the turn channel a
    # secret-touching command is not denied but HELD with the ⚠ (names_sensitive_path) in the preview,
    # and the run_command AUTONOMY-WITHHELD FLOOR below guarantees it can never AUTO-run — so the human
    # hand + the ⚠ are the mitigation there (maintenance preserved; the deceived-human approve is the
    # acknowledged residual). Placed after the code-root deny so a command naming BOTH surfaces the
    # higher-severity code reason first. POROUS DiD — NO structural boundary here and none planned.
    if (intent.name == "run_command" and getattr(intent, "source", "") == "proposed"
            and names_sensitive_path(intent.args.get("command"))):
        return Decision(action_id=action_id, tool=tool.name, status=DENIED,
                        reason=("the proposer may not self-originate a command naming the operator's "
                                "sensitive host paths (SSH/cloud/OS credentials); a human must direct it"),
                        leash=leash, directive=directive, args=intent.args)

    # --- EMISSION FLOOR / auto-lift (ADR 0003 Tier 2) ------------------------
    # A side-effecting egress (net_post) is "acting on the world" — the less-reversible,
    # un-verifiable channel — so it is HUMAN-GATED by default. It may run autonomously
    # (act_then_report) ONLY when ALL of these positive, non-model-reachable conditions hold:
    #   1. a SIGNED grant governs the session (enforced) — never mutable legacy caps (F5);
    #   2. that grant names THIS exact host for autonomous emission (net.post.auto:<host>);
    #   3. the emit() ENTRY POINT is directing THIS specific call — BOTH the keyword
    #      leash=act_then_report AND source=="host" (emit() sets both; run_turn/the parser set
    #      NEITHER, and the parser can only ever stamp structured/content_block/content_json). These
    #      are TWO independent, non-model-reachable barriers (red-team F-5 defence-in-depth): even in
    #      a power-user config where a signed leash-cap satisfies cond-4, a model tool-call is still
    #      gated on both. F1: the model can NEVER self-originate an autonomous emission;
    #   4. the per-tool net_post leash-cap ALREADY permits act_then_report — i.e. `leash` is STILL
    #      ACT_THEN_REPORT after the signed-leash-cap application above. This is the "require BOTH
    #      signed signals" rule (Josh's steer): the per-host auto grant AND an explicit
    #      net_post act_then_report leash-cap must AGREE. It also keeps this gate consistent with
    #      the terminal re-cap in execute_and_verify (which re-applies the same leash_cap): the
    #      two must never disagree on an unlisted tool, or an auto-lift here would silently become
    #      a notify-only no-op there (red-team MINOR-A). An unlisted net_post is already capped to
    #      notify_only here (leash_cap defaults unlisted→strictest under enforcement), so it can
    #      never be lifted; an explicit net_post: propose_first (or tighter) also blocks autonomy.
    # Absent any condition -> FLOOR to propose_first (never below the leash already computed); an
    # explicit host tighten (e.g. notify_only) survives, since it is stricter than the floor (F2).
    emit_host = None
    auto_host = False   # net.post.auto:<host> is granted for THIS emission's destination
    if getattr(tool, "egress", False) and tool.mutating:
        emit_host = egress.canonical_host(str(intent.args.get("url") or ""))
        auto_host = (emit_host is not None
                     and directive.grants_capability(egress.EGRESS_AUTO_PREFIX + emit_host))
        auto = (auto_host and host_directed_atr and enforced(session)
                and getattr(intent, "source", "") == "host"
                and leash == ACT_THEN_REPORT)
        if not auto:
            leash = apply_cap(leash, PROPOSE_FIRST)

    # --- run_command AUTONOMY-WITHHELD FLOOR — "protection earns autonomy" (F-6 / ADR 0003) --
    # run_command has NO filesystem fence: an autonomous shell could overwrite the Collaborator's own
    # code and neuter F1. An act_then_report run_command may AUTO-run ONLY when BOTH hold: (a) its
    # write-reach to the code is STRUCTURALLY prevented AND VERIFIABLE on this host (bwrap containment
    # of PROTECTED_ROOTS — code_protection_available() is now a real probe), AND (b) the operator has
    # SIGNED for it (shell.contained_autonomy — enforced + a signed grant, never mutable
    # session.capabilities; the same F5 rule shell.raw_network/net.post.auto follow). Verified
    # containment is necessary but NOT sufficient — the signed cap keeps the single largest autonomy
    # expansion operator-ratcheted, never lifted by the mere presence of bwrap on a host. Absent EITHER,
    # WITHHOLD autonomy: floor to propose_first (a human hand). An INDEPENDENT, monotonic floor,
    # ANDed with the emission + isolation-earns-autonomy floors (stricter always wins, no shared mutable
    # state); an explicit host tighten (notify_only) survives, being stricter than the floor.
    if (intent.name == "run_command" and leash == ACT_THEN_REPORT
            and not (code_protection_available()
                     and enforced(session)
                     and directive.grants_capability(SHELL_CONTAINED_AUTONOMY_CAP))):
        leash = apply_cap(leash, PROPOSE_FIRST)

    # --- run_command ISOLATION-EARNS-AUTONOMY FLOOR (ADR 0003 revisit #1a) ----
    # run_command's raw network reach is isolated (netns) ONLY on Linux with VERIFIED netns. Where
    # isolation is unavailable/unverifiable, an UNATTENDED shell would reach the network raw — so the
    # autonomy that isolation would earn is WITHHELD: an act_then_report run_command floors to a human
    # hand unless a signed, default-deny opt-in (shell.raw_network) explicitly accepts raw reach on
    # this host. An INDEPENDENT, monotonic floor ORTHOGONAL to the code floor above (code protection
    # != network isolation — they gate different reach), composing by ANDing (stricter always wins,
    # no shared mutable state). NOTE: while the code floor withholds run_command autonomy
    # unconditionally (OS code-protection deferred -> code_protection_available() is False), this
    # floor is BEHAVIOR-MASKED — it is the pre-wired second axis that becomes live the moment code
    # protection lands and lifts the code floor on a capable host. Tested in isolation by patching
    # code_protection_available()=True.
    # The raw-reach opt-in must be a SIGNED grant (enforced), never mutable session.capabilities —
    # the same F5 rule the emission auto-lift follows: the highest-stakes "let this run raw
    # unattended" signal can't rest on a legacy attribute a runtime widen could flip (red-team F1).
    if (intent.name == "run_command" and leash == ACT_THEN_REPORT
            and not netns_available()
            and not (enforced(session) and directive.grants_capability(SHELL_RAW_NETWORK_CAP))):
        leash = apply_cap(leash, PROPOSE_FIRST)

    # --- LEASH (second axis) — ALLOWLIST dispatch (red-team F0) ---------------
    # Run ONLY on the explicit act_then_report; notify_only notifies; EVERYTHING ELSE (propose_first
    # and any unrecognised string) fails CLOSED to HELD. The old denylist ran on the `else` branch,
    # so a typo'd leash slipped through and ran autonomously.
    offense_flag = flag_offense_shape(intent.name, intent.args)  # audit-only tag, never a deny
    if leash == ACT_THEN_REPORT:
        return execute_and_verify(session, tool, directive, action_id, intent.args,
                                  leash=leash, human_gated=False)
    if leash == NOTIFY_ONLY:
        # Loud MINOR-A hint — but nudge the OPERATOR to add the missing signal ONLY when the OPERATOR
        # is directing THIS emission (host_directed_atr) under an ENFORCED session with the auto grant.
        # NOT on a model-originated intent (red-team F-3: model output must never prompt the operator
        # to loosen a leash-cap for a model-chosen host — an in-band outbound-influence path), NOT on
        # an unsigned session where autonomy is structurally unreachable (F-2b regression), and worded
        # to be TRUE whether net_post is unlisted OR explicitly capped stricter — "not capped at
        # act_then_report", never the false "has no leash-cap" (F-2a: leash_cap can't tell the two
        # apart, and an explicit notify_only is a deliberate deny, not an omission).
        reason = "notify-only leash"
        if host_directed_atr and enforced(session) and auto_host:
            reason = (f"notify-only: net.post.auto:{emit_host} is granted but net_post is not capped "
                      "at act_then_report — autonomous emission requires BOTH signals")
        return Decision(action_id=action_id, tool=tool.name, status=NOTIFIED,
                        reason=reason, leash=leash, directive=directive,
                        args=intent.args, offense_flag=offense_flag)
    # PROPOSE_FIRST or any unrecognised leash -> HELD (awaiting a human hand).
    # FREEZE the consequential payload ONCE now, so the args the human is shown, the args the seal
    # digests, and the args the executor runs are ONE immutable mapping — a shared-by-reference list
    # command (or bytes-like body) can't be swapped between the human's view/approval and the run
    # (red-team: approved != executed via an in-place list mutation). approved==executed is then
    # STRUCTURAL; the seal below is the belt that also catches a whole-key REASSIGNMENT.
    args = freeze_args(intent.args)
    # Seal a HELD action's consequential payload — approval re-checks it so a mutation cannot change
    # what actually gets sent/run. For an EMISSION the seal is mandatory (Tier 2 has no verifier);
    # for run_command / write_file it is the approved==executed binding (their verifiers re-derive
    # from the same args, so they do NOT catch substitution). Other tools -> "" (nothing to bind).
    if getattr(tool, "egress", False) and tool.mutating:
        seal = egress.emission_seal(str(args.get("url") or ""), args.get("body"),
                                    str(args.get("content_type") or ""))
    else:
        seal = held_action_seal(tool.name, args)
    # Surface the CANONICAL destination for an egress action, so the one human hand on an emission
    # reads the host bytes actually leave for — not the raw model string, which canonicalization may
    # quietly rewrite (soft hyphen, ideographic dot; transport red-team M6). The preview shows the
    # FROZEN args, so what the human reads is exactly what a later approval will run.
    preview = {"tool": tool.name, "args": args,
               "verification_depth": directive.verification_depth}
    if getattr(tool, "egress", False):
        preview["canonical_dest"] = egress.canonical_host(str(args.get("url") or ""))
    if intent.name == "run_command":
        # Surface the honest posture to the human holding the hand (F-6). A HELD run_command, once
        # APPROVED, runs on the HUMAN path (human_gated) which is UNCONTAINED BY DESIGN — full filesystem
        # reach for maintenance — REGARDLESS of whether this host CAN contain (that only earns AUTONOMY, a
        # different, un-held path). So the run the human is approving is NOT code-protected: say so, never
        # imply a fence the approved run won't have (else code_protection_available() going True on a
        # capable host would print 🔒 over a full-reach run).
        preview["code_protected"] = False
        preview["reach"] = ("full filesystem — a human-approved run_command is deliberately uncontained "
                            "(maintenance); containment applies only to the autonomy-earning path")
        _named = names_code_root(args.get("command"))
        if _named:
            preview["names_code_root"] = _named
        # PR 1a Harm B: surface any literal reference to an operator sensitive host path to the human
        # holding the hand on this shell. POROUS/audit-grade — there is no fence here (see docstring).
        _sensitive = names_sensitive_path(args.get("command"))
        if _sensitive:
            preview["names_sensitive_path"] = _sensitive
        # ADR 0003 revisit #1a: whether this shell will run with RAW network reach (no netns
        # isolation on this host) — the honest posture the approving human sees. LIVE off-Linux
        # (netns_available() is False), independent of whether the raw-reach opt-in was granted:
        # the reach is raw either way; the opt-in only governs whether it may AUTO-run.
        if not netns_available():
            preview["raw_network"] = True
        # ADR 0003 residual sweep (F2 dropper): if this command references a workspace file an
        # AUTONOMOUS run authored, surface it to the human BEFORE they approve an UNCONTAINED run of
        # bytes they never reviewed. POROUS/advisory (never a deny) — the human keeps full reach.
        _authored = _references_autonomous(session, args.get("command"))
        if _authored:
            preview["autonomous_authored"] = _authored
        # HONEST degraded-tracking signal (external panel, F2): if some autonomous run's writes could
        # not be observed, a MISSING autonomous_authored tag is NOT proof the file is human-authored —
        # say so, so the human isn't falsely assured by the absence of a ⚠.
        if getattr(session, "_autonomous_tracking_incomplete", False):
            preview["provenance_tracking_incomplete"] = True
    reason = ("propose-first leash: awaiting approval" if leash == PROPOSE_FIRST
              else f"unrecognised leash {leash!r} -> held (fail-closed)")
    return Decision(action_id=action_id, tool=tool.name, status=HELD, reason=reason, leash=leash,
                    directive=directive, args=args, offense_flag=offense_flag,
                    provenance_touch=(_authored if intent.name == "run_command" else ""),
                    seal=seal, preview=preview, origin_subject=_subject(session))


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
        required_cap = egress.required_capability(
            str(args.get("url") or ""), getattr(tool, "egress_method", "GET"))
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
    # A signed leash cap tightened to NOTIFY_ONLY between hold and approve means the operator now
    # says "never run this" — refuse rather than execute a held action the CURRENT policy forbids
    # (red-team F3: the re-gate checked capability + workspace but never the leash).
    if apply_cap(leash, leash_cap(session, tool.name)) == NOTIFY_ONLY:
        return Decision(action_id, tool.name, DENIED,
                        "leash tightened to notify_only since hold — refusing to run", leash,
                        directive=directive, args=args)
    return None


def execute_and_verify(session, tool: Tool, directive, action_id: str, args: dict,
                       leash: "str | None" = None, human_gated: bool = False) -> Decision:
    """Run a permitted, unleashed action and (for mutating tools) verify the claim
    against the observed world. Used both for act_then_report and for an approved
    propose_first action. INTERNAL: assumes the capability gate already passed (call
    ``govern_action``/``approve``, not this directly).

    ``leash`` is the EFFECTIVE leash the caller resolved. ``human_gated`` is True only on the
    approve() path — it, not the leash string, decides the emission audit split (a HAND-approved
    emission keeps a bounded body preview; an autonomous one is body-free), so a signed cap that
    tightens the recorded leash can't silently drop the preview (red-team F3)."""
    # The signed leash cap applies here too (the terminal enforcement point), so the recorded
    # leash is the EFFECTIVE (capped) one and no future caller can reach this path with an
    # un-capped leash (panel: leash cap must hold at the moment of use).
    leash = apply_cap(leash if leash is not None else _leash_for(session, tool),
                      leash_cap(session, tool.name))
    # If the effective leash is NOTIFY_ONLY the operator said "never run this" — do NOT execute,
    # whatever path reached here (red-team F3: execute_and_verify used to run regardless of leash).
    if leash == NOTIFY_ONLY:
        return Decision(action_id, tool.name, NOTIFIED,
                        "notify-only leash — not executed", leash, directive=directive, args=args)
    # ADR 0003 revisit #1a + F-6 Harm A — re-assert the run_command autonomy floors at the MOMENT OF
    # USE (defence in depth, red-team F3/N1/N2): govern_action already withholds these, but binding
    # them AT EXECUTION means no path — a direct internal caller, a future refactor — reaches a raw or
    # unprotected autonomous run. Keyed on `not human_gated` (an AUTONOMOUS execution), NOT the leash
    # string, so a propose_first-with-human_gated=False caller can't slip one notch over (N1). A
    # human-approved run is the human's call, unaffected.
    require_isolation = False
    require_code_protection = False
    if tool.name == "run_command" and not human_gated:
        # CODE floor re-asserted at the MOMENT OF USE (defence in depth, N2): an autonomous shell may run
        # only CONTAINED and only where the operator SIGNED for it — verified code protection on this host
        # AND a signed shell.contained_autonomy grant. Bound here (not just govern-time) so no direct
        # internal caller / future refactor reaches an unprotected or un-opted-in autonomous run.
        if not (code_protection_available()
                and enforced(session)
                and directive.grants_capability(SHELL_CONTAINED_AUTONOMY_CAP)):
            return Decision(action_id, tool.name, DENIED,
                            "autonomous shell withheld at execution: contained-autonomy unavailable "
                            "(needs verified code protection + a signed shell.contained_autonomy grant)",
                            leash, directive=directive, args=args)
        # The BINDING flag (ADR 0003:336-338): the executor wraps in bwrap and REFUSES to run if it cannot
        # verifiably contain — the guarantee is bound to the executor's REAL result (execution.code_protected),
        # NOT re-read here, so the belief-vs-behaviour split cannot reopen on the code axis.
        require_code_protection = True
        # NETWORK floor: on the contained path bwrap isolates the network too (unshare_net=require_isolation).
        # Require ACTUAL isolation unless signed-opted-in; bound to the executor's real result (F3).
        require_isolation = not (enforced(session) and directive.grants_capability(SHELL_RAW_NETWORK_CAP))
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
        # ADR 0003 Tier 2: for a side-effecting emission (net_post) thread the HOST-INJECTED
        # credential — looked up from host config by the consented canonical host that already
        # passed the net.post gate, never sourced from model args, never logged — and record a
        # bounded body preview ONLY when human-gated (leash == propose_first); an autonomous
        # emission is body-free (Josh's steer). A GET (web_fetch) has no body and no credential.
        egress_auth = None
        egress_preview = False
        if getattr(tool, "egress", False) and tool.mutating:
            emit_host = egress.canonical_host(str(args.get("url") or ""))
            # ADR 0003 residual sweep — per-DESTINATION emission quota. The caps bound WHERE + HOW BIG an
            # emission is, but nothing bounded HOW MANY; this is the single dispatch point BOTH the
            # autonomous and the human-approved path reach, so check + consume here. Fail closed: an
            # emission over quota does NOT go out (the bytes never leave). getattr-guarded like
            # egress_credentials so a lightweight/legacy session simply has no quota (unchanged behaviour).
            _allowed = getattr(session, "emission_allowed", None)
            if callable(_allowed) and not _allowed(emit_host):
                return Decision(action_id, tool.name, DENIED,
                                f"per-destination emission quota exhausted for {emit_host}", leash,
                                directive=directive, args=args)
            creds = getattr(session, "egress_credentials", None) or {}
            egress_auth = creds.get(emit_host) if emit_host is not None else None
            # A HAND-approved emission keeps a bounded preview; an autonomous one is body-free. Keyed
            # on the FACT of human approval, not the leash string, so a cap can't drop it (F3).
            egress_preview = human_gated
            _consume = getattr(session, "consume_emission", None)
            if callable(_consume):
                _consume(emit_host)   # count one attempt right before the bytes leave
        try:
            execution = execute_tool(tool, session.workspace, args,
                                     egress_preview=egress_preview, egress_auth=egress_auth)
        except Exception as exc:  # noqa: BLE001 — egress must degrade to FAILED, never raise out of
            # govern_action/approve (transport red-team S1 backstop: the mediated client's own input
            # guards are belt; this is suspenders around the whole egress execute, like the artifact branch).
            return Decision(action_id, tool.name, FAILED, f"egress error: {type(exc).__name__}", leash,
                            cleared=False, directive=directive, args=args)
        rec = execution.egress
        if rec is None:  # an egress executor must always attach a record; missing -> FAILED, never a raise
            return Decision(action_id, tool.name, FAILED, "egress produced no record", leash,
                            cleared=False, result=execution.result, directive=directive, args=args)
        ok = bool(execution.result.ok)
        reason = f"egress {rec.canonical_dest} [{rec.status}]" if ok else rec.error
        obs = execution.egress_obs   # ADR 0003 #1b: the independent reconcile verdict (may be None)
        return Decision(action_id, tool.name, RAN if ok else FAILED, reason, leash,
                        cleared=ok, result=execution.result, directive=directive, args=args,
                        egress=rec,
                        egress_reconciled=(obs.reconciled if obs is not None else None),
                        egress_discrepancy=(obs.note if (obs is not None and obs.reconciled is False) else ""))

    # Command with no declared artifact: clearance is the SUPERVISOR's exit code
    # (its own view of the child, not the tool's self-report) — a nonzero exit
    # can't be narrated as success.
    if tool.verify_mode == "exit":
        offense_flag = flag_offense_shape(tool.name, args)  # audit-only tag, never a deny
        code_touch = names_code_root(args.get("command"))   # F-6 audit-only tag (porous), never a deny
        secret_touch = names_sensitive_path(args.get("command"))  # PR 1a audit-only (porous), never a deny
        # F2 dropper — does this command reference an ALREADY-recorded autonomy-authored file? Computed
        # from the manifest BEFORE this run so the tag never reflects a file THIS run is about to write.
        provenance_touch = _references_autonomous(session, args.get("command"))
        # F2 recording — snapshot the workspace ONLY for an AUTONOMOUS contained run (the dropper path).
        # A human maintenance run is trusted + its writes aren't tracked; this is the deferred post-exec
        # write-set tripwire (contained.py:31-38), now built for provenance. Best-effort: a snapshot
        # failure NEVER blocks the run (an advisory tag must not gate a legitimate autonomous action),
        # but it is surfaced HONESTLY (below) rather than silently under-recording.
        _pre = None
        _snap_failed = False
        if require_code_protection:
            try:
                _pre = snapshot_tree(session.workspace)
            except Exception:  # noqa: BLE001
                _pre = None
                _snap_failed = True
        try:
            execution = execute_tool(tool, session.workspace, args, require_isolation=require_isolation,
                                     require_code_protection=require_code_protection)
        except WorkspaceError as exc:
            return Decision(action_id, tool.name, DENIED, str(exc), leash, directive=directive,
                            args=args, offense_flag=offense_flag, code_touch=code_touch,
                            secret_touch=secret_touch, provenance_touch=provenance_touch)
        except Exception as exc:  # noqa: BLE001 — a shell that can't even START (missing binary,
            # unbalanced quotes -> shlex ValueError, NUL in argv) must FAIL honestly, never raise out
            # of approve()/govern_action (which promise never to raise) and never burn the held
            # action_id with no audit record. Mirrors the egress + artifact branches' broad backstop.
            return Decision(action_id, tool.name, FAILED, f"command error: {type(exc).__name__}",
                            leash, cleared=False, directive=directive, args=args,
                            offense_flag=offense_flag, code_touch=code_touch,
                            secret_touch=secret_touch, provenance_touch=provenance_touch)
        # Record what the autonomous run authored (new/changed workspace files) so a later HUMAN run of
        # them carries the ⚠. Diff pre/post; skip pure deletions and directories (a human runs a FILE).
        if require_code_protection:
            if _pre is not None:
                try:
                    _post = snapshot_tree(session.workspace)
                    # Record new/changed FILES (skip pure deletions + directories); exclude the contained
                    # run's own in-fence HOME (`.sandbox-home/`, created by the executor) — those are
                    # sandbox internals (git config, caches), not a deliberate drop a human would run.
                    _authored_now = [p for p in observed_write_set(_pre, _post)
                                     if _post.get(p) not in (None, "dir")
                                     and p != ".sandbox-home" and not p.startswith(".sandbox-home/")]
                    _record_autonomous_authorship(session, _authored_now)
                except Exception:  # noqa: BLE001
                    _snap_failed = True
            if _snap_failed:
                _mark_tracking_incomplete(session)   # fail-HONEST, never fail-silent (external panel)
        # F2 clear — a HUMAN-approved run of an autonomy-authored file is a conscious accept of those
        # EXACT bytes; drop their taint so an unchanged file does not nag on every future approval (a
        # later autonomous re-write re-taints via the diff above). Keyed on human_gated, never the leash.
        if human_gated and provenance_touch:
            _clear_autonomous_authorship(session, provenance_touch.split(","))
        cleared = bool(execution.result.ok)
        if cleared:
            reason = "supervised exit 0"
        elif execution.exit_code is None:
            # No exit code == the command never ran (isolation refusal, empty/invalid argv) — surface
            # the honest executor error in the reason field, not a bare "exit None" (red-team nit).
            reason = execution.result.error or "did not run"
        else:
            reason = f"exit {execution.exit_code}"
        return Decision(action_id, tool.name, RAN if cleared else FAILED, reason,
                        leash, cleared=cleared, result=execution.result, directive=directive,
                        args=args, offense_flag=offense_flag,
                        network_isolated=execution.network_isolated,
                        code_protected=execution.code_protected, code_touch=code_touch,
                        secret_touch=secret_touch, provenance_touch=provenance_touch)

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
    # F2 dropper — track write_file authorship (an autonomous write_file is a workspace dropper too,
    # contained.py:36). RECORD an AUTONOMOUS write whose child reached DISK (execution.result.ok) —
    # NOT gated on full verification, since a partial / hash-divergent write still leaves runnable bytes
    # on disk (external panel: gating record on `cleared` let a written-but-unverified drop slip). CLEAR
    # only on a HUMAN-approved VERIFIED (cleared) write — the strict gate for "these bytes are now
    # human-vetted". Other artifact ops untouched.
    if getattr(tool, "op", "") == "file.write":
        if human_gated:
            if cleared:
                _clear_autonomous_authorship(session, execution.write_set)
        elif execution.result.ok:
            _record_autonomous_authorship(session, execution.write_set)
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
