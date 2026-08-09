# Egress build code panel: anthropic/claude-opus-4.1

_finish=stop seconds=125.1 cost=$0.5781 usage={'prompt_tokens': 29288, 'completion_tokens': 1851, 'total_tokens': 31139, 'cost': 0.578145, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.578145, 'upstream_inference_prompt_cost': 0.43932, 'upstream_inference_completions_cost': 0.138825}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

I'll conduct a thorough adversarial review of this egress implementation, focusing on breaking the security guarantees.

## SECURITY FINDINGS

### 1. CRITICAL - IPv4-MAPPED IPv6 BYPASS IN is_safe_public_ip
**ID:** EGRESS-001  
**TITLE:** IPv4-mapped IPv6 addresses bypass private IP blocking  
**SEVERITY:** CRITICAL  
**LOCATION:** egress.py:117-124  
**CONCRETE INPUT:** URL `https://example.com/` where DNS resolves to `::ffff:169.254.169.254`  
**WHY IT BREAKS:** The `is_safe_public_ip()` function uses `ipaddress.ip_address()` which treats IPv4-mapped IPv6 addresses like `::ffff:169.254.169.254` as IPv6 addresses. The `.is_private` check on the IPv6 object returns False for the metadata endpoint, allowing SSRF to cloud metadata.  
**ANOTHER CHECK:** No other check catches this - the IP is pinned and connected to.  
**FIX:** Explicitly check for and reject IPv4-mapped IPv6 addresses, or extract and validate the embedded IPv4 address.

### 2. HIGH - UNICODE NORMALIZATION BYPASS IN canonical_host
**ID:** EGRESS-002  
**TITLE:** NFKC vs NFC normalization allows homograph attacks  
**SEVERITY:** HIGH  
**LOCATION:** egress.py:89  
**CONCRETE INPUT:** URL with Unicode character U+2044 (fraction slash): `https://example.com⁄evil.com/` normalizes differently under NFKC vs NFC  
**WHY IT BREAKS:** The code uses NFC normalization but IDNA encoding may use NFKC internally, creating a mismatch between the capability key and actual connection host. Some Unicode characters that look like slashes or dots could bypass host boundaries.  
**ANOTHER CHECK:** The IDNA encoding might catch some cases, but not all normalization mismatches.  
**FIX:** Use NFKC normalization consistently, or reject any URL containing characters that normalize differently under NFC vs NFKC.

### 3. HIGH - INCOMPLETE IPv6 ZONE ID HANDLING
**ID:** EGRESS-003  
**TITLE:** IPv6 zone identifiers can bypass host validation  
**SEVERITY:** HIGH  
**LOCATION:** egress.py:91-95  
**CONCRETE INPUT:** `https://[fe80::1%eth0]/` - link-local with zone ID  
**WHY IT BREAKS:** The code rejects IPv6 literals in brackets but doesn't handle zone identifiers (% suffix). While `urlsplit` may parse these, the validation could miss edge cases where the zone ID contains special characters.  
**ANOTHER CHECK:** The `_HOST_CHARS` check should catch this since `%` isn't allowed.  
**FIX:** Already caught by character validation.

### 4. MEDIUM - TOCTOU IN CAPABILITY RE-DERIVATION
**ID:** EGRESS-004  
**TITLE:** URL mutation between hold and approve could bypass checks  
**SEVERITY:** MEDIUM  
**LOCATION:** governance.py:306-320  
**CONCRETE INPUT:** Hold a decision with `https://EXAMPLE.COM/`, then if the args dict is mutable and changed to `https://evil.com/` before approval  
**WHY IT BREAKS:** The re-gate derives the capability from `args.get("url")` which comes from the held decision. If the args dict is mutable and modified between hold and approve, it could derive a different capability.  
**ANOTHER CHECK:** The args are frozen in the Decision dataclass, preventing mutation.  
**FIX:** Already protected by immutable Decision.

### 5. MEDIUM - DECIMAL/OCTAL IP FORMS NOT BLOCKED
**ID:** EGRESS-005  
**TITLE:** Alternative IP representations bypass canonical host validation  
**SEVERITY:** MEDIUM  
**LOCATION:** egress.py:73-99  
**CONCRETE INPUT:** `https://2852039166/` (decimal form of 169.254.169.254) or `https://0251.0376.0251.0376/` (octal)  
**WHY IT BREAKS:** These alternative IP forms pass through `urlsplit` as the hostname, but the canonical_host function doesn't recognize them as IP addresses. They could be allowlisted as "2852039166" but resolve to the metadata endpoint.  
**ANOTHER CHECK:** The `is_safe_public_ip` check on the resolved IP would catch this - it would resolve to 169.254.169.254 and be blocked.  
**FIX:** Non-finding due to IP safety check.

### 6. LOW - MISSING TIMEOUT ON DNS RESOLUTION
**ID:** EGRESS-006  
**TITLE:** DNS resolution has no timeout, enabling DoS  
**SEVERITY:** LOW  
**LOCATION:** egress.py:127-129  
**CONCRETE INPUT:** A domain with a slow/hanging DNS server  
**WHY IT BREAKS:** The `socket.getaddrinfo` call in `_resolve()` has no timeout parameter, so a malicious DNS server could hang the resolution indefinitely.  
**ANOTHER CHECK:** The connection timeout would eventually apply, but only after resolution completes.  
**FIX:** Add a timeout to the DNS resolution call.

### 7. LOW - QUERY STRING NOT INCLUDED IN CAPABILITY
**ID:** EGRESS-007  
**TITLE:** Query parameters could exfiltrate without being part of the capability  
**SEVERITY:** LOW  
**LOCATION:** egress.py:164-169  
**CONCRETE INPUT:** `https://example.com/?secret=data` where only `net.get:example.com` is checked  
**WHY IT BREAKS:** The capability is derived only from the host, not the full URL. While query length is capped and hashed for audit, the capability check doesn't prevent exfiltration to an allowlisted host.  
**ANOTHER CHECK:** This is explicitly acknowledged in the ADR as best-effort only.  
**FIX:** Documented limitation.

## VERIFICATION OF CLAIMED PROTECTIONS

### flag_offense_shape Audit-Only Verification
**CONFIRMED:** The function only returns a string tag at tools.py:77-91, never affects control flow. The governance code at governance.py:193 and elsewhere only records the tag in the Decision, never uses it for denial.

### Research web_get Default-Deny
**CONFIRMED:** The `_web_get_finding` at research.py:173-186 properly checks `granted_capabilities(session)` before allowing any fetch, implementing true default-deny.

### Redirect Protection
**CONFIRMED:** The fetch function at egress.py:203-207 properly returns an error on 3xx status codes with `max-redirects = 0` effectively implemented.

## STEELMAN

The implementation demonstrates strong security architecture with defense-in-depth: canonical parsing prevents host confusion attacks, IP pinning with safety checks blocks SSRF/rebind attacks, and the capability derivation ensures the authorized host matches the connected host. The structural default-deny approach using exact-match capabilities is more robust than pattern matching, and the re-gate at approval time properly handles TOCTOU concerns. The code is honest about its limitations (same-channel logging, GET exfiltration) rather than claiming false guarantees.

## VERDICT

**SERIOUS_FLAWS** - The IPv4-mapped IPv6 bypass is a critical SSRF vulnerability that completely defeats the metadata endpoint protection.

**Single highest-value fix:** Add explicit detection and rejection of IPv4-mapped IPv6 addresses in `is_safe_public_ip()`: check for the `::ffff:` prefix and either reject outright or extract and validate the embedded IPv4 address.
