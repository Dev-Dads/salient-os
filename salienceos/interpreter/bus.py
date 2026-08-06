"""The salience bus — the auditable contract and the audit surface (Finding G).

Append-only and hash-chained, like the verifier's evidence log. It holds the
durable record: which subsystems published what influence about a subject, and
which directive the interpreter emitted. It is deliberately incapable of holding
the ephemeral inputs — a `SalienceSignal` carries only bounded, ref-shaped tokens
(enforced by `valid_signal`, which every `publish` requires), never prompts,
bodies, args, or chain-of-thought — so "a total durable record is itself a
liability" is handled by construction, not policy. The chain is verifiable end to
end via `verify_chain()`.

The bus does not decide or authorize anything: enforcement is the interpreter's.
Recording a directive here is NOT proof it was authorized — a directive's
authority comes from having been produced by `interpret()` against a signed
policy, never from its presence on the bus. The bus only records and serves
signals for arbitration, keeping the choke point single.

Integrity scope: `verify_chain()` detects accidental corruption, truncation, and
reordering of the durable record (the in-scope non-malicious-corruption case). It
does NOT prove authentic history against an adversary who can rewrite every entry
AND the head consistently — that requires a signed/anchored head under an audit
key, which is deferred (out of scope, same boundary as the verifier's). This is a
reviewed decision, not an oversight — see docs/adr/0001-verify-chain-integrity-scope.md.
"""

import json
import os
from dataclasses import asdict

from salienceos.interpreter.directive import Directive
from salienceos.interpreter.signal import MAX_TOKEN_LEN, SalienceSignal, valid_signal
from salienceos.verifier.signing import digest

# The directive half of the audit fence (Finding G). Signals are body-free via
# valid_signal; directive entries are the OTHER durable kind and get the same
# structural guarantee: every string bounded, every collection bounded, and on
# replay an exact payload key set — nothing prompt-sized can become durable.
DIRECTIVE_PAYLOAD_KEYS = frozenset({
    "subject", "policy_id", "compute_budget", "verification_depth",
    "retention_class", "routing_hint", "adaptation_eligibility",
    "adaptation_rationale", "allowed_capabilities", "reconfigure",
    "interpreter_version", "reasons",
})
MAX_DIRECTIVE_REASONS = 32
MAX_DIRECTIVE_CAPABILITIES = 64


def _bounded_str(x) -> bool:
    return isinstance(x, str) and len(x) <= MAX_TOKEN_LEN


def _valid_directive_shape(d) -> bool:
    """Ref-shaped bounds for the durable directive record — the emit-side
    fence. Enum fields are checked by emit's own .value access on the real
    enum types (Directive construction already requires them)."""
    return (
        _bounded_str(d.subject) and _bounded_str(d.policy_id)
        and _bounded_str(d.retention_class) and _bounded_str(d.routing_hint)
        and _bounded_str(d.interpreter_version)
        and isinstance(d.compute_budget, int) and not isinstance(d.compute_budget, bool)
        and isinstance(d.verification_depth, int) and not isinstance(d.verification_depth, bool)
        and isinstance(d.allowed_capabilities, tuple)
        and len(d.allowed_capabilities) <= MAX_DIRECTIVE_CAPABILITIES
        and all(_bounded_str(c) for c in d.allowed_capabilities)
        and isinstance(d.reasons, tuple)
        and len(d.reasons) <= MAX_DIRECTIVE_REASONS
        and all(_bounded_str(r) for r in d.reasons)
    )


def _valid_directive_payload(p) -> bool:
    """The replay-side fence: exact key set and the same ref-shaped bounds,
    applied to the parsed dict (enum fields arrive as their .value strings)."""
    if not isinstance(p, dict) or set(p) != DIRECTIVE_PAYLOAD_KEYS:
        return False
    strings = ("subject", "policy_id", "retention_class", "routing_hint",
               "adaptation_eligibility", "adaptation_rationale", "reconfigure",
               "interpreter_version")
    return (
        all(_bounded_str(p[k]) for k in strings)
        and isinstance(p["compute_budget"], int) and not isinstance(p["compute_budget"], bool)
        and isinstance(p["verification_depth"], int) and not isinstance(p["verification_depth"], bool)
        and isinstance(p["allowed_capabilities"], list)
        and len(p["allowed_capabilities"]) <= MAX_DIRECTIVE_CAPABILITIES
        and all(_bounded_str(c) for c in p["allowed_capabilities"])
        and isinstance(p["reasons"], list)
        and len(p["reasons"]) <= MAX_DIRECTIVE_REASONS
        and all(_bounded_str(r) for r in p["reasons"])
    )


class SalienceBus:
    """Single-threaded by contract: callers that share a bus across threads
    must serialize access themselves (this package imports no threading — the
    discipline allowlist has none)."""

    def __init__(self, path=None):
        self._signals = []          # (hash, SalienceSignal)
        self._directives = []       # (hash, directive dict)
        self._entries = []          # ordered full entries, for chain verification
        self._head = ""
        self._path = path
        if path is not None and os.path.exists(path):
            self._replay(path)

    def _replay(self, path) -> None:
        """Rebuild state from an existing JSONL so a REOPENED bus continues its
        own chain. Without this, a second process (session resume, host
        restart) would append an entry with prev="" after the existing lines
        and permanently break `verify_chain()` at the junction.

        Verifies while loading and fails CLOSED: a corrupt, tampered,
        discontinuous, or key-smuggling record raises rather than silently
        appending after garbage — a bus you cannot trust to extend is a bus
        you must not extend.

        Scope limit (ADR 0001, unchanged): the head is derived FROM the file,
        so deletion of trailing lines (tail truncation) is indistinguishable
        from a shorter honest history and is accepted. Detecting it requires
        the externally-anchored head (`verify_chain(trusted_head)`) that ADR
        0001 defers."""
        with open(path, encoding="utf-8") as fh:
            lines = [ln for ln in (raw.strip() for raw in fh) if ln]
        prev = ""
        for i, line in enumerate(lines):
            try:
                e = json.loads(line)
                base = {"kind": e["kind"], "payload": e["payload"], "prev": e["prev"]}
                intact = (
                    # Exactly these keys: unknown keys sit OUTSIDE the digest
                    # base and would otherwise ride along unverified — a
                    # smuggling channel through the audit fence (Finding G).
                    set(e) == {"kind", "payload", "prev", "hash"}
                    and isinstance(e["payload"], dict)
                    and e["prev"] == prev
                    and digest(base) == e["hash"]
                )
            except Exception:  # noqa: BLE001 — any malformed line is corruption
                intact = False
            if not intact:
                raise ValueError(
                    f"corrupt or discontinuous bus record at line {i + 1}: {path}"
                )
            if e["kind"] == "signal":
                p = dict(e["payload"])
                # Validate the value the FILE carried, not a coerced view of
                # it: coercing before validation would accept shapes publish()
                # could never produce (a string split into char refs, dict
                # keys harvested as refs) and let a non-list escape as a
                # TypeError instead of the ValueError this loop guarantees.
                prov = p.get("provenance", [])
                if not isinstance(prov, list):
                    raise ValueError(
                        f"persisted signal fails validation at line {i + 1}: {path}"
                    )
                p["provenance"] = tuple(prov)
                try:
                    signal = SalienceSignal(**p)
                except TypeError:
                    signal = None
                if signal is None or not valid_signal(signal):
                    raise ValueError(
                        f"persisted signal fails validation at line {i + 1}: {path}"
                    )
                self._signals.append((e["hash"], signal))
            elif e["kind"] == "directive":
                if not _valid_directive_payload(e["payload"]):
                    raise ValueError(
                        f"persisted directive fails the audit fence at line {i + 1}: {path}"
                    )
                self._directives.append((e["hash"], e["payload"]))
            else:
                raise ValueError(
                    f"unknown entry kind at line {i + 1}: {path}"
                )
            self._entries.append(e)
            prev = e["hash"]
        self._head = prev

    def publish(self, signal) -> str:
        if not valid_signal(signal):
            raise TypeError("SalienceBus.publish accepts only a valid SalienceSignal")
        entry = {"kind": "signal", "payload": asdict(signal), "prev": self._head}
        return self._append(entry, ("signal", signal))

    def emit(self, directive) -> str:
        """Record a directive decision for a subject (the audit trail). Requires a
        Directive whose every string is ref-shaped and bounded — the directive
        half of the audit fence, so nothing prompt-sized can enter the durable
        record through this kind either. A well-formedness check, NOT an
        authorization check (see module docstring)."""
        if type(directive) is not Directive or not _valid_directive_shape(directive):
            raise TypeError("SalienceBus.emit accepts only a bounded, ref-shaped Directive")
        payload = {
            "subject": directive.subject,
            "policy_id": directive.policy_id,
            "compute_budget": directive.compute_budget,
            "verification_depth": directive.verification_depth,
            "retention_class": directive.retention_class,
            "routing_hint": directive.routing_hint,
            "adaptation_eligibility": directive.adaptation_eligibility.value,
            "adaptation_rationale": directive.adaptation_rationale.value,
            "allowed_capabilities": list(directive.allowed_capabilities),
            "reconfigure": directive.reconfigure.value,
            "interpreter_version": directive.interpreter_version,
            "reasons": list(directive.reasons),
        }
        entry = {"kind": "directive", "payload": payload, "prev": self._head}
        return self._append(entry, ("directive", payload))

    def signals_for(self, subject: str) -> tuple:
        return tuple(s for _, s in self._signals if isinstance(s, SalienceSignal) and s.subject == subject)

    def directives_for(self, subject: str) -> tuple:
        """Recorded directive payloads for a subject, oldest first, as
        json-round-trip COPIES — reading the audit record must not be able to
        mutate it (a shallow copy would alias the nested lists). Deliberately
        dicts, not reconstructed Directive objects: presence on the bus is NOT
        authorization (module docstring), and dicts are all a JSONL-loaded
        record could ever faithfully serve."""
        return tuple(json.loads(json.dumps(p)) for _, p in self._directives
                     if p.get("subject") == subject)

    def head(self) -> str:
        return self._head

    def verify_chain(self) -> bool:
        """Recompute the hash chain end to end: every entry's hash must match its
        content, its `prev` must be the previous entry's hash, and the last hash
        must be the head. Catches accidental corruption, reordering, and — for a
        bus whose head predates the damage (same process) — truncation of the
        durable record; "append-only" is then a checkable property, not merely
        the absence of a mutator method. Across a REOPEN the head is derived
        from the file, so tail truncation needs the externally-anchored head
        ADR 0001 defers. See the module docstring for the other exclusion
        (consistent malicious rewrite)."""
        prev = ""
        for e in self._entries:
            base = {"kind": e["kind"], "payload": e["payload"], "prev": e["prev"]}
            if e["prev"] != prev or digest(base) != e["hash"]:
                return False
            prev = e["hash"]
        return prev == self._head

    def _append(self, entry: dict, stored) -> str:
        entry_hash = digest(entry)
        entry = {**entry, "hash": entry_hash}
        kind, obj = stored
        (self._signals if kind == "signal" else self._directives).append((entry_hash, obj))
        self._entries.append(entry)
        self._head = entry_hash
        if self._path is not None:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry_hash
