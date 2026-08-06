"""The weight-adaptation channel consumer (the "adaptation gate").

A CONSUMER of the control seam's decision, never a re-decider (Finding D): the
ONLY nomination predicate is `bool(outcome.adaptation_allowed)` — every other
field on the decision is explanatory. The gate never reads `verdict.status`,
never touches raw salience, and never compares identities (binding was decided
once, in `decide()`).

The ceiling is CANDIDATE: a nomination is an entry into OFFLINE review, never a
promotion — `AdaptationDecision` has no promote/apply surface at all
(structural, schema-pinned by test). "Unverified novelty is excluded"
(docx §13.2) falls out of consuming the seam: an eligibility the world never
VERIFIED arrives here as `adaptation_allowed=False`.

The one thing this gate ORIGINATES is the inhibitor hand-off: when the
RECORDED rationale is RISK_EXCEEDED (an asserted over-cap risk, stamped by the
interpreter — not re-derived here), the reject is packaged as an
`InhibitorHandoff` for the memory channel (docx §4.4). RISK_UNKNOWN is
deliberately NOT a trigger: ignorance is not an incident.
"""

from dataclasses import dataclass, field

from salienceos.consumers.handoff import HANDOFF_SOURCE_RISK_REJECT, InhibitorHandoff
from salienceos.control import GovernedOutcome
from salienceos.interpreter import AdaptationRationale

ADAPTATION_GATE_VERSION = "adaptation-gate/0.1.0"


@dataclass(frozen=True)
class AdaptationDecision:
    """The weight channel's record: a nomination for offline review, or a
    refusal (optionally carrying the inhibitor hand-off). No promote/apply
    field exists — the schema IS the ceiling."""

    subject: str
    nominated: bool                        # nomination for OFFLINE review — never promotion
    rationale: AdaptationRationale | None  # the RECORDED rationale; None when invalid/unbound
    handoff: InhibitorHandoff | None       # None except on the risk-reject path
    gate_version: str
    reasons: tuple = field(default=())


def nominate(outcome) -> AdaptationDecision:
    """Consume a governed outcome into the weight channel's decision.

    Pure and total over well-typed input: deny-shaped outcomes produce refusal
    records (a denial is an auditable event), never raises for them. Raises
    TypeError only on the type fence (the bus.publish precedent).
    """
    if type(outcome) is not GovernedOutcome:
        raise TypeError("nominate accepts only a GovernedOutcome")

    d = outcome.directive
    if d is None or not outcome.subject:
        # The seam withheld identity (unbound or invalid inputs): act on nothing.
        return AdaptationDecision(
            subject="", nominated=False, rationale=None, handoff=None,
            gate_version=ADAPTATION_GATE_VERSION,
            reasons=("invalid_or_unbound_outcome",),
        )

    if outcome.adaptation_allowed:
        # THE only true path. adaptation_allowed already encodes: cleared AND
        # directive CANDIDATE AND world-VERIFIED (seven conditions across two
        # components) — nothing is re-checked here.
        return AdaptationDecision(
            subject=outcome.subject, nominated=True,
            rationale=d.adaptation_rationale, handoff=None,
            gate_version=ADAPTATION_GATE_VERSION,
            reasons=("nominated_for_offline_review",),
        )

    rationale = d.adaptation_rationale
    if not isinstance(rationale, AdaptationRationale):
        # Belt on top of the seam's boundary check (_valid_directive rejects
        # this upstream): a malformed rationale refuses, never crashes — a
        # crash is not a deny.
        return AdaptationDecision(
            subject=outcome.subject, nominated=False, rationale=None,
            handoff=None, gate_version=ADAPTATION_GATE_VERSION,
            reasons=("invalid_rationale",),
        )
    handoff = None
    if rationale is AdaptationRationale.RISK_EXCEEDED:
        # The recorded incident: asserted over-cap risk. Preserve, don't learn.
        handoff = InhibitorHandoff(
            subject=outcome.subject,
            source=HANDOFF_SOURCE_RISK_REJECT,
            rationale=rationale.value,
        )
        reasons = ("risk_reject_handoff",)
    elif rationale is AdaptationRationale.ELIGIBLE:
        # Eligible by salience/policy, yet not allowed: the seam denied on
        # verification/clearance grounds. Named without reading verdict.status.
        reasons = ("unverified_novelty_excluded",)
    else:
        reasons = (rationale.value,)

    return AdaptationDecision(
        subject=outcome.subject, nominated=False, rationale=rationale,
        handoff=handoff, gate_version=ADAPTATION_GATE_VERSION, reasons=reasons,
    )
