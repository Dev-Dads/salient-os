"""The control seam: compose the interpreter's Directive with the verifier's
Verdict into one governed outcome.

This is where the two vocabularies reconcile. The interpreter speaks
`verification_depth` (0-3); the verifier speaks `Stakes` (LOW..CRITICAL, where
HIGH+ demands two independent sources). The seam:

  1. Maps the directive's required depth to an UPWARD-only stakes escalation and
     runs the verifier at max(envelope.stakes, escalation) — salience can demand
     stricter verification than the envelope signed, never weaker (P-01: salience
     influences, policy authorizes; escalation is the allowed "stricter" direction).
  2. Reads the ACHIEVED level back off the verdict + the effective stakes.
  3. Clears the action only if achieved >= required and the verdict is not FAILED.
  4. Allows adaptation only if the directive deemed it eligible AND the verifier
     returned VERIFIED — the sealed-gate analog for learning.

`decide()` is the pure spine (the mutation-test target); `govern()` is the thin
orchestration that also runs the verifier. Both fail closed: a directive/envelope
that refer to different actions, or any non-VERIFIED verdict, deny clearance.
"""

from salienceos.control.outcome import FULL, INDEPENDENT, NONE, RECEIPT, GovernedOutcome
from salienceos.interpreter import AdaptationEligibility, AdaptationRationale, Directive
from salienceos.verifier import Reason, Stakes, Status, Verdict, max_stakes

GOVERNOR_VERSION = "governor/0.1.0"


def escalation_for(required_depth: int):
    """The upward-only stakes floor a required depth implies. Only FULL depth
    (two independent sources) needs HIGH; lesser depths add no floor (None), so
    they never lower a higher envelope stakes."""
    return Stakes.HIGH if required_depth >= FULL else None


def stakes_for(directive: Directive, floor: Stakes) -> Stakes:
    """Authorization-time helper: the stakes to SIGN into an envelope for this
    directive, given a policy floor. The stronger of the floor and the directive's
    implied escalation — so the envelope is grounded at least as strictly as
    salience demands, and never below the floor."""
    return max_stakes(floor, escalation_for(directive.verification_depth))


def _stakes_floor(stakes) -> int:
    """The clearance level a policy-signed envelope stakes demands on its own,
    independent of salience: LOW accepts receipt integrity, NORMAL demands one
    independent fact, HIGH/CRITICAL demand two. Anything that is not a Stakes
    (None, or a malformed/unhashable value from a corrupted verdict) fails closed
    to the strictest floor without raising. HIGH and CRITICAL collapse because the
    verifier distinguishes only one-source vs two-source rigor (see achieved_level)."""
    if not isinstance(stakes, Stakes):
        return FULL
    return {
        Stakes.LOW: RECEIPT,
        Stakes.NORMAL: INDEPENDENT,
        Stakes.HIGH: FULL,
        Stakes.CRITICAL: FULL,
    }[stakes]


def achieved_level(verdict: Verdict, effective_stakes: Stakes) -> int:
    """What the world actually corroborated, on the unified 0-3 scale.

    FAILED is not a level — it is a conclusive disproof handled separately. A
    VERIFIED verdict achieved two independent sources iff it ran at HIGH/CRITICAL
    (FULL), else one source (INDEPENDENT). A receipt-authentic but independently
    uncorroborated action reaches RECEIPT; anything else reaches NONE.

    HIGH and CRITICAL are deliberately indistinguishable here: the verifier's
    two-source rigor is the ceiling it can express, so the seam treats them as
    equivalent. If CRITICAL is ever meant to demand *more*, both the verifier and
    this mapping must change together.

    Non-monotonicity note (safe direction): with an authentic receipt, ZERO world
    facts yield INTEGRITY_ATTESTED -> RECEIPT, but ONE *insufficient* fact at HIGH
    yields INSUFFICIENT_CHANNELS (not attested) -> NONE. Adding partial
    corroboration can lower the achieved level — it only ever makes clearance
    HARDER, never a false clear.
    """
    if verdict.status is Status.VERIFIED:
        return FULL if effective_stakes in (Stakes.HIGH, Stakes.CRITICAL) else INDEPENDENT
    # RECEIPT only for a CLEAN attestation: INTEGRITY_ATTESTED present AND every
    # reason drawn from the attestation-compatible set. A real attested verdict
    # from the composer carries INTEGRITY_ATTESTED plus a per-obligation
    # "no independent fact" reason (NO_WORLD_FACT / NO_DISTINCT_FAILURE_MODE) —
    # those are the definition of attestation, not failures. Any OTHER reason
    # (INSUFFICIENT_CHANNELS, TYPE_FENCE, a contradiction, …) means it is not a
    # clean attestation and must not reach RECEIPT. An ALLOWLIST is drift-safe: a
    # newly added hard-failure Reason is excluded by default.
    if (
        verdict.status is Status.UNVERIFIED
        and Reason.INTEGRITY_ATTESTED in verdict.reasons
        and set(verdict.reasons) <= _ATTESTATION_COMPATIBLE
    ):
        return RECEIPT
    return NONE


# The reasons that legitimately accompany a clean attestation (see achieved_level).
_ATTESTATION_COMPATIBLE = frozenset({
    Reason.INTEGRITY_ATTESTED,
    Reason.NO_WORLD_FACT,
    Reason.NO_DISTINCT_FAILURE_MODE,
})

_NULL_VERDICT = Verdict(status=Status.UNVERIFIED, reasons=(), composer_version=GOVERNOR_VERSION)


def _denied(reasons, verdict=None) -> GovernedOutcome:
    """The fail-closed outcome: nothing cleared, nothing learned, required at the
    strictest level. Used for every malformed-input path so the gate DENIES rather
    than raises (a crash is not a deny)."""
    return GovernedOutcome(
        verdict=verdict if type(verdict) is Verdict else _NULL_VERDICT,
        required_level=FULL, achieved_level=NONE, effective_stakes=None,
        cleared=False, adaptation_allowed=False, reasons=tuple(reasons),
    )


def _valid_directive(directive) -> bool:
    return (
        type(directive) is Directive
        # `subject` is the binding key: `bound` tests `directive.subject ==
        # verdict.envelope_id`, an operator that dispatches to this
        # attacker-supplied operand. A non-str subject (e.g. an always-equal
        # object, or one whose __bool__/__eq__ raises) could bind to a verdict
        # for a DIFFERENT action, or crash the gate — a crash is not a deny.
        # Matches the emit-side fence, which already requires a bounded str.
        and isinstance(directive.subject, str)
        and isinstance(directive.verification_depth, int)
        and not isinstance(directive.verification_depth, bool)
        # The rationale rides through to the consumer gates (self-describing
        # outcome), so the seam validates it at the boundary: it must be a real
        # AdaptationRationale AND cohere with eligibility (ELIGIBLE iff
        # CANDIDATE — interpret() maintains this; a directive that desyncs the
        # pair is malformed, and a crash downstream is not a deny).
        and isinstance(directive.adaptation_eligibility, AdaptationEligibility)
        and isinstance(directive.adaptation_rationale, AdaptationRationale)
        and (
            (directive.adaptation_rationale is AdaptationRationale.ELIGIBLE)
            == (directive.adaptation_eligibility is AdaptationEligibility.CANDIDATE)
        )
    )


def decide(directive, verdict) -> GovernedOutcome:
    """Pure gate: compose a directive and a (self-describing) verdict into a
    governed outcome.

    The verdict carries its own `envelope_id` and `effective_stakes` (stamped by
    the verifier pipeline), so there are NO free parameters a caller could desync
    from the verdict: the required level, the achieved level, and the action
    binding all derive from the verdict itself. A hand-forged verdict is out of
    scope (it is equivalent to bypassing the verifier).

    Fail-closed: null inputs, a directive whose subject does not bind to the
    verdict's action, a conclusive failure, or an achieved level short of the
    required level all deny clearance; adaptation additionally requires a real
    VERIFIED. The required level is floored by BOTH the directive depth and the
    verdict's effective stakes; `effective_stakes` is the rigor-of-record (which
    `required_level` can understate).
    """
    if type(verdict) is not Verdict or not _valid_directive(directive):
        return _denied(("null_or_invalid_inputs",), verdict)

    reasons = []
    effective_stakes = verdict.effective_stakes

    # TWO policy floors bound the required verification level: the salience-driven
    # directive depth AND the (policy-signed) effective stakes the verifier ran at
    # (`_stakes_floor`). Take the stronger — salience may raise the bar, never
    # lower it. The stakes floor is what stops a low-depth directive from clearing
    # a high-stakes action the verifier could not corroborate.
    required = max(directive.verification_depth, _stakes_floor(effective_stakes))
    required = NONE if required < NONE else FULL if required > FULL else required  # range guard
    achieved = achieved_level(verdict, effective_stakes)

    bound = bool(directive.subject) and directive.subject == verdict.envelope_id
    if not bound:
        reasons.append(
            f"directive/verdict action mismatch: {directive.subject!r} != {verdict.envelope_id!r}"
        )
        # Do not surface the other action's stakes floor in the outcome.
        required = FULL

    if verdict.status is Status.FAILED:
        reasons.append("conclusive_failure")

    cleared = bound and verdict.status is not Status.FAILED and achieved >= required
    if bound and verdict.status is not Status.FAILED and achieved < required:
        reasons.append(f"under_verified: achieved={achieved} < required={required}")

    adaptation_allowed = (
        cleared
        and directive.adaptation_eligibility is AdaptationEligibility.CANDIDATE
        and verdict.status is Status.VERIFIED
    )

    return GovernedOutcome(
        verdict=verdict,
        required_level=required,
        achieved_level=achieved,
        effective_stakes=effective_stakes,
        cleared=cleared,
        adaptation_allowed=adaptation_allowed,
        reasons=tuple(reasons),
        # Self-description, stamped ONLY when bound: an unbound directive's
        # identity is withheld from the outcome (the _hard_deny precedent), so
        # no consumer can act on a directive that governed a different action.
        directive=directive if bound else None,
        subject=directive.subject if bound else "",
    )


def govern(verifier, directive: Directive, envelope, receipt, world_evidence) -> GovernedOutcome:
    """Run the verifier at the directive-escalated stakes, then gate the outcome.

    The directive and envelope must refer to the same action: `directive.subject`
    binds to `envelope.envelope_id`. The verifier runs at
    max(envelope.stakes, escalation_for(directive)) — upward only.
    """
    if not _valid_directive(directive):
        return _denied(("invalid_directive",))
    escalate = escalation_for(directive.verification_depth)
    verdict = verifier.verify(envelope, receipt, world_evidence, escalate_to=escalate)
    # The verdict is self-describing (envelope_id + effective_stakes stamped by
    # verify), so the gate needs nothing else.
    return decide(directive, verdict)
