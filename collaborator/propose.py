"""① The propose channel — the Collaborator brings YOU a governed proposal.

A proposal is an action GOVERNED BUT NOT RUN — exactly a HELD decision (Step 0),
except the Collaborator originates it on its own initiative from context rather than in
reply to your instruction. It reuses the whole seam: the candidate passes the capability
gate + salience + workspace fence + audit (via ``govern_action``) before you ever see it,
and approving runs it through the same ``approve()`` path (where the capability gate
applies again at run time).

The spine: **surfacing a proposal grants NO authority.** The proactivity dial and the
model's self-rated confidence gate only whether a proposal is *shown* — never whether it
*runs*. The worst an eager or adversarial proposer can do is add noise; only your approval
plus the capability gate ever run anything. So confidence is deliberately powerless beyond
surfacing: clamped to [0,1], absent→0.0, and never read again after the gate.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from collaborator.governance import HELD, NOTIFIED, Decision, govern_action
from collaborator.loop import approve
from collaborator.tools import PROPOSE_FIRST
from collaborator.toolcall import ToolIntent

COLLABORATOR_PROPOSE_VERSION = "0.1.0"

# proactivity level -> minimum model confidence to SURFACE; None = dormant (never).
_PROACTIVITY_THRESHOLDS = {"off": None, "conservative": 0.80, "eager": 0.40}

PROPOSED = "proposed"
APPROVED = "approved"
VETOED = "vetoed"

_PROPOSER_SYSTEM = """You are the Collaborator's proposal sense. Given the workspace
context, decide whether there is ONE useful, SAFE next action worth proposing to the
human — something they would likely want done. You do NOT act; you only propose.

The tools and their EXACT arguments (use these keys precisely):
  write_file  {"path": "<relative path in the workspace>", "content": "<the full file text>"}
  read_file   {"path": "<relative path in the workspace>"}
  run_command {"command": ["<program>", "<arg>", ...]}

Reply with ONE JSON object and NOTHING else:
  {"propose": true, "confidence": 0.0-1.0, "rationale": "<one short line why>",
   "action": {"name": "write_file"|"read_file"|"run_command", "arguments": { ... }}}
or, if nothing is clearly worth proposing:
  {"propose": false}

For write_file you MUST include both "path" and the full "content". Only propose actions
confined to the workspace. Be honest about confidence: use a high value (>= 0.8) only when
you are quite sure it is worth the human's attention. Emit exactly one JSON object, no
prose, no code fence."""


@dataclass
class Proposal:
    """A governed-but-not-run action the Collaborator originated for your decision."""

    proposal_id: str
    decision: Decision       # the HELD (or NOTIFIED) decision — capability-checked, audited
    rationale: str           # the model's one-line "why" — prose only, no authority
    confidence: float        # model self-rating in [0,1]; gated surfacing ONLY
    status: str = PROPOSED
    origin: str = "collaborator"

    def summary(self) -> str:
        d = self.decision
        tail = "approve to run" if d.status == HELD else "FYI only (nothing to run)"
        return (f"[proposal {self.confidence:.2f} · {d.leash}] {d.tool}({d.args}) — "
                f"{self.rationale}  ⟨{tail}⟩")


def _clamp01(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    if v != v:  # NaN
        return 0.0
    return 0.0 if v < 0 else (1.0 if v > 1 else v)


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _candidate_from_response(content: str):
    """Parse the proposer's JSON reply into (confidence, rationale, ToolIntent), or None.

    Fail-quiet: anything malformed, ``{"propose": false}``, or an action that is not a
    well-formed {name, arguments-object} yields None — i.e. no proposal. The capability
    gate and workspace fence are NOT re-checked here; that is govern_action's job."""
    try:
        obj = json.loads(_strip_fence(content or ""))
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict) or not obj.get("propose"):
        return None
    action = obj.get("action")
    if not isinstance(action, dict):
        return None
    name = action.get("name") or action.get("tool")
    args = action.get("arguments")
    if args is None:
        args = action.get("args")
    if not isinstance(name, str) or not name or not isinstance(args, dict):
        return None
    return _clamp01(obj.get("confidence")), str(obj.get("rationale") or "")[:200], \
        ToolIntent(name=name, args=args, source="proposed")


def _threshold_for(session, threshold):
    if threshold is not None:
        return threshold
    level = getattr(session, "proactivity", "conservative")
    return _PROACTIVITY_THRESHOLDS.get(level, 0.80)


def propose(session, client, context: str, *, importance=None, leash: str = PROPOSE_FIRST,
            threshold=None) -> "list[Proposal]":
    """Run one proposal pass and return the surfaced proposals (possibly empty).

    Fail-closed everywhere: OFF/None dial → dormant → []; model error → []; malformed or
    absent candidate → []; below the confidence bar → []; a candidate the capability gate
    or workspace fence would DENY → dropped (never surfaced). Nothing here runs an action.
    """
    bar = _threshold_for(session, threshold)
    if bar is None:  # OFF — the channel is dormant
        return []
    try:
        msg = client.complete([{"role": "system", "content": _PROPOSER_SYSTEM},
                               {"role": "user", "content": str(context)}])
    except Exception:  # noqa: BLE001 — proposer/model error must not surface a proposal
        return []
    content = msg.get("content") if isinstance(msg, dict) else str(msg or "")
    parsed = _candidate_from_response(content or "")
    if parsed is None:
        return []
    confidence, rationale, intent = parsed
    if confidence < bar:  # dial not met — a noise control, not a safety one
        return []
    imp = session.default_importance if importance is None else importance
    # Govern the candidate as HELD (forced propose_first) — capability gate, salience,
    # workspace fence, audit — WITHOUT running it. A DENIED candidate (ungranted
    # capability / escaping path) is dropped, never surfaced.
    d = govern_action(session, intent, importance=imp, leash=leash)
    if d.status not in (HELD, NOTIFIED):
        return []
    d.origin = "collaborator"  # provenance: the Collaborator raised this, not the user
    return [Proposal(proposal_id="prop-" + uuid.uuid4().hex[:12], decision=d,
                     rationale=rationale, confidence=confidence)]


def approve_proposal(session, proposal: Proposal) -> Decision:
    """Approve a proposal into existence: run it through the same ``approve()`` path as
    any held action — the capability gate applies again at run time. A NOTIFY_ONLY
    proposal is inert (approve() runs only a HELD decision). Returns the run Decision.

    Only a still-PROPOSED proposal runs: a vetoed one never runs, and an already-approved
    one is not run again (no double execution). In those cases the original held decision
    is returned unchanged (its status is still HELD/NOTIFIED — clearly not RAN)."""
    if proposal.status != PROPOSED:
        return proposal.decision
    proposal.status = APPROVED
    d = approve(session, proposal.decision)
    d.origin = "collaborator"  # the run record keeps the proposal's provenance
    return d


def veto_proposal(session, proposal: Proposal) -> Proposal:
    """Veto a proposal: nothing runs, and it is marked so it cannot later be approved."""
    proposal.status = VETOED
    return proposal
