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

from collaborator.governance import (
    HELD,
    Decision,
    execute_and_verify,
    govern_action,
    reauthorized_or_denied,
)
from collaborator.toolcall import parse_message
from collaborator.tools import get_tool

COLLABORATOR_LOOP_VERSION = "0.1.0"


@dataclass
class TurnResult:
    reply: str
    decisions: list = field(default_factory=list)
    history: list = field(default_factory=list)
    ambiguous: list = field(default_factory=list)
    stopped: str = "final"  # "final" | "held" | "max_iterations"


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
    tool = get_tool(decision.tool)
    if tool is None:
        return decision
    denied = reauthorized_or_denied(session, tool, decision.action_id, decision.args,
                                    decision.leash, decision.directive)
    if denied is not None:
        return denied
    return execute_and_verify(session, tool, decision.directive, decision.action_id, decision.args)
