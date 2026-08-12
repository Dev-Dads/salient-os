# provenance code panel: x-ai/grok-4.5

_finish=stop seconds=206.3 cost=$0.1332 usage={'prompt_tokens': 36871, 'completion_tokens': 9950, 'total_tokens': 46821, 'cost': 0.1332244, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1332244, 'upstream_inference_prompt_cost': 0.0735244, 'upstream_inference_completions_cost': 0.0597}, 'completion_tokens_details': {'reasoning_tokens': 7335, 'image_tokens': 0, 'audio_tokens': 0}}_

## F2 Provenance Review

### F1 / HIGH / Snapshot exception → full recording false-negative (model-triggerable)
**Location:** `collaborator/governance.py` (exit-branch pre/post snapshot try/except); `salienceos/verifier/observers.py` `snapshot_tree` (hash `read_bytes()`)

**Concrete case:** Autonomous contained `run_command` (signed `shell.contained_autonomy`, `require_code_protection=True`) runs:
```sh
printf '#!/bin/sh\npwn\n' > evil.sh && chmod 000 evil.sh
```
or drops `evil.sh` and also creates any unreadable node that makes `p.read_bytes()` raise during `os.walk`. `_pre` may succeed; `_post = snapshot_tree(...)` raises; bare `except` swallows; `_record_autonomous_authorship` never runs. `evil.sh` remains on the shared workspace. Later HELD `sh evil.sh` → `provenance_touch == ""`, no preview `autonomous_authored`, no summary ⚠.

**Why it fails its goal:** Stated goal is to make autonomous authorship **visible at the human approval hand**. This is an autonomous path that writes a runnable workspace file with **no** manifest record. Failure mode is fail-open on the audit (opposite of “best-effort skip” as mere lossiness—it is attacker-shaped). Documented porosity is token/glob/symlink **recall** on the recognizer, not “any I/O blip drops the whole write-set.”

**Other control?** No. Containment still allows workspace rw; human path is uncontained by design; seal/capability gates do not re-derive authorship. Nothing else tags the path.

**Fix:** Make `snapshot_tree` / diff **per-entry resilient** (on read error, record a distinct marker like `unreadable:` still ≠ pre, so path appears in `observed_write_set`). Never abort the whole post snapshot on one file. Optionally `try/finally` record partial diffs; treat “post snapshot failed” as loud audit, not silent skip.

---

### F2 / MEDIUM / `write_file` verification exception after bytes hit disk → FN
**Location:** `collaborator/governance.py` artifact branch (`execute_and_verify` try/except around `execute_tool` → `govern`); record only when `cleared and op == "file.write"`

**Concrete case:** Autonomous `write_file` of `drop.sh` succeeds in `_exec_write` (file on disk). Then `observe_action` / `govern` / receipt path raises (hostile workspace race, snapshot blow-up, verifier plumbing). Branch returns `FAILED` **without** `_record_autonomous_authorship`. Manifest empty; human `sh drop.sh` unflagged.

**Why it fails its goal:** Same visibility goal. Asymmetry: exit-branch `run_command` records even on nonzero exit; artifact branch records only on `cleared`, and the broad `except` skips recording entirely after a real write.

**Other control?** Verifier may mark FAILED (honest execution outcome) but does **not** surface autonomy provenance on a later hand.

**Fix:** On `op == "file.write"` and `execution is not None`, record from `execution.write_set` (or independent rehash presence) in `finally` when `not human_gated` and file exists; keep clear-on-human only when `cleared and human_gated`. Or observe disk presence before returning FAILED.

---

### F3 / MEDIUM / Stale ⚠: human re-vet via shell does not clear taint
**Location:** `collaborator/governance.py` (clear only in artifact `file.write` + `human_gated`); exit-branch never calls `_clear_autonomous_authorship`; `session.clear_autonomous_authorship`

**Concrete case:** Autonomy authors `build.sh` (tainted). Human approves maintenance `run_command`: `printf '#!/bin/sh\necho safe\n' > build.sh` (or `cp vetted.sh build.sh`). Bytes are human-chosen; taint remains. Later `sh ./build.sh` still gets ⚠ / `provenance_touch`. Module text calls **stale ⚠ after human re-vet** the failure mode to avoid; clear is only implemented for verified `write_file`, not for the normal maintenance writer (`run_command`).

**Why it fails its goal:** Own anti-goal is noise-blind from stale flags. Clear-on-write is keyed too narrowly vs “human-approved (re-)write of a path.”

**Other control?** None for taint lifetime. Human can ignore ⚠ (trained by staleness).

**Fix:** Also clear paths whose content **changes** on `human_gated` `run_command` (same pre/post diff, cheaply only when `_autonomous_authored` nonempty), or clear any path the human `write_file`s **and** any path appearing as a changed file in a human-gated exit diff. At minimum, document honestly if shell re-vet is out of scope (currently over-claimed).

---

### F4 / LOW / Benign token FPs (within documented porosity, but noisy)
**Location:** `collaborator/provenance.py` `references_autonomous_file` / `_tokenize` / `_rel_candidates`

**Concrete case:** `build.sh` ∈ `_autonomous_authored`; human held commands `echo build.sh`, `cat build.sh`, `ls build.sh`, `wc -l build.sh` all match token `build.sh` → ⚠. Flag skip blocks `-x` but not these.

**Why:** Documented token porosity; not a silent FN. Still pushes the “real failure” (noise-blind) if maintenance often names the file without executing it.

**Other control?** No.

**Fix (optional, precision):** Weight match if token is in executor position (`sh`/`bash`/`source`/`. ` argv patterns) or suppress pure-stdout utilities—only if you can do it without new FNs you care about.

---

### F5 / LOW / Prefix / `--file=` style recognizer FNs (documented porous)
**Location:** `provenance.py` `_rel_candidates`: `if not t or t.startswith("-"): return out`

**Concrete case:** `run --file=build.sh` or `--config=build.sh`: entire token skipped → no ⚠ while human runs autonomy-authored bytes via flag binding.

**Why:** Porous-by-construction; listed as acceptable recall loss if non-literal. Not worse than “glob/symlink/shell var” story unless operators commonly use GNU long-opt `=path` forms.

**Other control?** No.

**Fix (optional):** Split `=` for long opts when RHS normalizes to an authored rel.

---

### F6 / Model reachability (clear / poison / private API) — mostly sound
| Attack | Result |
|--------|--------|
| (a) Clear taint without human | **Blocked.** `_clear_autonomous_authorship` only when `human_gated` and cleared `file.write`. Model tools never set `human_gated`; `clear_*` not on tool surface. |
| (b) Avoid recording | **Open via F1** (force snapshot/note except). No direct skip flag. |
| (c) Poison manifest to hide drop | **Cannot remove** except human clear. Can **add** huge write-sets (noise in set, not automatic ⚠ spam unless commands name those paths). |
| (d) Reach `_autonomous_authored` / `note_*` / `clear_*` via model fields | **No.** Only governance calls; getattr-guarded. |

---

### F7 / Laundering via clear — sound
Human clear then autonomous re-write **re-adds** (`note` unconditional add). Ordering “human write same path clears prior autonomy taint” is intentional re-vet, not a silent launder channel. No ordering where autonomy drops **after** clear without re-note on a successful autonomous author path (except F1/F2 FNs).

---

### F8 / Totality / regression — mostly sound, one cost
- `_references_autonomous`, `references_autonomous_file`, note/clear wrappers: fail closed to `""` / skip; do not break `govern_action` never-raise.
- HELD `provenance_touch=(_authored if run_command else "")` is safe (ternary does not eval `_authored` off-path).
- New Decision/preview fields are additive defaults.
- **Cost:** extra full-tree hash pre+post on **every** autonomous contained `run_command` (double `snapshot_tree` over whole workspace). Large trees → latency/DoS-ish stall, pressure toward snapshot exceptions (**amplifies F1**). Not a raise out of govern if except stays local.

**Fix:** Cap/walk budget, incremental mtime+size fast path, or snapshot only when manifest non-empty / sampling—without failing open on errors (see F1).

---

### F9 / Autonomous shell without recording floor — sound
`execute_and_verify`: `not human_gated` + `run_command` either **DENY** at execution or sets `require_code_protection=True` (needs `code_protection_available` ∧ enforced ∧ `SHELL_CONTAINED_AUTONOMY_CAP`). No path where autonomous shell runs with `require_code_protection=False`. Recording gate matches the real contained autonomy path. Netns raw opt-in does not disable code containment or the snapshot. **Recall boundary for “which runs are tracked” matches the doc** (autonomous contained + autonomous cleared `write_file`); it is **worse than doc** when snapshot/verify plumbing drops records (F1/F2).

---

### F10 / Content change of existing file / rename / hardlink (recording)
- Content change: hash diff → `observed_write_set` → recorded (good).
- Rename: new name recorded; old name may **stale-remain** in set (minor FP if later named).
- Hardlink new name: new path recorded (good).
- mtime/chmod-only: not recorded (content-authorship definition; OK if intentional).
- Dirs / `.sandbox-home/`: excluded (good; pinned by tests).

---

### F11 / Absolute-into-workspace mis-relativize — sound enough
Textual `ws + "/"` prefix avoids `/ws` vs `/ws-evil`. `normpath` before prefix check avoids `/ws/../etc/passwd` → inner escape. Escaping `..` dropped in `norm_rel`. Residual: purely textual `workspace` string must match how humans spell abs paths (otherwise only relative token form matches)—acceptable.

---

## STEELMAN
The design honestly scopes an advisory, session-lived, token-level flag around a deliberate shared rw workspace, and the hard parts are wired correctly: clear is human-gated, autonomous shell recording is tied to the same `require_code_protection` bit that execution withholds on, write_file note/clear is split on `human_gated`, and totality wrappers match the govern/approve contract. Tests usefully pin the happy paths (write_file record, contained dropper, human maintenance not tracked, clear-on-approve write). The porous recognizer is labeled as non-boundary in the same breath as codefence/sensitivepaths.

## VERDICT: **SERIOUS_FLAWS**
Core visibility goal fails open when post (or pre) `snapshot_tree` throws—**model-triggerable with `chmod 000` / unreadable entries in the same autonomous drop**—so runnable workspace authorship can miss the manifest entirely; secondary gaps are verify-except write_file FN and stale taint after human **shell** re-vet despite the module’s own stale-⚠ doctrine.

**Single highest-value fix:** Make authorship snapshot/diff **never fail open**—per-file error markers + always-run record path (and optionally clear-on-human content change for exit-mode)—so an autonomous workspace drop cannot erase its own provenance by breaking the walker.
