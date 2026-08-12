# Emission-quota + method->cap CODE panel — disposition (ADR 0003 residual sweep)

5-vendor external CODE panel on the SHIPPED per-destination emission-quota + method->cap fail-closed
diff (a lean, matched-to-risk single pass: a bounded session counter + a fail-closed capability
mapping, no privileged/network surface).

## Verdicts + cost

| model | verdict | cost |
|---|---|---|
| openai/gpt-5.1 | (F1 CRITICAL — refuted) + F2 canonical footgun | $0.0650 |
| google/gemini-2.5-pro | canonical footgun CRITICAL | $0.1080 |
| anthropic/claude-opus-4.1 | MINOR_ISSUES (canonical footgun) | $0.6176 |
| x-ai/grok-4.5 | MINOR (canonical footgun); method->cap SOUND | $0.1001 |
| qwen/qwen3-max | HIGH (canonical footgun); approve-bypass self-refuted | $0.0297 |
| **TOTAL** | | **$0.9205** |

## Findings + disposition

1. **Canonical-host-key footgun** — ALL 5 flagged (CRITICAL/HIGH/MEDIUM). `emission_quota` dict keys were
   stored verbatim but the runtime lookup uses `egress.canonical_host()` (lowercase/NFC/IDNA), so
   `{"API.Example": 1}` silently never matched `"api.example"` → the quota quietly did nothing. Not a
   model bypass (host config is trusted), but a SILENT fail-open, and INCONSISTENT with the rest of the
   surface (proactivity / leash_overrides / a bool quota all fail LOUD on a typo). **FIXED:**
   `_validate_emission_quota` now canonicalizes each key via `egress.canonical_host("https://"+host)` at
   construction, and fails LOUD on a key that is not a valid canonical host OR on two keys colliding to
   one canonical host. Pinned: `test_dict_keys_are_canonicalized...`, and the reject-loud cases.

2. **"HELD-emission quota bypass via approve" — gpt CRITICAL, REFUTED.** Claim: a held emission skips the
   quota. But `approve()` (loop.py:237) routes through the SAME `execute_and_verify` egress_log branch,
   so it both CHECKS and CONSUMES the quota there — qwen reviewed the identical path and concluded "NOT a
   bug." Reproduced + pinned: `test_approve_path_enforces_and_consumes_quota` (held -> approve consumes
   one; a second held -> approve is DENIED over quota).

3. **Method->cap fail-closed** — grok + qwen explicitly SOUND: GET/HEAD->net.get:, POST->net.post:, any
   other verb -> None -> DENY (no write verb inherits the read cap); None/"" default to GET (unchanged).
   No regression to the only live verbs (GET/POST).

## Not-findings (documented scope, confirmed by the panel)

Per-SESSION counter (cross-session durable store is a noted follow-up — enough to bound a runaway run);
a COUNT bound, not content inspection; `web_fetch` (GET, non-mutating) deliberately not counted; the
getattr-guard means a legacy/lightweight session simply has no quota (unchanged behaviour); off-by-one
correct (`count < limit`: Nth allowed, N+1 denied; limit=0 blocks all).

## Net

The one real issue (silent canonical-key fail-open) is fixed to fail LOUD; the one "critical" bypass was
a misread, reproduced-and-refuted + pinned. Pure-Python governance logic — fully covered by the
cross-platform suite (688 tests green), no OS-specific behaviour, so no Sparky proof needed.
