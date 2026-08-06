"""SalienceOS salience bus + central interpreter.

The second build (SalienceOS_Design_Review_v0.2.md, Part 4 #2): subsystems each
compute salience their own way and publish a thin `SalienceSignal` onto a shared
`SalienceBus`; the central `interpret()` reads them and issues one `Directive`,
bounded by a signed `PolicyCaps`. It is the single fail-closed choke point, under
the invariant **salience influences; policy authorizes** — the directive analog
of the verifier's composer.

Discipline (shared with the verifier): stdlib-only, synchronous, zero deps,
AST-enforced by tests/test_discipline.py; the core `interpret()` is pure and the
mutation-test target.
"""

from salienceos.interpreter.signal import Facet, SalienceSignal, valid_signal
from salienceos.interpreter.policy import (
    RETENTION_ORDER,
    AdaptationEligibility,
    PolicyCaps,
    VerificationDepth,
    issue_policy,
    verify_policy,
)
from salienceos.interpreter.directive import AdaptationRationale, Directive, Reconfigure
from salienceos.interpreter.interpreter import (
    INTERPRETER_VERSION,
    IMMEDIATE_RECONFIGURE_THRESHOLD,
    interpret,
)
from salienceos.interpreter.bus import SalienceBus
from salienceos.interpreter.scorers import additive_scorer, threshold_scorer

__all__ = [
    "Facet",
    "SalienceSignal",
    "valid_signal",
    "RETENTION_ORDER",
    "AdaptationEligibility",
    "PolicyCaps",
    "VerificationDepth",
    "issue_policy",
    "verify_policy",
    "AdaptationRationale",
    "Directive",
    "Reconfigure",
    "INTERPRETER_VERSION",
    "IMMEDIATE_RECONFIGURE_THRESHOLD",
    "interpret",
    "SalienceBus",
    "additive_scorer",
    "threshold_scorer",
]
