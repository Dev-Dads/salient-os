# ADR 0004 design panel: anthropic/claude-opus-4.1

_finish=stop seconds=152.6 cost=$0.7453 usage={'prompt_tokens': 41209, 'completion_tokens': 1695, 'total_tokens': 42904, 'cost': 0.74526, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.74526, 'upstream_inference_prompt_cost': 0.618135, 'upstream_inference_completions_cost': 0.127125}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## Adversarial Review of ADR 0004

### F1: LAUNDERING VIA INDIRECT ANCHOR MUTATION
**ID:** F1 / **TITLE:** Model reaches anchors via run_command / **SEVERITY:** CRITICAL  
**AREA:** trust-root  
**SCENARIO:** The ADR claims "nothing reachable from run_turn touches anchors" but `session.offense_trust_anchors` is a mutable dict. A `run_command` that writes a config file parsed by the host, or a tool that mutates `session.__dict__` directly, could inject anchors. The "operator provisions anchors" claim assumes ONE provisioning moment, but if anchors are re-read from config or mutated at runtime, the model influences the trust set.  
**WHY IT BREAKS:** If the model can add its own key to the anchor set, it mints offense:<target> capabilities, violating "rooted in a key the operator does not hold."  
**ADR SCOPES IT:** No - states anchors are "host-provisioned" but doesn't address runtime mutation.  
**FIX:** Make `offense_trust_anchors` immutable at Session construction (frozendict/tuple). Never re-read from disk after init.

### F2: CANONICAL_HOST DIVERGENCE
**ID:** F2 / **TITLE:** Artifact target vs connect host mismatch / **SEVERITY:** HIGH  
**AREA:** crypto/artifact  
**SCENARIO:** The artifact's `target` field uses `egress.canonical_host` form, but there's no guarantee the probe executor canonicalizes identically. If `probe_target` uses a different parser or the canonicalization changes between artifact-verify and probe-execute, you could authorize `evil.com` but probe `evil.com.internal`.  
**WHY IT BREAKS:** The "exact target" guarantee fails if canonicalization isn't byte-identical between verify and execute paths.  
**ADR SCOPES IT:** Claims "ONE canonicalizer" but doesn't show probe_target using the same function.  
**FIX:** The probe executor MUST call `egress.canonical_host(artifact.target)` and connect to EXACTLY that string.

### F3: NONCE WITHOUT STATE = REPLAY
**ID:** F3 / **TITLE:** Nonce present but never checked / **SEVERITY:** HIGH  
**AREA:** crypto/artifact  
**SCENARIO:** The artifact has a `nonce` field but `verify_scope_artifact` never checks it against anything. An artifact for `target1.com` valid today can be replayed tomorrow, or used multiple times in one session. The "bounded engagement window" doesn't prevent replay WITHIN the window.  
**WHY IT BREAKS:** Without nonce-tracking, the same artifact authorizes unlimited probes within its window.  
**ADR SCOPES IT:** No - includes nonce in the format but no replay prevention.  
**FIX:** Track used nonces per session; reject reuse.

### F4: OFFENSE CAPABILITY BYPASS VIA HMAC
**ID:** F4 / **TITLE:** offense:target mintable via PolicyCaps / **SEVERITY:** MEDIUM  
**AREA:** gate/P-01  
**SCENARIO:** The ADR claims `offense:<target>` is "never grantable from PolicyCaps" but `granted_capabilities()` returns whatever's in the signed caps. If an operator accidentally includes `"offense:evil.com"` in their PolicyCaps, it's granted without any artifact.  
**WHY IT BREAKS:** The "structurally unmintable" claim requires the HMAC path to actively reject offense:* capabilities, not just "don't mint them."  
**ADR SCOPES IT:** No - assumes operators won't mint these, doesn't enforce it.  
**FIX:** `granted_capabilities()` must filter out any `offense:*` capability - these come ONLY from artifacts.

### F5: PROBE AS SCANNER PRIMITIVE
**ID:** F5 / **TITLE:** Loop probe_target for full scan / **SEVERITY:** MEDIUM  
**AREA:** dual-use probe  
**SCENARIO:** Call `probe_target` in a loop over ports 1-65535, or over a /24 subnet. Each call is "one bounded TCP connect" but 65k calls is a port scan. The "minimal" claim is per-invocation, not per-session.  
**WHY IT BREAKS:** "Minimal offense action" becomes full scanner when composed.  
**ADR SCOPES IT:** Acknowledges "loop it over ports/hosts" but doesn't prevent it.  
**FIX:** Rate-limit or quota probe attempts per artifact/session.

### F6: MISSING REVOCATION
**ID:** F6 / **TITLE:** No revocation before not_after / **SEVERITY:** MEDIUM  
**AREA:** missing  
**SCENARIO:** A 30-day artifact is compromised on day 2. No mechanism exists to revoke it before expiry. The "short windows substitute" claim assumes engagements are hours, not weeks.  
**WHY IT BREAKS:** Real pentests run weeks; a compromised artifact remains valid the whole time.  
**ADR SCOPES IT:** Yes - explicitly deferred as revisit trigger #3.  
**FIX:** Add revocation list checked in verify_scope_artifact.

### F7: AUTHORITY_ID CONFUSION
**ID:** F7 / **TITLE:** No authority_id uniqueness / **SEVERITY:** LOW  
**AREA:** crypto/artifact  
**SCENARIO:** Two different authorities could use the same `authority_id` string. The operator thinks they're trusting "Acme Pentest Inc" but a different key with the same ID gets accepted.  
**WHY IT BREAKS:** Trust anchor selection becomes ambiguous.  
**ADR SCOPES IT:** No - no uniqueness requirement for authority_id.  
**FIX:** Include key fingerprint in authority_id or enforce globally unique IDs.

## Summary

**LAUNDERING JUDGMENT:** The design achieves "rooted in a key the operator does not hold" ONLY if anchors are immutable after construction. The admitted residual (operator adds their own key) is honestly stated but Finding F1 shows ANOTHER laundering path the ADR misses - runtime anchor mutation. Stating the self-signing residual is necessary but not sufficient while F1 remains.

**MISSING:** 
- How probe_target actually canonicalizes its target (F2)
- Replay prevention mechanism (F3)  
- Active rejection of offense:* in PolicyCaps (F4)
- Rate limiting for probe attempts (F5)

**STEELMAN:** The design correctly identifies that asymmetric crypto with external public keys is the only way to achieve Tier 3's invariant. The scope artifact format is sound, the gate placement is correct (derive-verify-reverify pattern), and honestly admitting the single-node residual rather than claiming false guarantees shows integrity.

**VERDICT:** SERIOUS_FLAWS + The design's core insight is correct but F1 (model-reachable anchor mutation) breaks the fundamental claim.

**Single highest-value change:** Make offense_trust_anchors immutable at Session construction and prove no runtime path can modify it.
