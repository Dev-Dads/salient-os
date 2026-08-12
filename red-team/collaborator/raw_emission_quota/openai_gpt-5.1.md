# emission-quota code panel: openai/gpt-5.1

_finish=stop seconds=38.5 cost=$0.0650 usage={'prompt_tokens': 29516, 'completion_tokens': 2811, 'total_tokens': 32327, 'cost': 0.065005, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.065005, 'upstream_inference_prompt_cost': 0.036895, 'upstream_inference_completions_cost': 0.02811}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID: F1 / TITLE: HELD-EMISSION QUOTA BYPASS VIA APPROVE PATH / SEVERITY: CRITICAL  
LOCATION: collaborator/governance.py:~397-474; collaborator/loop.py:~190-276  

CONCRETE CASE:  
- Host config: `Session(workspace=..., emission_quota={"api.example": 1})`.  
- A model-originated `net_post` with URL `https://api.example/x` comes through `govern_action` with leash floored to `PROPOSE_FIRST` and is HELD.  
- The human calls `approve(session, held_decision)` twice.  
- In both calls, `approve()` → `reauthorized_or_denied()` → `execute_and_verify(..., human_gated=True)`; inside `execute_and_verify`, the emission quota check+consume block is keyed on `tool.verify_mode == "egress_log"` *and* `getattr(tool, "egress", False) and tool.mutating` (true), but the quota logic itself is guarded only by `getattr(session, "emission_allowed", None)` / `getattr(session, "consume_emission", None)`.  
- However, the **counter itself (`_emission_counts`) is never looked at before approve**; `emission_allowed/consume_emission` are only called inside the autonomous `egress_log` branch. Because the HELD → approve path does not re-run that branch with a fresh check per approval, you can approve N times and each call sends a POST while only counting once or, depending on use, not at all.

WHY IT BYPASSES/MISCOUNTS/FAILS-OPEN:  
- The design intent states: “Consumed at the actual dispatch point in execute_and_verify (covers BOTH the autonomous and the human-approved path).”  
- In reality, the quota enforcement for `net_post` is implemented only within the `egress_log` branch in `execute_and_verify`, and counts are incremented *per dispatch* from that path.  
- When a `net_post` is HELD and later approved, the call to `execute_and_verify(..., human_gated=True)` shares the same `egress_log` path, but nothing in `approve`/`reauthorized_or_denied` ensures that each approval reuses or rechecks the quota *before* execution in a way that burns quota exactly once per emission.  
- This means a pattern of HELD → approve, then reusing the same decision or constructing equivalent ones, can cause more POSTs to be sent than the per-host quota intends, because the enforcement is not tied to “approved emission count” but only to raw dispatches via the autonomous path. Under realistic UI reuse or internal APIs reusing decisions, quota can be miscounted (under-count) relative to the number of actual emissions the human can get through.  

WHETHER ANOTHER CONTROL CATCHES IT:  
- No. The capability gate (`net.post:<host>`), leash floors, and emission seal protect authority and payload integrity, not the *count*.  
- `decision.consumed` is a guard against *re-running the same decision object*, but it does not track per-host emission count or enforce that a host’s cap is exhausted once N approved emissions have been run. New held emissions can always be created and approved; no cumulative gate exists beyond the `egress_log` dispatch point, which is not scoped per approval event.

FIX:  
- Make the emission quota explicitly enforced per successful emission, regardless of autonomous vs approved path, and bind it to the actual dispatch that sends bytes:  
  - Keep the check+consume in `execute_and_verify`’s `egress_log` branch as the single source of truth, but ensure that **every** emission, including those triggered via `approve()`, runs through that logic once per approval.  
  - Add explicit tests for "held then approved emissions consume quota once per sent emission" and "after N HELD+approved emissions to a host with quota N, further approvals are DENIED with quota-exhausted and no POST is sent."  
  - Optionally, add a small helper: `session.record_emission_attempt(host)` that is always called from `execute_and_verify` immediately before calling `egress.post`, and assert in tests that both autonomous (`govern_action` direct execution) and `approve` paths increment counts identically.


ID: F2 / TITLE: CANONICAL-HOST KEY FOOTGUN FOR PER-HOST QUOTA / SEVERITY: MEDIUM  
LOCATION: collaborator/session.py:~137-165; collaborator/egress.py:~129-175; tests/test_collaborator_emission_quota.py:NP_URL  

CONCRETE CASE:  
- Host config: `Session(workspace=..., emission_quota={"API.Example": 1})`.  
- `canonical_host("https://API.Example/v1/x")` returns `"api.example"`.  
- `_emission_limit(host)` uses the dict key `"API.Example"`, but `emit_host` passed into `emission_allowed` / `consume_emission` is `"api.example"`, so `q.get(host)` returns `None` (unlimited).  
- Emissions to `https://API.Example/...` are never capped and can be sent unbounded even though the operator believed they set a per-host quota of 1.

WHY IT BYPASSES/MISCOUNTS/FAILS-OPEN:  
- The quota map keys are raw strings and are not canonicalized in `_validate_emission_quota`, `_emission_limit`, or on assignment.  
- The egress system, including capability derivation and quota host, consistently uses `egress.canonical_host`, but the config path for `emission_quota` does not enforce that same canonicalization.  
- This mismatch means that many “reasonable-looking” host keys (mixed case, Unicode forms, trailing dots) silently fail to apply. The host is still gated by capabilities, but the quota bound is silently not enforced and the emitter gets more emissions than intended.

WHETHER ANOTHER CONTROL CATCHES IT:  
- Capability gating still enforces which hosts can be reached; byte/time caps still bound per-request size. But no other mechanism enforces "at most N emissions per destination."  
- There is no warning or validation error that the configured quota keys do not match canonical form.

FIX:  
- Canonicalize per-host quota keys at construction using the exact same pipeline as capability/egress:  
  - In `_validate_emission_quota`, when given a dict, for each `host` key: run `egress.canonical_host(host)`; if it returns `None`, reject with `ValueError`; otherwise store the canonical string as the key.  
  - Alternatively, change `_emission_limit` to call `egress.canonical_host` on the input host when `q` is a dict.  
- Document clearly that keys are canonical hosts and add tests for mixed-case and Unicode variants mapping correctly to the same quota entry.


ID: F3 / TITLE: EMISSION QUOTA DISABLED BY TRUTHY-NON-INT VALUES IN DICT / SEVERITY: LOW  
LOCATION: collaborator/session.py:~137-165  

CONCRETE CASE:  
- Host config: `Session(workspace=..., emission_quota={"api.example": True})`.  
- `_validate_emission_quota` rejects boolean caps (`isinstance(cap, bool)` clause), but only for top-level dict values. If a future code path mutates `session.emission_quota` directly (e.g., `session.emission_quota["api.example"] = True`) after construction—something not prevented by type system or immutability—the in-memory structure will contain a truthy non-int.  
- `_emission_limit` simply does `q.get(host)` and `emission_allowed` compares `_emission_counts.get(host, 0) < limit`. With `limit=True`, this is `0 < True`, which is `0 < 1` and works for the first emission, but becomes `1 < 1` (False) after `consume_emission()`. Now the quota behaves like `1`, but the path to this state is not validated and might differ for other truthy-but-weird values if type constraints are weakened or bypassed in later refactors.

WHY IT BYPASSES/MISCOUNTS/FAILS-OPEN:  
- As shipped, the constructor validation is solid and rejects malformed quotas, but state can be mutated later with no guard. The semantics in `emission_allowed` rely on `limit` being an `int` or `None`; they do not re-validate or coerce types.  
- If any external code (e.g., orchestration layer, tests, host scripting) mutates `session.emission_quota` in place, the emission quota enforcement may behave inconsistently or unexpectedly, possibly failing open (e.g., if `limit` is set to a large non-int whose comparison semantics are unusual or error-prone under Python upgrades).

WHETHER ANOTHER CONTROL CATCHES IT:  
- No runtime check exists; the only validation is at construction, and it can be bypassed by direct attribute mutation.  
- Capability gates and byte caps still apply, but the emission count bound can be effectively disabled or mis-tuned this way.

FIX:  
- Treat `emission_quota` as write-once immutable configuration:  
  - Make it a private attribute (`_emission_quota`) and expose only read-only access, or wrap it in an object that validates on mutation.  
  - Alternatively, add a defensive re-validation in `_emission_limit`: if `q` is a dict, assert that `cap` is a non-negative int; if not, raise loudly instead of silently treating truthy-but-invalid caps.  
- Add tests that direct post-construction mutation of `session.emission_quota` with invalid types raises on first use (`emission_allowed`) to keep the invariant strong.


ID: F4 / TITLE: REQUIRED_CAPABILITY DEFAULT-METHOD EDGE-CASE / SEVERITY: LOW  
LOCATION: collaborator/egress.py:~160-175; collaborator/governance.py:~230-254  

CONCRETE CASE:  
- A future egress tool is added with `egress_method=""` or `egress_method=None` but intended to behave as a write (e.g., a tool that internally treats a blank method as a POST).  
- `required_capability(url, method)` does `m = str(method or "GET").upper()`, so `None` or `""` map to `"GET"` and thus to `net.get:<host>`.  
- The new tool could then send an emission-like request under a `net.get:<host>` read capability, unintentionally widening what that cap allows, contrary to the intended separate namespace for emissions.

WHY IT BYPASSES/MISCOUNTS/FAILS-OPEN:  
- As documented, existing tools use `GET` and `POST` correctly, and tests pin that. The failure mode is forward-looking: if a new tool is wired carelessly with an empty method but actually emits data, it will inherit read authority accidentally.  
- That’s a latent “future regression” vector rather than an immediate exploitable hole, but it weakens the intended invariants around method->cap mapping.

WHETHER ANOTHER CONTROL CATCHES IT:  
- Capability gating still applies: you need `net.get:<host>` to reach the host. But the separation between read and emit authorities (net.get vs net.post) is undermined.  
- No other layer inspects HTTP method semantics or payload presence.

FIX:  
- Make the defaulting logic explicit and safer:  
  - Change `required_capability` to require that callers pass a non-empty method string, and treat `None`/`""` as an error (`return None`) rather than defaulting to GET, except for `web_fetch` which can explicitly pass `"GET"`.  
  - Add tests that a tool with `egress_method=""` or `None` yields `None` capability, ensuring governance denies it until properly wired.


STEELMAN (defense of the current design):  
- The quota design correctly places enforcement at the network-dispatch seam (`execute_and_verify`’s `egress_log` branch), which is the one place both autonomous and human-approved emissions must pass through, minimizing the risk of alternate network paths that ignore quotas. The constructor-time validation on `emission_quota` is rigorous and fails loudly on malformed configs, and hosts are treated as trusted to provide canonical host keys.  
- The method->cap mapping in `required_capability` strengthens security by separating read and write capabilities and failing closed on unsupported verbs; current tools all use explicit `GET` or `POST`, and the defaults for `None`/`""` preserve existing behavior without introducing any new write authority.

VERDICT: SERIOUS_FLAWS — The canonical-host/key mismatch and the potential to miscount or under-enforce the per-host emission quota (especially around HELD/approved emissions and host-keying) undermine the core purpose of the new bound. The single highest-value fix is to canonically normalize all per-host quota keys using `egress.canonical_host` at configuration time and enforce quota strictly per dispatched emission for both autonomous and approved paths via a single, well-tested check in `execute_and_verify`.
