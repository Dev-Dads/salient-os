"""Empirically verify each red-team finding against the real code.
Prints CONFIRMED / REJECTED for each, with the observed behavior.
"""
import sys, tempfile, pathlib
sys.path.insert(0, r"D:\Repo\salient-os")

from salienceos.verifier import (
    Stakes, Status, Reason, build_contract, issue_envelope, issue_receipt,
    claims_from_receipt, compose,
)
from salienceos.verifier.observers import snapshot_tree, observed_write_set
sys.path.insert(0, r"D:\Repo\salient-os\tests")
from helpers import (make_verifier, write_envelope, honest_receipt, run_write_tool,
                     observe_world, POLICY_KEY, EXECUTOR_ID, EXECUTOR_KEY)

def line(msg): print("=" * 3, msg)

# ---- Grok F1: stale accumulated evidence -> false VERIFIED on re-verify ----
line("Grok F1: stale evidence on Verifier reuse")
with tempfile.TemporaryDirectory() as d:
    ws = pathlib.Path(d)
    v = make_verifier()
    env = write_envelope("env-reuse", "out.txt", "hello world")
    pre = snapshot_tree(ws)
    res = run_write_tool(ws, "out.txt", "hello world", 0)
    r1 = honest_receipt("rcpt-1", env, "hello world")
    world = observe_world(env, ws, pre, res)
    v1 = v.verify(env, r1, world)
    print("   first verify:", v1.status.name)
    # Now: same envelope_id, brand-new success receipt, NO fresh world, file even deleted
    (ws / "out.txt").unlink()
    r2 = honest_receipt("rcpt-2", env, "hello world", reported_success=True)
    v2 = v.verify(env, r2, [])   # empty world
    print("   second verify (empty world, file deleted):", v2.status.name)
    print("   >>> CONFIRMED false VERIFIED" if v2.status is Status.VERIFIED
          else "   >>> REJECTED (did not reproduce)")

# ---- Grok F4 / Kimi F2: build_contract crash on malformed args ----
line("Grok F4/Kimi F2: build_contract on content=int")
env_bad = issue_envelope("env-bad", "file.write", {"path": "out.txt", "content": 123},
                         "project_mutation", Stakes.NORMAL, "p", POLICY_KEY)
try:
    c = build_contract(env_bad)
    print("   returned:", c, "-> REJECTED (no crash)" if c is None else "   (built a contract?!)")
except Exception as e:
    print(f"   >>> CONFIRMED crash: {type(e).__name__}: {e}")

# ---- Grok F2: dir.make always FAILs with file-only snapshot ----
line("Grok F2: dir.make write-set with file-only snapshot")
with tempfile.TemporaryDirectory() as d:
    ws = pathlib.Path(d)
    pre = snapshot_tree(ws)
    (ws / "foo").mkdir()            # honest dir creation
    post = snapshot_tree(ws)
    ws_diff = observed_write_set(pre, post)
    print(f"   observed write-set after mkdir foo: {ws_diff}")
    print("   >>> CONFIRMED empty (dir invisible)" if ws_diff == []
          else "   >>> REJECTED")

# ---- Grok F3 / Qwen F1: observers must not read outside the workspace ----
line("Grok F3/Qwen F1: observer workspace-escape guard")
from salienceos.verifier.observers import rehash, path_state
with tempfile.TemporaryDirectory() as d:
    got_abs = rehash(d, "/etc/passwd")
    got_state = path_state(d, "/etc/passwd")
    print(f"   rehash(ws, '/etc/passwd')={got_abs!r}  path_state(...)={got_state!r}")
    print("   >>> REJECTED (reads as absent, fails closed)"
          if got_abs == "absent" and got_state == "absent"
          else "   >>> CONFIRMED escape")

# ---- Grok F5: INTEGRITY_ATTESTED attaches on INSUFFICIENT_CHANNELS (high stakes) ----
line("Grok F5: INTEGRITY_ATTESTED on high-stakes insufficient channels")
from tests.test_composer import full_world  # reuse fixture builder
env_h = write_envelope("env-hi", "out.txt", "hello world", stakes=Stakes.HIGH)
c_h = build_contract(env_h)
r_h = honest_receipt("rcpt-hi", env_h, "hello world")
claims_h = claims_from_receipt(r_h, authentic=True)
v_h = compose(c_h, claims_h, full_world(env_h), Stakes.HIGH)  # one channel only
print(f"   status={v_h.status.name} reasons={[r.name for r in v_h.reasons]}")
both = (Reason.INSUFFICIENT_CHANNELS in v_h.reasons and Reason.INTEGRITY_ATTESTED in v_h.reasons)
print("   >>> CONFIRMED both attached (semantic bug)" if both else "   >>> REJECTED")

# ---- deepseek F4: value-disagreeing world facts satisfy two-source? ----
line("deepseek F4: value-mismatched facts counted toward channels")
from salienceos.verifier.evidence import WorldEvidence
from salienceos.verifier.contract import obligation_id
env_d = write_envelope("env-d", "out.txt", "hello world", stakes=Stakes.HIGH)
c_d = build_contract(env_d)
r_d = honest_receipt("rcpt-d", env_d, "hello world")
claims_d = claims_from_receipt(r_d, authentic=True)
from salienceos.verifier.signing import sha256_bytes
good = sha256_bytes(b"hello world")
# one channel emits target once and garbage once (same channel+mode)
w = [
 WorldEvidence(obligation_id("env-d","exit_status"),"exit_status","0","supervisor_exit","host.supervisor","p"),
 WorldEvidence(obligation_id("env-d","write_set"),"write_set",'["out.txt"]',"host_snapshot_diff","host.snapshot","p"),
 WorldEvidence(obligation_id("env-d","artifact_hash","out.txt"),"artifact_hash",good,"host_rehash","host.rehash","p"),
 WorldEvidence(obligation_id("env-d","artifact_hash","out.txt"),"artifact_hash","0"*64,"host_rehash","host.rehash","p"),
]
v_d = compose(c_d, claims_d, w, Stakes.HIGH)
print(f"   status={v_d.status.name} reasons={[r.name for r in v_d.reasons]}")
print("   >>> deepseek F4 REJECTED (mismatch value does not create 2nd source)"
      if v_d.status is not Status.VERIFIED else "   >>> CONFIRMED")
