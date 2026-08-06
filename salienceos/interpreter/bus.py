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
from dataclasses import asdict

from salienceos.interpreter.directive import Directive
from salienceos.interpreter.signal import SalienceSignal, valid_signal
from salienceos.verifier.signing import digest


class SalienceBus:
    def __init__(self, path=None):
        self._signals = []          # (hash, SalienceSignal)
        self._directives = []       # (hash, directive dict)
        self._entries = []          # ordered full entries, for chain verification
        self._head = ""
        self._path = path

    def publish(self, signal) -> str:
        if not valid_signal(signal):
            raise TypeError("SalienceBus.publish accepts only a valid SalienceSignal")
        entry = {"kind": "signal", "payload": asdict(signal), "prev": self._head}
        return self._append(entry, ("signal", signal))

    def emit(self, directive) -> str:
        """Record a directive decision for a subject (the audit trail). Requires a
        Directive so malformed entries cannot corrupt the record; note this is a
        well-formedness check, NOT an authorization check (see module docstring)."""
        if type(directive) is not Directive:
            raise TypeError("SalienceBus.emit accepts only a Directive")
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

    def head(self) -> str:
        return self._head

    def verify_chain(self) -> bool:
        """Recompute the hash chain end to end: every entry's hash must match its
        content, its `prev` must be the previous entry's hash, and the last hash
        must be the head. Catches accidental corruption, truncation, and
        reordering of the durable record — "append-only" is then a checkable
        property, not merely the absence of a mutator method. See the module
        docstring for what this does NOT prove (consistent malicious rewrite)."""
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
