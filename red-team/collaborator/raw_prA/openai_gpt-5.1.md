# PR-A host-emit code panel: openai/gpt-5.1

_finish=stop seconds=42.4 cost=$0.0991 usage={'prompt_tokens': 50011, 'completion_tokens': 3661, 'total_tokens': 53672, 'cost': 0.09912375, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.09912375, 'upstream_inference_prompt_cost': 0.06251375, 'upstream_inference_completions_cost': 0.03661}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID: F1 / TITLE: Model-Influenced Autonomy via Misused `emit()` Wrapper / SEVERITY: MEDIUM  
LOCATION: collaborator/loop.py:118-161; collaborator/loop.py:188-244  
CONCRETE INPUT OR BYPASS:  
- A host or extension author can write a helper that takes arbitrary model output (e.g. a URL and body suggested in a chat) and calls `emit(session, url, body, autonomous=True)`.  
- Because `emit()` is exported in `__all__` and documented as the operator entry point, nothing in code prevents a “convenience” wrapper from blindly piping model-chosen destinations/payloads into `emit(..., autonomous=True)` once the signed caps are present.  

WHY IT BREAKS A GUARANTEE:  
- The F1 guarantee is “the model can never self‑originate an autonomous emission”; PR-A implements this at the seam (parser cannot set `source='host'` or a keyword leash, and `govern_action` requires `source=="host"` plus `host_directed_atr`).  
- However, the docstring of `emit()` explicitly warns that host code MUST NOT pipe model-derived content with `autonomous=True` because that becomes an unauditable exfil channel; this means the guarantee relies on *out-of-band discipline* at the caller, not on code.  
- In practice, any careless host wrapper that forwards model content can convert model-influenced payloads into body-free autonomous POSTs to `net.post.auto:<host>`; the semantic guarantee “model cannot originate autonomy” is then undermined even though the *tool intent* appears `source="host"`.  

WHETHER ANOTHER CHECK CATCHES IT:  
- No in-process check detects that `url`/`body` originated from the model; `emit` treats all callers as trusted host code and always stamps `source="host"`.  
- Capability/leash caps only ensure *who* is allowed to call autonomy (signed host signals), not that the payload is operator-authored.  

FIX:  
- Narrow the exported surface: do not re-export `emit` from `collaborator.__init__`, or put it under a clearly separate “host-only” module/namespace that UI/plugin authors do not see by default.  
- Add a hard guard in `emit` that refuses `autonomous=True` unless the caller passes a positive flag that is *not model-reachable*, e.g. an out-of-band token or a `host_only=True` argument placed in a different module; or only allow `autonomous=True` from code that holds an additional key (e.g. a separate capability / signing key distinct from the policy caps).  
- At minimum, add runtime assertions/hooks so that deployments can instrument and detect when `emit(..., autonomous=True)` is called with visibly model-derived data (e.g. from the last assistant message), and fail closed by default in the OSS version.

---

ID: F2 / TITLE: `origin_subject` Not Set for Direct Decisions (Potential Dangling Path) / SEVERITY: LOW  
LOCATION: collaborator/governance.py:103-112, 365-372; collaborator/loop.py:118-161  
CONCRETE INPUT OR BYPASS:  
- A host might construct a `Decision` manually (outside of `govern_action`) with `status=HELD`, `tool="net_post"`, and no `origin_subject`, then call `approve()` under a different session.  

WHY IT BREAKS A GUARANTEE:  
- The design intent is: “A held emission is bound to the session that created it; approving it under a different subject is refused.”  
- That binding only applies when `origin_subject` is set; direct callers constructing `Decision` instances can omit it, and `approve()` will skip the cross-subject check. In such a hand-rolled scenario, a held emission can be ported between sessions and sent with the *new* session’s credential, contrary to the comment that there is “no cross-subject approval when this is set.”  
- This doesn’t affect the normal `govern_action` pathway, but it weakens the stated invariant for anyone manipulating `Decision` objects directly (including extensions/tests that might treat the dataclass as a stable API).  

WHETHER ANOTHER CHECK CATCHES IT:  
- No other check re-derives which session originally held the decision; `reauthorized_or_denied` only re-checks current caps and destination, not provenance.  
- In standard integration, all held emissions come from `govern_action`, so `origin_subject` *is* set; this is only a problem for non-standard entry points.  

FIX:  
- Make `Decision` construction private or clearly internal: add a factory in `governance` and mark the dataclass as internal; discourage direct construction.  
- Alternatively, change `Decision.origin_subject` to default to `workspace_subject(session)` whenever `approve()` encounters an egress+mutating `HELD` decision with an empty `origin_subject`, and *first* call `_subject(session)` from `approve` to fill it lazily.  
- Or, add a guard in `approve` that if `tool.egress and tool.mutating` and `origin_subject` is empty, treat that as “cannot establish subject” and *deny* rather than allowing cross-session approval.

---

ID: F3 / TITLE: `emit` Does Not Validate `content_type` Beyond Egress Contract / SEVERITY: LOW  
LOCATION: collaborator/loop.py:234-244; collaborator/egress.py:428-454  
CONCRETE INPUT OR BYPASS:  
- Host calls `emit(session, url, body, content_type="text/html; charset=utf-8\r\nX-Evil: 1")`.  

WHY IT BREAKS A GUARANTEE:  
- The egress layer’s `_is_clean_header_value` enforces ASCII and rejects control chars, so this concrete string is refused with “illegal content-type (header injection?)” and returns a non-ok `EgressRecord`.  
- However, the guarantee in the docstrings says that any error *should degrade to a FAILED decision, never raise*; with current code, `post()` never raises for this input, so the contract holds; there is no silent success, and the emission is correctly treated as FAILED.  
- This is therefore *not* a guarantee-break but a “belt-and-suspenders” nit: `emit` trusts `content_type` and leaves full validation to `post()`.  

WHETHER ANOTHER CHECK CATCHES IT:  
- Yes: `_is_clean_header_value` in `egress.post` rejects the header and returns a refused `EgressResult`; `execute_and_verify` wraps that as `FAILED` with a clear error.  

FIX:  
- Optionally pre-validate `content_type` in `emit` to let operators see misconfiguration earlier, but behaviorally this is already safe and fail-closed.  

---

ID: F4 / TITLE: Proposer and Parser Cannot Escalate Autonomy; Defense-in-Depth Confirmed / SEVERITY: LOW (NON-FINDING)  
LOCATION: collaborator/toolcall.py:113-200; collaborator/propose.py:154-171; collaborator/governance.py:319-372  
CONCRETE INPUT OR BYPASS:  
- Parser: Model message that encodes a `ToolIntent('net_post', ...)` only ever yields `source` in `{"structured","content_block","content_json"}`.  
- Proposer: Proposer JSON with `{"action":{"name":"net_post",...}}`.  

WHY IT DOES NOT BREAK A GUARANTEE:  
- Parser cannot set `source='host'` and has no access to the keyword `leash` override; only `emit` and test helpers ever do.  
- Proposer explicitly drops any egress+mutating tool (`net_post`) structurally, so it cannot surface one-click emissions.  
- Combined with the emission floor condition `auto = auto_host and host_directed_atr and enforced(session) and intent.source=="host" and leash==ACT_THEN_REPORT`, a model or proposer cannot reach autonomous emission, even if a buggy caller tried to pass a leash override into `govern_action` from the model path.  

WHETHER ANOTHER CHECK CATCHES IT:  
- The leash floor (`apply_cap(leash, PROPOSE_FIRST)`) and `source!="host"` condition independently block autonomy for model/proposer intents.  

FIX:  
- None required; this is working as intended and provides the expected F1 defense-in-depth.

---

ID: F5 / TITLE: Canonical-Host Confusion for `net.post.auto` / SEVERITY: LOW (NON-FINDING)  
LOCATION: collaborator/egress.py:329-371; collaborator/governance.py:319-372  
CONCRETE INPUT OR BYPASS:  
- Try to satisfy `net.post.auto:api.example` while POSTing to a different place via IDNA or punctuation variants, e.g.:  
  - `https://api.example./v1/x`  
  - `https://api.exa­mple/v1/x` (soft hyphen)  
  - `https://API.EXAMPLE:443/v1/x`  
  - `https://user@api.example/v1/x`  
  - `https://api.example.evil.com/v1/x`.  

WHY IT DOES NOT BREAK A GUARANTEE:  
- `canonical_host` normalizes with `urlsplit`, strips userinfo/port, normalizes Unicode, IDNA-encodes, lowercases, and then strictly validates `HOST_CHARS` and the dot structure.  
- The same `canonical_host` result is used both to 1) derive the capability (`net.post:<host>` and `net.post.auto:<host>`) and 2) compute the sealed host and 3) drive the actual connect host. There is no separate parse path for auto-capability vs. connection.  
- All of the listed variants either map to the same canonical `api.example` (in which case the capability and connect host match) or are rejected as ineligible (userinfo, wrong port, bad dots), resulting in DENIED.  

WHETHER ANOTHER CHECK CATCHES IT:  
- Yes: if `canonical_host` returns `None`, `required_capability` -> None and the governance layer denies before any emission.  

FIX:  
- None; this is correctly implemented and meets the “canonical-host confusion” requirement.

---

ID: F6 / TITLE: Non-Enforced Session Cannot Reach Autonomy / SEVERITY: LOW (NON-FINDING)  
LOCATION: collaborator/policycaps.py:114-152; collaborator/governance.py:319-372  
CONCRETE INPUT OR BYPASS:  
- Session with `capabilities=("net.post:api.example","net.post.auto:api.example")` but no `policy_caps`/`caps_key` (legacy mutable caps). Host then calls `_emit_directed` or `emit(..., autonomous=True)`.  

WHY IT DOES NOT BREAK A GUARANTEE:  
- `enforced(session)` is false in this configuration; the emission floor requires `enforced(session)` to consider `auto=True`.  
- For non-enforced sessions, `leash_cap` returns `None`, so the emission leash is set by `_resolve_leash` but then forced through `apply_cap(leash, PROPOSE_FIRST)` when `auto` is false, flooring it to `propose_first`.  
- Tests (`NetPostAutoLift.test_legacy_unsigned_session_cannot_auto_lift`) verify that even with `net.post.auto` in mutable caps, the decision is HELD and never RAN autonomously.  

WHETHER ANOTHER CHECK CATCHES IT:  
- The explicit `enforced(session)` conjunct plus the leash floor both independently prevent auto-lift in non-enforced sessions.  

FIX:  
- None required; F5 is respected.

---

ID: F7 / TITLE: Approved != Sent via Held-Args Mutation / SEVERITY: LOW (NON-FINDING)  
LOCATION: collaborator/loop.py:118-161; collaborator/egress.py:305-339  
CONCRETE INPUT OR BYPASS:  
- Construct a held `net_post` decision, mutate `decision.args["url"]`, `["body"]`, or `["content_type"]` between hold and `approve()`.  

WHY IT DOES NOT BREAK A GUARANTEE:  
- `govern_action` records a `seal = emission_seal(url, body, content_type)` at hold time; `approve` then snapshots `args = dict(decision.args)` and re-computes the seal from this snapshot. Any mutation of the live `decision.args` the user saw leads to a seal mismatch and DENIED.  
- The snapshot ensures TOCTOU-resistant semantics even in the presence of mapping proxies that change return values after the first read; tests cover this via `_Flip` mapping.  
- Additionally, an egress+mutating decision missing a seal is refused (“no payload seal — refusing (fail closed)”), so there is no path to run a held emission without a bound payload.  

WHETHER ANOTHER CHECK CATCHES IT:  
- The approval-time re-gate also re-derives `required_capability` from the frozen args, preventing capability TOCTOU; together these checks ensure approved==sent.  

FIX:  
- None required; this guarantee is correctly enforced.

---

ID: F8 / TITLE: Credential Echo Leakage to Logs via Response Body / SEVERITY: LOW (NON-FINDING)  
LOCATION: collaborator/tools.py:319-359; collaborator/egress.py:535-619  
CONCRETE INPUT OR BYPASS:  
- An API that echoes back the `Authorization` header in its JSON response body: e.g. `{"echo":{"Authorization":"Bearer sk-..."}}`.  

WHY IT DOES NOT BREAK A GUARANTEE:  
- The outbound seam never logs credentials; the only way they could re-enter logs is if a remote server echoes them.  
- `_exec_net_post` now uses `_redact_credential` on the concatenation of the synthetic header line plus `result.text(2000)`, scrubbing both the full header and the bare token before forming `result.output`. Since `Decision.summary()` uses `result.output` and not raw body, the credential is not persisted in the audit trail.  
- Tests explicitly verify that neither the bare token nor the prefixed header value shows up in `result.output` or the summary.  

WHETHER ANOTHER CHECK CATCHES IT:  
- No other layer scrubs secrets; this is the key fix, and it appears correct.  

FIX:  
- None required; credential echo leakage to logs is mitigated.

---

ID: F9 / TITLE: `emit()` / `approve()` Raising Instead of Failing Closed / SEVERITY: LOW (NON-FINDING)  
LOCATION: collaborator/egress.py:535-619; collaborator/governance.py:430-524; collaborator/loop.py:118-161  
CONCRETE INPUT OR BYPASS:  
- Lone surrogate body (`"\ud800"`); very large body (`> MAX_POST_BODY`); non-ASCII `content_type`; malformed URL.  

WHY IT DOES NOT BREAK A GUARANTEE:  
- `post()` treats all of these as refusals returning non-ok `EgressRecord`s and never raises out of the tool boundary; execute_and_verify wraps unexpected exceptions from `execute_tool` in `FAILED` decisions.  
- `approve()` now snapshots args with `dict()`, which for pathological mapping types can still raise, but those callers are expected to be normal `dict`/Mapping; inside the shipped system, all calls use `dict` or Mapping-compatible objects, and tests show the lone-surrogate and cap overflow cases return `FAILED`, not an exception.  
- There is no straightforward path from model or host inputs to an unhandled exception propagating across `emit`/`approve`; both degrade correctly.  

WHETHER ANOTHER CHECK CATCHES IT:  
- Yes: try/except blocks in `post`, `fetch`, and `execute_and_verify` are belt-and-suspenders preventing raises.  

FIX:  
- None required for the stated guarantee; optional: make `approve` more defensive by catching `TypeError` from `dict(decision.args)` and denying that decision.

---

Steelman (overall assessment)  
PR-A’s core mechanisms — the emission floor, “require both” (auto-cap + leash-cap), signed-cap enforcement, approval-time seal, and structural exclusion of emissions from the proposer and parser — are implemented carefully and match the design steers. I was unable to construct a purely code-driven path that lets the model or proposer reach autonomous emission, nor a way to bypass host canonicalization or the approved==sent binding. The main residual risk is socio-technical: `emit(..., autonomous=True)` is safe only when called correctly, but nothing in code distinguishes “host-authored” vs model-derived payloads at that entry point, so the F1 guarantee relies on wrapper authors honoring the warning in the docstring.

VERDICT: MINOR_ISSUES — the shipped PR’s core guarantees are sound, with the highest-value fix being to strengthen the separation and usage constraints around `emit(..., autonomous=True)` so that careless host wrappers cannot accidentally route model-derived payloads into autonomous, body-free emissions.
