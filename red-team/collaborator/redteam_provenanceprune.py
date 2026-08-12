"""LEAN external 5-vendor CODE panel on the SHIPPED provenance-manifest PRUNING (F2 durable-store
follow-up, branch feat/provenance-prune) BEFORE merge. Small, low-risk change (a load-time filter), so
a tight focused pass.

At Session construction, after loading the DURABLE provenance manifest, drop any recorded
autonomy-authored path whose workspace file no longer EXISTS, and re-persist. Rationale: a durable
manifest accumulates; a dropped-then-deleted file lingers, so a human creating a same-named file would
get a FALSE ⚠ (noise-blinding the advisory control) + unbounded growth. Pruning only removes taints for
ABSENT (un-runnable) files.

Hunt specifically for:
  * Does pruning ever drop a LIVE warning — a present autonomous file the human should still be warned
    about? (It should only ever drop ABSENT files.)
  * Does it open a LAUNDERING path — can an autonomous run get its own taint pruned and then have the
    (still-malicious) file present + un-flagged for a human? (Consider: drop -> delete -> recreate, by
    the agent vs by a human; mid-session vs cross-session; the prune is at LOAD only.)
  * TOCTOU / totality — the exists() check timing; a stat error; a symlink whose target is absent; a
    path that is now a DIRECTORY; a huge manifest. Session.__init__ must not raise.
  * Does re-persisting the pruned set interact badly with the untrusted / degraded flags?

Reports per-model + total API cost (standing request).
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
    ("THE PRUNE + CALL SITE — collaborator/session.py (_prune_stale_provenance + the durable-load "
     "block that calls it)", "collaborator/session.py"),
    ("THE RECOGNIZER (what a taint feeds) — collaborator/provenance.py", "collaborator/provenance.py"),
    ("WHAT IS PINNED — tests/test_collaborator_provenancestore.py (PruneStaleProvenance + the durable "
     "persistence tests)", "tests/test_collaborator_provenancestore.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

CODE review of a SHIPPED, SMALL diff (F2 durable-store follow-up). SalienceOS's "Collaborator" flags
workspace files an AUTONOMOUS (not-human-approved) action authored, warning a human (a ⚠ at approval)
before they run an agent-dropped file uncontained. That manifest is now cross-session DURABLE
(integrity-protected, out-of-workspace). THIS DIFF adds PRUNING: at Session construction, after the
durable load, `_prune_stale_provenance` drops any recorded path whose workspace file no longer exists,
then re-persists. It is a QUALITY fix (avoid a false ⚠ on a human's same-named recreate; bound growth),
NOT a security boundary. The recognizer that consumes the manifest is included for reach.

Key invariants to check: pruning removes taints ONLY for ABSENT files (never a present one), so it can
never drop a live warning; a file re-created by an AUTONOMOUS action is re-recorded (re-tainted); a file
re-created by a HUMAN write is human-authored (correctly not flagged). The prune runs at LOAD only (not
mid-session). Session.__init__ must remain total (never raise on a bad path/stat)."""

SYSTEM = """You are a senior security engineer doing a tight adversarial CODE review of a small
load-time pruning step for an ADVISORY provenance manifest. Assume the author is wrong until proven
otherwise, but this is explicitly a quality fix, not a boundary — judge it against ITS stated invariants
(never drop a LIVE warning; don't open a laundering path; stay total). Report only real defects.

For EACH finding: ID / TITLE / SEVERITY / LOCATION (file:line) / CONCRETE CASE / WHY / WHETHER ANOTHER
CONTROL CATCHES IT / FIX. Then STEELMAN + VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + the single
highest-value fix). If sound, say so plainly."""

USER_TMPL = """Review the SHIPPED provenance-pruning diff below.
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
        "temperature": 0.3, "max_tokens": 12000, "usage": {"include": True},
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS prune Code Panel"},
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
        return {"model": model, "error": f"empty (finish={choice.get('finish_reason')})", "usage": usage}
    return {"model": model, "seconds": round(dt, 1), "usage": usage,
            "cost": usage.get("cost"), "content": content, "finish": choice.get("finish_reason")}


def _fmt_cost(c):
    return f"${c:.4f}" if isinstance(c, (int, float)) else "n/a"


def main():
    raw = HERE / "raw_provenanceprune"
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
                print(f"[ OK ] {m}  {r['seconds']}s  cost={_fmt_cost(r.get('cost'))}")
                (raw / f"{slug}.md").write_text(
                    f"# prune code panel: {m}\n\n_finish={r['finish']} cost={_fmt_cost(r.get('cost'))} "
                    f"usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("\n======= API COST — prune CODE panel =======")
    total = 0.0
    have_any = False
    for m in MODELS:
        r = results.get(m, {})
        c = r.get("cost")
        if isinstance(c, (int, float)):
            total += c
            have_any = True
        print(f"  {m:<34} {_fmt_cost(c):>10}")
    print("  " + "-" * 46)
    print(f"  {'TOTAL':<34} {(_fmt_cost(total) if have_any else 'n/a'):>10}")
    print("===========================================")


if __name__ == "__main__":
    main()
