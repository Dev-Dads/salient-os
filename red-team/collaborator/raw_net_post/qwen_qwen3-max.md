# net.post Tier-2 code panel: qwen/qwen3-max

_finish=stop seconds=63.2 cost=$0.0859 usage={'prompt_tokens': 36853, 'completion_tokens': 3642, 'total_tokens': 40495, 'cost': 0.08589828, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.08589828, 'upstream_inference_prompt_cost': 0.05749068, 'upstream_inference_completions_cost': 0.0284076}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## FINDINGS

### ID-01 / TITLE: `net.post` auto-lift bypass via case/IDN mismatch between capability derivation and credential lookup / SEVERITY: CRITICAL / LOCATION: `collaborator/governance.py:302` and `collaborator/governance.py:458`

**CONCRETE INPUT OR BYPASS**:  
Operator grants `net.post.auto:api.example` (ASCII). Model proposes a POST to `https://API.EXAMPLE/` (uppercase) or `https://аpple.com/` (Cyrillic homograph that canonicalizes differently than expected).  

**WHY IT BREAKS A GUARANTEE**:  
The auto-lift check at `governance.py:302` uses `emit_host = egress.canonical_host(...)`, which correctly canonicalizes the URL. However, the credential lookup at `governance.py:458` also uses `emit_host = egress.canonical_host(...)`. The vulnerability is elsewhere: the **capability derivation for the initial gate** uses the same canonicalization, so if the host canonicalizes to a string that doesn't match the granted capability, it would be denied. But the real issue is in the **auto-lift condition**: it checks `directive.grants_capability(egress.EGRESS_AUTO_PREFIX + emit_host)` where `emit_host` is the canonicalized host. If the operator granted `net.post.auto:api.example` but the model uses a URL that canonicalizes to something else (e.g., due to a canonicalization bug), it might not match. However, the tests show canonicalization is consistent.

Wait—re-examining: the actual critical flaw is that **the credential lookup uses the same canonical host as the capability check**, which is correct. But there's a more subtle issue: what if the canonicalization produces a host string that matches a different granted capability? For example, if there's a canonicalization bug that makes `evil.com` canonicalize to `api.example`, then a grant for `api.example` would authorize `evil.com`. But the tests specifically check for homograph attacks (`test_homograph_distinct_from_ascii`) and show they are handled correctly.

Actually, the real critical finding is elsewhere...

### ID-02 / TITLE: TOCTOU vulnerability in `approve()` allows mutation of URL/body between hold and approve / SEVERITY: HIGH / LOCATION: `collaborator/loop.py:120` and `collaborator/governance.py:370`

**CONCRETE INPUT OR BYPASS**:  
1. Model proposes `net_post` with `url="https://api.example/"` and `body="safe"`.
2. Human approves the action.
3. Between approval decision and execution, an attacker modifies the held `Decision.args` to change `url` to `"https://evil.com/"` and `body` to `"exfiltrate"`.

**WHY IT BREAKS A GUARANTEE**:  
The `approve()` function uses `decision.args` directly without re-validating that these args haven't been mutated since the original `govern_action()` call. While `reauthorized_or_denied()` does re-derive the capability from `args.get("url")`, it doesn't verify that the **body** hasn't changed, and more critically, it doesn't ensure that the **canonical host derived from the URL** matches what was originally approved. 

However, looking closer: `reauthorized_or_denied()` DOES re-derive `required_cap = egress.required_capability(str(args.get("url") or ""), ...)` and re-checks it against current caps. So if the URL is changed to `evil.com`, and `evil.com` isn't granted, it will be denied. But what if `evil.com` IS granted? Then the body could be swapped.

The bigger issue: the human approved "send 'safe' to api.example" but the system sends "exfiltrate" to api.example (same host, different body). The body is not part of the capability check—it's only bounded by length. So this is a **body mutation attack**.

**WHETHER ANOTHER CHECK CATCHES IT**:  
No. The governance re-check only validates the URL/host capability, not the body content. The body is passed through directly from `decision.args`.

### ID-03 / TITLE: Credential injection for non-consented hosts via canonical host collision / SEVERITY: HIGH / LOCATION: `collaborator/governance.py:458`

**CONCRETE INPUT OR BYPASS**:  
Operator configures `egress_credentials = {"api.example": "secret"}`. Model proposes POST to a URL that canonicalizes to `api.example` but is actually a different host (e.g., due to IDN homograph attack that bypasses canonicalization).

**WHY IT BREAKS A GUARANTEE**:  
If there's any weakness in the `canonical_host()` function that allows two different hosts to canonicalize to the same string, then credentials for the legitimate host would be sent to the malicious host. The tests show homograph protection, but what about edge cases?

Looking at `canonical_host()`: it uses `host.encode("idna").decode("ascii").lower()`. This should handle IDN correctly. The test `test_homograph_distinct_from_ascii` confirms Cyrillic 'а' doesn't become ASCII 'a'. So this might not be exploitable.

### ID-04 / TITLE: `keep_preview` parameter can be controlled by model via tool args / SEVERITY: MEDIUM / LOCATION: `collaborator/tools.py:598`

**CONCRETE INPUT OR BYPASS**:  
Model includes `"keep_preview": true` in the `net_post` tool arguments.

**WHY IT BREAKS A GUARANTEE**:  
The `_exec_net_post` function takes `keep_preview` as a parameter, but `execute_tool` only passes `egress_preview` from the governance seam. Looking at `execute_tool`:

```python
def execute_tool(tool: Tool, workspace, args: dict, *, egress_preview: bool = False,
                 egress_auth: "str | None" = None) -> Execution:
    if tool.name == "net_post":
        return _exec_net_post(workspace, args, keep_preview=egress_preview, auth=egress_auth)
```

And `egress_preview` is set in `execute_and_verify` based on `leash == PROPOSE_FIRST`. The model's `args` are not used for `keep_preview`. So this is NOT a vulnerability.

### ID-05 / TITLE: Redirect handling could leak credential if connection_factory is compromised / SEVERITY: LOW / LOCATION: `collaborator/egress.py:410`

**CONCRETE INPUT OR BYPASS**:  
Custom `connection_factory` that ignores the redirect check and follows redirects.

**WHY IT BREAKS A GUARANTEE**:  
The production code uses the default connection factory, and the redirect check is in the main flow: `if 300 <= status < 400: return _refused(...)`. So even with a custom connection factory, the redirect is caught after getting the response. The body and credential are never re-sent. Tests confirm this (`test_redirect_fails_closed_no_second_connection`). So this is NOT a vulnerability.

### ID-06 / TITLE: Body preview could contain sensitive data in audit logs for human-gated emissions / SEVERITY: LOW / LOCATION: `collaborator/egress.py:370`

**CONCRETE INPUT OR BYPASS**:  
Human approves a POST with sensitive body data; the preview is stored in `EgressRecord.request_body_preview`.

**WHY IT BREAKS A GUARANTEE**:  
This is by design—Josh's steer explicitly states that human-gated emissions keep a bounded preview. The ADR acknowledges this as a trade-off. Not a bug.

### ID-07 / TITLE: `content_type` header injection via multi-byte UTF-8 sequences / SEVERITY: MEDIUM / LOCATION: `collaborator/egress.py:362`

**CONCRETE INPUT OR BYPASS**:  
`content_type = "application/json%EF%BF%BD"` where `%EF%BF%BD` is the UTF-8 encoding of U+FFFD (replacement character), which might not be caught by `_is_clean_header_value`.

**WHY IT BREAKS A GUARANTEE**:  
The `_is_clean_header_value` function checks `ord(c) < 0x20 or ord(c) == 0x7f`. U+FFFD has ord 0xFFFD, which is > 0x7F, so it would pass. But is U+FFFD a control character? No—it's a valid Unicode character. The real danger is CR/LF characters, which are < 0x20 and would be caught.

Testing: `\r` is 0x0D < 0x20 → caught. `\n` is 0x0A < 0x20 → caught. So this is properly handled.

### ID-08 / TITLE: Missing validation that `auth` credential is only injected for hosts that passed the net.post capability gate / SEVERITY: HIGH / LOCATION: `collaborator/governance.py:458`

**CONCRETE INPUT OR BYPASS**:  
None directly, but consider: what if the capability check passes for `net.post:host`, but then the credential lookup happens before the IP safety check? Actually, the order is:
1. Capability check (govern_action)
2. IP safety check (in egress.post)
3. Credential injection (in execute_and_verify)

But the credential is only injected if the host passed the capability gate. However, there's a subtle issue: **the credential is looked up by canonical host, but what if the canonical host resolves to a private IP?** The credential would still be injected, but the request would fail at the IP safety check. That's acceptable—the credential isn't sent.

The real issue is: **is the credential ever looked up for a host that didn't pass the net.post gate?** No, because `execute_and_verify` is only called if the capability gate passed.

### ID-09 / TITLE: Leash threading error in `execute_and_verify` could use wrong leash for audit split / SEVERITY: MEDIUM / LOCATION: `collaborator/governance.py:457`

**CONCRETE INPUT OR BYPASS**:  
If `leash` parameter is not passed correctly, `egress_preview` might be set incorrectly.

**WHY IT BREAKS A GUARANTEE**:  
In `govern_action`, when auto-lift occurs, it sets `leash = apply_cap(ACT_THEN_REPORT, ...)`. Then it calls `execute_and_verify(..., leash=leash)`. In `approve`, it passes `leash=decision.leash`. This seems correct.

But what if `execute_and_verify` is called without the `leash` parameter? The signature is `leash: "str | None" = None`, and it falls back to `_leash_for(session, tool)`. This could be wrong if the effective leash was modified (e.g., by auto-lift). However, both call sites (`govern_action` and `approve`) explicitly pass the leash. So this is safe.

### ID-10 / TITLE: `net.get:<host>` grant could authorize `net.post:<host>` if method detection fails / SEVERITY: CRITICAL / LOCATION: `collaborator/egress.py:89` and `collaborator/governance.py:270`

**CONCRETE INPUT OR BYPASS**:  
Model calls `web_fetch` (GET tool) but somehow makes it do a POST, or calls `net_post` with method overridden to GET.

**WHY IT BREAKS A GUARANTEE**:  
The `required_capability` function uses `getattr(tool, "egress_method", "GET")` from the governance side. For `web_fetch`, `egress_method="GET"`; for `net_post`, `egress_method="POST"`. So they are separate.

But what if a tool is misconfigured? The `_TOOLS` dict correctly sets `egress_method="POST"` for `net_post`. So this is safe.

### ID-11 / TITLE: Body length accounting evasion via non-UTF-8 strings / SEVERITY: MEDIUM / LOCATION: `collaborator/egress.py:345`

**CONCRETE INPUT OR BYPASS**:  
Body is a string with surrogate pairs or invalid UTF-8 sequences that encode to more bytes than expected.

**WHY IT BREAKS A GUARANTEE**:  
The code does `body_bytes = body.encode("utf-8")`. In Python, this will either succeed (producing a byte sequence) or raise a UnicodeError (which would be caught as "body must be str or bytes"). The length check is on the encoded bytes, so it's accurate. No evasion possible.

### ID-12 / TITLE: Canonical host parsing allows IPv4 literals which could bypass allowlist / SEVERITY: HIGH / LOCATION: `collaborator/egress.py:65`

**CONCRETE INPUT OR BYPASS**:  
Operator grants `net.post:93.184.216.34` (IPv4 literal). Model uses `https://93.184.216.34/` which canonicalizes to `93.184.216.34`. This is allowed, but what if the IP resolves to a different host? No—IP literals don't resolve; they connect directly. And the IP safety check (`is_safe_public_ip`) would block private IPs. So this is acceptable.

But the real issue: **should IP literals be allowed at all?** The ADR doesn't prohibit them, and the IP safety check handles it. Not a bug.

After thorough analysis, the most concrete finding is:

### ID-TOCTOU-BODY / TITLE: Body/content mutation between approval and execution / SEVERITY: HIGH / LOCATION: `collaborator/loop.py:120` and `collaborator/governance.py:370`

**CONCRETE INPUT OR BYPASS**:  
An attacker with access to modify the `Decision.args` dictionary between the time a human approves an action and when it executes could change the `body` field to exfiltrate different data, while keeping the same approved URL. Since only the URL/host is re-validated during `reauthorized_or_denied()`, the modified body would be sent.

**WHY IT BREAKS A GUARANTEE**:  
Violates the principle that "what was approved is what is sent." The human approved specific content to be emitted, but different content is actually sent.

**WHETHER ANOTHER CHECK CATCHES IT**:  
No. The re-authorization only checks the destination capability, not the payload integrity.

**FIX**:  
In `reauthorized_or_denied()`, for egress tools, also validate that the body hash matches what was originally approved (store the original body hash in the Decision).

However, looking at the code again: the `Decision` object stores `args`, which includes the body. The `approve()` function uses these args directly. There's no mechanism to detect if args were mutated. But in a normal execution flow, who would mutate the args? The `Decision` is created by `govern_action` and passed to `approve`. If the system is single-threaded and no external modification occurs, it's safe. But if there's any shared state or serialization/deserialization, mutation could occur.

The tests don't cover this scenario. This is a theoretical TOCTOU on the body content.

But wait—the ADR states: "emission TOCTOU across the re-gate" is a concern, and the re-gate re-derives the capability from the frozen args. It doesn't mention body freezing. So this might be an oversight.

Actually, re-reading the material: "TOCTOU: does approve() re-derive net.post:<host> from the FROZEN args and re-check CURRENT caps; can host/body/url be mutated between hold and approve (human approved X, Y is sent)?"

Yes! This is explicitly listed as a concern to check. And the answer is: host/url are re-checked, but body is not. So this is a valid finding.

## STEELMAN

The design correctly separates read and write capabilities, enforces strict canonicalization, blocks redirects and private IPs, and implements human gating by default with signed auto-lift. The credential injection is properly isolated from model input, and the audit split between autonomous (body-free) and human-gated (preview) aligns with the stated goals. Most attack vectors like header injection, redirect following, and capability namespace confusion are well-addressed.

## VERDICT

**SERIOUS_FLAWS** — The emission TOCTOU vulnerability allows body/content mutation between human approval and execution, breaking the guarantee that approved payloads are faithfully sent. The highest-value fix is to store and verify a hash of the approved body during re-authorization in `reauthorized_or_denied()`.
