"""The signed authority envelope — where capability and the bounds on every knob
come from (P-01). Analogous to the verifier's policy-signed action envelope.

Signals set scalar knobs *within* these caps; they can never widen them, add a
capability, or flip the adaptation switch. The interpreter verifies this
envelope's signature itself, so an unsigned or forged policy yields the hardest
fail-closed directive (no capabilities at all), not a trusted one.
"""

import enum
from dataclasses import dataclass, field

from salienceos.verifier.signing import sign, signature_valid

# Retention classes, least- to most-durable. Salience may buy UP to the policy's
# ceiling; the default (no memory signal) is the least-durable — a total durable
# record is itself a liability (Finding G).
RETENTION_ORDER = ("ephemeral", "working", "episodic", "semantic")


class VerificationDepth(enum.IntEnum):
    NONE = 0
    RECEIPT = 1
    INDEPENDENT = 2
    FULL = 3


class AdaptationEligibility(enum.Enum):
    NONE = "none"
    CANDIDATE = "candidate"  # offline review only; never auto-promoted (no live self-modification)


@dataclass(frozen=True)
class PolicyCaps:
    policy_id: str
    subject: str
    granted_capabilities: tuple   # the ONLY source of capability in a directive
    min_budget: int
    max_budget: int
    min_verification: int         # VerificationDepth floor
    max_verification: int         # ceiling AND the fail-closed default depth
    max_retention: str            # highest retention salience may buy
    allow_adaptation: bool        # policy switch; salience cannot flip it
    adaptation_min_verification: int
    adaptation_max_risk: float
    allow_immediate_reconfigure: bool  # else all reconfiguration defers to between-turn
    signature: str = field(default="")

    def signed_payload(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "subject": self.subject,
            "granted_capabilities": list(self.granted_capabilities),
            "min_budget": self.min_budget,
            "max_budget": self.max_budget,
            "min_verification": self.min_verification,
            "max_verification": self.max_verification,
            "max_retention": self.max_retention,
            "allow_adaptation": self.allow_adaptation,
            "adaptation_min_verification": self.adaptation_min_verification,
            "adaptation_max_risk": self.adaptation_max_risk,
            "allow_immediate_reconfigure": self.allow_immediate_reconfigure,
        }


def issue_policy(
    policy_id: str,
    subject: str,
    granted_capabilities,
    min_budget: int,
    max_budget: int,
    min_verification: int,
    max_verification: int,
    max_retention: str,
    allow_adaptation: bool,
    adaptation_min_verification: int,
    adaptation_max_risk: float,
    allow_immediate_reconfigure: bool,
    policy_key: bytes,
) -> PolicyCaps:
    caps = PolicyCaps(
        policy_id=policy_id,
        subject=subject,
        granted_capabilities=tuple(granted_capabilities),
        min_budget=min_budget,
        max_budget=max_budget,
        min_verification=min_verification,
        max_verification=max_verification,
        max_retention=max_retention,
        allow_adaptation=allow_adaptation,
        adaptation_min_verification=adaptation_min_verification,
        adaptation_max_risk=adaptation_max_risk,
        allow_immediate_reconfigure=allow_immediate_reconfigure,
    )
    return PolicyCaps(**{**caps.__dict__, "signature": sign(caps.signed_payload(), policy_key)})


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def verify_policy(policy, policy_key: bytes) -> bool:
    if type(policy) is not PolicyCaps:
        return False
    try:
        ok = signature_valid(policy.signed_payload(), policy.signature, policy_key)
    except (TypeError, ValueError):
        return False  # e.g. a non-bytes policy_key — fail closed, never raise
    if not ok:
        return False
    # A signed-but-incoherent envelope must be rejected here (the trust boundary):
    # the `: bool` / `: int` annotations are not enforced by issue_policy, and a
    # truthy non-bool switch (e.g. the string "false", or 1) would otherwise slip
    # past the adaptation/reconfigure gates.
    return (
        _is_int(policy.min_budget)
        and _is_int(policy.max_budget)
        and _is_int(policy.min_verification)
        and _is_int(policy.max_verification)
        and _is_int(policy.adaptation_min_verification)
        and isinstance(policy.allow_adaptation, bool)
        and isinstance(policy.allow_immediate_reconfigure, bool)
        and _is_num(policy.adaptation_max_risk)
        and isinstance(policy.granted_capabilities, tuple)
        and all(isinstance(c, str) for c in policy.granted_capabilities)
        and 0 <= policy.min_budget <= policy.max_budget
        and 0 <= policy.min_verification <= policy.max_verification <= int(VerificationDepth.FULL)
        # adaptation gate must be reachable: a floor above the ceiling would
        # silently make adaptation impossible without saying allow_adaptation=False.
        and 0 <= policy.adaptation_min_verification <= policy.max_verification
        and policy.max_retention in RETENTION_ORDER
        and 0.0 <= policy.adaptation_max_risk <= 1.0
    )
