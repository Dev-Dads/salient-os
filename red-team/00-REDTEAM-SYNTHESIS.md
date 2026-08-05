# Verifier Red-Team — Synthesis (v0.1)

**Date:** 2026-08-05
**Under review:** `salienceos/verifier` (implements `00-VERIFIER-FINAL-RECOMMENDATION-v3.md`)
**Panel:** five coding models, all outside the big three, one call each, run blind (no cross-talk):
DeepSeek R1, xAI Grok-4.5, Qwen3-Coder, Moonshot Kimi-K2-Thinking, Zhipu GLM-4.6.

**Method note (the coordinator's lesson from v3, applied here).** Every finding below was
**re-run against the actual code** before being accepted or rejected (`red-team/verify_findings.py`).
An adversarial rubric measures "can a flaw be articulated," not "is the flaw real" — so raw
critiques are treated as *hypotheses*, and only empirically-reproduced ones are called confirmed.
Four plausible-sounding findings were **rejected** after they failed to reproduce; those rejections
are as much the deliverable as the confirmations.

Panel verdicts: 3× SERIOUS_FLAWS (DeepSeek, Grok, Qwen), 2× MINOR_ISSUES (Kimi, GLM). The spread
is driven almost entirely by **one** confirmed defect (C1) plus how each model rated the
path-handling gaps.

---

## Resolution status (2026-08-05)

**All six confirmed defects are fixed and regression-tested** (`tests/test_redteam_fixes.py`; the
finding-reproduction script `red-team/verify_findings.py` now shows every one no longer reproduces).
Full suite: 44 tests green (1 symlink test skipped where the OS forbids symlink creation).

| # | Fix | Test |
|---|-----|------|
| C1 | `Verifier.verify` composes over per-attempt evidence, not store history; explicit receipt↔envelope bind check; stores stay append-only audit logs | `StaleEvidence.*` |
| C2 | `snapshot_tree` is directory-aware and records symlinks by target; `observe_action` dispatcher wires `path_state` for `dir.make`/`file.delete` | `DirAndDeleteOps.*` |
| C3 | `_resolve_within` rejects absolute/`..`/symlink escapes; `rehash`/`path_state` return `"absent"` (fail closed) off-root | `WorkspaceEscape.*` |
| C4 | `build_contract` catches the malformed-args set → `None` → `UNVERIFIED(NO_CONTRACT)` | `MalformedArgsFailClosed.*` |
| C5 | `INTEGRITY_ATTESTED` attaches only when every obligation is unmet for lack of a usable world fact, never on `INSUFFICIENT_CHANNELS` | `AttestationScoping.*` |
| C6 | High-stakes two-source counts distinct **failure modes**, not channel strings | `DistinctFailureModes.*` |

The two surfaced **design decisions** (output-less `shell.run`; write-set scoped to the bind mount)
are left as-is pending a product call — both are currently fail-closed and safe.

---

## Confirmed defects (reproduced against the code), ranked

### C1 — False `VERIFIED` from stale accumulated evidence *(the one that matters)*
- **Severity:** HIGH — a genuine M1 break (false `VERIFIED`), in scope.
- **Raised by:** Grok F1 (also the root cause behind Kimi F1, Grok F6, GLM F1).
- **Location:** `pipeline.py` → `Verifier.verify`.
- **Root cause:** the pipeline `extend`s the append-only evidence stores on every call, then feeds
  `compose()` **the entire store history** filtered only by `envelope_id` prefix — not the evidence
  from *this* attempt. Re-verifying the same `envelope_id` reuses earlier WORLD facts.
- **Reproduced:** verify an honest write → `VERIFIED`; then delete the file, submit a fresh success
  receipt for the same envelope with an **empty** world set → still `VERIFIED`. The independent
  world fact is stale and unbound to the current attempt.
- **In-scope trigger:** ordinary receipt replay / glue re-issuing a success receipt after a partial
  failure — no compromised host required.
- **Fix:** compose over **per-attempt** evidence only (build ephemeral claim/world lists for this
  `verify` call and pass those to `compose`); keep the stores as the append-only audit log they
  already are. Add an explicit `receipt.envelope_id == envelope.envelope_id` bind check (closes
  GLM F1) and match evidence by exact `obligation_id`, not prefix (closes Kimi F1 / Grok F6). One
  fix collapses four findings. **Regression fixture required.**

### C2 — `dir.make` (and by extension `file.delete`) is broken and untested
- **Severity:** MEDIUM — correctness hole; fails *closed* (never false `VERIFIED`), so not C1-class.
- **Raised by:** Grok F2 + Kimi F4.
- **Location:** `observers.py` → `snapshot_tree` (walks files only); no pipeline test for dir/delete ops.
- **Reproduced:** `mkdir foo` produces an observed write-set of `[]`, but the `dir.make` contract
  expects write-set `["foo"]` → the obligation contradicts → an honest `dir.make` **always FAILs**.
  The op has a contract builder but no working observer path and no fixture, which is why it slipped through.
- **Fix:** make snapshots directory-aware (record dir paths with a type tag; define write-set over
  files *and* dirs), wire `path_state_evidence` into the dir/delete observation path, and add
  mutation fixtures for `dir.make` and `file.delete` so the coverage gate actually exercises the
  `path_state` obligation kind.

### C3 — Observers read outside the workspace on absolute / `..` / symlink paths
- **Severity:** MEDIUM in-scope (robustness / check desync), **not** the HIGH "forge agreement" some
  models claimed — that framing assumes a compromised executor, which §1 puts out of scope.
- **Raised by:** Grok F3, Qwen F1/F2, Kimi F5 (Kimi correctly self-labeled it near-out-of-scope).
- **Location:** `observers.py` → `rehash`, `path_state`, `snapshot_tree` (`Path(root) / path`).
- **Reproduced:** `Path("/workspace") / "/etc/passwd"` → `/etc/passwd`; an absolute or `..`-bearing
  authorized path makes the re-hash observe a different file universe than the snapshot, desyncing
  the three always-on checks.
- **Fix:** resolve `(root / path).resolve()` and reject unless it stays under `root`; don't follow
  symlinks out of root; normalize obligation subjects to workspace-relative posix before hashing.
  Cheap defense-in-depth that's correct even though the trusted-executor model blunts the severity.

### C4 — `build_contract` is not fail-closed on malformed args
- **Severity:** LOW — availability / fail-closed-surface; not a false `VERIFIED`.
- **Raised by:** Grok F4, Kimi F2, Qwen F4.
- **Location:** `contract.py` → `build_contract` (`except KeyError` only).
- **Reproduced:** `args={"content": 123}` → `AttributeError: 'int' has no attribute 'encode'`
  propagates out of `verify()` instead of resolving to `UNVERIFIED(NO_CONTRACT)`.
- **Fix:** catch the broad malformed-args set (`KeyError, TypeError, AttributeError, ValueError`) →
  return `None`. Add a fixture feeding malformed args of each op.

### C5 — `INTEGRITY_ATTESTED` over-attached when world facts exist but stakes gate fails
- **Severity:** LOW — misleads `require_attested()` consumers; no false `VERIFIED`.
- **Raised by:** Grok F5.
- **Location:** `composer.py`, unmet branch → `_receipt_attested`.
- **Reproduced:** HIGH stakes, full agreeing world set, one channel → `UNVERIFIED` with **both**
  `INSUFFICIENT_CHANNELS` and `INTEGRITY_ATTESTED`. But M5 defines attested as "authentic receipt
  yet *no* independent world fact" — here a world fact was obtained; it was just short of the
  high-stakes two-source bar.
- **Fix:** attach `INTEGRITY_ATTESTED` only when every gap is `NO_WORLD_FACT` /
  `NO_DISTINCT_FAILURE_MODE` (genuinely no usable independent fact), never on `INSUFFICIENT_CHANNELS`.

### C6 — High-stakes "two source" counts channels, not distinct failure modes
- **Severity:** LOW→MEDIUM — hardening; no false `VERIFIED` with the stock observers (they already
  use a distinct failure mode per channel), but weaker than M1's language intends.
- **Raised by:** DeepSeek F1 (its literal mechanism was backwards, but the underlying concern is real).
- **Location:** `composer.py` → `_agreement_gap` (`channels = {w.channel for w in distinct}`).
- **Issue:** two *correlated* channels that share a failure mode (e.g. `host.rehash` +
  `host.rehash_mirror`) would satisfy the high-stakes two-source requirement even though they are
  not independent. M1 keys independence on **distinct failure mode**, not channel string.
- **Fix:** require ≥ N distinct **failure modes** (not channel strings) among the value-agreeing,
  claim-distinct world facts.

---

## Rejected after verification *(kept, per the anti-manufactured-consensus discipline)*

- **DeepSeek F4** (value-disagreeing world facts satisfy two-source): **REJECTED.** `_agreement_gap`
  pre-filters `matching = [w for w in o_world if w.value == target]`, so a disagreeing value never
  counts toward the channel tally. Reproduction: the disagreeing second hash actually triggers
  `CONCLUSIVE_CONTRADICTION → FAILED` — strictly *safer*, not weaker.
- **DeepSeek F1 as literally stated** (same channel + different failure mode counted as 2 channels):
  **REJECTED** — `{w.channel for w in distinct}` dedupes by channel, so it collapses to 1. The
  inverse concern (correlated channels) is real and kept as **C6**.
- **GLM F5** (coverage-gate assertion too weak): **REJECTED** — `assertEqual(tripped, contract_kinds)`
  is strict; an uncovered new kind makes the sets unequal and fails the test. The genuine coverage
  gap is Kimi F4's (dir/delete ops absent), kept under **C2**.
- **GLM F2** (`"absent"` sentinel weakens hash typing): **REJECTED as material** — a 64-hex-char
  hash cannot collide with the literal `"absent"`, and the composer already catches the mismatch.
  Logged as a very-low robustness nicety only.
- **Qwen F3** (test-helper string interpolation): out-of-scope and test-only; correctly self-labeled.

---

## Design decisions surfaced (not bugs — product calls for Josh)

- **`shell.run` with empty `declared_outputs`** → currently `UNVERIFIED(MISSING_FLOOR)`. DeepSeek F2
  wants it verifiable via exit + empty-write-set; GLM F4 wants it rejected outright. Current behavior
  is *safe* (fail-closed); the question is whether a genuinely output-less command should be
  verifiable from exit status + an agreed empty write-set. A spec-interpretation call, not a defect.
- **Write-set only covers the bind-mounted workspace** (Qwen F2). Detecting writes *outside* the
  sandbox is the mount namespace's job, not the verifier's; M2 scopes the diff to the declared
  workspace. Worth documenting as an explicit boundary rather than "fixing."

---

## Bottom line

The spine held up: no model defeated the CLAIM/WORLD **type fence**, the sealed consumer gate, the
`require_attested()` / no-`bool` leak-locks, or turned only-CLAIM / type-smuggled evidence into
`VERIFIED`. The pure composer's fail-closed structure is sound. **One** real false-`VERIFIED` exists
(C1), and it's in the *pipeline glue*, not the predicate — the append-only stores are the right audit
substrate but the wrong composer input. Fixing C1 (which also closes three prefix/binding findings),
plus the `dir.make`/path/robustness items C2–C6, would harden the component without touching the
core design. Estimated effort: C1 + regression test is the priority; C2–C6 are small, mechanical,
and independent.
