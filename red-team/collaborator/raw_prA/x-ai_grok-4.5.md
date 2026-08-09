# PR-A host-emit code panel: x-ai/grok-4.5

_finish=stop seconds=217.2 cost=$0.1708 usage={'prompt_tokens': 55073, 'completion_tokens': 10150, 'total_tokens': 65223, 'cost': 0.1708284, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1708284, 'upstream_inference_prompt_cost': 0.1099284, 'upstream_inference_completions_cost': 0.0609}, 'completion_tokens_details': {'reasoning_tokens': 7449, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

### F-01 / NON-FINDING — F1: model/proposer cannot auto-run net_post
**Severity:** n/a (blocked)  
**Location:** `collaborator/loop.py` (`run_turn` ~L74; `emit` ~L218–220); `collaborator/toolcall.py` (`_coerce_call` / `parse_message`); `collaborator/propose.py` (`_candidate_from_response` structural bar); `collaborator/governance.py` emission floor (~L334–365)

**Attempted bypass:** Full signed grant `net.post:api.example` + `net.post.auto:api.example` + `net_post:act_then_report`, model tool_call `net_post` via `run_turn`; or proposer candidate with `leash=ACT_THEN_REPORT`.

**Why it does not break:**  
- Parser only stamps `structured` | `content_block` | `content_json` — never `host`, and never a keyword `leash`.  
- `run_turn` → `govern_action(session, intent)` with no `leash=`.  
- Floor requires **all of** `host_directed_atr` (keyword `leash==act_then_report`) **and** `source=="host"` **and** `enforced` **and** `net.post.auto:<canon>` **and** post-cap `leash==ACT_THEN_REPORT`.  
- Proposer hard-drops egress+mutating tools before `govern_action`; even a direct `source="proposed"` + keyword ATR is floored earlier and fails `source=="host"`.  
- `emit()` is the only production stamp of `source="host"` + optional ATR leash; it is not on the model path.

**Independent catch:** terminal `execute_and_verify` leash re-cap; propose structural bar.  
**Fix:** none.

---

### F-02 / NON-FINDING — require-both + MINOR-A (unlisted net_post)
**Severity:** n/a (blocked)  
**Location:** `collaborator/governance.py` ~L334–390; `collaborator/policycaps.py` `leash_cap` (unlisted → `NOTIFY_ONLY` under enforcement)

**Attempted bypass:**  
(A) auto grant only, empty `leash_caps`;  
(B) ATR leash-cap only, no `net.post.auto:<host>`;  
(C) unlisted net_post + `emit(..., autonomous=True)`.

**Why it does not break:**  
- (A)/(C): after `apply_cap(..., leash_cap)`, `leash` is `NOTIFY_ONLY` ≠ `ACT_THEN_REPORT` → `auto` false → stays notify-only; **no POST**; loud reason when operator-directed + enforced + `auto_host`. Not a silent no-op.  
- (B): `auto_host` false → floor to `propose_first` → **HELD**, not RAN.  
- `signed_leash_cap` removal eliminates the old “auto-cap alone lifts unlisted” path; no dangling references in this delta (import replaced by `workspace_subject` only).

**Independent catch:** `execute_and_verify` re-applies `leash_cap` and refuses to run `NOTIFY_ONLY`.  
**Fix:** none.

---

### F-03 / NON-FINDING — canonical-host confusion (auto-cap vs connect)
**Severity:** n/a (blocked; relies on paneled `canonical_host`)  
**Location:** `governance.py` (`emit_host = egress.canonical_host(...)` + `EGRESS_AUTO_PREFIX + emit_host`); `egress.py` `canonical_host` / `post`

**Attempted bypass:** case, trailing dot, `:443`, userinfo, IDNA, non-443 port, subdomain (`api.example` grant vs `evil.api.example` / `api.example.evil.com`).

**Why it does not break:** Same `canonical_host()` feeds capability key, auto grant, credential lookup, and connect host. Userinfo / bad port / non-https → `None` → deny or no auto. Exact string match on `net.post.auto:<canon>` — no subdomain widen.

**Independent catch:** `required_capability` / transport contract (PR #31).  
**Fix:** none.

---

### F-04 / NON-FINDING — F5 mutable / non-enforced session
**Severity:** n/a (blocked)  
**Location:** `governance.py` `enforced(session)` conjunct; `policycaps.py` `enforced` / `granted_capabilities`

**Attempted bypass:** `Session(capabilities=(..., "net.post.auto:api.example"), ...)` without signed `policy_caps`, `emit`/`govern_action` with host ATR.

**Why it does not break:** `auto` requires `enforced(session)`; unsigned path cannot lift. Caps under enforcement come from verified grant, not mutable `session.capabilities`.

**Fix:** none.

---

### F-05 / NON-FINDING — approved≠sent via held.args mutation / missing seal
**Severity:** n/a (blocked)  
**Location:** `loop.py` `approve` ~L118–175; `egress.py` `emission_seal` (length-prefixed framing + body type tag)

**Attempted bypass:** mutate `url`/`body`/`content_type` after hold; clear `seal`; NUL shift across fields; str↔bytes swap; multi-read proxy args.

**Why it does not break:**  
- Snapshot `args = dict(decision.args)` once; seal check + reauth + execute all use snapshot.  
- Egress+mutating: **missing seal → DENIED** (fail closed); mismatch → DENIED; **not consumed** on either.  
- Framing is length-prefixed; str vs bytes tagged (`\x00` / `\x01`).

**Independent catch:** reauth still sees snapshot destination.  
**Fix:** none (optional hardening below).

---

### F-06 / NON-FINDING — credential inject / non-consented host
**Severity:** n/a (blocked for stated goals)  
**Location:** `governance.py` `execute_and_verify` credential lookup; `tools.py` `_exec_net_post` / `_redact_credential`; tests `NetPostCredentialInjection`

**Attempted bypass:** model `auth` / `authorization` / headers in args; echo into `summary()`; cred for host B while posting to A.

**Why it does not break:** Auth only from `session.egress_credentials[canonical_host(url)]` after `net.post:<host>` gate; model args ignored; record is body/auth-free; response echo scrubbed (full header + bare token). Wrong host ⇒ different canon key ⇒ different/absent cred + separate allowlist entry.

**Residual (not guarantee-breaking):** redaction is literal substring — encoded/split echoes could survive (belt-and-suspenders). Exfil of operator secret **through a consented host** is an explicit non-goal.

**Fix:** none required for stated guarantees.

---

### F-07 / NON-FINDING — emit/approve raise vs fail-closed
**Severity:** n/a (blocked)  
**Location:** `egress.post` (lone surrogate / non-str / huge body / non-ASCII CT); `execute_and_verify` egress `try/except`; `emission_seal` `surrogatepass`

**Attempted bypass:** `\ud800` body, non-str body, huge body, non-ASCII `content_type`.

**Why it does not break:** `post` returns non-ok records; seam maps to `FAILED`; seal does not raise; govern path degrades rather than propagating.

**Fix:** none.

---

### F-08 / NON-FINDING — require-both weakens web_fetch / run_command
**Severity:** n/a  
**Location:** emission floor gated on `tool.egress and tool.mutating`

GET `web_fetch` and `run_command` never enter the emission auto-lift. Proposed `run_command`/egress floor and leash-cap behavior unchanged. Removing `signed_leash_cap` only affected the retired auto-lift branch.

---

### F-09 / LOW — shallow snapshot leaves mutable `bytearray` body shared
**Severity:** LOW  
**Location:** `collaborator/loop.py` `approve` — `args = dict(decision.args)` (~L128); `egress.emission_seal` treats `bytearray` as wire bytes

**Concrete input:**  
```python
body = bytearray(b'{"amt":1}')
held = emit(s, "https://api.example/v1/x", body)  # HELD, seal over contents
# single-threaded: mutate after seal check only possible concurrent/reentrant;
# more realistically: any code holding the same bytearray during approve after check
```
Shallow `dict(...)` keeps the same `bytearray` object through seal check and `execute_and_verify` → `post`. A concurrent mutator (or hostile `__getattribute__` on a custom mapping already partially addressed) could change bytes after the seal comparison.

**Why it’s not CRITICAL:** Normal host/`emit` bodies are `str`; single-threaded approve has no caller gap mid-function; in-place mutation **before** `approve` fails the seal. ADR 0002 single-trust-domain already limits in-process attackers.

**Another check:** none for mid-approve in-place byte mutation.  
**Fix:** freeze body at snapshot, e.g. `args["body"] = bytes(body) if isinstance(body, (bytes, bytearray)) else body`, and/or seal over `bytes(body)` consistently after copying.

---

### F-10 / LOW — doc drift: emit claims authority “not keyed on source”
**Severity:** LOW (correctness/docs, not exploit)  
**Location:** `loop.py` `emit` docstring vs `governance.py` floor `source == "host"`

Code **does** require `source=="host"` as a second barrier (intentional F-5 depth). Docstring still says authority is not keyed on `source` and only the keyword leash matters. Does not open a bypass; confuses future reviewers into deleting the conjunct.

**Fix:** align docstring with “keyword leash + `source=='host'` (emit-only), both non-model-reachable”.

---

### F-11 / LOW — cross-session bind is workspace-subject, not session/credential identity
**Severity:** LOW (design limit, partially in-scope for #5)  
**Location:** `governance.py` `origin_subject=_subject(session)`; `loop.py` approve compare

**Concrete:** Two `Session`s, **same** workspace path, different `egress_credentials`; hold under A, `approve` under B → subject matches → **RAN with B’s credential + A’s payload**.

**Why not HIGH:** Explicitly implemented as workspace subject (red-team #5 text); same resolved workspace is one trust/subject under ADR 0002. Not a cross-workspace credential theft.

**Fix (if desired):** bind a session nonce / creds fingerprint at hold, or inject cred identity into the seal side-channel check at approve.

---

## STEELMAN

The PR actually closes the production hole it claims to: autonomy is only reachable from `emit(autonomous=True)` with a **signed** session, **both** `net.post.auto:<canon>` and an explicit `net_post` ATR leash-cap, plus dual non-model signals (keyword leash + `source=="host"`). Unlisted net_post is consistently notify-only with a loud, operator-only hint (MINOR-A), seal/approve fail closed without consuming on mismatch, and credentials stay host-injected and off the model/parser path. Retiring `signed_leash_cap` removes the rejected auto-alone lift without touching GET/shell paths.

## VERDICT

**SOUND** (residual LOW hardening nits only — no guarantee-breaking bypass found on F1, require-both/MINOR-A, F5, host confusion, seal, or credential injection).

**Single highest-value fix:** freeze emission bodies at approve snapshot (`bytes(...)` copy for `bytes`/`bytearray`) so approved==sent does not rely on immutability of the held object graph.
