# PR B run_command parity + MINOR-B panel: openai/gpt-5.1

_finish=stop seconds=60.4 cost=$0.1249 usage={'prompt_tokens': 54940, 'completion_tokens': 5621, 'total_tokens': 60561, 'cost': 0.124885, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.124885, 'upstream_inference_prompt_cost': 0.068675, 'upstream_inference_completions_cost': 0.05621}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID: B1-1 / TITLE: No auto-run gap for unsandboxed run_command / SEVERITY: – (NON-FIND)  
LOCATION: collaborator/governance.py:396–448, 576–607; collaborator/tools.py:280–316, 394–404; collaborator/policycaps.py  
CONCRETE INPUT OR BYPASS:  
- Host config: `code_protection_available() == True`, `netns_available() == False`, session without any signed caps (no `policy_caps` / `caps_key`) or with unsigned legacy `session.capabilities` including `"shell.raw_network"`.  
- ToolIntent: `ToolIntent("run_command", {"command": ["curl", "https://example.com"]}, source="structured")`  
- Leash path 1: session default: `run_command.default_leash == PROPOSE_FIRST` → `govern_action` holds (no auto-run).  
- Leash path 2: host override: `leash_overrides={"run_command": ACT_THEN_REPORT}` and/or host `leash` kwarg `ACT_THEN_REPORT`.  

WHY IT BREAKS A GUARANTEE:  
It does not. Under these inputs we get:  
- Capability gate: requires `"shell.exec"`; satisfied by unsigned capabilities or signed caps.  
- Leash resolution: `_resolve_leash` + `apply_cap` + proposer floor produce `leash == ACT_THEN_REPORT`.  
- Code floor (F-6): skipped because `code_protection_available() == True`.  
- B1 floor (network): `intent.name == "run_command"`, `leash == ACT_THEN_REPORT`, `not netns_available()` is True, and `(enforced(session) and directive.grants_capability(SHELL_RAW_NETWORK_CAP))` is False because `enforced(session)` is False for an unsigned session. So `leash` is tightened via `apply_cap(leash, PROPOSE_FIRST)` to `PROPOSE_FIRST`.  
- Allowlist dispatch: `leash != ACT_THEN_REPORT` → we take the HELD branch, freeze args, mint seal, and return `Decision.status == HELD, leash == PROPOSE_FIRST`. No autonomous execution occurs.  

Even if an attacker:  
- Adds `"shell.raw_network"` into legacy `session.capabilities` (unsigned), or  
- Mutates `session.leash_overrides` at runtime to `ACT_THEN_REPORT`, or  
- Spoofs `intent.source` (non-`"proposed"`),  

B1 is guarded by `enforced(session)` and by the directive’s `grants_capability(SHELL_RAW_NETWORK_CAP)`, both derived solely from the signed PolicyCaps; neither `session.capabilities` nor any model-controlled field can forge this. Tests `IsolationEarnsAutonomyFloor.test_UNSIGNED_raw_network_optin_does_NOT_stand_the_floor_down` explicitly pin this.  

The execution belt in `execute_and_verify` adds an additional protection: for `tool.name == "run_command"` and `human_gated == False`, if `code_protection_available()` is False, the action is denied; if code protection is True but `require_isolation` is True and `_exec_command` reports `isolated == False`, the executor refuses to run. When `code_protection_available()` is True and `netns_available()` is False, the only way `require_isolation` is False is via the same signed-capability check used in B1, so there is no divergent path to an autonomous unsandboxed shell.  

WHETHER ANOTHER LAYER CATCHES IT:  
Yes—several independent layers reinforce B1:  

- Capability gate: `directive.grants_capability("shell.exec")` prevents unauthorized use of run_command.  
- PolicyCaps enforcement: `enforced(session)` is sticky and `_valid_grant` requires a valid signature bound to `workspace_subject`, so forged or stripped caps fail closed.  
- B1 floor itself: prevents autonomous `run_command` off-Linux or when netns is unverifiable unless the signed `shell.raw_network` capability is present.  
- Execution belt: re-applies the code and network floors at execution time for any autonomous call (regardless of leash string) via `human_gated == False` and `require_isolation`.  
- `wrap_no_network` + `netns_available`: enforce network isolation when available and honestly flag or refuse when not.  

FIX:  
No fix required. The network isolation floor is default-deny, tied only to signed grants, and re-enforced at execution; I do not see a path where an unsigned or model-constructed `shell.raw_network` or a mis-ordered floor yields an autonomous, raw-network `run_command`. This is a NON-FIND that strengthens C1.

---

ID: B1-2 / TITLE: No fail-open via leash ordering or invalid/unknown leash / SEVERITY: – (NON-FIND)  
LOCATION: collaborator/governance.py:396–448, 425–448; collaborator/policycaps.py:160–214  
CONCRETE INPUT OR BYPASS:  
- Attempt to exploit ordering: call `govern_action` with `leash="garbage"` (keyword arg) or set `session.leash_overrides["run_command"] = "garbage"`, hoping that B1 looks only for `leash == ACT_THEN_REPORT` and that some composition of floors leaves `leash` in an unsafe intermediate state.  

WHY IT BREAKS A GUARANTEE:  
It doesn’t. The pipeline is:  

1. `_resolve_leash` maps invalid overrides to `PROPOSE_FIRST` up front.  
2. `apply_cap` treats any unknown leash or cap as `NOTIFY_ONLY`, never returning the unknown string.  
3. Proposer floor, code floor, and B1 are keyed on `leash == ACT_THEN_REPORT`. Given the above normalization, a nonstandard/attacker-chosen string never survives to this check.  
4. Final allowlist dispatch only runs tools when `leash == ACT_THEN_REPORT`; everything else becomes HELD or NOTIFIED.  

The two floors (code and network) both `apply_cap(leash, PROPOSE_FIRST)` when they fire; they never widen the leash, and they are independent: reordering them does not create an auto-run state that would otherwise be held. Unit tests also exercise the proposer floor while patching both `code_protection_available` and `netns_available` up to ensure their isolation.  

WHETHER ANOTHER LAYER CATCHES IT:  
Yes, multiple: `apply_cap`’s fail-closed rank mapping, the allowlist dispatch in `govern_action`, and the execution belt (which keys on `human_gated`, not the leash string).  

FIX:  
No fix required. There is no observed ordering or invalid-leash gap that produces an unintended autonomous run. NON-FIND.

---

ID: B1-3 / TITLE: Raw-network preview honesty / SEVERITY: – (NON-FIND)  
LOCATION: collaborator/governance.py:448–475; collaborator/netns.py:83–115  
CONCRETE INPUT OR BYPASS:  
- Off-Linux host: `sys.platform != "linux"` → `netns_available() == False`.  
- ToolIntent: `ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured")` → default leash `PROPOSE_FIRST` → HELD.  

WHY IT BREAKS A GUARANTEE:  
It does not. For HELD run_command actions, preview construction uses the frozen args and sets:  

```python
if intent.name == "run_command":
    preview["code_protected"] = code_protection_available()
    ...
    if not netns_available():
        preview["raw_network"] = True
```

`netns_available()` is a verified probe that returns False on non-Linux and on Linux when userns/netns are unavailable or cannot be verified, and is cached only after that verified check. So on any host where B1 would withhold autonomous network access, the preview truthfully says `raw_network: True` (i.e., the reach is raw). If the operator explicitly opts in via the signed `shell.raw_network` capability, the preview remains honest about reach: it still reflects that isolation is not in effect; the opt-in only affects autonomy, not the preview.  

Tests `RawNetworkPreview.test_held_preview_shows_raw_network_off_linux` and `..._omits_raw_network_when_isolation_available` pin this. There is no way to coerce `netns_available()` to return True when isolation is actually not in place without breaking its inode-based verification, which would require a compromised runtime, beyond this code’s threat model.  

WHETHER ANOTHER LAYER CATCHES IT:  
Not applicable; this is an audit/UX guarantee, not an enforcement boundary. The isolation belt at execution and `wrap_no_network` ensure behavior matches the flag.  

FIX:  
None needed. The preview is honest by construction. NON-FIND.

---

ID: MINOR-B-1 / TITLE: Seal framing injectivity and mismatch handling / SEVERITY: – (NON-FIND)  
LOCATION: collaborator/tools.py:166–216 (`held_action_seal`, `freeze_args`); collaborator/loop.py:119–219 (`approve`)  
CONCRETE INPUT OR BYPASS:  
- Try to create two different executions with the same seal by:  
  - Using string vs list commands: `"echo hi"` vs `["echo", "hi"]`.  
  - Using `None`, dict, or list-valued `command`, `path`, or `content`.  
  - Exploiting Unicode/surrogates and type ambiguities.  

WHY IT BREAKS A GUARANTEE:  
It doesn’t. The seal encoding is:  

- Tool name is the first field and included in the digest, so a `Decision.tool` rebind between sealed tools will change the seal.  
- For `run_command`:
  - List/tuple commands: type tag `b"L"` + per-element `str(c).encode("utf-8", "surrogatepass")`.  
  - String command: type tag `b"S"` + encoded bytes.  
  - Any other type: type tag `b"N"`.  
- For `write_file`: type tag `b"W"`, then path and content strings coerced exactly as `_exec_write` will.  
- Each part is length-prefixed with 8-byte big-endian lengths before feeding into sha256, preventing boundary shift collisions (e.g., `part1 + "\x00" + part2`).  

`freeze_args` ensures the sealed values and executed values coincide:  
- `command` lists/tuples are converted into a tuple of `str(c)` once; any hostile `__str__` is evaluated and frozen at hold/approve.  
- `path` and `content` are always coerced to `str(x or "")`, matching `_exec_write`.  
- `body` (for egress) is frozen to bytes, but MINOR-B’s seal only covers run_command/write_file; emissions have their own seal.  

The approval path:  
- Re-freezes `args` (`freeze_args(dict(decision.args))`) so even decisions not minted via `govern_action` become safe.  
- For egress tools, verifies `emission_seal` as before.  
- For sealed tools, or any non-egress decision that carries a seal, it requires the seal to be a non-empty ASCII `str` and compares via `hmac.compare_digest(held_action_seal(decision.tool, args), decision.seal)`.  
- Any mismatch yields `DENIED` and leaves `decision.consumed == False`, so the same action can be retried after restoring the original payload.  

Tests cover: distinct seals for argv/content changes, string vs list command, seal emptiness for non-sealed tools, mutation denial, non-ASCII seals, wildcard `__eq__` objects, and tool rebinds. I see no mutation that changes what will execute yet leaves the args seal unchanged.  

WHETHER ANOTHER LAYER CATCHES IT:  
For run_command/write_file, the verifier is not sufficient to detect substitution, because it re-derives its expectations from the same mutated args; the seal is the primary approved==executed binding. For emissions, there is no external verifier, so their seal is primary as well. The freeze step gives structural TOCTOU resistance even if the seal mechanism itself failed.  

FIX:  
No fix required. The framing is injective, robust to Unicode oddities, and approval enforces matching. NON-FIND supporting C2.

---

ID: MINOR-B-2 / TITLE: Shared-command-list TOCTOU / shallow snapshot gap / SEVERITY: – (NON-FIND)  
LOCATION: collaborator/loop.py:119–152; collaborator/governance.py:425–475; collaborator/tools.py:201–214  
CONCRETE INPUT OR BYPASS:  
- Attempt to exploit a shared list command (by-reference) mutated between:  
  - origination seal check and execution, or  
  - approve’s seal check and execution.  
For example, in tests this is simulated by monkey-patching `names_code_root` to mutate the `command` list in-place during approval.  

WHY IT BREAKS A GUARANTEE:  
It doesn’t, after the changes in this PR. The code makes approved==executed structural, not just a property of digests:  

- At hold time (`govern_action` HELD branch), it calls `freeze_args(intent.args)`, which converts any `command` list/tuple to a tuple of strings. The held `Decision.args["command"]` is immutable; in-place mutation fails.  
- At approve time, it does `freeze_args(dict(decision.args))`, so even if `decision.args` was modified by a caller or never frozen, the local `args` passed into all subsequent checks and into `execute_and_verify` is immutable and canonical.  
- The seal recomputation uses `held_action_seal(tool.name, args)` — the same frozen values as will be run — and mismatch denies.  

Tests:  
- `test_held_command_is_frozen_immutable` asserts the tuple and that element assignment raises.  
- `test_in_window_list_mutation_cannot_change_execution` and `test_hostile_str_is_evaluated_once_at_freeze` exercise in-window mutation via hooks; they observe that the execution output remains the original approved command.  
- External panel regression tests show the re-freezing behavior protects even non-seam-minted decisions.  

Thus there is no remaining shallow-copy TOCTOU path where `Decision.args` mutation can change what runs while bypassing either the freeze or the seal check.  

WHETHER ANOTHER LAYER CATCHES IT:  
Even if the seal logic were bypassed, the freeze layer ensures structural immutability of the consequential parameters. Conversely, if freeze were omitted, the seal would still catch whole-key reassignment of `command`, `path`, or `content`.  

FIX:  
No fix needed; this was the gap the PR explicitly closes, and tests verify it. NON-FIND.

---

ID: MINOR-B-3 / TITLE: Seal-less or malformed seal decisions cannot execute / SEVERITY: – (NON-FIND)  
LOCATION: collaborator/loop.py:189–219  
CONCRETE INPUT OR BYPASS:  
- Construct a `Decision` for `run_command` or `write_file` that:  
  - is HELD,  
  - has no seal (`decision.seal == ""` or `None`), or  
  - carries a non-ASCII string or an object with hostile `__eq__`,  

and then call `approve(session, decision)`.  

WHY IT BREAKS A GUARANTEE:  
It doesn’t; the behavior is correct and fails closed as promised by C2:  

- For egress tools, the earlier emission-seal branch enforces presence and equality of `egress.emission_seal`; missing or mismatched seals deny.  
- For run_command/write_file and any non-egress decision with a leftover seal, the MINOR-B block enforces:  
  - Seal must be an instance of `str`, non-empty, and `.isascii()`; otherwise, it returns `DENIED` with a fail-closed reason and does not consume the decision. This stops wildcard `__eq__` and non-ASCII / lone-surrogate seals without raising.  
  - `hmac.compare_digest` is used to compare the recomputed seal with the held one; any mismatch denies, again without consumption.  

Tests `test_missing_seal_fails_closed`, `test_wildcard_eq_seal_cannot_spoof_a_match`, `test_non_ascii_seal_denies_and_does_not_raise`, and `test_surrogate_seal_denies_and_does_not_raise` explicitly demonstrate that:  
- Approve does not raise,  
- Status is `DENIED`,  
- `decision.consumed` stays False (so a corrected decision remains retryable).  

There is no path by which a held run_command/write_file can be executed via `approve` without carrying a valid, matching seal.  

WHETHER ANOTHER LAYER CATCHES IT:  
No. This is the first and only line ensuring a seal-less or malformed sealed decision cannot run. If this were faulty, artifact verification and exit code checks would not detect substitution.  

FIX:  
None required; this matches the “fail closed, retryable” contract. NON-FIND.

---

ID: MINOR-B-4 / TITLE: Snapshot vs stored args consistency / unmutated approvals / SEVERITY: – (NON-FIND)  
LOCATION: collaborator/governance.py:448–475; collaborator/loop.py:119–219; collaborator/tools.py:280–316, 358–381  
CONCRETE INPUT OR BYPASS:  
- Normal flows: create held `run_command` and `write_file` via `govern_action`, never mutate `Decision.args` or `Decision.seal`, and call `approve`.  
- Also, create non-seam decisions (e.g., by modifying `held.args` mid-stream to simulate a pre-PR behavior) and test that approvals still run when payload and seal are consistent.  

WHY IT BREAKS A GUARANTEE:  
It doesn’t; there is no regression to legitimate approvals. The chain is:  

- At hold: `govern_action` computes `args = freeze_args(intent.args)`, uses those `args` to build the seal, stores `args` into the Decision, and uses them in the preview.  
- At approve: it builds `args = freeze_args(dict(decision.args))`, rechecks the egress or held_action_seal, and passes this exact `args` into `execute_and_verify`.  
- `execute_tool` and `_exec_command/_exec_write` coerce arguments in the same way `freeze_args` and `held_action_seal` do.  

Tests `test_unmutated_run_command_approves_and_runs` and `test_unmutated_write_file_approves_and_runs` demonstrate that unchanged approvals succeed and that write_file writes exactly what was approved. More subtle cases (list content, str subclasses with drifting `__str__`) are covered by `test_writefile_list_content_is_frozen_to_str` and `test_str_subclass_content_is_frozen_to_plain_str`, showing that freezing does not alter behavior from the executors’ perspective: the on-disk content matches the frozen, sealed value.  

WHETHER ANOTHER LAYER CATCHES IT:  
Yes — for write_file, the artifact verifier compares the actual file hash with the expected hash derived from the (frozen) `content`. But since freeze and seal now align with the executor’s coercions, there’s no discrepancy to catch; verification is consistent.  

FIX:  
None needed. Legitimate, unmutated approvals and proposals behave as expected. NON-FIND.

---

ID: MINOR-B-5 / TITLE: No seal/args skew via tool rebind / SEVERITY: – (NON-FIND)  
LOCATION: collaborator/loop.py:189–219; collaborator/tools.py:166–214  
CONCRETE INPUT OR BYPASS:  
- Take a held `write_file` decision, then mutate:  
  - `decision.tool = "read_file"` (downgrade) or another non-sealed tool name, leaving `decision.seal` and `decision.args` intact;  
  - Or rebind between two sealed tools (e.g., `run_command` ↔ `write_file`) with identical argument shapes.  

Then call `approve`.  

WHY IT BREAKS A GUARANTEE:  
It doesn’t. The code explicitly addresses this rebind scenario:  

- The MINOR-B block triggers if `decision.tool in SEALED_TOOLS` **or** `(decision.seal and not getattr(tool, "egress", False))`. So even if you rebind `tool` to a non-sealed tool, the presence of a leftover seal forces a check.  
- `held_action_seal` includes `tool_name` as its first field in the digest, so re-sealing with the new `decision.tool` name always produces a different seal than the stale one.  
- Because the recomputed seal will never match the old one in a rebind scenario, `approve` returns `DENIED`, preserving `decision.consumed == False`.  

Tests `test_tool_rebind_downgrade_is_denied` and `test_seal_binds_tool_identity` exercise both cases. There is no path where a seal for one tool can be replayed as a valid seal for another tool.  

WHETHER ANOTHER LAYER CATCHES IT:  
For write_file→read_file rebinds, even if the seal logic were absent, `read_file` is read-only so it cannot introduce additional mutation. However, the current logic prevents even that downgrade replay. For run_command rebinds, the seal layer is primary.  

FIX:  
No fix required. Tool rebind replay is blocked as intended. NON-FIND.

---

ID: EXEC-1 / TITLE: Autonomous execution belt for run_command / SEVERITY: – (NON-FIND, but important for C1)  
LOCATION: collaborator/governance.py:576–607; collaborator/tools.py:294–316  
CONCRETE INPUT OR BYPASS:  
- Attempt to call `execute_and_verify` directly with an autonomous run:  
  - `tool = get_tool("run_command")`  
  - `execute_and_verify(session, tool, directive, action_id, args, leash=ACT_THEN_REPORT, human_gated=False)`  
on a host with `code_protection_available() == False` or `netns_available() == False` and no signed opt-in.  

WHY IT BREAKS A GUARANTEE:  
It doesn’t; this belt defends the guarantee. For autonomous run_command calls (`not human_gated`):  

- If `code_protection_available()` is False, it immediately returns a DENIED Decision: autonomous shell withheld because code protection is unavailable. This mirrors the code floor independently of the govern-time result.  
- If code protection is True, it computes `require_isolation = not (enforced(session) and directive.grants_capability(SHELL_RAW_NETWORK_CAP))`. That flag is fed into `_exec_command`.  
- In `_exec_command`, if `require_isolation` is True and `wrap_no_network` could not give a verified-isolated argv (`isolated == False`), it returns an Execution with `ok=False`, an explanatory error, and `network_isolated=False`. `execute_and_verify` turns that into a FAILED Decision with a reason like “network isolation required but unavailable on this host — not run”.  

Unit tests `test_exec_code_floor_belt_denies_autonomous_unprotected_shell`, `test_exec_network_floor_refuses_autonomous_unisolated_shell`, and `test_exec_belt_not_keyed_on_leash_string` cover:  

- Code-protection-based deny when protection is off.  
- Network-isolation-based refusal when isolation is unavailable and no raw opt-in.  
- The belt depending on `human_gated`, not the leash string, so a caller can’t bypass with `leash=PROPOSE_FIRST` but `human_gated=False`.  

WHETHER ANOTHER LAYER CATCHES IT:  
Yes — this is a second line of defense on top of B1 in `govern_action`. It guards against internal callers and future refactors that might bypass the govern path.  

FIX:  
None; this belt is implemented correctly and solidifies C1.

---

### CERTIFICATION LINES

C1 (B1 — isolation-earns-autonomy floor):

CERTIFIED — Given `code_protection_available() == True`, I see no path by which an `act_then_report` `run_command` on a host with no verified netns and no signed `shell.raw_network` capability can auto-run; all such calls are held or refused, and capability/ordering/preview semantics are default-deny and internally consistent.

C2 (MINOR-B — approved==executed seal for held run_command/write_file):

CERTIFIED — For held `run_command`/`write_file` actions, any post-hold argument mutation or seal tampering causes approval to deny without consumption, unsealed decisions fail closed, the seal framing is injective and aligned with the executors’ coercions, and I see no regression in legitimate (unmutated) approvals or proposals.

---

### STEELMAN

The design for B1 and MINOR-B is conservative and layered: enforcement depends only on signed PolicyCaps and runtime-verified isolation, with run_command autonomy double-gated in both govern and execute paths, and with honest telemetry about code/network protection surfaced to the human. The seal extension learns the right lessons from the earlier emission work: it freezes consequential arguments, uses an injective, type-aware framing, ties the seal to tool identity, and verifies it in `approve()` itself so that approved==executed is a self-contained boundary rather than an emergent property. The pinned tests are unusually thorough, exercising both the happy paths and realistic adversarial cases (mutating shared lists, drifting `__str__`, malformed seals, direct execution calls), which materially increases my confidence that both the code and the tests are targeting the right failure modes.

---

### VERDICT

VERDICT: SOUND — I did not find any guarantee-breaking bugs in the B1 isolation floor or the MINOR-B seal; the highest-value future hardening would be to maintain this rigor as new tools or capabilities are added, ensuring they are either structurally fenced or brought under the same seal/floor patterns from day one.
