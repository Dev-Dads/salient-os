"""SalienceOS Verifier — typed, provenanced evidence folded into a three-state
verdict by an explicit, pure, fail-closed aggregation predicate.

Implements 00-VERIFIER-FINAL-RECOMMENDATION-v3.md (the authoritative spec).

Threat model (binding, spec §1): the in-scope adversary is a wrong or misfiring
model, a buggy executor/tool, or non-malicious corruption — NOT a malicious,
kernel-capable executor. "Independent" therefore means "not derived from the
executor's own written artifacts or its self-reported receipt", not
"kernel-attested". This verifier detects bugs and model error; it does not
claim compromise-resistance.

Discipline: stdlib-only, synchronous, zero dependencies. Enforced by
tests/test_discipline.py in the quorum_core style.
"""

from salienceos.verifier.verdict import Status, Reason, Verdict, NotAttestedError
from salienceos.verifier.evidence import (
    ClaimEvidence,
    WorldEvidence,
    ClaimStore,
    WorldStore,
)
from salienceos.verifier.envelope import (
    Stakes,
    STAKES_ORDER,
    ActionEnvelope,
    issue_envelope,
    verify_envelope,
    max_stakes,
)
from salienceos.verifier.contract import Obligation, Contract, build_contract, FLOOR_KINDS
from salienceos.verifier.composer import compose, COMPOSER_VERSION
from salienceos.verifier.receipt import (
    ExecutionReceipt,
    issue_receipt,
    claims_from_receipt,
    ReceiptStore,
    SealedGateError,
)
from salienceos.verifier.pipeline import Verifier

__all__ = [
    "Status",
    "Reason",
    "Verdict",
    "NotAttestedError",
    "STAKES_ORDER",
    "max_stakes",
    "ClaimEvidence",
    "WorldEvidence",
    "ClaimStore",
    "WorldStore",
    "Stakes",
    "ActionEnvelope",
    "issue_envelope",
    "verify_envelope",
    "Obligation",
    "Contract",
    "build_contract",
    "FLOOR_KINDS",
    "compose",
    "COMPOSER_VERSION",
    "ExecutionReceipt",
    "issue_receipt",
    "claims_from_receipt",
    "ReceiptStore",
    "SealedGateError",
    "Verifier",
]
