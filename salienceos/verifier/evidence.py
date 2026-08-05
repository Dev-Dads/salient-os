"""CLAIM-side and WORLD-side evidence as separate types in separate stores.

Spec §2: in v1 the CLAIM/WORLD distinction was a runtime enum field on one
evidence type; reviewers noted a one-line refactor could drop the filter.
Here it is a *type*: `ClaimEvidence` and `WorldEvidence` share no base class,
each store accepts exactly one of them (checked with `type(...) is`, so even
subclassing cannot smuggle one across), and the composer takes them as two
separate parameters. The composer additionally re-fences at its own boundary
(composer.py), so dropping either fence alone still fails closed — that is
mutation fixture 4.

WorldEvidence must only ever be constructed by observers that read through a
channel the executor did not write (host-namespace re-hash, supervisor exit
status, host snapshot diff — see observers.py). Nothing derived from the
receipt may be constructed as WorldEvidence; that convention is what the
type fence plus the store separation makes auditable in one place.
"""

import json
from dataclasses import dataclass, asdict

from salienceos.verifier.signing import digest


@dataclass(frozen=True)
class ClaimEvidence:
    """A fact asserted by the executor (receipt-derived) or the model."""

    obligation_id: str
    kind: str  # exit_status | artifact_hash | write_set | path_state | receipt_integrity
    value: str
    failure_mode: str  # e.g. "executor_self_report"
    channel: str  # e.g. "receipt"
    provenance: str


@dataclass(frozen=True)
class WorldEvidence:
    """A fact observed through a channel the executor did not write."""

    obligation_id: str
    kind: str  # exit_status | artifact_hash | write_set | path_state
    value: str
    failure_mode: str  # e.g. "host_rehash", "supervisor_exit", "host_snapshot_diff"
    channel: str  # observer identity
    provenance: str


class _EvidenceLog:
    """Append-only, content-addressed, hash-chained store for one evidence side.

    Physically separate stores (spec §2): instantiate one per side, optionally
    backed by distinct JSONL files. There is no update or delete surface.
    """

    _accepts = None  # subclass sets the exact accepted type

    def __init__(self, path=None):
        self._entries = []
        self._path = path
        self._head = ""

    def append(self, item) -> str:
        if type(item) is not self._accepts:
            raise TypeError(
                f"{type(self).__name__} accepts only {self._accepts.__name__}, "
                f"got {type(item).__name__}"
            )
        payload = asdict(item)
        entry = {"payload": payload, "prev": self._head}
        entry_hash = digest(entry)
        entry["hash"] = entry_hash
        self._entries.append((entry_hash, item))
        self._head = entry_hash
        if self._path is not None:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry_hash

    def extend(self, items) -> None:
        for item in items:
            self.append(item)

    def items(self) -> tuple:
        return tuple(item for _, item in self._entries)

    def head(self) -> str:
        return self._head


class ClaimStore(_EvidenceLog):
    _accepts = ClaimEvidence


class WorldStore(_EvidenceLog):
    _accepts = WorldEvidence
