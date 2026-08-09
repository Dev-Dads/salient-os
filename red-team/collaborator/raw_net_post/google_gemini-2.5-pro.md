# net.post Tier-2 code panel: google/gemini-2.5-pro

_finish=stop seconds=65.7 cost=$0.1201 usage={'prompt_tokens': 42175, 'completion_tokens': 6734, 'total_tokens': 48909, 'cost': 0.12005875, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.12005875, 'upstream_inference_prompt_cost': 0.05271875, 'upstream_inference_completions_cost': 0.06734}, 'completion_tokens_details': {'reasoning_tokens': 5659, 'image_tokens': 0, 'audio_tokens': 0}}_

This is an outstandingly well-designed and implemented security control. The author has clearly studied the history of egress bypasses and has systematically closed the most critical vulnerabilities at the design level, with an implementation that appears to rigorously follow the specification. The provided tests correctly pin the security-critical behaviors. My review found no high-severity flaws that break the stated guarantees.

### Findings

---
**ID**: EG-1
**TITLE**: Unbounded Header Value Length
**SEVERITY**: LOW
**LOCATION**: `collaborator/egress.py:456`, `collaborator/egress.py:462`

**CONCRETE INPUT OR BYPASS**:
The model provides a `content_type` argument that is extremely long but otherwise "clean" according to `_is_clean_header_value`.
```python
intent = ToolIntent("net_post", {
    "url": "https://api.example.com/submit",
    "body": "{}",
    "content_type": "application/json; " + ("A" * 20000)
})
```
The `_is_clean_header_value` check at `egress.py:284` will pass this value, as it contains no control characters. The `post` function will then attempt to send it as a header via `conn.putheader("Content-Type", ctype)` at `egress.py:481`.

**WHY IT BREAKS A GUARANTEE**:
This does not break a core guarantee of local data exfiltration or capability bypass. However, it violates the general principle of bounding all inputs sent over the network, a principle otherwise well-enforced for the URL target (`MAX_URL_TARGET`) and body (`MAX_POST_BODY`). An unbounded header value could be used to cause a denial-of-service against the remote server (if it has a small header size limit) or potentially trigger unexpected behavior in intermediate proxies. It represents a small, un-bounded data channel. The same applies to the `auth` value, though its source is trusted host config.

**WHETHER ANOTHER CHECK CATCHES IT**:
No local check catches this. The `http.client` library does not enforce a length limit. The remote server or an intermediary proxy might reject the request with a `4xx` error, but this is not guaranteed and relies on external enforcement.

**FIX**:
Add a length check to `_is_clean_header_value` or directly within the `post` function before calling `putheader`. A reasonable limit would be 1024 or 2048 bytes.

```python
# In collaborator/egress.py, inside post()
MAX_HEADER_LEN = 2048

# ...
ctype = str(content_type or DEFAULT_POST_CONTENT_TYPE)
if len(ctype) > MAX_HEADER_LEN or not _is_clean_header_value(ctype):
    return _refused(host, target_hash, request_bytes, "illegal or oversized content-type", ...)

if auth is not None and (len(str(auth)) > MAX_HEADER_LEN or not _is_clean_header_value(str(auth))):
    return _refused(host, target_hash, request_bytes, "illegal or oversized authorization value", ...)
```

---
### Steelman and Verdict

**STEELMAN**:
The design of this outbound emission system is state-of-the-art for a governed agent. It correctly identifies that the most critical failure modes for allowlists are not simple membership checks, but subtle bypasses in implementation. By building the entire system around a single, hardened canonicalization function (`canonical_host`) and using its output for both the capability key and the connection host, it structurally closes the entire class of authorize-one/connect-another attacks (userinfo, case, IDN, trailing-dot). Furthermore, it correctly fails closed on redirects (preventing credential/body re-sending), pins resolved IPs against a strict public-only safety check (closing DNS rebind and SSRF-to-metadata), and implements a robust TOCTOU gate (`reauthorized_or_denied`) that re-checks authority at the moment of use. The separation of `net.get` and `net.post` capabilities, the `propose_first` floor for all emissions, and the secure, host-only credential injection mechanism are all textbook examples of building security in, not bolting it on.

**VERDICT**: **SOUND**. The outbound emission module is exceptionally well-designed and implemented, correctly closing critical attack vectors like canonicalization bypasses, redirects, and SSRF, with no significant security flaws found.

The single highest-value fix is to add length caps to the `Content-Type` and `Authorization` headers to fully align with the system's "bounded-by-default" design principle, even though the current implementation does not pose a direct threat to the local system's integrity.
