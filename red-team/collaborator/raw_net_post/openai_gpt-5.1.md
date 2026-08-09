# net.post Tier-2 code panel: openai/gpt-5.1

_finish=stop seconds=17.1 cost=$0.0635 usage={'prompt_tokens': 36523, 'completion_tokens': 1782, 'total_tokens': 38305, 'cost': 0.06347375, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.06347375, 'upstream_inference_prompt_cost': 0.04565375, 'upstream_inference_completions_cost': 0.01782}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID 1 / INSUFFICIENT ERROR HANDLING WHEN `egress.post` RETURNS `record=None` / SEVERITY: MEDIUM  
LOCATION: collaborator/governance.py: ~268-289; collaborator/tools.py: ~180-214  

CONCRETE INPUT OR BYPASS  
- Craft a `ToolIntent` that successfully passes the gate and reaches the `egress_log` branch, and cause `egress.post` to hit an unexpected internal bug that returns an `EgressResult` with `record=None`.  
  - This is easiest via a future regression or monkey‑patch; pseudo‑example in tests:  
    ```python
    from collaborator import egress
    from collaborator.egress import EgressResult

    def bad_post(url, body, **kw):
        # Simulate an internal bug: return EgressResult with no record
        return EgressResult(record=None, body=b"")

    with mock.patch("collaborator.egress.post", bad_post):
        dec = govern_action(session, ToolIntent("net_post", {"url": "https://api.example/x", "body": "x"}, "structured"))
    ```  
  - `execute_tool` -> `_exec_net_post` -> `egress.post` returns `EgressResult(record=None)`; governance then does `rec = execution.egress` (None), then `rec.canonical_dest` and `rec.status` unguarded.  

WHY IT BREAKS A GUARANTEE  
- ADR 0003 and the module docstring claim the egress path "never raises; a refusal is a non‑ok EgressRecord" and that the governance seam "never raises" (all failures become DENIED/FAILED decisions).  
- As implemented, `execute_and_verify`’s egress branch assumes `execution.egress` is a valid `EgressRecord`. If an internal bug or future change in `egress.post` or `_exec_net_post` ever yields `None`, this will raise `AttributeError` in core governance instead of converting to a FAILED/denied Decision.  
- That violates the "fails closed, never raises across the tool boundary" invariant and would surface as a 500‑style crash path, not an explicit refused action, weakening the robustness guarantee around outbound emission.  

WHETHER ANOTHER CHECK CATCHES IT  
- No. There is no defensive check around `execution.egress` in the `verify_mode=="egress_log"` branch. All tests assume a non‑None `EgressRecord`. Any `None` will propagate to an unhandled exception in `execute_and_verify`.  

FIX  
- Harden `execute_and_verify` against a missing or malformed egress record, treating it as a FAILED decision rather than letting an exception propagate:  
  ```python
  if tool.verify_mode == "egress_log":
      execution = execute_tool(tool, session.workspace, args,
                               egress_preview=egress_preview, egress_auth=egress_auth)
      rec = execution.egress
      if rec is None:
          # Defensive: egress client misbehaved / returned no record
          return Decision(
              action_id, tool.name, FAILED,
              "egress failed: no egress record", leash,
              cleared=False, result=execution.result, directive=directive, args=args,
              egress=None,
          )
      ok = bool(execution.result.ok) and bool(rec.ok)
      reason = (f"egress {rec.canonical_dest} [{rec.status}]" if ok
                else (rec.error or "egress failed"))
      return Decision(...)
  ```  
- Optionally also assert in `_exec_web_fetch` / `_exec_net_post` that `result.record` is an `EgressRecord`, and add a unit test that mocks `egress.post` / `egress.fetch` to return an `EgressResult(record=None)` and verifies that governance returns a FAILED Decision, not an exception.  


ID 2 / BODY LENGTH NOT CARRIED ON NON‑STRING BODY OVERFLOW REFUSAL / SEVERITY: LOW  
LOCATION: collaborator/egress.py: ~192-212  

CONCRETE INPUT OR BYPASS  
- Call `egress.post` directly (or through `net_post`) with a too‑large non‑string payload and inspect the resulting record:  
  ```python
  big = b"x" * (egress.MAX_POST_BODY + 1)
  res = egress.post("https://api.example/x", big, resolver=lambda h: ["93.184.216.34"],
                    connection_factory=_post_factory(_FakeResp(200), []))
  # res.record.request_body_len will be 0, even though we refused a >cap body
  ```  

WHY IT BREAKS A GUARANTEE  
- The contract says the body is "hard‑capped, hashed, and (only when keep_preview) previewed", and that refusals are represented as non‑ok `EgressRecord`s with honest accounting.  
- For string bodies, the overflow path includes `body_len` in the refusal record (`body_len=body_len`); for bytes/bytearray, the path is the same, so behaviour is consistent. However, the earlier `"body must be str or bytes"` refusal sets `body_len=0`, and the test coverage focuses only on the over‑cap path, not on all refusal cases.  
- This is a minor audit‑accuracy nit rather than a policy break: the system still refuses the over‑cap body, but some refusal paths do not consistently populate `request_body_len`/`hash`, making for slightly weaker forensic records.  

WHETHER ANOTHER CHECK CATCHES IT  
- Not applicable as a bypass: this doesn’t let anything emit that shouldn’t; it only affects the completeness of the refusal record. Tests already assert `request_body_len` on the explicit overflow path, but not on all refusal branches.  

FIX  
- Make all refusal paths in `post()` that have successfully computed `body_len` and `body_hash` pass them through to `_refused`, including the "body must be str or bytes" error where the type is wrong but a large raw object was supplied:  
  ```python
  # After computing body_bytes/body_len/body_hash:
  if not isinstance(body, (str, bytes, bytearray)):
      return _refused(host, target_hash, request_bytes,
                      "body must be str or bytes", method="POST",
                      body_hash=body_hash, body_len=body_len, body_preview=body_preview)
  ```  
- Add tests that cover refusal paths other than overflow (bad content‑type, bad auth, bad body type) and assert that the record’s `request_body_len` and `request_body_hash` are set when the bytes were known.  


STEELMAN (why this code is stronger than average)  
- The implementation cleanly separates authority (signed caps; method‑aware `net.get:` vs `net.post:`; exact canonical host) from influence, and enforces a structural "no capability, no reach" model — including for net.post, which gets its own namespace and separate `net.post.auto:` lift. The canonicalization and IP‑pinning logic is careful: userinfo is rejected, hostname parsing and IDNA handling are unified between the gate and the transport, redirects are fail‑closed, and only globally routable unicast IPs are allowed (with explicit CGNAT and NAT64 coverage).  
- Governance re‑derives the required capability on approval from the frozen args, closing emission TOCTOU; the proposer floor and separate `source=="proposed"` checks prevent a model‑originated auto‑emission even to an auto host. Credential handling is host‑only, never model‑sourced, sanitized against header‑splitting, not logged, and looked up by the same canonical host string that passed the gate, which matches the transport’s SNI/cert host.  

VERDICT: MINOR_ISSUES — the Tier‑2 `net.post` build largely matches the ADR’s guarantees (no redirect re‑POSTs, separate get/post namespaces, strict canonicalization, TOCTOU re‑gating, proposer floor, host‑only credentials). The highest‑value fix is to harden `execute_and_verify`’s `egress_log` path against malformed or missing `EgressRecord`s so that any internal egress client regression cleanly downgrades to a FAILED decision instead of throwing, preserving the "never raises; all failures are explicit decisions" boundary around outbound emissions.
