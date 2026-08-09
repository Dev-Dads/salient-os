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

Bounded: PENDING is never auto-evicted (that is the point), but growth IS capped
(``max_pending``) so a flooding proposer cannot exhaust memory — once full, NEW enrollments are
refused (existing pending are preserved) rather than silently dropped-and-forgotten.

Durability note: v0 is in-memory (per live session). Cross-restart persistence — so a pending
proposal survives a box restart — is the deferred follow-up, kept under the single-trust-
domain discipline of ADR 0002 (persistence is data-at-rest, never a second authority).
"""

from __future__ import annotations

from collaborator.propose import PROPOSED

COLLABORATOR_PROPOSALPOOL_VERSION = "0.1.0"

DEFAULT_MAX_PENDING = 256


def _safe_args(args) -> dict:
    """A display-safe projection of a proposal's args: every string (the attacker-influenced
    path/content the proposer authored) is flattened, so a memory-injected proposal cannot forge
    UI structure — newlines, control/ANSI codes, or fence markers — in a dashboard's pending
    queue. This is a DISPLAY view (values are neutralized and length-capped); a consumer that
    needs the raw bytes reads them from the decision, not from the snapshot."""
    from collaborator.memory import _flatten

    out = {}
    for k, v in (args or {}).items():
        key = _flatten(str(k))
        if isinstance(v, str):
            out[key] = _flatten(v)
        elif isinstance(v, (list, tuple)):
            out[key] = [_flatten(str(x)) for x in v]
        else:
            out[key] = v
    return out


class ProposalPool:
    """An insertion-ordered, size-bounded pool of surfaced proposals, keyed by proposal_id."""

    def __init__(self, max_pending: int = DEFAULT_MAX_PENDING) -> None:
        self._items: dict = {}  # proposal_id -> Proposal (insertion order preserved)
        self.max_pending = max(1, int(max_pending))

    def add(self, proposal) -> bool:
        """Enroll a surfaced proposal. Idempotent by proposal_id (re-adding the same proposal is
        a no-op). Returns False (refuses) when the PENDING set is already at ``max_pending`` and
        this is a new proposal — never evicting an existing pending one to make room."""
        pid = getattr(proposal, "proposal_id", None)
        if not pid:
            return False
        if pid in self._items:
            return True  # already enrolled — idempotent, and it still counts against the cap
        if self.pending_count() >= self.max_pending:
            return False  # full: refuse the NEW one, keep every existing pending proposal
        self._items[pid] = proposal
        return True

    def get(self, proposal_id: str):
        return self._items.get(proposal_id)

    def pending(self) -> list:
        """Surfaced proposals still awaiting a decision (status PROPOSED), oldest first."""
        return [p for p in tuple(self._items.values()) if getattr(p, "status", None) == PROPOSED]

    def resolved(self) -> list:
        """Proposals that are no longer pending (decided or otherwise off PROPOSED), oldest first.
        Defined as the complement of ``pending`` so ``pending ∪ resolved == all`` always holds —
        no status can make a proposal vanish from BOTH operational views."""
        return [p for p in tuple(self._items.values()) if getattr(p, "status", None) != PROPOSED]

    def all(self) -> list:
        return list(self._items.values())

    def pending_count(self) -> int:
        return sum(1 for p in self._items.values() if getattr(p, "status", None) == PROPOSED)

    def prune_resolved(self) -> int:
        """Drop no-longer-pending proposals (housekeeping). Pending ones are NEVER pruned — that
        is the whole point of the pool. Returns how many were removed."""
        drop = [pid for pid, p in tuple(self._items.items()) if getattr(p, "status", None) != PROPOSED]
        for pid in drop:
            del self._items[pid]
        return len(drop)

    def snapshot(self) -> list:
        """A JSON-serializable, DISPLAY-SAFE view for a dashboard / audit: one dict per proposal,
        in insertion order (newest last). Every human-facing string — the ``summary`` line AND the
        ``args`` (path/content) — is flattened (``_safe_args`` / ``Proposal.summary``), so a
        memory-injected proposal cannot forge UI structure in the pending queue."""
        out = []
        for p in tuple(self._items.values()):
            d = getattr(p, "decision", None)
            out.append({
                "proposal_id": getattr(p, "proposal_id", ""),
                "status": getattr(p, "status", ""),
                "confidence": round(float(getattr(p, "confidence", 0.0) or 0.0), 2),
                "tool": getattr(d, "tool", ""),
                "args": _safe_args(getattr(d, "args", {})),
                "leash": getattr(d, "leash", ""),
                "origin": getattr(p, "origin", "collaborator"),
                "summary": p.summary() if hasattr(p, "summary") else "",
            })
        return out

    def __len__(self) -> int:
        return len(self._items)
