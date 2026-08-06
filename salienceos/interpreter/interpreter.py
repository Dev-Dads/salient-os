"""The central interpreter — the single fail-closed choke point (Finding D).

`interpret()` is the spine: pure (no I/O, no clock, no globals), versioned, and
the primary mutation-test target — the directive analog of the verifier's
`compose()`. Every directive in the system converges here; enforcement lives in
this function, not in any UI ("removing the dashboard must not disable
enforcement").

The load-bearing invariant, enforced by construction (P-01):
  - `allowed_capabilities` is ALWAYS exactly `policy.granted_capabilities`.
    No signal, at any influence, touches it.
  - adaptation reaches CANDIDATE only with an explicit policy switch AND enough
    verification AND low-enough risk; salience alone can never make it eligible,
    and it never reaches "promoted" (no live self-modification).

Fail-closed defaults (safe = cautious):
  - no trustworthy policy  -> hard deny: EMPTY capabilities, min budget, MAX
    verification, no adaptation, ephemeral retention.
  - no informing signal    -> min budget, MAX verification (never under-verify
    when uninformed), ephemeral retention, between-turn reconfiguration.
"""

from salienceos.interpreter.directive import AdaptationRationale, Directive, Reconfigure
from salienceos.interpreter.policy import (
    RETENTION_ORDER,
    AdaptationEligibility,
    VerificationDepth,
    verify_policy,
)
from salienceos.interpreter.signal import Facet, valid_signal

INTERPRETER_VERSION = "interpreter/0.1.0"

# Attention salience must be at least this high (and policy must allow it) before
# reconfiguration is applied immediately rather than deferred to the next turn.
IMMEDIATE_RECONFIGURE_THRESHOLD = 0.9

# The facets that move a knob. Any other facet is recorded but never arbitrated.
KNOWN_FACETS = frozenset({
    Facet.ATTENTION, Facet.VERIFICATION, Facet.RISK,
    Facet.MEMORY, Facet.ROUTING, Facet.ADAPTATION,
})


def interpret(policy, signals, policy_key: bytes) -> Directive:
    """Arbitrate published salience into one directive, bounded by policy.

    `signals` may contain anything; invalid entries are dropped (fail-closed) and
    only signals whose subject matches the policy's subject inform the directive.
    """
    reasons = []

    # Materialize once (a one-shot iterator must not be consumed twice) and
    # degrade to no-signals rather than raising — the choke point must always
    # return a directive, even if a publisher's generator throws mid-stream.
    try:
        signals = tuple(signals)
    except Exception:  # noqa: BLE001 — fail-closed: any read failure => no signals
        signals = ()
        reasons.append("signals_unreadable")

    valid = tuple(s for s in signals if valid_signal(s))
    dropped = len(signals) - len(valid)
    if dropped:
        reasons.append(f"dropped_invalid_signals={dropped}")

    if policy is None or not verify_policy(policy, policy_key):
        return _hard_deny(policy, tuple(reasons) + ("policy_unsigned_or_invalid",))

    subject_signals = tuple(s for s in valid if s.subject == policy.subject)
    agg = _aggregate(subject_signals)  # facet -> confidence-weighted mean influence in [0,1]

    # Compute budget: scaled by attention salience, clamped into the policy window.
    budget = _clamp(
        _scale(agg.get(Facet.ATTENTION, 0.0), policy.min_budget, policy.max_budget),
        policy.min_budget,
        policy.max_budget,
    )

    # Verification depth: from the policy-authorized floor UP toward its ceiling,
    # driven by risk and explicit verification requests. Unknown risk is treated
    # as maximal (absent RISK => 1.0), so an uninformed subject verifies at the
    # ceiling and *lowering* verification requires a positive low-risk assertion
    # — which is still floored at policy.min_verification. Monotonic in risk; no
    # absent-vs-low discontinuity (an "asserted zero risk" can never dip below
    # the policy floor).
    risk = agg.get(Facet.RISK, 1.0)
    verify_request = agg.get(Facet.VERIFICATION, 0.0)
    verif_salience = risk if risk > verify_request else verify_request
    span = policy.max_verification - policy.min_verification
    # Round half UP for verification: at an exact half, bias toward MORE scrutiny
    # (banker's rounding would drop 0.5 -> 0, the less-cautious direction).
    v_depth = _clamp(
        policy.min_verification + _round_half_up(verif_salience * span),
        policy.min_verification,
        policy.max_verification,
    )

    # Retention: salience buys UP to the policy ceiling; default least-durable.
    retention = _retention(agg.get(Facet.MEMORY, 0.0), policy.max_retention)

    # Routing: advisory only — a hint, never authority.
    routing_hint = _routing_hint(subject_signals)

    # Adaptation eligibility: gated by policy switch + applied verification + risk.
    # Salience alone can NEVER make it eligible; it never exceeds CANDIDATE.
    # The priority chain mirrors the original conjunction's condition order, so
    # the eligibility PREDICATE is unchanged — only the recorded reason is
    # refined (a behavior-preserving refactor, pinned by tests).
    adaptation = AdaptationEligibility.NONE
    # `risk` (from the verification block above) is agg.get(RISK, 1.0):
    # unknown risk (absent) => 1.0 => blocked.
    if not policy.allow_adaptation:
        rationale = AdaptationRationale.POLICY_DISALLOWED
    elif not agg.get(Facet.ADAPTATION, 0.0) > 0.0:
        rationale = AdaptationRationale.NOT_REQUESTED
    elif v_depth < policy.adaptation_min_verification:
        rationale = AdaptationRationale.UNDER_VERIFIED
    elif risk > policy.adaptation_max_risk:
        # An ASSERTED over-cap risk is an incident (inhibitor hand-off trigger,
        # consumed downstream); an ABSENT risk signal is mere ignorance and is
        # recorded as such — blocked, but never an inhibitor.
        rationale = (AdaptationRationale.RISK_EXCEEDED if Facet.RISK in agg
                     else AdaptationRationale.RISK_UNKNOWN)
    else:
        adaptation = AdaptationEligibility.CANDIDATE
        rationale = AdaptationRationale.ELIGIBLE

    # Reconfiguration timing: between-turn by default (Finding F).
    reconfigure = Reconfigure.BETWEEN_TURN
    if policy.allow_immediate_reconfigure and agg.get(Facet.ATTENTION, 0.0) >= IMMEDIATE_RECONFIGURE_THRESHOLD:
        reconfigure = Reconfigure.IMMEDIATE

    if not subject_signals:
        reasons.append("no_subject_signals_failclosed_defaults")

    return Directive(
        subject=policy.subject,
        policy_id=policy.policy_id,
        compute_budget=budget,
        verification_depth=v_depth,
        retention_class=retention,
        routing_hint=routing_hint,
        adaptation_eligibility=adaptation,
        adaptation_rationale=rationale,
        allowed_capabilities=tuple(policy.granted_capabilities),  # pass-through ONLY
        reconfigure=reconfigure,
        interpreter_version=INTERPRETER_VERSION,
        reasons=tuple(reasons),
    )


def _hard_deny(policy, reasons) -> Directive:
    """Directive issued when no trustworthy authority envelope exists: empty
    capabilities, minimal compute, maximal verification, no adaptation.

    subject/policy_id are blanked: a policy that failed signature verification is
    untrusted, so its identifiers must not be echoed into the durable deny record
    (an untrusted source could otherwise place chosen identifiers into the audit
    trail)."""
    return Directive(
        subject="",
        policy_id="",
        compute_budget=0,
        verification_depth=int(VerificationDepth.FULL),
        retention_class=RETENTION_ORDER[0],
        routing_hint="",
        adaptation_eligibility=AdaptationEligibility.NONE,
        # No trustworthy policy IS "policy does not allow" (reasons already
        # carry policy_unsigned_or_invalid alongside).
        adaptation_rationale=AdaptationRationale.POLICY_DISALLOWED,
        allowed_capabilities=(),
        reconfigure=Reconfigure.BETWEEN_TURN,
        interpreter_version=INTERPRETER_VERSION,
        reasons=reasons,
    )


def _aggregate(signals) -> dict:
    by_facet = {}
    for s in signals:
        # Only KNOWN facets can move a knob. Unknown facets are still valid
        # signals (recorded on the bus) but are excluded from arbitration here,
        # so "an unrecognized signal grants nothing" is structural, not incidental.
        if s.facet in KNOWN_FACETS:
            by_facet.setdefault(s.facet, []).append(s)
    agg = {}
    for facet, group in by_facet.items():
        weight = sum(s.confidence for s in group)
        # A facet whose signals are ALL zero-confidence carries no information:
        # omit it entirely (treated as absent) rather than inserting 0.0. Inserting
        # 0.0 would invert the absent-RISK default (1.0, cautious) into 0.0
        # (permissive) — a fail-closed inversion.
        if weight > 0:
            agg[facet] = sum(s.influence * s.confidence for s in group) / weight
    return agg


def _routing_hint(signals) -> str:
    best = ""
    best_score = 0.0
    for s in signals:
        if s.facet == Facet.ROUTING:
            score = s.influence * s.confidence
            if score > best_score:
                best_score = score
                best = s.subsystem_id
    return best


def _retention(mem_salience: float, max_retention: str) -> str:
    max_idx = RETENTION_ORDER.index(max_retention) if max_retention in RETENTION_ORDER else 0
    idx = _clamp(_scale(mem_salience, 0, max_idx), 0, max_idx)
    return RETENTION_ORDER[idx]


def _scale(frac: float, lo: int, hi: int) -> int:
    return lo + _round_half_up(frac * (hi - lo))


def _round_half_up(x: float) -> int:
    # Deterministic round-half-up for non-negative x (frac in [0,1], span >= 0),
    # avoiding Python's banker's rounding at exact halves.
    return int(x + 0.5)


def _clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v
