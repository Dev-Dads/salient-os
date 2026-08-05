# Red-team: x-ai/grok-4.5

_finish=stop seconds=216.2 usage={'prompt_tokens': 19863, 'completion_tokens': 10769, 'total_tokens': 30632, 'cost': 0.1041224, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1041224, 'upstream_inference_prompt_cost': 0.0395084, 'upstream_inference_completions_cost': 0.064614}, 'completion_tokens_details': {'reasoning_tokens': 8604, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

- ID: F1
- TITLE: `Verifier` evidence stores accumulate across calls; stale WORLD facts can yield a false `VERIFIED`
- SEVERITY: HIGH
- LOCATION: `salienceos/verifier/pipeline.py` — `Verifier.verify` (claim/world `extend` + filter by `envelope_id` prefix only)
- CONCRETE TRIGGER:
  ```python
  v = Verifier(policy_key, {EXECUTOR_ID: EXECUTOR_KEY})
  env = write_envelope("env-reuse", "out.txt", "hello world")
  receipt1, world_ok = ...  # honest run, host re-hash matches
  assert v.verify(env, receipt1, world_ok).status is Status.VERIFIED

  # Buggy executor (or integrator) later presents a new success receipt for the
  # SAME envelope_id with NO fresh world observation:
  receipt2 = honest_receipt("rcpt-2", env, "hello world", reported_success=True)
  # workspace may even be empty / file deleted
  assert v.verify(env, receipt2, []).status is Status.VERIFIED  # FALSE VERIFIED
  ```
  `verify` always `extend`s stores, then selects evidence with only:
  `e.obligation_id.startswith(envelope.envelope_id + ":")`.
  Prior `WorldEvidence` for that envelope remains and still satisfies `_agreement_gap`.
- WHY IT MATTERS under the stated threat model: In-scope buggy executor / partial failure / misfiring glue can replay or re-issue a success receipt without re-doing the action. The core M1 invariant (“≥1 executor-independent world fact” for *this* attempt) is violated because the world fact is stale and not bound to the current verification. This is not a kernel-spoofing attack; it is ordinary API reuse.
- SUGGESTED FIX: Scope evidence per verification attempt (build ephemeral lists and pass those to `compose`), or key stored evidence by `(envelope_id, receipt_id)` / verification nonce and select only that slice. Do not compose over the entire append-only history of an envelope.

---

- ID: F2
- TITLE: Host write-set observer ignores directories — `dir.make` floor cannot be met; dir boundary gaps
- SEVERITY: MEDIUM
- LOCATION: `salienceos/verifier/observers.py` — `snapshot_tree` / `observed_write_set`; contract floor in `contract.py` `_build_dir_make`
- CONCRETE TRIGGER:
  - Envelope `op="dir.make", args={"path": "foo"}` → contract expects `write_set == '["foo"]'` and `path_state == "present:dir"`.
  - Real supervisor creates empty directory `foo`.
  - `snapshot_tree` walks **files only**, so pre/post are identical → `write_set_evidence` value `'[]'`.
  - Composer: world `[]` ≠ expectation `["foo"]` → `FAILED` (or never `VERIFIED`).
  - Undeclared directory creation is likewise invisible to the boundary check.
- WHY IT MATTERS: Always-on M2/M3 floor (side-effect + write-set boundary) is wrong for directory ops. Not a false `VERIFIED` with the stock observers (fail-closed / stuck `FAILED`), but the envelope-derived floor is unsatisfiable and undeclared dir side effects are not observed. No pipeline test covers `dir.make`.
- SUGGESTED FIX: Include directories (and/or mount/namespace metadata) in snapshots, e.g. map dir paths → type tag; define write-set as changes over files **and** dirs. Add mutation fixtures for `dir.make` / undeclared directory creation.

---

- ID: F3
- TITLE: Absolute / escaping paths in artifact observers bypass workspace root
- SEVERITY: MEDIUM
- LOCATION: `salienceos/verifier/observers.py` — `rehash`, `path_state` (`Path(root) / path`)
- CONCRETE TRIGGER:
  ```python
  Path("/workspace") / "/etc/passwd"   # → PosixPath('/etc/passwd')
  Path("/workspace") / "../outside"    # → escapes root
  ```
  Policy-signed envelope with `path` absolute or `..`-bearing (wrong/misfiring model + overly permissive policy) makes `artifact_evidence` / `path_state_evidence` read **outside** the workspace while `snapshot_tree(workspace)` only sees the workspace tree. Claim/world/write-set channels then refer to different path universes; outcomes depend on accidental host state, not the sandboxed action.
- WHY IT MATTERS: In-scope wrong model + authorized bad path. Does not require a compromised executor kernel. Can desynchronize the three always-on checks and produce misleading `FAILED`/`UNVERIFIED`, or agree on an unintended host file if write-set is satisfied loosely by other means.
- SUGGESTED FIX: Resolve with ` (root / path).resolve() ` and reject unless `resolved == root` or `root in resolved.parents`; normalize obligation subjects to workspace-relative posix paths before hashing/signing expectations.

---

- ID: F4
- TITLE: `build_contract` fail-closed only on `KeyError`; other malformed args crash the pipeline
- SEVERITY: LOW
- LOCATION: `salienceos/verifier/contract.py` — `build_contract` (`except KeyError` only); builders e.g. `_build_file_write` (`.encode` on `content`)
- CONCRETE TRIGGER: `args={"path": "out.txt", "content": 123}` (missing/`content_sha256` not used) → `int` has no `.encode` → `AttributeError` propagates out of `Verifier.verify` instead of `UNVERIFIED(NO_CONTRACT)`. Same for non-iterable `declared_outputs`, etc.
- WHY IT MATTERS: Spec M3/M4: missing/malformed contract must be `UNVERIFIED`, never a partial success path. Crash is not `VERIFIED` (so not M1-critical) but breaks the explicit fail-closed verdict surface and can skip sealed-gate recording.
- SUGGESTED FIX: Catch `(KeyError, TypeError, AttributeError, ValueError)` (or validate args before build) and return `None`.

---

- ID: F5
- TITLE: `INTEGRITY_ATTESTED` attached whenever any unmet obligation exists and receipt is authentic — even if WORLD facts were present
- SEVERITY: LOW
- LOCATION: `salienceos/verifier/composer.py` — unmet branch after `_agreement_gap` + `_receipt_attested`
- CONCRETE TRIGGER: HIGH stakes, full always-on world set agreeing on values, authentic receipt → `UNVERIFIED` + `INSUFFICIENT_CHANNELS` **and** `INTEGRITY_ATTESTED`. Spec text: attested = authentic receipt but *no* independent world fact obtained. Here world facts were obtained; stakes gate failed.
- WHY IT MATTERS: Does not create `VERIFIED`. Can mislead consumers of `require_attested()` / metrics (treats “world present but insufficient” like “claim-only”).
- SUGGESTED FIX: Add `INTEGRITY_ATTESTED` only when there is no distinct-mode WORLD agreement on any required obligation (e.g. all gaps are `NO_WORLD_FACT` / `NO_DISTINCT_FAILURE_MODE`), not on `INSUFFICIENT_CHANNELS` alone.

---

- ID: F6
- TITLE: Prefix filter on `envelope_id` is not a security boundary (benign today because `_on` matches full `obligation_id`)
- SEVERITY: LOW
- LOCATION: `salienceos/verifier/pipeline.py` — `prefix = envelope.envelope_id + ":"`
- CONCRETE TRIGGER: Evidence for envelope_id `"project:task1"` is included in the tuples when verifying `"project"` (`"project:task1:exit_status".startswith("project:")`). `_on` still requires exact `obligation_id` equality, so this does **not** alone produce false `VERIFIED`.
- WHY IT MATTERS: Footgun if a future change aggregates by kind/channel without exact id match; interacts with F1’s long-lived stores.
- SUGGESTED FIX: Filter with `obligation_id == envelope_id or obligation_id.startswith(envelope_id + ":")` **and** parse/namespace ids so envelope ids cannot contain `:`, or select by explicit verification batch id (preferred with F1 fix).

---

No additional in-scope path was found that turns **only-CLAIM** or **type-smuggled** evidence into `VERIFIED`: store `type(item) is` checks, composer re-fence, distinct `failure_mode`, and sealed `ReceiptStore` behave as specified. Compromised-host fabrication of `WorldEvidence` is out of scope by §1.

---

## STEELMAN

The design correctly makes the pure composer the spine: separate CLAIM/WORLD types, dual fences, envelope-grounded expectations, floor checks, distinct-failure-mode agreement, and stakes-scaled channel counts are implemented in one fail-closed function with real mutation fixtures (byte-flip, exit laundering, wrong path, claim-only). M5 leak-locks (`__bool__`, `require_attested`, sealed success recording) are genuine and tested. For the binding threat model, host supervisor exit + re-hash + write-set is the right cheap independence story for file writes.

## VERDICT

**SERIOUS_FLAWS** — F1 is a concrete M1 break (false `VERIFIED` via stale WORLD evidence on `Verifier` reuse / receipt replay with empty world); F2–F3 are real grounding/observer gaps for non-file ops and paths, even though most fail closed rather than launder success.
