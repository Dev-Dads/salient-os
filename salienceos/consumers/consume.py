"""The channel seam: one call, both channels, in the required order.

The weight gate runs FIRST — its risk-reject is what originates the inhibitor
hand-off — and the memory governor receives that hand-off explicitly, as a
record. The two channels can (and for high-risk content, must) disagree; this
function is where the disagreement becomes two records instead of one blurred
decision.

Pure: there is no I/O anywhere in this package, so the house "single impure
wrapper" slot stays deliberately empty until something writes durably.
"""

from salienceos.consumers.adaptation import nominate
from salienceos.consumers.memory import retain


def consume(outcome, now_days) -> tuple:
    """(AdaptationDecision, MemoryRetention) for one governed outcome."""
    decision = nominate(outcome)
    retention = retain(outcome, now_days, handoff=decision.handoff)
    return decision, retention
