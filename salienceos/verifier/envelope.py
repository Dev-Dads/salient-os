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
