# PR#40 egress-observer certification panel: openai/gpt-5.1

_finish=stop seconds=71.0 cost=$0.0578 usage={'prompt_tokens': 50814, 'completion_tokens': 5135, 'total_tokens': 55949, 'cost': 0.0578435, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 50688, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0578435, 'upstream_inference_prompt_cost': 0.0064935, 'upstream_inference_completions_cost': 0.05135}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID: F1 / STRONG-TIER CLAIMED-BUT-UNOBSERVED CAN BE MIS-SCORED TRUE  
SEVERITY: HIGH  
LOCATION: collaborator/egressobserver.py:231-262 (`reconcile`)  
CONCRETE INPUT OR BYPASS:  
- Environment: nftables strong tier available and working, Collaborator uid = 1000.  
- Before snapshot: `before = EgressSnapshot(dests=frozenset({("203.0.113.1", 443)}), conn_count=1, tier=TIER_STRONG)`.  
- After snapshot: `after = EgressSnapshot(dests=frozenset({("203.0.113.1", 443)}), conn_count=2, tier=TIER_STRONG)` – e.g. two outgoing SYNs to the one remote IP:443 (legit scenario: HTTP client reconnects due to TLS failure or retry).  
- Claimed destinations: `claimed = [("203.0.113.1", 443), ("203.0.113.1", 443)]` – EgressRecord logically claims “two connections to 203.0.113.1:443” (for example, two GETs in a single tool action if the executor ever grows that way, or a future multi-request transport).  
WHY IT BREAKS A GUARANTEE:  
- The spec text for `claimed_unobserved` says: “strong tier only — a claimed connection the kernel never saw,” and a mismatch on that axis should produce `reconciled=False`.  
- The current implementation treats destinations as a set: `claimed_set = frozenset((ip, port)...)`. As soon as *at least one* connection to `(203.0.113.1, 443)` appears in `observed`, `claimed_unobserved` becomes empty, even if some claimed connections don’t exist in the nft-based count.  
- The only “count” signal is `conn_delta = max(0, after.conn_count - before.conn_count)`, but that is *not compared against the number of claimed endpoints*. On the strong tier, a situation with fewer observed new connections than claimed is silently treated as a clean `True` if all claimed endpoints appear in `observed`.  
- Example: claimed endpoints = 2 identical `(ip,port)`, `observed` has that pair once, `conn_delta=1`. The result: `unexpected` empty, `claimed_unobserved` empty, tier strong ⇒ `reconciled=True, note="independently observed (nft); all destinations accounted for"`. In reality, the kernel never saw one of the claimed connections.  
- That is exactly a “strong tier claimed-but-unobserved at the strong tier being mis-scored True,” which the certification text explicitly calls out as forbidden.  
WHETHER ANOTHER CHECK CATCHES IT:  
- No. There is no independent cross-check tying claimed multiplicity to kernel-observed `conn_count` or per-dest counts. Governance only passes through `obs.reconciled` and `obs.note` from `ReconcileResult`; nothing else asserts “#claimed == #observed new connections”.  
FIX:  
- Treat claims as *multisets* at the strong tier and compare against the counter:  
  - Strong-tier-only: compute `claimed_total = len(claimed)` (or better, number of claimed *connections* if the record syntax ever differentiates).  
  - Compare `conn_delta` to `claimed_total`. If `conn_delta < claimed_total`, set `reconciled=False` with a note like “record claims N connections, but only M new connections were independently observed”.  
  - If you never intend to support multiple connections per egress tool action, then narrow the contract: enforce “at most one connection per call” in egress.py and/or tools, and add an explicit guard in `reconcile` that rejects a `claimed` with length > 1 as a mismatch.  
- Additionally, you may want to preserve the connection count in `claimed_unobserved` semantics, for example by recording a scalar discrepancy “claimed_unobserved_count = claimed_total - conn_delta” when tier is strong and this difference is positive. That keeps the TRI-STATE semantics intact, but prevents a false `True` on multiplicity discrepancies.  


ID: F2 / STRONG-TIER DEGRADES TO “UNAVAILABLE” SILENTLY ON READ FAILURE  
SEVERITY: MEDIUM  
LOCATION: collaborator/egressobserver.py:269-295 (`_nft_snapshot`), 323-347 (`snapshot`), 354-381 (`observer_available`), 413-437 (`begin`), 440-455 (`end`)  
CONCRETE INPUT OR BYPASS:  
- Host: Linux, nftables installed, strong tier initially available and successfully probed (`observer_available() == TIER_STRONG`).  
- Attack: after the proof but before or during runtime, a local admin (or buggy system tool) removes or renames the `inet salient_obs` table *between* `begin()` and `end()` for an egress action, or revokes permissions such that `nft -j list set inet salient_obs dests4` returns non‑zero.  
- Behaviour:  
  - `begin()` at strong tier calls `install()` and returns `EgressSnapshot(tier=TIER_STRONG)` (empty dests).  
  - At `end()`, `_nft_snapshot()` sees a non-zero returncode from nft and returns `None`.  
  - `end()` then substitutes `after = EgressSnapshot(tier=TIER_UNAVAILABLE)` and calls `reconcile`. Because tier is now `TIER_UNAVAILABLE`, reconcile treats the whole thing as “no vantage” and sets `reconciled=None` with note “egress not independently observed on this host (no vantage)”.  
WHY IT BREAKS A GUARANTEE:  
- The text promises: “strong tier: a PASSIVE, uid-scoped nft OUTPUT hook … read‑only before/after snapshots around an egress action = that action's dest-set, RACE‑FREE … A second in-process client / wrong dest IS observed and surfaced as a discrepancy at the strong tier.”  
- In this scenario, the strong tier *was* available at probe time and at `begin()`, but for this specific action the after‑snapshot fails. The code silently downgrades this single action to `TIER_UNAVAILABLE`, losing the “strong tier” semantics without surfacing that this is a *regression* relative to the host‑property probe.  
- This is not a false True (the result is `None`), but it weakens the “if strong tier is available on this host, this action will be observed at the strong tier or clearly marked as failed observation” guarantee. It allows an attacker with just enough privilege to break the table between `begin` and `end` to cause a *per-action* loss of observation that is indistinguishable from “this machine simply has no vantage at all,” which seems stronger than what you advertise.  
WHETHER ANOTHER CHECK CATCHES IT:  
- The CI guard in `.github/workflows/ci.yml` ensures that on the runner the strong tier is *initially* available, but it does not cover mid‑run failures.  
- No runtime path asserts “we expected strong tier and we got UNAVAILABLE after begin() was already strong-tier”; governance and tools simply propagate `reconciled=None`.  
FIX:  
- Distinguish “host has no strong tier” from “strong tier was expected for this action but reading the table failed”:  
  - Carry the “expected tier” inside `before` (you already do), and in `end()` if `before.tier == TIER_STRONG` and `_nft_snapshot()` returns `None`, return a `ReconcileResult` with `tier=TIER_STRONG`, `reconciled=None`, and a note such as “strong-tier observer failed for this action (table missing or unreadable)” rather than downgrading tier to `TIER_UNAVAILABLE`.  
  - Optionally add a separate boolean field like `observer_error=True` so governance can distinguish “host has no vantage” from “observer failed mid-run”, and you can alarm differently.  
- Keep the existing “never a false True”: still use `reconciled=None` in this error case, but do not mask it as if the host were non-Linux/unsupported.  


ID: F3 / POTENTIAL OBSERVER HANG IF SUDO IS NOT INSTALLED BUT `_SUDO_BIN` PATH EXISTS  
SEVERITY: LOW  
LOCATION: collaborator/egressobserver.py:52-66 (`_resolve`), 70-76 (`_NFT`), 91-95 (`_run_nft`)  
CONCRETE INPUT OR BYPASS:  
- Host: Linux, with a non-standard filesystem layout such that `/usr/bin/sudo` exists as a regular file but is not executable or is a wrapper that prompts regardless of `-n`. For example:  
  - `/usr/bin/sudo` is present but `mode 000` due to misconfiguration.  
  - `/usr/bin/sudo` is a wrapper script that ignores `-n` and always asks for a password on stdin.  
- Behaviour:  
  - `_resolve(["/usr/bin/sudo", "/bin/sudo"], "sudo")` returns `/usr/bin/sudo` without checking for executability or behaviour.  
  - `_NFT` becomes `['/usr/bin/sudo', '-n', '/usr/sbin/nft'…]`.  
  - `_run_nft` calls `subprocess.run` with those arguments. If sudo blocks on a password prompt despite `-n` (or fails in a way that never exits promptly), `install()`, `teardown()`, `observer_available()`, and `snapshot()` can hang until `timeout` (10 seconds per call), potentially stalling an egress action for hundreds of seconds across multiple invocations.  
WHY IT BREAKS A GUARANTEE:  
- The design intent: “go through NON-INTERACTIVE sudo (`sudo -n`) so a host without passwordless sudo fails the availability probe (→ fallback) rather than blocking on a password prompt.”  
- That only holds if `_SUDO_BIN` is both executable and honours `-n`. With the current `_resolve` (which returns a path if `os.path.isfile(p)` without checking `os.access(p, X_OK)`), a misconfigured `/usr/bin/sudo` can cause `_run_nft` to block until its `timeout` on any invocation. For `observer_available` this is mostly just startup friction, but `snapshot()` and thus `begin()`/`end()` do not have external timeouts; they use the default 10s per nft invocation. For a long‑running Collaborator that executes many egress actions, this can become user‑visible latency or even look like a hang.  
- This is not a direct security boundary failure and doesn’t cause a false True, but it is a robustness problem at the “sudo -n never blocks” guarantee.  
WHETHER ANOTHER CHECK CATCHES IT:  
- No. CI only asserts that on GitHub runners `_NFT` works; it doesn’t defend against weird host sudo layouts.  
FIX:  
- Harden `_resolve` and `_NFT` to check executability and fall back cleanly:  
  - In `_resolve`, only accept candidates where `os.path.isfile(p) and os.access(p, os.X_OK)`.  
  - If no candidate is executable, rely on `shutil.which(name)`. If that also fails, return `candidates[0]` as today, but then have `install()` and `observer_available()` explicitly detect `FileNotFoundError` / `PermissionError` and mark the tier as unavailable without repeated retries.  
- Optionally shorten the `timeout` for `_run_nft` in `observer_available()` and `snapshot()` to a smaller bound (e.g. 2s) since these are “soft” observations, minimizing worst‑case hangs.  


ID: F4 / STRONG-TIER MISCOUNTS MULTIPLE CONNECTIONS TO DIFFERENT PORTS AT SAME IP  
SEVERITY: LOW  
LOCATION: collaborator/egressobserver.py:231-262 (`reconcile`)  
CONCRETE INPUT OR BYPASS:  
- Environment: nftables strong tier available.  
- Before snapshot: empty (`before.dests = ∅, conn_count=0`).  
- During action: the governed egress client creates one allowed connection to `(1.2.3.4, 443)`; a malicious second client in the same uid creates a connection to `(1.2.3.4, 8443)` *but the record claims both dests* (e.g. due to a bug or deliberate misuse in egress.py claiming a non-HTTPS port).  
- After snapshot: `after.dests = {("1.2.3.4", 443), ("1.2.3.4", 8443)}`, `conn_count=2`.  
- Claimed: `claimed = [("1.2.3.4", 443), ("1.2.3.4", 8443)]`.  
WHY IT BREAKS A GUARANTEE:  
- Transport contract in egress.py refuses non‑443 ports (“HTTPS-only (:443, non‑443 refused)”), so within *today’s* design, the egress record should never legitimately claim `("1.2.3.4", 8443)`.  
- However, the observer is deliberately decoupled: it reconciles against whatever the record claims. If egress.py were bugged or compromised to log `(ip,8443)` while actually sending some bytes there, `reconcile` would treat that second, wrong-port connection as entirely acceptable: `unexpected` is empty because both observed pairs are in `claimed_set`. Result: `reconciled=True` at strong tier and “all destinations accounted for,” even though port 8443 is out of contract.  
- This is not a bypass of the *observer* relative to its current spec (it just checks equality of (ip,port)), but it dilutes the value of the “hands didn’t lie” story: the observer cannot tell “record claims off-contract ports” from legitimate behaviour. Since the certification headline focuses on wrong dests, and “dest” is (ip,port), there is a residual here: the observer will happily mark as verified an egress action that went to the wrong *service* on the right IP, *if the record also lies about the port*.  
WHETHER ANOTHER CHECK CATCHES IT:  
- Yes, partly: the main mitigant is egress.py itself refusing non‑443. So under the un-compromised-client assumption, this is not reachable. If the client is compromised, your out‑of‑scope list (“root can evade”) covers it, but a non-root bug in egress.py could still cause this. No independent check in governance ensures that claimed ports are 443.  
FIX:  
- Small defensive hardening: in `_egress_claimed` you already fix port 443 from `resolved_ip`; that is good. In `reconcile`, for the strong tier, consider *enforcing* that claimed ports are 443 (or a whitelisted set) and treating any deviation as a discrepancy (e.g. unexpected dest) regardless of observation. That way, even a buggy record that claims `(ip,8443)` can’t silently get a “True” reconciliation – the mismatch between contract (“HTTPS-only :443”) and record claims is surfaced.  
- Alternatively, document explicitly in the module docstring that the observer assumes egress.py has already enforced `:443` and that port-level misreporting is outside this PR’s threat model. Given the current code, this is more of a design/assumption gap than an implementation bug.  


ID: F5 / POTENTIAL AUDIT NOISE ON DUAL-STACK SERVERS WITH STRONG TIER  
SEVERITY: LOW  
LOCATION: collaborator/egressobserver.py:231-262 (`reconcile`); collaborator/egress.py:154-206 (`_resolve`) and 282-362 (`fetch`)  
CONCRETE INPUT OR BYPASS:  
- Target host `example.com` is dual-stack with both A and AAAA records; DNS returns `[2001:db8::1, 93.184.216.34]`.  
- The resolver used by egress.py selects one IP (e.g. IPv4 93.184.216.34) as its pinned IP, but the strong-tier nftables hook observes *both* `tcp syn` to that host due to OS-level connect() retries or Happy Eyeballs behaviour from a future egress implementation (or the SSL stack).  
- Recorded claimed dest from `_egress_claimed` is only `(93.184.216.34, 443)`. Observed set ends up `{("93.184.216.34",443), ("2001:db8::1",443)}`.  
WHY IT BREAKS A GUARANTEE:  
- Reconcile computes `unexpected = observed - claimed_set`, so `("2001:db8::1",443)` is treated as an unexpected dest and results in `reconciled=False`, “observed [('2001:db8::1', 443)] not accounted for by the egress record”.  
- The ADR text explicitly notes that a false positive discrepancy (dual-stack/CDN multi-IP) is a *documented residual* and at most a LOW nit. This scenario is exactly that: audit noise, not a safety break. It does not violate “never returns a false True,” because we get False (over-reporting), and it does not deny anything; governance keeps status=RAN, cleared=True.  
WHETHER ANOTHER CHECK CATCHES IT:  
- Not “catch” in the sense of mitigation; this is by design. Governance’s `Decision.summary` surfaces it as a discrepancy, but doesn’t deny.  
FIX:  
- None required for safety; this is consistent with the out-of-scope statement. If you want to tame noise, you could:  
  - Treat additional connections to the *same hostname’s* other IPs as lower‑severity signals (e.g. separate `extra_ips` field) instead of making the whole reconciliation “False”. But that’s a design choice, not a requirement for correctness.  


ID: F6 / SUDO TIMEOUT COULD TRANSIENTLY MARK STRONG TIER UNAVAILABLE AND STICK IN CACHE  
SEVERITY: LOW  
LOCATION: collaborator/egressobserver.py:354-381 (`observer_available`), 78 (`_available_tier`)  
CONCRETE INPUT OR BYPASS:  
- Host: Linux, nft & sudo installed, but the very first call to `install()` or `_nft_snapshot()` fails with a transient error: e.g. `subprocess.SubprocessError` due to `nft` not in PATH for that moment, or sudo policy reloading causing a short failure.  
- Behaviour:  
  - `observer_available()` sets `_available_tier = TIER_UNAVAILABLE` at entry.  
  - `install()` returns False on any exception, so the strong-tier probe is skipped.  
  - `_proc_snapshot()` also fails (e.g. /proc not yet mounted in a container).  
  - The function returns `_available_tier` (still `"none"`) and caches it. Subsequent calls will never retry strong tier even if the underlying host condition improves (e.g. a later `apt install nftables` and sudo configuration).  
WHY IT BREAKS A GUARANTEE:  
- The workflow CI asserts strong tier is available on CI, but on deployed hosts the code treats “first probe had a transient error” as “no vantage forever.” This weakens the “observer_available is a *verified* host property” guarantee – it’s verified once, but never re‑examined.  
- It doesn’t lead to a false True, nor to blocking an otherwise-good egress, but it can silently suppress the observer for the life of the process after a transient problem.  
WHETHER ANOTHER CHECK CATCHES IT:  
- No automatic recovery exists. Tests only reset the cache via `_reset_probe_cache_for_tests`, which is private to tests.  
FIX:  
- Make the cache more conservative or self-healing:  
  - Only cache *positive* strong-tier detection; leave negative results uncached or cached for a short TTL.  
  - Alternatively, keep a monotonic state machine: allow transitions `none -> proc` or `none -> nft`, but not “once none, always none.” On a new `observer_available` call, re‑probe maybe every N seconds if the current cached tier is `none`.  
- This is primarily robustness, but directly improves the “verified host property” claim.  


---

CERTIFICATION LINE (HEADLINE CLAIMS)

1. “reconcile never returns a false True (a clean confirmation it did not earn — especially the racy /proc fallback and an empty/failed nft snapshot must be None, not True)” — **NOT-CERTIFIED**.  
   - The strong-tier `reconcile` can return `True` even when the record over-claims multiple connections to the same dest compared to the nft `conn_count` (F1), which is a claimed-but-unobserved case that is mis-scored as a clean confirmation.

2. “a second in-process client / wrong dest IS observed and surfaced as a discrepancy at the strong tier” — **CERTIFIED (within stated scope)**.  
   - For destinations differing in `(ip,port)` and observed within the nft table lifetime, the strong-tier logic reliably marks them as `unexpected` and returns `reconciled=False`; tests and the live Linux proof pin this, and the only caveats are documented residuals (same-uid co-tenant; transport-stack dual-stack quirks).

3. “the observer is evidence-only and never blocks a good egress; a discrepancy never changes RAN/cleared, and an observer failure never blocks or fails an otherwise-good egress” — **CERTIFIED**.  
   - Decision wiring treats `egress_reconciled` as orthogonal evidence: discrepancies only add audit text; RAN/cleared come solely from the egress tool result; observer errors degrade to `reconciled=None` and do not change status.

4. “the module can NEVER disturb a host firewall (only ever creates/deletes its own table, never flush ruleset, rule is policy accept / never drops)” — **CERTIFIED**.  
   - `install()` and `teardown()` only call `nft delete table inet salient_obs` and `nft -f -` with a ruleset that creates `table inet salient_obs` with `policy accept` and no `drop`/`reject`, and tests pin that no `flush ruleset` or other table names are ever emitted.

---

STEELMAN

The core observation path is carefully constructed: nft rules are scoped to a dedicated table, uid-filtered via `meta skuid`, purely observational with `policy accept`, and the tests explicitly assert that no `flush ruleset` or `drop` actions are ever emitted. The reconciliation logic is honest about fidelity tiers: the proc fallback never asserts a clean `True`, and observer failures degrade to `None` rather than fabricating verification; governance attaches this tri-state result as audit evidence without feeding it back into authorization. Within that design boundary, a second in-process client using a different destination `(ip,port)` is predictably surfaced at the strong tier, and the live Linux proof materially checks that guarantee.

---

VERDICT: **MINOR_ISSUES**

The shipped PR materially upholds its safety goals: it does not interfere with host firewalls, it never lets the racy `/proc` fallback or an unavailable vantage produce a false positive `True`, and it cleanly surfaces unexpected destinations without blocking egress. The main correctness gap is in the strong-tier multiplicity handling (F1), where claimed-but-unobserved *counts* can be mis-scored as fully reconciled; the single highest-value fix is to compare claimed connection multiplicity against the nft `conn_count` at the strong tier and treat any deficit as a discrepancy rather than a clean `True`.
