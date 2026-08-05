"""Regression fixtures for the red-team findings (red-team/00-REDTEAM-SYNTHESIS.md).

Each test is written so it FAILS against the pre-fix code — the mutation
discipline the spec §5 requires: a fixture that cannot reach the wrong answer
proves nothing.

  C1 — stale accumulated evidence → false VERIFIED on Verifier reuse   (HIGH)
  C1b — receipt/envelope mismatch fails closed                          (bind check)
  C2 — dir.make / file.delete verify end-to-end and their mutants FAIL  (MED)
  C3 — observers refuse to read outside the workspace root              (MED)
  C4 — build_contract fails closed (no crash) on malformed args         (LOW)
  C5 — INTEGRITY_ATTESTED not attached when world present but short     (LOW)
  C6 — high-stakes two-source needs distinct FAILURE MODES, not channels (LOW→MED)
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

from salienceos.verifier import (
    Reason,
    Stakes,
    Status,
    build_contract,
    claims_from_receipt,
    compose,
    issue_envelope,
    issue_receipt,
)
from salienceos.verifier.contract import obligation_id
from salienceos.verifier.evidence import WorldEvidence
from salienceos.verifier.observers import (
    artifact_evidence,
    observe_action,
    path_state,
    rehash,
    run_supervised,
    snapshot_tree,
)
from salienceos.verifier.signing import sha256_bytes
from tests.helpers import (
    EXECUTOR_ID,
    EXECUTOR_KEY,
    POLICY_KEY,
    honest_receipt,
    make_verifier,
    observe_world,
    run_write_tool,
    write_envelope,
)

CONTENT = "hello world"


# --------------------------------------------------------------------------- C1
class StaleEvidence(unittest.TestCase):
    """The one confirmed false VERIFIED: re-verifying an envelope must not reuse
    a prior attempt's world facts."""

    def test_reuse_with_empty_world_is_not_verified(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            v = make_verifier()
            env = write_envelope("env-reuse", "out.txt", CONTENT)

            pre = snapshot_tree(ws)
            res = run_write_tool(ws, "out.txt", CONTENT, 0)
            r1 = honest_receipt("rcpt-1", env, CONTENT)
            first = v.verify(env, r1, observe_world(env, ws, pre, res))
            self.assertIs(first.status, Status.VERIFIED)

            # Same envelope_id, fresh success receipt, NO fresh observation,
            # file even removed. Must NOT ride the stale world fact.
            (ws / "out.txt").unlink()
            r2 = honest_receipt("rcpt-2", env, CONTENT, reported_success=True)
            second = v.verify(env, r2, [])
            self.assertIsNot(second.status, Status.VERIFIED)
            self.assertIn(Reason.INTEGRITY_ATTESTED, second.reasons)

    def test_receipt_envelope_mismatch_fails_closed(self):
        v = make_verifier()
        env = write_envelope("env-A", "out.txt", CONTENT)
        receipt_for_B = honest_receipt("rcpt-B", write_envelope("env-B", "out.txt", CONTENT), CONTENT)
        verdict = v.verify(env, receipt_for_B, [])
        self.assertIs(verdict.status, Status.UNVERIFIED)
        self.assertIn(Reason.RECEIPT_ENVELOPE_MISMATCH, verdict.reasons)


# --------------------------------------------------------------------------- C2
class DirAndDeleteOps(unittest.TestCase):
    """dir.make / file.delete must verify honestly and their mutants must FAIL."""

    def _envelope(self, op, path, eid):
        return issue_envelope(eid, op, {"path": path}, "project_mutation",
                              Stakes.NORMAL, "policy-0.1.0", POLICY_KEY)

    def test_dir_make_verifies(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            env = self._envelope("dir.make", "foo", "env-mk")
            pre = snapshot_tree(ws)
            (ws / "foo").mkdir()
            res = run_supervised([sys.executable, "-c", "pass"], cwd=ws)
            receipt = issue_receipt("rcpt-mk", "env-mk", res.returncode, {}, ("foo",),
                                    True, EXECUTOR_ID, EXECUTOR_KEY)
            world = observe_action(env, ws, pre, res)
            v = make_verifier().verify(env, receipt, world)
            self.assertIs(v.status, Status.VERIFIED)

    def test_dir_make_wrong_path_state_fails(self):
        # Honest write-set/exit, but the directory was never actually created.
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            env = self._envelope("dir.make", "foo", "env-mk2")
            pre = snapshot_tree(ws)
            # do NOT create foo; fabricate an agreeing write-set claim
            res = run_supervised([sys.executable, "-c", "pass"], cwd=ws)
            receipt = issue_receipt("rcpt-mk2", "env-mk2", res.returncode, {}, ("foo",),
                                    True, EXECUTOR_ID, EXECUTOR_KEY)
            world = observe_action(env, ws, pre, res)  # path_state observes "absent"
            v = make_verifier().verify(env, receipt, world)
            self.assertIs(v.status, Status.FAILED)

    def test_file_delete_verifies(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            (ws / "gone.txt").write_text("bye")
            env = self._envelope("file.delete", "gone.txt", "env-del")
            pre = snapshot_tree(ws)
            (ws / "gone.txt").unlink()
            res = run_supervised([sys.executable, "-c", "pass"], cwd=ws)
            receipt = issue_receipt("rcpt-del", "env-del", res.returncode, {}, ("gone.txt",),
                                    True, EXECUTOR_ID, EXECUTOR_KEY)
            world = observe_action(env, ws, pre, res)
            v = make_verifier().verify(env, receipt, world)
            self.assertIs(v.status, Status.VERIFIED)

    def test_file_delete_still_present_fails(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            (ws / "gone.txt").write_text("bye")
            env = self._envelope("file.delete", "gone.txt", "env-del2")
            pre = snapshot_tree(ws)
            # do NOT delete; claim we did
            res = run_supervised([sys.executable, "-c", "pass"], cwd=ws)
            receipt = issue_receipt("rcpt-del2", "env-del2", res.returncode, {}, ("gone.txt",),
                                    True, EXECUTOR_ID, EXECUTOR_KEY)
            world = observe_action(env, ws, pre, res)  # path_state "present:file", write-set []
            v = make_verifier().verify(env, receipt, world)
            self.assertIs(v.status, Status.FAILED)


# --------------------------------------------------------------------------- C3
class WorkspaceEscape(unittest.TestCase):
    """Observers must not read outside the workspace via absolute/../symlink paths."""

    def test_absolute_path_reads_as_absent(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(rehash(d, "/etc/passwd"), "absent")
            self.assertEqual(path_state(d, "/etc/passwd"), "absent")

    def test_parent_escape_reads_as_absent(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d) / "inner"
            ws.mkdir()
            (Path(d) / "secret.txt").write_text("top secret")
            self.assertEqual(rehash(ws, "../secret.txt"), "absent")

    def test_symlink_escape_reads_as_absent(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d) / "inner"
            ws.mkdir()
            outside = Path(d) / "secret.txt"
            outside.write_text("top secret")
            link = ws / "link.txt"
            try:
                os.symlink(outside, link)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks not permitted in this environment")
            # The declared path is a symlink pointing outside the workspace:
            # must not be followed into `outside`.
            self.assertEqual(rehash(ws, "link.txt"), "absent")

    def test_escaping_artifact_evidence_cannot_verify(self):
        # An authorized-but-escaping write path can never produce VERIFIED,
        # because its re-hash is "absent" and contradicts the content hash.
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            env = issue_envelope("env-esc", "file.write",
                                 {"path": "/etc/passwd", "content": CONTENT},
                                 "project_mutation", Stakes.NORMAL, "p", POLICY_KEY)
            contract = build_contract(env)
            receipt = issue_receipt("rcpt-esc", "env-esc", 0,
                                    {"/etc/passwd": sha256_bytes(CONTENT.encode())},
                                    ("/etc/passwd",), True, EXECUTOR_ID, EXECUTOR_KEY)
            claims = claims_from_receipt(receipt, authentic=True)
            world = [artifact_evidence("env-esc", ws, "/etc/passwd", "obs")]
            v = compose(contract, claims, world, Stakes.NORMAL)
            self.assertIsNot(v.status, Status.VERIFIED)


# --------------------------------------------------------------------------- C4
class MalformedArgsFailClosed(unittest.TestCase):
    def test_wrong_type_content_returns_no_contract(self):
        env = issue_envelope("env-bad", "file.write", {"path": "out.txt", "content": 123},
                             "project_mutation", Stakes.NORMAL, "p", POLICY_KEY)
        self.assertIsNone(build_contract(env))

    def test_non_iterable_declared_outputs_returns_no_contract(self):
        env = issue_envelope("env-bad2", "shell.run", {"command": "x", "declared_outputs": 5},
                             "project_mutation", Stakes.NORMAL, "p", POLICY_KEY)
        self.assertIsNone(build_contract(env))

    def test_pipeline_maps_malformed_args_to_unverified(self):
        env = issue_envelope("env-bad3", "file.write", {"path": "out.txt", "content": 123},
                             "project_mutation", Stakes.NORMAL, "p", POLICY_KEY)
        receipt = issue_receipt("rcpt-bad3", "env-bad3", 0, {}, (), False,
                                EXECUTOR_ID, EXECUTOR_KEY)
        v = make_verifier().verify(env, receipt, [])
        self.assertIs(v.status, Status.UNVERIFIED)
        self.assertIn(Reason.NO_CONTRACT, v.reasons)


# --------------------------------------------------------------------------- C5
class AttestationScoping(unittest.TestCase):
    def test_high_stakes_insufficient_channels_is_not_attested(self):
        env = write_envelope("env-hi", "out.txt", CONTENT, stakes=Stakes.HIGH)
        contract = build_contract(env)
        claims = claims_from_receipt(honest_receipt("r", env, CONTENT), authentic=True)
        one_source = _world(env, CONTENT)  # single failure mode per obligation
        v = compose(contract, claims, one_source, Stakes.HIGH)
        self.assertIs(v.status, Status.UNVERIFIED)
        self.assertIn(Reason.INSUFFICIENT_CHANNELS, v.reasons)
        self.assertNotIn(Reason.INTEGRITY_ATTESTED, v.reasons)

    def test_claim_only_is_still_attested(self):
        env = write_envelope("env-lo", "out.txt", CONTENT)
        contract = build_contract(env)
        claims = claims_from_receipt(honest_receipt("r", env, CONTENT), authentic=True)
        v = compose(contract, claims, [], Stakes.NORMAL)
        self.assertIs(v.status, Status.UNVERIFIED)
        self.assertIn(Reason.INTEGRITY_ATTESTED, v.reasons)


# --------------------------------------------------------------------------- C6
class DistinctFailureModes(unittest.TestCase):
    """Two correlated world channels sharing a failure mode are one source."""

    def test_two_channels_same_failure_mode_are_insufficient(self):
        env = write_envelope("env-corr", "out.txt", CONTENT, stakes=Stakes.HIGH)
        contract = build_contract(env)
        claims = claims_from_receipt(honest_receipt("r", env, CONTENT), authentic=True)
        h = sha256_bytes(CONTENT.encode())
        # Two artifact channels, DIFFERENT channel strings, SAME failure mode.
        correlated = _world(env, CONTENT) + [
            WorldEvidence(obligation_id("env-corr", "exit_status"), "exit_status", "0",
                          "supervisor_exit", "host.supervisor.mirror", "p"),
            WorldEvidence(obligation_id("env-corr", "write_set"), "write_set", '["out.txt"]',
                          "host_snapshot_diff", "host.snapshot.mirror", "p"),
            WorldEvidence(obligation_id("env-corr", "artifact_hash", "out.txt"), "artifact_hash",
                          h, "host_rehash", "host.rehash.mirror", "p"),
        ]
        v = compose(contract, claims, correlated, Stakes.HIGH)
        self.assertIs(v.status, Status.UNVERIFIED)
        self.assertIn(Reason.INSUFFICIENT_CHANNELS, v.reasons)

    def test_two_distinct_failure_modes_verify(self):
        env = write_envelope("env-two", "out.txt", CONTENT, stakes=Stakes.HIGH)
        contract = build_contract(env)
        claims = claims_from_receipt(honest_receipt("r", env, CONTENT), authentic=True)
        h = sha256_bytes(CONTENT.encode())
        two_modes = _world(env, CONTENT) + [
            WorldEvidence(obligation_id("env-two", "exit_status"), "exit_status", "0",
                          "audit_log", "host.audit", "p"),
            WorldEvidence(obligation_id("env-two", "write_set"), "write_set", '["out.txt"]',
                          "audit_log", "host.audit", "p"),
            WorldEvidence(obligation_id("env-two", "artifact_hash", "out.txt"), "artifact_hash",
                          h, "mirror_read", "host.mirror", "p"),
        ]
        v = compose(contract, claims, two_modes, Stakes.HIGH)
        self.assertIs(v.status, Status.VERIFIED)


def _world(env, content):
    """Single-source always-on world set for a file.write envelope."""
    h = sha256_bytes(content.encode())
    eid = env.envelope_id
    return [
        WorldEvidence(obligation_id(eid, "exit_status"), "exit_status", "0",
                      "supervisor_exit", "host.supervisor", "p"),
        WorldEvidence(obligation_id(eid, "write_set"), "write_set", '["out.txt"]',
                      "host_snapshot_diff", "host.snapshot", "p"),
        WorldEvidence(obligation_id(eid, "artifact_hash", "out.txt"), "artifact_hash",
                      h, "host_rehash", "host.rehash", "p"),
    ]


if __name__ == "__main__":
    unittest.main()
