"""The four mutation fixtures from spec §5, each run through the real
pipeline: a subprocess executor, host-side observation, and the composer.

1. Byte-flip post-write        → host re-hash disagrees          → FAILED
2. Exit-code laundering        → supervisor exit ≠ receipt claim → FAILED
3. Right bytes, wrong path     → write-set diff                  → not VERIFIED
4. CLAIM/WORLD separation drop → claim-only / smuggled claims    → never VERIFIED

Plus the coverage gate: for every obligation kind in the file.write contract,
some mutant must trip some check.
"""

import tempfile
import unittest
from pathlib import Path

from salienceos.verifier import Reason, Status, build_contract
from salienceos.verifier.evidence import WorldStore
from salienceos.verifier.observers import snapshot_tree
from tests.helpers import (
    honest_receipt,
    make_verifier,
    observe_world,
    run_write_tool,
    write_envelope,
)

CONTENT = "hello world"


class MutationFixtures(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self._tmp.name)
        self.verifier = make_verifier()

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, envelope, target_path=None, exit_code=0, claimed_exit=None,
             corrupt_after=None):
        """Execute, optionally mutate the world post-receipt, then observe."""
        pre = snapshot_tree(self.ws)
        result = run_write_tool(
            self.ws, target_path or envelope.args["path"], CONTENT, exit_code
        )
        receipt = honest_receipt(
            f"rcpt-{envelope.envelope_id}", envelope, CONTENT,
            exit_code=result.returncode, claimed_exit=claimed_exit,
        )
        if corrupt_after is not None:
            corrupt_after(self.ws)
        world = observe_world(envelope, self.ws, pre, result)
        return receipt, world

    def test_baseline_honest_run_verifies(self):
        envelope = write_envelope("env-m0", "out.txt", CONTENT)
        receipt, world = self._run(envelope)
        v = self.verifier.verify(envelope, receipt, world)
        self.assertIs(v.status, Status.VERIFIED)

    def test_fixture_1_byte_flip_post_write_fails(self):
        envelope = write_envelope("env-m1", "out.txt", CONTENT)

        def flip(ws):
            target = ws / "out.txt"
            data = bytearray(target.read_bytes())
            data[0] ^= 0xFF
            target.write_bytes(bytes(data))

        receipt, world = self._run(envelope, corrupt_after=flip)
        v = self.verifier.verify(envelope, receipt, world)
        self.assertIs(v.status, Status.FAILED)
        self.assertIn(Reason.CONCLUSIVE_CONTRADICTION, v.reasons)

    def test_fixture_2_exit_code_laundering_fails(self):
        # Child really exits 3; the receipt launders it to 0 and claims success.
        envelope = write_envelope("env-m2", "out.txt", CONTENT)
        receipt, world = self._run(envelope, exit_code=3, claimed_exit=0)
        v = self.verifier.verify(envelope, receipt, world)
        self.assertIs(v.status, Status.FAILED)
        self.assertIn(Reason.CONCLUSIVE_CONTRADICTION, v.reasons)

    def test_fixture_3_right_bytes_wrong_path_does_not_verify(self):
        # Correct bytes written to an unauthorized path; receipt claims the
        # declared path.
        envelope = write_envelope("env-m3", "out.txt", CONTENT)
        receipt, world = self._run(envelope, target_path="evil.txt")
        v = self.verifier.verify(envelope, receipt, world)
        self.assertIsNot(v.status, Status.VERIFIED)
        self.assertIs(v.status, Status.FAILED)

    def test_fixture_4_claim_only_never_verifies(self):
        envelope = write_envelope("env-m4", "out.txt", CONTENT)
        receipt, _world = self._run(envelope)
        v = self.verifier.verify(envelope, receipt, [])
        self.assertIs(v.status, Status.UNVERIFIED)
        self.assertIn(Reason.INTEGRITY_ATTESTED, v.reasons)

    def test_fixture_4_smuggled_claims_are_fenced(self):
        # The refactor risk: claim evidence offered on the world channel.
        envelope = write_envelope("env-m5", "out.txt", CONTENT)
        receipt, _world = self._run(envelope)

        from salienceos.verifier import claims_from_receipt

        smuggled = claims_from_receipt(receipt, authentic=True)
        with self.assertRaises(TypeError):
            WorldStore().extend(smuggled)

        # Even bypassing the store, the composer's own fence drops them.
        from salienceos.verifier import compose

        contract = build_contract(envelope)
        v = compose(contract, smuggled, smuggled, envelope.stakes)
        self.assertIs(v.status, Status.UNVERIFIED)
        self.assertIn(Reason.TYPE_FENCE, v.reasons)

    def test_coverage_gate_every_obligation_kind_trips_some_mutant(self):
        """Spec §5: for every named invariant in the contract, some mutant
        must turn some check red; a mutant that stays green is a verifier bug."""
        tripped = set()

        cases = [
            ("env-g1", dict(corrupt_after=self._flip_out)),   # artifact_hash
            ("env-g2", dict(exit_code=3, claimed_exit=0)),    # exit_status
            ("env-g3", dict(target_path="evil.txt")),         # write_set
        ]
        for envelope_id, kwargs in cases:
            envelope = write_envelope(envelope_id, "out.txt", CONTENT)
            receipt, world = self._run(envelope, **kwargs)
            verifier = make_verifier()
            v = verifier.verify(envelope, receipt, world)
            self.assertIs(v.status, Status.FAILED, f"mutant {envelope_id} stayed green")
            contract = build_contract(envelope)
            for o in contract.obligations:
                if any(o.obligation_id in d for d in v.details):
                    tripped.add(o.kind)

        contract_kinds = {o.kind for o in build_contract(
            write_envelope("env-g0", "out.txt", CONTENT)).obligations}
        # Compare kinds (obligation ids embed the envelope id, kinds are stable).
        self.assertEqual(tripped, contract_kinds)

    @staticmethod
    def _flip_out(ws):
        target = ws / "out.txt"
        data = bytearray(target.read_bytes())
        data[0] ^= 0xFF
        target.write_bytes(bytes(data))


if __name__ == "__main__":
    unittest.main()
