"""Red-team the SHIPPED ADR 0003 Tier-2 net.post build (branch feat/net-post-tier2) BEFORE merge.

A CODE panel on the actual implementation — the Collaborator's first OUTBOUND EMISSION path
(the less-reversible, un-verifiable channel: sending data OUT). Authority-floor code + a new
trust surface -> both passes are a GATE (ADR 0003 Verification).

Attack surface (hand reviewers the real modules + the tests, so they hunt for what the tests DON'T
pin):
  1. egress.post — the mediated emission client. Break the transport contract for POST: does a
     redirect ever re-send the BODY or the CREDENTIAL to an attacker Location? Is auth host-injected
     only (never from model args), never logged, sent only to the pinned canonical host? Body cap /
     content-type (CRLF) injection / IP-pin/SSRF / canonical==connect for POST.
  2. governance — the METHOD-AWARE derivation (net.get:<host> vs net.post:<host>: reading a host is
     NOT emitting to it) + the EMISSION FLOOR / auto-lift (net.post.auto:<host>) + the proposer
     floor + the credential lookup + the emission-TOCTOU re-gate + the execute_and_verify leash
     threading.
  3. tools — net_post registration (mutating, propose_first, egress_log), _exec_net_post (never
     reads auth from args), execute_tool routing.

Reports per-model + total API cost (standing request).

Usage:  python red-team/collaborator/redteam_net_post.py [openai/gpt-5.1 ...]
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
    ("THE SPEC — docs/adr/0003-outbound-authority-and-prohibition-floor.md (see Tier 2 + revisit #2)",
     "docs/adr/0003-outbound-authority-and-prohibition-floor.md"),
    ("THE CODE (highest risk) — collaborator/egress.py (the mediated client; NEW: post())",
     "collaborator/egress.py"),
    ("THE CODE — collaborator/governance.py (method-aware derivation, EMISSION FLOOR/auto-lift, "
     "credential injection, execute_and_verify leash threading, re-gate)",
     "collaborator/governance.py"),
    ("THE CODE — collaborator/tools.py (net_post registration, _exec_net_post, execute_tool)",
     "collaborator/tools.py"),
    ("THE CODE — collaborator/loop.py (approve passes the held leash into execute_and_verify)",
     "collaborator/loop.py"),
    ("THE CODE — collaborator/session.py (egress_credentials host-config map)",
     "collaborator/session.py"),
    ("THE CODE — collaborator/policycaps.py (signed caps; leash_cap only TIGHTENS)",
     "collaborator/policycaps.py"),
    ("WHAT IS PINNED — tests/test_egress.py", "tests/test_egress.py"),
    ("WHAT IS PINNED — tests/test_collaborator_egress_gate.py", "tests/test_collaborator_egress_gate.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

CODE review of a SHIPPED diff: ADR 0003 Tier 2 `net.post` — the Collaborator's OUTBOUND EMISSION
path. SalienceOS is a salience-based control plane. Invariant P-01: salience INFLUENCES, policy
AUTHORIZES. The "Collaborator" is a governed agent: a PROPOSER model suggests actions; every action
passes ONE core-enforced capability gate (directive.grants_capability(cap) — EXACT-STRING membership
over a host-SIGNED capability set; the model cannot forge a capability, salience cannot add one),
plus a leash axis (act_then_report / propose_first / notify_only) and a workspace fence.

Tier 1 (already shipped) is `web_fetch` (GET, net.get:<host>). run_command is netns-isolated on
Linux (no route out), so `egress` is the sole IP-network path. This diff adds the OTHER half —
`net_post`, genuinely SENDING data out. Outbound is governed by AUTHORITY, not verification (the
verifier observes the LOCAL world; it CANNOT observe what a remote API did with a POST).

WHAT SHIPPED (attack THIS):
  - egress.post(url, body, *, content_type, auth, keep_preview, ...): reuses the whole Tier-1
    contract (canonical_host == connect host, resolve-once + IP-pin + private/CGNAT/metadata block,
    HTTPS-only, no-redirect-fail-closed, bounds). Adds: a capped/hashed request BODY (MAX_POST_BODY);
    a HOST-INJECTED Authorization credential `auth` (NEVER from model args, NEVER logged); a
    body-free-vs-bounded-preview audit split (request_body_preview populated ONLY when keep_preview).
  - governance: for an egress tool required_cap = required_capability(url, method) — net.get:<host>
    for GET, net.post:<host> for POST (SEPARATE namespaces). EMISSION FLOOR: a side-effecting egress
    (net_post) is FLOORED to propose_first UNLESS the signed caps grant net.post.auto:<host> AND the
    intent is not source=="proposed" (then it lifts to act_then_report; a signed leash_cap may still
    tighten). Credential: session.egress_credentials.get(canonical_host) injected at execute time
    for the consented host only. Re-gate (reauthorized_or_denied) re-derives net.post:<host> and
    re-checks the signed allowlist at approval (emission TOCTOU). execute_and_verify now takes the
    EFFECTIVE leash (so autonomous=act_then_report vs gated=propose_first drives the audit split).
  - tools: net_post (mutating, default propose_first, verify_mode egress_log). _exec_net_post reads
    ONLY url/body/content_type from args — never an auth field. execute_tool routes net_post's
    egress_preview/egress_auth.

STEER (Josh's decisions, so DON'T re-litigate these as "wrong", only whether the CODE implements
them correctly): net.post is human-gated by default but a signed net.post.auto:<host> may loosen a
SPECIFIC host to autonomous (motivating case: the operator directing OpenRouter panel calls); the
audit is body-free for autonomous emissions and keeps a bounded preview for human-gated ones.

OUT OF SCOPE (assume correct): the core capability gate / signed-caps internals, ADR 0001/0002, the
inward staging subsystem, Tier-1 GET (already paneled). Same-channel egress is LOGGING not
verification, and exfil of the operator's own secret THROUGH a consented host / offense through a
sanctioned channel are STATED ADR non-goals — not findings unless the CODE contradicts a claim.
Attack the NEW emission code.
"""

SYSTEM = """You are a senior application-security engineer doing an adversarial CODE review of a
shipped OUTBOUND-EMISSION module and its authority gate. Your job is to BREAK it — emit to a host
the operator did not allowlist, exfiltrate the body or a credential, re-send a payload/credential to
a redirect, make a net.get grant authorize a net.post, make an emission auto-fire without a human
hand or a signed auto grant, let the model self-originate an autonomous emission, smuggle a
credential via model args, or mutate what-was-approved vs what-is-sent — not to praise it. Assume the
author is wrong until proven otherwise.

Attack hardest, name file:line, give a CONCRETE input/bypass, and state whether another check
independently catches it (a blocked attack is a valuable NON-finding):
1. egress.post — REDIRECT: on a 3xx is the body/credential ever re-sent, or a second connection to
   the Location ever opened? CREDENTIAL: can the model set Authorization through args/content_type/
   body? Does the credential ever land in the EgressRecord, a returned string, or a log? Can a CRLF/
   control char in content_type or auth split the request? Is auth sent only to the pinned canonical
   host over validated TLS (not to a rebind IP)? CANONICAL==CONNECT for POST (userinfo/case/IDN/
   trailing-dot/port). BODY CAP: can a bytes/huge/streaming/non-str body evade MAX_POST_BODY or the
   hash/len accounting? PREVIEW: can it exceed _BODY_PREVIEW_BYTES, leak when keep_preview is False,
   or break on a multi-byte boundary? Does any input make post() RAISE instead of returning a
   refused record?
2. governance — NAMESPACE: can a net.get:<host> grant authorize a net.post to the same host (or
   vice-versa)? Is the derived cap key the SAME canonical host the client connects to? EMISSION
   FLOOR: can any leash config / importance / risk / intent field make a net_post to a NON-auto host
   run act_then_report (or NOTIFIED-that-still-emits) WITHOUT a signed net.post.auto:<host>? AUTO-
   LIFT: can a proposer-originated (source=="proposed") emission reach act_then_report? can an auto
   grant for host A lift an emission to host B (prefix/substring/case/IDN)? can the model forge
   `source`? CREDENTIAL LOOKUP: is auth ever injected for a host that did NOT pass the net.post gate,
   or looked up by a host string that differs from the connect host? TOCTOU: does approve() re-derive
   net.post:<host> from the FROZEN args and re-check CURRENT caps; can host/body/url be mutated
   between hold and approve (human approved X, Y is sent)? LEASH THREADING: does execute_and_verify's
   new leash param create any path with the wrong leash, or change an existing tool's behavior?
3. tools — does _exec_net_post read auth from args anywhere? is net_post's mutating + egress_log the
   right verify path (no artifact/write-set false-fail)? execute_tool routing.

Calibration: concrete, code-grounded, honest. Distinguish a real guarantee-breaking bug from a
belt-and-suspenders nit. If the code is sound or a concern is a stated ADR non-goal, say so plainly
— do not invent severity."""

USER_TMPL = """Review the SHIPPED net.post (Tier-2 emission) build below.

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
                 "HTTP-Referer": "https://salient-os.local/redteam",
                 "X-Title": "SalienceOS ADR 0003 net.post Tier-2 Code Panel"},
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
    raw = HERE / "raw_net_post"
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
                    f"# net.post Tier-2 code panel: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — ADR 0003 net.post Tier-2 CODE panel =======")
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
    print("==============================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
