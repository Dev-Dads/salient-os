"""The governed outcome — the single object a consumer reads after the control
seam has composed the interpreter's directive with the verifier's verdict.

The unified verification scale is the interpreter's `VerificationDepth`
(0=NONE, 1=RECEIPT, 2=INDEPENDENT, 3=FULL). Both vocabularies map onto it:
  - the directive supplies the REQUIRED level (salience-driven, policy-bounded);
  - the verdict supplies the ACHIEVED level (what the world actually corroborated).
An action is `cleared` only when achieved >= required and the verdict is not a
conclusive failure. `adaptation_allowed` additionally requires a real VERIFIED —
salience/policy may deem an action adaptation-ELIGIBLE, but only the world (the
verifier) can confirm the action actually succeeded before anything is learned.
"""

from dataclasses import dataclass, field

from salienceos.interpreter import VerificationDepth
from salienceos.verifier import Stakes, Verdict

# Re-exported names for the unified scale (aliases of VerificationDepth).
NONE = int(VerificationDepth.NONE)
RECEIPT = int(VerificationDepth.RECEIPT)
INDEPENDENT = int(VerificationDepth.INDEPENDENT)
FULL = int(VerificationDepth.FULL)


@dataclass(frozen=True)
class GovernedOutcome:
    verdict: Verdict
    required_level: int          # from the directive (salience-driven, policy-bounded)
    achieved_level: int          # from the verdict (what the world corroborated)
    effective_stakes: Stakes     # the stakes the verifier actually ran at
    cleared: bool                # achieved >= required AND not a conclusive failure
    adaptation_allowed: bool     # directive-eligible AND verdict VERIFIED
    reasons: tuple = field(default=())
