# F2 autonomy-authorship provenance CODE panel — disposition (ADR 0003 residual sweep)

5-vendor external CODE panel on the SHIPPED F2 provenance-flagging diff (branch
`feat/autonomy-provenance`) BEFORE merge. Lean single pass matched to risk: a session-lived set + a
POROUS argv recognizer + a pre/post snapshot-diff — no new privileged surface, no network. The
control is deliberately ADVISORY (a ⚠ + audit tag, never a deny), so the review targeted the failure
modes of an advisory control (recall worse than documented; model-reachable clear/skip; noise-blind
FPs; totality).

## Verdicts + cost

| model | verdict | cost |
|---|---|---|
| openai/gpt-5.1 | MINOR_ISSUES (snapshot fail-open MEDIUM + 3 LOW) | $0.0720 |
| google/gemini-2.5-pro | (truncated output) snapshot fail-open + stale-⚠ | $0.0000 (n/a) |
| anthropic/claude-opus-4.1 | MINOR_ISSUES (record-on-cleared CRITICAL + 4) | $0.7085 |
| x-ai/grok-4.5 | SERIOUS_FLAWS (snapshot fail-open HIGH + stale-⚠ + 9) | $0.1332 |
| qwen/qwen3-max | SERIOUS_FLAWS (snapshot fail-open CRITICAL + stale-⚠ HIGH) | $0.0775 |
| **TOTAL** | | **$0.9913** |

(gemini returned `finish=error` with partial content and $0 usage accounting — its two named issues
duplicate the converged findings below, so nothing is lost.)

## Findings + disposition (reproduce-before-accept)

1. **Snapshot fail-OPEN / hang on a special or unreadable file — ALL 5 flagged (CRITICAL/HIGH/MEDIUM).**
   `snapshot_tree` read every non-symlink file with `read_bytes()`. A rw workspace lets an autonomous
   run `mkfifo` a FIFO; a reader with no writer BLOCKS forever → the govern loop HANGS (a real DoS,
   model-triggerable on the contained Linux path). A transient read error aborted the whole walk →
   `_pre=None` → the run's drops silently un-recorded (fail-open). **FIXED at the root** (helps the
   pre-existing write_file artifact path too): `snapshot_tree` now marks a non-regular file `"special"`
   (never opens it) and an unreadable/vanished file `"unreadable"` (per-file guard, never aborts).
   **AND** the run_command exit-branch keeps a fail-CLOSED-to-honest posture: a snapshot failure never
   blocks the run (an advisory tag must not gate a legitimate autonomous action) but sets
   `session._autonomous_tracking_incomplete`, surfaced in the human preview
   (`provenance_tracking_incomplete`) so a MISSING ⚠ is not misread as "human-authored". Pinned:
   `test_fifo_is_marked_special_and_does_not_hang`, `test_dropped_fifo_is_recorded_via_diff_without_hanging`
   (both `@skipUnless(mkfifo)` → run on Linux CI), `test_snapshot_failure_marks_tracking_incomplete_and_surfaces_it`.
   *Calibration note:* this is an ADVISORY control — a missed tag degrades to the pre-feature state (no
   flag), not a boundary break; the fix removes the exploitable trigger AND makes the residual honest.

2. **Autonomous write_file recorded only when fully `cleared` — opus CRITICAL.** A write whose child
   reached disk but whose verification did not clear (partial / hash-divergent) left runnable bytes
   UNtracked. **FIXED:** record an autonomous write on `execution.result.ok` (child reached disk), not
   on `cleared`; CLEAR (human-approved) stays on the strict `cleared` gate. Pinned:
   `test_autonomous_write_recorded_even_when_verification_does_not_clear`.

3. **Stale ⚠: a human re-vet via the SHELL did not clear the taint — grok HIGH, qwen HIGH, gemini.**
   Only a human-approved write_file cleared a taint; approving `sh ./build.sh` did not, so the same ⚠
   nagged on every future approval of an unchanged file. **FIXED:** a human-approved run_command that
   references autonomy-authored files now CLEARS their taint (a conscious accept of those exact bytes);
   a later autonomous re-write re-taints via the diff. Pinned:
   `test_human_approved_run_of_autonomous_file_clears_its_taint`.

## Reproduced-and-REFUTED (verify-before-trust)

* **"diff misses rename / hardlink / symlink-target / content-change" (opus MEDIUM, grok F10).**
  Reproduced against `snapshot_tree`/`observed_write_set`: a content change of an existing file, a NEW
  hardlink (new path, same content), and a symlink-target change (marker `symlink:X` differs) are ALL
  in the write-set; a rename records the end-state file. The content+path model catches them —
  refuted. (Repro in the session log.)
* **"manifest poisoning noise-blinds / buries a real drop" (gpt LOW, opus LOW, qwen MEDIUM).** The ⚠
  fires ONLY when a human command REFERENCES a recorded path, so N junk entries create ZERO warnings
  (no reference → no ⚠) and the real drop stays flagged when referenced. No size cap added on purpose:
  a cap that silently dropped the real entry would be a worse fail-open than bounded memory (workspace
  file count). Refuted as a defeat of the control.
* **"model can clear its own taint" (probed by all).** Clear is gated strictly on `human_gated`
  (a real approve()) + a verified write / a consciously-approved run — the model, emitting tool
  intents, has no path to `note_*`/`clear_*`/`_autonomous_authored`. Sound.
* **write_file `write_set` under-reports (gpt LOW).** For `file.write` the write_set is the single
  declared, resolved path actually written — not under-reported. Sound.

## Not-findings (documented advisory posture, confirmed by the panel)

Token-only recognizer (a file reached without a bare argv token — nested `sh -c`, a pipe into `sh`, a
glob, a symlink — slips: recall loss accepted, a false ⚠ is the worse failure); count/reference, not
content inspection; per-SESSION manifest (cross-session durable store is a follow-up); the human
maintenance run deliberately NOT tracked (full reach by design); getattr-guarded (a legacy session
simply has no manifest).

## Net

Three real fixes shipped (special-file hang/fail-open → root-robustified + fail-honest; record on
disk-write not full-verify; clear on human-approved run), each pinned; the rename/hardlink/symlink and
poisoning concerns reproduced-and-refuted. Pure-Python governance + a total-function primitive
hardening — fully covered by the cross-platform suite (710 green; the FIFO proofs run on Linux CI), no
Sparky proof needed (no OS-privileged mechanism changed).
