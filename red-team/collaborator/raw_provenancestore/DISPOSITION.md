# Durable provenance-store CODE panel — disposition (F2 follow-up)

5-vendor external CODE panel on the SHIPPED cross-session durable provenance store (branch
`feat/durable-provenance-store`) BEFORE merge — a lean pass matched to a bounded, integrity-protected
persistence layer (no privileged surface, no network).

## Verdicts + cost

| model | verdict | cost |
|---|---|---|
| openai/gpt-5.1 | CRITICAL default-key (→ downstream of reachability) + 4 | $0.0657 |
| google/gemini-2.5-pro | SERIOUS_FLAWS (concurrency + rollback) | $0.1027 |
| anthropic/claude-opus-4.1 | MINOR_ISSUES (workspace-symlink CRITICAL) | $0.5180 |
| x-ai/grok-4.5 | MINOR_ISSUES (symlink-in-ws HIGH + save-fail + concurrency) | $0.1100 |
| qwen/qwen3-max | (worked to) HIGH symlink; forge/rollback self-refuted | $0.0337 |
| **TOTAL** | | **$0.8301** |

## The crux — reproduce-before-accept

**Symlink reachability: the check resolved the path but the session PERSISTED/IO'd the UNRESOLVED path
(grok F1, qwen, opus, gpt).** A host store configured as `ws/prov -> /outside` (a symlink lexically
inside the workspace) passed `_assert_store_outside_workspace` (the *resolved* target is outside) — but
`self._provenance_store` was the *unresolved* `ws/prov`, so `save()`'s `os.replace(ws/prov.tmp, ws/prov)`
would drop a **real file at the in-workspace path**, which the contained agent (workspace-writable) can
then delete (→ missing → fresh-trusted → taints erased) or corrupt. That defeats the store's one
load-bearing property: **unreachability by the agent.** **FIXED:**
- `_assert_store_outside_workspace` now RETURNS the RESOLVED path and the session stores/IOs *that*, so
  all reads/writes hit one fixed out-of-workspace location (no os.replace onto an in-workspace symlink);
- it rejects a path inside the workspace by **either resolved OR lexical** containment (a symlink at
  `ws/prov` is lexically in-workspace → refused);
- it requires an **absolute** path (no CWD-relative ambiguity about what was validated).
Pinned: `test_symlink_in_workspace_pointing_outside_is_refused`, `test_persisted_store_path_is_the_resolved_target`
(both run on Linux CI where symlinks + the contained agent live; skip on Windows-no-privilege),
`test_relative_store_path_is_refused`.

**Default `policy_key` forgery (gpt CRITICAL) — downstream of the above.** With the shipped public
default key, someone who can WRITE the store could forge a clean MAC. But grok reproduced the coupling
correctly: *"default key is public — fine iff the store is unwritable."* Once the store is genuinely
unreachable (crux fix), the agent cannot place a forged file at all, key or no key. **Hardened anyway
(defense-in-depth):** the MAC now uses a **dedicated derived key** `HMAC(policy_key,
"salient-provenance-store-v1")` — so a hypothetical signing oracle for `policy_key` elsewhere (gpt ID3)
can't mint store MACs — and the docstring states plainly that integrity assumes a host-SECRET
`policy_key` (with the default it degrades to corruption detection; unreachability is the real property).

**Save-failure fail-silent (grok F2, gpt ID4).** A silently-failed `save()` left the durable record
stale, and a later session would trust it as complete. **FIXED:** `_persist_provenance` marks
`_autonomous_tracking_incomplete` on a failed save (surfaced as degraded tracking). Pinned:
`test_save_failure_marks_tracking_incomplete`.

## Documented scope (not fixed — proportionate for an ADVISORY control)

- **Concurrent same-workspace sessions racing `save()` (gemini CRITICAL, gpt/opus/grok LOW-MED).** Real,
  but only in an UNSUPPORTED concurrent mode, and it is advisory data-loss (a missed ⚠), not a
  forge/tamper. The store is designed for SEQUENTIAL sessions (each loads-mutates-writes; `os.replace` is
  atomic so no torn read). A file-lock for the concurrent mode is a **documented follow-up** (module
  docstring). Not half-fixed with union-on-save, which would silently UNDO a legitimate clear.
- **Accumulation / pruning** — a dropped-then-deleted path lingers until a human write/approved-run
  clears it (same as the base F2 control; a pruning pass is a follow-up).

## Reproduced-and-REFUTED

- **HMAC forge without the key** — sound; `hmac.compare_digest` over a keyed MAC; the agent cannot mint
  `ok=True` (and post-crux cannot write the store at all).
- **Rollback of an old same-store version** — out of scope once the store is unreachable by the agent
  (crux fix); a human restore is the trusted hand. No freshness counter needed for the stated model.
- **Torn read vs `os.replace`** — atomic on the same filesystem; a reader sees the whole old or whole
  new file. `load()` only ever reads the real path, never the leftover `.tmp` (refutes opus F2).
- **Subject binding** — the subject is inside the MAC'd body AND re-checked equal to the caller's
  subject on load, so a cross-workspace store is untrusted (refutes gpt ID2).

## Net

The one real defeat (symlink reachability, which also subsumes the default-key "forgery") is fixed at
the root — resolve-and-persist + lexical-and-resolved rejection + absolute-path requirement — plus
key-separation and save-failure honesty. Concurrency + pruning are documented advisory-scope follow-ups.
Pure-Python + stdlib; 729 tests green cross-platform (the two symlink proofs run on Linux CI), no Sparky
proof needed.
