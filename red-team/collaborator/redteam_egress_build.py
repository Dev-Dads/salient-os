"""Red-team the SHIPPED ADR 0003 Tier-1 egress build (branch feat/egress-subsystem) BEFORE merge.

This is a CODE panel on the actual implementation, not the design (the design was paneled as
ADR 0003). The build adds the Collaborator's first network reach — an authority floor and a new
trust boundary — so it gets both passes as a GATE, per the ADR's Verification section.

Attack surface (hand the reviewers the real modules + the tests, so they hunt for what the tests
DON'T pin):
  1. egress.py — the single mediated client. Break the transport safety contract: authorize-one/
     connect-another (does the capability host == the connected host?), redirect-follow, DNS-
     rebind / SSRF-to-metadata (IP pin + private-range block), GET exfil via query/headers, TLS.
  2. governance.py — the capability-derivation gate (capability = f(intent.args)). Can a crafted
     URL MINT a capability the operator didn't grant, or DODGE the exact-match default-deny? Is
     the derivation the SAME canonicalization the client connects with? Emission TOCTOU across
     reauthorized_or_denied.
  3. research.py — web_research injection: fetched UNTRUSTED content -> proposer originates a
     source="proposed" action (the ADR's own broken keystone). Default-deny holes.
  4. The audit-only offense recognizer: does anything rely on it as a boundary (it must not)?

Reports per-model + total API cost (Josh's standing request).

Usage:  python red-team/collaborator/redteam_egress_build.py [openai/gpt-5.1 ...]
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
    ("THE SPEC — docs/adr/0003-outbound-authority-and-prohibition-floor.md",
     "docs/adr/0003-outbound-authority-and-prohibition-floor.md"),
    ("THE CODE (highest risk) — collaborator/egress.py (the single mediated client)",
     "collaborator/egress.py"),
    ("THE CODE — collaborator/governance.py (capability-derivation gate + egress_log branch + re-gate)",
     "collaborator/governance.py"),
    ("THE CODE — collaborator/tools.py (web_fetch registration + flag_offense_shape)",
     "collaborator/tools.py"),
    ("THE CODE — collaborator/research.py (web_research web_get lift + injection floors)",
     "collaborator/research.py"),
    ("WHAT IS PINNED — tests/test_egress.py", "tests/test_egress.py"),
    ("WHAT IS PINNED — tests/test_collaborator_egress_gate.py", "tests/test_collaborator_egress_gate.py"),
    ("WHAT IS PINNED — tests/test_collaborator_research_web.py", "tests/test_collaborator_research_web.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

This is a CODE review of a SHIPPED diff (the ADR 0003 Tier-1 egress build), not a design review.
SalienceOS is a salience-based control plane. Invariant P-01: salience INFLUENCES, policy
AUTHORIZES. The "Collaborator" is a governed agent: a PROPOSER model suggests actions; nothing
runs until a human approves; every action passes ONE core-enforced capability gate
(directive.grants_capability(cap) — EXACT-STRING membership over a host-SIGNED capability set;
the model cannot forge a capability, salience cannot add one) plus a leash axis and a workspace
fence.

WHAT SHIPPED (attack THIS):
  - egress.py: the ONLY module that touches the network. canonical_host(url) returns the
    punycode/lowercased/port-userinfo-stripped host used BOTH as the capability key
    (net.get:<host>) AND as the connect host; fetch() resolves once, refuses a non-global IP
    (loopback/private/link-local/metadata), PINS the resolved IP, connects to it with TLS
    validated against the canonical name, does NOT follow redirects (3xx -> refused), and bounds
    timeout/response-size/query-length.
  - governance.py: for an egress tool it computes required_cap = "net.get:"+canonical_host(args)
    and gates on THAT (a "capability = f(intent.args)" step the static gate lacked); ineligible
    URL -> DENY. reauthorized_or_denied re-derives + re-checks at approval (emission TOCTOU).
    flag_offense_shape is AUDIT-ONLY (tags a shell command, never denies).
  - tools.py: web_fetch (op net.get, verify_mode egress_log, not mutating).
  - research.py: web_research does an allowlisted (default-deny) read-only GET, tags the bytes
    UNTRUSTED, and is perception (never surfaced, grants no authority).

OUT OF SCOPE (assume correct, do not re-litigate): the core capability gate / signed PolicyCaps
internals, ADR 0001/0002, the inward staging subsystem. The claim that egress is same-channel
LOGGING (not the independent-observer verifier) and that run_command still reaches the network
until a netns lands are STATED LIMITS in the ADR — not findings unless the CODE contradicts them.
Attack the NEW code.
"""

SYSTEM = """You are a senior application-security engineer doing an adversarial CODE review of a
shipped network-egress module and its authority gate. Your job is to BREAK it — reach a host the
operator did not allowlist, exfiltrate data, mint or dodge a capability, defeat the IP pin, follow
a redirect out, or make the proposer autonomously originate a prohibited action — not to praise it.
Assume the author is wrong until proven otherwise.

Attack hardest, name file:line, give a CONCRETE input/bypass, and state whether another check
independently catches it (a blocked attack is a valuable NON-finding):
1. egress.canonical_host / fetch — AUTHORIZE-ONE / CONNECT-ANOTHER: can the host used to build the
   capability key differ from the host the socket actually connects to? Push on urlsplit quirks
   (userinfo, ports, IDN/punycode round-trips, trailing dot, backslashes, whitespace, embedded
   control chars, %-encoding, uppercase, "https://a\\@b", "https://a b.com"), the .encode('idna')
   behavior, and whether the pinned-IP connection + Host header + TLS server_hostname are all the
   SAME canonical host. REDIRECT: is 3xx really never followed (http.client vs urllib)? IP PIN /
   REBIND / SSRF: is is_safe_public_ip complete (IPv4-mapped IPv6 ::ffff:169.254.169.254, NAT64,
   0.0.0.0, decimal/octal IP forms, IPv6 forms)? Is the pinned IP the one connected to? GET EXFIL:
   are query/headers actually bounded; can the model inject headers?
2. governance capability-derivation — can a crafted URL cause required_capability to compute a
   capability the operator granted while fetch connects elsewhere (or vice versa)? Does the gate
   fail OPEN on any canonicalization edge (None handling)? Is the approval re-gate (reauthorized_
   or_denied) actually re-deriving from the same args, and does the egress_log path re-check the
   allowlist at execute time? Any TOCTOU between derive and connect?
3. research web_get — trace fetched UNTRUSTED content into the proposer context: does the default-
   deny capability check use the SAME authority source as the gate (granted_capabilities)? Can
   web_get reach a non-allowlisted host? Does the UNTRUSTED tagging actually bound anything, or is
   it cosmetic? Is there an exfil-via-research-URL path?
4. flag_offense_shape — confirm it is TRULY audit-only (never changes status). Flag any place that
   (wrongly) relies on it as a control, or where its tagging could be user-influenced to hide.

Calibration: concrete, code-grounded, honest. Distinguish a real guarantee-breaking bug from a
belt-and-suspenders nit. If the code is sound or a concern is an explicitly-stated ADR limit, say
so plainly — do not invent severity."""

USER_TMPL = """Review the SHIPPED egress build below.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) /
CONCRETE INPUT OR BYPASS / WHY IT BREAKS A GUARANTEE / WHETHER ANOTHER CHECK CATCHES IT / FIX.
Then STEELMAN (2-3 sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence,
and the single highest-value fix).

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
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS ADR 0003 Egress Build Code Panel"},
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
    raw = HERE / "raw_egress_build"
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
                    f"# Egress build code panel: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — ADR 0003 egress build CODE panel =======")
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
    print("===========================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
