# emission-quota code panel: x-ai/grok-4.5

_finish=stop seconds=125.0 cost=$0.1001 usage={'prompt_tokens': 32032, 'completion_tokens': 6049, 'total_tokens': 38081, 'cost': 0.1001404, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1001404, 'upstream_inference_prompt_cost': 0.0638464, 'upstream_inference_completions_cost': 0.036294}, 'completion_tokens_details': {'reasoning_tokens': 3982, 'image_tokens': 0, 'audio_tokens': 0}}_

# Adversarial review: emission-quota + method→cap

## Findings

### F1 / QUOTA DICT KEYS NOT CANONICALIZED — SILENT FAIL-OPEN / MEDIUM
**Location:** `collaborator/session.py` — `_validate_emission_quota` (~lines that build `out[host] = cap`) and `_emission_limit` (`q.get(host)`).

**Concrete case:**
```python
s = Session(workspace=d, emission_quota={"API.Example": 2})  # or "api.example.", or "https://api.example"
# emit_host from egress.canonical_host("https://api.example/v1/x") == "api.example"
# _emission_limit("api.example") -> q.get("api.example") -> None -> unlimited
```
Operator believes the host is capped at 2; session emits forever.

**Why it fails open:** Check/consume always key on `egress.canonical_host(...)` (lowercase NFC/IDNA, no trailing dot, bare host). Validation only type-checks `str → non-neg int` and stores the operator string **verbatim**. Any non-canonical key never matches → `None` → unlimited. Same class: IDN display form vs punycode.

**Other control?** Cap/leash/byte caps still apply (WHERE/HOW BIG), not HOW MANY. No second count bound.

**Fix:** At validate time, normalize every dict key to the canonical host form and reject keys that do not round-trip, e.g. require bare hosts and:
```python
canon = canonical_host("https://" + host)  # or a bare-host normalizer sharing canonical_host rules
if canon is None or canon != host:  # after normalizing, prefer store canon
    raise ValueError(...)
out[canon] = cap
```
Prefer: normalize then store `canon`, so `{"API.Example": 2}` becomes `{"api.example": 2}` (or reject loudly — reject is clearer).

**Verdict on “trusted config”:** Real operator trap, not a model bypass. Still worth fixing: the rest of this surface fails **loud** on typos (`proactivity`, `leash_overrides`, bool quota). Silent disable is inconsistent and will bite.

---

### F2 / NO APPROVE-PATH E2E PIN — COVERAGE GAP / LOW
**Location:** `tests/test_collaborator_emission_quota.py` (`QuotaEndToEnd` only drives autonomous `_emit` → `govern_action`); consume hook is `execute_and_verify` egress_log+mutating branch used by `loop.approve` as well.

**Concrete case:** Hold `net_post` → `approve(session, d)` N+1 times with `emission_quota={"api.example": N}`. Code path review says consume runs once at approve dispatch (HELD does not consume). **Not pinned by test.**

**Why it matters:** Hold→approve is the default emission path. A future refactor that checks quota only when `not human_gated` would green the suite and unbind the common path.

**Other control?** None for count.

**Fix:** One test: quota=1, first approve RAN + count==1, second approve DENIED `"quota exhausted"`. Optionally assert HELD does not increment.

---

### F3 / POST-INIT `emission_quota` MUTATION BYPASSES VALIDATION / LOW
**Location:** `Session.__init__` assigns `self.emission_quota = self._validate_emission_quota(...)` once; no property setter.

**Concrete case:**
```python
s = Session(workspace=d, emission_quota=3)
s.emission_quota = True   # bool is int subclass; validation skipped
# _emission_limit -> True == 1; or False == 0 (block all); or {"H": True} etc.
```
Model cannot do this through tools; host/runtime bug or a confused wrapper can.

**Other control?** Construction path is solid (`isinstance(q, bool)` before `int`).

**Fix:** Store behind a validated `@property` / freeze config after init, or re-validate in `_emission_limit` (cheap). Optional hardening only.

---

### F4 / QUOTA HOOK COUPLED TO `verify_mode == "egress_log"` ∧ `egress` ∧ `mutating` / LOW (scope)
**Location:** `governance.py` `execute_and_verify` — quota only inside `if tool.verify_mode == "egress_log:"` → `if egress and mutating`.

**Concrete case:** A future/mis-wired emitter with `egress=True`, `mutating=True`, `verify_mode="none"` (or `"exit"`) hits the read/other branch, calls `execute_tool` with **no** quota check/consume. Same mis-wire also drops Tier-2 credential/preview discipline — so this is “tool definition is load-bearing,” not a second dispatch for today’s `net_post`.

**Other control?** Capability gate still requires `net.post:<host>` if `egress_method` is correct.

**Fix (defense-in-depth):** Gate on `(egress and mutating)` **before** verify_mode branching (or assert net_post’s triad in tests). Not a ship-blocker if tool defs stay pinned.

---

## Attack checklist (explicit)

### 1. Quota bypass
| Path | Result |
|------|--------|
| Autonomous `govern_action` → `execute_and_verify` | Checked + consumed |
| `approve` → `execute_and_verify` | Same branch; HELD does not consume |
| `emit()` | → `govern_action` only |
| Direct `egress.post` | Outside seam by design (not model-reachable via `run_turn`) |
| `web_fetch` / non-mutating egress | Intentionally not counted (documented) |
| `verify_mode` other than `egress_log` | Only if tool misconfigured (F4) |
| `reauthorized_or_denied` | No emit; quota correctly deferred to dispatch |
| Retry after FAILED | Consumed before dispatch — retry channel bounded (tested) |
| `getattr` missing methods | Legacy no-quota; real `Session` always has methods — **not fail-open for shipped Session** |

**No model-reachable bypass of a correctly keyed quota** found.

### 2. Miscount
| Case | Result |
|------|--------|
| Off-by-one | `count < limit` → N allowed, N+1 denied; `limit=0` blocks all — correct |
| HELD | No consume |
| HELD→approve | One consume at dispatch |
| DENIED (incl. over-quota) | No consume (check before consume) |
| FAILED after consume | Burns quota — intentional |
| Double consume approve | No |
| Concurrency | Out of scope (single-threaded loop) — OK as documented |

### 3. Model reachability
- `emission_quota` / `_emission_counts` / `emission_allowed` / `consume_emission`: host session API, not tool args.
- Caps/leash still from signed grant + host leash; model cannot widen quota via `capabilities` / `leash_overrides`.
- Bool rejected at construction — good.
- No finding: model cannot set quota or reset counters through the governed path.

### 4. Fail-open
- Malformed quota at construction: fail loud — good.
- **Canonical key mismatch: real silent fail-open (F1).**
- `host is None`: `emission_allowed` True + no consume; upstream capability/`canonical_host` denies real emit — OK additive bound.

### 5. Method→cap regression
```text
GET/HEAD (any case) → net.get:<host>
POST               → net.post:<host>
other / junk       → None → DENY
None/""            → GET (unchanged)
```
- Case folded via `.upper()`; whitespacey methods DENY — fail-closed.
- Call sites already DENY on `None`.
- No legitimate GET/POST break.
- Method comes from `tool.egress_method`, not model args — good.
- Default `GET` if `egress_method` missing is pre-existing tool-wiring footgun (read cap for a POST tool), not introduced as a new open write verb; new verbs no longer inherit read. **Method→cap change is sound.**

---

## STEELMAN

The quota is hooked at the true single byte-leaving chokepoint shared by autonomous and human-approved paths; consume-before-dispatch correctly bounds retries; construction validation blocks the classic `True`-as-int footgun; getattr preserves legacy sessions without inventing an allow path. The method→cap change closes a real latent gap (write verb → read cap) without breaking current tools, and fail-closed `None` matches how the gate already works. Documented non-goals (per-session, count-not-content, web_fetch uncounted) are coherent.

## VERDICT: **MINOR_ISSUES**

No model-reachable quota bypass, miscount on hold/approve/deny/fail, or method→cap fail-open in the shipped seam; the one real defect is **operator-facing silent disable when dict keys ≠ `canonical_host` output**.

**Single highest-value fix:** Canonicalize (or reject non-canonical) `emission_quota` dict keys in `_validate_emission_quota` so they must equal what `consume`/`emission_allowed` look up — fail loud, same as every other host config dial on this object.
