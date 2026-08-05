"""Pipeline glue: ReceiptIngress → ObligationBuilder → EvidenceLog →
VerdictComposer, per spec §2.

Synchronous and side-effect-free with respect to the system under
verification: the Verifier holds no policy-grant handle, writes no memory,
and only appends to its own evidence logs. The composer stays pure; this
module is the only place I/O-adjacent steps (signature checks, store
appends) happen.
"""

from dataclasses import replace

from salienceos.verifier.composer import COMPOSER_VERSION, compose
from salienceos.verifier.contract import build_contract
from salienceos.verifier.envelope import ActionEnvelope, max_stakes, verify_envelope
from salienceos.verifier.evidence import ClaimStore, WorldStore
from salienceos.verifier.receipt import (
    ExecutionReceipt,
    claims_from_receipt,
    receipt_authentic,
)
from salienceos.verifier.verdict import Reason, Status, Verdict


class Verifier:
    def __init__(self, policy_key: bytes, executor_keys: dict, claim_store=None, world_store=None):
        self._policy_key = policy_key
        self._executor_keys = dict(executor_keys)
        self.claim_store = claim_store if claim_store is not None else ClaimStore()
        self.world_store = world_store if world_store is not None else WorldStore()

    def verify(self, envelope: ActionEnvelope, receipt: ExecutionReceipt, world_evidence,
               escalate_to=None) -> Verdict:
        """Fold ONE action attempt's receipt claims and world observations into a verdict.

        The verdict is composed over *this attempt's* evidence only — never the
        accumulated store history. The stores remain an append-only audit log;
        composing over them would let a stale WORLD fact from an earlier attempt
        on the same envelope corroborate a fresh, unobserved receipt (a false
        VERIFIED on receipt replay). Evidence is therefore built locally and
        passed straight to the pure composer.

        `escalate_to` is an UPWARD-only stakes floor (default None). The effective
        stakes is max(envelope.stakes, escalate_to), so a salience-driven caller
        (the control seam) can demand STRICTER verification than the envelope
        signed, but can never lower it below the policy-signed floor.

        Chain-of-custody is explicit: a receipt whose envelope_id does not match
        the envelope under verification is a broken binding and fails closed to
        UNVERIFIED before any contract is consulted.

        Other fail-closed paths: a bad policy signature yields no contract
        (→ UNVERIFIED); an unauthentic receipt still contributes claims, but its
        integrity claim reads "unauthentic", so INTEGRITY_ATTESTED can never
        attach to it.
        """
        world_evidence = tuple(world_evidence)
        effective_stakes = max_stakes(envelope.stakes, escalate_to)

        if receipt.envelope_id != envelope.envelope_id:
            # Record for audit, then fail closed — do not compose across a
            # broken receipt/envelope binding.
            self.claim_store.extend(claims_from_receipt(receipt, authentic=False))
            self.world_store.extend(world_evidence)
            return Verdict(
                status=Status.UNVERIFIED,
                reasons=(Reason.RECEIPT_ENVELOPE_MISMATCH,),
                details=(
                    f"receipt envelope_id {receipt.envelope_id!r} != "
                    f"envelope {envelope.envelope_id!r}",
                ),
                composer_version=COMPOSER_VERSION,
                envelope_id=envelope.envelope_id,
                effective_stakes=effective_stakes,
            )

        if verify_envelope(envelope, self._policy_key):
            contract = build_contract(envelope)
        else:
            contract = None

        executor_key = self._executor_keys.get(receipt.executor_id)
        authentic = executor_key is not None and receipt_authentic(receipt, executor_key)

        this_claims = claims_from_receipt(receipt, authentic)

        # Append to the append-only audit stores...
        self.claim_store.extend(this_claims)
        self.world_store.extend(world_evidence)

        # ...but compose over THIS attempt's evidence only, at the effective
        # (upward-only escalated) stakes, then stamp the verdict's provenance so a
        # downstream gate need not be told the envelope id / effective stakes
        # separately (which could be desynced from the verdict).
        verdict = compose(contract, tuple(this_claims), world_evidence, effective_stakes)
        return replace(verdict, envelope_id=envelope.envelope_id, effective_stakes=effective_stakes)
