"""H3 live proof: drive the REAL lifecycle dispatch -> observer -> bus with the
box's exact config (floor 25, headroom 15) and show the recorded compute_budget
MOVING across turns, read back from the on-disk audit JSONL the transparency
panel reads. No mocking of the observer: real emitters, real bus, real interpret.
"""
import os, tempfile, json, pathlib
HOME = tempfile.mkdtemp(prefix="h3proof_")
os.environ["HERMES_HOME"] = HOME

import hermes_constants
from hermes_cli import config as hermes_config
from hermes_cli import lifecycle
import model_tools
from hermes_cli.observability import salience_observer as so
from salienceos.interpreter.bus import SalienceBus
import product_identity

# Box config exactly: floor 25, headroom 15, subsystem on.
CFG = {"salience": {"enabled": True, "consume_compute": True, "compute_headroom": 15},
       "agent": {"max_iterations": 25}}
hermes_constants.get_hermes_home = lambda: pathlib.Path(HOME)
hermes_config.read_raw_config_readonly = lambda: CFG
product_identity.IS_QUORUM_EDITION = True
so._reset_for_tests()

SID = "box-live-demo"
def tool(turn, name, status="success", err=""):
    kw = dict(function_name=name, function_args={}, result={}, session_id=SID,
              task_id="t", turn_id=turn, tool_call_id=name+turn, status=status)
    if err: kw.update(error_type=err, error_message="x")
    model_tools._emit_post_tool_call_hook(**kw)

# ---- the operator's pristine budget, governed each turn via the real helper ----
class Agent:
    session_id = SID
    max_iterations = 25
agent = Agent()

def turn(label, n_tools):
    # open THIS turn's window (as pre_llm_call does), do n_tools real tool calls
    lifecycle.invoke_hook("pre_llm_call", session_id=SID, task_id="t", turn_id=label)
    for i in range(n_tools):
        tool(label, "read_file" if i % 2 else "write_file")

print(f"HERMES_HOME = {HOME}")
print(f"config: floor(max_iterations)=25, compute_headroom=15  =>  window [25, 40]\n")
print(f"{'turn':>6} {'tool events':>12} {'applied budget':>15}   note")

# Turn 1: govern (nothing recorded yet) -> 25, then a BUSY turn (8 events)
so.govern_iterations(agent); print(f"{'1':>6} {'-':>12} {agent.max_iterations:>15}   first turn: operator floor")
turn("u1", 8)
so.govern_iterations(agent); print(f"{'2':>6} {'8 (busy)':>12} {agent.max_iterations:>15}   applies u1 -> saturates to ceiling")
turn("u2", 4)
so.govern_iterations(agent); print(f"{'3':>6} {'4 (half)':>12} {agent.max_iterations:>15}   applies u2 -> mid-window")
turn("u3", 0)  # quiet
so.govern_iterations(agent); print(f"{'4':>6} {'0 (quiet)':>12} {agent.max_iterations:>15}   applies u3 -> decays to floor")
turn("u4", 8)
so.govern_iterations(agent); print(f"{'5':>6} {'8 (busy)':>12} {agent.max_iterations:>15}   applies u4 -> ceiling again (no compounding)")

# ---- read the DURABLE record the transparency panel reads ----
so._close_session({"session_id": SID})
path = pathlib.Path(HOME) / "salience" / (so._session_hash(SID) + ".jsonl")
bus = SalienceBus(str(path))
print(f"\naudit bus: {path.name}   chain_verified = {bus.verify_chain()}")
print("recorded directives (compute_budget per turn):")
for turn_lbl in ("u1", "u2", "u3", "u4"):
    ds = bus.directives_for(so._subject(SID, turn_lbl))
    if ds:
        print(f"    {turn_lbl}: compute_budget = {ds[0]['compute_budget']}")
