"""The directive — the interpreter's only output, and the single object every
consumer (orchestrator, memory manager, verifier, adaptation gate) reads.

`allowed_capabilities` is copied verbatim from the policy; the interpreter has no
code path from a signal to this field. `grants_capability()` is the only capability
accessor, so a consumer cannot infer authority from the scalar knobs.
"""

import enum
from dataclasses import dataclass, field

from salienceos.interpreter.policy import AdaptationEligibility


class Reconfigure(enum.Enum):
    BETWEEN_TURN = "between_turn"  # default — avoid mid-turn prefix-cache churn (Finding F)
    IMMEDIATE = "immediate"


class AdaptationRationale(enum.Enum):
    """Structured record of WHY the interpreter granted or denied adaptation
    eligibility (docx §9.3: rationale codes, never prose). Stamped by the
    decider so a downstream consumer can act on the recorded reason without
    re-deriving it from raw salience (Finding D).

    RISK_EXCEEDED requires an ASSERTED risk signal over the policy cap and is
    the only inhibitor hand-off trigger; RISK_UNKNOWN (no INFORMATIVE risk
    signal — absent entirely, or present only at zero confidence, which the
    aggregator omits) blocks eligibility but is deliberately NOT a trigger —
    ignorance is not an incident, and pinning unattributed content forever
    would pollute the inhibitor tier."""

    ELIGIBLE = "eligible"                    # iff eligibility is CANDIDATE
    POLICY_DISALLOWED = "policy_disallowed"  # allow_adaptation False, or untrusted policy
    NOT_REQUESTED = "not_requested"          # no positive ADAPTATION salience
    UNDER_VERIFIED = "under_verified"        # v_depth < adaptation_min_verification
    RISK_UNKNOWN = "risk_unknown"            # no RISK signal; blocked, NOT an inhibitor trigger
    RISK_EXCEEDED = "risk_exceeded"          # asserted risk > adaptation_max_risk — inhibitor trigger


@dataclass(frozen=True)
class Directive:
    subject: str
    policy_id: str
    compute_budget: int
    verification_depth: int
    retention_class: str
    routing_hint: str                       # advisory only
    adaptation_eligibility: AdaptationEligibility
    adaptation_rationale: AdaptationRationale  # no default: forgetting it is a construction error
    allowed_capabilities: tuple             # copied from policy; never signal-derived
    reconfigure: Reconfigure
    interpreter_version: str
    reasons: tuple = field(default=())

    def grants_capability(self, capability: str) -> bool:
        return capability in self.allowed_capabilities
