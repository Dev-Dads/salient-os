"""External 5-vendor CODE review + CERTIFICATION panel for the empty-completion fix in run_turn.

Per Josh's standing rule (external review on EVERY non-doc PR). This touches the seam-adjacent
directive loop, so it gets a panel even though it is a robustness fix, not a safety change. The
headline to attack: the retry/temperature recovery changes only WHETHER a model response is
obtained — it can NEVER grant authority, bypass govern_action, or let a no-op be reported as done.
Reproduce-before-accept; per-model + total cost.

Usage:  python red-team/collaborator/redteam_loopfix.py [openai/gpt-5.1 ...]
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
    ("THE DELTA — the exact diff this change ships (read first)",
     "red-team/collaborator/raw_loopfix/loopfix.diff"),
    ("THE CODE — collaborator/loop.py (run_turn + _is_actionable + _complete_actionable + _retry_temperature)",
     "collaborator/loop.py"),
    ("THE CODE — collaborator/model_client.py (per-call temperature override; ScriptedClient)",
     "collaborator/model_client.py"),
    ("THE CODE (the sole authority boundary, UNCHANGED) — collaborator/governance.py",
     "collaborator/governance.py"),
    ("THE CODE (the parser, unchanged) — collaborator/toolcall.py",
     "collaborator/toolcall.py"),
    ("WHAT IS PINNED — tests/test_collaborator_loop.py",
     "tests/test_collaborator_loop.py"),
    ("THE EMPIRICAL FINDING — 00-LOOP-EMPTY-COMPLETION-SYNTHESIS.md",
     "red-team/collaborator/00-LOOP-EMPTY-COMPLETION-SYNTHESIS.md"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

CODE review + CERTIFICATION of a SHIPPED change to the SalienceOS "Collaborator". Vocabulary: the
"Core" is the governed executor; "Sal" is the user-facing face; the "seam" is `govern_action` (the
sole authority boundary); `run_turn` (collaborator/loop.py) is the DIRECTIVE loop that acts on the
user's instruction; a "leash" is act_then_report / propose_first / notify_only.

=== THE BUG THIS FIXES (live-found) ===
gpt-oss:120b intermittently ends a turn after emitting ONLY its private reasoning channel:
finish_reason=stop, content=="", NO tool_calls (completion tokens far under budget — NOT
truncation). run_turn read "no parsed intents" as stopped="final" and returned a success-looking
TurnResult with ZERO decisions — a SILENT no-op narrated as a finished task. This violates the
loop's core promise ("a step that failed can't be narrated as success"). Measured facts: at greedy
temperature the empty is DETERMINISTIC and streaks (a plain same-input retry escaped 0/6; a
prompt "you returned nothing, act now" nudge escaped 0/6); RAISING the temperature escaped it
(temp 0.7 -> 5/6). Empty-proneness is prompt-specific and stable across a model reload.

=== WHAT CHANGED ===
In collaborator/loop.py:
  * `_is_actionable(msg, parsed)` -> True iff the completion DID (parsed.intents), TRIED
    (parsed.ambiguous), or SAID (non-empty parsed.text / content) something. An empty completion is
    none of these.
  * `_complete_actionable(client, history, empty_retries)` -> loops up to (empty_retries+1) attempts:
    attempt 0 uses the client's own temperature; each retry calls
    client.complete(history, tools=openai_tools(), temperature=_retry_temperature(attempt)) with an
    ESCALATING temperature (0.7, 0.85, 1.0 capped). Returns (msg, parsed, actionable).
  * run_turn: replaces the single `client.complete(...)` with `_complete_actionable(...)`. If NOT
    actionable after the budget, returns TurnResult(stopped="empty", reply="(no action taken — the
    model returned an empty response N times)", decisions=<so far>) — NEVER stopped="final".
    empty_retries defaults to 3. Everything after obtaining an actionable completion is unchanged
    (append assistant turn, parse intents, govern EACH via govern_action, feed HONEST results back).
In collaborator/model_client.py:
  * OllamaClient.complete + ScriptedClient.complete gain an optional per-call `temperature` (None ->
    the client's configured default). OllamaClient.max_tokens default 1024 -> 4096.

=== THE CERTIFICATION CLAIMS (attack each) ===
  C1 (no silent no-op): an empty completion (no content, no tool_calls, no ambiguous) can NEVER be
     reported as a finished/successful turn. It is either recovered (a later attempt is actionable)
     or surfaced as stopped="empty" with an explicit reply — never stopped="final" with an empty
     reply. Show any path where empty content silently becomes a "final" success.
  C2 (governance untouched): the retry + temperature logic cannot grant authority, widen reach, or
     bypass the seam. EVERY completion — including a retried, higher-temperature one — still flows
     through govern_action (capability gate + leash + workspace fence + signed caps), default-deny.
     No authority/leash/salience decision reads the temperature, the attempt index, or the retry
     state. A higher temperature changes only token SAMPLING, never what a capability permits (P-01:
     importance/behaviour never buys permission). Trace an intent from a RETRIED completion through
     govern_action and confirm the decision is identical to a first-attempt one.
  C3 (termination + bounded cost): a legitimate final answer (content, no tool call) is actionable
     on attempt 0, so it is NEVER retried (no infinite loop, no wasted spend, no behavior change);
     an ambiguous-only completion is actionable (surfaced, not retried). The empty path is bounded
     by empty_retries (no unbounded call storm), and the OUTER loop is still bounded by
     max_iterations. Show any input that makes _complete_actionable or run_turn spin unboundedly or
     re-roll a valid answer.
  C4 (no regression / interface): the per-call temperature override does not break the None/scripted
     path (temperature defaults to the client's own); propose.py still calls complete() with no
     temperature and is unaffected; the max_tokens 1024->4096 bump is safe; _retry_temperature is
     monotonic and capped in [0.7, 1.0]; the resume/history path (history[0] system re-assert) is
     unchanged; the new stopped="empty" terminal state is a value callers can handle (it is not a
     silent success). Show any existing behavior this breaks.

=== OUT OF SCOPE (do NOT rate as bugs) ===
  - Whether temperature 0.7/0.85/1.0 is the OPTIMAL schedule, or whether gpt-oss "should" behave this
    way — tuning/opinion. The claim is correctness + honesty, not optimality.
  - The residual that a deterministically empty-prone PROMPT can still exhaust the budget and report
    "empty" — that is the HONEST fallback, explicitly a backend/model follow-up, not this loop's job.
  - The single-trust-domain limit (ADR 0002); already-certified egress/run_command/seal/grounding code.
  - The make-it-move greedy-decoding choice for the first attempt (an operator call).
"""

SYSTEM = """You are a senior application-security + correctness engineer doing an adversarial CODE
review AND CERTIFICATION of a robustness fix to a governed agent loop. BREAK it, but also certify:
if you cannot break a claim, say so explicitly (a blocked attack is a valuable NON-finding — the
author wants certification as much as bugs). Assume the author is wrong until proven otherwise.
Concretely probe:
  - C1: find ANY path where an empty completion (no content, no tool_calls) ends up reported as a
    successful/finished turn (stopped="final" with nothing done). Consider: content that is
    whitespace-only; a tool_calls list that is present but empty; parse_message edge cases; the
    interaction of _is_actionable with parsed.text vs _content(msg); the boundary between "empty"
    and a legit terminal answer.
  - C2: does ANYTHING in the retry/temperature path reach govern_action's authority decision? Can a
    higher temperature, the attempt index, or being on a retry change what capability/leash is
    granted, or which tools are advertised, or the workspace fence? Trace parse_message ->
    govern_action for a retried, temp=1.0 completion and confirm authority is byte-identical to the
    first attempt. Can retry cause the SAME action to run twice, or a held/denied action to slip?
  - C3: can _complete_actionable or run_turn spin unboundedly (a call storm), or re-roll a valid
    final answer (extra latency/spend), or drop decisions accumulated before an empty attempt? Is
    the budget arithmetic (max(1, empty_retries+1)) correct for empty_retries in {0,1,3}? Does the
    "empty" return preserve decisions already taken earlier in the same turn?
  - C4: does the optional temperature kwarg break any caller (ScriptedClient tests, propose.py,
    resume path)? Is _retry_temperature monotonic + capped? Does max_tokens 1024->4096 introduce a
    problem? Is stopped="empty" handled or does any existing caller assume stopped in
    {final,held,paused,max_iterations}?
Name file:line, give a CONCRETE input/bypass, and state whether ANOTHER layer (govern_action, the
parser) independently catches it. Distinguish a real guarantee-breaking bug from a nit or a stated
non-goal."""

USER_TMPL = """Review + CERTIFY the SHIPPED empty-completion fix below.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) /
CONCRETE INPUT OR BYPASS / WHY IT BREAKS A GUARANTEE / WHETHER ANOTHER LAYER CATCHES IT / FIX.
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
        "temperature": 0.3, "max_tokens": 6000, "usage": {"include": True},
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS LoopFix Panel"},
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
    raw = HERE / "raw_loopfix"
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
                    f"# LoopFix panel: {m}\n\n_cost={_fmt(r.get('cost'))} finish={r['finish']}_\n\n{r['content']}\n",
                    encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n=========== API COST — loopfix panel ===========")
    total, have = 0.0, False
    for m in MODELS:
        c = results.get(m, {}).get("cost")
        if isinstance(c, (int, float)):
            total += c; have = True
        print(f"  {m:32s} {_fmt(c)}")
    print(f"  {'TOTAL':32s} {_fmt(total) if have else 'n/a'}")


if __name__ == "__main__":
    main()
