# PR#38 sensitivepaths certification panel: qwen/qwen3-max

_finish=stop seconds=32.7 cost=$0.0777 usage={'prompt_tokens': 42896, 'completion_tokens': 1376, 'total_tokens': 44272, 'cost': 0.07765056, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.07765056, 'upstream_inference_prompt_cost': 0.06691776, 'upstream_inference_completions_cost': 0.0107328}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

**FINDINGS**

---

**ID**: F1  
**TITLE**: `names_sensitive_path` raises on hostile `command` input, violating “never raise” guarantee  
**SEVERITY**: HIGH  
**LOCATION**: `collaborator/sensitivepaths.py:84` (`names_sensitive_path`)  
**CONCRETE INPUT OR BYPASS**: `command = object()` with a `__str__` that raises (e.g., `type('Boom', (), {'__str__': lambda _: 1/0})()`)  
**WHY IT BREAKS A GUARANTEE**: `govern_action` and `approve` promise never to raise. `names_sensitive_path` calls `str(command)` unguarded; if `__str__` raises, the exception propagates out of `govern_action`/`approve`, breaking audit guarantees and potentially crashing the loop.  
**WHETHER ANOTHER CHECK CATCHES IT**: **NO** — no try/except wraps the call in `govern_action` or `approve`. The only backstops are broad `except Exception` in `execute_and_verify` (exit branch) and `reauthorized_or_denied`, but those do **not** wrap the `names_sensitive_path` calls in the *deny* or *preview* paths.  
**FIX**: Wrap `str(command or "")` in a try/except that returns `""` on exception (fail closed).

---

**ID**: F2  
**TITLE**: Proposer can bypass hard-deny by emitting `source != "proposed"` via content-embedded tool call  
**SEVERITY**: CRITICAL  
**LOCATION**: `collaborator/toolcall.py:parse_message` + `collaborator/governance.py:366`  
**CONCRETE INPUT OR BYPASS**: A proposer emits a run_command as a **content_block** or **content_json** (not structured). The parser sets `source="content_block"` or `"content_json"`. The deny condition requires `source == "proposed"`, so it is **not triggered**. The command is HELD (due to autonomy floor), but **one-click approvable**, achieving the confused-deputy trap the deny was meant to prevent.  
**WHY IT BREAKS A GUARANTEE**: The certification claim states: “a proposer-originated secret-touching run_command cannot … bypass the hard-DENY”. But a proposer can originate such a command via non-structured channels, dodging the deny while remaining model-authored and one-click-approvable.  
**WHETHER ANOTHER CHECK CATCHES IT**: **NO** — the only other check is the approve-time re-deny, which keys on `origin == "collaborator"`. However, **only `propose.py` sets `origin = "collaborator"`**. A turn-channel intent (from `run_turn`) gets `origin = "direct"` by default in `Decision.__init__`. So the approve-time re-deny **does not trigger** either.  
**FIX**: Change the deny condition to key on **provenance**, not parse-channel `source`. Specifically, hard-deny if `(intent.name == "run_command" and names_sensitive_path(...) and getattr(intent, "source", "").startswith("content_"))` **OR** extend `propose.py` to be the **only** path that can produce `source="proposed"`, and ensure all model-authored commands (including turn-channel) are marked with a common indicator (e.g., a new `model_authored` flag). Alternatively, set `origin = "collaborator"` for **all** model-authored intents in `run_turn`.

---

**ID**: F3  
**TITLE**: Approve-time re-deny uses mutable `args`, allowing TOCTOU bypass if seal check is skipped  
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/loop.py:193` (`approve`)  
**CONCRETE INPUT OR BYPASS**: A held `run_command` decision with `tool not in SEALED_TOOLS` (e.g., due to a bug or future refactor removing it) and `seal == ""`. An attacker mutates `decision.args["command"]` to a secret path **after hold but before approve**. The approve-time re-deny runs `names_sensitive_path(args.get("command"))` on the **live** `args` (before `freeze_args`). If the seal check is skipped (because `tool not in SEALED_TOOLS`), the mutated secret command **runs**.  
**WHY IT BREAKS A GUARANTEE**: The re-deny is meant to catch post-hold mutations, but it operates on unfrozen args. The MINOR-B seal is the primary defense, but if it fails open (e.g., tool removed from `SEALED_TOOLS`), the re-deny should be a backup. However, because it reads mutable state, it can be bypassed.  
**WHETHER ANOTHER CHECK CATCHES IT**: **YES, BUT ONLY IF SEAL IS ENFORCED** — the MINOR-B seal (`held_action_seal`) is the load-bearing check. This finding only materializes if that seal is absent or bypassed.  
**FIX**: Move the approve-time re-deny **after** `args = freeze_args(dict(decision.args))` so it operates on the immutable snapshot.

---

**CERTIFICATION LINE**  
**NOT-CERTIFIED** — a proposer can originate a secret-touching run_command via content-embedded tool calls that bypasses the hard-deny and reaches a one-click-approvable HELD state, breaking the core guarantee.

---

**STEELMAN**  
The PR correctly implements a porous, defense-in-depth layer that hard-denies only the narrow `source=="proposed"` channel, preserving maintenance for user-directed commands. The approve-time re-deny and audit tags are symmetric with Harm A, and the test suite thoroughly validates the happy paths and documented exclusions.

---

**VERDICT**  
**SERIOUS_FLAWS** — the hard-deny is bypassable by model-authored commands emitted outside the structured `proposed` channel, allowing adversarial proposers to surface one-click-approvable secret-exfiltration commands.  
**Highest-value fix**: Extend the hard-deny condition in `govern_action` to cover **all model-authored origins** (i.e., `source in ("proposed", "content_block", "content_json")`), or introduce a unified `model_authored` flag at parse/emit time to reliably distinguish operator-directed vs. model-authored intents.
