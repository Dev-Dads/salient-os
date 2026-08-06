# PR-H1 (SalienceOS producer) — red-team synthesis

The Stage-2 producer wires the judgment system into quorum-agent (the test rig) as a
**produce-only** observer. Reviewed under Josh's authorized **double-pass** policy:
2 internal + external general panel (pass 1), then 2 internal + external coding panel
(pass 2), reproduce-before-accept throughout. Target: quorum-agent
`feat/salience-producer`.

## Verdict

No CRITICAL/HIGH survived reproduction. Both internal reviewers and the five-model
general panel independently judged it ship-worthy as produce-only; the substantive
catches were one MEDIUM (durable-subject aliasing) plus a cluster of LOW hardening and
test-honesty items. Every accepted finding is fixed and pinned by a test that goes red
if the fix is reverted.

## Internal pass (2 reviewers)

- **A / correctness+fail-closed:** confirmed 6 of 7 guarantees hold with quoted
  evidence; found **[MEDIUM] per-session leak** — `_close_session` never freed
  `_WINDOWS`/`_BUSES`, so a long-lived host accumulates one materialized bus per
  session (the one way a produce-only observer could OOM the host). Plus 2 LOW
  (`_operator_budget` read twice under the lock; lock held across config I/O).
- **B / design+test-honesty:** confirmed produce-only faithfulness, no overclaims;
  found **[MEDIUM] directive content untested** — A4's operator-budget binding had
  zero coverage; a regression to budget 0 / silent fallback would pass every test.
  Plus 3 LOW honesty nits.
- **Fixes:** free registries on session close (not on turn rollover); memoize the
  budget; add directive-content tests; honesty cleanups. Commits 743c5c3.

## External pass 1 — general panel (2026-08-06)

deepseek-v4-pro, x-ai/grok-4.5, mistral-medium-3-5, kimi-k3, glm-5.2. 5/5 returned.

### Accepted (fixed in dd4b5114)

- **[MEDIUM, grok] durable-subject truncation aliases distinct turns.** A `turn_id`
  longer than ~111 chars truncated to the same subject as another sharing its prefix,
  cross-contaminating turns in the persisted record. Fix: hash the turn_id when it
  won't fit intact; short ids stay readable. Test: `test_subject_..._without_aliasing`.
- **[defense-in-depth] SystemExit containment.** `observe_lifecycle` now catches
  `(Exception, SystemExit)` — NOT KeyboardInterrupt — so the next sys.exit-shaped host
  API can't crash the host past the guards (the class the already-fixed
  `get_config_value` belonged to). Test: `test_systemexit_from_host_api_is_contained`.
- **[LOW, grok] kill-switch robustness** to falsey non-bool values
  (`enabled: "false"/0/off/no/""`). Test: `test_kill_switch_honors_falsey_values`.
- **[LOW, kimi] seam import fragility** — import the observer independently of relay
  so a salience import failure can't starve relay dispatch/handling.
- **[LOW, kimi] mid-session-disable leak** — cleanup (session-close) is no longer gated
  on the enable check, so an open window is finalized+freed even if the kill switch is
  flipped mid-session. Test: `test_close_frees_even_when_gate_flips_off`.
- **test honesty:** cross-session isolation (`test_records_drop_across_sessions`),
  `_close_locked` idempotency (`test_close_locked_is_idempotent`).

### Rejected with evidence (reproduce-before-accept earning its keep)

- **deepseek CRITICAL — `get_hermes_home` SystemExit:** refuted; its source documents
  it must never raise ("would brick 30+ callers"), returns the platform default. (The
  general SystemExit class is still covered by the defense-in-depth fix above.)
- **mistral CRITICAL/HIGH cluster, all rejected:** KeyboardInterrupt-swallowing (wrong
  — would suppress the user's Ctrl+C); provider-unbounded (`_ref` already truncates the
  whole concatenated token — the proposed "fix" is the existing behavior); `_bus_for`
  race (callers already hold `_LOCK`; the proposed `with _LOCK` inside would DEADLOCK
  the non-reentrant lock); 64-bit session-hash collision (no adversary motive on a
  local per-session audit log; filename uses the full hash).
- **grok F2 lock-across-I/O:** the config-read half was already removed by the internal
  memoization; the bus write under the lock is *required* by the single-threaded bus
  contract; the re-entrancy trigger needs a pathological synchronous plugin. Accept-risk.

Post-fix: 28 tests pass; lifecycle/relay seam tests unchanged.

## External pass 2 — coding-specialist panel (2026-08-06)

qwen3-coder-plus, kat-coder-pro-v2.5, kimi-k2.7-code, laguna-s-2.1, grok-4.5 (anchor,
locked per Josh). 4/5 returned (kat-coder exhausted its budget on reasoning, empty
output). Run on the post-pass-1 code.

### Accepted (fixed in 0a36b2b7)

- **[convergent — grok LOW / laguna CRITICAL / kimi HIGH] gate-path SystemExit.**
  Pass-1's SystemExit containment was *incomplete*: it wrapped `observe_lifecycle`'s
  produce body but not the GATE path (`handles_hook → salience_enabled → _config_flag
  → read_raw_config_readonly`), which runs on the tool-call hot path via `has_hook`
  OUTSIDE that guard. A config helper that sys.exit()s there would still crash the
  host past every `except Exception`. Three independent code models caught it. Fixed
  at the source: `salience_enabled` (IS_QUORUM import), `_config_flag`,
  `_operator_budget`, `_close_locked` now catch `(Exception, SystemExit)` — never
  KeyboardInterrupt. Test: `test_gate_contains_systemexit_from_config`.
- **[grok, MEDIUM, test honesty] audit fence untested.** Nothing asserted tool
  args/results/messages were ABSENT from the durable record — only that tokens are
  ≤128, so a sabotage adding `args:`/`result:` to provenance would pass every test
  and turn the bus into a silent exfil channel. Added
  `test_bus_never_contains_tool_payload` (sentinel payload must not appear in JSONL),
  a seam return-value isolation test (guarantee 6), and `retryable=None` coverage.

### Rejected with evidence

- **qwen CRITICAL — KeyboardInterrupt/BaseException swallow:** would suppress the
  user's Ctrl+C; kimi independently concluded not-catching KeyboardInterrupt is
  deliberately correct. The legitimate SystemExit half is the accepted gate fix above.
- **qwen HIGH cross-session race:** no concrete trigger; already pinned by
  `test_records_drop_across_sessions` (pass 1).
- **qwen `_ref` unbounded growth** (fixed 2–3-arg arity), **qwen/laguna provider
  length** (already truncated by `_ref`), **qwen policy-key regeneration**
  (process-local by design — ADR 0002 claims no cross-process authenticity).
- **grok F3 memoize-the-gate:** would break the LIVE kill switch (the operator must be
  able to disable mid-run); the read is already the cached fast path relay uses.
- **laguna log-signal-content / append-before-publish:** logging content would violate
  the audit fence it's meant to protect; the fail-closed drop is correct.

Post-fix: 31 tests pass; lifecycle/relay seam tests unchanged.

## Outcome

Two full external passes (10 model-runs, two complementary rosters) plus two internal
reviewers. Net: one MEDIUM aliasing bug, one incomplete-containment gap, and one
untested-audit-fence gap were the real catches — all fixed and pinned; every louder
CRITICAL dissolved under reproduce-before-accept. Produce-only, fail-closed, hashed-
identity, and audit-fence guarantees hold. Ship.
