"""External 5-vendor CODE panel on the SHIPPED run_command network-isolation (ADR 0003
revisit #1, branch feat/run-command-netns) BEFORE merge.

An internal adversarial pass already ran (verdict MINOR_ISSUES) and found + fixed one real
bypass: `unshare` invoked by bare name was PATH-shadowable into a silent false-`isolated`.
Per the escalate-on-a-real-finding rule, this external panel reviews the FIXED code — hunt for
RESIDUAL bypasses the internal pass or the fix missed.

Reports per-model + total API cost (Josh's standing request).

Usage:  python red-team/collaborator/redteam_netns.py [openai/gpt-5.1 ...]
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
    ("THE MECHANISM (highest risk) — collaborator/netns.py", "collaborator/netns.py"),
    ("THE WIRING — collaborator/tools.py (_exec_command wraps argv; Execution.network_isolated)",
     "collaborator/tools.py"),
    ("THE SUPERVISOR — salienceos/verifier/observers.py (run_supervised = subprocess.run, no shell)",
     "salienceos/verifier/observers.py"),
    ("THE FLAG PATH — collaborator/governance.py (Decision.network_isolated, exit branch, summary())",
     "collaborator/governance.py"),
    ("WHAT IS PINNED — tests/test_netns.py", "tests/test_netns.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

This is a CODE review of a SHIPPED diff (ADR 0003 revisit #1: run_command network isolation),
not a design review. SalienceOS is a salience-based control plane; the "Collaborator" is a
governed agent whose `run_command` tool was the last UNMEDIATED path off the machine (a shell
can curl / open a raw socket / git push, which the mediated egress client `web_fetch` never
sees). The fix runs run_command inside a fresh, UNPRIVILEGED network namespace with no route
out, so `egress.py` becomes the sole IP-network path off the machine.

MECHANISM: `wrap_no_network(argv)` returns
  [<abs>/unshare, --map-root-user, --net, --, sh, -c, 'ip link set lo up 2>/dev/null; exec "$@"', sh, *argv]
executed by `run_supervised` → `subprocess.run(argv)` with NO shell=True. `--map-root-user`
makes a user namespace (current user → root inside) that grants CAP_NET_ADMIN in the new net
namespace; a fresh net namespace has only loopback and no route → external connects fail closed.
Where netns is unavailable (non-Linux / no unshare / userns disabled), it runs UNISOLATED and
reports network_isolated=False — an honest flag, never a silent claim. The flag flows
Execution → Decision → summary() (🔒 net-isolated / ⚠ net NOT isolated).

ALREADY FOUND + FIXED by an internal pass (look for RESIDUALS, not these):
  - `unshare` is now an ABSOLUTE path resolved at import (was a bare name → PATH-shadowable at
    run time by a planted binary, since the child is userns-root in a SHARED mount namespace).
  - "sole path" narrowed to "sole IP-network path"; pathname-UNIX-socket residual documented
    (a netns does NOT isolate the filesystem, so a network-capable local daemon socket is a
    known out-of-scope residual).

OUT OF SCOPE (documented limits, not findings unless the CODE contradicts them): filesystem/
PID/IPC are not isolated (network-ns only); pathname UNIX sockets remain reachable; non-Linux
hosts run unisolated (flagged). The isolation PROOF test is Linux-gated (runs in ubuntu CI).
Attack the mechanism + the honesty of the flag.
"""

SYSTEM = """You are a senior Linux-security engineer doing an adversarial CODE review of a
run_command network-isolation mechanism (unprivileged user+net namespaces via `unshare`). Your
job is to BREAK it: get an isolated shell command to reach the network, or make the
`network_isolated` flag LIE (report isolated when it isn't, or vice versa). Assume the author is
wrong until proven otherwise. A prior internal pass already fixed a PATH-shadow bypass and an
over-claim — hunt for what it MISSED.

Attack hardest, name file:line, give a CONCRETE bypass, and say whether another control catches
it (a blocked attack is a valuable NON-finding):
1. NETNS/USERNS ESCAPE. From inside the (child userns-root, fresh netns, SHARED mount ns) can a
   command regain host network? setns/nsenter into host netns; open /proc/<pid>/ns/net of a host
   process; join a pre-existing `ip netns`; a second unshare to a routed ns; ptrace/CRIU tricks;
   binding a host-side pathname UNIX socket that proxies to the network (docker.sock, a local
   forward proxy, systemd-resolved, DBus). For each: is it actually reachable given the child has
   caps ONLY over its own descendant userns, and is it IP-egress or just local IPC?
2. THE FLAG LIES. Any path where network_isolated=True but egress actually happened, or
   =False/None when it was isolated? Re-examine the absolute-path unshare fix for residual gaps
   (symlink/hardlink at the resolved path? a writable /usr/bin on some hosts? TOCTOU between the
   import-time resolve and run time?). Does the cached probe (netns_available) ever disagree with
   the actual wrap at run time (probe passes, real run doesn't isolate, flag says True)?
3. ARGV / SHELL HANDLING. Can a crafted `command` arg (string → shlex.split, or a list) escape
   `exec "$@"` and get a shell to interpret metacharacters, or change what binary runs as
   argv[0]/unshare? Any divergence from the pre-existing no-shell path that NEWLY enables
   injection? Env-based tricks (IFS, BASH_ENV, ENV, LD_PRELOAD/LD_LIBRARY_PATH affecting unshare
   or the inner command)?
4. FALL-OPEN / PROBE. Can a model/attacker force netns_available()=False to disable isolation
   (any model-controlled input to the probe)? Probe hang/timeout, cache staleness, missing
   sh/ip. Is unisolated-with-honest-flag an acceptable fallback, or does something claim more?
5. FLAG PROPAGATION. Trace network_isolated from wrap_no_network → Execution → Decision →
   summary(). Any drop or misreport, esp. on the approve()/re-gate path or a failed command?

Calibration: concrete, code-grounded, honest, precise about Linux namespace/cap semantics. You
cannot run netns (assume Linux). Distinguish a real isolation/honesty break from a documented
scope limit (network-ns ≠ filesystem/IPC isolation — not a finding). If sound, say so plainly."""

USER_TMPL = """Review the SHIPPED run_command network-isolation diff below.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) /
CONCRETE BYPASS / WHY IT BREAKS ISOLATION OR THE FLAG'S HONESTY / WHETHER ANOTHER CONTROL CATCHES
IT / FIX. Then STEELMAN (2-3 sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one
sentence, and the single highest-value fix).

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
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS netns Code Panel"},
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
    raw = HERE / "raw_netns"
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
                    f"# netns code panel: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — netns isolation CODE panel =======")
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
    print("=====================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
