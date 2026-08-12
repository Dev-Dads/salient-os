# emission-quota code panel: qwen/qwen3-max

_finish=stop seconds=27.2 cost=$0.0297 usage={'prompt_tokens': 29699, 'completion_tokens': 1682, 'total_tokens': 31381, 'cost': 0.02972502, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.02972502, 'upstream_inference_prompt_cost': 0.02316522, 'upstream_inference_completions_cost': 0.0065598}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

### 1. QUOTA BYPASS: Non-canonical host key mismatch
**ID**: F1  
**TITLE**: Host quota dict keys must match canonical_host output or cap silently never applies  
**SEVERITY**: HIGH  
**LOCATION**: collaborator/session.py: `_emission_limit` method (lines implied by logic)  
**CONCRETE CASE**: Operator configures `emission_quota={"API.Example": 1}` but `egress.canonical_host("https://api.example/x")` returns `"api.example"`. The quota check in `_emission_limit` does a plain dict lookup with the canonical host, so it returns `None` (unlimited) instead of applying the cap.  
**WHY IT BYPASSES**: The quota dictionary uses operator-provided host strings as keys, but emissions are checked against canonicalized hosts. If the operator doesn't use the exact canonical form, the quota is silently ignored.  
**ANOTHER CONTROL**: No - capability grants also use canonical hosts, so if the operator similarly misconfigures caps (`net.post:API.Example` instead of `net.post:api.example`), the emission would be denied at the capability gate. But if caps are correctly configured with canonical hosts while quotas aren't, emissions bypass quota limits.  
**FIX**: In `_validate_emission_quota`, canonicalize all host keys in the quota dict during validation.

### 2. MISCOUNT: Held-then-approved emissions consume quota twice
**ID**: F2  
**TITLE**: Held emissions consume quota at hold time AND approve time  
**SEVERITY**: HIGH  
**LOCATION**: collaborator/governance.py: `govern_action` (HELD path) and `execute_and_verify` (egress_log branch)  
**CONCRETE CASE**: Session has `emission_quota=1` for host "api.example". A net_post intent is governed with leash=PROPOSE_FIRST, creating a HELD decision. At this point, no quota is consumed. When approved via `approve()`, it calls `execute_and_verify` which consumes quota. However, if the same session later tries another emission to "api.example", it should succeed since only one emission was actually sent. But if there's a code path that consumes quota during the hold phase (which there isn't in current code), it would double-count. Wait - reviewing again: **Actually, the current implementation correctly only consumes at dispatch time in execute_and_verify, which both autonomous and approved paths reach. Held paths don't consume until approved. So this is NOT a bug.**  
**WHY IT MISCOUNTS**: N/A - This is actually implemented correctly. The quota is only consumed in `execute_and_verify`'s egress_log branch, which is reached by both autonomous executions (act_then_report) and approved executions (via approve()). Held decisions don't consume quota until they're actually executed.  
**ANOTHER CONTROL**: N/A  
**FIX**: N/A  

Wait, let me re-examine the code more carefully...

Actually, looking at `govern_action`: when leash is PROPOSE_FIRST, it returns a HELD decision without calling `execute_and_verify`. Then `approve()` calls `execute_and_verify` with `human_gated=True`. In `execute_and_verify`, in the egress_log branch, it checks `emission_allowed` and then calls `consume_emission`. So quota is only consumed once, at actual dispatch time. This is correct.

### 3. MODEL REACHABILITY: emission_quota validation allows truthy non-int values
**ID**: F3  
**TITLE**: emission_quota validation rejects bool but not other truthy non-int types  
**SEVERITY**: MEDIUM  
**LOCATION**: collaborator/session.py: `_validate_emission_quota` method  
**CONCRETE CASE**: If somehow a float like `3.0` were passed as emission_quota, it would pass the `isinstance(q, int)` check? No, wait: `isinstance(3.0, int)` is False, so it would raise the final ValueError. The validation correctly rejects non-int, non-dict, non-None values.  
**WHY IT FAILS-OPEN**: It doesn't - the validation is comprehensive.  
**ANOTHER CONTROL**: N/A  
**FIX**: N/A  

Actually, the validation looks solid:
- Rejects bool explicitly 
- For int: checks >= 0
- For dict: validates keys are str, values are non-negative int (and not bool)
- Rejects everything else

### 4. METHOD->CAP REGRESSION: Case sensitivity in method handling
**ID**: F4  
**TITLE**: HTTP methods are case-normalized but validation might be inconsistent  
**SEVERITY**: LOW  
**LOCATION**: collaborator/egress.py: `required_capability` function  
**CONCRETE CASE**: Method "post" (lowercase) is normalized to "POST" via `.upper()`, so it correctly maps to net.post. Similarly, "get" becomes "GET". This is correct behavior per HTTP spec (methods are case-sensitive but conventionally uppercase).  
**WHY IT BREAKS**: It doesn't - the code correctly normalizes methods to uppercase.  
**ANOTHER CONTROL**: N/A  
**FIX**: N/A  

The code does `m = str(method or "GET").upper()`, which is correct.

### 5. FAIL-OPEN: None host handling in quota check
**ID**: F5  
**TITLE**: emission_allowed returns True for None host, which is correct  
**SEVERITY**: LOW  
**LOCATION**: collaborator/session.py: `emission_allowed` method  
**CONCRETE CASE**: An ineligible URL (e.g., "http://example.com") canonicalizes to None. The egress gate already denies these upstream, so `emission_allowed` correctly returns True for None, since it shouldn't add an additional deny.  
**WHY IT FAILS-OPEN**: It doesn't - this is documented correct behavior.  
**ANOTHER CONTROL**: Yes - the egress capability check in `govern_action` already denies ineligible URLs before reaching quota check.  
**FIX**: N/A  

This is working as designed.

## Re-evaluation

After careful review, the main real issue is:

### **F1: Host quota dict keys must match canonical_host output**

This is a legitimate HIGH severity issue. The quota dictionary keys are provided by the operator, but the lookup uses canonicalized hosts from `egress.canonical_host()`. If the operator uses a non-canonical form in their quota config (e.g., "API.Example" instead of "api.example"), the quota will silently not apply.

**Concrete reproduction**:
1. Create session with `emission_quota={"API.Example": 1}`
2. Emit to "https://api.example/test" 
3. `canonical_host` returns "api.example"
4. `_emission_limit("api.example")` looks up key "api.example" in quota dict, which doesn't exist
5. Returns None (unlimited) instead of 1
6. Emission succeeds despite quota being configured

This is a classic footgun where the configuration interface doesn't match the internal representation.

## STEELMAN

The implementation correctly centralizes quota consumption at the single dispatch point (`execute_and_verify`'s egress_log branch) that both autonomous and human-approved paths reach, ensuring consistent counting. The validation of emission_quota at construction time prevents malformed values from disabling the control, and the getattr-guarded access ensures backward compatibility with legacy sessions. The method->cap mapping properly fails closed on unrecognized HTTP verbs, closing a latent security gap.

## VERDICT

**SERIOUS_FLAWS** - The host quota dictionary keys must be canonicalized during validation to match the canonical host format used during emission checks, otherwise quotas can be silently bypassed when operators use non-canonical host names in their configuration. **Highest-value fix**: In `_validate_emission_quota`, canonicalize all host keys in quota dictionaries using `egress.canonical_host()` and validate that they remain valid (not None).
