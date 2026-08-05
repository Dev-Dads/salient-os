# Red-team: z-ai/glm-4.6

_finish=stop seconds=65.6 usage={'prompt_tokens': 17920, 'completion_tokens': 1489, 'total_tokens': 19409, 'cost': 0.0119124, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 64, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0119124, 'upstream_inference_prompt_cost': 0.0089344, 'upstream_inference_completions_cost': 0.002978}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_



### Findings

**F1**
- **TITLE**: Composer accepts mismatched envelope/receipt pair without failing
- **SEVERITY**: HIGH
- **LOCATION**: `salienceos/verifier/pipeline.py`, `Verifier.verify`, lines 44-55
- **CONCRETE TRIGGER**: Provide a receipt whose `envelope_id` does not match the envelope passed to `verify`. The pipeline will still attempt to compose a verdict using the contract from the provided envelope and claims from the mismatched receipt, potentially leading to a false VERIFIED if the obligations happen to align by chance.
- **WHY IT MATTERS**: The spec requires the receipt to be bound to the envelope it claims to execute. A mismatch indicates a broken chain of custody and should force UNVERIFIED. Under the in-scope threat model (buggy executor/tool), a receipt could be replayed against a different envelope, causing an incorrect verification.
- **SUGGESTED FIX**: In `Verifier.verify`, after authenticating the receipt, explicitly check `if receipt.envelope_id != envelope.envelope_id: return Verdict(status=Status.UNVERIFIED, reasons=(Reason.NO_CONTRACT,), details=("envelope_id mismatch",))`.

**F2**
- **TITLE**: WorldEvidence constructor allows 'absent' string for artifact hash, bypassing contradiction detection
- **SEVERITY**: MEDIUM
- **LOCATION**: `salienceos/verifier/observers.py`, `rehash` function, lines 45-49
- **CONCRETE TRIGGER**: An artifact obligation expects a specific hash (e.g., from `file.write`), but the host-side observation finds the file missing. The `rehash` function returns the string `"absent"`. The composer's `_conclusive_contradiction` compares this string to the expected hash, which are unequal, so it correctly returns a contradiction. However, if the claim also reports `"absent"` (which it cannot for a write obligation), the contradiction would not fire. The issue is that the type system allows a string where a hash is expected, weakening the contract.
- **WHY IT MATTERS**: While the current composer logic catches this via expectation mismatch, it relies on string comparison rather than type safety. A future change could inadvertently treat `"absent"` as a valid hash value, causing a missed contradiction. This is a robustness gap against non-malicious corruption.
- **SUGGESTED FIX**: Change `rehash` to return `None` for a missing file. Update `_conclusive_contradiction` to treat `None` as a special indicator of absence and compare against the expectation appropriately (e.g., if expectation is a hash and world is `None`, it's a contradiction).

**F3**
- **TITLE**: Composer's distinct-failure-mode check can be satisfied by a single claim with no world evidence
- **SEVERITY**: LOW
- **LOCATION**: `salienceos/verifier/composer.py`, `_agreement_gap` function, lines 140-144
- **CONCRETE TRIGGER**: An obligation has a claim evidence but no world evidence. The code sets `target = o_claims[0].value`. Since `o_world` is empty, `matching` is empty, and the function returns `(Reason.NO_WORLD_FACT, ...)`. This correctly leads to UNVERIFIED. The flaw is that if a claim were somehow mis-categorized as world evidence (which the type fence prevents), the logic would not catch it. This is a LOW because the type fence is the primary defense.
- **WHY IT MATTERS**: This is a defense-in-depth observation. The core invariant is protected by the type fence, but the logic in `_agreement_gap` is not robust to a violation of that fence. It's a minor fragility.
- **SUGGESTED FIX**: In `_agreement_gap`, add an early check: `if not o_world: return (Reason.NO_WORLD_FACT, "no world evidence")`. This makes the intent clearer and fails closed even if the type fence were bypassed.

**F4**
- **TITLE**: `build_contract` for `shell.run` does not enforce declared_outputs are non-empty
- **SEVERITY**: LOW
- **LOCATION**: `salienceos/verifier/contract.py`, `_build_shell_run` function, lines 118-131
- **CONCRETE TRIGGER**: Create a `shell.run` envelope with `declared_outputs` set to an empty list `[]`. The contract builder will create floor obligations but no artifact_hash obligations. The composer will then fail with `MISSING_FLOOR` because no side-effect kind is present, resulting in UNVERIFIED. The behavior is correct, but the contract is arguably malformed.
- **WHY IT MATTERS**: The spec implies a `shell.run` should have declared outputs. While the system fails closed, allowing an empty list might mask a contract authoring error. This is a LOW because the outcome is still safe.
- **SUGGESTED FIX**: In `_build_shell_run`, if `declared_outputs` is empty, return `None` to force `NO_CONTRACT` and make the error more explicit.

**F5**
- **TITLE**: Test coverage gate does not assert the contract contains all expected kinds
- **SEVERITY**: LOW
- **LOCATION**: `tests/test_mutation_fixtures.py`, `test_coverage_gate_every_obligation_kind_trips_some_mutant` function, lines 108-135
- **CONCRETE TRIGGER**: If a new obligation kind were added to the `file.write` contract but no corresponding mutant were added, the test would still pass because `tripped` would only contain the kinds covered by the existing mutants, and `contract_kinds` would be compared against this incomplete set. The test only asserts that the mutants it has cover the kinds in the contract, not that all contract kinds are covered.
- **WHY IT MATTERS**: This weakens the mutation-testing guarantee. The spec requires that "for every named invariant in a contract, some mutant must trip some check." The test as written allows a new invariant to be added without a mutant, violating the spec.
- **SUGGESTED FIX**: Change the assertion from `self.assertEqual(tripped, contract_kinds)` to `self.assertTrue(contract_kinds.issubset(tripped), f"Missing mutants for kinds: {contract_kinds - tripped}")`.

### STEELMAN
The design correctly enforces the core invariant M1 by separating CLAIM and WORLD evidence into distinct types and stores, a robust choice against refactoring. The explicit, pure `compose` function is a strong, testable spine for the verification logic, and the sealed consumer gate effectively prevents laundering UNVERIFIED status into success. The always-on host-side observations provide a practical, low-cost source of independent evidence.

### VERDICT
MINOR_ISSUES: The implementation is sound against the binding threat model, with a few high- and medium-severity robustness gaps that do not violate the core invariants but should be addressed to harden the system against non-malicious failures and implementation drift.
