"""Red-team + CERTIFY the SHIPPED PR 1b delta (salient-os feat/contained-earns-autonomy, PR #39).

An external 5-vendor CODE panel on the change that makes `code_protection_available()` a REAL,
verified host probe backed by bubblewrap containment, so an AUTONOMOUS run_command can EARN autonomy
by running contained (code roots read-only, no $HOME/secrets, cleared env, fresh routeless netns).
Certification is as important as bug-finding: a claim we CANNOT break is a valuable NON-finding,
recorded as such. The internal adversarial pass is run separately; this panel is the independent
certification.

Reports per-model + total API cost (standing request).

Usage:  python red-team/collaborator/redteam_contained.py [openai/gpt-5.1 ...]
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
    ("THE DELTA — the exact diff PR #39 ships (read this first)",
     "red-team/collaborator/raw_contained/contained.diff"),
    ("THE NEW CODE (highest risk) — collaborator/contained.py (the whole autonomy-earning wrapper: "
     "_resolve absolute-binary; _RO_SYSTEM/_MASK computed at import from host dirs; _roots_with_witness; "
     "_guarded_script — the in-child VERIFY-then-exec guard; _bwrap_argv — the order-sensitive argv; "
     "wrap_contained; protection_unverified / setup_failed; containment_available — the cached VERIFIED probe)",
     "collaborator/contained.py"),
    ("THE CODE (highest risk) — collaborator/governance.py (the govern-time AUTONOMY-WITHHELD floor: "
     "requires code_protection_available() AND enforced(session) AND grants(shell.contained_autonomy); the "
     "execute-time floor re-assert + require_code_protection threading into the executor; the preview "
     "two-lie fix — code_protected=False + reach on the human path)",
     "collaborator/governance.py"),
    ("THE CODE — collaborator/tools.py (_exec_command require_code_protection path: mkdir .sandbox-home, "
     "wrap_contained, REFUSE if not protected; the code_protected=protected binding — NOT a govern belief; "
     "the three-way downgrade protection_unverified/isolation_unverified/setup_failed; execute_tool threading)",
     "collaborator/tools.py"),
    ("THE CODE — collaborator/codefence.py (code_protection_available() now DELEGATES to "
     "contained.containment_available(protected_roots_with_witness()); the new protected_roots_with_witness(); "
     "the single-source-of-truth _code_slots the witness pairs derive from)",
     "collaborator/codefence.py"),
    ("THE PRECEDENT + the path it SPLITS AWAY FROM — collaborator/netns.py (the certified human/opted-in "
     "wrapper wrap_no_network, UNTOUCHED; the --map-root-user nesting trap this must NEVER layer under; the "
     "_UNVERIFIED_SENTINEL/exit-44 + isolation_unverified conventions contained.py reuses)",
     "collaborator/netns.py"),
    ("THE CODE — collaborator/loop.py (approve(): the moment-of-use re-denies + the MINOR-B seal "
     "freeze_args(dict(decision.args)) — approved==executed; does the contained path interact with it?)",
     "collaborator/loop.py"),
    ("THE CODE — collaborator/session.py (enforced(session): what makes a session 'enforced'; can a model "
     "reach an enforced session with a forged shell.contained_autonomy in mutable session.capabilities, or is "
     "the cap only honoured from a host-SIGNED directive via directive.grants_capability?)",
     "collaborator/session.py"),
    ("WHAT IS PINNED — tests/test_collaborator_contained.py (the guarantees encoded as tests, incl. the "
     "off-Linux honest-fallback + the argv/guard construction + the Linux @skipUnless LIVE containment proof)",
     "tests/test_collaborator_contained.py"),
    ("WHAT IS PINNED — tests/test_collaborator_run_command_parity.py (the signed-cap earns-autonomy gate + "
     "the refuse-when-host-cannot-contain + the belt-not-keyed-on-leash-string cases)",
     "tests/test_collaborator_run_command_parity.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

CODE review + CERTIFICATION of a SHIPPED diff (salient-os PR #39). SalienceOS is a salience-based
control plane; invariant P-01: salience INFLUENCES, policy AUTHORIZES. The "Collaborator" is a
governed agent: a PROPOSER model suggests actions; every action passes ONE core-enforced capability
gate (directive.grants_capability — EXACT-STRING membership over a host-SIGNED capability set; the
model cannot forge a capability, salience cannot add one), plus a LEASH axis (act_then_report /
propose_first / notify_only, applied via apply_cap which only ever TIGHTENS — stricter wins) and a
workspace fence. A capability is honoured ONLY when it rides a host-SIGNED directive AND the session is
'enforced' — never from mutable session.capabilities (that is the F5 signed-caps rule the raw_network /
net.post.auto opt-ins already follow, and shell.contained_autonomy now follows).

THE AXIS (F-6 / ADR 0003 revisit #1 — "protection earns autonomy" on the CODE axis): run_command has
NO filesystem fence (write_file/read_file ARE workspace-fenced via resolve_in_workspace; _exec_command
runs arbitrary argv with no containment). Harm A (a shell rewriting the Collaborator's OWN code —
collaborator/ + salienceos/ = codefence.PROTECTED_ROOTS — and neutering F1 "the model can never
self-originate an autonomous emission") was PREVIOUSLY held ONLY by WITHHOLDING autonomy:
code_protection_available() was hardcoded False, so EVERY run_command floored to propose_first (a human
hand) and none could auto-run. This PR makes that predicate REAL.

WHAT SHIPPED IN THIS PR (attack + CERTIFY this):
  - collaborator/contained.py (NEW): wrap_contained() runs an AUTONOMOUS run_command inside a bubblewrap
    sandbox where PROTECTED_ROOTS are --ro-bind READ-ONLY, $HOME/secrets are absent (deny-by-default
    view: only _RO_SYSTEM ro + the code roots ro + the workspace rw; /dev/null masks over
    /etc/shadow|gshadow|sudoers|ssh), env is --clearenv, and (unless a signed raw-network opt-in) the
    network is a fresh routeless netns via bwrap --unshare-net. VERIFIED-not-trusted: an in-child sh
    guard PROVES per run, for each root R with witness F, that F is present, opening F for append FAILS,
    and mountinfo shows R mounted ro; any miss => sentinel SALIENT_CODEFENCE_UNVERIFIED + exit 45. The
    net half reuses netns's inode check + exit 44. containment_available() (cached) runs the SAME argv
    template against a throwaway workspace and trusts rc==0 — not "bwrap exists".
  - THE DECISIVE CONSTRAINT: bwrap is NEVER nested under netns's --map-root-user. That child is uid 0,
    so bwrap's drop_all_caps() early-returns and the sandbox would INHERIT CAP_SYS_ADMIN and remount the
    ro-bind rw — a FALSE protection claim (bubblewrap#287 / CVE-2014-5206). So the two containments are
    SPLIT BY PATH: the human/opted-in path uses netns.wrap_no_network (untouched); the autonomous path
    uses contained.wrap_contained (bwrap does BOTH mount + net), running as the real unprivileged uid so
    --cap-drop ALL + --disable-userns make the trap structurally unreachable.
  - governance.py: an act_then_report run_command AUTO-runs ONLY when BOTH code_protection_available()
    (verified containment on THIS host) AND enforced(session) AND grants(shell.contained_autonomy) (a
    SIGNED opt-in) hold — else floored to propose_first (a human hand). Enforced at BOTH govern-time and
    moment-of-use (defence in depth). require_code_protection is threaded into the executor (ADR
    0003:336-338) so code_protected is bound to the executor's REAL per-run result, NOT re-read
    govern-side (the belief-vs-behaviour split must not reopen on the code axis).
  - THE TWO-LIE FIX: tools.py code_protected and governance.py preview['code_protected'] now bind to the
    wrapper's real result / False on the uncontained human path — never print "protected" over a
    full-reach run.

THE CERTIFICATION CLAIM (attack it HARDEST): "No FALSE code_protected — a human-approved (uncontained)
run is NEVER reported or previewed as contained. An autonomous run_command auto-runs ONLY with BOTH
verified containment AND a signed shell.contained_autonomy grant; absent either it floors to a human
hand and cannot auto-run. Containment is VERIFIED per-run (guard proves ro), not trusted; a setup
failure or a tripped guard downgrades protected->False (fail-safe) and the command did not run. bwrap is
never nested under netns, so the cap-inheritance ro-bypass is unreachable. No existing guarantee (netns
isolation, the emission floor, MINOR-B approved==executed) regressed."

DESIGN CONSTRAINT (owner's steer — do NOT re-litigate as 'wrong', only whether the CODE implements it):
a HUMAN-approved run_command KEEPS full filesystem reach (uncontained by design) — a hard fence would
kill the system maintenance the Collaborator is FOR. Containment gates ONLY the autonomy-earning path.

OUT OF SCOPE — DEFERRED / BY DESIGN (do NOT report as findings; acknowledged):
  - A DECEIVED HUMAN who APPROVES an uncontained maintenance run_command — the acknowledged residual;
    the human path is deliberately unfenced. NOT a finding.
  - The FUNCTIONALITY CEILING: earned autonomy is a hermetic inner loop (no network fetch/install —
    --unshare-net); "it can't apt-get" is BY DESIGN, not a finding.
  - names_code_root / names_sensitive_path porousness (already-shipped POROUS DiD recognizers; not the
    boundary here — the bwrap ro-bind is). "The recognizer can be bypassed" is a NON-finding unless a
    control path RELIES on it as the SOLE boundary.
  - CVE-2014-5206 on a SINGLE-LEVEL hand-rolled bind is exactly WHY bwrap is used; "a single ro-bind is
    defeatable" is the acknowledged reason for the design, not a finding against it.
  - Kernel/bwrap 0-days, a root-compromised host, ADR 0001/0002, the core gate internals."""

SYSTEM = """You are a senior application-security engineer doing an adversarial CODE review AND
CERTIFICATION of a governance-seam change that makes an autonomous shell EARN autonomy via verified
bubblewrap containment + a signed opt-in. Your job is to BREAK it, not praise it — but ALSO to certify:
if you cannot break a claim, say so EXPLICITLY (a blocked attack is a valuable NON-finding and is part
of certification). Assume the author is wrong until proven otherwise. Concretely try to:
  - FAIL-OPEN the AND gate: make an act_then_report run_command AUTO-run (leash stays act_then_report,
    reaches the executor with require_code_protection but actually runs uncontained; OR floors are
    individually insufficient). Drop ONE conjunct at a time: does the floor still hold if
    code_protection_available() is True but the signed cap is ABSENT? if enforced(session) is False? Is
    any conjunct truthiness-based on a MODEL-controllable value (mutable session.capabilities vs a signed
    directive)? Can a path reach _exec_command with require_code_protection=False yet auto-run a shell?
  - BELIEF-vs-BEHAVIOUR: is code_protected ever re-read from the predicate govern-side instead of bound
    to the executor's REAL result? Can wrap_contained return protected=True while the child actually ran
    UNcontained? Does containment_available() cache a stale True a later call trusts?
  - DEFEAT THE GUARD (contained._guarded_script): a root with no witness; a witness that is a symlink
    OUT of the root; the append-check ( : >> "$f" ) passing though the root is really writable; the
    mountinfo parse (awk '$5==m {print $6}', ",ro," case) spoofable/missing/multiple-mounts; a root path
    with a space/quote/newline breaking `for spec in ...`; exit 45 not propagating through run_supervised;
    the guard running but exec "$@" never reached.
  - SENTINEL CONFUSION: protection_unverified keys on rc==45 + sentinel in STDERR; setup_failed on rc!=0
    + stderr.startswith("bwrap:"). Can the PAYLOAD (the contained command) emit exit 45 + the sentinel to
    stderr to force a FALSE downgrade (protected->False on a run that WAS contained) — and is that
    harmful or merely fail-safe? Can a payload SUPPRESS a real downgrade? Is the 3-check ordering
    exploitable?
  - THE SPLIT: confirm the autonomous path NEVER routes through netns and the human path NEVER through
    bwrap; can any caller reach _exec_command with require_code_protection=True nested under netns? Is
    unshare_net=require_isolation correct — an autonomous run with a signed raw_network gets bwrap
    WITHOUT --unshare-net; is that a hole (bwrap still contains the FS; net is the operator's signed
    choice) or a real leak?
  - THE VIEW: _RO_SYSTEM/_MASK computed at import from host dirs — on a host missing /etc (empty _MASK),
    or a code root that is a symlink, does the bind set still hold? Does --ro-bind of a symlinked root
    bind the target writable elsewhere? Does --proc/--dev/tmpfs /tmp leak an escape (/proc/1/root, a full
    /dev)? Does the probe's throwaway workspace differ from the real session.workspace in a way that lets
    a real run foot-gun (workspace overlapping a code root — is disjoint_from_code load-bearing here?)?
  - REGRESS: does the human path change at all (byte-identical wrap_no_network)? Does the MINOR-B
    approved==executed seal still hold on the contained path? Off-Linux, does ANY path auto-run a shell
    uncontained (wrap_contained returns (argv,False,False) => executor must REFUSE)?
Name file:line, give a CONCRETE input/bypass, and state whether ANOTHER check independently catches it.
Distinguish a real guarantee-breaking bug from a belt-and-suspenders nit or a stated non-goal — do not
invent severity, and do not report the acknowledged out-of-scope items."""

USER_TMPL = """Review + CERTIFY the SHIPPED PR #39 delta below (PR 1b: "protection earns autonomy" —
an autonomous run_command earns autonomy via verified bubblewrap containment + a signed opt-in).

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) /
CONCRETE INPUT OR BYPASS / WHY IT BREAKS A GUARANTEE / WHETHER ANOTHER CHECK CATCHES IT / FIX.
Then a CERTIFICATION LINE for the headline claim ("no false code_protected; a human-approved run is
never contained-claimed; earned autonomy needs the signed cap AND verified containment; the guard
proves ro per-run; bwrap is never nested under netns; nothing regressed") — CERTIFIED / NOT-CERTIFIED +
one sentence. Then STEELMAN (2-3 sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one
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
                 "X-Title": "SalienceOS PR#39 contained Certification Panel"},
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
    raw = HERE / "raw_contained"
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
                    f"# PR#39 contained certification panel: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — PR#39 contained CERTIFICATION panel =======")
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
    print("==================================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
