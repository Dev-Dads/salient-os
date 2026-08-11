"""Red-team + CERTIFY the SHIPPED PR 1b-#2 delta (salient-os feat/independent-egress-observer, PR #40).

An external 5-vendor CODE panel on the INDEPENDENT egress observer (ADR 0003 revisit #1b): a kernel-vantage
(nft OUTPUT uid-hook strong tier / /proc fallback) that reconciles what actually left the machine against
the EgressRecord, upgrading egress from "channel-integrity logging" (observer == executor) toward the FS
verifier's "hands can't lie" property. Certification is as important as bug-finding: a claim we CANNOT break
is a valuable NON-finding, recorded as such. The internal adversarial pass runs separately.

Reports per-model + total API cost (standing request).

Usage:  python red-team/collaborator/redteam_egressobserver.py [openai/gpt-5.1 ...]
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
    ("THE DELTA — the exact diff PR #40 ships (read this first)",
     "red-team/collaborator/raw_egressobserver/egressobserver.diff"),
    ("THE NEW CODE (highest risk) — collaborator/egressobserver.py (the whole observer: _resolve/_NFT "
     "(sudo-vs-root); _nft_ruleset (the passive uid-scoped OUTPUT hook, policy accept); install/teardown "
     "(SCOPED to table inet salient_obs, error-tolerant, NEVER flush ruleset); EgressSnapshot; _parse_nft_set; "
     "_nft_snapshot; _hex_to_endpoint/_proc_snapshot (the unprivileged fallback); snapshot; observer_available "
     "(cached VERIFIED probe); ReconcileResult; reconcile (TRI-STATE — True/False/None, never a false verify); "
     "begin/end (the per-action window))",
     "collaborator/egressobserver.py"),
    ("THE CODE — collaborator/tools.py (_egress_claimed; the begin()/end() wrapping of the two egress "
     "executors _exec_web_fetch + _exec_net_post; Execution.egress_obs)",
     "collaborator/tools.py"),
    ("THE CODE — collaborator/governance.py (Decision.egress_reconciled tri-state + egress_discrepancy; the "
     "RAN Decision attach ~line 697; summary() rendering — EVIDENCE not a gate: RAN/cleared never change on a "
     "discrepancy)",
     "collaborator/governance.py"),
    ("THE CODE — collaborator/egress.py (what the observer reconciles against: EgressRecord.resolved_ip; egress "
     "is HTTPS-only :443 non-443-refused; no-redirect-fail-closed; canonical_host/IP-pin transport contract)",
     "collaborator/egress.py"),
    ("THE PRECEDENT it graduates toward — salienceos/verifier/observers.py (the FS verifier's independent "
     "re-observation / WorldEvidence pattern this egress observer mirrors)",
     "salienceos/verifier/observers.py"),
    ("WHAT IS PINNED — tests/test_collaborator_egressobserver.py (the guarantees as tests, incl. the "
     "BlastRadiusSafety pins (never flush ruleset / never drop), the tri-state reconcile, and the Linux "
     "@skipUnless LIVE proof)",
     "tests/test_collaborator_egressobserver.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

CODE review + CERTIFICATION of a SHIPPED diff (salient-os PR #40). SalienceOS is a salience-based control
plane; invariant P-01: salience INFLUENCES, policy AUTHORIZES. The "Collaborator" is a governed agent whose
one mediated network client is collaborator/egress.py (Tier-1 GET / Tier-2 POST): default-deny, exact-host
signed capability net.get/net.post:<host>, HTTPS-only (:443, non-443 refused), IP-pinned (canonical host ==
connect host), no-redirect-fail-closed, host-injected credential never logged. run_command's raw network is
netns-isolated on Linux, so egress.py is the sole IP path.

THE GAP THIS PR CLOSES (ADR 0003 revisit #1b): the egress audit (EgressRecord) is CHANNEL-INTEGRITY LOGGING,
NOT verification — egress.py both MAKES and RECORDS the request, so "the observer IS the executor". It proves
what was sent through the sanctioned channel, NOT that no other bytes left, and NOT that the wire bytes match
the record's claimed dest. A compromised/buggy egress client, or a SECOND in-process HTTP client that never
calls egress.py, can log a benign dest while bytes leave elsewhere — and netns does NOT catch it (it proves
only that bytes left SOME socket in the namespace, never tying them to the record).

WHAT SHIPPED IN THIS PR (attack + CERTIFY this):
  - collaborator/egressobserver.py (NEW): an INDEPENDENT vantage — it reads the KERNEL's own account of which
    destinations the Collaborator's uid connected to, from OUTSIDE the egress client. STRONG tier: a PASSIVE,
    uid-scoped (meta skuid) nft OUTPUT hook (policy accept — observational, never drops) records every
    outbound TCP packet's `ip daddr . dport` (ALL packets, NOT SYN-only — so a connection PRE-ESTABLISHED
    before the window and reused to exfil in-window is still observed) into a dynamic set + counter; read-only
    before/after snapshots around an egress action = that action's dest-set, RACE-FREE and ATTRIBUTABLE; the
    client cannot remove/forge a root-installed rule (needs root / passwordless sudo). FALLBACK tier:
    /proc/net/tcp sampling of the uid's own sockets (point-in-time, no bytes). UNAVAILABLE (non-Linux / no
    vantage): egress_observed=False — never a fake claim.
  - RECONCILE is TRI-STATE + honest: True = strong tier confirmed every observed dest is accounted for by the
    record ("hands didn't lie"); False = a dest the record can't explain was OBSERVED (a second client / wrong
    dest) — surfaced loudly; None = UNCHECKED (no vantage, OR the racy /proc fallback saw no discrepancy but
    cannot positively confirm) — NEVER a false "verified". EVIDENCE, not a gate (P-01): Decision.egress_reconciled
    + a discrepancy note; it FLAGS, never denies; RAN/cleared/status are unchanged by a discrepancy.
  - STRICTLY SCOPED blast radius: the module ONLY ever creates/deletes `table inet salient_obs` (via
    `nft delete table`, error-tolerant), NEVER `nft flush ruleset`, never touches another table.

THE CERTIFICATION CLAIM (attack it HARDEST): "reconcile NEVER returns a false True (a clean confirmation it
did not earn — especially the racy /proc fallback and an empty/failed nft snapshot must be None, not True);
a second in-process client / wrong dest IS observed and surfaced as a discrepancy at the strong tier; the
observer is EVIDENCE only (a discrepancy never changes RAN/cleared, and an observer failure never blocks or
fails an otherwise-good egress); and the module can NEVER disturb a host firewall (only ever creates/deletes
its own table, never flush ruleset, rule is policy accept / never drops)."

RE-CERTIFICATION NOTE (this is the FINAL shipped code, hardened after a first panel — attack the CODE AS IT
STANDS in the diff; do not assume a fix is correct because it is claimed). Hardening already applied and to be
re-attacked, not re-found: (1) the OUTPUT rule matches ALL outbound TCP, not just the SYN (a pre-established
connection reused in-window IS observed); (2) `_parse_nft_set` FAILS CLOSED to None on any parse/structural
failure INCLUDING a `set` object whose elements are present but don't decode to an (ip,port) — a silent empty
that could mint a false True is gone; (3) a strong-tier read that fails mid-action returns reconciled=None with
tier STILL strong (honestly distinct from "no vantage"); (4) `begin()` returns UNAVAILABLE if install() fails
(no stale-table attribution); (5) IPs are canonicalized both sides (no IPv6 text-form false discrepancy);
(6) reconcile's verdict is over the DESTINATION SET — it deliberately does NOT assert per-dest connection
multiplicity (the strong counter is PACKETS, not connections), so an over-claim of a repeat connection to an
ALREADY-observed dest is True, while a HIDDEN new dest is caught as unexpected=False; (7) the availability
probe caches only a POSITIVE tier (a transient none self-heals); (8) `_resolve` requires an executable binary
(no sudo-prompt hang). If any of these is not actually closed by the code in the diff, THAT is a finding.

DESIGN CONSTRAINT (owner's steer — do NOT re-litigate as 'wrong', only whether the CODE implements it): this
OBSERVES, it does not ENFORCE. Confinement so nothing CAN egress unobserved (an enforced netns chokepoint) is
the DEFERRED full-parity follow-up. The strong tier is deliberately privileged (a root-installed rule the
non-root client cannot tamper with) — like netns needs the userns sysctl.

OUT OF SCOPE — DEFERRED / BY DESIGN (do NOT report as findings; acknowledged):
  - A ROOT-COMPROMISED client that flushes/edits the kernel state — the root-compromised-host residual (same
    stance as the bwrap CVE work). "Root can evade" is NOT a finding.
  - SAME-UID co-tenant egress (another process of the same uid) → an honest false-positive residual.
  - UDP/DNS exfil and (partial) IPv6 nuance, and the observe-not-enforce gap — documented residuals; a
    bypass via a channel the strong tier doesn't watch is a NON-finding UNLESS the PR CLAIMS to catch it.
  - The enforced chokepoint (nothing CAN egress unobserved) — the explicit follow-up, not this PR.
  - A false-POSITIVE discrepancy (dual-stack/CDN multi-IP) is at most a LOW audit-noise nit (it is evidence,
    never a deny) — rate it honestly, not as a guarantee break.
  - egress.py's own transport contract, the core gate, ADR 0001/0002 — already certified elsewhere."""

SYSTEM = """You are a senior application-security engineer doing an adversarial CODE review AND CERTIFICATION
of an INDEPENDENT egress observer that reconciles kernel-observed egress against a mediated client's record.
BREAK it, don't praise it — but ALSO certify: if you cannot break a claim, say so EXPLICITLY (a blocked
attack is a valuable NON-finding). Assume the author is wrong until proven otherwise. Concretely try to:
  - make `reconcile` return a FALSE True (a clean confirmation it did not earn): via the racy /proc fallback,
    an empty or failed nft snapshot, a None snapshot falling through, or a claimed-but-unobserved at the strong
    tier being mis-scored True. The cardinal sin is a false "verified".
  - EVADE observation while the record stays benign: a second client on a different uid / UDP / IPv6 / an
    existing (non-SYN) connection / the install delete-then-create window / a dport the rule misses / loopback
    proxy. State which are REAL evasions the PR claims to catch vs documented residuals.
  - disturb the HOST FIREWALL: any path to `nft flush ruleset`, deleting/editing another table, or emitting a
    `drop`/`reject` instead of `policy accept`. Check install/teardown scoping under a crafted uid/host.
  - CRASH a benign egress: feed malformed nft-JSON / /proc rows / odd hex to the parsers — do they fail closed
    (empty/None) or RAISE and escape begin/end/_exec_web_fetch, turning a good fetch into an error? Can a sudo
    prompt HANG (must be `sudo -n`)? Can the cached probe poison to UNAVAILABLE forever?
  - break EVIDENCE-NOT-GATE (P-01): does a discrepancy EVER change RAN/cleared/status, or does an observer
    failure block/fail an otherwise-good egress?
  - FALSE-POSITIVE a legit fetch (dual-stack/CDN/redirect) into a discrepancy — rate the harm honestly (it is
    evidence, not a deny).
Name file:line, give a CONCRETE input/bypass, and state whether ANOTHER check independently catches it.
Distinguish a real guarantee-break from a documented residual or a low audit-noise nit — do not invent
severity, and do not report the acknowledged out-of-scope items."""

USER_TMPL = """Review + CERTIFY the SHIPPED PR #40 delta below (the independent egress observer — ADR 0003
revisit #1b: a kernel vantage reconciling actual egress against the mediated client's record).

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) / CONCRETE INPUT OR
BYPASS / WHY IT BREAKS A GUARANTEE / WHETHER ANOTHER CHECK CATCHES IT / FIX. Then a CERTIFICATION LINE for the
headline ("reconcile never returns a false True; a second client / wrong dest is observed + surfaced at the
strong tier; the observer is evidence-only and never blocks a good egress; the module can never disturb a host
firewall") — CERTIFIED / NOT-CERTIFIED + one sentence. Then STEELMAN (2-3 sentences) and VERDICT (SOUND /
MINOR_ISSUES / SERIOUS_FLAWS + one sentence, and the single highest-value fix).

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
                 "X-Title": "SalienceOS PR#40 egress-observer Certification Panel"},
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
    raw = HERE / "raw_egressobserver"
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
                    f"# PR#40 egress-observer certification panel: {m}\n\n_finish={r['finish']} "
                    f"seconds={r['seconds']} cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n",
                    encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — PR#40 egress-observer CERTIFICATION panel =======")
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
