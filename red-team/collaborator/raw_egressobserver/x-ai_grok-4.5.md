# PR#40 egress-observer certification panel: x-ai/grok-4.5

_finish=stop seconds=223.0 cost=$0.1853 usage={'prompt_tokens': 63773, 'completion_tokens': 9659, 'total_tokens': 73432, 'cost': 0.1852824, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1852824, 'upstream_inference_prompt_cost': 0.1273284, 'upstream_inference_completions_cost': 0.057954}, 'completion_tokens_details': {'reasoning_tokens': 8251, 'image_tokens': 0, 'audio_tokens': 0}}_

# Adversarial review — PR #40 independent egress observer

## Findings

### F-01 / Observer exception would fail a good egress (P-01 belt missing) / LOW  
**Location:** `collaborator/tools.py` (`_exec_web_fetch` ~begin/fetch/end; `_exec_net_post` same); `collaborator/governance.py` (`execute_and_verify` egress branch `except Exception → FAILED`)  
**Concrete input:** Any unexpected exception from `end()`/`reconcile()` *after* a successful `egress.fetch` (e.g. future change that lets `int(port)` or set arithmetic throw; today `_egress_claimed` only passes `(resolved_ip, 443)`).  
**Why:** Fetch already succeeded; governance maps *any* executor exception to `FAILED`/`cleared=False`. That would make an observer fault fail an otherwise-good egress — against “evidence-only / never blocks.”  
**Independent catch:** Current `begin`/`end`/`reconcile`/`_nft_snapshot`/`_proc_snapshot` are written to return None/False and not raise on nft/proc/parse failures (`TimeoutExpired` ⊂ `SubprocessError`). No live raise path found with the shipped claimed shape.  
**Fix:** Wrap `begin`/`end` in the executors: on exception set `obs=None` (or `ReconcileResult(reconciled=None, …)`) and still return the egress `Execution` with `ok` from the record.

---

### F-02 / IPv4-mapped / dual-stack text mismatch → false discrepancy (not false True) / LOW  
**Location:** `collaborator/egressobserver.py` — `_canon_ip`; `reconcile` observed vs claimed sets  
**Concrete input:** Kernel/nft emits `::ffff:a.b.c.d` in `dests6` while `EgressRecord.resolved_ip` is `a.b.c.d` (or rare Happy-Eyeballs/multi-A/AAAA noise if anything other than the pinned IP appears in-window).  
**Why:** `_canon_ip("::ffff:1.2.3.4")` ≠ `_canon_ip("1.2.3.4")` → `unexpected` / `claimed_unobserved` → `reconciled=False`.  
**Guarantee impact:** Audit noise only; does **not** mint `True`. P-01: status/cleared unchanged. Matches documented dual-stack/CDN residual class.  
**Independent catch:** None needed (evidence-only). Live path pins one IP via egress.py.  
**Fix (optional):** Map IPv4-mapped IPv6 down to IPv4 in `_canon_ip` before compare.

---

### F-03 / Non-findings explicitly attacked (blocked or documented residuals)

| Attack | Result |
|--------|--------|
| `/proc` clean → `True` | **Blocked:** `reconcile` only sets `True` for `TIER_STRONG`; proc clean → `None` (`egressobserver.py` reconcile branch). |
| Empty/failed nft JSON → empty set → `True` | **Blocked:** `_parse_nft_set` → `None` on any structural/undecodable element; `_nft_snapshot` → `None`; `end` → `reconciled=None`, tier stays `strong`. |
| `None` snapshot fall-through → `True` | **Blocked:** `end` explicit failed-read path; unavailable → `None`. |
| Claimed-but-unobserved at strong → `True` | **Blocked:** `claimed_unobserved` → `False`. |
| Pre-established TCP reuse in-window | **Caught:** rules match all `meta l4proto tcp`, not SYN-only (`_nft_ruleset`). |
| Second in-process client / wrong dest (same uid, TCP, non-loopback) | **Caught** at strong (`unexpected`); live proof pins it. |
| `nft flush ruleset` / other tables / `drop`/`reject` | **Blocked:** only `delete table inet salient_obs` + `-f` body for that table; `policy accept`; tests pin argv/body. |
| sudo password hang | **Blocked:** `sudo -n`; `_resolve` requires `X_OK`. |
| Probe cache stuck `none` forever | **Blocked:** only positive tier cached. |
| `begin` after failed `install` attributing stale table | **Blocked:** `begin` → `TIER_UNAVAILABLE`. |
| Different uid / UDP-DNS-QUIC / loopback forward proxy / between-windows idle / root flush / same-uid co-tenant FP / setup delete-create race | **Documented residuals** — not claimed; not reported as defects. |
| Same dest:443 multiplicity under-claim | **By design** (dest **set**, not conn count); hidden *new* dest still `False`. |

---

## Certification line

**CERTIFIED** — With the code as shipped, `reconcile` cannot return a false `True` (proc/empty/failed/None paths are `None` or `False`); a same-uid TCP second client or wrong non-loopback dest is observed and surfaced at the strong tier; discrepancy/observer soft-failure does not change `RAN`/`cleared` (P-01 holds on all non-raising paths); the module only creates/deletes `table inet salient_obs` with `policy accept` and never flushes or drops.

---

## Steelman

The design correctly separates channel-integrity logging from kernel vantage, forces tri-state honesty (especially “proc never verifies”), and fail-closes parse/install/read errors to unchecked instead of a clean confirm. Scoping nft to one named table with accept-only rules, `sudo -n`, positive-only probe cache, dest-set (not packet-counter) verdicts, and all-TCP matching close the serious panel holes without turning the observer into an enforcer. Wiring keeps reconciliation on `Decision` as evidence while clearance stays on the egress record alone.

---

## Verdict

**MINOR_ISSUES** — Headline guarantees hold under adversarial review; remaining gaps are LOW defense-in-depth (try/except around observer in executors) and acknowledged false-positive noise, not false “verified” or firewall blast radius.

**Single highest-value fix:** In `_exec_web_fetch` / `_exec_net_post`, isolate observer `begin`/`end` failures so they never take the governance `except Exception → FAILED` path after a successful mediated fetch.
