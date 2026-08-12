"""External 5-vendor CODE panel on the SHIPPED per-destination emission-quota + method->cap
fail-closed diff (ADR 0003 residual sweep, branch feat/emission-quota) BEFORE merge.

Lower-risk than the sandbox work (a bounded session counter + a fail-closed capability mapping; no
new privileged surface, no network mechanism) — a single lean pass. Hunt for: a quota BYPASS (an
emission path that skips the check/consume), a MISCOUNT (double-count, no-count, or off-by-one on the
hold->approve path), model-REACHABILITY of the quota/counter, a FAIL-OPEN on a malformed quota, and
any regression from the method->cap change.

Reports per-model + total API cost (standing request).

Usage:  python red-team/collaborator/redteam_emission_quota.py [openai/gpt-5.1 ...]
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
    ("THE QUOTA STATE + VALIDATION — collaborator/session.py (emission_quota, emission_allowed, "
     "consume_emission, _validate_emission_quota)", "collaborator/session.py"),
    ("THE HOOK — collaborator/governance.py (execute_and_verify egress_log branch: check + consume at "
     "the dispatch point)", "collaborator/governance.py"),
    ("THE METHOD->CAP FIX — collaborator/egress.py (required_capability)", "collaborator/egress.py"),
    ("THE EMISSION ENTRY POINTS (all route through execute_and_verify) — collaborator/loop.py "
     "(emit + approve)", "collaborator/loop.py"),
    ("WHAT IS PINNED — tests/test_collaborator_emission_quota.py", "tests/test_collaborator_emission_quota.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

CODE review of a SHIPPED diff (ADR 0003 residual sweep), not a design review. SalienceOS's
"Collaborator" governs outbound emission (net_post) with: a per-host capability (net.post:<host>,
default-deny, exact-match), a leash (propose_first floor; autonomy needs a SIGNED net.post.auto:<host>
+ a host-directed signal), a hold-time payload seal, and per-request byte/time caps. What was MISSING:
a bound on HOW MANY emissions a session may send to one host — a granted host could be an unbounded
drip channel.

THIS DIFF adds two things:
1. PER-DESTINATION EMISSION QUOTA. `Session(emission_quota=...)` is host config (never model-chosen):
   None = unlimited (default); an int = the same cap for every host; a {host: int} dict = per-host cap
   (unlisted host = unlimited). Validated at construction (fail loud on a malformed value, incl. bool).
   The counter is session-lived in-memory (`_emission_counts`). It is checked + consumed in
   `execute_and_verify`'s `egress_log` + `tool.mutating` branch — the SINGLE dispatch point that BOTH
   the autonomous path (govern_action -> RAN) and the human-approved path (approve -> execute_and_verify)
   reach, and where emit() lands too (emit builds a net_post intent and calls govern_action). Fail
   closed: over quota -> DENIED, the bytes never leave. Consume happens right BEFORE dispatch, so a
   failing attempt still burns quota (bounds a retry channel). getattr-guarded so a legacy/lightweight
   session simply has no quota.
2. METHOD->CAP FAIL-CLOSED. `required_capability(url, method)` used to map POST->net.post: and
   ANYTHING ELSE->net.get: (the read cap). Now: GET/HEAD->net.get:, POST->net.post:, and any OTHER verb
   (PUT/DELETE/PATCH/...) -> None -> DENY. No tool issues those verbs today (web_fetch=GET, net_post=POST),
   so this is a latent gap closed by construction: a future write verb must be wired to its OWN cap,
   never inherit read authority. None/"" default to GET (unchanged).

Documented, NOT findings: the counter is per-SESSION (cross-session durability is a noted follow-up —
enough to bound a runaway run); the quota is a COUNT bound, not content inspection; web_fetch (GET,
non-mutating) is deliberately NOT counted (it is a read, not an emission).
"""

SYSTEM = """You are a senior security engineer doing an adversarial CODE review of a per-destination
emission-quota + a method->capability fail-closed change in a governed agent. Assume the author is
wrong until proven otherwise. Break it: get MORE emissions to a host than the quota allows, make the
counter MISCOUNT, reach the quota/counter from the MODEL's path, or FAIL OPEN.

Attack, name file:line, give a CONCRETE case, and say whether another control catches it:
1. QUOTA BYPASS. Is `execute_and_verify`'s egress_log+mutating branch truly the ONLY place an emission
   dispatches? Any path that sends a net_post without passing it (a direct egress.post; a different
   verify_mode; web_fetch mutating; a tool with egress=True but mutating=False that still emits; the
   approve() re-gate; a retry)? getattr-guarded check — can a session legitimately lack emission_allowed
   yet still be a real emitter (fail open)?
2. MISCOUNT. Off-by-one (is the Nth allowed and N+1 denied, or N-1?); double-count or no-count on the
   hold->approve path (does a HELD-then-approved emission consume once, at approve, not at hold?); does a
   DENIED/HELD/NOTIFIED emission wrongly consume, or a RAN one wrongly not-consume? Concurrency (two
   in-flight emissions racing the counter — is that in scope for a single-threaded governance loop)?
3. MODEL REACHABILITY. Can the model influence emission_quota, _emission_counts, or emission_allowed
   (it is session state)? Can a model-set field (session.capabilities, leash_overrides) interact to
   widen it? Is emission_quota validated so a truthy-but-meaningless value (e.g. bool) can't disable it?
4. FAIL-OPEN. A malformed quota that slips validation and disables the bound; a dict with a non-canonical
   host key so the cap never matches the canonicalized emit_host (does the key have to equal
   egress.canonical_host output — is that a footgun where the operator sets {"API.Example":1} but the
   host canonicalizes to "api.example" and the cap silently never applies)? None-host handling.
5. METHOD->CAP REGRESSION. Does the new mapping break any legitimate GET/POST, or open a new fail-open
   (a verb that should map to a write cap but returns a read cap, or vice versa)? Case-sensitivity;
   None/"" handling; does anything downstream assume required_capability is never None for a "valid" URL?

Calibration: concrete, code-grounded. Distinguish a real bypass/miscount/fail-open from a documented
scope note (per-session counter; count-not-content; web_fetch not counted). The canonical-host-key
footgun in (4) is worth a clear verdict — is it a real trap or acceptable given host config is trusted?
If sound, say so plainly."""

USER_TMPL = """Review the SHIPPED emission-quota + method->cap diff below.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) /
CONCRETE CASE / WHY IT BYPASSES/MISCOUNTS/FAILS-OPEN / WHETHER ANOTHER CONTROL CATCHES IT / FIX. Then
STEELMAN (2-3 sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence + the single
highest-value fix).

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
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS emission-quota Code Panel"},
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
    raw = HERE / "raw_emission_quota"
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
                    f"# emission-quota code panel: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — emission-quota CODE panel =======")
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
    print("====================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
