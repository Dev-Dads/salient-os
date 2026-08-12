"""External 5-vendor DESIGN panel on the PROPOSED forced-observed-egress-route (ADR 0005, ADR 0003
revisit #1c) BEFORE any code is written.

This is a DESIGN review, not code. The mechanism introduces a NEW privileged trust surface (a root
helper that ultimately execs the human payload) and RE-OPENS an IP egress channel that the routeless
maintenance sandbox had closed. Pressure-test the architecture — and answer the meta-question: is the
trade (observed maintenance network) WORTH the privileged helper + a second, dest-only-observed egress
path, or is the honest default to keep maintenance network-less?

Reports per-model + total API cost (standing request).

Usage:  python red-team/collaborator/redteam_observedroute_design.py [openai/gpt-5.1 ...]
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
    ("THE DESIGN UNDER REVIEW — docs/adr/0005-observed-egress-route.md", "docs/adr/0005-observed-egress-route.md"),
    ("WHAT IT BUILDS ON (the maintenance sandbox, PR #42) — collaborator/maintain.py", "collaborator/maintain.py"),
    ("THE OBSERVER TO REBUILD (currently OUTPUT/skuid) — collaborator/egressobserver.py",
     "collaborator/egressobserver.py"),
    ("THE FALLBACK (routeless netns) — collaborator/netns.py", "collaborator/netns.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

This is a DESIGN review of a PROPOSED architecture (ADR 0005), NOT a code review — no code exists yet.
SalienceOS is a salience-based control plane; the "Collaborator" is a governed agent. Its human/opted-in
`run_command` currently runs in a bubblewrap MAINTENANCE SANDBOX (ADR 0003 revisit #1b, maintain.py):
full read-write host view, egress-deputy sockets (docker.sock, ...) un-removably masked via cap-drop, and
a ROUTELESS netns (--unshare-net). Routeless => egress.py (the mediated HTTP client) is the SOLE IP path
off the machine (what makes the same-channel egress log sound), AND the maintenance shell has NO network
(git push / apt / ssh fail).

THE PROPOSAL (ADR 0005): return maintenance network through a SINGLE OBSERVED ROUTE. A privileged root
helper (invoked via `sudo -n`, the same posture the egress observer already uses) does all root-only
setup with FIXED args — create a per-run netns, a veth pair, addressing, ip_forward, an nft masquerade,
a host FORWARD/iifname observer hook (records every dest ip:port off the one veth), FORWARD policy-drop
with accept only for observed protocols, host INPUT drop from the veth, IPv6 disabled in the netns — then
DROPS PRIVILEGES (setgroups/setgid/setuid to the collaborator uid) and EXECS the PR-#42 bwrap sandbox
WITHOUT --unshare-net, so bwrap inherits the prepared routed netns (still cap-dropped, deputy-masked,
full-/). Fallback ladder (monotonic, fail-closed, never routed-but-unobserved): OBSERVED_ROUTE ->
SANDBOX_ROUTELESS (today) -> NETNS_ROUTELESS -> UNISOLATED (honest flag).

The design deliberately makes a TRADE it wants attacked: (1) it RE-OPENS an IP egress channel — routeless
made egress.py the sole IP path; the observed route is a SECOND IP path, OBSERVED (dest ip:port, off one
veth, unbypassable) but NOT content-mediated (no URL/method/body, no per-host net.get/net.post caps like
egress.py applies); (2) it adds a root helper that ultimately EXECS the human payload; (3) it observes
WHERE not WHAT (DNS names + UDP/QUIC payload not captured; deputy mask is a blocklist; full-/ write still
permits async laundering).

Your job: break the mechanism AND judge the trade. A design that is technically sound but not worth
building is a valid SERIOUS_FLAWS / "do the alternative" verdict — the honest default (keep maintenance
network-less) is a first-class option.
"""

SYSTEM = """You are a senior Linux-security + systems-design engineer doing an adversarial DESIGN review
of a "forced observed egress route" for a governed agent's maintenance shell. Assume the author is wrong
until proven otherwise. Two jobs: (A) find where the ARCHITECTURE breaks or is unsafe; (B) judge whether
the TRADE is worth it versus the stated alternatives (keep maintenance network-less; pasta; a content-
mediating proxy). Be concrete and precise about Linux userns/netns/cap/nft/privilege-drop semantics.

Attack these, concretely, and say whether another control catches each:
1. THE PRIVILEGED HELPER. It does fixed-arg root setup, then setgroups/setgid/setuid-drops and execs the
   human payload via bwrap. Is that boundary sound? Privilege-drop ordering + verification (setgroups
   before setgid/setuid; checking the drop; residual caps/ambient; no-new-privs; leaked fds/env/cwd into
   the payload); a race or symlink/rename attack on /run/netns/<name> between create and setns+exec;
   reserved-name collision or a second concurrent run; can the payload argv or environment influence ANY
   privileged step before the drop; is `sudo -n <helper> <argv>` itself an injection surface (argv
   boundary, sudoers scoping)? Is a root helper that execs the payload acceptable at all, or a net loss?
2. THE RE-OPENED CHANNEL. Routeless made egress.py the SOLE IP path. Now there are two. The observed route
   logs dest ip:port but does NOT see content or apply net.get/net.post caps. Does this undermine the
   egress-governance model (a human-approved shell can now POST to a non-allowlisted host, observed but
   not blocked)? Is "observed, not mediated" coherent with the rest of the system, or does it quietly
   demote egress.py's guarantee? Would a reviewer trust the audit trail when it's dest-set-only?
3. OBSERVER COMPLETENESS / FALSE-OBSERVED. Does a host FORWARD/iifname nft hook truly capture EVERY egress
   off the veth — all l4proto, fragments, IPv6 if not fully disabled, first-packet vs established? Any
   packet that leaves unrecorded while the guard still emits the positive "observed" token is a false
   claim. Is FORWARD policy-drop + explicit-accept the right belt, and is the accept set complete for real
   maintenance without leaving an unobserved hole?
4. THE JOIN. Unprivileged processes cannot setns into a root-owned netns; the design has the ROOT helper
   setns then drop+exec bwrap without --unshare-net. Is that correct? Does bwrap INHERITING a netns (vs
   creating one) interact badly with --unshare-user/--cap-drop/--disable-userns or the deputy masks? Any
   simpler/safer join?
5. FAIL-CLOSED + LADDER. Any path to routed-but-UNOBSERVED (observer install fails but route is up; probe
   passes but a live run's observer isn't; teardown leaves a route/veth/nft rule for the next run; the
   cached positive probe goes stale)? Is the monotonic ladder honest at each rung?

Calibration: concrete, code/kernel-grounded, honest about what is and isn't sealed. Distinguish a real
architectural break from a documented residual (DNS/UDP-payload not captured; blocklist deputy; async
laundering — the author already concedes these). If the design is sound, say so; if it's sound but not
worth building, say THAT and name the alternative."""

USER_TMPL = """Review the PROPOSED observed-egress-route design below.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / WHERE (which design step) /
CONCRETE ATTACK OR FLAW / WHETHER ANOTHER CONTROL CATCHES IT / FIX-OR-REDESIGN. Then answer the
META-QUESTION explicitly: IS THIS WORTH BUILDING vs keeping maintenance network-less? Finish with a
STEELMAN (2-3 sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence + the single
highest-value change, or "do the alternative: <which>").

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
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS observed-route Design Panel"},
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
    raw = HERE / "raw_observedroute_design"
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
                    f"# observed-route DESIGN panel: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — observed-route DESIGN panel =======")
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
    print("======================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
