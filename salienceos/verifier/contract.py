"""ObligationBuilder — envelope-derived expectations + the minimum-obligation
floor (spec M3).

The floor per action class cannot be dropped by any contract:
  1. independent exit observation        (kind: exit_status)
  2. independent artifact/side-effect    (kind: artifact_hash or path_state)
  3. boundary check                      (kind: write_set)
The builder always emits the floor, and the composer independently rejects any
contract missing it (defense in depth against a weakened builder).

Expectations come from the authorized args: `file.write(path, content)` ⇒
expected artifact hash = hash(content); `dir.make(p)` ⇒ p is a directory.
An expectation of None means "no deterministic oracle from args" — the
obligation is then met only by claim/world *agreement* across distinct
failure modes (e.g. shell.run declared outputs: receipt hash vs host re-hash).

Missing or unknown op ⇒ no contract ⇒ UNVERIFIED, never VERIFIED.
"""

import json
from dataclasses import dataclass

from salienceos.verifier.envelope import ActionEnvelope
from salienceos.verifier.signing import sha256_bytes

CONTRACT_VERSION = "contract/0.1.0"

# Floor: exit observation, boundary check, and at least one side-effect kind.
FLOOR_KINDS = ("exit_status", "write_set")
SIDE_EFFECT_KINDS = ("artifact_hash", "path_state")


@dataclass(frozen=True)
class Obligation:
    obligation_id: str
    kind: str  # exit_status | artifact_hash | write_set | path_state
    subject: str  # path for artifact/path obligations; "" otherwise
    expectation: object  # str expected value, or None => two-source agreement
    floor: bool


@dataclass(frozen=True)
class Contract:
    envelope_id: str
    action_class: str
    obligations: tuple
    version: str = CONTRACT_VERSION


def write_set_value(paths) -> str:
    """Canonical encoding of an observed or declared write set."""
    return json.dumps(sorted(set(paths)), separators=(",", ":"))


def obligation_id(envelope_id: str, kind: str, subject: str = "") -> str:
    return f"{envelope_id}:{kind}:{subject}" if subject else f"{envelope_id}:{kind}"


def build_contract(envelope: ActionEnvelope):
    """Derive the obligation contract from a policy-signed envelope.

    Returns None for ops with no registered derivation — the composer then
    fails closed to UNVERIFIED(NO_CONTRACT).
    """
    builder = _OP_BUILDERS.get(envelope.op)
    if builder is None:
        return None
    try:
        obligations = builder(envelope)
    except (KeyError, TypeError, AttributeError, ValueError):
        # Malformed args (missing key, wrong type, non-iterable outputs, …):
        # fail closed to no contract, never a partial one.
        return None
    return Contract(
        envelope_id=envelope.envelope_id,
        action_class=envelope.action_class,
        obligations=tuple(obligations),
    )


def _floor(envelope: ActionEnvelope, declared_paths, expected_exit: str = "0"):
    eid = envelope.envelope_id
    return [
        Obligation(obligation_id(eid, "exit_status"), "exit_status", "", expected_exit, True),
        Obligation(
            obligation_id(eid, "write_set"),
            "write_set",
            "",
            write_set_value(declared_paths),
            True,
        ),
    ]


def _build_file_write(envelope: ActionEnvelope):
    path = envelope.args["path"]
    if "content_sha256" in envelope.args:
        expected_hash = envelope.args["content_sha256"]
    else:
        expected_hash = sha256_bytes(envelope.args["content"].encode("utf-8"))
    obligations = _floor(envelope, [path])
    obligations.append(
        Obligation(
            obligation_id(envelope.envelope_id, "artifact_hash", path),
            "artifact_hash",
            path,
            expected_hash,
            True,
        )
    )
    return obligations


def _build_dir_make(envelope: ActionEnvelope):
    path = envelope.args["path"]
    obligations = _floor(envelope, [path])
    obligations.append(
        Obligation(
            obligation_id(envelope.envelope_id, "path_state", path),
            "path_state",
            path,
            "present:dir",
            True,
        )
    )
    return obligations


def _build_file_delete(envelope: ActionEnvelope):
    path = envelope.args["path"]
    obligations = _floor(envelope, [path])
    obligations.append(
        Obligation(
            obligation_id(envelope.envelope_id, "path_state", path),
            "path_state",
            path,
            "absent",
            True,
        )
    )
    return obligations


def _build_shell_run(envelope: ActionEnvelope):
    declared_outputs = list(envelope.args["declared_outputs"])
    expected_exit = str(envelope.args.get("expected_exit", 0))
    obligations = _floor(envelope, declared_outputs, expected_exit)
    for path in declared_outputs:
        # No deterministic oracle for command output bytes: expectation None,
        # met only by receipt-hash vs host-rehash agreement.
        obligations.append(
            Obligation(
                obligation_id(envelope.envelope_id, "artifact_hash", path),
                "artifact_hash",
                path,
                None,
                True,
            )
        )
    return obligations


_OP_BUILDERS = {
    "file.write": _build_file_write,
    "dir.make": _build_dir_make,
    "file.delete": _build_file_delete,
    "shell.run": _build_shell_run,
}
