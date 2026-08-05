"""Shared fixtures: a real (subprocess) executor, honest receipts, and the
always-on world observation pass, so mutation fixtures exercise the actual
pipeline rather than hand-fed values."""

import sys

from salienceos.verifier import (
    Stakes,
    Verifier,
    issue_envelope,
    issue_receipt,
)
from salienceos.verifier.observers import (
    artifact_evidence,
    exit_evidence,
    run_supervised,
    snapshot_tree,
    write_set_evidence,
)
from salienceos.verifier.signing import sha256_bytes

POLICY_KEY = b"policy-test-key"
EXECUTOR_KEY = b"executor-test-key"
EXECUTOR_ID = "exec-1"


def make_verifier() -> Verifier:
    return Verifier(policy_key=POLICY_KEY, executor_keys={EXECUTOR_ID: EXECUTOR_KEY})


def write_envelope(envelope_id: str, path: str, content: str, stakes=Stakes.NORMAL):
    return issue_envelope(
        envelope_id=envelope_id,
        op="file.write",
        args={"path": path, "content": content},
        action_class="project_mutation",
        stakes=stakes,
        policy_id="policy-0.1.0",
        policy_key=POLICY_KEY,
    )


def run_write_tool(workspace, target_path: str, content: str, exit_code: int = 0):
    """The 'executor': a real child process that writes bytes and exits."""
    script = (
        "import sys, pathlib\n"
        "pathlib.Path(sys.argv[1]).write_bytes(sys.argv[2].encode('utf-8'))\n"
        "sys.exit(int(sys.argv[3]))\n"
    )
    return run_supervised(
        [sys.executable, "-c", script, target_path, content, str(exit_code)],
        cwd=workspace,
    )


def honest_receipt(receipt_id: str, envelope, content: str, exit_code: int = 0,
                   reported_success: bool = True, claimed_exit=None):
    """Receipt as an honest executor would write it; claimed_exit lets a test
    simulate exit-code laundering."""
    path = envelope.args["path"]
    return issue_receipt(
        receipt_id=receipt_id,
        envelope_id=envelope.envelope_id,
        exit_code=exit_code if claimed_exit is None else claimed_exit,
        artifact_hashes={path: sha256_bytes(content.encode("utf-8"))},
        write_set=(path,),
        reported_success=reported_success,
        executor_id=EXECUTOR_ID,
        executor_key=EXECUTOR_KEY,
    )


def observe_world(envelope, workspace, pre_snapshot, supervised_result, provenance="obs-1"):
    """The always-on tier: supervisor exit + host re-hash + write-set diff."""
    post = snapshot_tree(workspace)
    eid = envelope.envelope_id
    return [
        exit_evidence(eid, supervised_result, provenance),
        artifact_evidence(eid, workspace, envelope.args["path"], provenance),
        write_set_evidence(eid, pre_snapshot, post, provenance),
    ]
