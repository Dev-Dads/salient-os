"""ReceiptIngress — the executor's signed claims — and the sealed consumer gate.

Everything a receipt asserts becomes ClaimEvidence and only ClaimEvidence:
the executor wrote it, so it can never count as the executor-independent
world-side fact (spec M1).

The sealed consumer gate (spec M5): the receipt store rejects any row where
reported_success is true and the verdict is not VERIFIED, so an operator or a
downstream consumer cannot quietly map "attested" (or any UNVERIFIED) to
"success".
"""

from dataclasses import dataclass

from salienceos.verifier.contract import obligation_id, write_set_value
from salienceos.verifier.evidence import ClaimEvidence
from salienceos.verifier.signing import sign, signature_valid
from salienceos.verifier.verdict import Status, Verdict

CLAIM_FAILURE_MODE = "executor_self_report"
CLAIM_CHANNEL = "receipt"


@dataclass(frozen=True)
class ExecutionReceipt:
    receipt_id: str
    envelope_id: str
    exit_code: int
    artifact_hashes: dict  # declared path -> claimed sha256
    write_set: tuple  # declared changed paths
    reported_success: bool
    executor_id: str
    signature: str

    def signed_payload(self) -> dict:
        return {
            "receipt_id": self.receipt_id,
            "envelope_id": self.envelope_id,
            "exit_code": self.exit_code,
            "artifact_hashes": self.artifact_hashes,
            "write_set": list(self.write_set),
            "reported_success": self.reported_success,
            "executor_id": self.executor_id,
        }


def issue_receipt(
    receipt_id: str,
    envelope_id: str,
    exit_code: int,
    artifact_hashes: dict,
    write_set,
    reported_success: bool,
    executor_id: str,
    executor_key: bytes,
) -> ExecutionReceipt:
    payload = {
        "receipt_id": receipt_id,
        "envelope_id": envelope_id,
        "exit_code": exit_code,
        "artifact_hashes": artifact_hashes,
        "write_set": list(write_set),
        "reported_success": reported_success,
        "executor_id": executor_id,
    }
    return ExecutionReceipt(
        receipt_id=receipt_id,
        envelope_id=envelope_id,
        exit_code=exit_code,
        artifact_hashes=artifact_hashes,
        write_set=tuple(write_set),
        reported_success=reported_success,
        executor_id=executor_id,
        signature=sign(payload, executor_key),
    )


def receipt_authentic(receipt: ExecutionReceipt, executor_key: bytes) -> bool:
    return signature_valid(receipt.signed_payload(), receipt.signature, executor_key)


def claims_from_receipt(receipt: ExecutionReceipt, authentic: bool):
    """Translate a receipt into CLAIM evidence keyed to the contract's
    obligation ids. Includes a receipt_integrity claim so the composer can
    attach the INTEGRITY_ATTESTED sub-code without doing I/O itself."""
    eid = receipt.envelope_id
    claims = [
        ClaimEvidence(
            obligation_id=obligation_id(eid, "exit_status"),
            kind="exit_status",
            value=str(receipt.exit_code),
            failure_mode=CLAIM_FAILURE_MODE,
            channel=CLAIM_CHANNEL,
            provenance=receipt.receipt_id,
        ),
        ClaimEvidence(
            obligation_id=obligation_id(eid, "write_set"),
            kind="write_set",
            value=write_set_value(receipt.write_set),
            failure_mode=CLAIM_FAILURE_MODE,
            channel=CLAIM_CHANNEL,
            provenance=receipt.receipt_id,
        ),
        ClaimEvidence(
            obligation_id=obligation_id(eid, "receipt_integrity"),
            kind="receipt_integrity",
            value="authentic" if authentic else "unauthentic",
            failure_mode=CLAIM_FAILURE_MODE,
            channel=CLAIM_CHANNEL,
            provenance=receipt.receipt_id,
        ),
    ]
    for path, claimed_hash in sorted(receipt.artifact_hashes.items()):
        claims.append(
            ClaimEvidence(
                obligation_id=obligation_id(eid, "artifact_hash", path),
                kind="artifact_hash",
                value=claimed_hash,
                failure_mode=CLAIM_FAILURE_MODE,
                channel=CLAIM_CHANNEL,
                provenance=receipt.receipt_id,
            )
        )
    return claims


class SealedGateError(Exception):
    """reported_success=true with a non-VERIFIED verdict is unrecordable."""


class ReceiptStore:
    """Append-only receipt log guarded by the sealed consumer gate."""

    def __init__(self):
        self._rows = []

    def record(self, receipt: ExecutionReceipt, verdict: Verdict) -> None:
        if not isinstance(verdict, Verdict):
            raise SealedGateError("a Verdict is required to record a receipt")
        if receipt.reported_success and verdict.status is not Status.VERIFIED:
            raise SealedGateError(
                f"receipt {receipt.receipt_id} reports success but verdict is "
                f"{verdict.status.value}; refusing to record"
            )
        self._rows.append((receipt, verdict))

    def rows(self) -> tuple:
        return tuple(self._rows)
