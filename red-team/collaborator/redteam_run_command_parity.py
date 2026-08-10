"""External 5-vendor CERTIFICATION panel for salient-os PR B — non-Linux run_command parity
(the "isolation earns autonomy" floor, B1) + the approved==executed seal for held run_command /
write_file (MINOR-B).

Per Josh's standing rule (external review on EVERY non-doc PR; doc-only is the sole exception), this
security-relevant change (a fail-closed governance floor + an integrity seal) gets its own external
panel — reproduce-before-accept, per-model + total API cost reported.

Usage:  python red-team/collaborator/redteam_run_command_parity.py [openai/gpt-5.1 ...]
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
    ("THE DELTA — the exact diff PR B ships (read this first)",
     "red-team/collaborator/raw_prB/prB.diff"),
    ("THE CODE (full, for grounding) — collaborator/governance.py",
     "collaborator/governance.py"),
    ("THE CODE — collaborator/netns.py (netns_available + SHELL_RAW_NETWORK_CAP)",
     "collaborator/netns.py"),
    ("THE CODE — collaborator/tools.py (held_action_seal + _exec_command/_exec_write + run_command tool)",
     "collaborator/tools.py"),
    ("THE CODE — collaborator/loop.py (approve(): MINOR-B seal check + the args snapshot)",
     "collaborator/loop.py"),
    ("THE CODE — collaborator/policycaps.py (granted_capabilities / enforced / apply_cap / leash_cap)",
     "collaborator/policycaps.py"),
    ("THE CODE — collaborator/egress.py::emission_seal (the pattern MINOR-B mirrors)",
     "collaborator/egress.py"),
    ("WHAT IS PINNED — tests/test_collaborator_run_command_parity.py",
     "tests/test_collaborator_run_command_parity.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

CODE review + CERTIFICATION of a SHIPPED change (salient-os PR B) to the SalienceOS Collaborator's
governance seam. TWO independent additions to the run_command govern slot, plus a seal extension.

=== BACKGROUND: the run_command govern slot has TWO independent monotonic autonomy floors ===
Each floor does `leash = apply_cap(leash, PROPOSE_FIRST)` (monotonic: stricter always wins). An
`act_then_report` run_command that survives BOTH floors auto-runs; otherwise it is HELD for a human
`approve()`.
  * Floor 1 (F-6 "Harm A" code floor, ALREADY SHIPPED in PR #33-#35): fires when
    `not code_protection_available()`. `code_protection_available()` is HARDCODED False in this build
    (OS-level code-protection is a deferred follow-up), so this floor ALWAYS fires — run_command
    autonomy is withheld UNCONDITIONALLY today.
  * Floor 2 (B1, THIS PR): fires when a run_command is `act_then_report` AND `not netns_available()`
    AND the signed caps do NOT grant `shell.raw_network`.

CONSEQUENCE (state it plainly so you don't mis-rate): B1 is BEHAVIOR-MASKED today — the code floor
already holds every act_then_report shell, so B1 changes no auto-run behavior right now. B1 is the
PRE-WIRED second axis that becomes LIVE only when OS code-protection lands and Floor 1 stands down on
a capable host. This is the documented, intended design (the Harm A seam contract explicitly
anticipated "PR B adds an independent adjacent network-axis guard in the same slot"). Do NOT report
"B1 is a no-op today" as a bug. DO attack whether B1 is CORRECT as the future-live guard: assume code
protection is available (the tests patch `code_protection_available`→True to isolate B1) and try to
make an UNISOLATED, un-opted-in `act_then_report` run_command AUTO-RUN.

=== WHAT PR B SHIPS (attack + CERTIFY these) ===
B1 — "isolation earns autonomy" floor (collaborator/governance.py, the ISOLATION-EARNS-AUTONOMY
FLOOR block; collaborator/netns.py SHELL_RAW_NETWORK_CAP):
  run_command's raw network reach is isolated via a network namespace (netns) ONLY on Linux with
  VERIFIED netns (netns_available() actually checks the child is in a fresh netns, not just that
  `unshare` exited 0). Off Linux / where unverifiable, an `act_then_report` run_command floors to a
  human hand UNLESS the operator granted the default-deny `shell.raw_network` capability (explicit
  "I accept raw reach on this host"). The HELD preview surfaces `raw_network: True` when isolation is
  unavailable (honest — LIVE off-Linux, independent of the opt-in).

RE-CERTIFICATION NOTE (a prior 5-vendor panel already reviewed this PR): the prior panel's one
convergent finding was that approve() was not a SELF-CONTAINED approved==executed boundary — it
relied on govern_action having frozen/sealed the args. NOW FIXED: approve() itself re-freezes
(freeze_args) the snapshot AND re-verifies the seal; the seal binds the TOOL IDENTITY as its first
field; and the seal is verified for a sealed tool OR any non-egress decision that still carries a
seal (so a Decision.tool rebind cannot skip the check). Attack THIS: can ANY held decision (however
constructed) reach execution with args differing from what the seal bound, or a tool rebind replay a
seal? Also RE-CONFIRM the prior refutation: the B1 floor withholds autonomy for an UNSIGNED session
listing shell.raw_network (it requires enforced(session) AND the signed grant) — an unsigned/legacy
cap must NOT stand the floor down.

MINOR-B — approved==executed seal for held run_command / write_file (collaborator/tools.py
held_action_seal / freeze_args; govern_action HELD branch; loop.py approve()):
  net_post emissions already bind approval to the exact payload the human saw via
  egress.emission_seal (a held emission has no verifier). MINOR-B extends the SAME by-reference-
  mutation (TOCTOU) defence to held run_command (argv) and write_file (path+content): the seal is
  computed at hold time; approve() re-computes it over the snapshot args and DENIES on mismatch, and
  fails CLOSED if a run_command/write_file held decision carries no seal. These tools DO have
  verifiers (exit / artifact), so this is defense-in-depth over a real by-reference vector — the same
  vector a prior panel flagged (gemini LP-1) for the human-approve path.

=== THE CERTIFICATION CLAIMS (attack each) ===
  C1 (B1): assuming code protection is available, an `act_then_report` run_command on a host with NO
     verified netns and NO `shell.raw_network` grant is WITHHELD (floored to propose_first / HELD) —
     it can never auto-run with raw network reach. `shell.raw_network` is default-deny and NOT
     model-reachable/forgeable. No ordering gap between the two floors ever yields a result weaker
     than HELD when it should be held.
  C2 (MINOR-B): a held run_command / write_file whose args are mutated after origination is DENIED at
     approval (approved == executed); a held such decision with no seal fails CLOSED; the seal framing
     is injective (no mutation that changes what executes leaves the seal unchanged); no regression to
     legitimate (unmutated) approvals or to proposals.

=== OUT OF SCOPE (deferred non-goals — do NOT rate as bugs) ===
  - The masking itself (B1 not separately observable while the code floor holds) — intended.
  - OS-level structural prevention of a human-APPROVED run_command (bwrap/nested-userns ro-bind) —
    deferred "protection earns autonomy" follow-up.
  - Real OS-native Windows/macOS isolation; the independent egress proxy (ADR 0003 #1b).
  - The pathname-UNIX-socket residual (docker.sock) already documented in netns.py.
  - The single-trust-domain limit (an in-process re-signer) — ADR 0002; the seal closes the
    by-reference HOLD-mutation vector, not a fully-compromised in-process attacker.
  - ADR 0001/0002; the core capability-gate internals; already-certified PR #31/#32/#33-#35 code.
"""

SYSTEM = """You are a senior application-security engineer doing an adversarial CODE review AND
CERTIFICATION of a governance change: two autonomy floors that compose by monotonic capping, and an
integrity seal binding human approval to the exact executed payload. BREAK it, but also certify: if
you cannot break a claim, say so explicitly (a blocked attack is a valuable NON-finding — the author
wants certification as much as bugs). Assume the author is wrong until proven otherwise. Concretely:
  - B1 (assume code_protection_available()==True to isolate it): find ANY path where an
    `act_then_report` run_command with no verified netns and no `shell.raw_network` grant does NOT
    floor to propose_first (auto-runs raw). Probe: can the model/legacy config grant or forge
    `shell.raw_network`? does granted_capabilities/directive.grants_capability make it default-deny?
    can netns_available() be made to falsely return True (read its VERIFIED probe)? does the
    two-floor ordering (code floor then B1, each guarded on `leash == ACT_THEN_REPORT`) leave a gap?
    invalid/unknown leash, runtime leash_overrides mutation, source spoofing, a raising
    netns_available()/directive — anything fail-open. Is the `raw_network` preview flag honest?
  - MINOR-B: find a mutation that changes what EXECUTES but not the seal (non-injective framing —
    the seal is sha256 with 8-byte length prefixes + a leading type tag; probe boundary-shift
    collisions, str-vs-list command, None/dict command, path/content boundary, surrogate/unicode).
    Find a mutation where held_action_seal reads one value but _exec_command/_exec_write runs
    another (the snapshot `args = dict(decision.args)` is shallow; command is a LIST shared by
    reference). Can a seal-less or whitespace-seal held decision RUN? Does the placement (after the
    origin-gated re-denies) leave a user-origin mutation uncaught, or double-deny/mis-report? Is
    `consumed` left False on a seal-deny (retryable)? Any regression to unmutated approvals/proposals?
Name file:line, give a CONCRETE input/bypass (ideally a repro), and state whether ANOTHER layer
(the exit/artifact verifier, the capability gate, the code floor, reauthorized_or_denied)
independently catches it. Distinguish a real guarantee-breaking bug from a belt-and-suspenders nit or
a stated non-goal. A seal-collision the verifier would ALSO catch is lower severity than one that
silently executes unseen bytes."""

USER_TMPL = """Review + CERTIFY the SHIPPED PR B delta below (B1 isolation floor + MINOR-B seal).

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) /
CONCRETE INPUT OR BYPASS / WHY IT BREAKS A GUARANTEE / WHETHER ANOTHER LAYER CATCHES IT / FIX.
Then a CERTIFICATION LINE for EACH claim C1 (B1) and C2 (MINOR-B): CERTIFIED / NOT-CERTIFIED + one
sentence. Then STEELMAN (2-3 sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one
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
                 "X-Title": "SalienceOS PR B run_command parity + MINOR-B Panel"},
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
    raw = HERE / "raw_prB"
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
                    f"# PR B run_command parity + MINOR-B panel: {m}\n\n_finish={r['finish']} "
                    f"seconds={r['seconds']} cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n"
                    f"{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — PR B run_command parity + MINOR-B panel =======")
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
