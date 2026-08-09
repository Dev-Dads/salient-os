"""④ Memory — MULTI-STORE live proof: memory/self (CDMS-A) + world/user facts (CDMS-D).

Extends memory_live_proof.py to also wire the FACT layer from CDMS-D — the operator-curated
world/user facts (`world_fact` + `project_overview`), read from a COPY of the -D store. So the
two-agent Collaborator now runs against BOTH remembered layers, live, from dedicated copies:

  - memory/self  <- CDMS-A gist tuples   (CDMS_HOME=~/.local_memory/cdms-collab)
  - world/user   <- CDMS-D world_fact    (CDMS_D_HOME=~/.local_memory/cdms-d-collab)

Both live stores are byte-for-byte untouched. The CDMS wiring lives HERE, not in the
collaborator package (import-ban preserved): the gist read uses cdms's semantic retrieve; the
fact read is plain sqlite3 over the -D world.db copy (the -D schema is stable).

Run with the CDMS venv + salient-os on PYTHONPATH:
  CDMS_HOME=~/.local_memory/cdms-collab PYTHONPATH=D:/repo/salient-os \\
    D:/repo/contextual_differentiation_memory_service/.venv/Scripts/python.exe \\
    red-team/collaborator/stores_live_proof.py
"""
import hashlib
import os
import pathlib
import sqlite3
import tempfile

COLLAB_A_HOME = pathlib.Path(os.environ.get("CDMS_HOME") or
                             (pathlib.Path.home() / ".local_memory" / "cdms-collab"))
os.environ["CDMS_HOME"] = str(COLLAB_A_HOME)
COLLAB_D_DB = pathlib.Path(os.environ.get("CDMS_D_HOME") or
                           (pathlib.Path.home() / ".local_memory" / "cdms-d-collab")) / "world.db"

LIVE_A_DB = pathlib.Path.home() / ".local_memory" / "cdms-a" / "memory.db"
LIVE_D_DB = pathlib.Path.home() / ".local_memory" / "cdms-d" / "world.db"
BASELINE = {str(LIVE_A_DB): "849196ec9f3fab8b69c5af272fbf5d93",
            str(LIVE_D_DB): "8137e4cb1404e0b610cd856045d5d7fe"}

from cdms.config import Config           # noqa: E402
from cdms.store import MemoryService, TurnEvent  # noqa: E402

from collaborator.factsource import FactRecord, FactView, HistoryView, render_facts  # noqa: E402
from collaborator.governance import RAN, govern_action  # noqa: E402
from collaborator.memory import CdmsMemorySource  # noqa: E402
from collaborator.memory_ingest import remember  # noqa: E402
from collaborator.model_client import OllamaClient  # noqa: E402
from collaborator.propose import build_proposer_context, propose  # noqa: E402
from collaborator.session import Session  # noqa: E402
from collaborator.toolcall import ToolIntent  # noqa: E402

_fail = 0
_USER_PROJECTS = {"user-preferences", "user_preferences"}
_USER_SUBJECTS = {"josh", "joshe", "user"}


def check(label, ok, extra=""):
    global _fail
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' — ' + extra) if extra else ''}")
    if not ok:
        _fail += 1


def _md5(p):
    return hashlib.md5(pathlib.Path(p).read_bytes()).hexdigest()


def load_d_facts(db_path):
    """Read CDMS-D's curated world/user facts from the COPY (read-only sqlite3). Maps
    world_fact rows -> FactRecord, tiered user vs world by project/subject, and active
    project_overview summaries -> world facts. Never writes."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    recs = []
    for subj, rel, obj, proj in con.execute(
            "SELECT subject, relation, object, project FROM world_fact WHERE superseded_by=''"):
        tier = "user" if (str(proj).lower() in _USER_PROJECTS
                          or str(subj).lower() in _USER_SUBJECTS) else "world"
        recs.append(FactRecord(tier, f"{subj} {rel}".strip(), str(obj), "operator"))
    for name, summary, _proj in con.execute(
            "SELECT name, summary, project FROM project_overview WHERE archived_at=''"):
        recs.append(FactRecord("world", f"project:{name}", str(summary), "operator"))
    con.close()
    return recs


def main():
    print(f"memory/self   <- CDMS-A copy: {COLLAB_A_HOME}")
    print(f"world/user    <- CDMS-D copy: {COLLAB_D_DB}")
    svc = MemoryService(Config())

    def gist_reader(query, k, project):
        hits = svc.retrieve(query, tiers=("gist",), top_k=k, project=project or "", reinforce=False)
        return [{"subject": h.payload.get("subject", ""), "relation": h.payload.get("relation", ""),
                 "object": h.payload.get("object", ""), "valence": h.payload.get("valence", 0.0),
                 "frequency": h.payload.get("frequency", 1), "support": h.payload.get("support_count", 1),
                 "project": h.payload.get("project", ""), "tier": h.tier} for h in hits]

    ingested = []

    class CdmsSink:
        def write(self, deed):
            ingested.append(svc.ingest(TurnEvent(**deed.to_turn_event())))

    d_facts = load_d_facts(COLLAB_D_DB)
    n_user = sum(1 for r in d_facts if r.tier == "user")
    n_world = sum(1 for r in d_facts if r.tier == "world")

    with tempfile.TemporaryDirectory() as tmp:
        session = Session(workspace=tmp)
        session.history_view = HistoryView("josh", "", CdmsMemorySource(gist_reader))  # self
        session.fact_view = FactView("josh", tmp, d_facts)                              # world/user

        # --- 1. BOTH remembered layers feed the fenced context ------------------------ #
        print(f"\n1 — real facts wired from CDMS-D copy: {n_user} user + {n_world} world/overview")
        print("\n----- fenced facts (from your real -D store) -----")
        print(render_facts(session.fact_view.read())[:900])
        print("--------------------------------------------------")
        check("real CDMS-D world/user facts loaded", n_user >= 1 and n_world >= 1)

        ctx = build_proposer_context(session, query="what should we work on next")
        check("proposer context carries BOTH real history AND real facts",
              "observed-history" in ctx and "<<facts" in ctx and "the system previously" in ctx)
        check("a genuine user preference is present (fenced)",
              "prefers" in ctx.lower() or "concise" in ctx.lower())

        # --- 2. the proposer (real model) proposes, shaped by history + facts --------- #
        print("\n2 — the proposer (mistral-nemo) proposes, shaped by history + facts")
        client = OllamaClient(base_url="http://localhost:11434/v1", model="mistral-nemo:12b")
        try:
            props = propose(session, client, ctx, threshold=0.0)
        except Exception as exc:  # noqa: BLE001
            props = []
            print("    (model error:", exc, ")")
        print("    PROPOSAL:", props[0].summary() if props else "(model declined this pass)")

        # --- 3. a governed deed executes + ingests as `ambiguous` (into the A copy) ---- #
        print("\n3 — a governed deed runs and is remembered (ambiguous) in the CDMS-A copy")
        decision = govern_action(session, ToolIntent(
            "write_file", {"path": "NOTES.md", "content": "# notes\n- multi-store live\n"}, "structured"))
        check("governed deed RAN + verified", decision.status == RAN, f"status={decision.status}")
        remember(CdmsSink(), decision, session_id="stores-proof", project="salient-os")
        check("deed ingested `ambiguous`", bool(ingested) and ingested[0].provenance == "ambiguous")

    # --- 4. BOTH live stores untouched ----------------------------------------------- #
    print("\n4 — both live stores are byte-for-byte untouched")
    for db in (LIVE_A_DB, LIVE_D_DB):
        got = _md5(db) if db.exists() else "missing"
        check(f"live {db.name} md5 unchanged", got == BASELINE[str(db)], f"{got[:12]}")

    print(f"\n{'ALL MULTI-STORE LIVE PROOFS PASSED' if _fail == 0 else str(_fail) + ' FAILED'}")
    raise SystemExit(1 if _fail else 0)


if __name__ == "__main__":
    main()
