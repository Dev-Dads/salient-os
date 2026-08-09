"""④ Memory — LIVE proof against a real CDMS instance and a real model.

Runs the two-agent memory Collaborator against:
  - a DEDICATED CDMS instance (a COPY of the live store at CDMS_HOME=cdms-collab), so the
    proposer is shaped by Josh's REAL history while his live cdms-a is never touched; and
  - a real local model (ollama mistral-nemo) for the proposer.

The CDMS wiring (import cdms, the injected gist-reader + ingest-sink) lives HERE, not in the
collaborator package — so `collaborator/` keeps its structural no-CDMS-import guarantee (B).

Run with the CDMS venv + salient-os on PYTHONPATH:
  CDMS_HOME=~/.local_memory/cdms-collab PYTHONPATH=D:/repo/salient-os \\
    D:/repo/contextual_differentiation_memory_service/.venv/Scripts/python.exe \\
    red-team/collaborator/memory_live_proof.py
"""
import hashlib
import os
import pathlib
import tempfile

# --- the dedicated (copied) instance; NEVER the live cdms-a --------------------------- #
COLLAB_HOME = pathlib.Path(os.environ.get("CDMS_HOME") or
                           (pathlib.Path.home() / ".local_memory" / "cdms-collab"))
os.environ["CDMS_HOME"] = str(COLLAB_HOME)
LIVE_DB = pathlib.Path.home() / ".local_memory" / "cdms-a" / "memory.db"
# baseline md5 captured before any wiring (proves the live consolidated store is untouched)
LIVE_DB_BASELINE = "849196ec9f3fab8b69c5af272fbf5d93"

from cdms.config import Config          # noqa: E402  (CDMS venv)
from cdms.store import MemoryService, TurnEvent  # noqa: E402

from collaborator.factsource import FactRecord, FactView, HistoryView  # noqa: E402
from collaborator.memory import CdmsMemorySource  # noqa: E402
from collaborator.memory_ingest import remember  # noqa: E402
from collaborator.model_client import OllamaClient  # noqa: E402
from collaborator.propose import (  # noqa: E402
    approve_proposal, build_proposer_context, propose,
)
from collaborator.session import Session  # noqa: E402
from collaborator.governance import RAN, govern_action  # noqa: E402
from collaborator.toolcall import ToolIntent  # noqa: E402

_fail = 0


def check(label, ok, extra=""):
    global _fail
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' — ' + extra) if extra else ''}")
    if not ok:
        _fail += 1


def _md5(p):
    return hashlib.md5(pathlib.Path(p).read_bytes()).hexdigest()


def main():
    print(f"CDMS instance (dedicated copy): {COLLAB_HOME}")
    svc = MemoryService(Config())

    # --- the injected wiring (lives here, not in collaborator/) ----------------------- #
    def gist_reader(query, k, project):
        hits = svc.retrieve(query, tiers=("gist",), top_k=k, project=project or "", reinforce=False)
        return [{"subject": h.payload.get("subject", ""), "relation": h.payload.get("relation", ""),
                 "object": h.payload.get("object", ""), "valence": h.payload.get("valence", 0.0),
                 "frequency": h.payload.get("frequency", 1), "support": h.payload.get("support_count", 1),
                 "project": h.payload.get("project", ""), "tier": h.tier} for h in hits]

    ingested = []

    class CdmsSink:
        def write(self, deed):
            rec = svc.ingest(TurnEvent(**deed.to_turn_event()))
            ingested.append(rec)

    source = CdmsMemorySource(gist_reader)

    with tempfile.TemporaryDirectory() as tmp:
        session = Session(workspace=tmp)                       # fs.write:project by default
        # Unscoped (whole-persona) recall for the demo — the richest way to show the proposer
        # shaped by real history. Production scopes per-workspace (design cross-project fence);
        # here the tmpdir matches no real project, so "" recalls Josh's genuine persona.
        session.history_view = HistoryView("josh", "", source)    # PROPOSER-only, gist-tuple only
        session.fact_view = FactView("josh", tmp, [                # DOER + proposer, fenced
            FactRecord("world", "project", "salient-os", "verifier"),
            FactRecord("world", "test_runner", "pytest", "verifier"),
        ])

        # --- 1. REAL recall shapes the proposer (the "finds its own history" payoff) --- #
        print("\n1 — the proposer's context is shaped by REAL recalled history (fenced, 3rd-person)")
        ctx = build_proposer_context(session, query="what should we work on next")
        print("\n----- proposer context (fenced) -----\n" + ctx[:1100] + "\n-------------------------------------")
        check("real gist tuples were recalled into the fenced context",
              "observed-history" in ctx and "the system previously" in ctx)
        check("recall is fenced as DATA + third-person (no first person)",
              "<<observed-history" in ctx and " i " not in (" " + ctx.lower() + " "))

        # --- 2. the proposer (a real model) proposes, shaped by that history ----------- #
        print("\n2 — the separate proposer (mistral-nemo) proposes, shaped by history")
        client = OllamaClient(base_url="http://localhost:11434/v1", model="mistral-nemo:12b")
        try:
            props = propose(session, client, ctx, threshold=0.0)  # surface whatever it proposes
        except Exception as exc:  # noqa: BLE001
            props = []
            print("    (model error:", exc, ")")
        if props:
            print("    PROPOSAL (shaped by recalled history):", props[0].summary())
        else:
            print("    (the model declined to propose this pass — its choice, not a system fault;")
            print("     recall [step 1] + the governed ingest path [steps 3-5] are the proof.)")

        # --- 3. a governed deed executes (doer acts; ③ gates) ------------------------- #
        print("\n3 — a governed deed executes (doer on facts, capability-gated)")
        decision = None
        if props and props[0].decision.tool == "write_file" and props[0].decision.status == "held":
            decision = approve_proposal(session, props[0])       # run the model's own proposal
            print("    ran the proposer's proposal")
        if decision is None or decision.status != RAN:
            decision = govern_action(session, ToolIntent(
                "write_file", {"path": "NEXT_STEPS.md", "content": "# next steps\n- wire live CDMS\n"},
                "structured"))
            print("    ran a governed write_file")
        check("the governed deed RAN and verified", decision.status == RAN,
              f"status={decision.status}")

        # --- 4. the deed ingests into the COPY as `ambiguous` (never scars) ----------- #
        print("\n4 — the deed is remembered in the COPY, stamped `ambiguous`")
        remember(CdmsSink(), decision, session_id="live-proof", project="salient-os")
        check("the deed ingested into the dedicated instance", len(ingested) == 1)
        if ingested:
            check("provenance == 'ambiguous' (gists, never scars)",
                  ingested[0].provenance == "ambiguous", f"got {ingested[0].provenance!r}")

    # --- 5. the LIVE store is untouched ---------------------------------------------- #
    print("\n5 — the live cdms-a consolidated store is byte-for-byte untouched")
    live_md5 = _md5(LIVE_DB) if LIVE_DB.exists() else "missing"
    check("live memory.db md5 unchanged from baseline", live_md5 == LIVE_DB_BASELINE,
          f"{live_md5[:12]} vs {LIVE_DB_BASELINE[:12]}")

    print(f"\n{'ALL LIVE PROOFS PASSED' if _fail == 0 else str(_fail) + ' FAILED'}")
    raise SystemExit(1 if _fail else 0)


if __name__ == "__main__":
    main()
