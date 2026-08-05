"""VerdictComposer — the explicit, pure, fail-closed aggregation predicate.

Spec M4: this function IS the component. It is pure (no I/O, no clock, no
globals), versioned, and the primary mutation-test target. The verdict rule:

  FAILED      — any obligation has a conclusive contradiction between
                expectation, executor claim, and world observation.
  VERIFIED    — every required obligation agrees with at least one WORLD fact
                (a) observed through a channel the executor did not write and
                (b) carrying a failure mode distinct from the CLAIM fact it
                agrees with; HIGH/CRITICAL stakes require two distinct world
                channels per obligation (spec §4 "two-source").
  UNVERIFIED  — everything else: no contract, floor missing, invalid stakes,
                only-CLAIM evidence, budget-exhausted (manifesting as absent
                world facts). INTEGRITY_ATTESTED is attached as a sub-code
                when the receipt was authentic and self-consistent but no
                independent world fact was obtained.

Fail-closed means every early exit returns UNVERIFIED, never VERIFIED.
"""

from salienceos.verifier.contract import FLOOR_KINDS, SIDE_EFFECT_KINDS
from salienceos.verifier.envelope import Stakes
from salienceos.verifier.evidence import ClaimEvidence, WorldEvidence
from salienceos.verifier.verdict import Reason, Status, Verdict

COMPOSER_VERSION = "composer/0.1.0"


def compose(contract, claim_ev, world_ev, stakes) -> Verdict:
    """Fold typed CLAIM and WORLD evidence into a three-state verdict.

    claim_ev and world_ev are SEPARATE typed inputs (spec §2). The composer
    re-fences both at its own boundary: anything of the wrong exact type is
    dropped, so a smuggled claim can never count as a world fact even if the
    store-level fence were refactored away (mutation fixture 4).
    """
    reasons = []
    details = []

    claims = tuple(e for e in claim_ev if type(e) is ClaimEvidence)
    world = tuple(e for e in world_ev if type(e) is WorldEvidence)
    if len(claims) != len(tuple(claim_ev)) or len(world) != len(tuple(world_ev)):
        reasons.append(Reason.TYPE_FENCE)
        details.append("evidence of the wrong type was dropped at the composer boundary")

    if contract is None or not contract.obligations:
        return _unverified([Reason.NO_CONTRACT] + reasons, ["missing or empty contract"] + details)

    kinds = {o.kind for o in contract.obligations}
    if not (all(k in kinds for k in FLOOR_KINDS) and any(k in kinds for k in SIDE_EFFECT_KINDS)):
        return _unverified(
            [Reason.MISSING_FLOOR] + reasons,
            [f"contract kinds {sorted(kinds)} lack the minimum-obligation floor"] + details,
        )

    if not isinstance(stakes, Stakes):
        return _unverified([Reason.INVALID_STAKES] + reasons, ["stakes is not a policy Stakes value"] + details)

    for o in contract.obligations:
        conflict = _conclusive_contradiction(o, claims, world)
        if conflict:
            return Verdict(
                status=Status.FAILED,
                reasons=(Reason.CONCLUSIVE_CONTRADICTION,),
                details=(f"{o.obligation_id}: {conflict}",) + tuple(details),
                composer_version=COMPOSER_VERSION,
            )

    unmet = []
    for o in contract.obligations:
        why = _agreement_gap(o, claims, world, stakes)
        if why is not None:
            unmet.append((o, why))

    if unmet:
        for o, (reason, note) in unmet:
            if reason not in reasons:
                reasons.append(reason)
            details.append(f"{o.obligation_id}: {note}")
        # INTEGRITY_ATTESTED means the receipt is authentic yet NO usable
        # independent world fact was obtained (spec M5). Attach it only when
        # every obligation is unmet for exactly that reason — never when a
        # world fact was present but merely fell short of the high-stakes
        # channel bar (INSUFFICIENT_CHANNELS), which would mislabel
        # "world present but insufficient" as "claim-only".
        no_usable_world = {r for _, (r, _) in unmet} <= {
            Reason.NO_WORLD_FACT,
            Reason.NO_DISTINCT_FAILURE_MODE,
        }
        if _receipt_attested(claims) and len(unmet) == len(contract.obligations) and no_usable_world:
            reasons.append(Reason.INTEGRITY_ATTESTED)
            details.append("receipt authentic and self-consistent; no independent world fact")
        return _unverified(reasons, details)

    return Verdict(
        status=Status.VERIFIED,
        reasons=(),
        details=tuple(details),
        composer_version=COMPOSER_VERSION,
    )


def _unverified(reasons, details) -> Verdict:
    return Verdict(
        status=Status.UNVERIFIED,
        reasons=tuple(reasons),
        details=tuple(details),
        composer_version=COMPOSER_VERSION,
    )


def _on(obligation, evidence):
    return tuple(
        e for e in evidence if e.obligation_id == obligation.obligation_id and e.kind == obligation.kind
    )


def _conclusive_contradiction(o, claims, world):
    """A world or claim fact that conflicts with the envelope-derived
    expectation, or a world fact that conflicts with the executor's claim."""
    o_claims = _on(o, claims)
    o_world = _on(o, world)
    if o.expectation is not None:
        for w in o_world:
            if w.value != o.expectation:
                return f"world {w.channel} observed {w.value!r}, expected {o.expectation!r}"
        for c in o_claims:
            if c.value != o.expectation:
                return f"claim {c.channel} asserts {c.value!r}, expected {o.expectation!r}"
    for w in o_world:
        for c in o_claims:
            if w.value != c.value:
                return (
                    f"world {w.channel} observed {w.value!r} but claim "
                    f"{c.channel} asserts {c.value!r}"
                )
    return None


def _required_sources(stakes) -> int:
    return 2 if stakes in (Stakes.HIGH, Stakes.CRITICAL) else 1


def _agreement_gap(o, claims, world, stakes):
    """None when the obligation is met; else (Reason, note)."""
    o_claims = _on(o, claims)
    o_world = _on(o, world)

    if o.expectation is not None:
        target = o.expectation
    elif o_claims:
        target = o_claims[0].value
    else:
        return (Reason.NO_WORLD_FACT, "no expectation and no claim to corroborate")

    matching = tuple(w for w in o_world if w.value == target)
    if not matching:
        return (Reason.NO_WORLD_FACT, "no executor-independent world fact agrees")

    claim_modes = {c.failure_mode for c in o_claims}
    distinct = tuple(w for w in matching if w.failure_mode not in claim_modes)
    if not distinct:
        return (
            Reason.NO_DISTINCT_FAILURE_MODE,
            "world facts share the claim's failure mode",
        )

    # Independence is keyed on distinct FAILURE MODES, not channel strings
    # (spec M1). Two channels that share a failure mode (e.g. host.rehash and
    # a host.rehash mirror) are correlated, not two sources, and must not
    # satisfy the high-stakes two-source bar.
    modes = {w.failure_mode for w in distinct}
    if len(modes) < _required_sources(stakes):
        return (
            Reason.INSUFFICIENT_CHANNELS,
            f"stakes {stakes.value} require {_required_sources(stakes)} distinct "
            f"world failure modes, got {len(modes)}",
        )
    return None


def _receipt_attested(claims) -> bool:
    return any(c.kind == "receipt_integrity" and c.value == "authentic" for c in claims)
