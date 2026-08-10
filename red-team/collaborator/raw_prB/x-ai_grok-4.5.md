# PR B run_command parity + MINOR-B panel: x-ai/grok-4.5

_finish=stop seconds=202.5 cost=$0.1825 usage={'prompt_tokens': 59676, 'completion_tokens': 10566, 'total_tokens': 70242, 'cost': 0.1825304, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1825304, 'upstream_inference_prompt_cost': 0.1191344, 'upstream_inference_completions_cost': 0.063396}, 'completion_tokens_details': {'reasoning_tokens': 8706, 'image_tokens': 0, 'audio_tokens': 0}}_

# PR B Adversarial Review + Certification

## Findings

### F1 / Tool-rebind + seal-strip skips MINOR-B gate / LOW
- **LOCATION:** `collaborator/loop.py` (approve seal predicate: `decision.tool in SEALED_TOOLS or (decision.seal and not egress)`)
- **CONCRETE INPUT:**
  ```python
  held = govern_action(s, ToolIntent("write_file", {"path": "note.txt", "content": "x"}, "structured"))
  # human sees write_file hold
  held.tool = "read_file"
  held.seal = ""          # strip
  held.args = {"path": "secret.txt"}
  approve(s, held)        # seal branch skipped → read_file runs if fs.read granted
  ```
- **WHY:** The leftover-seal rebind case is caught; clearing `seal` *and* moving `tool` off `SEALED_TOOLS` skips the fail-closed path that still applies when `tool` remains `run_command`/`write_file`.
- **OTHER LAYER:** `reauthorized_or_denied` only checks the *new* tool’s capability/path; no seal/preview/tool-identity re-bind. Artifact/exit verifiers do not apply to the swapped tool in a way that restores “approved tool == executed tool.”
- **FIX:** Treat govern-minted holds as seal-mandatory via an immutable flag set at hold (e.g. `decision.binds_args = True` / record `held_tool`), or deny approve when `origin_subject` is set and `(tool in SEALED_TOOLS) != bool(valid seal)` / require `decision.tool == preview["tool"]` before execute.  
- **CLASS:** Belt-and-suspenders nit under stated ADR 0002 scope (full `Decision` field rewrite ≈ in-process re-signer; bare hash seal is not a keyed approval MAC). Not a by-reference TOCTOU break. Do not block C2 on this alone.

### F2 / `str(x or "")` falsy collapse on write path/content / LOW
- **LOCATION:** `collaborator/tools.py` `freeze_args` / `held_action_seal` / `_exec_write` (same `or ""` pattern)
- **CONCRETE INPUT:** `content=0` or `content=False` → frozen/sealed/written as `""`, not `"0"`/`"False"`.
- **WHY:** Does **not** break approved==executed (all three layers agree). Surprising coercion only.
- **OTHER LAYER:** N/A (consistent).
- **FIX:** Use `"" if v is None else str(v)` (and distinguish missing vs empty if ever needed).
- **CLASS:** Stated non-goal / quality nit, not a guarantee break.

### F3 / B1 condition can raise out of `govern_action` / LOW
- **LOCATION:** `collaborator/governance.py` isolation floor (`enforced(session) and directive.grants_capability(...)`)
- **CONCRETE INPUT:** Hostile/broken `directive.grants_capability` raising when `leash==ACT_THEN_REPORT`, `netns_available()==False`.
- **WHY:** Breaks “never raises”; does **not** fail open to auto-run (exception aborts before `execute_and_verify`).
- **OTHER LAYER:** Execution belt still denies autonomous unprotected/unisolated shells if anything reached it.
- **FIX:** Wrap floor predicate in try/except → treat as “not granted” / force `apply_cap(..., PROPOSE_FIRST)`.
- **CLASS:** Fail-closed nit on the never-raise promise.

---

### Non-findings (attacks blocked — certification value)

| Probe | Result |
|--------|--------|
| Unsigned/legacy `session.capabilities` includes `shell.raw_network` | Floor stays up: requires `enforced(session) and directive.grants_capability` (`governance.py` B1 block). Pinned by `test_UNSIGNED_raw_network_optin_does_NOT_stand_the_floor_down`. |
| Model forges `shell.raw_network` | No mint key; capability gate is signed-grant path under enforcement. Default-deny. |
| `netns_available()` false True | Probe checks **inode ≠ parent**, not bare `unshare` rc; cache is host probe. Per-run guard + `require_isolation` bind to **actual** `wrap_no_network` isolation (`tools.py` `_exec_command`). |
| Floor ordering gap (code then B1, both `leash==ATR`) | Monotonic `apply_cap`; either floor only tightens. Invalid leash → `PROPOSE_FIRST` / allowlist dispatch fail-closed. No weaker-than-HELD gap when a floor should hold. |
| Auto path bypasses govern floors | `execute_and_verify` re-asserts code deny + `require_isolation` on `not human_gated` (not leash string). |
| By-ref list mutation / hostile `__str__` / shallow `dict` share | `freeze_args` → `tuple(str once)`; approve re-freezes; seal over frozen snapshot. Tests pin. |
| Args key reassignment without seal update | `hmac.compare_digest(held_action_seal(...), decision.seal)` DENY; `consumed` stays False. |
| Missing / non-str / non-ASCII / surrogate seal | Fail closed before `compare_digest`; no raise. |
| Str vs list command collision | Type tags `S`/`L`/`N`; length-prefixed fields. |
| Path/content boundary shift | 8-byte BE length prefixes + `W` tag; tool name first field. |
| Leftover seal + tool rebind to non-sealed | Second disjunct forces recompute `""` ≠ leftover seal. |
| Seal-less `run_command`/`write_file` hold | DENY fail closed; retryable. |
| Unmutated approve / proposals | Pinned green; no regression in tests. |
| `raw_network` preview | `True` iff `not netns_available()`; independent of opt-in; honest raw reach. |

Bare-hash seal **recompute** after args mutation is **out of scope** (ADR 0002 single-trust-domain / non-keyed digest) — same as emission seal.

---

## Certification

**C1 (B1): CERTIFIED** — With `code_protection_available()==True`, an `act_then_report` `run_command` without verified netns and without a **signed** `shell.raw_network` grant is floored to `propose_first` at govern and again refused at execution via `require_isolation`; unsigned/legacy caps cannot stand the floor down; floors compose monotonically with no fail-open ordering gap.

**C2 (MINOR-B): CERTIFIED** — Hold-time `freeze_args` + seal, approve-time re-freeze + mandatory seal verify for `SEALED_TOOLS`, injective length-prefixed/type-tagged framing, fail-closed missing seal, non-consumption on deny, and no regression on clean approvals match the stated by-reference / partial-mutation threat model (not a keyed anti-rebind MAC against full `Decision` forgery).

---

## STEELMAN

B1 is correctly pre-wired as an orthogonal, monotonic network axis: signed default-deny opt-in, verified netns probe, honest `raw_network` preview, and an execution-time belt that keys off `human_gated` and real isolation — not govern-time belief. MINOR-B makes approve() self-contained (re-freeze + re-seal + tool identity in the digest + single `SEALED_TOOLS` source), which closes the panel’s approved≠executed hold-path gap the same way `emission_seal` does for Tier 2, with structural immutability rather than digest-only hope over a shared list.

## VERDICT

**SOUND** — No guarantee-breaking path found against C1/C2 under the stated threat model and non-goals; residual items are LOW nits.

**Highest-value fix:** Bind approve to hold-time tool identity (deny `decision.tool != preview["tool"]`, or a non-optional `binds_args`/`held_tool` set at mint) so seal-strip + rebind cannot skip the sealed-tool fail-closed path even as DiD inside ADR 0002.
