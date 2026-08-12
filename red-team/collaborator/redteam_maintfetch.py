"""External 5-vendor CODE panel on the SHIPPED maint_fetch build (ADR 0006).

maint_fetch = a human-gated mediated artifact fetch through egress.py that STREAMS a non-vendorable
artifact (proprietary driver, licensed binary) to a workspace-fenced file under a host byte ceiling; the
maintenance shell stays routeless. This reviews the ACTUAL CODE (the DESIGN panel already chose this over
a privileged CONNECT proxy). Attack the streaming fail-closed ceiling, the net.maint: authority-namespace
isolation, the url+dest seal (approved==executed), the workspace fence, the reused transport contract, and
the never-raises boundary.

Reports per-model + total API cost (standing request).

Usage:  python red-team/collaborator/redteam_maintfetch.py [openai/gpt-5.1 ...]
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
    ("THE DECISION — docs/adr/0006-maintenance-egress-proxy.md", "docs/adr/0006-maintenance-egress-proxy.md"),
    ("THE CHANGE (unified diff of the 4 edited files)", "red-team/collaborator/raw_maintfetch/CHANGES.diff"),
    ("FULL egress.py (fetch_to_file + the reused contract)", "collaborator/egress.py"),
    ("FULL tools.py (maint_fetch tool + executor + held_action_seal + freeze_args)", "collaborator/tools.py"),
    ("THE TESTS — tests/test_collaborator_maint_fetch.py", "tests/test_collaborator_maint_fetch.py"),
]

CONTEXT = """CONTEXT FOR REVIEWERS

CODE review of a SHIPPED build. SalienceOS's "Collaborator" is a governed agent. egress.py is its SOLE
mediated IP path — an application-layer HTTPS client with a hard safety contract: canonical_host
(https-only, IDNA, no userinfo/port/IP-literal), is_safe_public_ip (blocks private/loopback/link-local/
CGNAT 100.64/NAT64/IPv4-mapped-v6/metadata 169.254.169.254), a _PinnedHTTPSConnection (resolve-once ->
pin the IP -> SNI-validate the name), and redirect-FAIL-CLOSED. Authority is checked in the governance
seam (default-deny; a per-destination signed capability), NOT in egress.py.

THE BUILD (ADR 0006): maint_fetch(url, dest) — a HUMAN-GATED tool that fetches a non-vendorable artifact
and STREAMS it to a workspace-fenced file:
 * NEW egress.fetch_to_file(url, sink, *, max_bytes, ...): mirrors fetch() through the redirect check,
   then streams resp.read() chunks to sink.write() under a HARD max_bytes ceiling. OVER-CAP -> a non-ok
   record (the executor deletes the partial file). Body is NEVER returned to the model. Never raises.
 * NEW capability namespace net.maint:<canonical-host> (required_capability(url, "MAINT")) — SEPARATE
   from net.get:/net.post: (a read or emit grant never confers a maintenance fetch). ONE derivation site,
   used at BOTH hold-time and approve-time in governance.
 * The tool: Tool("maint_fetch", "net.maint:__derived__", mutating=False, verify_mode="egress_log",
   egress=True, egress_method="MAINT"), default_leash=PROPOSE_FIRST (human-gated, no auto-lift).
 * The executor _exec_maint_fetch: resolve_in_workspace(dest) (WorkspaceError -> DENY), mkdir parent
   within the fence, open(dest,"wb"), egress.fetch_to_file(...); on non-ok -> _unlink_quiet(dest).
 * SEALED: maint_fetch is in SEALED_TOOLS; held_action_seal seals (url, dest) [b"M" branch], freeze_args
   coerces url/dest; loop.approve() re-verifies the seal (approved==executed; a post-hold url/dest swap
   is DENIED). It is egress+NOT-mutating, so it seals via held_action_seal, NOT emission_seal.
 * max_bytes is a HOST value (session.maint_fetch_max_bytes, default 100 MiB), threaded by governance
   into execute_tool; the model's args never carry it.

Governance wiring (in the diff): the egress_log branch derives required_cap via required_capability(url,
"MAINT") at hold AND approve, checks it against the signed caps (default-deny), threads maint_max_bytes,
and surfaces canonical_dest + dest in the held preview. Autonomous-emission logic (net.post.auto, quota,
credentials) is gated on tool.mutating and so does NOT apply to maint_fetch.

DELIBERATE residuals (documented, not bugs): content is bounded+hashed but not SEMANTICALLY inspected
(a compromised signed host could serve bad bytes -> human-gated + operator-signed host); a staged
artifact a human later runs is the human's call; v0 is HTTPS/GET only.

Your job: find where the CODE is WRONG or the contract leaks. Reproduce-before-claim; distinguish a real
defect from a documented residual.
"""

SYSTEM = """You are a senior application-security + Python engineer doing an adversarial CODE review of
maint_fetch — a mediated, human-gated artifact fetch that streams to a workspace file. Assume the author
is wrong until proven otherwise. Be concrete and cite exact functions/lines. Attack these, and say
whether another control catches each:

1. THE STREAMING FAIL-CLOSED CEILING. fetch_to_file streams resp.read(chunk) to sink.write() under
   max_bytes. Can an OVER-CAP artifact be staged as if complete? Is the over-cap check off-by-one (>= vs
   >)? On over-cap the loop returns a non-ok record but the sink already got up to max_bytes — does the
   EXECUTOR reliably delete the partial (Windows file-still-open? the `with` closes before unlink?)? Can a
   non-2xx error page be staged as the artifact? A chunked/again-EOF/zero-length read loop hang? A
   sink.write() OSError — is it caught (never-raises) and does it leave a partial? Does response_len/hash
   reflect ONLY the bytes actually written?
2. THE net.maint AUTHORITY NAMESPACE. required_capability(url,"MAINT") -> net.maint:<host>. Is it TRULY
   separate — can a net.get:<host> (read) or net.post:<host> (emit) grant ever confer a maint fetch, or
   vice versa? Is the SAME canonical_host used for the cap key AND the connect (authorize==connect)? Is
   the derivation identical at hold-time and approve-time (one site) so a TOCTOU can't diverge? An
   ineligible URL -> None -> DENY (fail closed), not a bare "net.maint:"? Could "MAINT" as a pseudo-method
   collide with a real HTTP method path anywhere?
3. THE url+dest SEAL (approved==executed). maint_fetch is in SEALED_TOOLS and seals via held_action_seal
   (b"M": url,dest), NOT emission_seal (it's egress but NOT mutating). Verify: the HOLD path
   (governance ~577) mints held_action_seal for it (egress&mutating is False -> else branch), and
   approve() (loop ~217) re-verifies it (in SEALED_TOOLS). Is there a gap where an egress tool skips BOTH
   seals? Can a Decision.tool rebind replay one tool's seal as maint_fetch? Does freeze_args freeze url
   AND dest so a drifting __str__ / shared mutable can't seal-one-stage-another? Field-framing injective
   (length-prefixed) so url/dest can't steal each other's bytes?
4. THE WORKSPACE FENCE ON dest. resolve_in_workspace(dest) — can a dest escape via ../, absolute path,
   symlink, or the mkdir(os.path.dirname or workspace)? Does the parent-dir creation stay INSIDE the
   fence? Is the WorkspaceError raised (not swallowed) so the gate DENIES? Overwrite of an existing
   workspace file (or a symlink in the workspace pointing OUT) — does open("wb") follow it out of the
   fence?
5. THE max_bytes THREADING + LEASH. Is max_bytes ALWAYS a host value (session.maint_fetch_max_bytes),
   never model-reachable through args? Session validation (positive int / None->default; a bool or 0 or
   negative rejected)? Is maint_fetch actually HUMAN-GATED (HELD) by default with NO auto-lift path (no
   net.maint.auto), and does the mutating=False choice correctly keep it OUT of the net.post.auto /
   emission-quota / credential-injection paths (or does it WRONGLY skip a needed control)?
6. THE REUSED CONTRACT in fetch_to_file. Did the author faithfully mirror fetch()'s redirect-fail-closed,
   is_safe_public_ip pin, request-target cleanliness/bounds, HTTPS-only — or drift (e.g. a second resolve,
   an unsafe fallback IP, a missing control-char check, a header the model can influence)? Never-raises on
   junk URLs / resolve failure / TLS error?

Calibration: concrete, code-grounded, reproduce-before-claim. Distinguish a real defect from a stated
residual (no semantic content inspection; async human-run; https/GET-only). If the code is sound, say so."""

USER_TMPL = """Review the SHIPPED maint_fetch code below.

For EACH finding: ID / TITLE / SEVERITY (CRITICAL|HIGH|MEDIUM|LOW) / FILE:FUNCTION / CONCRETE BUG OR
ATTACK (inputs -> wrong behavior) / WHETHER ANOTHER CONTROL CATCHES IT / FIX. Then a STEELMAN (2-3
sentences) and VERDICT (SOUND / MINOR_ISSUES / SERIOUS_FLAWS + one sentence + the single highest-value
change).

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
                 "HTTP-Referer": "https://salient-os.local/redteam", "X-Title": "SalienceOS maintfetch Code Panel"},
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
    raw = HERE / "raw_maintfetch"
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
                    f"# maintfetch CODE panel: {m}\n\n_finish={r['finish']} seconds={r['seconds']} "
                    f"cost={_fmt_cost(r.get('cost'))} usage={r['usage']}_\n\n{r['content']}\n", encoding="utf-8")
    (raw / "_raw.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = [m for m in MODELS if "error" not in results.get(m, {"error": 1})]

    print("\n======= API COST — maintfetch CODE panel =======")
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
    print("================================================")
    print(f"\nDone: {len(ok)}/{len(MODELS)} succeeded.")


if __name__ == "__main__":
    main()
