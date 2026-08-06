"""The cross-channel boundary record.

Finding C requires the memory and weight-adaptation channels to stay strictly
separate — separate enough to DISAGREE: highly salient malicious content is a
memory RETAIN (as an inhibitor) and a weight HARD BLOCK, simultaneously
(docx §4.4: "preserved as an incident or inhibitor, not learned as a
capability"). This module is the only thing both channels may share: an
explicit, frozen record of the weight gate's risk-reject, handed to the memory
governor. Because the hand-off is a record and not an import, channel
separation stays an import-graph fact (pinned by test): memory.py and
adaptation.py never import each other; both may import this.
"""

from dataclasses import dataclass, field

# The only hand-off source defined in v0: the weight gate rejecting an
# adaptation request because an ASSERTED risk exceeded the policy cap.
HANDOFF_SOURCE_RISK_REJECT = "adaptation.risk_reject"


@dataclass(frozen=True)
class InhibitorHandoff:
    """A weight-channel risk-reject, addressed to the memory channel.

    Clock-free by design — the memory governor stamps time on receipt. The
    receiving gate (`memory.retain`) validates every field against the
    outcome's RECORDED decision before honoring it; a hand-off that cannot be
    attributed is unrecordable (raises), never silently dropped or accepted.
    """

    subject: str       # the bound outcome subject this reject belongs to
    source: str        # HANDOFF_SOURCE_RISK_REJECT
    rationale: str     # AdaptationRationale value, e.g. "risk_exceeded"
    reasons: tuple = field(default=())
