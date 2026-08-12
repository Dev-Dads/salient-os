# maintfetch CODE panel: openai/gpt-5.1

_finish=stop seconds=58.4 cost=$0.0854 usage={'prompt_tokens': 31841, 'completion_tokens': 4561, 'total_tokens': 36402, 'cost': 0.08541125, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.08541125, 'upstream_inference_prompt_cost': 0.03980125, 'upstream_inference_completions_cost': 0.04561}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID: MF-01 / TITLE: Over-cap streaming ceiling is correct and fail-closed  
SEVERITY: LOW  
FILE: collaborator/egress.py:fetch_to_file; collaborator/tools.py:_exec_maint_fetch  

CONCRETE BUG OR ATTACK:  
- Over-cap logic: `if total + len(chunk) > max_bytes:` (egress.py, ~line in fetch_to_file) means:
  - A body of exactly `max_bytes` is fully written and accepted (OK), which matches the English “cap” semantics.
  - A body of `max_bytes + 1` is rejected before writing the last byte; earlier bytes up to `max_bytes` may have been written.
- `tests/FetctToFileTransport.test_over_cap_fails_closed_single_read` and `test_over_cap_stops_mid_stream_no_full_write` explicitly validate:
  - No bytes are written when `max_bytes` is smaller than the first chunk (the check before any write).
  - At most `max_bytes` bytes are written when the stream goes over-cap mid-way.
- The executor `_exec_maint_fetch` deletes the target file whenever `rec.ok` is False, including the over-cap path. File-handling is under a `with open(..., "wb")` (tools.py, `_exec_maint_fetch`), so:
  - The partial write happens while the file is open.
  - `fetch_to_file` returns non-ok, closing `resp` and `conn`; then the `with` closes the file descriptor.
  - Only after the `with` scope, `_exec_maint_fetch` calls `_unlink_quiet(target)`, which works on all platforms because the OS has closed the file.
- sink.write() exceptions:
  - In `fetch_to_file` the `sink.write(chunk)` is inside a try that catches `OSError` (and `ssl.SSLError`, `http.client.HTTPException`) and returns a non-ok `_refused(...)` (egress.py, `except` block).
  - `_exec_maint_fetch` wraps the entire mkdir/open/egress.fetch_to_file call in `try: ... except OSError as exc:` and unlinks the file there. Tests (`Executor.test_non_ok_deletes_partial_artifact`) verify partials are deleted when `ok=False`, even for simulated partial writes.
- Non-2xx / redirect:
  - For 3xx: it never streams; returns a `_refused` record with `ok=False` and no writes.
  - For non-2xx: it drains up to `DEFAULT_MAX_RESPONSE + 1` into nowhere, then fails; no bytes are written to sink.
- response_len/hash:
  - For success, `response_len=total` and `response_hash` is computed purely from `chunk` values actually processed and written.
  - For refusal (including over-cap and write errors) the record’s `ok=False` and `response_len` is 0; it never pretends the artifact was staged.

WHETHER ANOTHER CONTROL CATCHES IT:  
- Governance executor `_exec_maint_fetch` is the belt: any non-ok egress record leads to unlink, preventing partial artifacts from remaining.
- Unit tests are explicit backstops for these properties; they will catch regressions.

FIX:  
- No behavioral change required; the logic is sound.  
- Minor hardening: consider updating the error message for over-cap refusal to include the actually-written byte count (`total`) in the record for better operator visibility, while leaving `ok=False` and staging behavior unchanged.

STEELMAN:  
The streaming ceiling is implemented with an explicit `>` guard and checked prior to each write, ensuring that a response strictly larger than `max_bytes` is never accepted as OK, and no more than `max_bytes` bytes are ever written. Because the executor uniformly unlinks on any non-ok record and all write/disk errors are converted into non-ok records, a truncated or over-cap artifact cannot persist and cannot masquerade as complete. Response length and hash are computed only from written bytes, maintaining the integrity of the audit record.  

---

ID: MF-02 / TITLE: MAINT capability namespace is cleanly separate and bound to the same canonical host  
SEVERITY: LOW  
FILE: collaborator/egress.py:required_capability; tests/test_collaborator_maint_fetch.py:CapDerivation  

CONCRETE BUG OR ATTACK:  
- `required_capability(url, "MAINT")` returns `EGRESS_MAINT_CAP_PREFIX + host` (egress.py, `required_capability`), where `host` is derived by the same `canonical_host` as GET/POST.
- Tests validate:
  - `"MAINT"` returns `net.maint:docs.example`.
  - It differs from `net.get:docs.example` and `net.post:docs.example`.
  - Ineligible URLs (http, userinfo, non-443) yield `None`.
- The single derivation site is used by governance both at hold-time and at approve-time (governance wiring in ADR and tests under `Seam`), ensuring authorize==connect and no TOCTOU in capability derivation.
- `EGRESS_MAINT_CAP_PREFIX` is a different static prefix string from `EGRESS_CAP_PREFIX` and `EGRESS_POST_CAP_PREFIX`; so no path exists where a `net.get` or `net.post` grant is misinterpreted as `net.maint`, or vice versa.
- “MAINT” is not used as an HTTP method on the wire; it is only used at the governance layer as the `egress_method`/cap-kind. On the transport side, the verb is always `"GET"`.

WHETHER ANOTHER CONTROL CATCHES IT:  
- Signed PolicyCaps and the default-deny capability gate ensure that even if `required_capability` misbehaved, ungranted static capabilities (“net.maint:__derived__”) would fail closed.
- Tests exercise that a read grant (`net.get`) does not confer maint, and that lack of `net.maint` denies.

FIX:  
- No functional fix required.  
- Documentation improvement: make explicit in ADR and code comments that `egress_method="MAINT"` is strictly a capability-kind tag and is never serialized into the HTTP request line.

STEELMAN:  
Capability derivation for maint_fetch uses the same canonicalization pipeline as GET/POST and returns a distinct `net.maint:<host>` authority, guaranteeing that only an explicitly signed maintenance grant can authorize an artifact fetch. The method name “MAINT” is confined to the governance side and does not affect HTTP semantics, preventing any confusion between capability kinds and wire methods. Because the policy caps are signed and default-deny, a misconfiguration cannot accidentally promote `net.get` or `net.post` to maintenance authority.  

---

ID: MF-03 / TITLE: Seal and freeze binding for maint_fetch (url,dest) is structurally correct  
SEVERITY: LOW  
FILE: collaborator/tools.py:SEALED_TOOLS, held_action_seal, freeze_args; tests: SealAndFreeze, Seam  

CONCRETE BUG OR ATTACK:  
- `SEALED_TOOLS` includes `"maint_fetch"`; `held_action_seal` has a specific branch for it: a leading type-tag `b"M"` and two str-coerced fields for `url` and `dest`, length-prefixed and hashed together with the tool name (tools.py, `held_action_seal`).
- `freeze_args` coerces `url` and `dest` to `str(frozen[key] or "")`, identical to how `_exec_maint_fetch` reads them, closing the drifting-`__str__` class vector.
- Tests demonstrate:
  - Changing `url` or `dest` changes the seal.
  - Seals for `maint_fetch` do not collide with seals for other tools (e.g., write_file).
  - Mutating `held.args["dest"]` after hold causes the `approve` path to deny with a seal mismatch and never reaches the executor.
- Field framing is injective (8-byte length prefixes per part). Because the tool name is included as the first field, a Decision.tool rebind cannot replay a seal for one tool as another.

WHETHER ANOTHER CONTROL CATCHES IT:  
- Even if the seal logic were to be broken, governance still re-derives `required_capability` and compares against signed caps at approve-time. That would at least prevent host authority escalation, though it wouldn’t protect against dest-swapping inside the same tool.

FIX:  
- No fix required; the logic matches the design.  
- Optional improvement: add a dedicated maint_fetch-specific unit test that mutates both `url` and `dest` between hold and approve to ensure both fields are enforced symmetrically (today’s tests already cover dest swap, but not an explicit url swap mutation).

STEELMAN:  
Maint_fetch uses the same robust TOCTOU-hardening scheme as other high-risk tools: args are frozen to immutable, str-coerced values and sealed with an injective, tool-tagged framing. Governance recomputes this seal at approve-time, rejecting any post-hold mutations of either `url` or `dest`, and because the executor reads the same frozen fields, approved==executed is guaranteed structurally, not just by convention. This prevents an attacker from using mutable argument objects or tool rebinding to stage artifacts at unintended URLs or destinations.  

---

ID: MF-04 / TITLE: Workspace fence and parent-dir creation for dest are correctly constrained  
SEVERITY: LOW  
FILE: collaborator/tools.py:resolve_in_workspace, _exec_maint_fetch  

CONCRETE BUG OR ATTACK:  
- `resolve_in_workspace(workspace, rel)`:
  - Resolves `root = Path(workspace).resolve()`.
  - Resolves `target = (root / rel).resolve()`, catching and mapping resolution errors to `WorkspaceError`.
  - Ensures `target == root` or `root in target.parents`; otherwise raises `WorkspaceError("path escapes workspace")`.
- `_exec_maint_fetch` does:
  - `rel = str(args.get("dest") or "")`.
  - `target = resolve_in_workspace(workspace, rel)`; a bad or escaping path raises `WorkspaceError` which is deliberately not caught here, so it bubbles up to governance as a DENY.
  - `os.makedirs(os.path.dirname(str(target)) or str(workspace), exist_ok=True)` to create parents. Since `target` itself is already constrained to the workspace subtree, its parent is also inside the fence.
- Tests (`Executor.test_dest_escaping_workspace_raises`) demonstrate that `dest="../escape.deb"` raises `WorkspaceError`.
- Overwrite/symlink behavior:
  - `open(target, "wb")` truncates/overwrites existing files, including symlinks. However, `resolve_in_workspace` resolves symlinks in the path before validating, so a symlink pointing outside the workspace will cause `target` to resolve outside `root` and be rejected.

WHETHER ANOTHER CONTROL CATCHES IT:  
- Governance treats `WorkspaceError` from `execute_tool` as a DENY at the gate. That’s the primary control for escaping paths.
- Egress observer and capabilities have no impact here; the path fence is entirely local and must be correct, which it is.

FIX:  
- No fix required.  
- Optional hardening: explicitly assert that `target.parent` is within the workspace root just before `os.makedirs`, to protect against any future refactor of `resolve_in_workspace` that might weaken its guarantee.

STEELMAN:  
Destination paths for maint_fetch are forced through a resolve-then-check fence that rejects any attempt to escape the workspace, including via `..`, absolute paths, and symlink tricks. Parent directory creation is done using the already-validated resolved path, preserving the fence, and escaping paths raise `WorkspaceError` that governance converts to a denial. Symlink escapes are caught up front at resolution time, so writes can’t be redirected out of the workspace by symlink manipulation.  

---

ID: MF-05 / TITLE: maint_fetch max_bytes is host-only and correctly validated/threaded  
SEVERITY: LOW  
FILE: collaborator/session.py:Session.__init__; collaborator/governance.py:execute_and_verify; collaborator/tools.py:execute_tool, _exec_maint_fetch; collaborator/egress.py:fetch_to_file  

CONCRETE BUG OR ATTACK:  
- `Session.__init__`:
  - Accepts `maint_fetch_max_bytes`.
  - Enforces: if `None`, uses `DEFAULT_MAINT_MAX_BYTES`. If boolean or non-int → `ValueError`. If `<= 0` → `ValueError`. Only positive ints are allowed (session.py, lines around the maint comments).
- Governance threads the host-side value:
  - `maint_max = getattr(session, "maint_fetch_max_bytes", None) or egress.DEFAULT_MAINT_MAX_BYTES`.
  - Passes `maint_max_bytes=maint_max` into `execute_tool` (governance.py, `execute_and_verify`).
  - Model args never contain `max_bytes`; it’s not part of the tool schema or args.
- `execute_tool` special-cases `tool.name == "maint_fetch"` and calls `_exec_maint_fetch(..., max_bytes=maint_max_bytes)`; other tools ignore that parameter.
- `_exec_maint_fetch` passes `max_bytes` through to `egress.fetch_to_file`, and `fetch_to_file` uses that in the streaming ceiling, as discussed in MF-01.
- Human-gating:
  - Tool definition: `Tool("maint_fetch", ..., mutating=False, ..., default_leash=PROPOSE_FIRST, egress=True, egress_method="MAINT")`.
  - Tests (`Seam.test_granted_is_human_gated_by_default`) confirm that with a maint cap present, the initial decision status is HELD, not RAN; there is no `net.maint.auto` path.

WHETHER ANOTHER CONTROL CATCHES IT:  
- Even if `maint_fetch_max_bytes` were misconfigured to an overly large value, the hard-coded default would still cap artifacts at 100 MiB. But correctness in config validation means this misconfig is surfaced at Session construction rather than silently weakening the cap.

FIX:  
- No fix required.  
- Optional improvement: add a governance-level log line whenever maint_fetch runs, including the effective `maint_max_bytes`, to help operators audit configuration and detect unexpected ceilings.

STEELMAN:  
The maintenance artifact ceiling is owned entirely by the host: it’s validated at session creation, threaded through governance, and never appears in tool arguments, so the model cannot widen its own limits. Maint_fetch is always human-gated (`PROPOSE_FIRST`) with no `auto` path, and because `_exec_maint_fetch` exclusively trusts the host-validated `max_bytes` parameter, the size of artifacts is bounded by operator policy, not by model influence. This preserves the intended fail-closed behavior even under adversarial model behavior.  

---

ID: MF-06 / TITLE: fetch_to_file faithfully reuses the Tier-1 transport contract  
SEVERITY: LOW  
FILE: collaborator/egress.py:fetch_to_file (vs fetch, post); tests: FetchToFileTransport  

CONCRETE BUG OR ATTACK:  
- Canonical host and scheme restrictions:
  - `host = canonical_host(url)`; rejects non-https, bad host, userinfo, non-443 ports, IP-literal with unsafe patterns, etc.—identical to `fetch`.
  - If `host is None`, returns a `_refused` record and never calls `resolver`.
- Request-target cleanliness:
  - Uses the same `urlsplit(url.strip())`, constructs `target` with path+query, then enforces `len(target) <= MAX_URL_TARGET` and `_is_clean_request_target(target)` (control & ASCII). Same as `fetch`.
  - Hash and byte length are computed identically (`target_hash`, `request_bytes`).
- IP safety & pinning:
  - Resolution uses `resolver(host)` under try/except and finds first `is_safe_public_ip(ip)`; otherwise `_refused` with "no safe public IP". Same as `fetch` and `post`.
  - Connection is either `_PinnedHTTPSConnection` or injected `connection_factory`, with SNI bound to `host`.
- Redirect / non-2xx:
  - For 3xx, calls `_sanitize_location` and `_refused` (with `redirect` set and no body read as artifact).
  - For non-2xx, reads up to a bounded amount (`DEFAULT_MAX_RESPONSE + 1`) and then refuses; it never writes to sink.
- Header safety:
  - Explicit headers: `Host`, `User-Agent`, `Accept`, `Connection`. No model-controlled headers are allowed, and there is no path for Authorization/Cookie injection here.
- Never-raises behavior:
  - All network/TLS/http exceptions (`ssl.SSLError`, `OSError`, `http.client.HTTPException`) are caught and converted to `_refused` results with error messages; the tests (`FetchToFileTransport.test_never_returns_none_or_raises_on_junk`) validate that junk URLs produce an `EgressResult` with `ok=False` instead of exceptions.
- Chunked/EOF behavior:
  - The `_StreamResp` test helper ensures `read(n)` advances the cursor; unit tests verify multi-chunk reassembly and over-cap mid-stream behavior.
  - Zero-length `chunk` (EOF) breaks the loop; there’s no infinite loop on empty reads.

WHETHER ANOTHER CONTROL CATCHES IT:  
- The egress_observer (`_observe_begin`/`_observe_end`) is only wrapped at the tool executor; it doesn’t correct for any problems in fetch_to_file, but it can detect inconsistent destination/IP use if another egress path existed. Here, the path is clean.

FIX:  
- No fix required.  
- Optional: consider factorizing shared logic with `fetch` into a common helper to reduce risk of future drift (currently duplicated but consistent).

STEELMAN:  
The streaming client preserves all the core protections of the Tier-1 GET path: canonical_host is the single source of truth for both capability key and connect host, redirects fail-closed, unsafe IPs are blocked via a pinned resolution, and ASCII/control-char guards prevent header/request smuggling. Non-2xx responses are treated as error pages and never staged, and all network/TLS errors degrade cleanly into non-ok records without leaking exceptions out of the tool boundary. This ensures maint_fetch’s streamed transport is no weaker than the existing fetch/post contract.  

---

ID: MF-07 / TITLE: Partial artifact cleanup on errors, including sink.write() failures, is robust  
SEVERITY: LOW  
FILE: collaborator/egress.py:fetch_to_file; collaborator/tools.py:_exec_maint_fetch; tests: Executor  

CONCRETE BUG OR ATTACK:  
- Egress-side:
  - Any `sink.write(chunk)` that raises `OSError` is caught by `fetch_to_file`’s `except (ssl.SSLError, OSError, http.client.HTTPException)` and converted into a `_refused` EgressResult with `ok=False`.
  - In such a case, the file descriptor is still open inside `_exec_maint_fetch`’s `with open(target, "wb") as sink:` context.
- Executor-side:
  - `_exec_maint_fetch` has a broad `except OSError as exc` around both `os.makedirs(...)` and the `with open(...)`/`egress.fetch_to_file(...)` call. On any OSError (including disk full, permission errors, etc.), it unlinks the target (`_unlink_quiet(target)`) and returns an EgressRecord built via `_refused`.
  - For non-ok egress results (including over-cap, network error, or write error), `if not rec.ok: _unlink_quiet(target)` runs after the `with` block, guaranteeing the file is closed before unlink.
- Tests (`Executor.test_non_ok_deletes_partial_artifact`) explicitly simulate a partial write from a fake `fetch_to_file` and assert that the resulting file does not exist in the workspace.

WHETHER ANOTHER CONTROL CATCHES IT:  
- Even in the unlikely event that unlink fails (e.g., OS-level issues), the egress record stays `ok=False`, and the governance path treats the operation as failed; an operator wouldn’t mistake the state for a successfully staged artifact.

FIX:  
- No fix required.  
- Optional: log unlink failures (currently swallowed) in a debug or audit channel; they’re unlikely but might be useful for diagnosing filesystem problems.

STEELMAN:  
The code carefully ensures that any error during streaming—including disk-write errors—results in a non-ok egress record and prompt deletion of any partial file once the descriptor is closed. The tests explicitly validate deletion of partial artifacts, and no success path exists that both leaves a partial artifact on disk and reports `ok=True`. This ensures that consumers and operators never mistake a truncated or partially written file for a valid, fully staged artifact.  

---

VERDICT: SOUND  

The shipped maint_fetch implementation is structurally faithful to its ADR: it reuses the proven egress contract, enforces a host-owned streaming ceiling with fail-closed semantics, maintains strict separation of authority via `net.maint:<host>`, binds approved==executed through seals and frozen args, and correctly fences destinations to the workspace. The single highest-value change would be to factor shared logic between `fetch` and `fetch_to_file` into a common helper to prevent future drift and preserve today’s equivalence of safety checks as the code evolves.
