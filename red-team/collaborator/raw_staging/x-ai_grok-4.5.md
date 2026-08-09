# Staging subsystem red-team: x-ai/grok-4.5

_finish=stop seconds=213.6 cost=$0.1146 usage={'prompt_tokens': 26723, 'completion_tokens': 10226, 'total_tokens': 36949, 'cost': 0.1145844, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1145844, 'upstream_inference_prompt_cost': 0.0532284, 'upstream_inference_completions_cost': 0.061356}, 'completion_tokens_details': {'reasoning_tokens': 7733, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

### F1 / Controlled-location deny is `write_file`-only (shell path) / HIGH  
**Location:** `collaborator/governance.py` ~lines with controlled-location block (`intent.name == "write_file" and ... source == "proposed"`); `collaborator/tools.py` (`run_command` / `_exec_command`)

**Concrete bypass:**  
Session with `shell.exec` granted (and default `run_command` leash `propose_first`, or host `act_then_report`):

```json
{"propose": true, "confidence": 0.9, "rationale": "ci",
 "action": {"name": "run_command",
   "arguments": {"command": ["bash", "-c",
     "mkdir -p .github/workflows && printf 'on: push\\n' > .github/workflows/ci.yml"]}}}
```

- `is_controlled_location` never runs (name ≠ `write_file`).
- Candidate is **surfaced** (HELD), not hard-denied; with `act_then_report` it **lands** with no staging step.
- Collaborator `run_command` uses `verify_mode="exit"` only — **no** write-set / rehash floor on this path — so the shell write is not caught by the new boundary either.

**Guarantee broken:** “Proposer-originated writes into controlled subtrees are refused and must stage to scratch; placement is a separate approved act.” Only `write_file` honors that.

**Other checks:** Capability gate blocks only if `shell.exec` absent (default session). Workspace fence does not help (path stays inside root). Verifier write-set **does not** run for collaborator `run_command`. Human approval still required under default leash — so this is not unattended RCE, but it **does** bypass hard-deny-and-stage and can auto-run if leash is loosened.

**Fix:** Apply the same controlled-location policy to any proposer-originated mutating tool that can create/write paths (at least `run_command`): e.g. deny when `source=="proposed"` and the command/args clearly target a controlled prefix, or require declared_outputs + full artifact/write-set verification and treat controlled destinations like `write_file`. Stronger: force `propose_first` + explicit path admission for controlled trees on every mutator.

---

### F2 / Approval re-gate omits controlled-location / MEDIUM  
**Location:** `collaborator/governance.py` — `reauthorized_or_denied` (capability + `resolve_in_workspace` only); controlled check only in `govern_action`

**Concrete bypass:**  
1. Propose/hold `write_file` `path=staged/ci.yml` (passes controlled check).  
2. Before `approve`, mutate the held object (same trust domain / buggy UI / script):  
   `proposal.decision.args["path"] = ".github/workflows/ci.yml"`.  
3. `approve` → `reauthorized_or_denied` only checks capability + workspace containment → **runs** write into `.github`.

**Guarantee broken:** Controlled deny is origination-only; moment-of-use authority re-gate does not re-assert “proposer-originated placement into controlled trees.”

**Other checks:** None on the approve path. Single-trust-domain (ADR 0002) makes silent remote exploit unlikely without host-side mutation — still a real TOCTOU/defense-in-depth hole relative to the stated hard-deny.

**Fix:** In `reauthorized_or_denied` (and/or `execute_and_verify`), if `decision.origin == "collaborator"` / original `source=="proposed"`, re-run `is_controlled_location` and deny (or require an explicit host “placement” intent distinct from proposer hold).

---

### F3 / Pool cap is pending-only → resolved retention DoS / LOW  
**Location:** `collaborator/proposalpool.py` — `add` / `pending_count`; no auto-`prune_resolved` in `propose` / `approve_proposal`

**Concrete input:** Loop: fill `max_pending` (256) → approve/veto all → enroll 256 more → never call `prune_resolved`. `_items` grows without bound; only **pending** is capped.

**Guarantee broken:** Doc claim that growth is capped so a flooding proposer cannot exhaust memory is overstated — a chatty propose+approve loop (or host forgetting prune) still grows without bound.

**Other checks:** None. Not an authority leak.

**Fix:** Cap `len(_items)` as well, or auto-prune resolved on `add` / after approve/veto; keep pending non-evicting.

---

### F4 / Unicode normalization aliases vs controlled prefix (macOS) / LOW  
**Location:** `collaborator/tools.py` — `is_controlled_location` / `_fs_normcase` (normcase + Win trailing `.\` only)

**Concrete bypass (APFS/HFS+):** Proposer path uses NFD `.github` while `controlled_paths` is NFC `.github` (or the reverse after `resolve()`). Component tuples differ; check returns **False**; write still lands in the directory GitHub/CI treats as `.github`.

**Guarantee broken:** FS-collapsing alias discipline (case/dot) is incomplete vs Unicode canonical equivalence.

**Other checks:** Workspace fence allows the path (in-tree). Not an issue on typical Linux CI case-sensitive stores where NFD ≠ NFC are distinct dirs (CI won’t read the lookalike).

**Fix:** Normalize each component with `unicodedata.normalize("NFC", ...)` (and normcase) before compare; optionally resolve controlled prefs the same way.

---

### F5 / Staging prompt teaches policy shape / LOW (non-bypass)  
**Location:** `collaborator/propose.py` — `_PROPOSER_SYSTEM` controlled-location paragraph

**Input:** Any caller who can read the system prompt learns “use `staged/` + human placement” and that deny is framed around `.github` / write path.

**Why weak:** Does not itself grant authority; fences still say DATA ≠ instructions. Combined with F1, it nudges a capable model toward **shell** or non-`.github` mutators rather than inventing a novel alias.

**Other checks:** Capability + leash still apply.

**Fix:** Keep instruction; close F1 so the documented stage path is the only mutator path that works.

---

## Verifier exemption — attacked claim (non-findings)

Exemption in `observe_action` (`observers.py`):

```text
anc ∉ subject_set ∧ anc ∉ pre_snapshot ∧ post[anc] == "dir"
```

| Attack | Verifier vs reality | Independent catch? |
|--------|---------------------|--------------------|
| Pre-existing **file** at ancestor → replace with dir + child write | Ancestor **in** `pre` → **not** exempt; write-set sees file→`dir` | write-set fails closed; `rehash` on child can still match (pinned `test_file_to_dir_ancestor_replacement_is_caught`) |
| Ancestor **symlink** → dir (or dir→symlink) | In `pre` as `symlink:…` or post ≠ `"dir"` → not exempt | write-set |
| New symlink where parent should be | `post.get(anc) == "dir"` false | write-set |
| Delete/rmtree removing ancestor while declaring child delete | Ancestor removal not exempt (in `pre`) | write-set (pinned `test_delete_that_removes_an_ancestor_dir_is_caught`) |
| Declared nested path; **sibling** undeclared write | Sibling ∉ `entailed_ancestors` | write-set (pinned) |
| Crafted declared path whose string “ancestors” name a sensitive **existing** dir | Existing dir ∈ `pre` → not exempt; only **absent→dir** parents exempt | Mutations under that tree still appear as their own keys |
| `dir.make` / `file.delete` / `shell.run` declared_outputs | Same transition rule; subjects never self-exempt | `path_state` / `artifact_hash` still on subjects; extra paths stay in write-set |
| Exemption wider than parent chain? | `entailed_ancestors` is strict parents only; filters `""`/`.` only | Unrelated paths remain visible |

`entailed_ancestors("foo/../bar/x")` can emit non-snapshot keys (`foo/..`, …) while the disk path is `bar/x` → exemption **under**-applies → write-set / path mismatch **fails closed**, not open.

**rehash / path_state:** Do **not** observe ancestors for `file.write` / `shell.run`; the transition-gated write-set is doing the real work for destructive ancestor type-changes. That design is consistent and test-pinned.

**No soundness break found** on the entailed-ancestors exemption as shipped.

---

## Pool / propose wiring — further non-findings

- **Authority:** Pool is reference bookkeeping; run path is `approve_proposal` → `approve` → re-gate + `execute_and_verify`. Surfacing ≠ authority (as designed).
- **Double-run / veto bypass bare `approve(decision)`:** `Decision.consumed` + veto marking pinned in `tests/test_collaborator_staging.py`; no second execution in those tests.
- **TOCTOU capability:** Denied re-gate leaves `PROPOSED` / pending (pinned).
- **`snapshot()` forging:** `_safe_args` + `Proposal.summary()` → `_flatten` strips newlines / ANSI / `<<` (pinned).
- **`intent.source` forge via model JSON:** `_candidate_from_response` hardcodes `source="proposed"`; model cannot clear the deny by omitting source.
- **Path/`..`/symlink into `.github` via `write_file`:** `resolve_in_workspace` then prefix on **resolved** parts; symlink-to-`.github` still denies. Case/trailing-dot on Windows covered by `_fs_normcase` + tests.
- **Root-anchored match:** Component prefix, not string prefix — `src/.github` and `github/` not controlled; `.githubfoo` not treated as `.github`. No false-negative for exact `.github/**` on normcase-equivalent names (modulo F4).
- **User-directed controlled write:** `source != "proposed"` not denied (pinned) — intentional.

---

## STEELMAN

The write-set fix is the right shape: entail **only** absent→`dir` parents of **declared** subjects, so nested honest writes verify without blinding pre-existing ancestor type-changes; tests lock the failure mode that has no second observer. Controlled-location deny is correctly origin-keyed (`proposed` hardcoded) and FS-aware for the Windows aliases that actually collapse, and the pool correctly separates memory of pending work from authority (re-gate, single-use, display flattening). Remaining gaps are coverage breadth (shell mutator, approve-time re-check, total pool size), not a collapse of the write-set transition rule.

## VERDICT

**MINOR_ISSUES** — Verifier exemption is sound and fail-closed under the attacks in scope; controlled-location hard-deny is real for `write_file` but incomplete against proposer `run_command` (and approve-time path mutation), which is the main guarantee gap to close.
