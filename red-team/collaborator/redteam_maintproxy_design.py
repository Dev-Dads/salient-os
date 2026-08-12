"""External 5-vendor DESIGN panel on the PROPOSED maintenance egress proxy (ADR 0006) BEFORE any code.

ADR 0005 rejected the dest-only "observed route" (5/5 SERIOUS_FLAWS) and named a content-mediating proxy
at egress.py's destination fidelity as the successor. The operator settled three forks: destination-
allowlist (NO MITM), explicit CONNECT forward proxy, signed `net.maint:<host>` caps. ADR 0006 is that
design. This is a DESIGN review, not code — no proxy/helper exists yet.

Reports per-model + total API cost (standing request).

Usage:  python red-team/collaborator/redteam_maintproxy_design.py [openai/gpt-5.1 ...]
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
    ("THE DESIGN UNDER REVIEW — docs/adr/0006-maintenance-egress-proxy.md",
     "docs/adr/0006-maintenance-egress-proxy.md"),
    ("THE REUSED BRAIN (canonical_host / is_safe_public_ip / resolve-once-pin) — collaborator/egress.py",
     "collaborator/egress.py"),
    ("THE SANDBOX IT MODIFIES (bwrap + SALIENT_MAINT_VERIFIED proof token) — collaborator/maintain.py",
     "collaborator/maintain.py"),
    ("THE ROUTELESS FALLBACK FLOOR — collaborator/netns.py", "collaborator/netns.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

DESIGN review of a PROPOSED architecture (ADR 0006), NOT a code review — no proxy/helper code exists yet.
SalienceOS is a salience-based control plane; the "Collaborator" is a governed agent. Its human/opted-in
`run_command` runs in a bubblewrap MAINTENANCE SANDBOX (maintain.py): full read-write host view, egress-
deputy sockets (docker.sock, ...) masked, cap-drop, and a ROUTELESS netns (--unshare-net). Routeless =>
egress.py (the mediated HTTP client) is the SOLE IP path off the machine, AND the maintenance shell has
NO network (git push / apt / ssh / driver-fetch all fail).

THE PROPOSAL (ADR 0006): give the maintenance shell CONTROLLED network via an explicit CONNECT forward
proxy at egress.py's DESTINATION fidelity. NOT transparent, NOT TLS-terminating (no MITM). Three parts:
(1) an UNPRIVILEGED proxy (maintproxy.py) bound to a veth host IP: per client `CONNECT host:443` it runs
egress.py's brain — canonical_host -> allowlist(net.maint:<host>) -> getaddrinfo -> is_safe_public_ip ->
connect to the pinned IP -> splice RAW BYTES (client does its own end-to-end TLS; proxy never sees
plaintext, cannot MITM). (2) a PRIVILEGED root helper (sudo -n, fixed-arg, model-input-free) that builds a
per-run netns + veth, installs nft DEFAULT-DENY-except-to-the-proxy IN THE NETNS, disables v6 in the netns,
setns+drops privilege+execs the bwrap sandbox WITHOUT --unshare-net (inherits the routed netns), tears down
its own table on exit (never flush ruleset). (3) wiring: host-config opt-in human_shell_network in
{routeless(default), proxied}; allowlist DERIVED from verified signed net.maint:<host> caps (model never
chooses); monotonic fail-closed ladder PROXIED_ROUTE -> ROUTELESS -> UNISOLATED; an in-sandbox POSITIVE
PROOF TOKEN (SALIENT_PROXY_VERIFIED) that proves "in the prepared netns + HTTPS_PROXY set + a direct
non-proxy connect FAILS" before exec, else the command does not run.

Because the proxy TERMINATES the client TCP and RE-ORIGINATES, there is NO ip_forward, NO masquerade, NO
host FORWARD hook, NO DNS interception, NO SNI-sniffing (the client hands the proxy the hostname via
CONNECT). This is the pitch for why it dodges the observed-route's rejection reasons.

DELIBERATE TRADES it wants attacked: (1) destination-allowlist means "where, not what" — an allowlisted
host is an un-inspected bidirectional pipe; (2) a NEW privileged helper on the boundary; (3) proxy-env
reliant (a tool ignoring HTTPS_PROXY fails CLOSED via netns default-deny, but breaks); (4) full-/ write =>
async laundering out of scope for any synchronous chokepoint; (5) v0 is https/443-only.

Your job: break the mechanism AND judge the trade. A design that is technically sound but not worth
building is a valid SERIOUS_FLAWS / "do the alternative" verdict — keeping maintenance network-less (and
vendoring artifacts) is a first-class option.
"""

SYSTEM = """You are a senior Linux-security + systems-design engineer doing an adversarial DESIGN review
of a "maintenance egress proxy" (destination-allowlist CONNECT forward proxy) for a governed agent's
maintenance shell. Assume the author is wrong until proven otherwise. Two jobs: (A) find where the
ARCHITECTURE breaks or is unsafe; (B) judge whether the TRADE is worth it versus the alternatives (keep
maintenance network-less + vendor artifacts; a TLS-terminating proxy; pasta/slirp usermode networking).
Be concrete and precise about Linux userns/netns/cap/nft/privilege-drop/proxy semantics.

Attack these, concretely, and say whether another control catches each:
1. THE NETNS DEFAULT-DENY CRUX. The maintenance shell is bwrap mapped-root and holds CAP_NET_ADMIN INSIDE
   ITS OWN NETNS. The default-deny nft ruleset that is supposed to force all egress through the proxy lives
   IN THAT SAME NETNS. Can the shell simply `nft flush ruleset` / delete the drop rule / add a route / re-
   address the veth / bring up a new interface and reach the internet DIRECTLY, bypassing the proxy and the
   allowlist? If mapped-root in the netns can edit the netns's own nft/routes, the entire mediation is
   theater. Is there any way to make the default-deny UN-EDITABLE by the in-netns shell (rules in a parent/
   host-owned hook the shell can't reach; the veth peer's host end; egress filtering on the HOST side of the
   veth instead of inside the netns; a separate user/netns owner)? THIS IS THE LOAD-BEARING QUESTION.
2. THE PRIVILEGED HELPER TCB. Fixed-arg root setup, then setgroups/setgid/setuid-drop and exec the human
   payload via bwrap. Privilege-drop ordering + verification; residual caps/ambient/no-new-privs; leaked
   fds/env/cwd; a race or symlink/rename on /run/netns/<name> between create and setns+exec; reserved-name
   collision / concurrent runs; can the payload argv/env influence ANY privileged step before the drop; is
   `sudo -n <helper> <argv>` an injection surface (argv boundary, sudoers scoping)? Is a root helper that
   execs the payload acceptable, or a net loss vs keeping maintenance network-less?
3. THE PROXY DATA PATH. CONNECT parse (request smuggling, oversized headers, CRLF, absolute-form vs
   authority-form, pipelined bytes after CONNECT); the canonical_host reuse (does egress.canonical_host
   actually reject IP-literals/userinfo/ports/non-https as the design assumes for a CONNECT authority?);
   the resolve->pin race (DNS rebind between is_safe_public_ip check and create_connection — is resolve-
   once-pin actually pinned?); is_safe_public_ip completeness for a CONNECT dest (v6, mapped-v6, NAT64,
   CGNAT, metadata); a CONNECT to the proxy's OWN host IP / loopback / the veth subnet (SSRF back into the
   host or to host-local services); connection exhaustion / slowloris / unbounded splice; the proxy binding
   — is it reachable ONLY from the netns, or also from other host processes / the LAN?
4. THE ALLOWLIST + AUTHORITY. Is the derived allowlist truly from VERIFIED signed caps only, with the model
   unable to influence it? net.maint:<host> canonicalization (must match the CONNECT canonical_host exactly
   — confusables/IDNA/trailing-dot/case)? Empty-allowlist => deny-all or fall-back-routeless (must NOT fail
   open)? Does "destination-allowlist, no MITM" quietly demote egress.py's content guarantee for the
   maintenance path (a human-approved shell can now stream arbitrary bytes to/from an allowlisted host with
   no method/body/quota control) — is that coherent with the rest of the system?
5. FAIL-CLOSED LADDER + PROOF TOKEN. Any path to ROUTED-BUT-UNMEDIATED (proxy/default-deny not actually
   live but the sandbox runs routed; the SALIENT_PROXY_VERIFIED canary-direct-connect check is unreliable
   or spoofable; cached positive probe goes stale; teardown leaves a netns/veth/rule for the next run)? Is
   the monotonic ladder honest at each rung? Does the proof token bind to BEHAVIOR (a real blocked connect)
   or merely to a belief/inode?

Calibration: concrete, code/kernel-grounded, honest about what is and isn't sealed. Distinguish a real
architectural break from a documented residual (no-MITM/where-not-what; async laundering; https-only;
proxy-env-reliant — the author concedes these). If the design is sound, say so; if sound but not worth
building, say THAT and name the alternative."""

USER_TMPL = """Review the PROPOSED maintenance-egress-proxy design below.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / WHERE (which design part) / CONCRETE
ATTACK OR FLAW / WHETHER ANOTHER CONTROL CATCHES IT / FIX-OR-REDESIGN. Then answer the META-QUESTION
explicitly: IS THIS WORTH BUILDING vs keeping maintenance network-less? Finish with a STEELMAN (2-3
sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence + the single highest-value
change, or "do the alternative: <which>").

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
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS maintproxy Design Panel"},
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
    raw = HERE / "raw_maintproxy_design"
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
                    f"# maintproxy DESIGN panel: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — maintproxy DESIGN panel =======")
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
    print("==================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
