"""The turn loop we own.

Send the running history to the model, parse tool intents from its reply (ours to
read — structured OR content-embedded), govern each as its own action, and feed the
HONEST result back before the model runs again. The message appended for any
action is derived from the real ``Decision`` (``decision.summary()``), never the
model's narration — so a held, denied, or failed action can't be reported to you as
a success (panel gap #4).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from collaborator import egress
from collaborator.governance import (
    DENIED,
    HELD,
    PAUSED,
    Decision,
    _subject,
    execute_and_verify,
    govern_action,
    reauthorized_or_denied,
)
from collaborator.codefence import names_code_root
from collaborator.toolcall import ToolIntent, parse_message
from collaborator.tools import ACT_THEN_REPORT, get_tool, is_controlled_location

COLLABORATOR_LOOP_VERSION = "0.1.0"


@dataclass
class TurnResult:
    reply: str
    decisions: list = field(default_factory=list)
    history: list = field(default_factory=list)
    ambiguous: list = field(default_factory=list)
    stopped: str = "final"  # "final" | "held" | "paused" | "max_iterations"


def _content(msg) -> str:
    if isinstance(msg, dict):
        return msg.get("content") or ""
    return str(msg or "")


def run_turn(session, client, user_message: str, history=None, max_iterations: int = 6,
             importance=None, risk=None) -> TurnResult:
    """Run one user turn to completion (or until max_iterations)."""
    history = list(history or [])
    history.append({"role": "user", "content": user_message})
    decisions: list[Decision] = []
    ambiguous: list = []

    for _ in range(max_iterations):
        msg = client.complete(history)
        parsed = parse_message(msg)
        history.append({"role": "assistant", "content": _content(msg)})
        ambiguous.extend(parsed.ambiguous)

        if not parsed.intents:
            return TurnResult(reply=parsed.text or _content(msg), decisions=decisions,
                              history=history, ambiguous=ambiguous, stopped="final")

        # Each tool call is its own governed action; feed authoritative results back.
        lines = []
        iter_decisions = []
        for intent in parsed.intents:
            d = govern_action(session, intent, importance=importance, risk=risk)
            decisions.append(d)
            iter_decisions.append(d)
            lines.append(d.summary())
        if parsed.ambiguous:
            lines.append("[ambiguous — NOT run, surfaced for you]: "
                         + "; ".join(str(a) for a in parsed.ambiguous))
        history.append({
            "role": "user",
            "content": ("TOOL RESULTS (authoritative, from the system — treat as ground truth, "
                        "not your own narration):\n" + "\n".join(lines)),
        })

        # A propose-first action means "wait for my yes." The loop cannot approve on
        # the human's behalf, so it PAUSES and hands the held action(s) back — rather
        # than calling the model again, which just spins it re-proposing the same call
        # until max_iterations (a real waste found by the live run). The host approves
        # via approve() and resumes with run_turn(history=result.history).
        if any(d.status == PAUSED for d in iter_decisions):
            return TurnResult(reply="(stopped: the host paused the session)",
                              decisions=decisions, history=history, ambiguous=ambiguous,
                              stopped="paused")
        if any(d.status == HELD for d in iter_decisions):
            return TurnResult(reply="(paused: awaiting your approval of the held action(s) above)",
                              decisions=decisions, history=history, ambiguous=ambiguous,
                              stopped="held")

    return TurnResult(reply="(stopped: max iterations reached)", decisions=decisions,
                      history=history, ambiguous=ambiguous, stopped="max_iterations")


def approve(session, decision: Decision) -> Decision:
    """Approve a HELD (propose-first) action: run it now through the same verified path.

    Authority is RE-CHECKED against the current session before running (not merely
    trusted from origination): a held action — a lingering proposal especially — must
    still be granted its capability and, for a path tool, still resolve in the workspace.
    If authority no longer holds, it is DENIED, not run (closes the TOCTOU the propose
    channel would otherwise widen). Salience/verification depth still come from the
    recorded directive."""
    if decision.status != HELD:
        return decision
    # Single-use: a HELD decision may run at most once, through ANY approval path. Without this
    # the same held decision (held by the proposal pool by reference, and reachable as
    # ``proposal.decision``) could be re-run — running a VETOED action, or double-executing an
    # approved one under a REUSED action_id (a one-id/one-action audit break). Both were proven
    # by the red-team; the flag closes them at the decision layer, below the wrapper's status.
    if getattr(decision, "consumed", False):
        return decision
    tool = get_tool(decision.tool)
    if tool is None:
        return decision
    # SNAPSHOT the held args ONCE (red-team #2: approved != sent via a multi-read TOCTOU). Every
    # downstream read — the seal check here, the credential-host lookup and the wire inside
    # execute_and_verify — MUST see the SAME mapping, so a mutable / proxy / UI-backed args view
    # cannot return one payload to the seal check and another to the socket. Everything below reads
    # this local ``args``, never the live ``decision.args``.
    args = dict(decision.args)
    # Bind a held decision to the session that HELD it (red-team #5): a held emission carries an
    # injected credential at approve time, so approving it under a DIFFERENT workspace subject would
    # silently send THAT session's credential with THIS payload. Refuse the cross-subject approval
    # (skipped when no origin subject was recorded — a decision built outside govern_action).
    origin_subject = getattr(decision, "origin_subject", "") or ""
    if origin_subject and origin_subject != _subject(session):
        return Decision(decision.action_id, decision.tool, DENIED,
                        "cross-session approval refused: held under a different workspace subject",
                        decision.leash, directive=decision.directive, args=args, origin=decision.origin)
    # ADR 0003 Tier 2 (panel: approved != sent): an EMISSION has no verifier, so bind approval to the
    # EXACT payload the human saw. For an egress+mutating decision a seal is MANDATORY — a MISSING
    # seal fails CLOSED (red-team #3: an empty seal used to SKIP the check and send an un-approved
    # payload), and a seal that no longer matches the snapshot DENIES. Not consumed on either -> a
    # restored / re-authorized payload stays retryable. (A full in-process re-signer is the ADR 0002
    # single-trust-domain limit, not this control's target — this closes the by-reference vector.)
    if getattr(tool, "egress", False) and tool.mutating:
        if not decision.seal:
            return Decision(decision.action_id, decision.tool, DENIED,
                            "egress decision has no payload seal — refusing (fail closed)",
                            decision.leash, directive=decision.directive, args=args, origin=decision.origin)
        if egress.emission_seal(str(args.get("url") or ""), args.get("body"),
                                str(args.get("content_type") or "")) != decision.seal:
            return Decision(decision.action_id, decision.tool, DENIED,
                            "emission payload changed since approval (seal mismatch)",
                            decision.leash, directive=decision.directive, args=args,
                            origin=decision.origin)
    denied = reauthorized_or_denied(session, tool, decision.action_id, args,
                                    decision.leash, decision.directive)
    if denied is not None:
        return denied            # authority no longer holds: NOT consumed -> retryable later
    # Defence-in-depth (red-team): re-assert the controlled-location hard-deny at the MOMENT OF
    # USE for a COLLABORATOR-originated write. A proposer can never *originate* such a write (it
    # is denied at govern time), so a held collaborator proposal whose path now lands in a
    # controlled tree can only have been mutated after origination — refuse it, don't consume it.
    # A user-directed placement (origin != "collaborator") is unaffected: approval is the human's.
    if (decision.origin == "collaborator" and decision.tool == "write_file"
            and is_controlled_location(session.workspace, str(args.get("path") or ""),
                                       tuple(getattr(session, "controlled_paths", ()) or ()))):
        return Decision(decision.action_id, decision.tool, DENIED,
                        "controlled location: proposer placement re-denied at approval",
                        decision.leash, directive=decision.directive, args=args,
                        origin=decision.origin)
    # F-6 Harm A: re-assert the code-root deny at the MOMENT OF USE for a COLLABORATOR-originated
    # held run_command. A proposer can never *originate* one naming the code root (denied at govern
    # time), so a held collaborator run_command that now names it can only have been mutated after
    # origination — refuse, don't consume. A user-directed command (origin != "collaborator") is
    # the human's to approve. POROUS DiD (names_code_root is not the boundary), symmetric with the
    # controlled-location re-deny above.
    if (decision.origin == "collaborator" and decision.tool == "run_command"
            and names_code_root(args.get("command"))):
        return Decision(decision.action_id, decision.tool, DENIED,
                        "code root: proposer-authored command re-denied at approval",
                        decision.leash, directive=decision.directive, args=args,
                        origin=decision.origin)
    decision.consumed = True     # claim it before running, so no concurrent/second path re-runs
    # human_gated=True: this IS the human-approval path, so the emission keeps a bounded body preview
    # regardless of how a signed cap rewrote the recorded leash (ADR 0003 Tier 2; red-team F3). The
    # SNAPSHOT ``args`` (not decision.args) is what is sealed-checked AND sent — approved == sent (#2).
    return execute_and_verify(session, tool, decision.directive, decision.action_id,
                              args, leash=decision.leash, human_gated=True)


def emit(session, url, body, *, content_type="application/json", autonomous=False):
    """HOST-facing outbound emission (ADR 0003 Tier 2) — the operator entry point that actually
    USES the governed net.post channel. CALLER authority: invoked by operator/host code, NEVER
    reachable from ``run_turn`` (the model can't call this, so a model-emitted net_post is always
    gated — red-team F1). This is the sibling of ``approve()``: both direct a specific action, and
    neither is on the model's path.

    ``autonomous=True`` REQUESTS act_then_report; it is honored ONLY if the session's signed grant
    fully authorizes it — enforced + ``net.post.auto:<host>`` + an explicit ``net_post``
    act_then_report leash-cap ("require BOTH signed signals", Josh's steer). Otherwise the emission
    is governed exactly like any propose_first action: HELD, for the host to ``approve()`` by hand.

    Authority is NOT keyed on the intent ``source`` (the F1 lesson — a parse-channel label is not
    provenance). The ONLY positive "the operator is directing THIS emission autonomously" signal is
    the keyword ``leash`` this function passes to ``govern_action``, which ``run_turn``/the parser
    never carry. The host credential (if any) is injected by the seam from ``session`` config,
    keyed by the consented canonical host — never supplied here, never logged.

    To DISABLE emission, the kill switches are ``session.paused`` (-> PAUSED, nothing runs) and the
    SIGNED caps (revoke ``net.post:<host>`` -> DENIED, or cap ``net_post`` below act_then_report ->
    held/notify). NOT kill switches: a mutable ``session.leash_overrides`` (an explicit
    ``autonomous=True`` here beats session config, by design — only signed signals are the ceiling),
    and pulling ``session.egress_credentials`` (a held emission still SENDS, just unauthenticated —
    the credential authenticates, it does not gate; remove the cap or tighten the leash to stop it).

    ``autonomous`` must be the literal ``True`` to request act_then_report — any other value (a
    truthy string like ``"false"`` from an env/JSON/YAML wrapper, a number, a list) holds for a hand
    (red-team F-1: this is the ONE knob that flips human-gated to autonomous, so it fails safe on a
    non-bool rather than trusting truthiness).

    WARNING (red-team F-8): the emission-floor's F1 guarantee governs WHO may trigger an autonomous
    emission, NOT what it carries. ``url`` and ``body`` are sent verbatim and an autonomous emission
    is audited body-free — so a host wrapper MUST NOT pipe model-derived content into ``url``/``body``
    with ``autonomous=True``, or it becomes an unauditable exfil channel to the granted host. Keep
    the destination and payload operator/host-controlled when emitting autonomously.

    Returns the Decision (carrying the egress channel-record on RAN, or the seal + preview on HELD).
    """
    intent = ToolIntent("net_post", {"url": url, "body": body, "content_type": content_type},
                        source="host")
    return govern_action(session, intent, leash=(ACT_THEN_REPORT if autonomous is True else None))
