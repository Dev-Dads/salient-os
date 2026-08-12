# provenance-store code panel: openai/gpt-5.1

_finish=stop seconds=45.5 cost=$0.0657 usage={'prompt_tokens': 23307, 'completion_tokens': 3653, 'total_tokens': 26960, 'cost': 0.06566375, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.06566375, 'upstream_inference_prompt_cost': 0.02913375, 'upstream_inference_completions_cost': 0.03653}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID 1 / TITLE: Unsafe default `policy_key` allows full provenance forgery  
SEVERITY: CRITICAL  
LOCATION: collaborator/session.py:71 (Session.__init__ default), collaborator/provenancestore.py:21–29  

CONCRETE CASE  
- Environment: host uses the shipped defaults, i.e. `Session(..., policy_key=b"collab-policy-key")` everywhere (as also reflected in tests: `KEY = b"collab-policy-key"` in tests/test_collaborator_provenancestore.py:19).  
- The agent (in the contained run) cannot reach the store file, but a deceived human is inside the workspace. The model outputs a small Python script to “debug provenance,” which the human runs uncontained (`python helper.py`), giving that script full process power.  
- That script can trivially open any provenance store path outside the workspace (user knows/was told where it is), and it knows the HMAC key value from the public code/tests (`b"collab-policy-key"`). It can then:  
  - Call `_canonical` with any `subject` and `authored` it wants and `_mac(KEY, payload)` to produce a valid MAC; or  
  - Inline the code from `provenancestore.py` directly.  
- It writes a new `prov.json` with a `body` omitting some previously-tainted files and a correct `mac`. Because key and subject both match, `load()` will return `(forged_authored, forged_incomplete, ok=True)` and the new Session will treat provenance as trusted, non-degraded, and missing those taints.  

WHY IT FORGES/TAMPERS/FAILS-SILENT/BREAKS-TOTALITY  
- This defeats the store’s stated integrity goal: “So the agent cannot FORGE a ‘clean’ store” and “host’s `policy_key` — an in-process secret … unreachable by the contained run.”  
- With a globally-known, hard-coded default key, any code the model convinces a human to run with host privileges can forge a store that `load()` will accept as clean (`ok=True`, no degradation), silently erasing taints. It is not just “trusted human maintenance” here: it is the adversarial code path the whole F2 control is trying to bound.  
- The forgery is *indistinguishable* from a genuine store; there is no fail-honest degradation, and no error is raised.  

WHETHER ANOTHER CONTROL CATCHES IT  
- No.  
  - The integrity check in `provenancestore.load` is exactly what is being subverted by reusing the known `policy_key`.  
  - `_assert_store_outside_workspace` only controls write reach from the contained run, not from human-run adversarial code.  
  - There is no per-host secret management for `policy_key`; all examples and tests encourage the insecure default.  

FIX  
- Treat `policy_key` as a *required host secret*, not a public default:  
  - Remove the default value from `Session.__init__` (require the host to pass a high-entropy key) or, at minimum, generate a random key on first use and persist it in a host-controlled location outside any workspace.  
  - Update tests so `KEY` is a test-only constant, and never encourage reuse of `b"collab-policy-key"` in real deployments.  
  - Add a defensive runtime check: if `policy_key` equals any known default/test key, log loudly / refuse to enable a provenance_store, falling back to in-memory only.  


ID 2 / TITLE: `subject` not bound into MAC input in the intended way creates a weaker-than-claimed binding  
SEVERITY: MEDIUM  
LOCATION: collaborator/provenancestore.py:11–18, 25–29  

CONCRETE CASE  
- Threat model says: “Integrity is an HMAC-SHA256 over canonical JSON (sorted authored set + subject + incomplete flag) … plus a workspace-SUBJECT binding.” `_canonical` currently serializes a dict `{"v": ..., "subject": str(subject), "authored": ..., "incomplete": ...}` and that whole JSON is MACed.  
- However, `load()` computes `payload = _canonical(body.get("subject"), body.get("authored") or [], body.get("incomplete"))` and then separately checks `if str(body.get("subject")) != str(subject): return set(), True, False`.  
- Because the MAC is over `body["subject"]` (inside the signed `body`) but the “actual” `subject` passed to `load` is *outside* the signed structure, a local attacker that can write arbitrary JSON with a valid MAC (via the weak-key issue above, or if they ever get a compromised `policy_key`) can construct multiple-on-disk subject variants that still pass `compare_digest` for their own `body["subject"]`, then arrange for the host to call `load(path, subject="workspace:B")` on a store that was originally generated for `"workspace:A"`.  

WHY IT FORGES/TAMPERS/FAILS-SILENT/BREAKS-TOTALITY  
- The code *does* separately check equality of `body["subject"]` and the caller’s `subject`, so under normal, honest use this prevents cross-workspace replay; the issue is that the subject being checked isn’t what’s described in the comment as “bound into the MAC” in the sense of “the caller’s subject is part of the signed context.” The effective binding relies entirely on that extra equality check, not on a joint canonicalization of (caller_subject, body.subject, authored, incomplete).  
- This is weaker than the stated guarantee (“MAC … plus a workspace-SUBJECT binding”): if something else ever manages to bypass or incorrectly call `load` with a mismatched `subject`, the MAC verification alone will not protect against cross-subject replay, because the MAC is still valid for the embedded `body["subject"]`. This is not presently exploitable without the key (so it is second-order to ID 1), but it is a discrepancy between claim and mechanism.  

WHETHER ANOTHER CONTROL CATCHES IT  
- The explicit `if str(body.get("subject")) != str(subject):` equality check *does* catch simple cross-subject replays in today’s call-sites; so in practice you don’t immediately get a replay bug.  
- However, nothing enforces that every caller always does that external check; the provenance_store API is the only consumer today, but the design is brittle.  

FIX  
- Make the subject binding structurally non-bypassable:  
  - Change `_canonical`’s signature to take the *caller’s* `subject` value and never accept a `subject` embedded inside `body`.  
  - When saving, compute `payload = _canonical(subject, authored_sorted, incomplete)` and store `subject` only at the top level (not duplicated under `body`). On load, reconstruct the same canonical bytes using the *caller-provided* `subject` and the decoded `authored`/`incomplete`, and verify the MAC using that; if it matches, you already know the MAC was computed for this exact subject.  
- This makes it impossible to reuse an old MAC for any other subject, even if call-sites are later refactored.  


ID 3 / TITLE: `policy_key` aliasing across identities allows one component to forge another’s provenance  
SEVERITY: MEDIUM  
LOCATION: collaborator/session.py:70–77; tests/test_collaborator_provenancestore.py:18–21  

CONCRETE CASE  
- The same `policy_key` default (`b"collab-policy-key"`) is used for multiple roles:  
  - As the provenance HMAC key (`policy_key` in `Session`, passed into `provenancestore`);  
  - As the verifier policy key (`Verifier(policy_key, {executor_id: executor_key})`);  
  - And in tests, `KEY = b"collab-policy-key"` is reused to validate stores.  
- Suppose the host later introduces another component or tool that also uses `policy_key` for signing/verifying *different* integrity-critical data (e.g., emission seals, policy envelopes) and exposes operations that, given arbitrary payload bytes and access to `policy_key` in memory, can produce HMACs. If the model can convince a human to expose such a signer (or reuse this key in scriptable contexts), it can forge provenance stores indirectly without *direct* access to the key — just by abusing this other signer that shares the same key.  

WHY IT FORGES/TAMPERS/FAILS-SILENT/BREAKS-TOTALITY  
- The threat model implicitly assumes that `policy_key` is an in-process secret not usable by the contained run. It does *not* consider the risk of key reuse across different logical domains where some domains might offer oracle access to HMAC signing. If that happens, the “agent cannot forge” claim becomes false: the agent can call the other component to sign arbitrary `_canonical` payloads and then write forged stores via a deceived human.  
- This is not a concrete exploit with the code shown, but it is a structural break of key separation: provenance integrity is no longer cryptographically independent from other signing domains. Under modest evolution of the codebase, this becomes a real forgery path.  

WHETHER ANOTHER CONTROL CATCHES IT  
- No explicit control enforces key separation. The same `policy_key` is threaded everywhere.  
- There is no check that forbids using `policy_key` as an ambient, general-purpose key for other signing tasks.  

FIX  
- Split the keys:  
  - Introduce a dedicated `provenance_key` distinct from `policy_key`, with separate configuration and generation, and use it *only* for `provenancestore._mac`.  
  - Ensure that any signing APIs available to untrusted or semi-trusted contexts never have access to `provenance_key`.  
  - Add comments / tests that assert `provenance_key != policy_key` and treat any accidental equality as a configuration error.  


ID 4 / TITLE: `Session` will happily start with a silently *non-durable* provenance store when `save()` always fails  
SEVERITY: LOW  
LOCATION: collaborator/session.py:174–181, 236–247; collaborator/provenancestore.py:36–53  

CONCRETE CASE  
- Host configures `provenance_store` to a path on a read-only filesystem (or a directory where the process has no write permissions), but one that is still *resolvable* and outside the workspace, so `_assert_store_outside_workspace` passes.  
- `Session.__init__` calls `provenancestore.load(...)` which succeeds (file may or may not exist), and then sets `_provenance_store` to that path.  
- During the session, `note_autonomous_authorship` and `mark_tracking_incomplete` call `_persist_provenance`, which in turn calls `provenancestore.save(...)`. `save()` catches all exceptions and returns `False` on failure, but `_persist_provenance` ignores the return value. The session and tests both treat durability as if it were working.  
- A later session is started with the same `provenance_store` path; it loads an out-of-date or empty file, seeing no taints tracking the previous session’s autonomous writes.  

WHY IT FORGES/TAMPERS/FAILS-SILENT/BREAKS-TOTALITY  
- This is not a cryptographic forgery, but it *silently* under-delivers on “DURABLE store” semantics: the API accepts a misconfigured, unwritable store and gives no host-visible indication that persistence is failing. The human believes cross-session provenance is being recorded, but it is not.  
- Given the stated posture (“best-effort durability; in-memory manifest stays authoritative”), this is partially intentional, but there is a subtle mismatch: failure to persist is *never* surfaced even as an advisory degradation flag, so operators cannot distinguish “durable provenance is on” from “we tried but every save() failed.” It doesn’t break Session.totality, but it breaks honest signaling about durability.  

WHETHER ANOTHER CONTROL CATCHES IT  
- No. There is no logging or flag when `save()` returns `False`.  
- `_autonomous_tracking_incomplete` is only about *capture* gaps, not durability gaps; it is not set on `save` failure.  

FIX  
- Keep `save()` total, but wire its failure into an advisory degradation signal:  
  - Have `_persist_provenance` check the return value; if it is `False`, set a separate `_provenance_persistence_failed` flag and potentially surface that in the UI alongside `_autonomous_tracking_incomplete`.  
  - Optionally, on `Session.__init__`, perform a one-time test write (e.g., write an unchanged store) and log or raise a configuration error if persistence is impossible, so operators know their “durable” mode isn’t actually durable.  


ID 5 / TITLE: Concurrent sessions sharing one store risk last-writer-wins provenance loss  
SEVERITY: LOW  
LOCATION: collaborator/provenancestore.py:36–53; collaborator/session.py:236–247, 295–320  

CONCRETE CASE  
- Host launches two `Session` instances pointing at the same `provenance_store` path and same workspace. Both load the same initial set of taints.  
- Session A notes autonomous authorship of `["a.sh"]`, calling `_persist_provenance` and saving a store with `authored={"a.sh"}`.  
- Concurrently, Session B notes autonomous authorship of `["b.sh"]`, using its *own in-memory* `_autonomous_authored` (which does not include `"a.sh"` because it never reloaded after A’s write). It then saves a store with `authored={"b.sh"}`, overwriting A’s `"a.sh"` taint; both writes are MACed correctly.  
- A later session C loads the file and sees only `{"b.sh"}`, with `ok=True` and no degraded flag; `a.sh`’s autonomous authorship is silently lost.  

WHY IT FORGES/TAMPERS/FAILS-SILENT/BREAKS-TOTALITY  
- This is an honest data race, not an adversarial cryptographic break, but it does undercut the intended “accumulative” provenance property when multiple sessions share a store: newer taints from one session can overwrite, rather than union with, taints from another. There is no signaling that the store’s completeness has been compromised.  
- The MAC protects integrity of whatever is stored, but not semantic monotonicity; under concurrency, that produces a fake “clean” view for some files despite no tampering.  

WHETHER ANOTHER CONTROL CATCHES IT  
- No control detects or flags concurrent access; `os.replace` is atomic per write, but there is no read–modify–write coordination.  
- `_autonomous_tracking_incomplete` is only set on snapshot failures or untrusted loads, not on concurrent last-writer-wins races.  

FIX  
- Document clearly that a single provenance_store path must not be shared concurrently across live sessions; or  
- Implement a simple cooperative merge-on-save strategy:  
  - On `_persist_provenance`, read the current store (if trusted), union its `authored` set with the in-memory `_autonomous_authored`, and then write that union (maintaining monotonicity) instead of blindly writing only the local set.  
  - Optionally, include a monotonic version counter in the signed body to detect and flag non-mergeable concurrent updates.  


STEELMAN (defense of the design)  
- The core durability and integrity mechanisms are fairly clean: the store is HMAC-protected with a canonical serialization and bound to a per-workspace subject, and `load()` is total and fail-honest, always treating any corrupt or mismatched store as empty+degraded rather than “clean.” The workspace-versus-store separation via `_assert_store_outside_workspace` directly enforces the main threat boundary (contained run cannot reach the store), and the Session wiring consistently maps `ok=False` into `_autonomous_tracking_incomplete=True`, so tampering never silently passes as trusted state.  
- The implementation also carefully avoids raising in governance paths, and uses atomic `os.replace` with a temp file to avoid partial-write/torn-write issues on single-process saves. Within a single well-configured host using a strong secret key, the provenance store behaves as advertised: durable across sessions, advisory, and resistant to direct agent tampering.  

VERDICT: SERIOUS_FLAWS  
- The single highest-impact issue is that `policy_key` is shipped and demonstrated as a fixed, low-entropy default, which makes the “HMAC integrity” claim largely illusory once the model can get any host-privileged code execution: forging a trusted “clean” store is then trivial.  
- Highest-value fix: make `policy_key` (or a dedicated `provenance_key`) a required, high-entropy host secret with no insecure default, and refuse to enable durable provenance when the key equals any known test/default value.
