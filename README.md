# SalienceOS

A salience-based AI control plane: routing, memory, verification, compute
budget, and adaptation each spend effort in proportion to what matters, under
one invariant — **salience influences; policy authorizes**.

The design docs live at the repository root:

| Document | Role |
|---|---|
| `SialinceOS Design Doc.docx` | Full system spec (v0.1) — salience model, policy broker, services, DGX Spark deployment. |
| `SalienceOS_Design_Review_v0.2.md` | Consolidated external review — reframes the contribution as **bus + central interpreter + verifier**; sets the build order. |
| `00-VERIFIER-FINAL-RECOMMENDATION-v3.md` | **Authoritative build spec for the verifier** (three red-team rounds). What `salienceos/verifier` implements. |

## Status

Building per the review's priority order:

1. **Verifier** — in progress, spine complete (this repo)
2. Salience bus + central interpreter — not started
3. Everything else sits on the existing stack (Hermes, CDMS, Salient-Tuning, quorum_core)

## The verifier

`salienceos/verifier` is a separate-process, side-effect-free evidence
pipeline: typed, provenanced evidence folds into a three-state verdict
(`VERIFIED` / `UNVERIFIED` / `FAILED`) via an explicit, pure, fail-closed
aggregation predicate. Its one non-negotiable rule: a status reaches
`VERIFIED` only when at least one **world-side fact the executor did not
write** agrees, with a distinct failure mode, with the executor's claim.

Spec mandate → implementation:

| Mandate | Where |
|---|---|
| M1 — CLAIM/WORLD separation **by type**, not flag | `evidence.py` (`ClaimEvidence` / `WorldEvidence`, no shared base; stores reject the wrong exact type; composer re-fences) |
| M2 — cheap independent facts: supervisor exit, host re-hash, write-set diff | `observers.py` (dir-aware snapshots, workspace-escape guard, and an `observe_action` dispatcher covering `file.write` / `shell.run` / `dir.make` / `file.delete`; container-runtime observers slot in behind the same evidence contract on the Spark) |
| M3 — envelope-derived expectations + minimum-obligation floor | `envelope.py`, `contract.py` (missing/unknown contract ⇒ `UNVERIFIED`, never `VERIFIED`) |
| M4 — pure, fail-closed, versioned aggregation predicate | `composer.py` (`compose()` — the mutation-test target; stakes are policy-signed inside the envelope) |
| M5 — `INTEGRITY_ATTESTED` as un-launderable sub-code of `UNVERIFIED` | `verdict.py` (`require_attested()`, no `bool(verdict)`), `receipt.py` (sealed consumer gate) |
| §5 — mutation fixtures + coverage gate | `tests/test_mutation_fixtures.py` (byte-flip, exit laundering, wrong path, fence drop) |
| Finding H — stdlib-only, synchronous, zero deps | `tests/test_discipline.py` (AST-enforced) |

A five-model red-team (models outside the big three) found and I fixed six defects — one genuine
false-`VERIFIED` from stale evidence plus five lesser gaps; see `red-team/00-REDTEAM-SYNTHESIS.md`
and the regressions in `tests/test_redteam_fixes.py`.

Threat model (binding, spec §1): the in-scope adversary is a wrong/misfiring
model, buggy executor, or non-malicious corruption. This verifier detects bugs
and model error; it does **not** claim resistance to a malicious kernel-capable
executor — that requires hardware attestation and is explicitly out of scope.

Deferred per spec §6: advisory LLM reviewer, replay/eBPF checks, memoization
with epoch/TTL, external attestation, calibrated confidence. The type fence
means adding the advisory reviewer later cannot weaken the verdict path.

## Running tests

Zero dependencies; any Python ≥ 3.11:

```
python -m unittest discover -s tests -v
```

## Layout

```
salienceos/
  verifier/
    signing.py    canonical JSON, content addressing, HMAC sign/verify
    envelope.py   policy-signed action envelope; stakes sealed inside signature
    contract.py   ObligationBuilder: floor + envelope-derived expectations
    evidence.py   ClaimEvidence / WorldEvidence types + append-only hash-chained stores
    composer.py   compose() — the pure fail-closed predicate
    receipt.py    receipt ingress (claims) + sealed consumer gate
    observers.py  host-side WORLD observers (exit, re-hash, write-set diff)
    pipeline.py   Verifier: glue, signature checks, store appends
tests/            golden composer fixtures, the four §5 mutation fixtures,
                  leak-lock tests, stdlib/sync discipline test
```
