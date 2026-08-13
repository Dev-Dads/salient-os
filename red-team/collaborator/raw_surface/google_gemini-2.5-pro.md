# Surface code panel: google/gemini-2.5-pro

_cost=$0.0884 finish=stop_

Excellent. As a senior application security engineer, my review will be maximally adversarial. I will assume every claim is false until proven true by the code itself. My findings will be concrete and actionable.

***

### Adversarial Code Review & Certification: `collaborator/surface.py`

### FINDINGS

---
**ID:** S-1
**TITLE:** Resource Exhaustion via Maliciously Large Cookie Header
**SEVERITY:** MEDIUM
**LOCATION:** `surface.py:304` (`_cookie_session`)

**CONCRETE REQUEST OR SEQUENCE:**
An attacker on the local machine (who does not need the session token) can pin a handler thread by sending a request with a very large `Cookie` header.

```http
GET / HTTP/1.1
Host: 127.0.0.1:<port>
Cookie: a=... (64KB of cookie data) ...
```

**WHY IT BREAKS A CLAIM:**
This attack targets **C3 (availability)**. The `_cookie_session` method reads the entire `Cookie` header and passes it to `http.cookies.SimpleCookie().load(raw)`. This parsing operation is not free; a sufficiently large and complex header can consume significant CPU and memory, pinning one of the handler threads from the bounded semaphore pool (`_conn_sem`) for the full `DEFAULT_REQUEST_TIMEOUT` of 15 seconds. An attacker can repeat this request `DEFAULT_MAX_CONNECTIONS` (16) times to saturate all available handler threads, effectively "darkening the surface" by making it unable to serve legitimate `/state` or `/submit` requests. This breaks the claim that a client "cannot... pin all slots indefinitely." While the slots are not pinned forever, they can be held for the request timeout, leading to a denial of service.

**WHETHER ANOTHER LAYER CATCHES IT:**
No. The `Content-Length` checks apply to the request *body*, not headers. The `BaseHTTPRequestHandler` has default header size limits, but they are typically large (e.g., 64KiB) and would not prevent this attack. The semaphore that is meant to protect against connection exhaustion is what is being exhausted here.

**FIX:**
Enforce a reasonable size limit on the `Cookie` header *before* parsing it.

```python
# In _Handler._cookie_session
MAX_COOKIE_HEADER_LEN = 4096  # A generous but safe limit
raw = self.headers.get("Cookie")
if not raw:
    return ""
if len(raw) > MAX_COOKIE_HEADER_LEN:
    # Log the attempt, but don't parse the potentially malicious header.
    return ""
try:
    # ... rest of the function
```

---
**ID:** S-2
**TITLE:** Inconsistent Defense-in-Depth: Origin Header Not Checked on /state Route
**SEVERITY:** LOW
**LOCATION:** `surface.py:339` (`_route_state`)

**CONCRETE REQUEST OR SEQUENCE:**
This is not a direct exploit but a missing defense layer. A non-browser client on the same machine that has stolen the session cookie and CSRF token could make a request to `/state` while spoofing a foreign `Origin` header, and it would be allowed.

```http
GET /state HTTP/1.1
Host: 127.0.0.1:<port>
Cookie: sal_session=<stolen_session_token>
X-Sal-Token: <stolen_csrf_token>
Origin: http://evil.com
```

**WHY IT BREAKS A CLAIM:**
This is a minor inconsistency that weakens the defense-in-depth posture of **C1 (door integrity)**. The design document claims an "Origin pin on state-changing requests," which is implemented for `/submit`. However, `/state` is a state-*reading* request, and the same logic should apply for consistency. While the primary CSRF defense (the custom `X-Sal-Token` header) correctly blocks browser-based cross-origin attacks, failing to check `Origin` on `/state` removes a redundant safeguard. If a novel browser bug or misconfiguration were to allow a custom header to be sent cross-origin without a preflight, this check would be the last line of defense. The principle of least privilege suggests all authenticated endpoints should be maximally strict.

**WHETHER ANOTHER LAYER CATCHES IT:**
Yes. The primary CSRF defense (`X-Sal-Token` custom header requirement) prevents any practical cross-origin browser attack today. This finding points out the lack of a secondary defense.

**FIX:**
Apply the `Origin` check to the `/state` route for consistency.

```python
# In _Handler._route_state
def _route_state(self) -> None:
    # Add check_origin=True
    if not self._guard_authed(check_origin=True):
        return
    # ... rest of the function
```

---
### CERTIFICATION

*   **C1 (door integrity): NOT-CERTIFIED.** While the CSRF and DNS rebinding defenses are sound, the door can be made unavailable to legitimate users via the resource exhaustion attack in S-1, which constitutes a break in the door's overall integrity.
*   **C2 (P-01 / no authority): CERTIFIED.** The code correctly isolates the surface from all authority-granting logic, importing nothing from `governance` and calling only the intended `host.submit()` and `host.snapshot()` methods. A hostile submit body cannot bypass the governance seam.
*   **C3 (availability / can't be darkened): NOT-CERTIFIED.** The resource exhaustion vulnerability (S-1) provides a direct method for an attacker to saturate all handler threads and "darken the surface," breaking the claim that slots cannot be pinned.
*   **C4 (scope honesty): CERTIFIED.** The web page honestly represents its watch-only nature for Stage B, correctly noting that controls for held tasks are part of a future stage. It does not misrepresent governance state or offer levers it shouldn't have.

### STEELMAN & VERDICT

**STEELMAN:**
The author has constructed a remarkably robust localhost door, demonstrating a deep understanding of modern web security. The primary CSRF threat is neutralized by the state-of-the-art combination of a `SameSite=Strict` cookie and a required custom header, which is correctly implemented by rejecting preflight `OPTIONS` requests. The design successfully closes the specific gaps identified by the pre-design panel, such as token-in-URL leaks (via the single-use bootstrap) and unbounded queue growth (via the 429 cap), and uses constant-time comparisons for all secret material. The availability defenses against slowloris and large bodies are thoughtful and well-implemented, particularly the tight body-read timeout.

**VERDICT: MINOR_ISSUES.**
The core security architecture is sound and defends well against the most common and severe web attack vectors like CSRF and XSS. However, a medium-severity availability vulnerability exists (S-1) that allows a local attacker to deny service by exhausting the connection pool with malicious headers. While the primary claims around authority (C2) and honesty (C4) hold, the integrity and availability claims (C1, C3) are broken by this single, fixable flaw.

The single highest-value fix is to **add a size limit to the `Cookie` header before parsing in `_cookie_session`** to mitigate the resource exhaustion DoS (S-1).
