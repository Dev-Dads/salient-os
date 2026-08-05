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

1. **Verifier** — built and red-teamed (`salienceos/verifier`)
2. **Salience bus + central interpreter** — built and red-teamed (`salienceos/interpreter`)
3. **Control seam** — the interpreter↔verifier integration, built and red-teamed (`salienceos/control`)
4. Everything else sits on the existing stack (Hermes, CDMS, Salient-Tuning, quorum_core)

## The control seam

`salienceos/control` is where the two components meet: subsystems publish salience →
`interpret()` issues a `Directive` → the directive's verification depth governs how hard
the `Verifier` checks the executed action → a `GovernedOutcome` gates clearance and
adaptation. It reconciles the two verification vocabularies (interpreter depth 0–3 ↔
verifier `Stakes`) and holds three invariants at once:

- **salience only escalates** verification (`max_stakes`, upward-only) — never weakens the
  policy-signed floor;
- **fail-closed clearance** — `required` is floored by both the directive depth and the
  verdict's effective stakes; a conclusive failure never clears;
- **adaptation is a sealed learning gate** — allowed only on a directive-eligible action
  that the verifier returned a real `VERIFIED` for.

`decide()` is the pure spine; `verify()` gained a small upward-only `escalate_to`, and the
`Verdict` is self-describing (`envelope_id` + `effective_stakes` stamped by the pipeline)
so the gate binds to and reads from it with no desyncable free parameters. Reviewed hardest
of all the components — three internal subagent passes and two five-model panels; see
`red-team/control/`.

## The bus + interpreter

`salienceos/interpreter` is the directive analog of the verifier's composer.
Subsystems each compute salience their own way and publish a thin `SalienceSignal`
(comparable influence + confidence + provenance + subsystem-id — structurally
incapable of carrying authority) onto a `SalienceBus`. The pure, fail-closed
`interpret()` reads them and issues one `Directive`, bounded by a signed
`PolicyCaps`. It is the single choke point, under **salience influences; policy
authorizes**:

| Concern | Where |
|---|---|
| Thin bus contract (private per-subsystem scoring; bounded ref-shaped tokens) | `signal.py` (`SalienceSignal`, `valid_signal`), `scorers.py` (two heterogeneous example scorers) |
| Authority envelope — capabilities and every knob bound come only from here | `policy.py` (signed `PolicyCaps`) |
| The pure fail-closed choke point (the mutation target) | `interpreter.py` (`interpret()`; untrusted policy ⇒ hard-deny to empty capabilities) |
| Directive — `allowed_capabilities` is a verbatim policy pass-through | `directive.py` (`grants_capability()`, no signal path to authority) |
| Audit surface — append-only, hash-chained, structurally body-free | `bus.py` (`SalienceBus`, `verify_chain()`) |
| P-01 leak-locks + fail-closed + adaptation gating | `tests/test_no_laundering.py`, `tests/test_interpret.py`, `tests/test_interp_review_fixes.py` |

Adaptation reaches at most `CANDIDATE` (never live self-modification), and only
with the policy switch plus sufficient applied verification and low-enough risk.
Reconfiguration prefers **between-turn** (Finding F). Red-team (two internal
subagent reviews + the five-model panel) in `red-team/interpreter/`.

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
and the regressions in `tests/test_redteam_fixes.py`. The interpreter went through the same
process (plus two internal subagent reviews) — see `red-team/interpreter/`.

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
