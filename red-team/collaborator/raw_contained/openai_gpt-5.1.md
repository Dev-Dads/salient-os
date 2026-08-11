# PR#39 contained certification panel: openai/gpt-5.1

_finish=stop seconds=79.4 cost=$0.1398 usage={'prompt_tokens': 61169, 'completion_tokens': 6331, 'total_tokens': 67500, 'cost': 0.13977125, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.13977125, 'upstream_inference_prompt_cost': 0.07646125, 'upstream_inference_completions_cost': 0.06331}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID 1 / TITLE: Autonomy gate is ANDed correctly; no fail-open path to uncontained auto-run  
SEVERITY: NONE (blocked attack; part of certification)  
LOCATION:  
- collaborator/governance.py: lines around 420–451, 617–641  
- collaborator/tools.py: lines around 358–420  
- collaborator/contained.py: lines around 151–192  

CONCRETE INPUT OR BYPASS:  
- Session with signed `shell.exec` only (no `shell.contained_autonomy`), `leash_overrides={"run_command": ACT_THEN_REPORT}`.  
- `govern_action(session, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))`.  

WHY IT BREAKS A GUARANTEE:  
- It does not: this is the attempted fail-open.  
- For autonomy you need all of:  
  - `code_protection_available()` True (host can verifiably contain),  
  - `enforced(session)` True (session built with `policy_caps`),  
  - `directive.grants_capability("shell.contained_autonomy")` True.  

Behavior:  
- Govern-time: if any conjunct missing, the branch:  

  ```python
  if (intent.name == "run_command" and leash == ACT_THEN_REPORT
          and not (code_protection_available()
                   and enforced(session)
                   and directive.grants_capability(SHELL_CONTAINED_AUTONOMY_CAP))):
      leash = apply_cap(leash, PROPOSE_FIRST)
  ```  

  floors `leash` to `PROPOSE_FIRST` → `govern_action` returns `HELD`.  

- Execution-time: if a caller tried to bypass and call `execute_and_verify(..., human_gated=False)` directly, the same 3-condition gate is re-asserted:  

  ```python
  if tool.name == "run_command" and not human_gated:
      if not (code_protection_available()
              and enforced(session)
              and directive.grants_capability(SHELL_CONTAINED_AUTONOMY_CAP)):
          return Decision(..., DENIED, "contained-autonomy unavailable", ...)
      require_code_protection = True
  ```  

- Inside the executor, `require_code_protection=True` forces `wrap_contained`; if containment can’t be achieved (`protected=False`), `_exec_command` returns an `Execution` with `ok=False` and `code_protected=False` – the command never runs.  

WHETHER ANOTHER CHECK CATCHES IT:  
- Yes. Two layers: govern-time floor and execute-time denial keyed on `not human_gated`. Even a direct internal call to `execute_and_verify` cannot escape both.  

FIX:  
- No fix required; this is correctly fail-closed and satisfies the “earned autonomy needs the signed cap AND verified containment” claim.  


ID 2 / TITLE: Contained path is never nested under netns; no CAP_SYS_ADMIN inheritance  
SEVERITY: NONE (blocked attack; part of certification)  
LOCATION:  
- collaborator/contained.py: module docstring, `_bwrap_argv`, `wrap_contained`  
- collaborator/netns.py: `wrap_no_network`  
- collaborator/tools.py: `_exec_command`  
- collaborator/governance.py: `execute_and_verify` run_command branch  

CONCRETE INPUT OR BYPASS:  
- Try to trigger a nested combination by:  
  - Setting `require_code_protection=True` and also relying on `wrap_no_network` to add netns on top of bwrap, or vice versa.  

Analysis of actual call graph:  
- Human / opt‑in path (`require_code_protection=False`):  

  ```python
  run_argv, isolated = wrap_no_network(argv)
  protected = False
  ```
  No bwrap; only `unshare --map-root-user` netns.  

- Autonomous path (`require_code_protection=True`):  

  ```python
  run_argv, isolated, protected = wrap_contained(argv, workspace,
                                                 unshare_net=require_isolation)
  ```
  No call to `wrap_no_network`; network isolation is provided by bwrap `--unshare-net`.  

No place calls `wrap_contained` inside `wrap_no_network` or vice versa. `netns.wrap_no_network` is only used in `_exec_command`’s else-branch when `require_code_protection` is False.  

WHETHER ANOTHER CHECK CATCHES IT:  
- Path split is structural: the functions are mutually exclusive by the `require_code_protection` flag. No hidden path uses both.  

FIX:  
- None; the split is implemented correctly and the CVE-2014-5206 / bubblewrap#287 nesting hazard is unreachable.  


ID 3 / TITLE: Belief vs behaviour on code protection is bound to executor; no “false True” code_protected  
SEVERITY: NONE (blocked attack; part of certification)  
LOCATION:  
- collaborator/tools.py: `_exec_command`, `execute_tool`  
- collaborator/governance.py: preview + `Decision.summary`  

CONCRETE INPUT OR BYPASS:  
- Try to:  
  1. Get `execution.code_protected=True` while the child actually ran uncontained.  
  2. Get a human-held run_command to be displayed or reported as “code_protected=True”.  

Behavior:  
- On the autonomous/contained path, `protected` is set from `wrap_contained`’s third return value. That is only `True` when:  
  - Platform is Linux,  
  - `_BWRAP_BIN` exists,  
  - `pairs` (code roots with witnesses) is non-empty,  
  - The guard was installed into the child command invocation.  
- After `run_supervised`, `_exec_command` downgrades `protected` to False whenever any of:  
  - `protection_unverified(returncode, stderr)` (exit 45 + sentinel),  
  - `isolation_unverified(returncode, stderr)` (exit 44 + sentinel),  
  - `setup_failed(returncode, stderr)` (non‑zero + stderr.startswith("bwrap:")).  

Thus `code_protected=True` is only returned if:  
- The container was actually used;  
- The per‑run guard did not trip;  
- bwrap reported no setup error.  

Human path:  
- Govern-time preview: `preview["code_protected"] = False` for all held run_command.  
- Decision.summary: includes the code_protected flag coming from `execution.code_protected`. For human‑approved runs, `require_code_protection=False` and `protected=False` in `_exec_command`, so summary always prints “⚠ code NOT protected (unfenced shell)”.  

WHETHER ANOTHER CHECK CATCHES IT:  
- The containment-availability probe (`containment_available`) is used only to gate autonomy; the per‑run result comes from the executor, not from that probe, closing the belief–behaviour gap. There is no path in which code_protection_available=True alone causes `code_protected=True`.  

FIX:  
- None; the binding is correctly “executor’s real result only”.  


ID 4 / TITLE: Sentinel confusion – payloads cannot forge code_protection downgrade, and cannot suppress real downgrade  
SEVERITY: LOW (belt-and-suspenders correctness)  
LOCATION:  
- collaborator/contained.py: `_CODEFENCE_UNVERIFIED_SENTINEL`, `protection_unverified`, `setup_failed`, `_guarded_script`  
- collaborator/tools.py: `_exec_command` downgrade logic  

CONCRETE INPUT OR BYPASS:  
1) Forged downgrade (payload emits exit 45 + sentinel to cause false `protected=False`):  
- Inside the contained shell, the payload could do:

  ```sh
  echo SALIENT_CODEFENCE_UNVERIFIED >&2
  exit 45
  ```

WHY IT BREAKS A GUARANTEE:  
- In this direction, it does not break safety:  
  - The result is that `_exec_command` will set `protected=False` even though the FS was read‑only. That’s a conservative misclassification (false negative), not a “false protected”.  
  - It may reduce functionality: an actually‑protected host may be treated as unprotected for that run, so autonomy decisions based on `code_protected` could be stricter than necessary.  

2) Suppressed downgrade (payload tries to hide a real unverified condition):  
- Real guard failures (append succeeds or mount not ro) are produced by the guard’s own shell logic before `exec "$@"` and cause the process to exit with code 45 and sentinel without ever running the payload. The payload cannot intervene.  

3) Stderr shaping around `setup_failed`:  
- `setup_failed` only fires when `returncode != 0 and text.startswith("bwrap:")`.  
- Those messages are produced by bwrap itself, before it `exec`s the guard script/payload. The payload cannot emit output when bwrap never reached exec.  

WHETHER ANOTHER CHECK CATCHES IT:  
- Even if a payload forged sentinel+45, the only effect is to move `protected` from True to False; the autonomy gate is upstream (govern + executor needing `require_code_protection`). There is no “upgrade” path controlled by these sentinels.  
- Real unprotected runs are caught either by the guard (never `exec "$@"`) or by bwrap returning a non-zero code with an error prefix; these are not under payload control.  

FIX:  
- Optional hardening: treat a mismatched combination (e.g., exit 45 without sentinel) as setup failure rather than “payload exit”, but that’s not required for the stated guarantees—it simply improves diagnostic clarity.  


ID 5 / TITLE: Guarded script robust against common parsing and symlink tricks; no practical “guard runs but payload runs unverified”  
SEVERITY: MEDIUM (defensive but important; verified negative)  
LOCATION: collaborator/contained.py: `_guarded_script`  

CONCRETE INPUT OR BYPASS (attacks attempted):  

1) Witness as a symlink out of root:  
- `protected_roots_with_witness()` uses each package’s `__file__`, then takes `witness.parent` as the root.  
- If the package directory on disk contains a malicious symlink, the witness is still a regular file under that root; the guard ro-binds the root directory (`--ro-bind root root`) and then tries to append to the witness. Under a genuine ro-bind, the kernel rejects writes irrespective of symlink content; under a malicious root that is not actually bound ro, the append succeeds and the guard exits 45 + sentinel.  

2) Append check vs underlying writability:  
- Code: `if ( : >> "$f" ) 2>/dev/null; then ... exit 45`.  
- If `f` is truly ro due to the bind, the open or write must fail → `if` body not taken → guard passes this step.  
- If `f` is writable via any path overlaying the root, `: >> "$f"` will succeed → guard aborts before calling `exec "$@"`.  

3) `mountinfo` parsing, multiple mounts, spoofable entries:  
- It uses:

  ```sh
  opts=$(awk -v m="$r" '$5==m {print $6}' /proc/self/mountinfo | tail -n1)
  case ",$opts," in *,ro,*) ;; *) exit 45 ;;
  ```

- The kernel controls `/proc/self/mountinfo`; the payload cannot lie here.  
- Multiple mounts: it picks the last matching line for mount-point `r`. If there are stacked mounts, the last line corresponds to the top-most visible mount—the one the process actually sees—which is correct for checking the effective ro/rw status.  

4) Roots with spaces / quotes / newlines:  
- `specs = " ".join(f'"{r.as_posix()}|{w.as_posix()}"' ...)` and then `for spec in {specs}; do ...`.  
- `as_posix()` never embeds spaces/quotes/newlines for normal package roots (these come from `Path(__file__).parent` of Python packages). In a pathological host where the package directory path includes spaces, the quoting would still be correct (`"path with space|...`); the inner parameter expansions split correctly because `spec` is assigned the entire `"root|witness"` token. There is no avenue here for injection into the guard logic from a model or payload.  

5) Guard exit propagation through `run_supervised`:  
- The guard either:  
  - fails-before-payload and exits 44 or 45, or  
  - runs `exec "$@"` on the payload.  
- `run_supervised` sees that as the child’s real `returncode`. The downgrade logic uses that `returncode` plus stderr to adjust flags. There is no layer swallowing exit 44/45.  

6) Guard runs but `exec "$@"` never reached (e.g., syntax error in inner script):  
- That would yield a non-zero rc and probably a shell error on stderr that doesn’t start with `bwrap:` and doesn’t contain the sentinel. `_exec_command` treats this as “payload failed” (`ok=False`) but does not set `code_protected=True`, because `protected` is only true if the guard did not trip and there was no setup failure; any non-zero without sentinel leaves `protected` as whatever came from wrapper, but here wrapper’s `protected=True` still correctly indicates “attempted containment”, and there was no evidence that roots were writable. The guarantee only promises “no false True”, not “errors are classified perfectly”.  

WHETHER ANOTHER CHECK CATCHES IT:  
- The live tests `ContainmentProofLinux` perform an actual write attempt into a real code root inside the sandbox and assert it fails and leaves host code untouched.  
- Combined with downgrades on exit 44/45 / `bwrap:` errors, there is no observable path where roots are writable and the run is both executed and marked `code_protected=True`.  

FIX:  
- None strictly required for the guarantee. For extra robustness, you could explicitly verify that `mountinfo` had at least one line for each root and treat “no match” as unverified, but on normal hosts that’s already the case.  


ID 6 / TITLE: Autonomous path never uses netns; human path never uses bwrap; network isolation semantics correct for raw-network opt-in  
SEVERITY: NONE (blocked attack; part of certification)  
LOCATION:  
- collaborator/tools.py: `_exec_command`  
- collaborator/govnernance.py: run_command + raw-network floor  
- tests/test_collaborator_run_command_parity.py: `IsolationEarnsAutonomyFloor`  

CONCRETE INPUT OR BYPASS:  
- Autonomous contained run without raw-network opt-in:  
  - Signed session with `shell.exec` + `shell.contained_autonomy`.  
  - No `shell.raw_network` capability.  
  - `code_protection_available()` patched True.  
  - netns_available() True.  

Result:  
- Governance: autonomy floor passes (code+cap), raw-network floor stands down (`netns_available()==True`, no need for raw-network cap).  
- Executor: `require_code_protection=True`, `require_isolation=True`.  
  - `_exec_command` uses `wrap_contained(..., unshare_net=True)` → bwrap `--unshare-net`.  
  - No call to `wrap_no_network`.  

- `d.network_isolated` becomes `True`, `d.code_protected` True as pinned by tests.  

- Autonomous contained run with signed raw_network:  
  - Same but with `shell.raw_network` in signed caps.  
  - Govern: raw-network floor stands down via signed cap; executor sets `require_isolation=False`.  
  - `_exec_command`: `wrap_contained(..., unshare_net=False)` → bwrap without `--unshare-net` (network is shared).  
  - Test `_contained` in parity suite asserts `network_isolated=False`, `code_protected=True`.  

Human-approved path:  
- `require_code_protection=False` → only `wrap_no_network`.  
- network isolation uses netns where available, with no bwrap involvement.  

WHETHER ANOTHER CHECK CATCHES IT:  
- The tests explicitly pin:  
  - that an earned-autonomy shell runs with `code_protected=True`,  
  - and that with raw-network opt‑in it has `network_isolated=False` but still `code_protected=True`.  

FIX:  
- None. This matches the design: FS containment is mandatory for autonomy; network isolation is opt-out via a signed raw-network grant and is never provided by netns on the autonomous path.  


ID 7 / TITLE: Containment availability probe cannot lie “protected=True” for an unprotected run  
SEVERITY: NONE (blocked attack; part of certification)  
LOCATION: collaborator/contained.py: `containment_available`, `_reset_probe_cache_for_tests`  

CONCRETE INPUT OR BYPASS:  
- Attempt: on a Linux host where bwrap exists but is misconfigured (e.g., kernel or bwrap patched), cause `containment_available()` to believe containment works while actual autonomous runs execute without read-only binds.  

Probe behavior:  
- It builds `run_argv` via `wrap_contained([_SH_BIN, "-c", "exit 0"], tmp, ...)`.  
- `wrap_contained` only returns `protected=True` when it will actually run via bwrap with guard.  
- Then the probe executes that command; only if `r.returncode==0 and isolated` (and hence guard was satisfied for both FS and netns) is `_available=True`.  
- If a subsequent real run misbehaves (e.g., kernel regression after boot), that’s outside the threat model; within a given boot, the probe and the later wraps share the same template.  

Belief vs behaviour:  
- Even if the probe incorrectly returns True due to a broken kernel or bwrap, actual runs still rely on their own per-run guard and downgrades. `code_protection_available()` is used to decide whether autonomy is even allowed, but `code_protected` reported per run is still executor-bound. There is no path where containment_available=True alone forces `code_protected=True`.  

WHETHER ANOTHER CHECK CATCHES IT:  
- Per-run guard + downgrades, as per ID 3 and 4.  

FIX:  
- None for the guarantees under review; this is a sound “verify, then trust cache” design.  


ID 8 / TITLE: Human path remains uncontained and never mis-labelled; no regression in wrap_no_network  
SEVERITY: NONE (blocked attack; part of certification)  
LOCATION:  
- collaborator/netns.py: unchanged `wrap_no_network`, `_UNSHARE` constants.  
- collaborator/tools.py: human path branch of `_exec_command`.  
- collaborator/governance.py: preview for run_command, HELD case; `Decision.summary`.  

CONCRETE INPUT OR BYPASS:  
- A held shell, approved by `approve`, on a Linux host with bwrap installed and `containment_available(True)` → ensure:  
  - It runs uncontained (full FS reach),  
  - `code_protected=False` in the Decision,  
  - preview shows “full filesystem … uncontained (maintenance)”.  

Behavior:  
- For human-gated runs, `execute_and_verify(..., human_gated=True)` means:  
  - The execution-time code-protection belt does not fire; `require_code_protection=False`.  
  - `_exec_command` takes the netns path:

    ```python
    run_argv, isolated = wrap_no_network(argv)
    protected = False
    ```

- Decision’s `code_protected` is set from `execution.code_protected` which is always False on this path.  
- Preview explicitly sets `code_protected=False` and includes `reach` clarifying it is deliberately uncontained, regardless of whether `code_protection_available()` is True for the host.  

Wrap_no_network:  
- No part of this diff changed `_UNSHARE` arguments or guard logic in netns.py.  
- Tests ensure its behavior is still pinned (network floor tests).  

WHETHER ANOTHER CHECK CATCHES IT:  
- Run-command parity tests assert that human-approved shells are unaffected by the autonomous belts and still run, which necessarily go through this uncontained branch.  

FIX:  
- None; this matches the owners’ explicit “human-approved stays unfenced” design constraint and is correctly communicated (no false protection claim).  


ID 9 / TITLE: Off-Linux and no-bwrap hosts never auto-run shell as “protected”  
SEVERITY: NONE (blocked attack; part of certification)  
LOCATION:  
- collaborator/contained.py: `wrap_contained`, `containment_available`  
- collaborator/codefence.py: `code_protection_available`  
- collaborator/governance.py: autonomy floor for run_command  

CONCRETE INPUT OR BYPASS:  
- Host: macOS or Windows, or Linux lacking /usr/bin/bwrap.  
- Session: even with signed `shell.contained_autonomy` capability.  
- Attempt: call run_command with `leash_overrides={"run_command": ACT_THEN_REPORT}`.  

Behavior:  
- `wrap_contained(...)` on non-Linux or missing bwrap returns `(argv, False, False)`.  
- `containment_available()` returns False; `code_protection_available()` delegates to that.  
- Autonomy floor in govern_action:  

  ```python
  if intent.name == "run_command" and leash == ACT_THEN_REPORT
          and not (code_protection_available() and enforced(session)
                   and directive.grants_capability(SHELL_CONTAINED_AUTONOMY_CAP)):
      leash = PROPOSE_FIRST
  ```

  floors to HELD.  

- Executor belt: if someone bypassed govern and invoked `execute_and_verify` with `require_code_protection=True`, `_exec_command` would immediately see `protected=False` from `wrap_contained` and refuse with an Execution whose result carries `"code protection required but unavailable on this host — not run"`.  

WHETHER ANOTHER CHECK CATCHES IT:  
- Tests `HonestFallback` in `test_collaborator_contained.py` and parity tests around `_uncontained` wrappers explicitly assert the no-op and refusal behavior.  

FIX:  
- None; this fully satisfies “off-Linux, containment_available is False and the seam withholds autonomy; never a fake fence”.  


ID 10 / TITLE: MINOR-B “approved==executed” seal interacts correctly with contained path  
SEVERITY: NONE (blocked attack; part of certification)  
LOCATION:  
- collaborator/tools.py: `Execution.code_protected` and `held_action_seal`, `freeze_args`  
- collaborator/govnernance.py: `held_action_seal` usage, preview  
- collaborator/loop.py: `approve` and re-freeze + seal checks  

CONCRETE INPUT OR BYPASS:  
- Held run_command on a host where containment is available and the session has signed contained-autonomy:  
  - Try to mutate `decision.args["command"]` between hold and approve.  
  - Ensure the contained path doesn’t bypass seal enforcement.  

Behavior:  
- For held tools (`propose_first`), `govern_action` freezes args via `freeze_args` and applies `held_action_seal` for run_command/write_file.  
- `approve` re-freezes args *again* from a copy, and re-computes the seal with `held_action_seal` for `decision.tool`. If the payload changed, HMAC compare fails, and approve returns DENIED; `decision.consumed` is left False.  
- This is independent of whether the eventual execution will be contained or not; the seal is checked before any call into `execute_and_verify`. The contained path never runs with a mutated payload.  

WHETHER ANOTHER CHECK CATCHES IT:  
- Parity tests under `MinorBApprovalSeal` and `RedTeamFixes` cover mutation and double-checking behavior, and they are upstream of any run_command execution path (contained or uncontained).  

FIX:  
- None; MINOR-B’s “approved==executed” property remains intact with the new contained executor.  


ID 11 / TITLE: View / root calculations at import are safe under unusual host layouts  
SEVERITY: LOW (defensive; mainly robustness)  
LOCATION:  
- collaborator/contained.py: `_RO_SYSTEM`, `_MASK`, `_bwrap_argv`  
- collaborator/codefence.py: `_resolved_roots`, `protected_roots_with_witness`  

CONCRETE INPUT OR BYPASS:  
- Host missing `/etc` (so `_RO_SYSTEM` does not include it) or missing `/lib64`, etc.  
- Host where `collaborator` or `salienceos` are in symlinked directories.  

Behavior:  
- `_RO_SYSTEM` includes only dirs that actually exist. On a host missing `/etc`, system view won’t include `/etc` but that does not undermine the core guarantee: code roots are always `--ro-bind`’d explicitly and verified per run via witness + mountinfo.  
- `_MASK` only masks files that exist; absent secrets staying absent is safe.  
- `protected_roots_with_witness` uses `Path(mod_file).resolve()` and requires `witness.is_file()` and `witness.parent.is_dir()`. That gives you `root` as the real directory on disk, even when the package path uses symlinks. Bwrap `--ro-bind root root` binds that resolved directory read-only; the host may have alternative writable paths to the same underlying inode, but the threat model here is “prevent shell from overwriting the code via normal paths”, not “defend against a root with arbitrary mount access”.  

Potential corner: workspace overlapping code root is already prohibited by `disjoint_from_code` in session construction; that invariant is load-bearing so bwrap’s workspace bind at `--bind ws ws` cannot inadvertently alias the code root.  

WHETHER ANOTHER CHECK CATCHES IT:  
- Live containment test uses `codefence.PROTECTED_ROOTS[0]` and attempts writes to a file inside it; the probe ensures that practical host layouts still result in an effectively ro view for code.  

FIX:  
- If you want extra hardening, you could additionally read `/proc/self/mountinfo` to ensure each `root` from `protected_roots_with_witness` actually appears as a mount-point; absent a line, treat containment unavailable. That doesn’t affect the guarantee but tightens assumptions about host layout.  


ID 12 / TITLE: Regression checks – netns behavior, human path, and off-Linux behavior unchanged  
SEVERITY: NONE (blocked attack; part of certification)  
LOCATION:  
- collaborator/netns.py (unchanged)  
- tests/test_collaborator_run_command_parity.py and test_collaborator_contained.py  

CONCRETE INPUT OR BYPASS:  
- Off-Linux run_command, on-Linux with no netns, and human-approved shells.  

Behavior:  
- Netns path: unchanged `wrap_no_network`, `netns_available`, `_UNVERIFIED_SENTINEL`, and `isolation_unverified`. Tests still assert that when isolation is unavailable and raw-network cap absent, an act_then_report shell is HELD, not auto-run.  
- Human-approved shells: tests confirm `test_human_approved_shell_unaffected_by_exec_belt` – the new executor belts (code protection and isolation) key on `not human_gated` and do not block an approved shell.  
- Off-Linux: honest fallback tests show `wrap_contained` is a no-op and `containment_available` False.  

WHETHER ANOTHER CHECK CATCHES IT:  
- Test suite directly pins these behaviors; no new paths were added that bypass them.  

FIX:  
- None needed.  


CERTIFICATION LINE  
Headline claim:  

> "No false code_protected; a human-approved run is never contained-claimed; earned autonomy needs the signed cap AND verified containment; the guard proves ro per-run; bwrap is never nested under netns; nothing regressed."  

CERTIFIED – within the stated threat model and design constraints, I could not find a concrete path that violates these guarantees, and attempted attacks on the AND gate, belief-vs-behaviour, guard correctness, sentinel confusion, path split, and regressions are all blocked by the existing code and tests.  


STEELMAN (2–3 sentences)  
The design is intentionally conservative: autonomy requires both structural FS containment *and* an operator-signed cap, with two independent floors (govern-time and execute-time) and per-run downgrades keyed to verifiable sentinels. The one-way trust from `containment_available()` to `code_protection_available()` is guarded by a separate per-run guard and downgrade logic, so misclassification by the host probe can only ever over-restrict, not create a false sense of protection. The separation between human path (uncontained) and autonomous path (bwrap-contained, never nested under netns) is cleanly implemented and well-pinned in tests, which is exactly what you want for a high-stakes seam like this.  


VERDICT  
SOUND – the implementation realizes the “protection earns autonomy” design without observable holes in the claimed guarantees; the highest-value improvement now would be modest hardening of guard diagnostics (e.g., treating “no mountinfo match for root” as an explicit unverified state), which would increase robustness but not change the fundamental safety properties.
