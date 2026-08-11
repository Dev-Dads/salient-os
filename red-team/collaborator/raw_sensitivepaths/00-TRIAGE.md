# PR #38 (sensitivepaths / Harm B) — external panel triage

External 5-vendor certification panel on the shipped PR 1a delta. Reproduce-before-accept: every
dissent was traced against the real code before disposition. **Total API cost: $1.1382.**

| Model | Cert | Verdict | Cost |
|---|---|---|---|
| anthropic/claude-opus-4.1 | **CERTIFIED** | SOUND | $0.8308 |
| x-ai/grok-4.5 | **CERTIFIED** | SOUND | $0.1359 |
| openai/gpt-5.1 | not-certified | MINOR_ISSUES | $0.0938 |
| qwen/qwen3-max | not-certified | SERIOUS_FLAWS | $0.0777 |
| google/gemini-2.5-pro | not-certified | (finish=error) | $0.0000 |

**Headline: CERTIFIED.** The two panels that actually *investigated* the serious vectors (opus, grok)
certified. The three not-certified votes reduce to two issues, both run down below; neither is a
reachable flaw.

## Dissent 1 — "proposer bypass via non-'proposed' source" (qwen F2 CRITICAL, opus SENS-001 HIGH→refuted)

Claim: a PROPOSER emits the command as `content_block`/`content_json`, dodging the `source=="proposed"`
deny, reaching a one-click-approvable HELD state.

**REFUTED (reproduced).** `source="proposed"` is set at exactly one site (`propose.py:170`);
`content_block`/`content_json` are produced ONLY by the turn parser (`toolcall.py:182,205,212`) — the
turn model's channel, never the proposer's. **opus investigated the identical vector and reached the
same conclusion** ("propose.py correctly hardcodes source='proposed' — bypass impossible, a non-finding
upon inspection"), and rt-1a verified end-to-end. A `content_*` command is the turn channel = the
accepted N1 scope: HELD-with-⚠, never auto-runs (autonomy floor), byte-identical to the certified
code-root deny. qwen's proposed fix (extend the deny to `content_*`) is **wrong** — user-directed
maintenance commands manifest as `structured`/`content_*`, so it would break the operator's
maintenance steer. Scope is owned explicitly in the govern deny comment (N1 doc fix).

## Dissent 2 — "names_sensitive_path can raise on a hostile __str__" (gpt-5.1/qwen/gemini/grok/opus)

Claim: a throwing `__str__` on a command element propagates out of `govern_action`/`approve`, violating
"never raise."

**Real but LOW + unreachable + pre-existing (reproduced).** `json.loads` yields only
`{bool, NoneType, int, float, str}` — never a hostile object — so a command cannot carry a throwing
`__str__` from any model/parser path. `names_code_root` (the sibling, which runs FIRST at every shared
call site) and `freeze_args` raise identically on the same shape (pre-existing; grok: "freeze_args
already throws first"). gemini's "CRITICAL/DoS" is overstated (requires injecting a Python object —
impossible for an attacker); grok + opus rate it LOW and CERTIFY.

**Disposition: FIXED for the new module (hygiene).** `names_sensitive_path` is now a TOTAL function
(try/except → "" fail-closed). Kept PR 1a tight: `codefence.names_code_root` and `freeze_args` share
the identical pre-existing, unreachable characteristic and are NOT re-opened here — a general
`govern_action` never-raise hardening (if wanted) is a separate, non-1a item, documented honestly in
the module docstring rather than overclaimed.

## Other panel notes (dispositioned)

- **qwen F3** (re-deny reads mutable args → TOCTOU): **refuted.** `loop.py:138`
  `args = freeze_args(dict(decision.args))` is the single frozen snapshot every downstream read
  (re-deny, seal, executor) uses; grok F2 concurs; the MINOR-B seal independently catches any
  post-hold mutation. The re-deny is correctly documented DiD-over-DiD.
- **opus SENS-003** (case-folding FP on case-sensitive Linux, `ID_RSA` matches): accepted +
  documented (cross-platform recall trade-off, audit-grade, proposer-deny only). Note + test added.
- **opus SENS-002 / gpt-5.1 ID 2** (non-dict args): same class as Dissent 2 — pre-existing
  `govern_action` assumption, not model-reachable (`json.loads` → dict). Out of 1a scope.

## Net

Zero reachable flaws. Headline CERTIFIED by both investigating panels + internal red-team (SOUND).
One hygiene fix applied (total-function recognizer); all dissents reproduced and refuted or
dispositioned. Suite 589 green.
