# ② Stage B — surface.py external code panel: disposition

5-vendor OpenRouter panel (gpt-5.1, gemini-2.5-pro, opus-4.1, grok-4.5, qwen3-max), $0.7688.
Design was pre-paneled separately (see `08-seam-page-design.md` disposition); this certifies the
SHIPPED code.

## Certifications

| Claim | Result | Notes |
|-------|--------|-------|
| **C2 — P-01 / no authority via the door** | **CERTIFIED 5/5** | Every reporter confirmed surface.py imports no governance/policycaps and calls only `host.submit()`/`host.snapshot()`; a hostile submit body only becomes a governed model directive. The thesis holds in the code. |
| **C4 — scope honesty (watch-only B)** | **CERTIFIED 5/5** | Only `submit` + watch exposed; held/paused shown honestly ("Stage C adds the button"); no hidden lever. |
| **C1 — door integrity** | **CERTIFIED 4/5** | gpt-5.1, grok, qwen, opus certified: no CSRF / rebind / simple-read / token-leak / single-use-race bypass found. gemini's NOT-CERTIFIED couples C1 to the availability DoS (really a C3 point). |
| **C3 — availability / can't be darkened** | **CERTIFIED 3/5** → **fixed** | opus, qwen, gpt-5.1 certified; gemini + grok NOT-CERTIFIED on pre-auth slot exhaustion (below). Addressed. |

Verdicts: **SOUND ×2** (opus, qwen), **MINOR_ISSUES ×3** (gpt-5.1, gemini, grok). No CRITICAL/HIGH
authority finding — the one HIGH (grok) was the availability slot-exhaustion, now fixed.

## The one real finding — FIXED

**Pre-auth connection-slot exhaustion / slowloris darkening (gemini S-1, grok F1; C3/D4).** The
bounded semaphore is acquired in `process_request` BEFORE `_guard_authed`, so an UNAUTHENTICATED
local process could rotate connections (slowloris on the *header* phase — my earlier 5 s body-read
deadline only covered the body) to hold all 16 slots for the full **15 s** request timeout,
transiently darkening the live `/state` view.

- **Fix:** `DEFAULT_REQUEST_TIMEOUT` 15 s → **5 s** (bounds the WHOLE request incl. headers, so any
  slot-hold self-heals in ≤5 s and a delayed poll retries). Cookie parsing replaced with a cheap
  bounded manual split (kills gemini's "SimpleCookie parse cost on a hostile header" angle).
- **Honest residual (documented in `_BoundedThreadingHTTPServer`):** a local process can still
  briefly degrade the live VIEW; it can NEVER corrupt task state, lose/hide a held action from the
  Host's record, forge authority, or make `/state` lie. Defeating a local process bent on degrading
  a local service (it could spike CPU / exhaust FDs itself) is outside a loopback single-user threat
  model. The guarantee kept is **integrity, not local availability**.

## Nits addressed

- **`/state` Origin pin** (gemini, gpt-5.1): `/state` now Origin-pinned like `/submit` (defense-in-
  depth beyond the custom-header wall). + regression test `test_cross_origin_state_forbidden`.

## Accepted as documented trades (not bugs)

- **429 cap not atomic with enqueue** (grok F2): a concurrent burst can overshoot `max_pending` by
  O(max_connections) — bounded (≤~64), self-corrects, is a soft DoS backpressure not an invariant.
- **Single-use-bootstrap prefetch race** (gpt-5.1): a local prefetcher can spend the token before
  navigation → re-run the launcher for a fresh URL; no external unfurler reaches loopback. Panel
  agreed this is an availability trade, not a door break; documented in `_consume_bootstrap`.
- **CSP `connect-src` only on HTML, minimal on JSON** (gpt-5.1): correct — JSON needs no script CSP.
- **`shell.exec` breadth** (gpt-5.1): a Stage-A Host grant, not something the surface widens.

Full suite green (28 surface tests; 848 total, lone pre-existing Windows-only netns quirk).
