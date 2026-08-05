# Red-team: deepseek/deepseek-r1

_finish=stop seconds=335.1 usage={'prompt_tokens': 19361, 'completion_tokens': 5453, 'total_tokens': 24814, 'cost': 0.0271852, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0271852, 'upstream_inference_prompt_cost': 0.0135527, 'upstream_inference_completions_cost': 0.0136325}, 'completion_tokens_details': {'reasoning_tokens': 4724, 'image_tokens': 0, 'audio_tokens': 0}}_

### Findings:

#### F1: Distinct failure modes from same channel counted as distinct channels
- **SEVERITY**: HIGH
- **LOCATION**: `salienceos/verifier/composer.py` in `_agreement_gap`
- **CONCRETE TRIGGER**: For HIGH/CRITICAL stakes, two WORLD facts for the same obligation with identical `channel` but different `failure_mode` (e.g., `host.rehash` with `failure_mode="host_rehash"` and `failure_mode="host_rehash_retry"`). The composer counts them as distinct channels because it groups solely by `channel` string.
- **WHY IT MATTERS**: Under the threat model (non-malicious observer), a single observer (e.g., host re-hash) could suffer correlated errors across retries. Counting these as "distinct channels" falsely satisfies the two-source requirement for HIGH/CRITICAL stakes, potentially leading to a false `VERIFIED` when only one independent observation path exists.
- **SUGGESTED FIX**: Modify channel counting to use `(channel, failure_mode)` tuples:  
  ```python
  channels = {(w.channel, w.failure_mode) for w in distinct}  # Replace {w.channel for w in distinct}
  ```

#### F2: Shell.run with no outputs leads to unverifiable due to missing floor
- **SEVERITY**: MEDIUM
- **LOCATION**: `salienceos/verifier/contract.py` in `_build_shell_run`
- **CONCRETE TRIGGER**: `shell.run` action with `declared_outputs=[]`. The contract builder emits only exit_status and write_set obligations (no artifact_hash). The composer rejects it for missing the side-effect kind (artifact_hash/path_state), resulting in `UNVERIFIED(MISSING_FLOOR)`.
- **WHY IT MATTERS**: Honest actions with no outputs (e.g., cleanup scripts) become unverifiable despite having valid exit status and write-set. This violates the principle that deterministic actions should be verifiable cheaply (spec §4) and forces unnecessary `UNVERIFIED` outcomes.
- **SUGGESTED FIX**: Adjust the floor check to allow missing side-effect obligations if `declared_outputs` is empty:  
  ```python
  # In composer.py, replace:
  # if not (all(k in kinds for k in FLOOR_KINDS) and any(k in kinds for k in SIDE_EFFECT_KINDS)):
  has_side_effect = any(k in kinds for k in SIDE_EFFECT_KINDS) or not declared_outputs
  if not (all(k in kinds for k in FLOOR_KINDS) and has_side_effect):
  ```

#### F3: World evidence from claim channel not filtered
- **SEVERITY**: MEDIUM
- **LOCATION**: `salienceos/verifier/composer.py` in `compose`
- **CONCRETE TRIGGER**: WORLD evidence with `channel="receipt"` (e.g., from a misconfigured observer). The composer accepts it as valid WORLD evidence since it passes the type check (`WorldEvidence`), potentially allowing claim-derived facts to count toward agreement.
- **WHY IT MATTERS**: While the threat model assumes non-malicious observers, a buggy observer could accidentally reuse the claim channel. This violates independence (M1) by allowing CLAIM-derived data to masquerade as WORLD evidence, risking false `VERIFIED` if the failure mode differs.
- **SUGGESTED FIX**: Explicitly filter out WORLD evidence with CLAIM-associated channels:  
  ```python
  world = tuple(e for e in world_ev if type(e) is WorldEvidence and e.channel != "receipt")
  ```

#### F4: Stakes scaling ignores failure mode distinctness per channel
- **SEVERITY**: MEDIUM
- **LOCATION**: `salienceos/verifier/composer.py` in `_agreement_gap`
- **CONCRETE TRIGGER**: Two WORLD facts for the same obligation with identical `(channel, failure_mode)` but different values (e.g., `host.rehash` returning different hashes due to non-malicious corruption). The composer still counts them as one channel despite the value mismatch.
- **WHY IT MATTERS**: The stakes scaling only counts unique channel/failure-mode pairs, ignoring whether the values agree. This could allow a single corrupted channel to satisfy the two-source requirement (if it emits two values) without actual agreement, potentially leading to false `VERIFIED`.
- **SUGGESTED FIX**: Require value consensus within distinct channels:  
  ```python
  # In _agreement_gap, after gathering distinct:
  distinct_values = {w.value for w in distinct}
  if len(distinct_values) != 1:
      return (Reason.INSUFFICIENT_CHANNELS, "world facts disagree on value")
  ```

---

### STEELMAN:
The design excels in enforcing CLAIM/WORLD separation via strict type fencing (stores/composer), making executor-derived facts physically uncountable as independent evidence. The pure, mutation-tested composer ensures fail-closed behavior, and the sealed consumer gate (`SealedGateError`) robustly prevents laundering of `UNVERIFIED` into success. Host-side observations provide genuinely independent facts for common cases without replay.

### VERDICT:
**SERIOUS_FLAWS** due to F1 (HIGH risk of false `VERIFIED` under correlated observer errors) and F2/F3/F4 (MEDIUM risks compromising independence/usability). Fixes are needed to uphold M1 (distinct failure modes) and M3 (action-class verifiability).
