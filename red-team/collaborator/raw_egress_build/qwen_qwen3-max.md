# Egress build code panel: qwen/qwen3-max

_finish=stop seconds=26.4 cost=$0.0257 usage={'prompt_tokens': 24795, 'completion_tokens': 1620, 'total_tokens': 26415, 'cost': 0.0256581, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0256581, 'upstream_inference_prompt_cost': 0.0193401, 'upstream_inference_completions_cost': 0.006318}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## FINDINGS

### ID-01 / AUTHORIZE-ONE / CONNECT-ANOTHER VIA TRAILING DOT
**SEVERITY**: CRITICAL  
**LOCATION**: `collaborator/egress.py:75` (`canonical_host`)  
**CONCRETE INPUT OR BYPASS**: `"https://example.com./"` (note trailing dot)  
**WHY IT BREAKS A GUARANTEE**: The capability key becomes `"net.get:example.com"` (trailing dot stripped), but the TLS `server_hostname` and Host header remain `"example.com."`. Many TLS stacks treat `"example.com."` and `"example.com"` as distinct, potentially connecting to a different host than authorized. This violates the "SAME canonical host" guarantee.  
**WHETHER ANOTHER CHECK CATCHES IT**: NO — the connection proceeds with the dotted name while authorization used the undotted name.  
**FIX**: Apply trailing-dot stripping AFTER IDNA encoding and use the stripped version for both capability key AND connection parameters.

### ID-02 / AUTHORIZE-ONE / CONNECT-ANOTHER VIA PORT NORMALIZATION EDGE CASE
**SEVERITY**: HIGH  
**LOCATION**: `collaborator/egress.py:68-70`  
**CONCRETE INPUT OR BYPASS**: `"https://example.com:443"` passes port check, but what if urlsplit returns port=None for default ports? Actually code handles this correctly by checking `parts.port != _HTTPS_PORT`. However, consider `"https://example.com:0443"` (octal). Python's urlsplit treats this as invalid port → ValueError → returns None → DENIED. So this is actually safe.  
**WHY IT BREAKS A GUARANTEE**: N/A - this path appears correctly handled.  
**WHETHER ANOTHER CHECK CATCHES IT**: N/A  
**FIX**: N/A

### ID-03 / IP SAFETY BYPASS VIA IPv4-MAPPED IPv6
**SEVERITY**: CRITICAL  
**LOCATION**: `collaborator/egress.py:120` (`is_safe_public_ip`)  
**CONCRETE INPUT OR BYPASS**: DNS returns `"::ffff:169.254.169.254"` (IPv4-mapped IPv6 format of metadata IP)  
**WHY IT BREAKS A GUARANTEE**: `ipaddress.ip_address("::ffff:169.254.169.254").is_private` returns `False` in Python, so this dangerous metadata IP would be considered safe and connected to, bypassing SSRF protection.  
**WHETHER ANOTHER CHECK CATCHES IT**: NO — the IP safety check incorrectly allows this.  
**FIX**: Normalize IPv4-mapped IPv6 addresses to their IPv4 equivalents before safety checking: `addr = addr.ipv4_mapped or addr`.

### ID-04 / REDIRECT HANDLING IN RESEARCH CONTEXT
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/research.py:143` (`_web_get_finding`)  
**CONCRETE INPUT OR BYPASS**: A 302 redirect to an allowlisted host that wasn't the original target  
**WHY IT BREAKS A GUARANTEE**: While main egress properly fails on redirects, the research path uses the same `egress.fetch` which correctly refuses redirects. So this is actually safe.  
**WHETHER ANOTHER CHECK CATCHES IT**: YES — redirects are properly refused in all paths.  
**FIX**: N/A

### ID-05 / CANONICALIZATION DIVERGENCE ON EMPTY LABELS
**SEVERITY**: LOW  
**LOCATION**: `collaborator/egress.py:85`  
**CONCRETE INPUT OR BYPASS**: `"https://a..b.com/"`  
**WHY IT BREAKS A GUARANTEE**: The check `if ".." in canon` catches this and returns None, so it's properly denied.  
**WHETHER ANOTHER CHECK CATCHES IT**: YES — properly denied as ineligible.  
**FIX**: N/A

### ID-06 / HOST HEADER vs SNI MISMATCH
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/egress.py:225-226`  
**CONCRETE INPUT OR BYPASS**: The code sets both `Host` header and uses `self.host` for SNI, which are the same canonical host, so this is consistent.  
**WHY IT BREAKS A GUARANTEE**: N/A - properly implemented.  
**WHETHER ANOTHER CHECK CATCHES IT**: N/A  
**FIX**: N/A

### ID-07 / EXFILTRATION VIA UNBOUNDED HEADERS
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/egress.py:223-226`  
**CONCRETE INPUT OR BYPASS**: Model cannot inject headers (only fixed headers are sent), so exfiltration via headers is prevented. Query length is capped.  
**WHY IT BREAKS A GUARANTEE**: N/A - properly bounded.  
**WHETHER ANOTHER CHECK CATCHES IT**: N/A  
**FIX**: N/A

### ID-08 / CAPABILITY DERIVATION TOCTOU
**SEVERITY**: LOW  
**LOCATION**: `collaborator/governance.py:306` (`reauthorized_or_denied`)  
**CONCRETE INPUT OR BYPASS**: The re-gate properly re-derives the capability from the frozen args, so TOCTOU is addressed.  
**WHY IT BREAKS A GUARANTEE**: N/A - properly implemented.  
**WHETHER ANOTHER CHECK CATCHES IT**: N/A  
**FIX**: N/A

### ID-09 / RESEARCH CONTENT INJECTION PATH
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/research.py:143`  
**CONCRETE INPUT OR BYPASS**: Research content is properly tagged UNTRUSTED and goes through normal proposal flow which requires approval for actions.  
**WHY IT BREAKS A GUARANTEE**: N/A - injection floors are properly implemented.  
**WHETHER ANOTHER CHECK CATCHES IT**: N/A  
**FIX**: N/A

### ID-10 / AUDIT-ONLY FLAG MISUSE
**SEVERITY**: LOW  
**LOCATION**: `collaborator/governance.py:228`  
**CONCRETE INPUT OR BYPASS**: The `offense_flag` is only used for audit tagging and never affects control flow.  
**WHY IT BREAKS A GUARANTEE**: N/A - properly audit-only.  
**WHETHER ANOTHER CHECK CATCHES IT**: N/A  
**FIX**: N/A

## STEELMAN

The implementation correctly enforces the core guarantees of ADR 0003: exact-match capabilities derived from canonicalized hosts, HTTPS-only with pinned IPs, redirect refusal, and proper separation between authorization and connection hosts in most cases. The governance integration properly derives capabilities per-request and re-checks at approval time. The research subsystem correctly treats web content as untrusted and maintains injection floors.

## VERDICT

**SERIOUS_FLAWS** — The IPv4-mapped IPv6 bypass (ID-03) allows SSRF to cloud metadata endpoints, and the trailing dot handling (ID-01) creates a potential authorize-one/connect-another vulnerability. **The single highest-value fix is to normalize IPv4-mapped IPv6 addresses in `is_safe_public_ip` to prevent SSRF to metadata services.**
