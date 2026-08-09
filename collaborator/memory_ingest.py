"""The Collaborator's memory — the write side (host-side, ledger-only, NEVER a model tool).

The doer's governed history feeds CDMS so the proposer can later be shaped by it. Two
hard rules (design v3):

  1. LEDGER-ONLY (S6): the ingested record is built from the ② judgment ledger's
     structured fields only — tool, a normalized/hashed arg key, cleared, a status in
     {ran, failed, vetoed}, project. NEVER the model's rationale or any prose. "Hands
     can't lie" only holds if the hook copies the deed, not the narration — so injection
     cannot re-enter at the source and "proposed" is never confused with "ran".
  2. AMBIGUOUS + SOURCE-TAGGED (C, S4): every deed is stamped ``provenance="ambiguous"``
     (CDMS "quarantine": gists but never scars) and carries ``source="collaborator_deed"``
     so consolidation never merges a deed with other ``ambiguous`` (mixed-origin) content.

There is NO ``memory.write`` verb for the model; the only write path is this host hook.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Protocol

COLLABORATOR_INGEST_VERSION = "0.1.0"

DEED_PROVENANCE = "ambiguous"          # CDMS quarantine rank: gists, never scars
DEED_SOURCE = "collaborator_deed"      # partition marker (never merge with other ambiguous)

# The only statuses a completed deed ingests as. A proposal HELD/paused/denied is not a
# deed and is not ingested here; a veto is ingested as an outcome the proposer can learn.
RAN, FAILED, VETOED = "ran", "failed", "vetoed"


def _args_key(args: dict) -> str:
    """A stable, bounded key for the deed's arguments — a hash of the normalized args,
    NOT the raw content (a multi-KB file body never enters the record)."""
    try:
        blob = json.dumps(args or {}, sort_keys=True, default=str)[:4096]
    except (TypeError, ValueError):
        blob = str(args)[:4096]
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:16]


@dataclass(frozen=True)
class DeedEvent:
    """A governed deed, ready to ingest. Structured fields only — no prose. Maps to a
    CDMS ``TurnEvent`` via ``to_turn_event`` with provenance ``ambiguous`` and the deed
    source marker."""

    tool: str
    args_key: str
    status: str            # RAN | FAILED | VETOED
    project: str
    session_id: str
    success: "bool | None" = None
    # NON-init + frozen: a deed is ALWAYS `ambiguous` and source-tagged. No caller can
    # construct a `trusted` deed (which could then mint a scar in CDMS) — the guarantee is
    # the type, not the convention of going through ingest_deed.
    provenance: str = field(default=DEED_PROVENANCE, init=False)
    source: str = field(default=DEED_SOURCE, init=False)

    def to_turn_event(self) -> dict:
        """A CDMS-``TurnEvent``-shaped dict. Text fields carry the STRUCTURED deed, never
        model narration; the source marker rides in ``session_id`` (until CDMS grows a
        first-class ``source`` field / consolidation partition — a named follow-up)."""
        return {
            "trigger_prompt": f"deed:{self.tool}",
            "action_taken": f"{self.tool}({self.args_key})",
            "outcome_feedback": self.status,           # a status token, not prose
            "tool_name": self.tool,
            "success": self.success,
            # Sanitize the delimiter so the source marker stays unambiguous even if the host
            # session_id itself contains a colon.
            "session_id": f"{self.source}:{str(self.session_id).replace(':', '_')}",
            "project": self.project,
            "provenance": self.provenance,             # MUST be 'ambiguous', never trusted
        }


def ingest_deed(decision, *, session_id: str, project: "str | None" = None) -> "DeedEvent | None":
    """Build a ``DeedEvent`` from a governed ``Decision`` (ledger fields only). Returns
    None for a decision that is not a completed deed (held/paused/denied/notified/unknown).
    A vetoed proposal is passed with ``decision.status`` pre-set to ``vetoed`` by the caller."""
    status = getattr(decision, "status", "")
    if status == "ran":
        st = RAN
    elif status == "failed":
        st = FAILED
    elif status == "vetoed":
        st = VETOED
    else:
        return None  # not a deed to remember
    return DeedEvent(
        tool=str(getattr(decision, "tool", "") or ""),
        args_key=_args_key(getattr(decision, "args", {}) or {}),
        status=st,
        project=str(project if project is not None else getattr(decision, "project", "") or ""),
        session_id=str(session_id),
        success=bool(getattr(decision, "cleared", False)) if st != VETOED else None,
    )


class IngestSink(Protocol):
    """Where deeds are written. The real sink maps ``to_turn_event`` onto CDMS
    ``MemoryService.ingest``; a fake collects them for tests."""

    def write(self, deed: DeedEvent) -> None:
        ...


class FakeIngestSink:
    """Collects deeds in memory for tests/offline runs."""

    def __init__(self) -> None:
        self.deeds: "list[DeedEvent]" = []

    def write(self, deed: DeedEvent) -> None:
        self.deeds.append(deed)


def remember(sink: IngestSink, decision, *, session_id: str,
             project: "str | None" = None) -> "DeedEvent | None":
    """Host-side: turn a governed decision into a ledger-only, ambiguous, source-tagged
    deed and write it. Best-effort — a sink error never breaks the action."""
    deed = ingest_deed(decision, session_id=session_id, project=project)
    if deed is None:
        return None
    try:
        sink.write(deed)
    except Exception:  # noqa: BLE001 — memory write is best-effort
        return deed
    return deed
