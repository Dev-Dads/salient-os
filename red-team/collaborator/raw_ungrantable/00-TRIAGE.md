# ungrantable-namespace CODE panel — triage

External CODE panel on the shipped core change (the structurally un-grantable `offense:` namespace,
ADR 0004 / ADR 0003 revisit #4). Runner `redteam_ungrantable.py`. Two rounds; `precert/` holds the
first (pre-fix) round.

## First round (pre-fix) — `precert/` — $0.52
- gpt-5.1 / opus-4.1 / qwen: **SOUND** — `grants_capability`'s unconditional refusal blocks every
  offense-grant attempt.
- grok-4.5: **MINOR_ISSUES** — `mint` is only ONE construction path; a valid signed grant built
  directly (the operator holds the HMAC key) could carry offense caps into the read path
  (`granted_capabilities`). "Operator cannot even construct" overclaims.
- gemini-2.5-pro: **NOT-CERTIFIED** — a full-width Unicode `ｏｆｆｅｎｓｅ：` bypasses `casefold()` (no
  compatibility normalization) and is not caught.

### Fixes applied
1. gemini: **NFKC-normalize** before the check (`ｏｆｆｅｎｓｅ：` → `offense:`), then casefold. `unicodedata`
   added to the core stdlib allowlist deliberately. (Reproduced: the full-width form returned False;
   NFKC catches it. Impact was low — a full-width cap grants nothing today and can't unlock a future
   ASCII-deriving tool — but a NOT-CERTIFIED on a core security namespace is closed, not argued.)
2. grok/gpt: **`granted_capabilities` strips** the namespace on both read paths, so offense never rides
   into the seam even from a hand-built foreign grant. Load-bearing guarantee stays core's
   `grants_capability` refusal.

## Re-cert (fixed code) — this dir — $0.55
- **5/5 CERTIFIED / SOUND** — gpt-5.1, opus-4.1, qwen, grok, gemini all certify: no in-band path grants
  an `offense:` capability (case/NFKC confusables + hand-built directives included), and no legitimate
  capability is broken.
- gemini LOW **FRAG-01**: `issue_policy(granted_capabilities=None)` raises `TypeError` (not a bypass;
  pre-existing — `tuple(None)` already raised before this change; unreachable — `granted_capabilities`
  always returns a tuple). Guarded anyway (a malformed input → zero capabilities, hardest fail-closed),
  since the line was already being edited and the core values total boundary functions.
- Documentation notes (gpt/opus/grok): institutionalize "grants_capability is the sole authority
  accessor" so no future consumer reads `allowed_capabilities` directly — the directive.py docstring
  already states this; a lint/review check is a reasonable follow-up.

## Cost
| round | cost |
|---|---|
| pre-fix | $0.52 |
| re-cert | $0.55 |
| **CODE total** | **$1.07** |

(Design panel on the deferred unlock was a further $1.05 — see `raw_scopeartifact/00-TRIAGE.md`.
Thread #3 external review total: **$2.12**.)
