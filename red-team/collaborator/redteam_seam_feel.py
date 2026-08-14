"""External 5-vendor CODE review + CERTIFICATION for the ② "make Sal talk back" feel PR (#59):
six fixes across host.py / surface.py / view.py / propose.py / loop.py that give Sal conversation
MEMORY, a VOICE (the conversation thread), action/proposal CONTEXT (target), proposer GROUNDING,
a CLOCK, and UN-TRUNCATED replies. Per Josh's standing rule (external review on EVERY non-doc PR).

Stage A (Host) and Stage B/C (the surface door + controls) are already externally certified; THIS
panel certifies that the feel changes DON'T regress the guarantees — above all P-01 (a new way to
KNOW/SPEAK is never a new way to be GRANTED power) and the surface's textContent-only render.

Reproduce-before-accept; per-model + total cost.

Usage:  python red-team/collaborator/redteam_seam_feel.py [openai/gpt-5.1 ...]
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

# The exact PR diff (written next to this script's run), then the FULL changed modules for context.
_DIFF_PATH = (r"C:\Users\joshe\AppData\Local\Temp\claude\D--Repo-salient-os"
              r"\22d667fc-73c0-41aa-ad58-12ecc49e3371\scratchpad\feel.diff")

_FILES = [
    ("FULL host.py — conversation memory (self._history) + Task.view truncation caps", "collaborator/host.py"),
    ("FULL surface.py — the conversation thread render (attack the textContent discipline)", "collaborator/surface.py"),
    ("FULL view.py — the action/proposal `target` context field", "collaborator/view.py"),
    ("FULL propose.py — the workspace-grounding block", "collaborator/propose.py"),
    ("FULL loop.py — run_turn history re-assertion + the clock (_now_line)", "collaborator/loop.py"),
    ("THE SEAM (unchanged, the SOLE authority boundary) — governance.py", "collaborator/governance.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

CODE review + CERTIFICATION of a SHIPPED feel PR in the SalienceOS "Collaborator". Sal is a
governed AI worker: a `Collaborator` Host (host.py) drives turns via `run_turn` (loop.py); EVERY
action a turn takes passes `govern_action` (governance.py) — the SOLE authority boundary,
default-deny, signed-capability gated. A localhost web surface (surface.py, ALREADY certified)
is WATCH + submit + steer; it imports nothing from governance and calls only host.submit()/
snapshot()/controls. The "seam" is: whose hands move, and every move the system's own hands make
is governed.

=== WHAT THIS PR CHANGES (six fixes; the diff is the first material below) ===
Live-testing showed the surface felt like a governance DASHBOARD, not a partner. Six fixes:
  1. CONVERSATION MEMORY (host.py): the Host now threads ONE running message history across turns
     (`self._history`, set in `_absorb_result`, passed to `run_turn(..., history=self._history)`)
     instead of running each submit blank. Worker-confined: a single serial worker thread means
     exactly one turn is ever in flight. `run_turn` re-asserts its system message (Sal's prompt +
     clock) at history[0] each turn.
  2. VOICE (surface.py): the page is now a You/Sal conversation thread built from the task list —
     Sal's reply / a held step / a failure speaks AS Sal, plus an OPTIMISTIC ECHO of your just-sent
     message + a "Sal is working" pulse. `/submit` returns {task_id}; the echo reconciles when the
     real task appears in snapshot().
  3. CONTEXT (view.py): each Decision and Proposal now carries a `target` string — which file /
     command / url the step acts on (`_action_target`), display-only, capped 200 chars.
  4. GROUNDING (propose.py): `build_proposer_context` prepends the ACTUAL workspace file list
     (fenced DATA, `_neutralize`'d, capped 60 entries) so the proposer stops proposing phantom
     files.
  5. CLOCK (loop.py): `_now_line()` appends a factual current-date/time line to Sal's system prompt
     so it stops guessing the date from training data. Refreshed each turn.
  6. UN-TRUNCATED REPLIES (host.py): `Task.view` reply cap 2000 -> 16000, prompt -> 4000, via
     `_clip` with an honest ellipsis; the old 2000 cap chopped answers mid-word.

=== THE CERTIFICATION CLAIMS (attack EACH, in the shipped code) ===
  C1 (P-01 preserved — THE one rule): NONE of the six fixes creates or widens an authority path.
     No model output, snapshot string, workspace FILENAME, held-step text, `target`, threaded
     history, or clock string can reach an authority field — grant/loosen a leash past its signed
     cap, set autonomous / intent.source, or trigger an emission. `govern_action` remains the sole
     gate and is unchanged. Find ANY path where a feel change smuggles data into an authority
     decision, or where the threaded history / grounding / target influences what is ALLOWED (vs
     merely what is KNOWN or SHOWN).
  C2 (render safety / no XSS): the new conversation thread renders EVERY model/snapshot string
     (reply, target, held text, optimistic echo, workspace-derived text) via textContent only —
     no innerHTML, no HTML sink. Read `el()`, `turn()`, `salContent()`, `renderThread()`,
     `decisionLi`, `proposalLi`. Find any string that reaches an HTML/JS sink, any innerHTML, any
     attribute injection, or a way a crafted reply/target/filename executes or breaks the door.
  C3 (memory-threading soundness): `self._history` threads correctly and safely — it never crosses
     principal/session (single-Host, single worker), a FAILED / HELD / CANCELLED / empty turn does
     not corrupt the next turn's history, and re-asserting history[0] cannot drop or duplicate the
     system grounding. ALSO assess unbounded growth: history grows every turn with no trim — is
     that a correctness/DoS/context-overflow problem, and is it acceptable for single-session v0 or
     a real defect? Find a concrete sequence that corrupts, leaks across, or breaks a turn.
  C4 (context surfacing doesn't leak or mislead): `target` (view.py), the workspace grounding
     (propose.py), and the clock (loop.py) surface CONTEXT to the operator's own auth-walled /state
     — they must not EXFILTRATE (no new egress), must not surface raw write CONTENT or a secret, and
     must not misrepresent governance state. `target` may show a url/command that itself contains a
     credential-shaped value — assess whether that is a real leak given /state is auth-walled and
     operator-only, or acceptable. Find a genuine info-leak or a dishonest render.

=== OUT OF SCOPE (do NOT rate as bugs) ===
  - The already-certified door mechanics (cookie/CSRF/Host-pin/CSP/availability) EXCEPT where a feel
    change newly regresses them.
  - Multi-user / login — single-user localhost is the point.
  - A compromised human endpoint that already holds the session token.
  - Missing deeper "life"/animation — that is ③ by design.
  - gpt-oss returning empty completions — a model quirk handled elsewhere.
  - Polling vs SSE — polling is the chosen v0.
"""

SYSTEM = """You are a senior application-security + systems engineer doing an adversarial CODE
review AND CERTIFICATION of a SHIPPED "feel" change to a governed AI agent's Host and localhost
surface. The prior door + Host were already certified; your job is to prove the SIX feel changes
do NOT regress the guarantees — above all P-01 (a new way to KNOW or SPEAK is never a new way to be
GRANTED power) and the surface's textContent-only render. Assume the author is wrong until proven
otherwise, but if you cannot break a claim, CERTIFY it explicitly (a blocked attack is a valuable
non-finding). Be maximally adversarial about:
  - AUTHORITY (C1): does the threaded `self._history`, the proposer's workspace grounding, the
    `target` field, or the clock EVER flow into an authority decision (a leash, a cap, autonomous/
    source, an emission)? Or only into what the model KNOWS / what the page SHOWS? Trace it. A
    hostile workspace filename, a hostile model reply echoed back into history next turn, a
    crafted `target` — can any of them cross from DATA into AUTHORITY?
  - XSS / render (C2): walk `el()` and every new render path. Is textContent truly the only sink?
    Any innerHTML, insertAdjacentHTML, attribute/style injection, or a string that reaches JS eval?
  - MEMORY (C3): reason about the serial-worker invariant and the history lifecycle across
    QUEUED/RUNNING/AWAITING_APPROVAL/PAUSED/DONE/FAILED/CANCELLED. Can a failed/held/empty turn
    poison the next? Can history[0] re-assertion drop the system grounding? Is unbounded growth a
    real problem?
  - LEAK (C4): does any feel change create egress or surface a secret/raw-content to anywhere
    beyond the operator's auth-walled view?
Name file:line, give a CONCRETE sequence, and say whether another layer independently catches it.
Distinguish a real regression from a nit or a stated non-goal. Also flag any MISSING defense."""

USER_TMPL = """Review + CERTIFY the SHIPPED feel PR below (the DIFF first, then the full modules).

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) /
CONCRETE SEQUENCE / WHICH CLAIM IT BREAKS / WHETHER ANOTHER LAYER CATCHES IT / FIX.
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
    try:
        diff = pathlib.Path(_DIFF_PATH).read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        diff = f"(could not read diff: {e})"
    parts.append(f"\n########## THE PR DIFF (attack THIS change) ##########\n\n{diff}")
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
                 "HTTP-Referer": "https://salient-os.local/redteam",
                 "X-Title": "SalienceOS Seam Feel Code Panel"},
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
    raw = HERE / "raw_seam_feel"
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
                    f"# Seam feel code panel: {m}\n\n_cost={_fmt(r.get('cost'))} finish={r['finish']}_\n\n{r['content']}\n",
                    encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n=========== API COST — seam feel code panel ===========")
    total, have = 0.0, False
    for m in MODELS:
        c = results.get(m, {}).get("cost")
        if isinstance(c, (int, float)):
            total += c; have = True
        print(f"  {m:32s} {_fmt(c)}")
    print(f"  {'TOTAL':32s} {_fmt(total) if have else 'n/a'}")


if __name__ == "__main__":
    main()
