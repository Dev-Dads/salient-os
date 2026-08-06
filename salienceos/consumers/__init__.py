"""SalienceOS consumers — the fourth build stage.

Build order: (1) verifier, (2) salience bus + central interpreter, (3) control
seam, (4) THIS — the consumers that make the seam's decision load-bearing.
control/ answers "was this action cleared, and may it be learned from";
consumers/ answers "what does each channel DO with that answer".

Two channels, strictly separate (Finding C) and able to DISAGREE: for
high-salience high-risk content the memory channel RETAINS (as a non-decaying
inhibitor — an incident record) while the weight channel HARD BLOCKS. The only
shared artifact is the explicit `InhibitorHandoff` record; memory.py and
adaptation.py never import each other (an import-graph fact, pinned by test).

Both gates are CONSUMERS of the recorded decision, never re-deciders
(Finding D): nomination's only predicate is `outcome.adaptation_allowed`;
retention's class comes from the bound directive; the inhibitor trigger is the
interpreter's RECORDED `RISK_EXCEEDED` rationale. No gate reads
`verdict.status` or raw salience.

Non-goals in v0 (deliberate): recall-time inhibitor checking ("before tools
and adaptation", docx §8.3) awaits a recall system; contradiction retention
with provenance (§15.4) likewise; nothing yet produces reinforcement events
(the parameter is honored in the decay formula); there is no direct
(non-hand-off) inhibitor trigger — and note that a host policy with
`allow_adaptation=False` therefore produces no inhibitors at all: the
disagreement property is library-real and host-dormant until an adaptation
path exists there.

Discipline: stdlib-only, synchronous, zero deps (AST-enforced by
tests/test_discipline.py); `nominate`, `retain`, `effective_weight` and
`consume` are pure and are the mutation-test targets. No clock — time is
injected (`now_days`).
"""

from salienceos.consumers.handoff import (
    HANDOFF_SOURCE_RISK_REJECT,
    InhibitorHandoff,
)
from salienceos.consumers.adaptation import (
    ADAPTATION_GATE_VERSION,
    AdaptationDecision,
    nominate,
)
from salienceos.consumers.memory import (
    HALF_LIFE_DAYS,
    MEMORY_GOVERNOR_VERSION,
    HandoffMismatchError,
    MemoryRetention,
    effective_weight,
    retain,
)
from salienceos.consumers.consume import consume

__all__ = [
    "HANDOFF_SOURCE_RISK_REJECT",
    "InhibitorHandoff",
    "ADAPTATION_GATE_VERSION",
    "AdaptationDecision",
    "nominate",
    "HALF_LIFE_DAYS",
    "MEMORY_GOVERNOR_VERSION",
    "HandoffMismatchError",
    "MemoryRetention",
    "effective_weight",
    "retain",
    "consume",
]
