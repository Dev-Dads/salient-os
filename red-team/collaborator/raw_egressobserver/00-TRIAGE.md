# PR #40 egress observer — external certification triage

Two 5-vendor OpenRouter panels (OpenRouter, reproduce-before-accept). `precert/` holds the FIRST panel
(pre-fix code); this dir holds the RE-CERT (final code). Per-model + total cost reported below.

## First panel (pre-fix code) — `precert/` — $1.19
- opus-4.1: **SOUND / CERTIFIED**.
- grok-4.5: **NOT-CERTIFIED** — F-01 (HIGH): SYN-only rule misses a pre-established connection reused
  in-window → a false strong-tier True.
- qwen: **NOT-CERTIFIED / SERIOUS_FLAWS** — ID-03 (CRITICAL): `_parse_nft_set` silently returns an empty
  set on malformed nft-JSON → a false True when the claim is also empty.
- gpt-5.1: **NOT-CERTIFIED / MINOR** — F1: strong-tier multiplicity (duplicate claims) mis-scored True.
- gemini: truncated (finish=error, $0).

### Fixes applied (all live-proven on Sparky, real nft)
1. grok F-01: OUTPUT rule matches **ALL** outbound TCP, not just the SYN.
2. qwen ID-03: `_parse_nft_set`/`_elem_concat` fail **CLOSED to None** on ANY parse/structural failure —
   including a `set` object whose elements are present but don't decode to `(ip,port)` (my first pass only
   Noned when NO set object; the malformed-**elements** sub-case still returned empty = the same false-True.
   Caught by reproduce-before-accept.)
3. gpt-F2: `end()` strong-read failure → `reconciled=None`, tier STAYS strong (distinct from no-vantage).
4. grok F-03: `begin()` honors `install()` failure → UNAVAILABLE (no stale-table attribution).
5. grok F-05: `_canon_ip` both sides kills the IPv6 text-form false discrepancy.
6. gpt-F1: reconcile's verdict is the DESTINATION SET, not connection multiplicity — the strong counter is
   PACKETS (≠ connections), so gpt's literal conn-count fix is unsound (would false-flag every multi-packet
   request). Over-claim of a repeat to an already-observed dest = True; a HIDDEN new dest = unexpected/False.
   Documented + tested.
7. gpt-F4/F6, grok-F-04: availability probe caches only a POSITIVE tier (a transient none self-heals).
8. gpt-F3: `_resolve` requires an executable (no sudo-prompt hang).
Plus a CI robustness fix: the all-packets rule widened same-uid co-tenant false-POSITIVES, so the live
"legit egress" proof asserts the per-dest guarantee (observed + not mis-flagged) with a separate
True-reachability retry, instead of machine-wide `reconciled is True` (flaked on GitHub's shared-uid runner).

## Re-cert (final code) — this dir — $1.61
- gpt-5.1: **SOUND / CERTIFIED** — every false-True attack marked BLOCKED.
- opus-4.1: **SOUND / CERTIFIED** — all 4 headline claims.
- grok-4.5: **CERTIFIED / MINOR_ISSUES**.
- qwen: **NOT-CERTIFIED** — sole dissent: `install()` deletes a pre-existing table named `salient_obs`
  (its own steelman concedes the name is SalienceOS-specific/unlikely to collide).
- gemini: truncated (finish=error, $0).

### Two remaining items applied (both strictly-safer, both what the panel prescribed; no 3rd panel — spend matches risk)
- **Observer isolation** (gpt + grok single highest-value fix): `begin()`/`end()` called via
  `tools._observe_begin`/`_observe_end`, which catch any raise and degrade to None — a future observer bug
  can never propagate into governance's `except→FAILED` to convert a good egress into FAILED.
- **uid-scoped table name** (qwen close): `_NFT_TABLE = salient_obs_u<uid>` — the idempotent reinstall can
  never delete a foreign same-name table; the "never disturb a host firewall" claim is now airtight.

## Cost
| panel | model | cost |
|---|---|---|
| pre-fix | (5-vendor) | **$1.19** |
| re-cert | openai/gpt-5.1 | $0.1539 |
| re-cert | google/gemini-2.5-pro | $0.0000 (truncated) |
| re-cert | anthropic/claude-opus-4.1 | $1.1545 |
| re-cert | x-ai/grok-4.5 | $0.1853 |
| re-cert | qwen/qwen3-max | $0.1129 |
| re-cert | **subtotal** | **$1.6066** |
| | **TOTAL (both panels)** | **$2.80** |
