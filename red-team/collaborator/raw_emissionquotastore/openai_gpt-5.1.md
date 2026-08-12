# emission-quota-store code panel: openai/gpt-5.1

_finish=stop seconds=12.7 cost=$0.0251 usage={'prompt_tokens': 11561, 'completion_tokens': 1060, 'total_tokens': 12621, 'cost': 0.02505125, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.02505125, 'upstream_inference_prompt_cost': 0.01445125, 'upstream_inference_completions_cost': 0.0106}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID 1 / TITLE: Emission quota store path check uses provenance-specific error text and name  
SEVERITY: LOW  
LOCATION: collaborator/session.py:153-174 (`_assert_store_outside_workspace`) as called for `emission_quota_store` at 126-133  

CONCRETE CASE  
- Host configures:  
  - `workspace = "/home/user/ws"`  
  - `emission_quota_store = "/home/user/ws/emq.json"` (inside workspace)  
- `Session(workspace=workspace, emission_quota_store=emission_quota_store)` calls  
  `self._assert_store_outside_workspace(emission_quota_store)`.  
- On detection, code raises `ValueError(f"provenance_store {store_path!r} ...")`, mentioning `provenance_store` even though this path is for the emission quota store.  

WHY IT FAILS-OPEN/BREAKS-SEPARATION/BREAKS-TOTALITY  
- It does not break the security invariant (the path is correctly rejected and the store remains outside the agent’s reach), but it mislabels the failing parameter and rationale.  
- This can mislead operators or future maintainers when diagnosing why an emission quota store configuration failed, and subtly couples what is supposed to be a generic substrate helper to only the provenance use-case.  
- No fail-open or domain separation break occurs; construction still fails loud, and the emission quota store cannot be placed in the workspace.  

WHETHER ANOTHER CONTROL CATCHES IT  
- The same `_assert_store_outside_workspace` logic correctly enforces out-of-workspace for both provenance and emission quota stores; there is no secondary control needed to stop an in-workspace path.  
- The bug is informational/maintainability rather than a security gap.  

FIX  
- Make `_assert_store_outside_workspace` parameter-agnostic and/or give it a neutral error message, or pass in a label indicating which store is being validated. For example:  
  ```python
  def _assert_store_outside_workspace(self, store_path, kind: str = "store"):
      import os as _os
      raw = Path(store_path)
      if not raw.is_absolute():
          raise ValueError(f"{kind} {store_path!r} must be an absolute path")
      ws = self.workspace.resolve()
      try:
          resolved = raw.resolve()
      except (OSError, RuntimeError, ValueError) as exc:
          raise ValueError(f"{kind} path {store_path!r} is unresolvable: {exc}") from exc
      lexical = Path(_os.path.abspath(str(raw)))
      for candidate, how in ((resolved, "resolves"), (lexical, "is lexically")):
          if candidate == ws or ws in candidate.parents:
              raise ValueError(
                  f"{kind} {store_path!r} {how} inside the workspace — a store the agent could "
                  f"write would let it tamper with {kind} state")
      return resolved
  ```  
  And call it as:  
  ```python
  self._emission_store = self._assert_store_outside_workspace(emission_quota_store, "emission_quota_store")
  self._provenance_store = self._assert_store_outside_workspace(provenance_store, "provenance_store")
  ```  

---

STEELMAN  

The durable emission quota store and shared substrate are, in the critical dimensions, carefully aligned with their stated goals. The fail-closed direction on untrusted stores is consistently enforced: any untrusted load sets `_emission_store_untrusted=True`, which in turn causes `emission_allowed` to deny all quota-limited hosts while leaving unlimited hosts unaffected. Domain separation between provenance and emission quota stores is implemented via distinct domain labels passed into a common HMAC derivation; tests explicitly verify that a provenance store cannot be replayed as an emission quota store, and the refactor preserves the provenance MAC by keeping its domain string unchanged.  

Persistence semantics are sound: the stored count is loaded at session construction, and per-emission persistence ensures that a restart cannot reset the quota counter; tests pin that a host at its limit remains denied after a restart. The store and substrate functions are total (they catch and map all exceptions to safe boolean/flagged results), so neither bad paths nor I/O failures cause `Session.__init__` or emission governance paths to raise. For sessions without a store, or for unlimited quotas, behavior remains unchanged from the pre-diff in-memory semantics, and persistence failures only ever degrade durability rather than correctness.  

---

VERDICT: SOUND  

The durable emission quota store and its substrate correctly enforce fail-closed behavior for restrictive bounds, maintain domain separation, preserve cross-session quotas, and remain total and non-disruptive in both store-present and store-absent cases. The single highest-value fix is to generalize `_assert_store_outside_workspace`’s error messaging (and optionally its interface) so that it clearly identifies which durable store is being validated, avoiding operator confusion without changing the security posture.
