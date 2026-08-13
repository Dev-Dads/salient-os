# Surface code panel: qwen/qwen3-max

_cost=$0.0220 finish=stop_

# FINDINGS

## ID-01 / SINGLE-USE BOOTSTRAP RACE CONDITION / CRITICAL / surface.py:237-245 / 
Two concurrent GET requests with the same bootstrap token can both succeed, violating single-use semantics. Request sequence:
1. Thread A calls `_consume_bootstrap(candidate)` - acquires lock, checks `not _bootstrap_used` (true), proceeds to compare
2. Thread B calls `_consume_bootstrap(candidate)` - blocks on lock
3. Thread A completes comparison (true), sets `_bootstrap_used = True`, releases lock, returns True
4. Thread B acquires lock, but `_bootstrap_used` is now True, so returns False

However, if both threads check `not _bootstrap_used` before either sets it to True, both could pass the first check. The current code prevents this by holding the lock during the entire operation. **Actually, the lock correctly prevents the race** - the finding is invalid. The lock ensures atomicity of the entire consume operation.

## ID-02 / HOST HEADER VALIDATION BYPASS VIA IPV6 / HIGH / surface.py:258-259 /
The `_LOOPBACK` set includes `"::1"` but `_allowed_hosts` only constructs entries for `"127.0.0.1:{port}"` and `"localhost:{port}"`. A request with `Host: [::1]:{port}` would be rejected by `_host_ok()` even though it's a valid loopback address. However, this is not a security bypass but rather overly restrictive validation. The real issue is that IPv6 loopback binding is allowed in `_LOOPBACK` but the host validation doesn't account for IPv6 literal format `[::1]`.

Concrete request:
```
GET /state HTTP/1.1
Host: [::1]:12345
Cookie: sal_session=valid_session
X-Sal-Token: valid_csrf
```

This would be incorrectly rejected with 403 even though it's a legitimate loopback request. However, since the server likely isn't bound to IPv6 (only explicitly to 127.0.0.1 or localhost), this may not be reachable. Another layer (the actual socket binding) catches this by not listening on IPv6.

## ID-03 / ORIGIN VALIDATION INCONSISTENCY / MEDIUM / surface.py:261-264 /
The `_origin_ok` method allows requests with no Origin header (for non-browser clients) but requires exact origin match when present. However, the Origin header specification allows for "null" origin in certain cases (like file:// URLs or sandboxed iframes). The current implementation would reject `Origin: null` as not matching the expected origin.

Concrete request from a file:// page:
```
POST /submit HTTP/1.1
Origin: null
Cookie: sal_session=valid_session  
X-Sal-Token: valid_csrf
Content-Type: application/json
Content-Length: 15

{"text": "test"}
```

This would be rejected with 403, which is actually correct behavior since file:// pages shouldn't be able to interact with the service. No other layer catches this because it's working as intended.

## ID-04 / MISSING CONSTANT-TIME COMPARISON FOR HOST HEADER / LOW / surface.py:258 /
The `_host_ok` method uses regular string comparison `(host_header or "") in self._allowed_hosts` instead of constant-time comparison. While host headers aren't typically secret, in a DNS rebinding scenario, timing differences could theoretically leak information about the allowed hosts set.

However, since the allowed hosts are predictable (just the local port), this doesn't provide meaningful advantage to an attacker. Another layer (network isolation to loopback) catches any potential issues.

## ID-05 / POTENTIAL TOKEN LEAK VIA JAVASCRIPT ERROR STACK TRACES / LOW / surface.py:_PAGE_HTML /
The JavaScript error handling in the page uses generic try/catch without sanitizing error messages. If an error occurs during fetch operations, the error message might contain sensitive information. However, the current error handling only displays generic messages like "Could not reach Sal" and doesn't expose raw error details.

No actual token leak exists in the current implementation. Another layer (the fact that tokens are only used as headers/cookies and never appear in response bodies) prevents exposure.

# CERTIFICATION

C1 (door integrity): CERTIFIED. The dual CSRF protection (SameSite=Strict cookie + custom header), strict host header validation, origin pinning, single-use bootstrap token exchange, and loopback-only binding effectively prevent unauthorized access from other processes, tabs, or origins.

C2 (P-01 / no authority): CERTIFIED. The surface module imports nothing from governance/policycaps and only calls host.submit()/host.snapshot(). All request data is treated as untrusted input that can only become model directives, which are still governed by the existing authority boundary.

C3 (availability / can't be darkened): CERTIFIED. The pending-work 429 cap, 5-second body read timeout, 64 KiB body cap, bounded concurrency via semaphore, and per-request timeouts effectively prevent resource exhaustion attacks that could hide held actions or corrupt state.

C4 (scope honesty): CERTIFIED. The interface honestly represents the governance state, showing held tasks as "awaiting your approval — Stage C adds the button" and providing no control mechanisms beyond the single submit input, consistent with the Stage B/C split.

# STEELMAN

The implementation demonstrates exceptional attention to security detail, with multiple overlapping defenses for critical paths (CSRF protection, host validation, token handling). The architectural decision to keep the surface as a pure view layer with no authority capabilities maintains a clean separation of concerns that significantly reduces the attack surface.

The availability protections are thoughtfully designed to prevent denial-of-service scenarios that could compromise the safety properties of the governed agent system, recognizing that hiding a held action is a safety concern, not just a performance issue.

# VERDICT

SOUND. The implementation successfully addresses all design panel concerns and maintains the P-01 invariant while providing robust protection against the specified threat model. The single highest-value fix already implemented is the single-use bootstrap token exchange mechanism, which eliminates the durable bearer token in URL vulnerability identified by the design panel.
