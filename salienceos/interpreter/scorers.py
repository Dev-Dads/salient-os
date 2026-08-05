"""Example per-subsystem scorers — demonstrations that the bus contract is thin.

These are NOT part of the contract; they show two subsystems computing salience
in entirely different ways yet publishing the same `SalienceSignal`. Finding A:
keep salience additive within a subsystem and independent across subsystems;
don't reintroduce cross-terms the data doesn't support. A subsystem's rich
internal scoring stays private behind the normalized influence it emits.

Comparability caveat: cross-subsystem influence comparability is a *convention*
(all emit [0,1]), not something the interpreter can enforce. Mixing a binary
scorer (threshold_scorer) and a continuous one on the SAME facet lets the binary
dominate the confidence-weighted mean. The mitigation is per-subsystem choice of
confidence, not interpreter magic — publishers that are less sure should say so.
"""

from salienceos.interpreter.signal import SalienceSignal


def additive_scorer(subsystem_id, subject, facet, features: dict, weights: dict,
                    confidence: float, provenance=()) -> SalienceSignal:
    """A deterministic-additive scorer (the CDMS-style baseline, Finding A):
    influence is a clamped weighted sum of named features. Diagonal, no
    cross-terms."""
    raw = sum(weights.get(k, 0.0) * float(features.get(k, 0.0)) for k in weights)
    influence = 0.0 if raw < 0.0 else 1.0 if raw > 1.0 else raw
    return SalienceSignal(
        subsystem_id=subsystem_id,
        subject=subject,
        facet=facet,
        influence=influence,
        confidence=_unit(confidence),
        provenance=tuple(provenance),
    )


def threshold_scorer(subsystem_id, subject, facet, value: float, threshold: float,
                     confidence: float, provenance=()) -> SalienceSignal:
    """A rule scorer with a completely different shape from the additive one:
    influence is 1.0 above a threshold, else 0.0. Same thin output."""
    influence = 1.0 if float(value) >= float(threshold) else 0.0
    return SalienceSignal(
        subsystem_id=subsystem_id,
        subject=subject,
        facet=facet,
        influence=influence,
        confidence=_unit(confidence),
        provenance=tuple(provenance),
    )


def _unit(x: float) -> float:
    x = float(x)
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x
