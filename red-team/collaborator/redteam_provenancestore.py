"""External 5-vendor CODE panel on the SHIPPED cross-session DURABLE provenance store (F2 follow-up,
branch feat/durable-provenance-store) BEFORE merge.

Bounded persistence layer: an HMAC-integrity-tagged, workspace-subject-bound JSON store kept OUTSIDE
the workspace, opt-in via a host-config path (default None = today's in-memory behaviour). No new
privileged surface, no network — a single lean pass. The control is ADVISORY (provenance flagging),
so the review targets the persistence-specific failure modes:

  * AGENT TAMPER/FORGE — the AGENT is the adversary (F2 = a deceived human running agent-authored
    bytes). Can the model/contained-run FORGE a "clean" store (defeat the HMAC), TAMPER the store to
    drop a taint, or reach it at all (is the out-of-workspace assert robust vs a symlink / relative /
    workspace-is-a-symlink case)? Is policy_key genuinely unreachable by the contained run?
  * FAIL-HONEST vs FAIL-SILENT — an untrusted store (bad HMAC / wrong subject / corrupt) must load
    EMPTY + mark tracking DEGRADED, never fake-clean, never raise. Is that airtight? Does a MISSING
    store (fresh) get correctly distinguished from a DELETED store (lost taints)?
  * REPLAY — a store from workspace A trusted for workspace B (subject binding); an OLD store version
    rolled back.
  * TOTALITY — load/save never raise (Session construction + govern paths must not blow up on a bad
    store); persistence failure degrades durability, not correctness.
  * POISON / BLOAT — can the model poison the durable manifest to noise-blind, or bloat it unbounded?

Reports per-model + total API cost (standing request).

Usage:  python red-team/collaborator/redteam_provenancestore.py [openai/gpt-5.1 ...]
"""

import concurrent.futures as cf
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).parent
SOS = HERE.parent.parent
KEY = os.environ["OPENROUTER_API_KEY"].strip()

_FILES = [
    ("THE STORE — collaborator/provenancestore.py (load/save, _canonical/_mac, integrity + subject "
     "binding)", "collaborator/provenancestore.py"),
    ("THE WIRING — collaborator/session.py (provenance_store param, _assert_store_outside_workspace, "
     "load-at-construction, _persist_provenance, note/clear/mark_tracking_incomplete)",
     "collaborator/session.py"),
    ("THE SEAM HOOK — collaborator/governance.py (_mark_tracking_incomplete prefers the persisting "
     "method; the F2 recording/surfacing already merged)", "collaborator/governance.py"),
    ("WHAT IS PINNED — tests/test_collaborator_provenancestore.py",
     "tests/test_collaborator_provenancestore.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

CODE review of a SHIPPED diff (F2 follow-up), not a design review. SalienceOS's "Collaborator" flags
workspace files an AUTONOMOUS (not-human-approved) action authored, so a human is warned (a ⚠ at the
approval hand) before running an agent-dropped file UNCONTAINED (`sh ./build.sh`). That manifest
(`session._autonomous_authored`) was session-lived; a drop in one session was forgotten by the next.

THIS DIFF makes it cross-session DURABLE, opt-in:
- `Session(provenance_store=<path>)` — host config, NEVER model-chosen. Default None = in-memory only
  (unchanged). A path opts in: the manifest (+ an honest "tracking degraded" flag) persists to that JSON
  file across sessions.
- THREAT MODEL: the AGENT is the adversary. The store MUST live OUTSIDE the workspace — the contained
  autonomous run can write ONLY the workspace (deny-by-default bwrap), so an out-of-workspace store is
  beyond its reach; the human maintenance run has full FS reach but is the TRUSTED hand. Session
  construction FAILS LOUD (ValueError) if the store path resolves inside the workspace.
- INTEGRITY: HMAC-SHA256 over canonical JSON (sorted authored set + subject + incomplete flag) under the
  host's `policy_key` — an in-process secret, never written to the workspace, unreachable by the
  contained run — plus a workspace-SUBJECT binding. So the agent cannot FORGE a clean store, and a store
  for a DIFFERENT workspace cannot be replayed in.
- FAIL-HONEST: a load that cannot be TRUSTED (missing/mismatched HMAC, wrong subject, corrupt, any error)
  returns (empty set, incomplete=True, ok=False); Session then loads EMPTY and marks tracking DEGRADED
  (surfaced in the human preview). A MISSING file is a fresh trusted start (empty, incomplete=False,
  ok=True). load/save are TOTAL (never raise); save is best-effort (in-memory manifest stays authoritative).

Documented, NOT findings (advisory posture, same as the base F2 control): the manifest is a COUNT of
authored paths, not content; it accumulates (a dropped-then-deleted path stays until a human write_file
/ approved-run CLEARS it — a documented pruning follow-up); a human-AUTHORIZED deletion of the store
degrades to an empty store (honest loss, never fake-clean); the token-only recognizer recall gap is
unchanged and out of scope here.
"""

SYSTEM = """You are a senior security engineer doing an adversarial CODE review of a durable,
integrity-protected persistence layer for an ADVISORY provenance control. Assume the author is wrong
until proven otherwise. The control is deliberately advisory/non-blocking and the recall/pruning gaps
are documented — do NOT re-report those as bugs. Report where the persistence layer fails ITS OWN goal
(survive across sessions WITHOUT letting the AGENT forge/tamper/erase provenance, and fail HONEST when
it can't be trusted) or breaks a HARD invariant (totality; the out-of-workspace guarantee).

Attack, name file:line, give a CONCRETE case, and say whether another control catches it:
1. AGENT TAMPER/FORGE. Can the model/contained-run FORGE a store that verifies as clean (defeat the
   HMAC without policy_key)? Is policy_key genuinely unreachable by the contained run (workspace-only
   write, no host process memory)? Can the agent REACH the store to delete/rewrite a taint — is
   `_assert_store_outside_workspace` robust vs: a store path that is a SYMLINK into the workspace; a
   relative path; the WORKSPACE itself being a symlink; `..` traversal; a parent/ancestor relationship
   computed via resolve()? Is the check at the right time (construction) given the path is host-config
   (not model-chosen)?
2. FAIL-HONEST vs FAIL-SILENT. Untrusted store (bad HMAC / wrong subject / corrupt / partial write /
   truncated) — does it ALWAYS load empty + degraded, never fake-clean, never raise? Trace load()'s
   branches. Is a MISSING file (fresh, trusted) correctly distinguished from a tampered/corrupt one?
   The `ok=False -> incomplete=True` mapping in Session — any path where an untrusted store loads as
   trusted-empty (silent) instead of degraded?
3. REPLAY / ROLLBACK. Subject binding stops a cross-workspace store; is the subject derivation
   (`workspace_subject`) actually distinct per workspace and bound INTO the MAC? Can an OLD version of
   the SAME store (fewer taints) be rolled back by the agent to erase a recent taint (no monotonic
   counter / no freshness)? Is that in scope given the store is out of the agent's write reach?
4. TOTALITY / CONSTRUCTION. Can load()/save()/_persist_provenance/_assert_store_outside_workspace raise
   out of Session.__init__ or a govern path (a bad path, a permission error, a race, a huge file, an
   unresolvable symlink)? Session/govern promise not to blow up on a bad store.
5. POISON / BLOAT / CONCURRENCY. Can the model poison the durable manifest to noise-blind future
   sessions (or is it reference-gated so junk = zero warnings)? Unbounded growth? Two sessions sharing
   one store file racing save() (os.replace atomicity; a torn read)? A save() that partially writes then
   the process dies — does the next load fail-honest?

Calibration: concrete, code-grounded. The AGENT (contained autonomous run + a deceived human running
its bytes) is the adversary; the human operator is trusted (they own the machine). Distinguish a real
forge/tamper/silent-trust/totality break from the DOCUMENTED advisory posture (count-not-content;
accumulation/pruning follow-up; human-authorized deletion = honest loss). If sound, say so plainly."""

USER_TMPL = """Review the SHIPPED durable provenance-store diff below.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) /
CONCRETE CASE / WHY IT FORGES/TAMPERS/FAILS-SILENT/BREAKS-TOTALITY / WHETHER ANOTHER CONTROL CATCHES IT /
FIX. Then STEELMAN (2-3 sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence +
the single highest-value fix).

=================== BEGIN MATERIAL ===================
{bundle}
=================== END MATERIAL ==================="""

PANEL = ["openai/gpt-5.1", "google/gemini-2.5-pro", "anthropic/claude-opus-4.1",
         "x-ai/grok-4.5", "qwen/qwen3-max"]
MODELS = sys.argv[1:] if len(sys.argv) > 1 else PANEL


def build_bundle() -> str:
    parts = [CONTEXT]
    for label, rel in _FILES:
        parts.append(f"\n\n########## {label} ##########\n\n{(SOS / rel).read_text(encoding='utf-8')}")
    return "".join(parts)


BUNDLE = build_bundle()


def call(model: str) -> dict:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": USER_TMPL.format(bundle=BUNDLE)}],
        "temperature": 0.3, "max_tokens": 16000,
        "usage": {"include": True},
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://salient-os.local/redteam",
                 "X-Title": "SalienceOS provenance-store Code Panel"},
        method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        return {"model": model, "error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:800]}"}
    except Exception as e:  # noqa: BLE001
        return {"model": model, "error": f"{type(e).__name__}: {e}"}
    dt = time.time() - t0
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or msg.get("reasoning") or ""
    usage = data.get("usage", {})
    if not content:
        return {"model": model, "error": f"empty content (finish={choice.get('finish_reason')})", "usage": usage}
    return {"model": model, "seconds": round(dt, 1), "usage": usage,
            "cost": usage.get("cost"), "content": content, "finish": choice.get("finish_reason")}


def _fmt_cost(c):
    return f"${c:.4f}" if isinstance(c, (int, float)) else "n/a"


def main():
    raw = HERE / "raw_provenancestore"
    raw.mkdir(parents=True, exist_ok=True)
    print(f"bundle chars={len(BUNDLE)}  models={MODELS}")
    results = {}
    with cf.ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(call, m): m for m in MODELS}
        for fut in cf.as_completed(futs):
            r = fut.result()
            m = r["model"]
            results[m] = r
            slug = m.replace("/", "_")
            if "error" in r:
                print(f"[FAIL] {m}: {r['error'][:200]}")
                (raw / f"{slug}.md").write_text(f"# {m}\n\nERROR: {r['error']}\n", encoding="utf-8")
            else:
                print(f"[ OK ] {m}  {r['seconds']}s  finish={r['finish']}  "
                      f"chars={len(r['content'])}  cost={_fmt_cost(r.get('cost'))}")
                (raw / f"{slug}.md").write_text(
                    f"# provenance-store code panel: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — provenance-store CODE panel =======")
    total = 0.0
    have_any = False
    for m in MODELS:
        r = results.get(m, {})
        c = r.get("cost")
        u = r.get("usage", {}) or {}
        toks = f"{u.get('prompt_tokens','?')}->{u.get('completion_tokens','?')} tok" if u else ""
        if isinstance(c, (int, float)):
            total += c
            have_any = True
        print(f"  {m:<34} {_fmt_cost(c):>10}   {toks}")
    print("  " + "-" * 54)
    print(f"  {'TOTAL':<34} {(_fmt_cost(total) if have_any else 'n/a'):>10}")
    print("=====================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
