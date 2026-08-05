"""Policy-signed action envelope — the grounded oracle (spec M3).

Expected post-state derives from the *authorized args*, not from the model's
after-the-fact narrative. Stakes live inside the signed payload (spec M4:
"stakes is a policy-signed input, not a mutable request field"), so a request
cannot lower its own scrutiny after authorization.
"""

import enum
from dataclasses import dataclass

from salienceos.verifier.signing import sign, signature_valid


class Stakes(enum.Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


# Least- to most-demanding. Used only to take the STRONGER of two stakes.
STAKES_ORDER = (Stakes.LOW, Stakes.NORMAL, Stakes.HIGH, Stakes.CRITICAL)


def max_stakes(a, b):
    """Return the stronger of two stakes. This is the ONLY way stakes is ever
    combined: escalation is upward-only, so a salience-driven request can raise
    scrutiny above the policy-signed floor but never lower it (spec M4 — "stakes
    is a policy-signed input, not a mutable request field"; making verification
    stricter is the allowed direction).

    Fail-safe on malformed input: a value that is not a known Stakes (including
    None or a stray string from a buggy caller) is treated as "absent" (rank -1),
    so it is ignored rather than raising. It can therefore never lower a valid
    stakes, and two malformed inputs yield None."""
    ra = STAKES_ORDER.index(a) if a in STAKES_ORDER else -1
    rb = STAKES_ORDER.index(b) if b in STAKES_ORDER else -1
    if ra < 0 and rb < 0:
        return None
    return a if ra >= rb else b


@dataclass(frozen=True)
class ActionEnvelope:
    envelope_id: str
    op: str  # "file.write" | "dir.make" | "file.delete" | "shell.run"
    args: dict  # authorized arguments; treated as immutable
    action_class: str
    stakes: Stakes
    policy_id: str
    signature: str

    def signed_payload(self) -> dict:
        return {
            "envelope_id": self.envelope_id,
            "op": self.op,
            "args": self.args,
            "action_class": self.action_class,
            "stakes": self.stakes.value,
            "policy_id": self.policy_id,
        }


def issue_envelope(
    envelope_id: str,
    op: str,
    args: dict,
    action_class: str,
    stakes: Stakes,
    policy_id: str,
    policy_key: bytes,
) -> ActionEnvelope:
    payload = {
        "envelope_id": envelope_id,
        "op": op,
        "args": args,
        "action_class": action_class,
        "stakes": stakes.value,
        "policy_id": policy_id,
    }
    return ActionEnvelope(
        envelope_id=envelope_id,
        op=op,
        args=args,
        action_class=action_class,
        stakes=stakes,
        policy_id=policy_id,
        signature=sign(payload, policy_key),
    )


def verify_envelope(envelope: ActionEnvelope, policy_key: bytes) -> bool:
    return signature_valid(envelope.signed_payload(), envelope.signature, policy_key)
