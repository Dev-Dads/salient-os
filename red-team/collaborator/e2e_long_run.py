"""④ Memory — the LONG multi-turn e2e: all pieces, 25+ turns, against gpt-oss:120b.

A proposer-driven working session: each turn the PROPOSER (120b), shaped by real recalled
gists (CDMS-A copy) + real curated facts (CDMS-D copy) + the growing workspace, surfaces a
proposal; it is GOVERNED (③ caps + leash: safe file ops run+verify, run_command holds, escapes
deny); executed deeds ingest `ambiguous` into the CDMS-A copy. A midpoint CONSOLIDATION lets
the run's deeds gist (persona-grows). Both live stores stay untouched.

Run with the CDMS venv + salient-os on PYTHONPATH, over an SSH tunnel to Sparky's NVMe :11500:
  ssh -N -L 11500:127.0.0.1:11500 chance6706@sparky &
  CDMS_HOME=~/.local_memory/cdms-collab CDMS_D_HOME=~/.local_memory/cdms-d-collab \\
    PYTHONPATH=D:/repo/salient-os \\
    D:/repo/contextual_differentiation_memory_service/.venv/Scripts/python.exe \\
    red-team/collaborator/e2e_long_run.py
"""
import hashlib
import json
import os
import pathlib
import sqlite3
import tempfile
from datetime import datetime, timezone

N_TURNS = 26
CONSOLIDATE_AT = 13
BASE_URL = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11500/v1")
MODEL = os.environ.get("OLLAMA_MODEL", "gpt-oss:120b")

COLLAB_A_HOME = pathlib.Path(os.environ.get("CDMS_HOME") or
                             (pathlib.Path.home() / ".local_memory" / "cdms-collab"))
os.environ["CDMS_HOME"] = str(COLLAB_A_HOME)
COLLAB_D_DB = pathlib.Path(os.environ.get("CDMS_D_HOME") or
                           (pathlib.Path.home() / ".local_memory" / "cdms-d-collab")) / "world.db"
LIVE = {str(pathlib.Path.home() / ".local_memory" / "cdms-a" / "memory.db"): "849196ec9f3fab8b69c5af272fbf5d93",
        str(pathlib.Path.home() / ".local_memory" / "cdms-d" / "world.db"): "8137e4cb1404e0b610cd856045d5d7fe"}

from cdms.config import Config           # noqa: E402
from cdms.consolidate import Consolidator  # noqa: E402
from cdms.store import MemoryService, TurnEvent  # noqa: E402

from collaborator.factsource import FactRecord, FactView, HistoryView  # noqa: E402
from collaborator.governance import DENIED, HELD, NOTIFIED, RAN, FAILED  # noqa: E402
from collaborator.memory import CdmsMemorySource  # noqa: E402
from collaborator.memory_ingest import remember  # noqa: E402
from collaborator.model_client import OllamaClient  # noqa: E402
from collaborator.propose import approve_proposal, build_proposer_context, propose  # noqa: E402
from collaborator.session import Session  # noqa: E402

_USER_PROJECTS = {"user-preferences", "user_preferences"}
_USER_SUBJECTS = {"josh", "joshe", "user"}


def load_d_facts(db_path):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    recs = []
    for subj, rel, obj, proj in con.execute(
            "SELECT subject, relation, object, project FROM world_fact WHERE superseded_by=''"):
        tier = "user" if (str(proj).lower() in _USER_PROJECTS or str(subj).lower() in _USER_SUBJECTS) else "world"
        recs.append(FactRecord(tier, f"{subj} {rel}".strip(), str(obj), "operator"))
    for name, summary, _p in con.execute(
            "SELECT name, summary, project FROM project_overview WHERE archived_at=''"):
        recs.append(FactRecord("world", f"project:{name}", str(summary), "operator"))
    con.close()
    return recs


def _md5(p):
    return hashlib.md5(pathlib.Path(p).read_bytes()).hexdigest()


def _gist_count():
    """Count gist rows by reading the CDMS-A copy directly (discover the gist table)."""
    try:
        con = sqlite3.connect(f"file:{COLLAB_A_HOME / 'memory.db'}?mode=ro", uri=True)
        t = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%gist%'").fetchall()
        n = con.execute(f"SELECT COUNT(*) FROM {t[0][0]}").fetchone()[0] if t else -1
        con.close()
        return n
    except Exception:  # noqa: BLE001
        return -1


def main():
    print(f"model={MODEL} @ {BASE_URL}")
    print(f"memory/self <- {COLLAB_A_HOME}   world/user <- {COLLAB_D_DB}\n")
    svc = MemoryService(Config())
    client = OllamaClient(BASE_URL, MODEL, timeout=600, max_tokens=2048, temperature=0.3)

    def gist_reader(query, k, project):
        hits = svc.retrieve(query, tiers=("gist",), top_k=k, project=project or "", reinforce=False)
        return [{"subject": h.payload.get("subject", ""), "relation": h.payload.get("relation", ""),
                 "object": h.payload.get("object", ""), "valence": h.payload.get("valence", 0.0),
                 "frequency": h.payload.get("frequency", 1), "support": h.payload.get("support_count", 1),
                 "project": h.payload.get("project", ""), "tier": h.tier} for h in hits]

    ingested = []

    class Sink:
        def write(self, deed):
            ingested.append(svc.ingest(TurnEvent(**deed.to_turn_event())))

    d_facts = load_d_facts(COLLAB_D_DB)
    transcript, tally = [], {RAN: 0, HELD: 0, NOTIFIED: 0, DENIED: 0, FAILED: 0, "declined": 0, "error": 0}
    gist_before = _gist_count()

    with tempfile.TemporaryDirectory() as tmp:
        session = Session(workspace=tmp, proactivity="eager")
        session.history_view = HistoryView("josh", "", CdmsMemorySource(gist_reader))
        session.fact_view = FactView("josh", tmp, d_facts)

        for turn in range(1, N_TURNS + 1):
            if turn == CONSOLIDATE_AT:
                print(f"\n--- turn {turn}: CONSOLIDATION (deeds gist; persona grows) ---")
                try:
                    rep = Consolidator(Config(), db=svc.db, embedder=svc.embedder).run(now=datetime.now(timezone.utc))
                    print("    consolidation report:", json.dumps(rep.as_dict())[:200])
                except Exception as exc:  # noqa: BLE001
                    print("    (consolidation skipped:", exc, ")")

            ws = sorted(os.listdir(tmp))
            ctx = build_proposer_context(session, query="a useful next action for this project",
                                         extra=f"current workspace files: {ws or '(empty)'}")
            row = {"turn": turn}
            try:
                props = propose(session, client, ctx, threshold=0.0)
            except Exception as exc:  # noqa: BLE001
                tally["error"] += 1
                row.update(outcome="error", detail=str(exc)[:120])
                transcript.append(row); print(f"[{turn:2}] ERROR {str(exc)[:80]}"); continue

            if not props:
                tally["declined"] += 1
                row.update(outcome="declined")
                transcript.append(row); print(f"[{turn:2}] (declined)"); continue

            p = props[0]
            d = p.decision
            row.update(tool=d.tool, conf=round(p.confidence, 2), rationale=p.rationale[:90],
                       args=json.dumps(d.args)[:90])
            # Auto-approve SAFE file ops (stands in for the human in this controlled run);
            # run_command stays HELD (leash); escapes are already DENIED by govern.
            if d.tool in ("write_file", "read_file") and d.status in (HELD, NOTIFIED):
                d = approve_proposal(session, p)
            if d.status == RAN:
                remember(Sink(), d, session_id="long-run", project="salient-os")
            tally[d.status] = tally.get(d.status, 0) + 1
            row["outcome"] = d.status
            transcript.append(row)
            print(f"[{turn:2}] {d.status:8} {d.tool}({row['args']}) c={row['conf']} — {row['rationale']}")

    gist_after = _gist_count()

    # --- summary + isolation --------------------------------------------------------- #
    print("\n================= LONG-RUN SUMMARY =================")
    print(f"turns={N_TURNS}  outcomes={ {k: v for k, v in tally.items() if v} }")
    print(f"ambiguous deeds ingested into the CDMS-A copy: {len(ingested)}"
          f"  (all ambiguous: {all(r.provenance == 'ambiguous' for r in ingested)})")
    print(f"gists before consolidation={gist_before}  after={gist_after}")
    print("live stores untouched:", {pathlib.Path(k).name: (_md5(k) == v) for k, v in LIVE.items()})

    out = pathlib.Path(__file__).parent / "e2e_long_run_output.json"
    out.write_text(json.dumps({"model": MODEL, "turns": N_TURNS, "tally": tally,
                               "ingested": len(ingested), "gist_before": gist_before,
                               "gist_after": gist_after, "transcript": transcript}, indent=2), encoding="utf-8")
    print(f"\ntranscript saved -> {out}")
    ok = (all(r.provenance == "ambiguous" for r in ingested) and
          all(_md5(k) == v for k, v in LIVE.items()))
    print("ALL PIECES E2E:", "PASS" if ok else "CHECK")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
