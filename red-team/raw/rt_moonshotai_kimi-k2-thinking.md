# Red-team: moonshotai/kimi-k2-thinking

_finish=stop seconds=440.9 usage={'prompt_tokens': 18011, 'completion_tokens': 16049, 'total_tokens': 34060, 'cost': 0.0509291, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0509291, 'upstream_inference_prompt_cost': 0.0108066, 'upstream_inference_completions_cost': 0.0401225}, 'completion_tokens_details': {'reasoning_tokens': 14299, 'image_tokens': 0, 'audio_tokens': 0}}_

- ID: F1
- TITLE: Prefix-based evidence filtering allows cross-envelope receipt_integrity contamination
- SEVERITY: MEDIUM
- LOCATION: `salienceos/verifier/pipeline.py`, `Verifier.verify`, lines filtering `self.claim_store.items()` and `self.world_store.items()` by `prefix = envelope.envelope_id + ":"`
- CONCRETE TRIGGER: 
  1. Policy issues two envelopes: `envelope_id="task-123"` and `envelope_id="task-123:sub"`.
  2. Verify the second envelope first with an authentic receipt; this appends a `ClaimEvidence(kind="receipt_integrity", value="authentic", obligation_id="task-123:sub:receipt_integrity")` to the `ClaimStore`.
  3. Verify the first envelope with an **unauthentic** receipt (bad signature) and no world evidence.
  4. The prefix filter `e.obligation_id.startswith("task-123:")` matches both `"task-123:..."` and `"task-123:sub:..."`, pulling in the second envelope's receipt_integrity claim.
  5. `compose()` calls `_receipt_attested(claims)` which returns `True` due to the stray claim, attaching `Reason.INTEGRITY_ATTESTED` to the verdict even though the receipt being verified is unauthentic.
- WHY IT MATTERS under the stated threat model: Spec M5 states `INTEGRITY_ATTESTED` must only attach when "the receipt was authentic and self-consistent." A buggy policy ID generator (non-malicious corruption) could produce IDs where one is a prefix of another, causing the verifier to misattribute attestation status. While the top-level status remains `UNVERIFIED`, consumers using `require_attested()` to handle attested-but-unverified receipts explicitly (e.g., for manual review workflows) would be misled into treating an unauthentic receipt as integrity-attested, potentially bypassing security checks intended for unauthentic receipts.
- SUGGESTED FIX: Filter evidence by exact envelope_id match instead of prefix:
  ```python
  claims = tuple(
      e for e in self.claim_store.items()
      if e.obligation_id.split(":")[0] == envelope.envelope_id
  )
  ```
  Similarly for `world`. Alternatively, pass `envelope_id` to `_receipt_attested()` and check that the claim's `obligation_id` matches `f"{envelope_id}:receipt_integrity"` exactly.

- ID: F2
- TITLE: Contract builder crashes on malformed args instead of returning UNVERIFIED
- SEVERITY: LOW
- LOCATION: `salienceos/verifier/contract.py`, `_build_file_write`, line `envelope.args["content"].encode("utf-8")`
- CONCRETE TRIGGER: An envelope with `args["content"]` set to an integer (e.g., `123` instead of `"hello"`) causes `AttributeError: 'int' object has no attribute 'encode'` when `build_contract` is called. The `try/except KeyError` block does not catch `AttributeError`, `TypeError`, etc.
- WHY IT MATTERS under the stated threat model: Under a buggy executor that produces malformed envelopes (non-malicious corruption), the verifier crashes rather than returning `UNVERIFIED(NO_CONTRACT)`. This is an availability issue and violates the fail-closed principle for the contract-building phase. It does not produce a false `VERIFIED`, but it prevents the system from gracefully handling corrupted input.
- SUGGESTED FIX: Change the exception handling in `build_contract` to catch all exceptions:
  ```python
  def build_contract(envelope: ActionEnvelope):
      builder = _OP_BUILDERS.get(envelope.op)
      if builder is None:
          return None
      try:
          obligations = builder(envelope)
      except Exception:
          return None  # Fail closed on any malformed args
      return Contract(...)
  ```

- ID: F3
- TITLE: Subprocess observer lacks timeout exception handling
- SEVERITY: LOW
- LOCATION: `salienceos/verifier/observers.py`, `run_supervised`
- CONCRETE TRIGGER: A supervised job that exceeds `timeout_seconds` (default 120) raises `subprocess.TimeoutExpired`, which propagates uncaught through `verify()`.
- WHY IT MATTERS under the stated threat model: A buggy executor that hangs causes the verifier to crash rather than returning `UNVERIFIED` or `FAILED`. This is an availability issue, not a false `VERIFIED`.
- SUGGESTED FIX: Wrap `subprocess.run` in a try/except block to catch `TimeoutExpired` and return a `SupervisedResult` with a non-zero returncode (e.g., `-1`) or raise a specific exception that the pipeline catches to return `UNVERIFIED`.

- ID: F4
- TITLE: Test coverage gap for `path_state` obligations
- SEVERITY: LOW
- LOCATION: `tests/test_mutation_fixtures.py`, `test_coverage_gate_every_obligation_kind_trips_some_mutant`
- CONCRETE TRIGGER: The coverage gate only tests `file.write` (which uses `artifact_hash`), but `dir.make` and `file.delete` operations use `path_state` obligations which are not exercised by the mutation fixtures.
- WHY IT MATTERS under the stated threat model: While the implementation appears correct, the lack of mutation testing for `path_state` means a regression (e.g., a bug in `_build_dir_make` or `_build_file_delete` that drops the obligation) would not be caught by the "coverage gate" test, violating the spec's requirement that "for every named invariant in a contract, some mutant must trip some check."
- SUGGESTED FIX: Add test cases for `dir.make` and `file.delete` operations in the coverage gate, or extend the existing mutants to cover these operation types.

- ID: F5
- TITLE: Path traversal in host-namespace observers via absolute paths
- SEVERITY: LOW (OUT-OF-SCOPE for kernel-capable attacker, relevant for buggy policy)
- LOCATION: `salienceos/verifier/observers.py`, `rehash` and `path_state` functions
- CONCRETE TRIGGER: An envelope with `args["path"]` set to an absolute path (e.g., `/etc/passwd`) causes `Path(root) / "/etc/passwd"` to resolve to `/etc/passwd` (absolute paths override the root), causing the verifier to read arbitrary files.
- WHY IT MATTERS under the stated threat model: Under a buggy policy system that fails to sanitize paths (non-malicious corruption), the verifier reads outside the workspace. However, under the binding threat model, the executor is trusted-not-hostile and the policy key is trusted; a malicious path would require a compromised policy signer, which is out of scope. This is primarily a defense-in-depth issue.
- SUGGESTED FIX: Validate that `path` is relative and does not contain `..` components before joining:
  ```python
  def _safe_path(root: Path, path: str) -> Path:
      p = Path(path)
      if p.is_absolute() or ".." in p.parts:
          raise ValueError("Path must be relative and normalized")
      return root / p
  ```

---

**STEELMAN:** The implementation rigorously enforces the CLAIM/WORLD separation through exact type checks (`type(e) is ClaimEvidence`) and physically separate stores, making the type fence auditable and resistant to refactoring errors. The composer is a pure function with explicit fail-closed logic (every early path returns UNVERIFIED), and the sealed consumer gate (`ReceiptStore.record`) correctly prevents the "attested success" laundering attack described in the spec by rejecting any `reported_success=True` receipt that lacks a `VERIFIED` verdict.

**VERDICT:** MINOR_ISSUES. The implementation correctly upholds the core invariant M1 (no false VERIFIED without independent world fact) and the fail-closed property. The identified issues are localized to the attribution of the INTEGRITY_ATTESTED sub-code under specific ID collision scenarios (MEDIUM), availability under malformed inputs (LOW), and test coverage gaps (LOW), none of which allow a false VERIFIED under the binding threat model.
