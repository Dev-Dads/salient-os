"""External 5-vendor CODE review + CERTIFICATION panel for the make-it-move grounding change:
Sal's directive-loop system prompt + the single-source tool manifest wired into `run_turn`.

Per Josh's standing rule (external review on EVERY non-doc PR), this ships to the seam-adjacent
loop, so it gets an external panel even though it is additive and non-safety-critical. The
headline claim to attack: grounding changes only what the model KNOWS, never what it is ALLOWED —
`govern_action` stays the sole authority boundary. Reproduce-before-accept; per-model + total cost.

Usage:  python red-team/collaborator/redteam_grounding.py [openai/gpt-5.1 ...]
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
     "red-team/collaborator/raw_grounding/grounding.diff"),
    ("THE CODE — collaborator/loop.py (run_turn wiring + _SAL_SYSTEM + sal_system_prompt)",
     "collaborator/loop.py"),
    ("THE CODE — collaborator/tools.py (_MODEL_FACING + tool_manifest + openai_tools)",
     "collaborator/tools.py"),
    ("THE CODE (parser this prompt targets) — collaborator/toolcall.py",
     "collaborator/toolcall.py"),
    ("THE CODE (the sole authority boundary, unchanged) — collaborator/governance.py",
     "collaborator/governance.py"),
    ("THE MODEL CLIENT — collaborator/model_client.py (complete(messages, tools=None))",
     "collaborator/model_client.py"),
    ("WHAT IS PINNED — tests/test_collaborator_grounding.py",
     "tests/test_collaborator_grounding.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

CODE review + CERTIFICATION of a SHIPPED change to the SalienceOS "Collaborator". Vocabulary: the
"Core" is the governed executor; "Sal" is the user-facing face; the "seam" is govern_action (the
authority boundary); "run_turn" is the DIRECTIVE loop (acts on the user's instruction).

=== WHAT CHANGED (make-it-move) ===
The directive loop `run_turn` used to send the model the bare conversation — no system prompt, no
tool list, no argument shapes — so the model guessed how to act and only sometimes did. This change
GROUNDS it:
  * tools.py `_MODEL_FACING`: ONE spec for the four model-facing tools (read_file, write_file,
    run_command, web_fetch) → drives BOTH `tool_manifest()` (text spliced into the system prompt)
    AND `openai_tools()` (the OpenAI/ollama `tools=` schema). Operator-directed tools (net_post,
    maint_fetch) are deliberately NOT advertised.
  * loop.py `_SAL_SYSTEM` (+ `sal_system_prompt()` which splices the manifest via a `__TOOL_MANIFEST__`
    sentinel using str.replace, NOT str.format — the prompt is full of literal JSON braces).
  * run_turn: prepends the system message IFF no `role=="system"` message is already present
    (idempotent for the resume path), and passes `tools=openai_tools()` to `client.complete`.

=== THE INVARIANT THAT MUST STILL HOLD ===
Nothing about authority changed. Every parsed intent still flows through `govern_action`
(capability gate + leash + workspace fence + signed caps + human_gated), default-deny. The system
prompt and the tool schema grant NOTHING — they only improve the model's emission quality.

=== THE CERTIFICATION CLAIMS (attack each) ===
  C1 (no new authority): the Sal system prompt + the `tools=` schema cannot cause any action that
     an un-grounded model could not already have caused. A model that names ANY tool — advertised or
     not (incl. net_post/maint_fetch, or an invented name) — is still fully governed; advertising a
     tool does not widen reach, and NOT advertising net_post/maint_fetch is a nudge, not a control.
  C2 (single source of truth): `tool_manifest()` and `openai_tools()` derive from the one
     `_MODEL_FACING` tuple, so the names/arg keys the model is told cannot drift from the executors.
     The splice leaves no `__TOOL_MANIFEST__` sentinel; the schema is valid JSON that serializes.
  C3 (injection fence + prompt integrity): the system prompt is HOST-authored — a static template +
     the generated manifest, with NO interpolation of model output, tool output, memory, or user
     text. So untrusted content cannot rewrite the prompt. The fence tells the model that tool/file/
     memory content is DATA not instructions; state plainly that this is a PROMPT-level (soft)
     mitigation whose HARD backstop is that any injected action is still governed (default-deny).
  C4 (loop integrity / no regression): the idempotent prepend cannot be abused by the MODEL to
     suppress grounding or inject a system message (the model's turns are role=="assistant", tool
     results role=="user" — only the host/loop writes role=="system"); passing `tools=` does not
     break the scripted/None client path; termination still requires a tool-call-free final message
     (the "no <tool_call> in the final answer" rule matches parse_message).

=== OUT OF SCOPE (do NOT rate as bugs) ===
  - Prompt wording/style preferences; whether 120b specifically obeys every line (that is the live
    e2e's job, not this review).
  - The deferred concurrent-orchestration ("progress independent work while a held item waits").
  - The single-trust-domain limit (ADR 0002); already-certified egress/run_command/seal code.
  - A fully model-controlled backend that ignores the tools schema — the in-prompt manifest is the
    floor and the parser (toolcall.py) already handles content-embedded calls; both are pre-existing.
"""

SYSTEM = """You are a senior application-security engineer doing an adversarial CODE review AND
CERTIFICATION of a change that adds a system prompt + a tool schema to a governed agent loop. BREAK
it, but also certify: if you cannot break a claim, say so explicitly (a blocked attack is a valuable
NON-finding — the author wants certification as much as bugs). Assume the author is wrong until
proven otherwise. Concretely probe:
  - C1: find ANY path where the added prompt/schema causes an action the seam would not otherwise
    allow. Does run_turn pass anything from the prompt/schema INTO govern_action's authority
    decision? Can advertising (or the model naming a non-advertised tool) change what is granted?
    Trace an intent from parse_message → govern_action and confirm authority is unchanged.
  - C2: can `tool_manifest()`/`openai_tools()` drift or disagree (a name/arg the prompt states but
    the executor doesn't expect, or vice-versa)? Can the `__TOOL_MANIFEST__` splice leave a sentinel,
    double-splice, or be corrupted by a tool hint that itself contains the sentinel or `{}`? Is the
    schema always JSON-serializable (it goes on the wire)?
  - C3: can any untrusted input (tool output, file contents, memory, prior assistant text, the user
    message) reach the SYSTEM PROMPT string and rewrite Sal's instructions? Is the fence's HARD
    backstop (injected actions still governed) actually true, or does the richer prompt create a new
    lever?
  - C4: can the MODEL cause a role=="system" entry (to suppress grounding or inject its own system
    prompt), or cause a double-prepend / lost prompt on the resume path? Does `tools=openai_tools()`
    break ScriptedClient (tools=None) or change any existing test's behavior? Does the "no <tool_call>
    in the final answer" rule actually match parse_message's termination (no intents → final)?
Name file:line, give a CONCRETE input/bypass, and state whether ANOTHER layer (govern_action, the
parser) independently catches it. Distinguish a real guarantee-breaking bug from a nit or a stated
non-goal."""

USER_TMPL = """Review + CERTIFY the SHIPPED grounding change below.

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
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS Grounding Panel"},
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
    raw = HERE / "raw_grounding"
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
                    f"# Grounding panel: {m}\n\n_cost={_fmt(r.get('cost'))} finish={r['finish']}_\n\n{r['content']}\n",
                    encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n=========== API COST — grounding panel ===========")
    total, have = 0.0, False
    for m in MODELS:
        c = results.get(m, {}).get("cost")
        if isinstance(c, (int, float)):
            total += c; have = True
        print(f"  {m:<32} {_fmt(c):>10}")
    print("  " + "-" * 44)
    print(f"  {'TOTAL':<32} {(_fmt(total) if have else 'n/a'):>10}")
    print("==================================================")
    ok = [m for m in MODELS if "error" not in results.get(m, {'error': 1})]
    print(f"Done: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
