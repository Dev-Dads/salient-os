"""Red-team the SHIPPED PR-A delta (branch feat/host-emit-entrypoint) BEFORE merge.

PR A activates AUTONOMOUS OUTBOUND EMISSION for real: it adds the operator entry point `emit()` —
the first sanctioned caller that can direct a net_post to run autonomously — and tightens the
emission auto-lift to REQUIRE BOTH signed signals. This is the highest-consequence capability in
the system (autonomous, credentialed, un-verifiable emission), so both red-team passes are a GATE.

This is a DELTA panel on top of the already-fully-paneled net.post Tier-2 base (PR #31). Attack the
NEW code, not the transport contract (already paneled): the entry point, the require-both floor, the
retirement of signed_leash_cap, the loud notify reason.

Reports per-model + total API cost (standing request).

Usage:  python red-team/collaborator/redteam_prA_emit.py [openai/gpt-5.1 ...]
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
    ("THE DELTA — the exact diff PR A ships (read this first)",
     "red-team/collaborator/raw_prA/prA.diff"),
    ("THE CODE — collaborator/loop.py (NEW: emit() entry point; approve() re-checks the seal)",
     "collaborator/loop.py"),
    ("THE CODE (highest risk) — collaborator/governance.py (REWRITTEN emission floor: 'require both'; "
     "loud notify reason; method-aware derivation; credential injection; re-gate)",
     "collaborator/governance.py"),
    ("THE CODE — collaborator/policycaps.py (signed caps; leash_cap only TIGHTENS; signed_leash_cap "
     "REMOVED — it implemented the rejected auto-cap-alone option)",
     "collaborator/policycaps.py"),
    ("THE CODE — collaborator/toolcall.py (the parser: what `source` values it can produce; can it "
     "ever produce source='host' or pass a keyword leash?)",
     "collaborator/toolcall.py"),
    ("THE CODE — collaborator/propose.py (the proposer: can it pass a keyword leash / reach emit?)",
     "collaborator/propose.py"),
    ("THE CODE — collaborator/egress.py (emission_seal, post(), the credential/preview flow — for "
     "grounding claims about what emit() drives)",
     "collaborator/egress.py"),
    ("WHAT IS PINNED — tests/test_collaborator_egress_gate.py (HostEmitEntryPoint, NetPostAutoLift, "
     "seal, credential, audit-split)",
     "tests/test_collaborator_egress_gate.py"),
    ("WHAT IS PINNED — tests/test_collaborator_loop.py (NetPostF1ModelCannotSelfOriginate)",
     "tests/test_collaborator_loop.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

CODE review of a SHIPPED diff. SalienceOS is a salience-based control plane; invariant P-01:
salience INFLUENCES, policy AUTHORIZES. The "Collaborator" is a governed agent: a PROPOSER model
suggests actions; every action passes ONE core-enforced capability gate (directive.grants_capability
— EXACT-STRING membership over a host-SIGNED capability set; the model cannot forge a capability,
salience cannot add one), plus a LEASH axis (act_then_report / propose_first / notify_only) and a
workspace fence.

net.post (Tier 2, PR #31, already fully paneled) is the OUTBOUND EMISSION channel — genuinely
SENDING data out. Outbound is governed by AUTHORITY, not verification (the verifier observes the
LOCAL world; it CANNOT observe what a remote API did with a POST). Until now the autonomous-emission
path was UNREACHABLE in production: run_turn (the model loop) never passes the keyword `leash`, so a
model-emitted net_post was always human-gated (red-team F1). This PR adds the operator entry point
that USES autonomous emission, and settles the authority rule for it.

WHAT SHIPPED IN THIS PR (attack THIS):
  - collaborator/loop.py emit(session, url, body, *, content_type='application/json',
    autonomous=False): a HOST-facing function (sibling of approve()), NOT reachable from run_turn.
    It builds ToolIntent('net_post', {...}, source='host') and calls
    govern_action(session, intent, leash=(ACT_THEN_REPORT if autonomous else None)). The keyword
    leash is the ONLY positive "the operator is directing THIS emission autonomously" signal; the
    model/parser never carry it. Authority is NOT keyed on `source` (F1 lesson: a parse-channel
    label is not provenance).
  - governance.py EMISSION FLOOR rewritten to REQUIRE BOTH signed signals. An emission lifts to
    act_then_report ONLY when ALL hold: enforced signed session (F5) + host-directed keyword
    leash==ACT_THEN_REPORT + source!='proposed' (F1) + the per-tool net_post leash-cap ALREADY
    permitted act_then_report (i.e. `leash` is still ACT_THEN_REPORT after line-227's leash_cap
    application) + directive grants net.post.auto:<canonical-host>. Otherwise FLOOR to propose_first.
    The `leash == ACT_THEN_REPORT` conjunct is the fix for red-team MINOR-A: the gate and the
    terminal re-cap in execute_and_verify (which re-applies the same leash_cap) must AGREE on an
    unlisted tool, else an auto-lift here silently became a notify-only no-op there. An unlisted
    net_post is already capped to notify_only under enforcement, so it can never be lifted.
  - policycaps.py: signed_leash_cap() REMOVED. It distinguished 'unlisted' (None) from a real cap so
    an auto grant could lift an UNLISTED net_post — the rejected 'auto-cap-alone' option. Retired.
  - governance.py: a LOUD notify-only reason when net.post.auto:<host> is granted but net_post has
    no act_then_report leash-cap (so 'require both' reads as a missing signal, not a silent no-op).

STEER (Josh's decisions — do NOT re-litigate as 'wrong', only whether the CODE implements them):
autonomous emission REQUIRES BOTH the per-host net.post.auto:<host> capability AND an explicit
net_post act_then_report leash-cap; the operator directs a specific emission via emit(autonomous=
True) (motivating case: OpenRouter red-team panels); credentials are host-injected, never model-
supplied, never logged; audit is body-free for autonomous, bounded-preview for human-gated.

OUT OF SCOPE (assume correct — already paneled or stated non-goals): the transport contract in
egress.post (redirect/IP-pin/TLS/body-cap/CRLF — paneled in PR #31), the core capability-gate
internals, ADR 0001/0002, non-Linux run_command parity (a SEPARATE PR), exfil of the operator's own
secret THROUGH a consented host, offense through a sanctioned channel. Same-channel egress is LOGGING
not verification. Attack the NEW entry point + authority logic.
"""

SYSTEM = """You are a senior application-security engineer doing an adversarial CODE review of a
newly-activated AUTONOMOUS OUTBOUND-EMISSION entry point and its authority gate. Your job is to
BREAK it, not praise it. Assume the author is wrong until proven otherwise. Concretely try to:
  - make a MODEL-emitted or PROPOSER-emitted net_post run autonomously (RAN, not HELD) — i.e. defeat
    the F1 guarantee that only the operator (via emit's keyword leash) can direct autonomy. Can the
    parser (toolcall.py) or proposer (propose.py) ever set source='host' or pass a keyword leash into
    govern_action? Is emit() reachable from any model-influenced path?
  - make an emission lift to act_then_report with only ONE of {net.post.auto:<host>, net_post
    act_then_report leash-cap} — defeat 'require both'. Probe the UNLISTED-net_post case specifically
    (MINOR-A): confirm it neither emits nor silently no-ops.
  - satisfy net.post.auto:<hostA> while POSTing to a DIFFERENT place (IDNA/unicode/case/port/
    trailing-dot/userinfo/subdomain) — canonical-host confusion between the auto-cap check and the
    connect host.
  - reach autonomous emission from a NON-enforced (mutable session.capabilities) session (F5).
  - break approved==sent THROUGH emit: mutate held.args (url/body/content_type) between emit() and
    approve() — is the seal re-checked and the decision NOT consumed on mismatch?
  - leak or inject a credential via emit (model args auth/authorization/headers; credential in
    EgressRecord/preview/reason/summary(); credential sent to a non-consented host).
  - make emit()/approve() RAISE instead of failing closed (lone surrogate / huge / non-str body;
    non-ASCII content_type).
Also check: does retiring signed_leash_cap leave any dangling reference or change an unrelated path?
does the 'require both' change weaken any EXISTING guarantee (web_fetch GET, run_command)?

Name file:line, give a CONCRETE input/bypass, and state whether ANOTHER check independently catches
it (a blocked attack is a valuable NON-finding). Distinguish a real guarantee-breaking bug from a
belt-and-suspenders nit or a stated non-goal — do not invent severity."""

USER_TMPL = """Review the SHIPPED PR-A delta below (host-side autonomous emission + require-both).

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) /
CONCRETE INPUT OR BYPASS / WHY IT BREAKS A GUARANTEE / WHETHER ANOTHER CHECK CATCHES IT / FIX.
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
        text = p.read_text(encoding="utf-8") if p.exists() else f"(missing: {rel})"
        parts.append(f"\n\n########## {label} ##########\n\n{text}")
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
                 "HTTP-Referer": "https://salient-os.local/redteam",
                 "X-Title": "SalienceOS PR-A host-emit Code Panel"},
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
    raw = HERE / "raw_prA"
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
                    f"# PR-A host-emit code panel: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — PR-A host-emit CODE panel =======")
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
