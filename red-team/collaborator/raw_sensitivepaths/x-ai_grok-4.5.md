# PR#38 sensitivepaths certification panel: x-ai/grok-4.5

_finish=stop seconds=142.3 cost=$0.1359 usage={'prompt_tokens': 46036, 'completion_tokens': 7337, 'total_tokens': 53373, 'cost': 0.1358764, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.1358764, 'upstream_inference_prompt_cost': 0.0918544, 'upstream_inference_completions_cost': 0.044022}, 'completion_tokens_details': {'reasoning_tokens': 5952, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

### F1 / `names_sensitive_path` propagates hostile `__str__` / LOW
- **Location:** `collaborator/sensitivepaths.py:68–89` (call sites `governance.py` ~sensitive-path deny & preview; `loop.py` approve re-deny)
- **Concrete input:**  
  `command = Broken()` with `def __str__(self): raise ValueError("boom")`, or  
  `command = ["cat", Broken()]`, via in-process `ToolIntent("run_command", {"command": ...}, "proposed")`.
- **Why it matters:** `str(c)` / `str(command or "")` are uncaught. `govern_action` / `approve` promise never to raise; an exception here aborts the deny/HELD path instead of failing closed to `""` (no match). For `approve`, `freeze_args` already throws first on the same shape (pre-existing).
- **Another check:** Model/JSON paths cannot supply throwing `__str__` (parser/propose only yield `str` / list / dict / scalars). Same pattern as certified `names_code_root`. Not reachable as a proposer bypass.
- **Fix:** Wrap the body in `try/except Exception: return ""` (fail closed = no match), matching the “never raise” contract.

### F2 / Approve sensitive-path re-deny is not load-bearing / LOW (belt-and-suspenders)
- **Location:** `collaborator/loop.py` (PR 1a block before MINOR-B seal)
- **Concrete input:** Held collaborator `run_command` with `args["command"]` reassigned post-hold to `["cat", "/home/op/.ssh/id_rsa"]`.
- **Why it does *not* break a guarantee:** `args = freeze_args(dict(decision.args))` is the same snapshot passed to re-deny, seal, and `execute_and_verify`. MINOR-B `held_action_seal` / `hmac.compare_digest` independently DENYs any post-hold command mutation. Re-deny only specializes the reason + mirrors code-root; author already labels DiD-over-DiD.
- **Another check:** Yes — MINOR-B seal.
- **Fix:** None required; keep for reason symmetry.

---

## Non-findings (blocked attacks / certification evidence)

| Probe | Result |
|--------|--------|
| Proposer emit `source != "proposed"` | **Blocked.** `propose.py` `_candidate_from_response` hardcodes `ToolIntent(..., source="proposed")`. |
| Parser forge `source=="proposed"` or dodge deny | **Blocked.** `toolcall.py` only stamps `structured` \| `content_block` \| `content_json`; never reads `source` from model JSON. |
| Proposer secret-touch → autonomous RAN | **Blocked.** Hard-DENY before leash dispatch; also proposer ATR floor + `code_protection_available()==False` autonomy floor. |
| Proposer secret-touch → one-click HELD | **Blocked.** DENY (not HELD); `propose()` drops non-`(HELD, NOTIFIED)`. |
| User/host/turn (`source!="proposed"`) secret command wrongly DENY | **Blocked.** Deny gated on `source=="proposed"` only; approve re-deny on `origin=="collaborator"` only. Pinned by `test_user_directed_secret_command_not_denied_and_runs`. |
| Placement regress (code+secret, net_post, floors, write_file ATR) | **Blocked.** After code-root deny (code reason first); `intent.name=="run_command"` only; before emission/autonomy floors; no interaction with write_file ATR. |
| `secret_touch` / preview flips RAN↔DENIED or tags non-`run_command` | **Blocked.** Set only on `verify_mode=="exit"` returns; never consulted for status; preview is display-only. |
| Control path with recognizer as **sole** FS boundary | **None.** Porousness is documented non-goal; autonomy withheld independently; approved shell intentionally unfenced. Evasion via `$HOME`/glob/`tar`/etc. = **out of scope**, not a finding. |
| `.env` / `.npmrc` miss | **By design** (precision); pinned landmine tests. |

`intent.source=="proposed"` is seam-authored and model-unforgeable. It is **not** the full complement of “any model-authored command” (turn-channel is deliberately HELD+⚠, not hard-denied). That matches the owned scope in the deny comment and the certification headline (“proposer-originated”).

Hostile non-`__str__` types (`None`, `int`, `dict`, nested lists) fail closed to `""` inside `names_sensitive_path`.

---

## Certification line

**CERTIFIED** — A proposer-originated `run_command` that literally names a sensitive marker cannot autonomously run or reach an approvable HELD state (hard-DENY at `govern_action` + drop in `propose`); user/host/turn-directed secret-touching commands stay hold-then-run; `secret_touch` is audit-only; no ordering regression found. (Caveat: in-process throwing `__str__` can still raise — F1 — not a model-reachable bypass.)

---

## Steelman

The change correctly copies the Harm A pattern: seam-only `source=="proposed"`, deny before floors, preview ⚠ + execution tag with no control authority, approve re-assert as reason-level DiD behind an already-load-bearing args seal, and an explicit non-boundary recognizer so maintenance stays unfenced. Scope is honest (proposer confused-deputy one-click only; turn channel relies on autonomy floor + human ⚠; porosity deferred to 1b). Tests pin the landmine exclusions, maintenance path, audit-only tag, and mutation re-deny.

---

## Verdict

**SOUND** — Headline guarantee holds under adversarial review; only nit is fail-closed hardening of `names_sensitive_path` against throwing `__str__`.

**Single highest-value fix:** In `names_sensitive_path`, catch all exceptions and return `""` so new call sites cannot violate the never-raise contract even for in-process hostile `command` objects.
