# DISPOSITION — maint_fetch CODE panel (ADR 0006)

5-vendor external CODE panel on the SHIPPED maint_fetch build. **Verdicts: 3 SOUND (gpt-5.1, gemini-2.5-pro,
opus-4.1), 1 MINOR_ISSUES (grok-4.5), 1 SERIOUS_FLAWS (qwen3-max).** Cost: **$1.0755** (gpt-5.1 $0.0854,
gemini $0.1581, opus $0.6740, grok $0.0925, qwen $0.0655).

The core contract was certified SOUND by ALL FIVE: the streaming fail-closed over-cap ceiling (`>` not
`>=`, checked before write, partial deleted, non-2xx/redirect not staged, sink.write OSError caught),
the `net.maint:` authority namespace isolation (separate from net.get/net.post, single derivation site,
"MAINT" never on the wire), the url+dest `held_action_seal` (+ `freeze_args`, injective framing, tool-bound,
no seal gap), max_bytes host-only + validation + PROPOSE_FIRST/no-auto-lift + mutating=False keeping it out
of the emission-auto/quota/credential paths, and the faithful reuse of the Tier-1 transport contract.

## The one convergent substantive finding — reproduced before accepting

**Workspace-fence symlink handling on `dest`** (grok F5 MEDIUM; qwen ID-7 CRITICAL / ID-9 HIGH).
Reproduced live on Sparky (`scratchpad/fence_repro.py`, real Linux symlinks):

- **qwen ID-7 (pre-planted symlink escape, e.g. `evil -> /outside`, dest=`evil/secret`) — FALSE POSITIVE.**
  `resolve_in_workspace` does `(root/rel).resolve()` which FOLLOWS all symlinks, THEN checks containment;
  the resolved path is outside root → **WorkspaceError (CAUGHT)**. Reproduced: intermediate, final-component,
  and dangling pre-planted symlinks are ALL caught. qwen's SERIOUS_FLAWS verdict rests on this false positive.
- **grok F5 (TOCTOU race) — REAL.** `open(dest,"wb")` FOLLOWS a symlink raced into place AFTER the fence
  check passes (reproduced: wrote THROUGH to `/outside/secret`). Requires a concurrent workspace writer
  during the human-gated fetch window; MEDIUM, and SHARED with `write_file` (same resolve-then-open pattern).

## Fixes applied (reproduce-before-accept, then close)
1. **Atomic staging via mkstemp + os.replace** (grok's own recommended fix; reproduced closing the vector):
   `_exec_maint_fetch` now streams to a fresh `tempfile.mkstemp` file in the fenced parent, then
   `os.replace(tmp, dest)`. We NEVER open `dest` for writing, so a symlink at dest (pre-planted OR raced) is
   REPLACED, never written THROUGH (reproduced: os.replace of the NAME does not follow the link). Also makes
   staging ATOMIC — dest is either the complete artifact or untouched; no partial ever appears at dest
   (closes grok F1 "partial lingers" + gemini's directory case: os.replace onto a dir → clean non-ok).
2. **Real bug found + fixed (`.record`):** the fail-handlers built `rec = egress._refused(...)` — which
   returns an **EgressResult**, not an EgressRecord — so `rec.error` / `egress=rec` were wrong. Pre-existing
   in the OSError-on-stage handler (no test had hit it); the new dest-is-a-directory test surfaced it.
   Fixed to `.record`. (This is the "certify not just when buggy" dividend — the code panel's directory case
   exposed a latent crash-on-error path.)
3. **Regression tests added:** pre-planted symlink dest → WorkspaceError (locks the qwen false-positive as
   tested behavior); atomic failure leaves no dest AND no leftover temp; success leaves no temp; dest-is-a-
   directory fails cleanly. 29 passed / 1 skipped (symlink test runs on CI/Linux).

## Documented residual (honest, shared, out of v0 scope)
An INTERMEDIATE directory component raced to a symlink still needs `openat2(RESOLVE_NO_SYMLINKS)` / a dirfd
walk to fully close — the same residual as `write_file`. The workspace is human-gated + disjoint-from-code,
and the final-component vector (the direct one) is now closed. Recorded in ADR 0006. A uniform
resolve_in_workspace hardening for all fenced writers is a documented follow-on.

## Outcome
Core contract SOUND across all 5. qwen's SERIOUS_FLAWS reproduced-refuted (pre-planted symlinks are caught).
grok's MEDIUM race + a latent `.record` crash CLOSED via atomic mkstemp+os.replace staging. No net-new test
failures; live-proven on Sparky. Cleared to merge.
