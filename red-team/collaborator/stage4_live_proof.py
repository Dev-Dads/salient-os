"""Stage-4-LIVE proof: the two learning channels DISAGREE on a real governed action.

The memory-retention governor + weight-adaptation gate (the two disagreeing channels)
already exist and are tested in salienceos/consumers/. They were host-DORMANT — nothing
drove an allow_adaptation path. The Collaborator is now that host: a real risky +
important governed action trips both channels — the weight gate HARD BLOCKS the skill
while the memory governor RETAINS it as a non-decaying inhibitor (a permanent warning).
This is the Stage-1 disagreement proof promoted from a unit fixture to a live worker.

Run:  python red-team/collaborator/stage4_live_proof.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from collaborator.governance import govern_action  # noqa: E402
from collaborator.session import Session  # noqa: E402
from collaborator.toolcall import ToolIntent  # noqa: E402
from salienceos.consumers import effective_weight  # noqa: E402


def _risky_important(session):
    return govern_action(
        session,
        ToolIntent("write_file", {"path": "incident.txt", "content": "risky important change"}, "structured"),
        importance=0.9, risk=0.9,  # risk over the 0.4 adaptation cap -> RISK_EXCEEDED
    )


def main():
    print("STAGE-4-LIVE PROOF — the two learning channels disagree on a real governed action")
    print("(the gate is salienceos/consumers/, already built + tested; here it fires end to end)\n")
    ok = True

    with tempfile.TemporaryDirectory() as tmp:
        s = Session(workspace=tmp, allow_adaptation=True)
        d = _risky_important(s)
        print("RISKY + IMPORTANT write  (importance=0.9, risk=0.9 > 0.4 cap):")
        print(f"  action ran (file written) : {(Path(tmp) / 'incident.txt').exists()}    verified: {d.cleared}")
        print(f"  recorded rationale        : {d.outcome.directive.adaptation_rationale}")
        print(f"  WEIGHT gate  -> nominated_for_learning={d.adaptation.nominated}  (HARD BLOCK)   "
              f"handoff={d.adaptation.handoff is not None}")
        print(f"  MEMORY gate  -> inhibitor={d.memory.inhibitor}  class={d.memory.retention_class!r}  (RETAIN as warning)")
        print(f"  DISAGREEMENT : {d.disagreement}   <- the same event refused as a skill, kept as a warning")
        w0 = effective_weight(d.memory, 0.0)
        wfar = effective_weight(d.memory, 100_000.0)
        print(f"  inhibitor weight: day 0 = {w0}   day 100000 = {wfar}   -> NO DECAY (a pin, not a fading memory)")
        try:
            chain_ok = s.bus.verify_chain()
            n_sig = len(s.bus.signals_for(d.action_id))
            print(f"  audit bus    : chain_intact={chain_ok}  signals_recorded_for_action={n_sig}")
        except Exception as e:  # noqa: BLE001
            print(f"  audit bus    : ({type(e).__name__})")
        ok &= (d.disagreement and not d.adaptation.nominated and d.memory.inhibitor and w0 == wfar)

    print("\nCONTRAST 1 — low-risk action (importance 0.5, risk 0.0), adaptation ON:")
    with tempfile.TemporaryDirectory() as tmp:
        s = Session(workspace=tmp, allow_adaptation=True)
        d = govern_action(s, ToolIntent("write_file", {"path": "safe.txt", "content": "ok"}, "structured"),
                          importance=0.5, risk=0.0)
        print(f"  disagreement={d.disagreement}  inhibitor={d.memory.inhibitor}  -> a safe change is not pinned as a warning")
        ok &= (not d.disagreement and not d.memory.inhibitor)

    print("\nCONTRAST 2 — adaptation OFF (host default): the gate is dormant:")
    with tempfile.TemporaryDirectory() as tmp:
        s = Session(workspace=tmp)  # allow_adaptation False
        d = _risky_important(s)
        print(f"  learning records: adaptation={d.adaptation} memory={d.memory}  -> no ADAPTATION signal, no consume, no inhibitor")
        ok &= (d.adaptation is None and d.memory is None and not d.disagreement)

    print(f"\nSTAGE-4-LIVE: {'OK' if ok else 'FAILED'}  "
          "(the Stage-1 disagreement proof, promoted from a unit fixture to a live governed worker)")


if __name__ == "__main__":
    main()
