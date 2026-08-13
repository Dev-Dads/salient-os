"""External 5-vendor CODE review + CERTIFICATION for the large/batched-tool-call reliability fix:
never silently drop a large/truncated/malformed/batched call (surface it), + retry-on-truncation.

Per Josh's standing rule (external review on EVERY non-doc PR). Headline to attack: a large or
batched tool call is NEVER silently lost, and the recovery (surface + grow max_tokens + retry)
cannot grant authority, run a partial/duplicate action, or bypass govern_action.
Reproduce-before-accept; per-model + total cost.

Usage:  python red-team/collaborator/redteam_largecall.py [openai/gpt-5.1 ...]
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
     "red-team/collaborator/raw_largecall/largecall.diff"),
    ("THE CODE — collaborator/toolcall.py (parser: surface, never drop)",
     "collaborator/toolcall.py"),
    ("THE CODE — collaborator/loop.py (_complete_actionable: empty + truncation retries)",
     "collaborator/loop.py"),
    ("THE CODE — collaborator/model_client.py (per-call max_tokens + finish_reason)",
     "collaborator/model_client.py"),
    ("THE CODE (sole authority boundary, UNCHANGED) — collaborator/governance.py",
     "collaborator/governance.py"),
    ("WHAT IS PINNED — tests/test_collaborator_toolcall.py",
     "tests/test_collaborator_toolcall.py"),
    ("WHAT IS PINNED — tests/test_collaborator_loop.py",
     "tests/test_collaborator_loop.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

CODE review + CERTIFICATION of a SHIPPED reliability change to the SalienceOS "Collaborator".
Vocabulary: the "seam" is `govern_action` (the sole authority boundary, default-deny); `run_turn`
(collaborator/loop.py) is the DIRECTIVE loop; the parser is `collaborator/toolcall.py`
(`parse_message` -> ParseResult{intents, ambiguous, text}); "ambiguous" = tool-shaped but NOT
run, surfaced to the human.

=== THE CONCERN THIS ADDRESSES ===
"Rejection of large or a large SET of tool calls." Verified there are NO arbitrary caps (a 50KB
write_file content and 25 calls in one turn both parse; each call is governed independently). The
real risk was SILENT LOSS: (a) a truncated <tool_call> block (clipped by max_tokens) was silently
dropped — not even surfaced — and leaked into the reply; (b) a whole-content JSON ARRAY with one
malformed element dropped the ENTIRE batch silently.

=== WHAT CHANGED ===
- toolcall.py `_tool_call_tag_objects`: now also returns UNBALANCED (truncated/never-closed) spans
  (balanced=False, spanning to end-of-content). `parse_message`: an unbalanced <tool_call> is
  appended to `ambiguous` (surfaced) and stripped from the prose `text`; a whole-content JSON list
  that does not FULLY coerce is appended to `ambiguous` (via `_looks_toolish`) instead of dropped.
  Behavior for valid structured / <tool_call> / whole-content calls is unchanged.
- model_client.py `complete(messages, tools=None, temperature=None, max_tokens=None)`: per-call
  temperature AND max_tokens overrides (None -> the client default); attaches `finish_reason` to
  the returned message dict (never sent back on the wire — run_turn builds its own assistant turns).
- loop.py `_complete_actionable`: two failure modes, two perturbations. EMPTY (reasoning-only, no
  content/call) -> escalate TEMPERATURE (0.7->0.85->1.0). TRUNCATED (finish_reason=="length") ->
  GROW max_tokens (double from the client's base, capped at 65536) and retry, so a clipped large
  call can complete. Retries bounded by empty_retries (default 3). Still unrunnable after the
  budget -> `(msg, parsed, actionable=_is_actionable(...))`; run_turn surfaces empty as
  stopped="empty", and a persistently-truncated-but-parseable turn surfaces its ambiguous.

=== THE CERTIFICATION CLAIMS (attack each) ===
  C1 (no silent loss): a large / truncated / malformed / batched tool call is NEVER silently
     dropped. Every tool-shaped thing that is not RUN is either surfaced as `ambiguous` or (empty)
     surfaced as stopped="empty". Find any path where a call vanishes with NO trace in intents,
     ambiguous, OR text.
  C2 (governance untouched): surfacing + the truncation/temperature retries cannot grant authority
     or bypass the seam. Every parsed intent still flows through govern_action (default-deny); an
     `ambiguous` item is NEVER run; a completion produced at a grown max_tokens or raised
     temperature is governed IDENTICALLY to a first-attempt one; finish_reason/max_tokens never
     enter an authority decision. Trace an intent from a RETRIED (grown-budget) completion through
     govern_action and confirm authority is unchanged.
  C3 (no partial/duplicate run + bounded + strictness preserved): the truncation retry never runs
     a PARTIAL or DUPLICATE action (a clipped call is ambiguous, not executed; a completed retry
     runs once). Budget growth is bounded (<=65536) and retries are bounded (empty_retries). A
     fully-valid batch still runs ALL its calls; a MIXED whole-content batch is still NOT partially
     run (strictness preserved) — only surfaced.
  C4 (no regression / interface): the per-call max_tokens override + finish_reason attachment do
     not break ScriptedClient, propose.py (calls complete() with no overrides), or the resume path;
     attaching finish_reason to the returned dict cannot corrupt a subsequent request (run_turn
     builds fresh assistant turns); the unbalanced-span handling cannot mis-span a VALID call
     followed by prose (a valid call is balanced and consumes only its own span); the parser's
     existing structured / content-block / whole-content-object behavior is unchanged.

=== OUT OF SCOPE (do NOT rate as bugs) ===
  - run_turn not catching a client HTTP error / timeout (a transient 500 crashing the turn) — a
    known general robustness item filed for a separate follow-up; NOT introduced or claimed here.
  - The reply for an ambiguous-only turn still being stopped="final" with leftover text — a
    pre-existing shape (grok noted it in the #53 panel), not changed here.
  - Whether gpt-oss "should" emit large calls / temperature or budget schedule tuning (opinion).
  - The single-trust-domain limit (ADR 0002); already-certified egress/run_command/seal code.
"""

SYSTEM = """You are a senior application-security + correctness engineer doing an adversarial CODE
review AND CERTIFICATION of a reliability fix to a governed agent loop + its tool-call parser.
BREAK it, but also certify: if you cannot break a claim, say so explicitly (a blocked attack is a
valuable NON-finding). Assume the author is wrong until proven otherwise. Concretely probe:
  - C1: find ANY input where a tool-shaped thing is neither run, nor in `ambiguous`, nor in `text`
    — i.e. vanishes. Consider: an unbalanced <tool_call> mixed with a valid one; multiple markers
    where one is truncated; a whole-content list of dicts none of which `_looks_toolish`; nested
    braces in a clipped span; the `remaining`/text stripping of unbalanced spans.
  - C2: does surfacing or either retry reach govern_action's authority decision? Can a grown
    max_tokens / raised temperature / finish_reason change what capability or leash is granted, or
    cause an `ambiguous` item to run? Trace parse_message -> run_turn -> govern_action for a
    grown-budget retry and confirm authority is byte-identical.
  - C3: can the truncation retry run a PARTIAL write (a clipped call executed) or the SAME action
    twice (a completed call plus a retry)? Is the budget growth actually bounded (min(65536, ...))
    and the retry count bounded (max(1, empty_retries+1)) for empty_retries in {0,1,3}? Does a
    valid whole-content batch still run ALL calls; is a mixed batch still NOT partially run?
  - C4: does the max_tokens kwarg / finish_reason attachment break ScriptedClient, propose.py, or
    the resume path? Can attaching finish_reason to the returned dict leak onto the wire on a later
    call? Can `_tool_call_tag_objects` now mis-handle a VALID <tool_call> followed by prose, or a
    valid call followed by a truncated one (span overlap / lost text)?
Name file:line, give a CONCRETE input/bypass, and state whether ANOTHER layer (govern_action, the
parser) independently catches it. Distinguish a real guarantee-breaking bug from a nit or a stated
non-goal."""

USER_TMPL = """Review + CERTIFY the SHIPPED large/batched-tool-call reliability fix below.

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
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS LargeCall Panel"},
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
    raw = HERE / "raw_largecall"
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
                    f"# LargeCall panel: {m}\n\n_cost={_fmt(r.get('cost'))} finish={r['finish']}_\n\n{r['content']}\n",
                    encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n=========== API COST — largecall panel ===========")
    total, have = 0.0, False
    for m in MODELS:
        c = results.get(m, {}).get("cost")
        if isinstance(c, (int, float)):
            total += c; have = True
        print(f"  {m:32s} {_fmt(c)}")
    print(f"  {'TOTAL':32s} {_fmt(total) if have else 'n/a'}")


if __name__ == "__main__":
    main()
