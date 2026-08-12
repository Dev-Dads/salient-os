# emission-quota code panel: anthropic/claude-opus-4.1

_finish=stop seconds=113.7 cost=$0.6176 usage={'prompt_tokens': 35179, 'completion_tokens': 1199, 'total_tokens': 36378, 'cost': 0.61761, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.61761, 'upstream_inference_prompt_cost': 0.527685, 'upstream_inference_completions_cost': 0.089925}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

I'll conduct a thorough adversarial review of this emission-quota and method->capability change, looking for bypasses, miscounts, and fail-open conditions.

## FINDINGS

### ID: 1 / TITLE: Quota Bypass via web_fetch / SEVERITY: LOW / LOCATION: collaborator/governance.py:~380
**CONCRETE CASE**: A model can issue unlimited `web_fetch` (GET) requests to any host without consuming quota, as web_fetch is deliberately not counted (documented scope). While this is intentional, it creates an unbounded READ channel that could be used for exfiltration via URL parameters.
**WHY IT BYPASSES**: `web_fetch` has `tool.mutating=False` and uses `verify_mode="egress_log"`, but the quota check at line ~380 only triggers when BOTH `egress_log` AND `tool.mutating` are true.
**ANOTHER CONTROL**: The MAX_URL_TARGET (2048 bytes) cap limits each exfil attempt, and the capability gate still requires `net.get:<host>`.
**FIX**: Document this clearly as an accepted risk, or add a separate read-quota if the exfil risk is unacceptable.

### ID: 2 / TITLE: Canonical Host Key Mismatch Footgun / SEVERITY: MEDIUM / LOCATION: collaborator/session.py:~180
**CONCRETE CASE**: Operator sets `emission_quota={"API.Example": 1}` but the canonical host becomes `"api.example"` (lowercased), so the quota never matches and the host gets unlimited emissions.
**WHY IT FAILS-OPEN**: The quota dict keys are stored as-provided without canonicalization, but `_emission_limit()` looks up by the already-canonical host from `egress.canonical_host()`. Mismatch = None = unlimited.
**ANOTHER CONTROL**: None - this silently fails open.
**FIX**: Canonicalize quota dict keys at validation time in `_validate_emission_quota()`, or at least warn if a key doesn't look canonical (contains uppercase/ports/schemes).

### ID: 3 / TITLE: Cross-Session Quota Sharing Not Possible / SEVERITY: LOW / LOCATION: collaborator/session.py:~180
**CONCRETE CASE**: Two parallel Session objects with the same workspace can each send N emissions to a host, getting 2N total when the operator intended N globally.
**WHY IT BYPASSES**: `_emission_counts` is instance state, not shared. This is documented ("per-SESSION") but could surprise operators expecting workspace-level enforcement.
**ANOTHER CONTROL**: Documented as a known scope limitation with follow-up planned.
**FIX**: Accept as documented, or add a workspace-keyed shared counter (complexity vs benefit tradeoff).

### ID: 4 / TITLE: Method->Cap None Default is Permissive / SEVERITY: LOW / LOCATION: collaborator/egress.py:~180
**CONCRETE CASE**: An empty/None method defaults to GET (read cap), not DENY. While reasonable for backwards compatibility, this means a malformed method field fails open to read access rather than closed.
**WHY IT FAILS-OPEN**: `str(method or "GET").upper()` treats falsy values as GET requests.
**ANOTHER CONTROL**: The capability gate still enforces - this only affects which capability is required.
**FIX**: Consider whether None/empty should map to None (DENY) for stricter fail-closed behavior, with only explicit "GET" getting read access.

### ID: 5 / TITLE: Quota Not Checked for Non-Mutating Egress / SEVERITY: LOW / LOCATION: collaborator/governance.py:~380
**CONCRETE CASE**: Even if an egress tool is created with `egress=True` but `mutating=False`, it bypasses quota entirely.
**WHY IT BYPASSES**: The quota check requires BOTH `tool.egress` AND `tool.mutating`. This matches the documented "web_fetch not counted" scope.
**ANOTHER CONTROL**: No such tool exists today; would require code changes to create one.
**FIX**: Document that quota applies only to mutating egress, or check `tool.egress` alone if all egress should be counted.

## STEELMAN

The implementation is largely sound with well-structured fail-closed defaults. The quota check occurs at the single correct dispatch point that both autonomous and human-approved paths must traverse. The method->capability mapping correctly fails closed on unknown verbs, preventing future write operations from inheriting read authority. The validation is strict and fails loudly on malformed quotas. The canonical host key mismatch (Finding #2) is the main operational footgun, but it affects only operator configuration, not model-reachable attack surface.

## VERDICT

**MINOR_ISSUES** - The implementation successfully bounds emission counts with proper fail-closed behavior, but the canonical host key mismatch could silently disable quotas for operators who naturally use mixed-case hostnames. **Single highest-value fix**: Canonicalize quota dictionary keys at validation time to match the runtime lookup canonicalization.
