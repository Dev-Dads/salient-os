# Provenance-manifest pruning — disposition (F2 durable-store follow-up)

Lean 5-vendor external CODE panel on the SHIPPED load-time pruning (branch `feat/provenance-prune`) — a
small, low-risk quality fix (drop taints for absent files at Session construction so a human's same-named
recreate isn't falsely warned + bound growth).

## Verdicts + cost

| model | verdict | cost |
|---|---|---|
| openai/gpt-5.1 | MINOR_ISSUES (raw-vs-normalized keys — non-issue) | $0.0327 |
| google/gemini-2.5-pro | (reasoning only, truncated — no verdict line) | $0.0000 (n/a) |
| anthropic/claude-opus-4.1 | MINOR_ISSUES (TOCTOU/symlink) | $0.2788 |
| x-ai/grok-4.5 | SERIOUS_FLAWS (exists() conflation — the real one) | $0.0648 |
| qwen/qwen3-max | SOUND | $0.0101 |
| **TOTAL** | | **$0.3865** |

## The real finding — fixed (reproduce-before-accept)

**`Path.exists()` conflates "absent" with "un-stattable" (grok F1 HIGH).** `exists()` returns False both
for a genuinely-deleted file AND for a stat that failed for another reason (EACCES/ELOOP/…). On the
contained autonomy path the agent can write the workspace, so an autonomous `chmod 000` of a PARENT dir
could make a STILL-PRESENT `evil.sh` look absent → its taint pruned AND re-persisted (durable) → a human
later runs the present, un-flagged file. That both drops a live warning and is a laundering step,
violating the diff's own invariants. **FIXED:** prune now keys on `os.lstat` and treats ONLY
`FileNotFoundError`/`NotADirectoryError` (a definitive "no directory entry") as absent; ANY other
condition — a present entry (incl. a broken symlink), a permission/other stat error, or a pathological
path string — KEEPS the taint. This also broadens totality (grok F2: a bad path string now can't raise
out of `Session.__init__`) and makes the previously-near-dead except live (grok F4). Pinned:
`test_a_present_directory_entry_is_kept_even_if_broken_symlink` (lstat keeps a present entry; runs on
Linux CI).

## Reproduced-and-REFUTED / documented

- **TOCTOU between check and use (opus MEDIUM)** — inherent to any load-time filter and low-impact for an
  ADVISORY manifest: the point-in-time check either keeps or drops; a file appearing later via an
  autonomous action is simply re-recorded. Not a boundary.
- **Symlink info-disclosure (opus LOW)** — REFUTED: pruning only STATs (now `lstat`, no follow), never
  reads; no content is disclosed.
- **Raw-vs-normalized keys (gpt LOW)** — non-issue: manifest keys are already `norm_rel`-normalized on the
  way in (`note_autonomous_authorship`), so `workspace / rel` is canonical. Noted, no change.
- **Rename/move laundering (grok F3)** — the quality goal is path-identity based (a human recreating a
  same NAME); an autonomous rename to a new name is re-recorded at the new path by the next contained
  run's snapshot-diff. Pruning the old absent name is correct (the old path is un-runnable). This is the
  documented advisory scope (path-identity, not content-identity), not a new hole.

## Net

The one real issue (exists()-conflation → false-prune/laundering) is fixed at the root with `lstat` +
ENOENT/ENOTDIR-only pruning; the rest are refuted or documented advisory scope. Pure-Python + stdlib;
747 tests green cross-platform (the broken-symlink proof runs on Linux CI), no Sparky needed.
