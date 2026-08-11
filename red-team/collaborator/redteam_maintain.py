"""External 5-vendor CODE panel on the SHIPPED maintenance-sandbox / deputy-seal diff (ADR 0003
revisit #1b, branch feat/deputy-socket-mask) BEFORE merge.

An internal adversarial pass + a LIVE Sparky proof already ran and drove the design: the cheap
`unshare --mount` + `/dev/null`-bind mask is DEFEATABLE in one `umount` (the netns runs as
mapped-root, so the shell keeps CAP_SYS_ADMIN over its own mount ns — proven live: `after-umount:
socket`). The shipped fix runs the human/opted-in run_command in a bubblewrap MAINTENANCE SANDBOX
with a FULL read-write host view where the deputy sockets are `/dev/null`-bound AND all caps are
dropped (`--cap-drop ALL` + `--disable-userns`), so the payload has no cap to `umount` the mask
(proven live: `umount: blocked`). The in-child guard verifies per run that the netns is fresh, both
`CapEff` and `CapPrm` are all-zero, and each deputy is masked, then emits a POSITIVE token
(verification whitelists on it, fail closed). Hunt for RESIDUAL bypasses this missed.

Reports per-model + total API cost (standing request).

Usage:  python red-team/collaborator/redteam_maintain.py [openai/gpt-5.1 ...]
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
    ("THE MECHANISM (highest risk) — collaborator/maintain.py", "collaborator/maintain.py"),
    ("THE WIRING — collaborator/tools.py (_exec_command: human path prefers the sandbox, whitelists on "
     "the token, falls back to netns)", "collaborator/tools.py"),
    ("THE FALLBACK + the umount context — collaborator/netns.py", "collaborator/netns.py"),
    ("THE REUSED BWRAP MACHINERY (for pattern context) — collaborator/contained.py", "collaborator/contained.py"),
    ("WHAT IS PINNED — tests/test_collaborator_maintain.py", "tests/test_collaborator_maintain.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

This is a CODE review of a SHIPPED diff (ADR 0003 revisit #1b: the run_command "deputy seal"),
not a design review. SalienceOS is a salience-based control plane; the "Collaborator" is a governed
agent whose `run_command` runs inside a routeless network namespace so `egress.py` is the SOLE
IP-network path off the machine. That netns has a confused-deputy HOLE: it runs as MAPPED-ROOT (to
get CAP_NET_ADMIN to create the namespace), so the shell holds CAP_SYS_ADMIN over its OWN mount ns
and can reach a host daemon's *pathname* UNIX socket (/var/run/docker.sock, containerd, podman, the
DBus system bus, systemd-resolved) and make THAT daemon egress on its behalf — AROUND the netns. A
naive `/dev/null` bind-mask does not help: a mapped-root child just `umount`s it (PROVEN on a live
host: pre=socket, after-mask=char-device, umount SUCCEEDED, after-umount=socket).

THE FIX (maintain.py): a human/opted-in run_command runs in a bubblewrap MAINTENANCE SANDBOX:
  bwrap --uid 0 --gid 0 --unshare-user --unshare-net --disable-userns --assert-userns-disabled
        --cap-drop ALL --die-with-parent --new-session
        --bind / / --dev /dev --proc /proc
        --ro-bind /dev/null <each PRESENT deputy socket>
        --chdir <workspace> -- sh -c '<GUARD>' sh <original argv...>
`--bind / /` keeps FULL read-write host reach (maintenance must not break); `--uid 0` matches the
current mapped-root "appears as root" view (files map to the collaborator's REAL uid); `--cap-drop
ALL` + `--disable-userns` means the payload has NO CAP_SYS_ADMIN and cannot create a userns to regain
it, so it cannot `umount` the mask (PROVEN live: umount blocked, nested-userns umount blocked). The
GUARD (runs as the sandbox process, BEFORE exec of the payload) verifies: (1) the netns is fresh
(inode != parent, else exit 44); (2) CapEff AND CapPrm are both all-zero (else exit 47) — the
load-bearing umount-defense precondition; (3) each present deputy path is now a character device
i.e. masked (else exit 46); then emits the POSITIVE token SALIENT_MAINT_VERIFIED and `exec "$@"`.
The caller (tools.py) WHITELISTS on that token: network_isolated stays True ONLY if present, so any
setup/guard failure fails CLOSED. Where the sandbox is unavailable (non-Linux / no bwrap / userns
blocked / a REAL-uid-0 collaborator where bwrap's cap-drop early-returns and the CapEff/CapPrm check
trips) it FALLS BACK to the certified routeless netns wrap (netns.wrap_no_network) — today's
behaviour + the documented docker.sock residual, honestly flagged, never a fake seal.

DELIBERATE, DOCUMENTED RESIDUALS (not findings unless the CODE contradicts them):
  - BLOCKLIST: a deputy socket not in _DEPUTY_SOCKETS (rootless docker at /run/user/<uid>/docker.sock,
    $SSH_AUTH_SOCK, a DB socket that can COPY..PROGRAM, an arbitrary local proxy) still slips. Known.
  - The full-FS WRITE view means an approved-but-adversarial command can LAUNDER egress
    asynchronously (plant a cron/systemd-unit/authorized_keys/script a later un-isolated process
    runs). The seal only raises the SYNCHRONOUS-egress bar; the trusted maintenance path is not
    leak-proof by design (the operator requires full-FS reach for maintenance). Known/accepted.
  - This path is HUMAN-approved; the deceived-human is a separate accepted axis.

Attack the MECHANISM (can a payload still reach a deputy / defeat the mask despite cap-drop?) and the
HONESTY of the network_isolated flag (True while egress happened, or the sandbox silently didn't run).
"""

SYSTEM = """You are a senior Linux-security engineer doing an adversarial CODE review of a
bubblewrap "maintenance sandbox" whose job is to (a) keep FULL filesystem reach for human
maintenance while (b) making a set of host daemon UNIX sockets UN-REMOVABLY masked and (c) keeping
the network a routeless namespace. Your job is to BREAK it: get a sandboxed command to reach a
masked deputy socket (or the network), or make the `network_isolated` flag LIE. Assume the author is
wrong until proven otherwise. The naive unprivileged mask was already found umountable and replaced
by this cap-dropped bwrap version — hunt for what THIS still misses.

Attack hardest, name file:line, give a CONCRETE bypass, and say whether another control catches it
(a blocked attack is a valuable NON-finding):
1. DEFEAT THE MASK despite cap-drop. With CapEff=CapPrm=0 and --disable-userns, can the payload still
   reveal a masked socket or reach the daemon another way? umount/umount2 MNT_DETACH; mount --move /
   open_tree/move_mount; pivot_root; a second bwrap/unshare; fusermount; ptrace of a sibling; writing
   /proc/sys or a cgroup; asking a DIFFERENT deputy that proxies (a non-masked socket); a symlink or
   bind-race so realpath masks the WRONG path and the real socket is reachable at another name; the
   deputy reachable over ABSTRACT-namespace socket (is that cut by --unshare-net?) or TCP on loopback
   (127.0.0.1:2375 dockerd, 127.0.0.53 resolved) rather than the pathname socket.
2. THE CAP CHECK. Is verifying CapEff+CapPrm==0 sufficient to guarantee no `umount`? Consider
   ambient/inheritable/bounding caps, file capabilities on an suid/fcap binary reachable in the
   full-/ view (can the payload exec a setuid-root or fcap binary — `mount`, `fusermount`, `su`,
   `newuidmap` — to regain privilege, since / is bound rw and NOT nosuid?). Does --cap-drop/userns
   actually neutralise a host setuid-root binary in the sandbox? THIS IS THE MOST LIKELY REAL HOLE —
   reason precisely about no-new-privs / setuid in a bwrap userns.
3. THE FLAG LIES. Any path where network_isolated=True but egress happened, or the command ran
   OUTSIDE the sandbox while reported isolated? The token is emitted by the GUARD before exec — can a
   payload emit SALIENT_MAINT_VERIFIED itself to forge PRESENCE (does that matter, since the guard
   already ran)? Can it SUPPRESS the token yet still run? Trace network_isolated from wrap_maintenance
   -> Execution -> the whitelist in tools.py. The probe (maintenance_available) runs a DIFFERENT
   command than the real run — can the probe pass while a real run is not actually sandboxed (TOCTOU
   on bwrap path, PATH, the deputy set changing between probe and run)?
4. FALL-OPEN / FALLBACK. Can a model/attacker force maintenance_available()=False to drop to the
   weaker netns wrap (which is umountable)? Is the fallback strictly no-worse-than-today, or does
   something now claim more? A real-uid-0 collaborator: is the CapEff/CapPrm trip the ONLY thing
   standing between "root collaborator" and an umountable mask — and is that trip actually reached
   (bwrap runs, guard runs) or could bwrap-as-root do something worse first?
5. ARGV / SHELL / ORDER. bwrap applies ops left-to-right: is `--ro-bind /dev/null <sock>` guaranteed
   AFTER `--bind / /` for every socket? Can a crafted `command` arg escape `exec "$@"`? Does
   _present_deputy_sockets' realpath+metachar filter actually prevent guard-string corruption? Env
   (IFS/BASH_ENV/ENV/LD_*) affecting the guard `sh` or the masked-ness?

Calibration: concrete, code-grounded, precise about Linux userns/cap/mount/setuid semantics. You
cannot run bwrap (assume Linux, bwrap 0.9, non-root collaborator uid 1000; live proof confirms
umount is blocked and CapEff/CapPrm are zero — so a generic "umount works" claim is already refuted;
find something subtler, e.g. a setuid path or a non-pathname reach). Distinguish a real break from a
documented residual (blocklist; async laundering; deceived-human — NOT findings). If sound, say so."""

USER_TMPL = """Review the SHIPPED maintenance-sandbox / deputy-seal diff below.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) /
CONCRETE BYPASS / WHY IT BREAKS THE SEAL OR THE FLAG'S HONESTY / WHETHER ANOTHER CONTROL CATCHES IT /
FIX. Then STEELMAN (2-3 sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence,
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
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS deputy-seal Code Panel"},
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
    raw = HERE / "raw_maintain"
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
                    f"# deputy-seal code panel: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — deputy-seal CODE panel =======")
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
    print("=================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
