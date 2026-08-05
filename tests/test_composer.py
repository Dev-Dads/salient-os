"""Golden fixtures for the pure composer: (claim_ev, world_ev) → verdict.

These are the spec-M4 mutation targets: every fail-closed early exit, the
agreement rule, the stakes scaling, and the distinct-failure-mode rule each
have a fixture that pins the expected verdict.
"""

import unittest

from salienceos.verifier import (
    ClaimEvidence,
    Contract,
    Obligation,
    Reason,
    Stakes,
    Status,
    WorldEvidence,
    build_contract,
    claims_from_receipt,
    compose,
)
from salienceos.verifier.contract import obligation_id
from salienceos.verifier.signing import sha256_bytes
from tests.helpers import honest_receipt, write_envelope

CONTENT = "hello world"
HASH = sha256_bytes(CONTENT.encode("utf-8"))


def world_for(envelope, kind, value, subject="", channel="host.test", failure_mode="host_observed"):
    return WorldEvidence(
        obligation_id=obligation_id(envelope.envelope_id, kind, subject),
        kind=kind,
        value=value,
        failure_mode=failure_mode,
        channel=channel,
        provenance="test",
    )


def full_world(envelope, artifact_hash=HASH, exit_value="0", write_set='["out.txt"]'):
    return [
        world_for(envelope, "exit_status", exit_value, failure_mode="supervisor_exit",
                  channel="host.supervisor"),
        world_for(envelope, "artifact_hash", artifact_hash, subject="out.txt",
                  failure_mode="host_rehash", channel="host.rehash"),
        world_for(envelope, "write_set", write_set, failure_mode="host_snapshot_diff",
                  channel="host.snapshot"),
    ]


class ComposerFailClosed(unittest.TestCase):
    def setUp(self):
        self.envelope = write_envelope("env-c1", "out.txt", CONTENT)
        self.contract = build_contract(self.envelope)
        self.receipt = honest_receipt("rcpt-c1", self.envelope, CONTENT)
        self.claims = claims_from_receipt(self.receipt, authentic=True)

    def test_missing_contract_is_unverified(self):
        v = compose(None, self.claims, full_world(self.envelope), Stakes.NORMAL)
        self.assertIs(v.status, Status.UNVERIFIED)
        self.assertIn(Reason.NO_CONTRACT, v.reasons)

    def test_empty_contract_is_unverified(self):
        empty = Contract(envelope_id="env-c1", action_class="x", obligations=())
        v = compose(empty, self.claims, full_world(self.envelope), Stakes.NORMAL)
        self.assertIs(v.status, Status.UNVERIFIED)
        self.assertIn(Reason.NO_CONTRACT, v.reasons)

    def test_contract_without_floor_is_unverified(self):
        # A contract that dropped the exit and boundary obligations must not verify.
        gutted = Contract(
            envelope_id="env-c1",
            action_class="x",
            obligations=(
                Obligation(obligation_id("env-c1", "artifact_hash", "out.txt"),
                           "artifact_hash", "out.txt", HASH, True),
            ),
        )
        v = compose(gutted, self.claims, full_world(self.envelope), Stakes.NORMAL)
        self.assertIs(v.status, Status.UNVERIFIED)
        self.assertIn(Reason.MISSING_FLOOR, v.reasons)

    def test_unsigned_stakes_type_is_unverified(self):
        v = compose(self.contract, self.claims, full_world(self.envelope), "high")
        self.assertIs(v.status, Status.UNVERIFIED)
        self.assertIn(Reason.INVALID_STAKES, v.reasons)

    def test_claim_only_is_unverified_with_integrity_attested(self):
        v = compose(self.contract, self.claims, [], Stakes.NORMAL)
        self.assertIs(v.status, Status.UNVERIFIED)
        self.assertIn(Reason.INTEGRITY_ATTESTED, v.reasons)
        self.assertIsNone(v.require_attested())

    def test_claim_only_unauthentic_receipt_gets_no_attestation(self):
        claims = claims_from_receipt(self.receipt, authentic=False)
        v = compose(self.contract, claims, [], Stakes.NORMAL)
        self.assertIs(v.status, Status.UNVERIFIED)
        self.assertNotIn(Reason.INTEGRITY_ATTESTED, v.reasons)


class ComposerAgreement(unittest.TestCase):
    def setUp(self):
        self.envelope = write_envelope("env-c2", "out.txt", CONTENT)
        self.contract = build_contract(self.envelope)
        self.receipt = honest_receipt("rcpt-c2", self.envelope, CONTENT)
        self.claims = claims_from_receipt(self.receipt, authentic=True)

    def test_full_agreement_verifies(self):
        v = compose(self.contract, self.claims, full_world(self.envelope), Stakes.NORMAL)
        self.assertIs(v.status, Status.VERIFIED)

    def test_world_sharing_claim_failure_mode_does_not_verify(self):
        # World facts whose failure mode duplicates the claim's are not
        # independent corroboration.
        correlated = [
            world_for(self.envelope, "exit_status", "0", failure_mode="executor_self_report"),
            world_for(self.envelope, "artifact_hash", HASH, subject="out.txt",
                      failure_mode="executor_self_report"),
            world_for(self.envelope, "write_set", '["out.txt"]',
                      failure_mode="executor_self_report"),
        ]
        v = compose(self.contract, self.claims, correlated, Stakes.NORMAL)
        self.assertIs(v.status, Status.UNVERIFIED)
        self.assertIn(Reason.NO_DISTINCT_FAILURE_MODE, v.reasons)

    def test_high_stakes_requires_two_world_channels(self):
        envelope = write_envelope("env-c3", "out.txt", CONTENT, stakes=Stakes.HIGH)
        contract = build_contract(envelope)
        receipt = honest_receipt("rcpt-c3", envelope, CONTENT)
        claims = claims_from_receipt(receipt, authentic=True)

        one_channel = full_world(envelope)
        v = compose(contract, claims, one_channel, Stakes.HIGH)
        self.assertIs(v.status, Status.UNVERIFIED)
        self.assertIn(Reason.INSUFFICIENT_CHANNELS, v.reasons)

        second_source = [
            world_for(envelope, "exit_status", "0", failure_mode="audit_log",
                      channel="host.audit"),
            world_for(envelope, "artifact_hash", HASH, subject="out.txt",
                      failure_mode="mirror_read", channel="host.mirror"),
            world_for(envelope, "write_set", '["out.txt"]', failure_mode="audit_log",
                      channel="host.audit"),
        ]
        v2 = compose(contract, claims, one_channel + second_source, Stakes.HIGH)
        self.assertIs(v2.status, Status.VERIFIED)

    def test_two_source_agreement_without_expectation(self):
        # shell.run declared outputs have no envelope-derived hash; the
        # obligation is met by receipt-hash vs host-rehash agreement alone.
        from salienceos.verifier import issue_envelope, issue_receipt
        from tests.helpers import EXECUTOR_ID, EXECUTOR_KEY, POLICY_KEY

        envelope = issue_envelope(
            "env-c4", "shell.run",
            {"command": "make build", "declared_outputs": ["build.log"]},
            "project_mutation", Stakes.NORMAL, "policy-0.1.0", POLICY_KEY,
        )
        contract = build_contract(envelope)
        receipt = issue_receipt(
            "rcpt-c4", "env-c4", 0, {"build.log": HASH}, ("build.log",), True,
            EXECUTOR_ID, EXECUTOR_KEY,
        )
        claims = claims_from_receipt(receipt, authentic=True)

        agreeing = [
            world_for(envelope, "exit_status", "0", failure_mode="supervisor_exit",
                      channel="host.supervisor"),
            world_for(envelope, "artifact_hash", HASH, subject="build.log",
                      failure_mode="host_rehash", channel="host.rehash"),
            world_for(envelope, "write_set", '["build.log"]',
                      failure_mode="host_snapshot_diff", channel="host.snapshot"),
        ]
        v = compose(contract, claims, agreeing, Stakes.NORMAL)
        self.assertIs(v.status, Status.VERIFIED)

        disagreeing = [
            world_for(envelope, "artifact_hash", "0" * 64, subject="build.log",
                      failure_mode="host_rehash", channel="host.rehash"),
        ]
        v2 = compose(contract, claims, disagreeing, Stakes.NORMAL)
        self.assertIs(v2.status, Status.FAILED)


class ComposerContradiction(unittest.TestCase):
    def setUp(self):
        self.envelope = write_envelope("env-c5", "out.txt", CONTENT)
        self.contract = build_contract(self.envelope)

    def test_honest_failure_report_fails(self):
        # Executor honestly reports exit 1 against an expectation of 0.
        receipt = honest_receipt("rcpt-c5", self.envelope, CONTENT, exit_code=1,
                                 reported_success=False)
        claims = claims_from_receipt(receipt, authentic=True)
        v = compose(self.contract, claims, [], Stakes.NORMAL)
        self.assertIs(v.status, Status.FAILED)
        self.assertIn(Reason.CONCLUSIVE_CONTRADICTION, v.reasons)


if __name__ == "__main__":
    unittest.main()
