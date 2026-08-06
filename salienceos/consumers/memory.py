"""The memory-retention channel consumer (the "memory governor").

Recall-steering ONLY (Finding C: CDMS pre-registered FALSE on
disposition-changes-behavior — the memory channel steers what is recalled,
never what the system becomes). Structurally enforced by the record itself:
`MemoryRetention` has no delete/tombstone field (deletion is policy's alone,
docx §3.1) and no retrieval-scope field (salience never widens reach). Decay
applies to the DERIVED retention weight, never to any event ledger (§4.5).

Two independent dimensions, deliberately not one ladder:
  - `retention_class`: the salience-bought, policy-capped ladder rung, taken
    from the BOUND directive. Binding — not clearance — selects the class: a
    bound denial retains at the directive's class (a denial is an auditable
    event); only unbound/invalid outcomes floor to ephemeral.
  - `inhibitor`: risk-triggered, arriving ONLY via the weight gate's explicit
    `InhibitorHandoff`. An inhibitor is exempt from automatic decay — the
    pinning primitive (§4.5 inhibitor_catastrophic: no automatic decay).

A hand-off that cannot be attributed to this outcome's RECORDED risk-reject
raises `HandoffMismatchError` (the SealedGateError precedent): silently
dropping an inhibitor is the fail-OPEN direction, and silently accepting a
mis-addressed one would let any caller pin content the weight gate never
rejected.

No clock: `now_days` is an injected parameter (the policy_key precedent);
`time` is not in the discipline allowlist. No `math` either — the docs specify
half-lives directly, and `exp(-λt) ≡ 0.5 ** (t / half_life)`.
"""

from dataclasses import dataclass, field

from salienceos.consumers.handoff import HANDOFF_SOURCE_RISK_REJECT, InhibitorHandoff
from salienceos.control import GovernedOutcome
from salienceos.interpreter import RETENTION_ORDER, AdaptationRationale

MEMORY_GOVERNOR_VERSION = "memory-governor/0.1.0"

# v0 half-lives in days, ladder classes only (docx §4.5 scale; ephemeral and
# working are sub-day by design). Inhibitors are orthogonal and NEVER decay.
HALF_LIFE_DAYS = {
    "ephemeral": 0.02,
    "working": 0.25,
    "episodic": 14.0,
    "semantic": 180.0,
}


class HandoffMismatchError(Exception):
    """An inhibitor hand-off that cannot be attributed to this outcome's
    recorded risk-reject is unrecordable — raise, never misattribute or lose
    an inhibitor (SealedGateError precedent)."""


@dataclass(frozen=True)
class MemoryRetention:
    """The memory channel's record. No delete, no tombstone, no scope, no
    capability field — the schema is pinned by test so none can be added
    casually."""

    subject: str
    retention_class: str      # ladder rung from the BOUND directive (policy-capped upstream)
    inhibitor: bool           # pinned: exempt from automatic decay
    cleared: bool             # the seam's clearance bit, carried so a persisting
                              # consumer can filter on it (inhibitors deliberately
                              # may retain UNcleared content: incident preservation)
    base_weight: float        # 1.0 in v0
    recorded_at_days: float   # injected clock — NO time module in this package
    governor_version: str
    reasons: tuple = field(default=())


def _real_number(x) -> bool:
    # bools are ints; exclude them. (x - x) == 0 is True exactly for finite
    # numbers: nan-nan and inf-inf are both nan.
    return isinstance(x, (int, float)) and not isinstance(x, bool) and (x - x) == 0


def retain(outcome, now_days, handoff=None) -> MemoryRetention:
    """Consume a governed outcome (and optional inhibitor hand-off) into a
    retention record.

    NEVER raises for deny-shaped outcomes — a denial is recorded at the
    fail-closed floor. Raises only on the type fences and on a hand-off that
    fails attribution against the recorded decision.
    """
    if type(outcome) is not GovernedOutcome:
        raise TypeError("retain accepts only a GovernedOutcome")
    if handoff is not None and type(handoff) is not InhibitorHandoff:
        raise TypeError("handoff must be an InhibitorHandoff or None")
    if not _real_number(now_days) or now_days < 0:
        raise TypeError("now_days must be a finite non-negative number")

    d = outcome.directive
    bound = d is not None and bool(outcome.subject)

    if handoff is not None:
        # Attribution against the RECORDED decision (consuming, not
        # re-deriving): the bound directive must actually carry the
        # RISK_EXCEEDED rationale this hand-off claims to deliver.
        attributable = (
            bound
            and handoff.subject == outcome.subject
            and handoff.source == HANDOFF_SOURCE_RISK_REJECT
            and handoff.rationale == AdaptationRationale.RISK_EXCEEDED.value
            and d.adaptation_rationale is AdaptationRationale.RISK_EXCEEDED
        )
        if not attributable:
            raise HandoffMismatchError(
                "inhibitor hand-off cannot be attributed to this outcome's "
                "recorded risk-reject"
            )

    reasons = []
    if bound and d.retention_class in RETENTION_ORDER:
        retention_class = d.retention_class
    else:
        # Floor durability, and say WHICH failure floored it — the audit token
        # must not call a bound record "unbound".
        retention_class = RETENTION_ORDER[0]
        reasons.append("retention_class_off_ladder_floored" if bound
                       else "unbound_or_invalid_retention_floored")

    inhibitor = handoff is not None
    if inhibitor:
        reasons.append("inhibitor:" + handoff.source)

    return MemoryRetention(
        subject=outcome.subject,
        retention_class=retention_class,
        inhibitor=inhibitor,
        cleared=bool(outcome.cleared),
        base_weight=1.0,
        recorded_at_days=float(now_days),
        governor_version=MEMORY_GOVERNOR_VERSION,
        reasons=tuple(reasons),
    )


def effective_weight(retention, now_days, reinforcement_sum=0.0) -> float:
    """Derived retrieval weight at `now_days`: half-life decay by class, plus
    reinforcement. Inhibitors do not decay — that IS the pin.

    Decay touches only this derived number; the retention record (and any
    event ledger behind it) is never modified.

    The "never above base + reinforcement" bound holds for records `retain()`
    produced (base_weight 1.0, finite recorded_at_days). A hand-built
    `MemoryRetention` with fabricated numerics is the caller's problem — the
    same stance the seam takes on hand-forged verdicts.
    """
    if type(retention) is not MemoryRetention:
        raise TypeError("effective_weight accepts only a MemoryRetention")
    if not _real_number(now_days) or now_days < 0:
        raise TypeError("now_days must be a finite non-negative number")
    if not _real_number(reinforcement_sum) or reinforcement_sum < 0:
        # Non-negative by definition: reinforcement is additive support, and a
        # negative term would be a decay-shaped side door that could null an
        # inhibitor's pin through the only weight API.
        raise TypeError("reinforcement_sum must be a finite non-negative number")

    if retention.inhibitor:
        return retention.base_weight + reinforcement_sum

    # Clock-skew clamp: a reader whose clock lags the recorder sees age 0.
    age = now_days - retention.recorded_at_days
    if age < 0:
        age = 0.0
    # Unknown class decays at the least-durable rung (fail-closed), no KeyError.
    half_life = HALF_LIFE_DAYS.get(retention.retention_class,
                                   HALF_LIFE_DAYS[RETENTION_ORDER[0]])
    return retention.base_weight * 0.5 ** (age / half_life) + reinforcement_sum
