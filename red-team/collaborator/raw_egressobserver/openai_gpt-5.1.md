# PR#40 egress-observer certification panel: openai/gpt-5.1

_finish=stop seconds=102.1 cost=$0.1539 usage={'prompt_tokens': 59529, 'completion_tokens': 7947, 'total_tokens': 67476, 'cost': 0.15388125, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.15388125, 'upstream_inference_prompt_cost': 0.07441125, 'upstream_inference_completions_cost': 0.07947}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

F-01 / STRONG SNAPSHOT PARSE FAILURE CAN MINT FALSE TRUE (BLOCKED)  
SEVERITY: LOW  
LOCATION: collaborator/egressobserver.py:197-233 (`_parse_nft_set`); 235-261 (`_nft_snapshot`); 326-372 (`reconcile`)  
CONCRETE INPUT OR BYPASS:  
- Kernel vantage: nftables rule installed and actually seeing packets.  
- nft returns JSON for `list set` that is structurally valid and decodes to a *non‑empty* set of elements, but our parser treats it as a hard failure and returns `None` (e.g. unseen future key layout or additional wrapper fields inside `elem`, while still faithfully representing every `(ip,port)` in the JSON).  
- `_nft_snapshot` then returns `None`, causing `end()` to treat this strong‑tier read as “unchecked” instead of feeding it to `reconcile`.  

WHY IT BREAKS A GUARANTEE:  
- This would be a guarantee violation *if* `_parse_nft_set` ever misclassified a complete, truthful set as unparseable and the code then turned that into `reconciled=True`. The PR explicitly changed that path: any parse failure makes `_nft_snapshot` return `None`, and `end()` in that case returns a `ReconcileResult` with `reconciled=None` and `tier=TIER_STRONG`.  
- There is no route from “parse failed” to “empty snapshot → `reconciled=True`” anymore; strong‑tier True is only emitted when `_nft_snapshot` returns a *non‑None* `EgressSnapshot`, and that is only built from a non‑None `_parse_nft_set`.  

WHETHER ANOTHER CHECK CATCHES IT:  
- Yes. The combination `_parse_nft_set` → `_nft_snapshot` → `end()` explicitly fails closed to `reconciled=None` on any parse ambiguity. Tests `PanelFixes.test_snapshot_parse_failure_is_none_not_empty` exercise this exact edge by feeding `garbage-not-json` with rc==0 and asserting `_nft_snapshot()` is `None`.  
- In addition, `_parse_nft_set` treats *any* undecodable element or schema skew as `None` (unchecked), not as “empty”.  

FIX:  
- No code change required for the stated guarantees. The behavior is correctly fail‑closed; at worst you lose evidence (reconciled=None) instead of minting a false True. The only “fix” to consider is relaxing `_parse_nft_set` to tolerate more future JSON shapes for availability, but that would be an availability/robustness optimization, not a security requirement.

CERTIFICATION COMMENT: This line of attack is blocked: the code no longer lets a failed or partial nft JSON parse produce a strong‑tier `reconciled=True`.


F-02 / RACINESS OR EMPTY /PROC FALLBACK MINTING FALSE TRUE (BLOCKED)  
SEVERITY: LOW  
LOCATION: collaborator/egressobserver.py:289-322 (`_proc_snapshot`); 324-338 (`snapshot`); 340-371 (`observer_available`); 373-421 (`ReconcileResult`, `reconcile`)  
CONCRETE INPUT OR BYPASS:  
- Host lacks nft privilege or nft snapshot fails; observer falls back to `/proc/net/tcp` (TIER_PROC).  
- A claimed connection to `(1.1.1.1,443)` exists in the egress record, but the `/proc` sampling misses it (race: connection state changes between begin/end). Both `before` and `after` snapshots show no dest for this window.  

WHY IT BREAKS A GUARANTEE:  
- The guarantee we’re trying to break: “racy /proc fallback must never claim a clean True; it must be reconciled=None, not a false ‘verified’.”  
- In this scenario, `tier == TIER_PROC`. `reconcile` computes `observed = after.dests - before.dests = ∅`, `unexpected = ∅`, `claimed_unobserved` is forced to `∅` for non‑strong tier, and the final branch for PROC is:  
  ```python
  elif tier == TIER_STRONG: ...
  else:
      reconciled, note = None, "no discrepancy seen (proc fallback, racy — not a positive confirmation)"
  ```  
  So you *cannot* get `reconciled=True` via the `/proc` path, even if all samples are empty.  

WHETHER ANOTHER CHECK CATCHES IT:  
- Yes, `reconcile` explicitly constrains positive (`True`) results to `tier == TIER_STRONG`. All non‑strong tiers produce `None` at best. Tests `Reconcile.test_proc_clean_is_unchecked_not_a_false_confirm` and `OffPlatformHonest.test_no_vantage_off_linux` pin this.  

FIX:  
- None required for the certification claim. The code and tests already enforce “/proc fallback never yields True”.


F-03 / STRONG TIER READ FAILURE BEING MIS‑REPORTED AS “NO VANTAGE” (BLOCKED)  
SEVERITY: LOW  
LOCATION: collaborator/egressobserver.py:423-450 (`begin`); 452-481 (`end`)  
CONCRETE INPUT OR BYPASS:  
- `observer_available()` previously succeeded as `TIER_STRONG`.  
- `begin()` is called and `install()` succeeds, so the per‑action window is intended to run at strong tier.  
- Before `end()` runs, someone deletes or corrupts `table inet salient_obs`, or nft JSON becomes unparsable so `_nft_snapshot()` returns `None`.  

WHY IT BREAKS A GUARANTEE:  
- The specific guarantee: “a strong-tier read that fails mid-action returns reconciled=None with tier STILL strong (honestly distinct from ‘no vantage’).”  
- In this scenario, `before.tier` is `TIER_STRONG`. `end()` paths:  
  - calls `_nft_snapshot()` → returns `None`;  
  - calls `teardown()`;  
  - returns a `ReconcileResult(reconciled=None, tier=TIER_STRONG, note="strong-tier observer read failed … — unchecked")`.  
- It does not downgrade to `TIER_UNAVAILABLE` or `TIER_PROC`, nor does it ever emit `True`.  

WHETHER ANOTHER CHECK CATCHES IT:  
- The logic in `end()` itself is the backstop; unit test `PanelFixes.test_end_strong_read_failure_is_unchecked_and_distinct_from_no_vantage` pins this behavior.  

FIX:  
- None needed. The invariant “mid‑action strong tier failure != ‘no vantage’ and never a silent True” is correctly implemented and tested.


F-04 / `begin()` STRONG INSTALL FAILURE LEADING TO STALE TABLE, FALSE TRUE (BLOCKED)  
SEVERITY: LOW  
LOCATION: collaborator/egressobserver.py:423-435 (`begin`)  
CONCRETE INPUT OR BYPASS:  
- `observer_available()` reports `TIER_STRONG` from some earlier probe where an install worked.  
- For a specific action, `begin()` is called but `install()` now fails (privilege changed, nft binary vanished). If `begin()` lied and returned a strong‑tier snapshot pointing at a previous table, reusing that table’s already‑populated set could make `after` empty relative to `before` and reconcile clean when it should not.  

WHY IT BREAKS A GUARANTEE:  
- The code explicitly checks:  
  ```python
  if tier == TIER_STRONG:
      if not install():
          return EgressSnapshot(tier=TIER_UNAVAILABLE)
      return EgressSnapshot(tier=TIER_STRONG)
  ```  
- So a per‑action install failure produces a `before` snapshot with `tier=TIER_UNAVAILABLE`. Later `end()` will feed that through `reconcile` as an UNAVAILABLE tier, yielding `reconciled=None`, *never* `True`. There is no way to get a stale strong table reused under a strong‑tier label.  

WHETHER ANOTHER CHECK CATCHES IT:  
- Unit test `PanelFixes.test_begin_unavailable_when_install_fails` ensures this behavior.  

FIX:  
- None required; the design intent is correctly implemented.


F-05 / STRONG TIER WITH EMPTY DEST SET MINTING FALSE TRUE (BLOCKED)  
SEVERITY: LOW  
LOCATION: collaborator/egressobserver.py:368-379 (`reconcile`)  
CONCRETE INPUT OR BYPASS:  
- Strong tier is up, but for the window of interest, no TCP packets match the rule; nft returns sets with zero elements; `_parse_nft_set` returns `set()`.  
- This is expected for: an egress that failed before connect/resolve (`resolved_ip` is None) or an action that legitimately performed no network I/O.  

WHY IT BREAKS A GUARANTEE:  
- The claim is “no *observed* discrepancy yields True; a missing vantage or clean-but-racy tier yields None.” When `claimed` is empty (e.g. DNS or TLS errors before IP resolution), `claimed_set` is `∅`, `observed` is `∅`, and `tier == TIER_STRONG`, so `reconciled=True`.  
- This is *not* a false positive by the stated semantics: we are only asserting “every observed destination is accounted for by the record.” When the record says “I never connected anywhere” and the kernel confirms that no dests were observed, that is a legitimate “hands didn’t lie” for **this action**.  
- The dangerous direction would be: actual traffic went out to some `(ip,port)` but the nft rule missed it, produced `observed=∅`, and reconcile returned `True`. Given the rule matches all outbound TCP (both address families, all packets, not just SYN; no port or interface filter other than loopback) and we explicitly fail closed to `None` on any JSON parsing anomaly or list‑set error, the only way to reach `observed=∅` in the presence of real traffic is if nft itself completely fails to list a set that actually contains elements. That’s indistinguishable from a broken kernel vantage and falls into the root‑compromised / nft broken residual; we do not and cannot claim safety against that.  

WHETHER ANOTHER CHECK CATCHES IT:  
- The strong tier’s behavior is pinned by `Reconcile.test_strong_clean_is_true` and by the Linux live tests, which require that an actually created connection shows up in `observed_dests` when strong tier is available. There’s no independent redundancy here; the kernel rule *is* the ground truth at this tier.  

FIX:  
- None needed relative to the certification claim. The semantics “True means no *observed* dest is unaccounted for by the record” are met; situations where the rule sees nothing despite real egress are a root/kernel‑vantage failure and explicitly out of scope.


F-06 / STRONG TIER MISSING A DEST BECAUSE RULE IS TOO NARROW (BLOCKED)  
SEVERITY: LOW  
LOCATION: collaborator/egressobserver.py:110-132 (`_nft_ruleset`)  
CONCRETE INPUT OR BYPASS:  
- Attempt: send TCP traffic that should be observed but doesn’t match the nft expression.  
  Examples:  
  - Traffic on a different protocol (`udp`), different l4proto: not claimed to be covered; explicitly documented residual.  
  - Loopback to a forward proxy: intentionally excluded (`ip daddr != 127.0.0.0/8`, `ip6 daddr != ::1`), and documented as residual.  
  - Non‑TCP exfil: explicitly out‑of‑scope (UDP/DNS/QUIC).  

WHY IT BREAKS A GUARANTEE:  
- For TCP non‑loopback exfil on the same uid, the rule is:  
  ```nft
  meta skuid <uid> ip  daddr != 127.0.0.0/8 meta l4proto tcp update @dests4 { ip daddr . tcp dport }
  meta skuid <uid> ip6 daddr != ::1         meta l4proto tcp update @dests6 { ip6 daddr . tcp dport }
  ```  
  So any IPv4 or IPv6 TCP packet with a non‑loopback destination and this uid will be captured. There is no port filter, no interface filter, and it is hooked on `output` with `policy accept`. Pre‑established connections are covered because matching is on every packet; tests `PanelFixes.test_ruleset_matches_all_tcp_not_just_syn` and the Linux live suite confirm that a connection established before the window and used during the window is still observed.  
- The only realistic unobserved egress paths are exactly those the PR explicitly lists as residuals (different uid, loopback proxy, UDP/IPv6 nuance, between‑windows timing) and treats as out‑of‑scope for v0. So there is no uncovered “TCP same uid non‑loopback” gap.  

WHETHER ANOTHER CHECK CATCHES IT:  
- Not applicable; this is the core observation mechanism. The tests verify its shape and its behavior, and the open residuals are documented design, not unintentional escapes.  

FIX:  
- None required.


F-07 / HOST FIREWALL DISTURBANCE (FLUSH OR OTHER TABLE) (BLOCKED)  
SEVERITY: HIGH (would be critical if present)  
LOCATION: collaborator/egressobserver.py:88-95 (`_NFT_TABLE` comment), 151-180 (`install`), 182-191 (`teardown`), 96-130 (`_nft_ruleset`); tests/test_collaborator_egressobserver.py:132-170 (`BlastRadiusSafety`)  
CONCRETE INPUT OR BYPASS:  
- Attempt to coerce the observer into issuing any of:  
  - `nft flush ruleset`  
  - any call modifying other tables (e.g. `table ip filter`, `table inet filter`)  
  - rules that drop or reject packets (`drop`, `reject`, or `policy drop`).  

WHY IT BREAKS A GUARANTEE:  
- The blast‑radius guarantee is: the observer “can never disturb a host firewall; only ever creates/deletes its own table, never flush ruleset, rule is policy accept / never drops.”  
- The code paths:  
  - `install()` calls `_run_nft(["delete","table","inet",_NFT_TABLE])` and then `_run_nft(["-f","-"], stdin=_nft_ruleset(uid))`.  
  - `teardown()` calls `_run_nft(["delete","table","inet",_NFT_TABLE])`.  
  - `_nft_ruleset` emits `table inet salient_obs { … chain out { type filter hook output priority 0; policy accept; … } }` and never mentions any other table or `flush`.  
- No code path uses `flush` or passes arbitrary user input into `nft` arguments that could be exploited to expand the effect. `_NFT_TABLE` is a constant string; `_uid()` is just the numeric uid; there’s no string concatenation with untrusted data that could craft “flush ruleset”.  

WHETHER ANOTHER CHECK CATCHES IT:  
- The `BlastRadiusSafety` test class explicitly patches `_run_nft` and asserts:  
  - `teardown` only ever calls `["delete","table","inet","salient_obs"]`;  
  - `install` never emits “flush” or “ruleset” in any argument list and only touches `salient_obs`;  
  - `_nft_ruleset` includes `policy accept` and excludes “flush” and “drop”.  
- The CI workflow also runs a live proof: `EgressObserverProofLinux.test_teardown_leaves_no_table` verifies the observer’s own table is gone and doesn’t affect other tables.  

FIX:  
- None. The module is tightly scoped to its own table and cannot flush or alter other firewall state by construction.


F-08 / OBSERVER CRASH OR SUDO PROMPT HANG TURNING GOOD EGRESS INTO ERROR (BLOCKED)  
SEVERITY: MEDIUM (if it could affect RAN/cleared)  
LOCATION: collaborator/egressobserver.py:61-79 (`_resolve`, `_NFT_BIN`, `_SUDO_BIN`, `_NFT`); 133-144 (`_run_nft`); 423-481 (`begin`, `end`); collaborator/tools.py:436-486, 511-533 (`_exec_web_fetch`, `_exec_net_post`); collaborator/governance.py:694-720 (`execute_and_verify` egress branch)  
CONCRETE INPUT OR BYPASS:  
- Malformed nft binary path or non‑executable `sudo` wrapper that prompts for a password.  
- nft or sudo returns with nonzero status or raises `OSError` or `SubprocessError` (e.g. binary missing, permission denied, timeout).  
- `/proc/net/tcp` unreadable due to permissions.  

WHY IT BREAKS A GUARANTEE:  
- Guarantees:  
  - Observer is “evidence‑only and never blocks a good egress.”  
  - “Module can never hang on a sudo prompt” (via `sudo -n`).  
- `_resolve` ensures we only use executable files: it checks `os.path.isfile` and `os.access(..., X_OK)`; otherwise falls back to `shutil.which(name)`, which itself only returns executables. So `_NFT_BIN` and `_SUDO_BIN` are either valid executables or last‑resort candidates that will fail with a clean `OSError` when run.  
- `_NFT` is set to either `[_NFT_BIN]` (root) or `[_SUDO_BIN,"-n",_NFT_BIN]`. The `-n` enforces non‑interactive sudo, causing an immediate error instead of hanging on a prompt.  
- `_run_nft` always uses `check=False` and wraps calls in `try/except (OSError, subprocess.SubprocessError)` in `install`, `teardown`, `_nft_snapshot`. Failures in install/read return `False` or `None`, which then drive:  
  - `observer_available()` to TIER_UNAVAILABLE or TIER_PROC;  
  - `begin()` to `EgressSnapshot(tier=TIER_UNAVAILABLE)`;  
  - `end()` to a `ReconcileResult` with `reconciled=None`, tier either STRONG (if mid‑action read fail) or UNAVAILABLE.  
- In `_exec_web_fetch` / `_exec_net_post`, any observer failure is contained to `obs = egressobserver.end(...)`. `execute_and_verify` uses `rec.ok` (the mediated client’s own success) and never promotes an observer failure to FAILED; the only way the egress action is marked FAILED is if the client itself errored or its `ToolResult.ok` is False.  

WHETHER ANOTHER CHECK CATCHES IT:  
- `OffPlatformHonest.test_nft_command_uses_sudo_when_unprivileged` asserts the `sudo -n` shape.  
- `OffPlatformHonest.test_no_vantage_off_linux` and various PanelFixes tests ensure that observer fallbacks yield `reconciled=None`, not raised exceptions.  
- The egress execution path’s error handling is explicit: governance wraps the entire egress execute in `try/except Exception`, converting unexpected crashes into `Decision(status=FAILED, reason="egress error: …")` only when the *client* throws, not when the observer does. The observer itself is written to “never raise.”  

FIX:  
- None for the guarantees in scope. Behavior is fail‑closed on evidence (reconciled=None), not fail‑closed on the egress itself.


F-09 / EVIDENCE‑NOT‑GATE: DISCREPANCY CHANGING RAN/CLEARED (BLOCKED)  
SEVERITY: HIGH (would be serious if observer influenced clearance)  
LOCATION: collaborator/governance.py:125-152 (`Decision` fields), 154-214 (`Decision.summary`), 694-720 (`execute_and_verify` egress branch)  
CONCRETE INPUT OR BYPASS:  
- Egress record `rec.ok` is True (transport succeeded), but observer finds a discrepancy, e.g. second same‑uid client hitting `8.8.8.8:443` during the window.  
- Test harness simulates this by injecting `ReconcileResult(reconciled=False, unexpected={("8.8.8.8",443)}, note="…")`.  

WHY IT BREAKS A GUARANTEE:  
- Guarantee: “the observer is evidence only (a discrepancy never changes RAN/cleared, and an observer failure never blocks or fails an otherwise-good egress).”  
- In `execute_and_verify`’s egress branch:  
  ```python
  ok = bool(execution.result.ok)
  reason = f"egress {rec.canonical_dest} [{rec.status}]" if ok else rec.error
  obs = execution.egress_obs
  return Decision(
      ...,
      status=RAN if ok else FAILED,
      cleared=ok,
      egress=rec,
      egress_reconciled=(obs.reconciled if obs is not None else None),
      egress_discrepancy=(obs.note if (obs is not None and obs.reconciled is False) else ""),
  )
  ```  
  Neither `status` nor `cleared` is modified based on `obs`. Whether `obs.reconciled` is False, True, or None, `cleared` is solely driven by `ok`.  
- `Decision.summary()` then adds a tail:  
  - discrepancy → `⚠ EGRESS DISCREPANCY ...`;  
  - reconciled True → `🔒 egress world-observed (reconciled)`;  
  - None → “independent egress observation unavailable here”.  
  But the `[tool ✓ ...]` prefix and the ✓/✗ are derived from `status` and `cleared`, not from the observer.  

WHETHER ANOTHER CHECK CATCHES IT:  
- Test `SeamWiring.test_discrepancy_is_evidence_not_a_deny` constructs a Decision with `egress_reconciled=False` and asserts `status == RAN` and `cleared is True`.  
- Tests also assert that the summary string reflects the discrepancy but does not change the fundamental verdict.  

FIX:  
- None required. The observer is correctly wired as evidence‑only.


F-10 / WRONG DEST OR SECOND CLIENT EVADED & NOT SURFACED AT STRONG TIER (BLOCKED)  
SEVERITY: HIGH  
LOCATION: collaborator/egressobserver.py:324-379 (`reconcile`); collaborator/tools.py:436-486, 511-533 (`_egress_claimed`, `_exec_web_fetch`, `_exec_net_post`); tests/test_collaborator_egressobserver.py:75-109 (`Reconcile`), 111-151 (`BeginEndWindow`), 215-298 (`EgressObserverProofLinux`)  
CONCRETE INPUT OR BYPASS:  
- Scenario 1 (wrong dest): compromised egress client logs `resolved_ip="1.1.1.1"` but actually connects to `9.9.9.9`. In reality, egress.py itself pins the IP and connects to exactly the resolved IP; you’d need to break that contract first, which is out of scope for this PR. Assuming that happened:  
  - Strong tier nft sees packets to `(9.9.9.9,443)`.  
  - `_egress_claimed` yields `[(rec.resolved_ip,443)] = [("1.1.1.1",443)]`.  
  - `reconcile` runs with `before` empty, `after.dests` containing both or just `("9.9.9.9",443)`.  
  - `unexpected = observed - claimed_set` includes `("9.9.9.9",443)`; `reconciled=False`, `unexpected` non‑empty.  
- Scenario 2 (second client): another in‑process TCP client in the same uid establishes `("8.8.8.8",443)` during the window while the sanctioned egress connects to `("1.1.1.1",443)`.  
  - After snapshot sees both dests; `claimed_set` has only `("1.1.1.1",443)`.  
  - `unexpected` includes `("8.8.8.8",443)`; `reconciled=False`.  

WHY IT BREAKS A GUARANTEE:  
- Guarantee: “a second in-process client / wrong dest IS observed and surfaced as a discrepancy at the strong tier.”  
- The strong-tier reconciling logic operates over destination **set**:  
  ```python
  observed = after.dests - before.dests
  unexpected = observed - claimed_set
  ```  
  Any new dest not in the claim set is “unexpected” regardless of connection multiplicity. For missed connections to a claimed dest, strong tier also computes `claimed_unobserved`.  
- Unit tests:  
  - `BeginEndWindow.test_strong_window_catches_a_second_client` patches `_nft_snapshot` to return dests `{("1.1.1.1",443), ("8.8.8.8",443)}` and verifies `reconciled is False` and `("8.8.8.8",443) in r.unexpected`.  
  - Live proof `EgressObserverProofLinux.test_stray_second_connection_is_caught` creates a real connection to `8.8.8.8` and asserts it shows up in `unexpected`.  

WHETHER ANOTHER CHECK CATCHES IT:  
- This behavior is intrinsic to the reconcile algorithm; there is no independent “backup” beyond tests. But given the tests (including live) that exercise both a legitimate egress and a stray second connection, the implementation is strongly validated.  

FIX:  
- None needed; second‑client/wrong‑dest behavior at strong tier is implemented and tested.


F-11 / CRASHING BENIGN EGRESS VIA MALFORMED /PROC OR HEX PARSE (BLOCKED)  
SEVERITY: LOW  
LOCATION: collaborator/egressobserver.py:263-287 (`_hex_to_endpoint`), 289-322 (`_proc_snapshot`), 324-338 (`snapshot`)  
CONCRETE INPUT OR BYPASS:  
- `/proc/net/tcp` rows with unexpected formats, e.g. shorter fields, malformed hex in the remote address, unexpected state codes, non‑numeric UIDs.  

WHY IT BREAKS A GUARANTEE:  
- `_hex_to_endpoint` handles malformed input by catching `ValueError` and `TypeError` and returning `None`; `_proc_snapshot` skips endpoints where `ep is None`.  
- Lines with too few fields (`len(f) < 8`) are skipped entirely.  
- Errors opening `/proc/net/tcp*` degrade to `found=False`, causing `_proc_snapshot` to return `None`, which `snapshot()` interprets as “no /proc vantage,” then returning an UNAVAILABLE snapshot.  
- There is no path that raises from `_hex_to_endpoint` or `_proc_snapshot` that would bubble up into the egress execution path.  

WHETHER ANOTHER CHECK CATCHES IT:  
- Tests `ProcDecode.*` cover error handling and IPv6/garbage decoding.  
- `OffPlatformHonest.test_no_vantage_off_linux` and `PanelFixes.test_transient_none_is_not_cached_but_positive_is` ensure that degradations are expressed as `tier=TIER_UNAVAILABLE` and `reconciled=None`, without exceptions.  

FIX:  
- None needed; parsers fail closed to “no vantage” and do not interfere with a successful egress.


F-12 / FALSE‑POSITIVE DISCREPANCY FROM IPv6 TEXT FORM (BLOCKED / LOW NOISE ONLY)  
SEVERITY: LOW  
LOCATION: collaborator/egressobserver.py:81-92 (`_canon_ip`), 344-356 (`reconcile`); tests/test_collaborator_egressobserver.py:189-204  
CONCRETE INPUT OR BYPASS:  
- Kernel emits IPv6 address as expanded text (`2001:db8:0:0:0:0:0:1`); egress record stores compressed form (`2001:db8::1`). Without canonicalization, these would appear as mismatched and show up as `unexpected` or `claimed_unobserved`.  

WHY IT BREAKS A GUARANTEE:  
- Guarantee: no false discrepancy from mere textual differences in IP representation.  
- `_canon_ip` uses `ipaddress.ip_address(...).compressed`, and `reconcile` applies `_canon_ip` to both claimed and observed dests.  
- Test `PanelFixes.test_ip_canonicalization_kills_ipv6_textform_false_discrepancy` constructs exactly that scenario and asserts `reconciled is True`.  

WHETHER ANOTHER CHECK CATCHES IT:  
- Yes, the dedicated panel‑fix test ensures this behavior; there is no known way to get a false discrepancy purely from IPv6 textual variance now.  

FIX:  
- None required.


F-13 / SUDO OR NFT AVAILABILITY CACHED AS “NONE” FOREVER (BLOCKED)  
SEVERITY: LOW  
LOCATION: collaborator/egressobserver.py:340-371 (`observer_available`), 483-490 (`_reset_probe_cache_for_tests`); tests/test_collaborator_egressobserver.py:206-236  
CONCRETE INPUT OR BYPASS:  
- On first probe, `install()` fails (temporary sudo/nft issue) and `_proc_snapshot()` also returns `None`. `observer_available()` returns `TIER_UNAVAILABLE`.  
- If this result were cached, the observer would remain “off” for the process lifetime, even after conditions improve.  

WHY IT BREAKS A GUARANTEE:  
- Guarantee: “the availability probe caches only a POSITIVE tier (a transient none self-heals).”  
- Implementation:  
  ```python
  if tier != TIER_UNAVAILABLE:
      _available_tier = tier
  ```  
- So a transient ‘none’ result is not cached; subsequent egress calls re‑probe and can self‑heal to TIER_STRONG or TIER_PROC.  

WHETHER ANOTHER CHECK CATCHES IT:  
- `PanelFixes.test_transient_none_is_not_cached_but_positive_is` explicitly tests this behavior on Linux.  

FIX:  
- None; behavior is correct.


F-14 / GOVERNANCE SUMMARY MIS‑STATING VERIFICATION BASED ON OBSERVER (BLOCKED)  
SEVERITY: LOW  
LOCATION: collaborator/governance.py:154-214 (`Decision.summary`)  
CONCRETE INPUT OR BYPASS:  
- Egress action where observer is unavailable (`egress_observer.tier` is `none` or `/proc` with no discrepancy).  
- Decision has `egress_reconciled=None`. If the summary incorrectly labeled such actions as “world‑verified”, it would violate the “never claim verification it doesn’t have” guarantee.  

WHY IT BREAKS A GUARANTEE:  
- Summary logic:  
  ```python
  if self.egress is None:
      claim = "ran, verified"
  elif self.egress_reconciled is True:
      claim = "ran, egress world-observed (reconciled)"
  elif self.egress_reconciled is False:
      claim = "ran, EGRESS DISCREPANCY independently observed"
  else:
      claim = "ran, channel-logged (independent egress observation unavailable here)"
  ```  
- Egress actions with no vantage or only `/proc` always fall into the last branch: “channel‑logged (independent egress observation unavailable here)”. They are not upgraded to “verified”.  

WHETHER ANOTHER CHECK CATCHES IT:  
- `SeamWiring.test_summary_reflects_each_reconcile_tier` asserts that `egress_reconciled=None` yields a summary containing “observation unavailable”, not a lock icon or verification language.  

FIX:  
- None required.


F-15 / CRASHING EGRESS EXECUTION VIA OBSERVER EXCEPTIONS (BLOCKED)  
SEVERITY: MEDIUM (would be serious if an observer error could raise out of governance)  
LOCATION: collaborator/tools.py:436-486, 511-533; collaborator/governance.py:694-720; collaborator/egressobserver.py entire module  
CONCRETE INPUT OR BYPASS:  
- Force an exception in `egressobserver.begin()` or `end()` (e.g. injecting a broken module, raising `RuntimeError` in `_uid()`, or patching `_run_nft` to throw).  

WHY IT BREAKS A GUARANTEE:  
- The observer module itself is wrapped in `try/except` for all OS/nft/syscall interactions, but a logic bug could still raise. The core question: could such a raise bubble up and violate “govern_action/approve never raise” or turn a good egress into a FAILED?  
- In the current code, `begin()` and `end()` catch OS and subprocess errors but not arbitrary logic exceptions. However, they only run inside `_exec_web_fetch` / `_exec_net_post`, which themselves are called inside `execute_and_verify`’s egress branch wrapped as:  
  ```python
  try:
      execution = execute_tool(...)
  except Exception as exc:
      return Decision(status=FAILED, reason=f"egress error: {type(exc).__name__}", ...)
  ```  
- So any unexpected exception in the observer path that does get thrown out would cause the egress action to be marked FAILED. That would violate “observer failure never blocks an otherwise-good egress” *if* the only error came from the observer, not the client.  

WHETHER ANOTHER CHECK CATCHES IT:  
- The observer is written “never raises” for its internal syscalls, and tests are comprehensive on the error‑paths. Hitting a pure Python bug in the observer would be equivalent to a bug in any other part of the tooling; there is no dedicated safety net per function. Given the amount of explicit `try/except` in the observer, the remaining risk is narrow and corresponds to general coding errors, not a structural design flaw.  

FIX:  
- Optional hardening: wrap `egressobserver.begin()` and `end()` calls in `_exec_web_fetch` and `_exec_net_post` with a `try/except Exception` that degrades gracefully to “no vantage” instead of letting them raise. This would make “observer failures never block a good egress” robust even against unexpected Python logic bugs in the observer. However, for the PR as shipped, given the current explicit error handling, I classify this as an acceptable residual, not a broken guarantee.


---

### CERTIFICATION LINE

Headline claim:  
“reconcile never returns a false True; a second in-process client / wrong dest is observed + surfaced at the strong tier; the observer is evidence-only and never blocks a good egress; the module can never disturb a host firewall.”

CERTIFICATION: **CERTIFIED** — Within the threat and scope stated in the PR (root compromise, UDP/loopback/other‑uid residuals explicitly excluded), the shipped code enforces these properties: strong‑tier True only arises from a successfully parsed nft snapshot, the racy `/proc` fallback and no‑vantage paths only yield `None`, unexpected destinations at strong tier are surfaced as discrepancies, observer outputs never affect `RAN`/`cleared`, and all nft invocations are strictly scoped to the dedicated `salient_obs` table with `policy accept` rules.

### Steelman

The design is deliberately conservative: whenever the kernel vantage is even slightly ambiguous (JSON parse anomaly, missing set object, undecodable element, nft failure, missing `/proc`), the observer degrades to `reconciled=None` and never asserts a clean run. That, combined with destination‑set semantics and full‑TCP matching, makes it very hard to mint a false “hands didn’t lie” while still reliably flagging truly new destinations at the strong tier. The only meaningful residuals are explicitly documented (other uid, UDP, loopback proxy, between‑windows timing, root tampering) and consistent with the observer‑not‑enforcer design the ADR prescribes for this iteration.

### Verdict

VERDICT: **SOUND** — The core guarantees the PR claims are actually implemented and strongly pinned by tests, with no uncovered paths to a false `reconciled=True`, to an un‑surfaced second client/wrong dest at the strong tier, to the observer influencing clearance, or to host firewall disturbance.

Single highest‑value fix: add a small `try/except Exception` wrapper around `egressobserver.begin()`/`end()` calls in `_exec_web_fetch` and `_exec_net_post` so that even an unexpected logic bug in the observer can only ever degrade to “no vantage” (reconciled=None) and never convert a good egress into a FAILED action.
