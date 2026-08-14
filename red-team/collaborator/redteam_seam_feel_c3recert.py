"""Focused RE-CERTIFICATION of the C3 memory fix for PR #59. The 5-vendor panel certified C1/C2/C4
but NOT C3, on two grounds: (F-growth) unbounded self._history growth, and (F1, grok) HELD +
intervening submit + approve clobbering/interleaving self._history from a stale aliased fork. This
asks the two models that articulated those findings (gpt-5.1, grok) whether the fix RESOLVES each
and whether C3 now certifies — and whether the fix introduced any NEW issue. Reproduce-before-trust;
per-model + total cost.

Usage:  python red-team/collaborator/redteam_seam_feel_c3recert.py [openai/gpt-5.1 ...]
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

_FIX_DIFF = (r"C:\Users\joshe\AppData\Local\Temp\claude\D--Repo-salient-os"
             r"\22d667fc-73c0-41aa-ad58-12ecc49e3371\scratchpad\c3fix.diff")

PRIOR = """THE TWO C3 FINDINGS YOU (THE PANEL) RAISED — re-check EACH against the fix:

FINDING A — UNBOUNDED HISTORY GROWTH (all 5 vendors; the unanimous highest-value fix).
  `_absorb_result` set `self._history = result.history` every turn with NO trimming; each `run_turn`
  re-sent the full history and appended more. Over a long session this overflows the model context
  (unpredictable backend truncation / EMPTY) and grows the process RSS. C3 was NOT-CERTIFIED for
  this. Proposed fix class: bound the history (token/char window; keep system + recent tail).

FINDING B — HELD + INTERVENING SUBMIT + APPROVE CLOBBER/INTERLEAVE (grok F1, MEDIUM).
  Because `self._history` advanced on EVERY outcome (including HELD/PAUSED), this deterministic
  sequence corrupted the conversation:
    1. submit A -> DONE (self._history = H1)
    2. submit B -> HELD (self._history = H2, a partial with a dangling unapproved tool-call)
    3. submit C -> runs with history=H2 (threads the dangling tool-call into an unrelated message)
    4. approve B -> _handle_resume runs from the stale task.history fork; _absorb overwrites
       self._history rooted at H2, REWINDING past turn C.
  Also: `task.history` and `self._history` aliased the one list `run_turn` mutates in place, so a
  later turn could mutate a stored history. C3 NOT-CERTIFIED for this too."""

FIX_DESC = """WHAT THE FIX DOES (the diff is below):
  1. NEW `_trim_history(history)`: returns a FRESH list holding the most-recent messages whose
     contents total <= `_HISTORY_CHAR_BUDGET` (=60000), always keeping >=1 message. Called on the
     history passed to EVERY `run_turn` (both `_handle_turn` and `_handle_resume`). `run_turn`
     re-asserts the system message at history[0], so an old system message dropped by the trim is
     re-inserted. Because it always allocates a new list, it also DE-ALIASES self._history /
     task.history from the list run_turn mutates in place.
  2. `_absorb_result` now advances `self._history` ONLY when `result.stopped not in STOPPED_AWAITING`
     (i.e. terminal DONE/FAILED). A HELD/PAUSED turn no longer advances the shared thread; its
     history stays on `task.history`, which `_handle_resume` reads.
  3. `_fail_active` documents that a hard exception intentionally does NOT advance memory (no
     coherent result.history) — the conversation stays at the last good state.

Three tests were added: `_trim_history` bounds growth + returns a fresh list + keeps the most-recent
(and never returns empty); a HELD step does not advance self._history until resolved; a new message
sent while a step is HELD threads the COMPLETED history, not the held partial (asserts the held
tool-call token does NOT appear in the next turn's messages)."""

SYSTEM = """You are re-certifying a targeted fix to a governed AI agent's Host. You previously did
NOT certify claim C3 (memory-threading soundness) of this PR for two findings (A: unbounded growth,
B: held+intervening-submit+approve clobber + list aliasing). Judge ONLY whether the fix genuinely
resolves each finding, whether it introduces any NEW correctness/safety problem, and whether C3 now
certifies. Be concrete and adversarial: trace the exact HELD + intervening-submit + approve sequence
through the patched `_absorb_result` / `_handle_resume` / `_trim_history`, and reason about the trim
(does dropping an old system message or splitting a turn's messages break anything, given run_turn
re-asserts system[0]? can it ever return empty? does the char budget actually bound context?). If
you cannot break it, say so explicitly."""

USER_TMPL = """{prior}

{fix}

For EACH of finding A and finding B: RESOLVED / NOT-RESOLVED + one-sentence why (trace it).
Then: any NEW issue the fix introduces? (yes+describe / no).
Then: C3 (memory-threading soundness) now: CERTIFIED / NOT-CERTIFIED + one sentence.
Then a one-line VERDICT.

=================== THE FIX DIFF (collaborator/host.py) ===================
{diff}

=================== THE FULL PATCHED host.py ===================
{host}"""

PANEL = ["openai/gpt-5.1", "x-ai/grok-4.5"]
MODELS = sys.argv[1:] if len(sys.argv) > 1 else PANEL


def build_bundle() -> str:
    try:
        diff = pathlib.Path(_FIX_DIFF).read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        diff = f"(could not read fix diff: {e})"
    host = (SOS / "collaborator/host.py").read_text(encoding="utf-8")
    return USER_TMPL.format(prior=PRIOR, fix=FIX_DESC, diff=diff, host=host)


BUNDLE = build_bundle()


def call(model: str) -> dict:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": BUNDLE}],
        "temperature": 0.2, "max_tokens": 4000, "usage": {"include": True},
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://salient-os.local/redteam",
                 "X-Title": "SalienceOS C3 Re-cert"},
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
    raw = HERE / "raw_seam_feel_c3recert"
    raw.mkdir(parents=True, exist_ok=True)
    print(f"bundle chars={len(BUNDLE)}  models={MODELS}")
    results = {}
    with cf.ThreadPoolExecutor(max_workers=len(MODELS)) as ex:
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
                    f"# C3 re-cert: {m}\n\n_cost={_fmt(r.get('cost'))} finish={r['finish']}_\n\n{r['content']}\n",
                    encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n=========== API COST — C3 re-cert ===========")
    total, have = 0.0, False
    for m in MODELS:
        c = results.get(m, {}).get("cost")
        if isinstance(c, (int, float)):
            total += c; have = True
        print(f"  {m:32s} {_fmt(c)}")
    print(f"  {'TOTAL':32s} {_fmt(total) if have else 'n/a'}")


if __name__ == "__main__":
    main()
