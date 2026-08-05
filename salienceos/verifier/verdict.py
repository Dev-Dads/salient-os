"""Three-state verdict with INTEGRITY_ATTESTED as a reason sub-code (spec M5).

There is deliberately no fourth state and no boolean success surface:
- `Verdict.__bool__` raises, so `if verdict:` cannot launder a non-VERIFIED
  status into "success".
- INTEGRITY_ATTESTED (receipt authentic and self-consistent, but no
  executor-independent world-side fact obtained) is reachable only through the
  dedicated `require_attested()` accessor, forcing consumers to handle it
  explicitly. It is UNVERIFIED for every other purpose: adaptation and
  user-facing "success" remain hard-blocked.
"""

import enum
from dataclasses import dataclass, field


class Status(enum.Enum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    FAILED = "FAILED"


class Reason(enum.Enum):
    # UNVERIFIED sub-codes
    NO_CONTRACT = "NO_CONTRACT"
    RECEIPT_ENVELOPE_MISMATCH = "RECEIPT_ENVELOPE_MISMATCH"
    MISSING_FLOOR = "MISSING_FLOOR"
    INVALID_STAKES = "INVALID_STAKES"
    TYPE_FENCE = "TYPE_FENCE"
    NO_WORLD_FACT = "NO_WORLD_FACT"
    NO_DISTINCT_FAILURE_MODE = "NO_DISTINCT_FAILURE_MODE"
    INSUFFICIENT_CHANNELS = "INSUFFICIENT_CHANNELS"
    INTEGRITY_ATTESTED = "INTEGRITY_ATTESTED"
    # FAILED sub-codes
    CONCLUSIVE_CONTRADICTION = "CONCLUSIVE_CONTRADICTION"


class NotAttestedError(Exception):
    """Raised by require_attested() on any verdict that is not exactly
    UNVERIFIED + INTEGRITY_ATTESTED."""


@dataclass(frozen=True)
class Verdict:
    status: Status
    reasons: tuple = field(default=())
    details: tuple = field(default=())
    composer_version: str = ""
    # Provenance stamped by the pipeline (not the pure composer): which action the
    # verdict is about, and the effective stakes it was actually composed at. These
    # make the verdict self-describing so a downstream gate binds to and reads them
    # rather than trusting free, desyncable parameters.
    envelope_id: str = ""
    effective_stakes: object = None

    @property
    def is_verified(self) -> bool:
        return self.status is Status.VERIFIED

    def require_attested(self) -> None:
        """Explicit accessor for the INTEGRITY_ATTESTED sub-code (spec M5).

        Returns None when the verdict is UNVERIFIED with integrity attested;
        raises NotAttestedError otherwise. Never a success signal.
        """
        if self.status is Status.UNVERIFIED and Reason.INTEGRITY_ATTESTED in self.reasons:
            return None
        raise NotAttestedError(
            f"status={self.status.value} reasons={[r.value for r in self.reasons]}"
        )

    def __bool__(self) -> bool:
        raise TypeError(
            "Verdict has no truth value; check .status explicitly "
            "(this prevents `if verdict:` from laundering UNVERIFIED into success)"
        )
