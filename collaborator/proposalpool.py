"""The proposal stage pool — a durable home for surfaced-but-undecided proposals.

A proposal the human neither approved nor vetoed must not fall through the cracks. The
pool makes "surfaced, awaiting your decision" a first-class, findable state: every proposal
the propose channel surfaces is enrolled here and stays PENDING until it is explicitly
approved or vetoed. It is the source of truth for "what is waiting on me" — the natural feed
for a dashboard's pending queue.

Held by REFERENCE: the pool stores the same ``Proposal`` object the caller approves/vetoes,
so a resolution elsewhere (``approve_proposal`` / ``veto_proposal``) is reflected here in
place — the pool never needs to be told twice. Host-side bookkeeping, influence-free: the
pool grants NO authority (approval still runs the full capability gate); it only guarantees a
pending proposal is remembered and surfaced, never silently dropped.

Durability note: v0 is in-memory (per live session). Cross-restart persistence — so a pending
proposal survives a box restart — is the deferred follow-up, kept under the single-trust-
domain discipline of ADR 0002 (persistence is data-at-rest, never a second authority).
"""

from __future__ import annotations

from collaborator.propose import APPROVED, PROPOSED, VETOED

COLLABORATOR_PROPOSALPOOL_VERSION = "0.1.0"

_RESOLVED = (APPROVED, VETOED)


class ProposalPool:
    """An insertion-ordered pool of surfaced proposals, keyed by proposal_id."""

    def __init__(self) -> None:
        self._items: dict = {}  # proposal_id -> Proposal (insertion order preserved)

    def add(self, proposal) -> None:
        """Enroll a surfaced proposal. Idempotent by proposal_id: re-adding the same
        proposal is a no-op that never duplicates or resurrects an entry."""
        pid = getattr(proposal, "proposal_id", None)
        if pid:
            self._items.setdefault(pid, proposal)

    def get(self, proposal_id: str):
        return self._items.get(proposal_id)

    def pending(self) -> list:
        """Surfaced proposals still awaiting a decision (status PROPOSED), oldest first."""
        return [p for p in self._items.values() if getattr(p, "status", None) == PROPOSED]

    def resolved(self) -> list:
        """Proposals that WERE decided (approved or vetoed), oldest first."""
        return [p for p in self._items.values() if getattr(p, "status", None) in _RESOLVED]

    def all(self) -> list:
        return list(self._items.values())

    def pending_count(self) -> int:
        return sum(1 for p in self._items.values() if getattr(p, "status", None) == PROPOSED)

    def prune_resolved(self) -> int:
        """Drop decided proposals (housekeeping). Pending ones are NEVER pruned — that is the
        whole point of the pool. Returns how many were removed."""
        drop = [pid for pid, p in self._items.items() if getattr(p, "status", None) in _RESOLVED]
        for pid in drop:
            del self._items[pid]
        return len(drop)

    def snapshot(self) -> list:
        """A JSON-serializable view for a dashboard / audit: one dict per proposal, in
        insertion order (newest last). The human-facing text rides through
        ``Proposal.summary()``, which is already flattened (no control chars), so a
        memory-injected proposal cannot forge UI structure in the pending queue."""
        out = []
        for p in self._items.values():
            d = getattr(p, "decision", None)
            out.append({
                "proposal_id": getattr(p, "proposal_id", ""),
                "status": getattr(p, "status", ""),
                "confidence": round(float(getattr(p, "confidence", 0.0) or 0.0), 2),
                "tool": getattr(d, "tool", ""),
                "args": getattr(d, "args", {}),
                "leash": getattr(d, "leash", ""),
                "origin": getattr(p, "origin", "collaborator"),
                "summary": p.summary() if hasattr(p, "summary") else "",
            })
        return out

    def __len__(self) -> int:
        return len(self._items)
