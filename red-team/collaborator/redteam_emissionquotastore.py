"""External 5-vendor CODE panel on the SHIPPED durable EMISSION-QUOTA counter store + the shared
`durablestate` substrate (ADR 0003 residual-sweep follow-up, branch feat/durable-emission-quota) BEFORE
merge.

Reuses the provenance-store pattern (out-of-workspace + HMAC + subject binding + fail-honest) — already
panel-vetted — factored into `collaborator/durablestate.py`. The NEW surface a lean pass targets:

  * THE SUBSTRATE — did factoring the security-critical bits into `durablestate` (domain-separated MAC +
    atomic_write) preserve them? Is the provenance store's MAC unchanged (same domain) so existing
    stores still verify? Is the DOMAIN SEPARATION real — a provenance store file must NOT verify as an
    emission-quota store and vice versa (a MAC for one kind replayed as another)?
  * THE FAIL DIRECTION — this is a RESTRICTIVE bound (a quota), so it must fail CLOSED, opposite the
    provenance store: an UNTRUSTED store (bad MAC / wrong subject / corrupt) must DENY a quota-limited
    host, never silently REFRESH the budget. Is that airtight (no host slips through allowed)? Is a
    MISSING store still a fresh trusted start (not a permanent deny)? An unlimited host correctly
    unaffected?
  * THE RESTART-RESET GAP — does the persisted counter actually close "restart to reset the quota"? Any
    path where a new session starts a limited host at 0 despite a prior persisted count?
  * MODEL REACHABILITY / TOTALITY — the store is host-config, out of workspace; can the model
    reset/loosen the quota, or make load/save/consume raise? Persist-per-consume cost.

Reports per-model + total API cost (standing request).

Usage:  python red-team/collaborator/redteam_emissionquotastore.py [openai/gpt-5.1 ...]
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
    ("THE SHARED SUBSTRATE — collaborator/durablestate.py (domain-separated mac + atomic_write)",
     "collaborator/durablestate.py"),
    ("THE EMISSION-QUOTA STORE — collaborator/emissionquotastore.py (load/save, fail-CLOSED direction)",
     "collaborator/emissionquotastore.py"),
    ("THE PROVENANCE STORE (refactored onto the substrate; must be MAC-unchanged) — "
     "collaborator/provenancestore.py", "collaborator/provenancestore.py"),
    ("THE WIRING — collaborator/session.py (emission_quota_store param, load-at-construction, "
     "_emission_store_untrusted -> emission_allowed fail-closed, consume_emission persist)",
     "collaborator/session.py"),
    ("WHAT IS PINNED — tests/test_collaborator_emission_quota_store.py",
     "tests/test_collaborator_emission_quota_store.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

CODE review of a SHIPPED diff (ADR 0003 residual-sweep follow-up), not a design review. SalienceOS's
"Collaborator" bounds outbound emission (net_post) with a per-destination QUOTA (how many emissions may
go to a canonical host), checked + consumed at the single dispatch point every emission path reaches.
The counter (`session._emission_counts`) was SESSION-LIVED, so a RESTART reset it and a patient
drip/spam channel could refresh its budget every new session.

THIS DIFF persists the counter across sessions, opt-in:
- `collaborator/durablestate.py` (NEW) factors the security-critical primitives shared by BOTH durable
  stores: `mac(key, payload, domain)` — a DOMAIN-SEPARATED derived HMAC (derive a per-store-kind key
  from policy_key, then HMAC the payload) so a MAC for one store kind can't be replayed as another; and
  `atomic_write` (tmp + os.replace).
- `collaborator/provenancestore.py` (already merged, F2) is refactored onto the substrate — SAME domain
  label, so its MAC output is byte-identical and existing provenance stores still verify.
- `collaborator/emissionquotastore.py` (NEW) persists `{host: count}` with a DISTINCT domain, subject
  binding, out-of-workspace requirement, and TOTAL load/save.
- `Session(emission_quota_store=<abs path>)` — host config, default None = session-lived (unchanged).
  Reuses `_assert_store_outside_workspace` (the store must be OUTSIDE the workspace; the contained agent
  writes only the workspace). `consume_emission` persists; the counter loads at construction.

THE FAIL DIRECTION IS THE KEY DIFFERENCE from provenance (which loads empty on untrusted, an advisory
loss). A quota is RESTRICTIVE, so an UNTRUSTED store (bad MAC / wrong subject / corrupt) must NOT loosen
it: `load` returns ok=False, Session sets `_emission_store_untrusted=True`, and `emission_allowed`
returns False for any quota-LIMITED host (an unlimited host is unaffected) — FAIL CLOSED, recoverable by
the operator resolving the store. A MISSING store is still a fresh trusted start (empty counts, allowed).

Documented, NOT findings: concurrent same-workspace sessions are not serialized (sequential-session
design; os.replace atomic; a file-lock is a follow-up); the agent cannot cause an untrusted store (out
of workspace), so the fail-closed guards disk corruption, not an agent exploit; integrity assumes a
host-SECRET policy_key (public default degrades to corruption detection; unreachability is the real
property).
"""

SYSTEM = """You are a senior security engineer doing an adversarial CODE review of a durable,
integrity-protected emission-QUOTA counter store + the shared substrate it factors out. Assume the
author is wrong until proven otherwise. The advisory/documented scope (concurrency; count-not-content;
public-default-key degradation) is stated — don't re-report it. Report where the layer fails ITS OWN
goal (persist the quota so a RESTART cannot refresh a drip budget, WITHOUT letting the agent
reset/loosen it, and FAIL CLOSED when the store can't be trusted) or breaks a HARD invariant.

Attack, name file:line, give a CONCRETE case, say whether another control catches it:
1. FAIL-DIRECTION. This is a RESTRICTIVE bound. Trace `load` untrusted -> Session `_emission_store_untrusted`
   -> `emission_allowed`. Is there ANY untrusted-store path where a quota-LIMITED host is still ALLOWED
   (silent budget refresh / fail-open)? Is a MISSING store correctly a fresh ALLOW (not a permanent
   deny that bricks emissions)? Does the None-host / unlimited-host handling interact wrongly with the
   untrusted deny? Could a partial write / truncated file load as trusted-empty (reset) instead of
   untrusted-deny?
2. DOMAIN SEPARATION / SUBSTRATE. Is `durablestate.mac` domain separation real — can a PROVENANCE store
   (or any other-domain MAC) be replayed as an emission-quota store, or vice versa? Did the provenance
   refactor CHANGE its MAC (breaking existing stores) — is the domain label identical? Any bug in the
   derived-key HMAC (key handling, bytes coercion) that weakens it? atomic_write correctness (os.replace,
   the .tmp name, mkdir)?
3. RESTART-RESET. Does the persisted counter actually close the gap — a new session with a prior
   persisted count starts the limited host at that count, not 0? Any path (subject derivation, load
   order, the consume-then-persist) where a restart silently resets to 0? Off-by-one on the boundary
   (Nth allowed, N+1 denied) preserved across the reload?
4. MODEL REACHABILITY / TOTALITY. The store is host-config, out of workspace — can the model
   reset/loosen the quota, reach `_emission_counts`/`_emission_store_untrusted`, or make
   load/save/consume/_assert_store_outside_workspace RAISE out of Session.__init__ or a govern path
   (bad path, permission error, race, huge file)? Session/govern promise not to blow up.
5. REGRESSION. Does adding the store or the untrusted-deny change behaviour for a session WITHOUT a
   store (default None), or for an unlimited (None) quota? Does per-consume persistence introduce a
   failure that could break an emission (must be best-effort, never raise)?

Calibration: concrete, code-grounded. The AGENT is the adversary; the human operator is trusted. The
fail-CLOSED-on-untrusted for a quota is DELIBERATE (opposite the advisory provenance store) — judge
whether it is correctly airtight, not whether it should exist. If sound, say so plainly."""

USER_TMPL = """Review the SHIPPED durable emission-quota store + durablestate substrate below.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / LOCATION (file:line) /
CONCRETE CASE / WHY IT FAILS-OPEN/BREAKS-SEPARATION/BREAKS-TOTALITY / WHETHER ANOTHER CONTROL CATCHES IT /
FIX. Then STEELMAN (2-3 sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence +
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
                 "X-Title": "SalienceOS emission-quota-store Code Panel"},
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
    raw = HERE / "raw_emissionquotastore"
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
                    f"# emission-quota-store code panel: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — emission-quota-store CODE panel =======")
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
    print("=========================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
