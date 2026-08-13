"""External 5-vendor CODE review + CERTIFICATION for ② Stage A — the Host (collaborator/host.py):
one presence that owns the loop + propose + view + ledger on a single serial worker thread.

Per Josh's standing rule (external review on EVERY non-doc PR). This is concurrency-sensitive
NEW code that becomes the trust-bearing engine behind Sal's surface, so it gets a full panel with
an explicit THREADING + P-01 attack. Reproduce-before-accept; per-model + total cost.

Usage:  python red-team/collaborator/redteam_host.py [openai/gpt-5.1 ...]
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
    ("THE DELTA — the new Host + its tests (read first)", "red-team/collaborator/raw_host/host.diff"),
    ("THE CODE — collaborator/host.py", "collaborator/host.py"),
    ("THE LOOP it drives — collaborator/loop.py (run_turn + approve + STOPPED_* frozensets)",
     "collaborator/loop.py"),
    ("THE SEAM (sole authority boundary) — collaborator/governance.py", "collaborator/governance.py"),
    ("THE VIEW + controls + ledger — collaborator/view.py", "collaborator/view.py"),
    ("THE PROPOSE channel — collaborator/propose.py", "collaborator/propose.py"),
    ("THE SESSION (steering state) — collaborator/session.py", "collaborator/session.py"),
    ("WHAT IS PINNED — tests/test_collaborator_host.py", "tests/test_collaborator_host.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

CODE review + CERTIFICATION of a SHIPPED new module in the SalienceOS "Collaborator". Vocabulary:
the "seam" is `govern_action` (collaborator/governance.py — the sole authority boundary,
default-deny); `run_turn` (loop.py) is the directive loop; `loop.approve(session, decision)` runs
a HELD action (re-gates authority, single-use via `decision.consumed`, DENIED does NOT consume);
the JudgmentLedger/JudgmentView (view.py) are the record + display; `propose()` (propose.py) is
the idle proposal channel (fail-closed).

=== WHAT THIS IS ===
`collaborator/host.py` `Collaborator` assembles those parts into ONE governed worker so a caller
(a CLI, and next a local web surface) talks to one object. It runs on a background worker thread
and exposes submit / snapshot / pause / resume / set_leash / set_proactivity / veto / approve /
approve_proposal / decline. It closes the gaps: no Host (all hand-wired), propose had no trigger,
no task lifecycle.

=== THE DESIGN (attack it) ===
* LINCHPIN: everything that touches run_turn / govern_action / execute_and_verify / propose runs
  on the ONE worker thread, serially, fed by `self._jobs` (a queue.Queue) of typed jobs (TurnJob,
  ResumeJob, ApproveProposalJob, ProposeJob). Controls ENQUEUE work; they never call
  run_turn/approve inline. Claim: this makes two concurrent turns / double-execution impossible.
* LOCK: one `threading.RLock` guards the compound structures ONLY — `_tasks`, the ledger (append
  AND the multi-pass `view.snapshot()` read), `_proposals`. Held for micro-sections; NEVER across
  a model call / turn / subprocess. Scalar steering (`session.paused`, `session.proactivity`) is
  read lock-free inside govern_action — that live read IS the pause/steer feature.
* TASK LIFECYCLE: QUEUED→RUNNING→{DONE (STOPPED_FINAL) | AWAITING_APPROVAL (STOPPED_HELD; held
  Decisions stored) | PAUSED (STOPPED_PAUSED) | FAILED (STOPPED_EMPTY|MAX_ITERATIONS|exception)};
  AWAITING→approve→RUNNING or →decline→CANCELLED; PAUSED→resume→RUNNING. Mapped via loop's
  STOPPED_SUCCESS/AWAITING/FAILED frozensets with an explicit else→FAILED.
* RESUME CONTRACT: `run_turn` REQUIRES a user_message (no native resume). On approve the worker
  runs each held Decision via `loop.approve` (re-gates; DENIED→stays AWAITING, retryable; single-
  use), RECORDS the returned Decision into the ledger (loop.approve does NOT), then resumes with a
  HOST-AUTHORED authoritative note (`"TOOL RESULTS (approved by the human, now executed ...)"`),
  NEVER the human's free text. A PAUSED task (no held decisions) resumes with a "(resumed)" note.
* PROPOSE TRIGGER: a ticker thread enqueues a ProposeJob (never calls propose itself) only when
  idle ∧ proactivity≠off ∧ no RUNNING/QUEUED task ∧ no PROPOSED proposal already ∧ cooldown
  elapsed. The worker builds recent_actions from the ledger → build_proposer_context → propose.
  Display reads `ledger.proposals`, NOT the ProposalPool (whose dict iteration would crash under a
  concurrent add).

=== THE CERTIFICATION CLAIMS (attack each) ===
  C1 (serial execution / no double-run): no path executes an action off the worker or runs the
     same action twice. Controls enqueue; `approve`/`approve_proposal` NEVER call
     loop.approve/execute inline. `loop.approve`'s `consumed` flag + the single worker prevent a
     held/proposal decision running twice. Find any interleaving that double-executes or runs on
     the request thread.
  C2 (thread-safety / honest snapshot): `snapshot()` is atomic w.r.t. worker appends (its multi-
     pass reads over ledger lists can't see a half-updated state), no `RuntimeError` from a dict
     mutated during iteration, and the lock is NEVER held across a blocking model call/turn (so a
     reader never blocks for minutes). Find an inconsistent/lying snapshot, a crash, or a
     lock-held-across-IO freeze.
  C3 (resume / TOCTOU / no silent loss): held→approve→resume runs the held action once, records
     it, and continues with a host-authored (not human-authored) note; `loop.approve` returning
     DENIED leaves the task AWAITING and re-approvable (never a fake DONE); a PAUSED task resumes
     without the approval path; EMPTY/MAX_ITERATIONS/exception → FAILED, never a success-looking
     DONE. Find a lost/duplicated/again-mis-stated task outcome, or human free-text reaching the
     model as authority.
  C4 (P-01 / controls never grant authority): every control only RESTRICTS (pause, tighten,
     veto, decline) or expresses HOST CONFIG (leash capped by the signed grant, proactivity —
     surfacing only). `approve` re-gates the capability at run time. No control adds a capability;
     no request data reaches a `leash=` keyword or `intent.source`/emit; the propose trigger is
     fail-closed and cannot stack. Find any control path that widens what the model may do.

=== OUT OF SCOPE (do NOT rate as bugs) ===
  - The local web server / CSRF / bind scope — that is Stage B (surface.py), not this module.
  - `run_turn` not catching a client HTTP error (a transient 500 crashing a turn) — a filed
    general follow-up, not introduced here.
  - Unbounded per-task history growth over a very long conversation (noted; v0 acceptable).
  - Proactivity/idle-threshold tuning; whether gpt-oss emits well (opinion / the live proof's job).
  - Already-certified loop/parser/egress/run_command code except where the Host newly drives it.
"""

SYSTEM = """You are a senior application-security + concurrency engineer doing an adversarial CODE
review AND CERTIFICATION of a new worker-thread Host for a governed agent loop. BREAK it, but also
certify: if you cannot break a claim, say so explicitly (a blocked attack is a valuable
NON-finding). Assume the author is wrong until proven otherwise. Be especially adversarial about
THREADING and AUTHORITY:
  - C1/C2: construct a concrete interleaving. Can a control (approve/approve_proposal/veto/
    resume) cause execution or a state write off the worker thread, or race the worker's ledger
    append / task-state write? Is every `snapshot()` read under the same lock as every append? Is
    the lock ever held across `run_turn`/`client.complete`/`loop.approve` (which would freeze a
    reader)? Can the ticker enqueue a ProposeJob that runs `propose` while a TurnJob is mid-flight
    (two govern_action drivers at once)? Can a Task's `history` be read/written by two threads?
  - C3: trace held→approve→resume. Is `loop.approve` ever called twice for one decision (double
    run)? On DENIED, does the task correctly stay AWAITING (not DONE)? Does the resume note come
    from the HOST or can the human's submitted text reach the model as an authoritative TOOL
    RESULT? Can EMPTY/MAX_ITERATIONS slip to DONE?
  - C4: can any control, or request-derived data, grant a capability, loosen a leash past the
    signed cap, set `autonomous`/`intent.source`, or trigger an emission? Trace approve →
    loop.approve → reauthorized_or_denied.
Name file:line, give a CONCRETE interleaving/input, and say whether another layer (the seam,
loop.approve's consumed flag, the single worker) independently catches it. Distinguish a real
guarantee-breaking bug from a nit or a stated non-goal."""

USER_TMPL = """Review + CERTIFY the SHIPPED Host below.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) /
CONCRETE INTERLEAVING OR INPUT / WHY IT BREAKS A GUARANTEE / WHETHER ANOTHER LAYER CATCHES IT / FIX.
Then a CERTIFICATION LINE for EACH claim C1, C2, C3, C4: CERTIFIED / NOT-CERTIFIED + one sentence.
Then STEELMAN (2-3 sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence, and
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
        p = SOS / rel
        try:
            body = p.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            body = f"(could not read {rel}: {e})"
        parts.append(f"\n########## {label} ##########\n\n{body}")
    return "\n".join(parts)


BUNDLE = build_bundle()


def call(model: str) -> dict:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": USER_TMPL.format(bundle=BUNDLE)}],
        "temperature": 0.3, "max_tokens": 6500, "usage": {"include": True},
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS Host Panel"},
        method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        return {"model": model, "error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:600]}"}
    except Exception as e:  # noqa: BLE001
        return {"model": model, "error": f"{type(e).__name__}: {e}"}
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or msg.get("reasoning") or ""
    usage = data.get("usage", {})
    if not content:
        return {"model": model, "error": f"empty (finish={choice.get('finish_reason')})", "usage": usage}
    return {"model": model, "seconds": round(time.time() - t0, 1), "usage": usage,
            "cost": usage.get("cost"), "content": content, "finish": choice.get("finish_reason")}


def _fmt(c):
    return f"${c:.4f}" if isinstance(c, (int, float)) else "n/a"


def main():
    raw = HERE / "raw_host"
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
                print(f"[FAIL] {m}: {r['error'][:180]}")
                (raw / f"{slug}.md").write_text(f"# {m}\n\nERROR: {r['error']}\n", encoding="utf-8")
            else:
                print(f"[ OK ] {m}  {r['seconds']}s  cost={_fmt(r.get('cost'))}  chars={len(r['content'])}")
                (raw / f"{slug}.md").write_text(
                    f"# Host panel: {m}\n\n_cost={_fmt(r.get('cost'))} finish={r['finish']}_\n\n{r['content']}\n",
                    encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n=========== API COST — host panel ===========")
    total, have = 0.0, False
    for m in MODELS:
        c = results.get(m, {}).get("cost")
        if isinstance(c, (int, float)):
            total += c; have = True
        print(f"  {m:32s} {_fmt(c)}")
    print(f"  {'TOTAL':32s} {_fmt(total) if have else 'n/a'}")


if __name__ == "__main__":
    main()
