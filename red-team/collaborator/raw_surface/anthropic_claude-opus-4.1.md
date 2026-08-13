# Surface code panel: anthropic/claude-opus-4.1

_cost=$0.4890 finish=stop_

# Security Review and Certification of surface.py

## FINDINGS

### Finding 1: CSRF Token Timing Side-Channel
**ID:** CSRF-001  
**TITLE:** CSRF Token Comparison Timing Side-Channel via Error Response Time  
**SEVERITY:** LOW  
**LOCATION:** surface.py:395 (_guard_authed)  
**CONCRETE REQUEST:** 
```
GET /state HTTP/1.1
Host: 127.0.0.1:PORT
Cookie: sal_session=VALID_SESSION
X-Sal-Token: A (try many values, measure 403 response time)
```
**WHY IT BREAKS A CLAIM:** While `_csrf_ok` uses `secrets.compare_digest`, the `_guard_authed` method checks session THEN csrf sequentially. An attacker with a valid session cookie could theoretically time the 403 response to distinguish between invalid session vs invalid CSRF token, potentially leaking CSRF token entropy through timing.  
**WHETHER ANOTHER LAYER CATCHES IT:** The attack requires a valid session cookie first (which requires the bootstrap token), making it impractical. The loopback-only binding prevents remote timing attacks.  
**FIX:** Check both session and CSRF in constant time regardless of which fails, or combine into a single comparison.

### Finding 2: Bootstrap Token Prefetch Race
**ID:** BOOT-001  
**TITLE:** Bootstrap Token Can Be Consumed by Local Prefetcher  
**SEVERITY:** LOW (explicitly documented as non-goal)  
**LOCATION:** surface.py:179 (_consume_bootstrap)  
**CONCRETE REQUEST:** A local browser prefetcher or terminal URL handler hitting the bootstrap URL before the human navigates to it.  
**WHY IT BREAKS A CLAIM:** The single-use bootstrap can be consumed by a prefetcher, requiring re-launch. This is explicitly documented in the code comments as a known trade-off.  
**WHETHER ANOTHER LAYER CATCHES IT:** N/A - documented behavior  
**FIX:** Already documented as acceptable trade-off for the security benefit of single-use tokens.

### Finding 3: Concurrent Bootstrap Consumption Race
**ID:** BOOT-002  
**TITLE:** Theoretical Race in Bootstrap Consumption Under Lock  
**SEVERITY:** LOW  
**LOCATION:** surface.py:179-186 (_consume_bootstrap)  
**CONCRETE REQUEST:** Two concurrent `GET /?k=VALID_TOKEN` requests arriving simultaneously  
**WHY IT BREAKS A CLAIM:** The lock properly prevents double-consumption, but both requests could theoretically get different session cookies if they both pass the bootstrap check before either sets the cookie. However, the lock prevents this - only one can consume the bootstrap.  
**WHETHER ANOTHER LAYER CATCHES IT:** The lock prevents the race correctly.  
**FIX:** None needed - the implementation is correct.

## NON-FINDINGS (Attacks That Don't Work)

### CSRF Protection - CERTIFIED SECURE
- The dual-wall CSRF protection (SameSite=Strict cookie + custom header) is properly implemented
- Both `/state` and `/submit` require the X-Sal-Token header (checked with compare_digest)
- No CORS headers are sent that would allow cross-origin requests with custom headers
- A cross-origin page cannot make a simple request that would bypass preflight due to the custom header requirement

### DNS Rebinding Protection - CERTIFIED SECURE  
- `_host_ok` (line 197) performs exact string matching against the allowlist
- The allowlist is frozen at construction time with only `127.0.0.1:PORT` and `localhost:PORT`
- No wildcard matching or substring checks that could be exploited
- Missing Host header is properly rejected (empty string not in allowlist)

### Authority Boundary (P-01) - CERTIFIED SECURE
- surface.py imports ONLY task state constants from host.py (line 73-74)
- No imports from governance/policycaps (verified in test_p01_no_governance_import)
- `/submit` only calls `host.submit(text)` - the text becomes a model directive still governed by govern_action
- No HTTP path can grant capabilities, change leashes, or set autonomous flags

### Availability Protection - CERTIFIED SECURE
- Pending work cap (line 494-498) properly counts non-terminal tasks and returns 429
- Body read timeout (5 seconds, line 517) prevents slowloris on POST body
- BoundedThreadingHTTPServer (line 87-109) drops connections when saturated rather than blocking
- Content-Length cap (64KB) enforced before reading (line 509-513)

## CERTIFICATION

**C1 (door integrity):** CERTIFIED - The authentication scheme with single-use bootstrap, HttpOnly SameSite=Strict session cookie, dual CSRF walls, Host header allowlist, and Origin checking successfully prevents unauthorized access. No concrete bypass found.

**C2 (P-01 / no authority):** CERTIFIED - The surface only calls host.submit() and host.snapshot(). No imports from governance/policycaps. A hostile submit body can only become a model directive that still flows through govern_action.

**C3 (availability):** CERTIFIED - The pending work cap (429), body read timeout (5s), connection cap with non-blocking drop, and Content-Length validation prevent the surface from being darkened or hiding held actions.

**C4 (scope honesty):** CERTIFIED - The surface exposes only submit() for control. Held/paused tasks are shown honestly as awaiting Stage C. No hidden levers or misrepresentation of governance state.

## STEELMAN

The implementation demonstrates exceptional security engineering with defense-in-depth throughout. The single-use bootstrap pattern elegantly solves the bearer token persistence problem, while the dual CSRF walls provide redundant protection. The strict CSP with nonce-based inline scripts and textContent-only rendering eliminates XSS even if other defenses fail. The availability bounds are thoughtfully designed to prevent a darkened surface from hiding safety-critical held actions.

## VERDICT

**SOUND** - The surface.py implementation successfully achieves all four certification claims with multiple independent defensive layers. The single highest-value fix would be to add a comment explicitly documenting why the sequential session-then-CSRF checking in `_guard_authed` is acceptable given the threat model (local-only, requires bootstrap token first).
