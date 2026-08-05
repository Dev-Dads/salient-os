"""The two leak-locks (spec M5): the sealed consumer gate and the
no-truth-value verdict, plus require_attested() semantics."""

import unittest

from salienceos.verifier import (
    NotAttestedError,
    Reason,
    ReceiptStore,
    SealedGateError,
    Status,
    Verdict,
)
from salienceos.verifier.composer import COMPOSER_VERSION
from tests.helpers import honest_receipt, write_envelope


def verdict(status, reasons=()):
    return Verdict(status=status, reasons=tuple(reasons), composer_version=COMPOSER_VERSION)


class SealedConsumerGate(unittest.TestCase):
    def setUp(self):
        self.envelope = write_envelope("env-g", "out.txt", "hello world")
        self.store = ReceiptStore()

    def test_reported_success_with_unverified_is_rejected(self):
        receipt = honest_receipt("rcpt-g1", self.envelope, "hello world",
                                 reported_success=True)
        attested = verdict(Status.UNVERIFIED, [Reason.INTEGRITY_ATTESTED])
        with self.assertRaises(SealedGateError):
            self.store.record(receipt, attested)
        self.assertEqual(self.store.rows(), ())

    def test_reported_success_with_failed_is_rejected(self):
        receipt = honest_receipt("rcpt-g2", self.envelope, "hello world",
                                 reported_success=True)
        with self.assertRaises(SealedGateError):
            self.store.record(receipt, verdict(Status.FAILED,
                                               [Reason.CONCLUSIVE_CONTRADICTION]))

    def test_verified_success_is_recordable(self):
        receipt = honest_receipt("rcpt-g3", self.envelope, "hello world",
                                 reported_success=True)
        self.store.record(receipt, verdict(Status.VERIFIED))
        self.assertEqual(len(self.store.rows()), 1)

    def test_honest_failure_is_recordable(self):
        receipt = honest_receipt("rcpt-g4", self.envelope, "hello world",
                                 exit_code=1, reported_success=False)
        self.store.record(receipt, verdict(Status.FAILED,
                                           [Reason.CONCLUSIVE_CONTRADICTION]))
        self.assertEqual(len(self.store.rows()), 1)


class VerdictSurface(unittest.TestCase):
    def test_verdict_has_no_truth_value(self):
        v = verdict(Status.VERIFIED)
        with self.assertRaises(TypeError):
            bool(v)
        with self.assertRaises(TypeError):
            if v:  # pragma: no cover - the raise is the assertion
                pass

    def test_require_attested_only_on_the_exact_subcode(self):
        ok = verdict(Status.UNVERIFIED, [Reason.INTEGRITY_ATTESTED])
        self.assertIsNone(ok.require_attested())

        for bad in (
            verdict(Status.VERIFIED),
            verdict(Status.FAILED, [Reason.CONCLUSIVE_CONTRADICTION]),
            verdict(Status.UNVERIFIED, [Reason.NO_WORLD_FACT]),
        ):
            with self.assertRaises(NotAttestedError):
                bad.require_attested()


if __name__ == "__main__":
    unittest.main()
