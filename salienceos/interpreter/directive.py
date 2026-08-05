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


@dataclass(frozen=True)
class Directive:
    subject: str
    policy_id: str
    compute_budget: int
    verification_depth: int
    retention_class: str
    routing_hint: str                       # advisory only
    adaptation_eligibility: AdaptationEligibility
    allowed_capabilities: tuple             # copied from policy; never signal-derived
    reconfigure: Reconfigure
    interpreter_version: str
    reasons: tuple = field(default=())

    def grants_capability(self, capability: str) -> bool:
        return capability in self.allowed_capabilities
