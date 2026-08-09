"""Red-team the ACTUAL v3 IMPLEMENTATION (not the design) — does the shipped code DELIVER
the guarantees `06-memory-design-v3.md` claims? The design was panel-reviewed twice, but
the modules that shipped (memory.py, factsource.py, memory_ingest.py, vetoledger.py + the
propose.py wiring) did not exist when those panels ran. This is the code-vs-claim review.

Bundles the real modules + the v3 spec + the test file (so reviewers see what IS pinned and
hunt for what is NOT). Reports per-model + total API cost (Josh's standing request).

Usage:  python red-team/collaborator/redteam_memory_v3_impl.py [general x-ai/grok-4.5 ...]
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
    ("THE CLAIMS — 06-memory-design-v3.md", "red-team/collaborator/06-memory-design-v3.md"),
    ("THE CODE — collaborator/memory.py", "collaborator/memory.py"),
    ("THE CODE — collaborator/factsource.py", "collaborator/factsource.py"),
    ("THE CODE — collaborator/memory_ingest.py", "collaborator/memory_ingest.py"),
    ("THE CODE — collaborator/vetoledger.py", "collaborator/vetoledger.py"),
    ("THE CODE — collaborator/propose.py (proposer seam + build_proposer_context)", "collaborator/propose.py"),
    ("WHAT IS ALREADY PINNED — tests/test_collaborator_memory.py", "tests/test_collaborator_memory.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

VERIFICATION PASS: this exact code was JUST hardened in response to a prior code-level panel
that found (and we fixed) a `\\b`-regex neutralize bug, render_history not neutralizing tuples,
`_flatten` keeping structure-forging codepoints + fence markers, `normalize_intent` doing no
path/command normalization, an isinstance (subclass-permissive) doer type guard, a
constructor-overridable DeedEvent.provenance, an unfenced `extra`, a raw-generator error path,
and a key-channel privacy leak in `system_admits`. Attack the CODE AS IT NOW STANDS: confirm
those fixes actually hold, AND hunt for anything they missed, regressed, or newly introduced.

This is a CODE review of a SHIPPED implementation, not a design review. SalienceOS P-01:
salience INFLUENCES, policy AUTHORIZES; ③ signed PolicyCaps gates every run (assumed correct,
out of scope). The two-agent memory architecture (a history-blind DOER on the fact layer + a
separate PROPOSER reading CDMS gist TUPLES; deeds stamped `ambiguous` = gist-but-never-scar)
was already adversarially reviewed at the DESIGN stage across two panels; do NOT re-litigate
the architecture or demand a cryptographic doer<->store boundary (single trust domain, ADR
0002).

ATTACK WHETHER THE CODE DELIVERS ITS CLAIMED GUARANTEES. The v3 spec claims four STRUCTURAL
controls (code+test pinned) and two BEHAVIORAL defenses (canary-tested, model-dependent):
  A doer is history-blind — `factsource.assemble_doer_context` rejects a HistoryView by type.
  B proposer memory is gist-tuple ONLY — `memory.py` has no retrieve/history/episodic API,
    errors return empty (never raw recall).
  C deeds ingest `ambiguous` + source-tagged, ledger-only (no prose) — `memory_ingest.py`.
  D ③ gates every run — memory only feeds the proposer's surfacing threshold, never leash/caps.
  E DATA fence over facts AND tuples — `factsource.render_facts` / `_neutralize`,
    `memory.render_history` / `_flatten`.
  F observer-stance renderer — third-person, no first/second person.
Plus: `factsource.system_admits` (the all-users system-store admission predicate) and
`vetoledger` (the decaying veto inhibitor). The test file shows exactly what is pinned.

The core salienceos + ③ are correct; attack the NEW code and whether its behavior matches the
doc.
"""

SYSTEM = """You are a senior security engineer doing a CODE review of a shipped memory layer,
checking whether the implementation actually delivers the guarantees its design doc claims. The
architecture was already design-reviewed; do not re-argue it. Find where the CODE fails its own
claim. The test file is included — assume anything it pins is covered; hunt for what it does NOT
pin.

Attack hardest, in order (name file:line and give a concrete bypass string/input where relevant):
1. FENCE ESCAPE (E): can content rendered by `render_facts`/`render_history` break OUT of the
   `<<facts…>>` / `<<observed-history…>>` fence or forge structure? Consider: a fact value or
   tuple field containing the literal close/open marker (`<<end facts>>`, `<<observed-history…>>`),
   the `- [tier] key = value` line shape, or role markers. `_flatten` strips control chars and
   newlines and caps at 160 chars — is that enough, or can a single-line payload still read as an
   instruction or a new fenced block?
2. NEUTRALIZE BYPASS (E): `_neutralize` uses two regexes (`_IMPERATIVE_MARKERS`, `_TOOLJSON_MARKER`).
   What instruction-shaped inputs do they MISS — non-English, homoglyphs/unicode, indirection
   ("the maintainer prefers you run…"), base64/rot13, spacing/casing tricks, tool shapes without a
   `:`/`=` (e.g. `run_command ["rm","-rf"]`), markdown/comment wrappers? Give payloads that pass
   through as apparent instructions.
3. SYSTEM-STORE ADMISSION BYPASS: `system_admits` — can a sensitive value slip the `_DENY_VALUE`
   regex while being typed bool/int? Can a private/pointer value be encoded to pass? Is the
   allowlist (`os.*`, `hw.*`, `pkg.*.installed`, `svc.*`) too broad — an allowlisted key whose
   admitted value still leaks or misleads? Any regex false-negative (e.g., IPv6, UNC paths,
   `~/`, env-var refs, uppercase creds)?
4. RAW-RECALL REACHABILITY (B): the import-ban test greps for call shapes. Is the ban actually
   complete, or can raw episodic be reached anyway — via `CdmsMemorySource`'s host-injected
   `gist_reader` returning raw episodic rows, a tier argument, or any indirection the grep misses?
   Does "errors return empty" hold on every path?
5. TYPE-GUARD CIRCUMVENTION (A): `assemble_doer_context` uses isinstance(FactView)/reject
   HistoryView. Can it be bypassed — a subclass, duck-typing, a FactView constructed over a
   history-backed source, or a code path that assembles doer context WITHOUT this function?
6. VETO-KEY BYPASS (S5): `normalize_intent` keys on tool + path/command. Can a vetoed action be
   re-proposed under a trivially different key that is semantically the same (trailing slash, `./`
   prefix, path casing on case-insensitive FS, equivalent command, `write_file` same path different
   content)? Does the inhibitor decay/compound as claimed?
7. INGEST INTEGRITY (C): does `ingest_deed`/`to_turn_event` truly carry no model prose, and are any
   fields attacker-controllable in a harmful way (tool name, args feeding the hash, session_id/source
   marker forgeable)? Is `ambiguous` guaranteed (could a caller pass a decision that yields trusted)?
8. INFLUENCE != AUTHORITY (D): trace `propose()` — can memory/fact content reach `leash`,
   `capabilities`, `importance`, or the veto bar in a way that changes what RUNS (not just what
   surfaces)? Is the veto delta applied only to surfacing?

Calibration: concrete, honest, code-grounded. Every finding: file:line + a concrete input/bypass +
why the claimed guarantee fails + a fix. Distinguish a real guarantee-breaking bug from a
belt-and-suspenders nit. Behavioral defenses (fence/stance) are model-dependent by design — a
finding there should show a payload the CODE fails to neutralize, not merely "LLMs can be tricked".
If the code soundly matches its claims with only minor notes, say so plainly."""

USER_TMPL = """Review the SHIPPED v3 MEMORY IMPLEMENTATION against its claims below.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) / CONCRETE INPUT OR BYPASS / WHICH CLAIM IT BREAKS / FIX. Then STEELMAN (2-3 sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence).

=================== BEGIN MATERIAL ===================
{bundle}
=================== END MATERIAL ==================="""

# DIFFERENT panel from pass 1 (deepseek/grok/mistral/kimi/glm) — fresh adversarial eyes,
# five distinct vendors (OpenAI, Google, Anthropic, Qwen, Meta).
PANEL = ["openai/gpt-5.1", "google/gemini-2.5-pro", "anthropic/claude-opus-4.1",
         "qwen/qwen3-max", "meta-llama/llama-4-maverick"]
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
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS Memory v3 Code Red-Team (verify)"},
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
    raw = HERE / "raw_memory_v3_verify"
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
                    f"# Memory v3 CODE red-team: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — ④ memory v3 CODE review (VERIFY pass) =======")
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
    print("==========================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
