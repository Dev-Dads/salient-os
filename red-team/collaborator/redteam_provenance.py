"""External 5-vendor CODE panel on the SHIPPED F2 autonomy-authorship PROVENANCE diff (ADR 0003
residual sweep, branch feat/autonomy-provenance) BEFORE merge.

Lower-risk than the sandbox work (a session-lived set + a POROUS argv recognizer + a pre/post
snapshot-diff; no new privileged surface, no network mechanism) — a single lean pass. This is the
LAST residual-sweep item and it is deliberately ADVISORY (a ⚠ + audit tag, never a deny), so the
review targets the specific failure modes of an advisory control:

  * a FALSE-NEGATIVE that matters — an autonomous drop that is NOT recorded (a write path we don't
    snapshot; an autonomous run_command that runs UN-contained yet writes; a rename/hardlink that
    dodges the pre/post file-set diff), OR a human run that references an autonomous file WITHOUT a
    bare argv token (nested `sh -c`, a pipe, a glob, a symlink) — is the recall gap HONESTLY bounded?
  * a MODEL-REACHABLE clear/skip — can the MODEL (not a human hand) drop its own taint, avoid being
    recorded, or force `_references_autonomous`/recording to except-swallow into "no tag"?
  * a FALSE-POSITIVE that noise-blinds the human (a stale ⚠ after a human re-vets; a benign mention
    like `echo build.sh`), and whether clear-on-human-write is correct + not itself a laundering path
    (an autonomous write to a path a human ONCE approved — is it re-tainted?).
  * a TOTALITY break — govern_action/approve promise NEVER to raise; does any new path raise?

Reports per-model + total API cost (standing request).

Usage:  python red-team/collaborator/redteam_provenance.py [openai/gpt-5.1 ...]
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
    ("THE RECOGNIZER + NORMALIZER — collaborator/provenance.py (references_autonomous_file, norm_rel, "
     "_tokenize, _rel_candidates)", "collaborator/provenance.py"),
    ("THE MANIFEST STATE — collaborator/session.py (_autonomous_authored, note_autonomous_authorship, "
     "clear_autonomous_authorship)", "collaborator/session.py"),
    ("THE RECORDING + SURFACING — collaborator/governance.py (_references_autonomous / "
     "_record_autonomous_authorship / _clear_autonomous_authorship; the run_command HELD preview; the "
     "exit-branch snapshot-diff recording; the artifact-branch write_file record/clear)",
     "collaborator/governance.py"),
    ("THE EXECUTOR CONTEXT — collaborator/tools.py (_exec_command autonomous-contained vs human path; "
     "_exec_write write_set)", "collaborator/tools.py"),
    ("THE SNAPSHOT PRIMITIVES — salienceos/verifier/observers.py (snapshot_tree, observed_write_set)",
     "salienceos/verifier/observers.py"),
    ("WHAT IS PINNED — tests/test_collaborator_provenance.py", "tests/test_collaborator_provenance.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

CODE review of a SHIPPED diff (ADR 0003 residual sweep — F2 shared-workspace dropper), not a design
review. SalienceOS's "Collaborator" governs a worker agent. run_command has TWO execution paths:
  * AUTONOMOUS (not human-approved): earns autonomy ONLY when it runs CONTAINED (bwrap: code roots
    read-only, deny-by-default mount view, routeless netns) AND a signed shell.contained_autonomy cap
    is held. The workspace is bound READ-WRITE (it must be useful) and is SHARED with...
  * HUMAN-approved / opted-in: FULL filesystem reach BY DESIGN (system maintenance must not break) —
    `cwd=workspace`, uncontained (a bwrap maintenance sandbox masks egress deputies + routeless net,
    but full FS reach).
Because the workspace is shared rw, an AUTONOMOUS action can DROP an executable file that a human
later approves and runs UNCONTAINED (`sh ./build.sh`) with the human's own reach. The two droppers:
an autonomous write_file, and an autonomous contained run_command. This was a DOCUMENTED open axis
(contained.py:31-38).

The operator chose the LIGHTEST closure: PROVENANCE-FLAGGING (not quarantine, not workspace
separation). We do NOT fence the human run (they keep full reach on purpose). We FLAG it:

1. A session-lived set `_autonomous_authored` records workspace-relative posix paths a NOT-human-
   approved (human_gated=False) action authored:
     - autonomous write_file: recorded from execution.write_set on a CLEARED (verified) write in the
       artifact branch; a HUMAN-approved (human_gated=True) verified write CLEARS the path's taint
       (those bytes are now human-vetted).
     - autonomous CONTAINED run_command: recorded in the exit branch via a snapshot_tree pre/post
       diff (new/changed FILES only; deletions + dirs + the `.sandbox-home/` in-fence HOME excluded).
       Only fires when require_code_protection is True (the autonomous contained path); a human
       maintenance run is NOT snapshotted/tracked.
2. `references_autonomous_file(command, authored, workspace)` is a POROUS argv-token recognizer: it
   shlex-splits (falling back to whitespace split), and for each non-flag token computes workspace-rel
   candidates (norm: \\ -> /, strip ./, drop escaping ..; plus an absolute-path-into-workspace form),
   matching against the recorded set. TOTAL — never raises (any error -> "" no tag).
3. Surfacing: when a run_command is HELD for a human hand, the preview carries `autonomous_authored`
   and the Decision carries a `provenance_touch` audit tag; the exit-branch Decision carries it too.
   summary() prints "⚠ autonomy-authored file referenced (not reviewed by you): <paths>". This is
   ADVISORY — NEVER a deny; the human can still approve.

The manifest + recognizer are getattr-guarded (a legacy session simply has no manifest -> "").
"""

SYSTEM = """You are a senior security engineer doing an adversarial CODE review of an ADVISORY
provenance-flagging control in a governed agent. Assume the author is wrong until proven otherwise.
This control is deliberately POROUS and NON-BLOCKING, so do NOT report "porous/advisory" as if it
were a bug — the author states it plainly. Report only where the control fails ITS OWN stated goal
(make autonomous authorship VISIBLE at the human's approval hand) worse than documented, or breaks a
HARD invariant.

Attack, name file:line, give a CONCRETE case, and say whether another control catches it:
1. RECORDING FALSE-NEGATIVE (the one that MATTERS). Is there an AUTONOMOUS path that writes a runnable
   workspace file but is NOT recorded? Consider: an autonomous run_command that runs on a NON-contained
   path (does require_code_protection ever end up False while an autonomous shell still executes and
   writes? trace govern_action's withhold at execution + the netns floor); the snapshot-diff missing a
   rename/hardlink/mtime-only change; a mutated EXISTING file (does observed_write_set catch a content
   change, and is it then recorded?); write_file NOT clearing verification yet the file existing on disk.
   Is the recall boundary HONESTLY the documented one, or worse?
2. MODEL REACHABILITY. Can the MODEL (emitting tool intents, never calling session methods directly)
   (a) CLEAR its own taint without a human hand — is clear gated strictly on human_gated + verified
   file.write? (b) AVOID being recorded — force the recording try/except to swallow, or make
   snapshot_tree/observed_write_set except so `_pre`/`_post` skip? (c) POISON the manifest to hide a
   real drop or to noise-blind (record thousands of junk paths)? (d) reach `_autonomous_authored`,
   note_*, or clear_* through any model-set field?
3. FALSE-POSITIVE / NOISE-BLIND. A stale ⚠ that survives a legitimate human re-vet (is clear-on-write
   keyed correctly?); a benign token match (`echo build.sh`, a flag, a substring) that fires the ⚠ and
   trains the human to ignore it. Is norm_rel's escaping-`..` drop + the flag skip enough to keep FPs
   rare? Does the absolute-into-workspace candidate ever mis-relativize an out-of-workspace path to a
   colliding rel path (false match)?
4. LAUNDERING VIA CLEAR. clear-on-human-write drops a taint. Can an autonomous run then RE-AUTHOR that
   same path and is it RE-TAINTED (record is unconditional add)? Is there an ordering where an
   autonomous drop is cleared by an unrelated human write to the SAME path (a human writes build.sh for
   their own reason, clearing a taint the autonomy set — acceptable, or a real laundering channel)?
5. TOTALITY / REGRESSION. govern_action/approve promise NEVER to raise. Does any new call
   (_references_autonomous, recording, note/clear, the extra snapshot_tree) raise on a hostile input
   (a non-str command element, a huge/racing workspace, a workspace deleted mid-run)? Does the extra
   pre/post snapshot on EVERY autonomous contained run regress anything (double hashing the workspace)?
   Does adding provenance_touch/preview keys break any existing consumer?

Calibration: concrete, code-grounded. Distinguish a real recall-worse-than-documented / model-bypass /
totality break from the DELIBERATE advisory-porous posture (token-only match, session-lived counter,
count-not-content, human keeps full reach). If sound, say so plainly."""

USER_TMPL = """Review the SHIPPED F2 autonomy-authorship provenance diff below.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) /
CONCRETE CASE / WHY IT FAILS ITS OWN GOAL or BREAKS AN INVARIANT / WHETHER ANOTHER CONTROL CATCHES IT /
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
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS provenance Code Panel"},
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
    raw = HERE / "raw_provenance"
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
                    f"# provenance code panel: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — provenance CODE panel =======")
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
    print("================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
