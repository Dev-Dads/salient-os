# PR#40 egress-observer certification panel: qwen/qwen3-max

_finish=stop seconds=41.4 cost=$0.1129 usage={'prompt_tokens': 60242, 'completion_tokens': 2432, 'total_tokens': 62674, 'cost': 0.11294712, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.11294712, 'upstream_inference_prompt_cost': 0.09397752, 'upstream_inference_completions_cost': 0.0189696}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

**FINDING 1 / FALSE TRUE FROM RACY PROC FALLBACK WHEN NO CONNECTIONS EXIST**  
**SEVERITY**: HIGH  
**LOCATION**: `collaborator/egressobserver.py:432` (`reconcile` function)  
**CONCRETE INPUT OR BYPASS**: On a Linux host with no strong tier (e.g., no passwordless sudo), run an egress that fails *before* connecting (e.g., invalid URL). `_egress_claimed` returns `[]`. The `/proc` fallback sees no sockets → `before.dests == after.dests == empty`.  
**WHY IT BREAKS A GUARANTEE**: `reconcile` computes `observed = after.dests - before.dests = empty`, `unexpected = empty`, then hits the `else` branch for `TIER_PROC` and returns `reconciled=None`. **However**, if the egress *succeeds* but the `/proc` snapshot races and misses the connection (point-in-time sampling), `observed` is empty while `claimed` is non-empty. The code correctly returns `None` here. **The real issue**: when `claimed` is empty (egress refused pre-connect) and `/proc` sees nothing, the result is `None` — which is correct. **No false True occurs**. The test `test_proc_clean_is_unchecked_not_a_false_confirm` explicitly covers this.  
**WHETHER ANOTHER CHECK CATCHES IT**: N/A — no vulnerability exists. The logic correctly returns `None` for all `/proc` cases without discrepancy.  
**FIX**: None needed.

---

**FINDING 2 / FALSE TRUE FROM EMPTY NFT SNAPSHOT DUE TO TIMEOUT EXPIRATION**  
**SEVERITY**: CRITICAL  
**LOCATION**: `collaborator/egressobserver.py:258` (`_nft_ruleset`) and `collaborator/egressobserver.py:309` (`_nft_snapshot`)  
**CONCRETE INPUT OR BYPASS**: The nft ruleset sets a 10-minute timeout on dynamic set elements. If an egress action completes successfully but the `end()` call is delayed >10 minutes (e.g., due to system load or debugger pause), the destination element expires from the set. `_nft_snapshot` reads an empty set → `after.dests = before.dests = empty` → `observed = empty`. If the record claims a destination, `claimed_unobserved` is populated and `reconciled=False` (correct). **But if the egress fails pre-connect** (`claimed=[]`), then `unexpected = empty`, `claimed_unobserved = empty`, and `tier == TIER_STRONG` → `reconciled=True`.  
**WHY IT BREAKS A GUARANTEE**: A failed egress (no bytes sent) that times out in the observer produces a false "clean" confirmation. The kernel *did not observe* the claimed destination (because there was none to claim), but the strong tier incorrectly asserts `True` instead of `None` or `False`. This violates "never returns a false True".  
**WHETHER ANOTHER CHECK CATCHES IT**: No. The governance layer only checks `rec.ok`; it does not correlate observer timing.  
**FIX**: In `reconcile`, when `tier == TIER_STRONG` and `claimed_set` is empty, `reconciled` must be `True` **only if** `observed` is also empty. But if `claimed_set` is empty and `observed` is non-empty, that’s `unexpected` (already handled). The bug is when `claimed_set` is empty and `observed` is empty: this should be `True` **only if the egress actually succeeded**. However, `reconcile` has no access to `rec.ok`. The correct fix is: **an empty `claimed_set` means "no expected egress", so any `observed` dest is `unexpected` (already done), and no `observed` dest should yield `reconciled=True` only if the action was supposed to egress nothing**. But the current contract is that `claimed` comes from `_egress_claimed(rec)`, which is empty iff `rec.resolved_ip` is None — i.e., the egress failed before IP resolution. In that case, **any observed egress is unexpected**, but **no observed egress is expected**. So `reconciled=True` is correct. **However**, the timeout expiration means a *successful* egress could appear as no observation, leading to `claimed=[("x",443)]`, `observed=[]` → `claimed_unobserved` non-empty → `reconciled=False` (correct). The only problematic case is a **successful egress that times out**, which yields `reconciled=False` (a false negative, not a false positive). **Therefore, no false True occurs from timeout**.  
**RE-ANALYSIS**: The 10m timeout is longer than any reasonable egress window. The `begin()`/`end()` window is per-action and short-lived. Expiration during the window is implausible. The test suite includes live proofs that successful egress reconciles clean. **Not a finding**.

---

**FINDING 3 / HOST FIREWALL DISTURBANCE VIA TABLE NAME COLLISION**  
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/egressobserver.py:67` (`_NFT_TABLE = "salient_obs"`)  
**CONCRETE INPUT OR BYPASS**: If the host already has an nft table named `inet salient_obs` (e.g., from another SalienceOS instance or admin config), `install()` deletes it unconditionally. This destroys the existing table, potentially disrupting firewall rules.  
**WHY IT BREAKS A GUARANTEE**: The certification claim states "the module can NEVER disturb a host firewall". Deleting a pre-existing table violates this.  
**WHETHER ANOTHER CHECK CATCHES IT**: No. The code assumes exclusive ownership of the table name.  
**FIX**: Use a unique table name (e.g., include UID or PID) or check for existence before deletion. However, the design requires a fresh table per window to avoid cross-action contamination. **Mitigation**: The table is re-created immediately after deletion, so the disturbance window is small. But deletion of a pre-existing table is still a violation.  
**STATUS**: Acknowledged residual? The PR states "STRICTLY SCOPED... ONLY ever creates/deletes `table inet salient_obs`". It does not claim the table name is unique. **This is a finding**.

---

**FINDING 4 / CRASH ON MALFORMED NFT JSON WITH NON-STRING PORT**  
**SEVERITY**: LOW  
**LOCATION**: `collaborator/egressobserver.py:224` (`_parse_nft_set`)  
**CONCRETE INPUT OR BYPASS**: nft emits an element with a non-integer port, e.g., `"concat":["1.1.1.1","https"]`. The code catches `ValueError` in `int(concat[1])` and returns `None`.  
**WHY IT BREAKS A GUARANTEE**: It doesn’t crash; it fails closed to `None`. The test `test_present_but_undecodable_elements_are_none_not_empty` confirms this.  
**WHY IT’S NOT A FINDING**: The code correctly handles this. No crash, no false True.

---

**FINDING 5 / EGRESS EVASION VIA IPV6 LOOPBACK NOT EXCLUDED**  
**SEVERITY**: LOW  
**LOCATION**: `collaborator/egressobserver.py:258` (`_nft_ruleset`)  
**CONCRETE INPUT OR BYPASS**: The IPv6 rule excludes `::1`, but what about other loopback addresses like `::ffff:127.0.0.1` (IPv4-mapped IPv6)? The rule `ip6 daddr != ::1` does not exclude these.  
**WHY IT BREAKS A GUARANTEE**: An egress to `::ffff:127.0.0.1` would be observed, but the design excludes loopback to avoid noise. This is a minor gap.  
**WHETHER ANOTHER CHECK CATCHES IT**: The egress client’s `is_safe_public_ip` blocks loopback, so such a destination would be refused pre-connect. Thus, no egress can legitimately target this address.  
**STATUS**: Not a finding — the transport contract blocks it upstream.

---

**FINDING 6 / FALSE POSITIVE FROM CDN DUAL-STACK**  
**SEVERITY**: LOW  
**LOCATION**: `collaborator/egressobserver.py:432` (`reconcile`)  
**CONCRETE INPUT OR BYPASS**: A CDN resolves to both IPv4 and IPv6. The egress client connects to one (e.g., IPv4), but the kernel observes both if connections are made (unlikely for a single fetch). More likely, the record claims one IP, but the observer sees the same IP in a different textual form (e.g., expanded vs compressed IPv6).  
**WHY IT BREAKS A GUARANTEE**: It doesn’t. The code canonicalizes IPs on both sides (`_canon_ip`), so `2001:db8::1` and `2001:db8:0:0:0:0:0:1` compare equal. The test `test_ip_canonicalization_kills_ipv6_textform_false_discrepancy` confirms this.  
**STATUS**: Not a finding.

---

**FINDING 7 / SUDO HANG IF SUDO PROMPTS DESPITE `-n`**  
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/egressobserver.py:85` (`_resolve` and `_NFT` construction)  
**CONCRETE INPUT OR BYPASS**: A `sudo` wrapper that ignores `-n` and prompts anyway (e.g., custom PAM module). `_run_nft` would hang until timeout (10s).  
**WHY IT BREAKS A GUARANTEE**: The availability probe would time out and fall back to `/proc`, which is acceptable. The egress itself would also time out and return `None` for the observer tier. No hang beyond the timeout.  
**WHETHER ANOTHER CHECK CATCHES IT**: The timeout in `_run_nft` prevents indefinite hangs.  
**STATUS**: Not a critical finding — degrades gracefully.

---

**CERTIFICATION LINE**:  
**NOT-CERTIFIED** — the observer can disturb a host firewall by deleting a pre-existing nft table named `salient_obs`, violating the "never disturb a host firewall" claim.

**STEELMAN**:  
The table name `salient_obs` is unlikely to collide in practice, as it’s a SalienceOS-specific name. The deletion is scoped and immediately followed by recreation, minimizing impact. The design prioritizes clean per-action attribution over coexistence with other tables.

**VERDICT**:  
**SERIOUS_FLAWS** — the blast radius exceeds the stated guarantee due to unconditional table deletion. The single highest-value fix is to use a unique table name (e.g., `salient_obs_<uid>_<pid>`) or verify the table’s ownership before deletion.
