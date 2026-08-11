# PR#38 sensitivepaths certification panel: openai/gpt-5.1

_finish=stop seconds=40.9 cost=$0.0938 usage={'prompt_tokens': 42756, 'completion_tokens': 4034, 'total_tokens': 46790, 'cost': 0.093785, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.093785, 'upstream_inference_prompt_cost': 0.053445, 'upstream_inference_completions_cost': 0.04034}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID 1 / TITLE: `names_sensitive_path` does not fail-closed on hostile `command` types  
SEVERITY: LOW  
LOCATION: collaborator/sensitivepaths.py:63-81  
CONCRETE INPUT OR BYPASS:  
- `names_sensitive_path(object())` where `object().__str__` raises or loops, e.g.:

```python
class Bad:
    def __str__(self):
        raise RuntimeError("boom")
names_sensitive_path(Bad())
```

or an object whose `__str__` never terminates.  
WHY IT BREAKS A GUARANTEE:  
- The certification claim states “govern_action/approve never raise on a hostile `command` type.”  
- `names_sensitive_path` is called in both `govern_action` and `execute_and_verify` and is used in `approve` for re-deny. It calls `str(command or "")` (or `str(c)` in a list) without any protection. A hostile type with a throwing or non-terminating `__str__` will propagate an exception or hang, violating the “never raises” / robustness guarantee.  
WHETHER ANOTHER CHECK CATCHES IT:  
- No. All callsites treat `names_sensitive_path` as side-effect-free and do not wrap it. Any exception bubbles out of `govern_action` or `approve`. There is no outer try/except at the top of these functions.  
FIX:  
- Make `names_sensitive_path` explicitly defensive, mirroring the intent in `_subject` and elsewhere:

```python
def names_sensitive_path(command) -> str:
    try:
        if isinstance(command, (list, tuple)):
            parts = []
            for c in command:
                try:
                    parts.append(str(c))
                except Exception:
                    parts.append("")  # or a stable placeholder
            text = " ".join(parts)
        else:
            try:
                text = str(command or "")
            except Exception:
                text = ""
    except Exception:
        return ""
    if not text:
        return ""
    hay = text.replace("\\", "/").lower()
    ...
```

- Document that on any error it returns `""` (fail-closed to “no tag, no deny”), which is acceptable since `names_sensitive_path` is explicitly non-boundary.  

---

ID 2 / TITLE: `govern_action` can raise on non-dict or non-str `intent.args` / `command` due to unguarded `.get` + `names_sensitive_path`  
SEVERITY: LOW  
LOCATION: collaborator/governance.py:171-178, 508-515, 649-683  
CONCRETE INPUT OR BYPASS:  

1. Non-dict args:

```python
intent = ToolIntent("run_command", ["not_a_dict"], "proposed")
govern_action(session, intent)
```

In `govern_action`:

```python
intent.args.get("command")
```

raises `AttributeError: 'list' object has no attribute 'get'`.

2. Dict mapping with hostile `__getitem__`/`get` or mapping-like proxy:

```python
class BadDict(dict):
    def get(self, *a, **k):
        raise RuntimeError("boom")
intent = ToolIntent("run_command", BadDict(command="cat /etc/shadow"), "proposed")
govern_action(session, intent)
```

3. Hostile `command` content (see ID 1) – same effect via `names_sensitive_path` / `names_code_root`.  

WHY IT BREAKS A GUARANTEE:  
- The promise is that `govern_action` “never raises” even on hostile `command` types. Untrusted `ToolIntent` can be constructed directly (e.g., from a compromised client or fuzzing harness) without going through `toolcall.parse_message`. Currently, a malformed/hostile `intent.args` can cause unexpected exceptions at multiple points (hard-deny check, preview generation, execution path), violating robustness.  
WHETHER ANOTHER CHECK CATCHES IT:  
- No. There is no envelope-level try/except around `govern_action`. Callers like `run_turn` assume a `Decision` is always returned.  
FIX:  
- Normalize and guard access to `intent.args` at the top of `govern_action` and all sensitive uses:

```python
def govern_action(..., intent: ToolIntent, ...):
    raw_args = intent.args
    # Force args into a plain dict, fail-closed to {} on bad types.
    try:
        if not isinstance(raw_args, dict):
            args = {}
        else:
            # Shallow copy to avoid hostile mappings with overridden `get`
            args = dict(raw_args)
    except Exception:
        args = {}
    intent_args = args  # use this instead of intent.args below
    ...
    # In sensitive-path / code-root deny:
    cmd = intent_args.get("command")
    try:
        sensitive = names_sensitive_path(cmd)
    except Exception:
        sensitive = ""  # fail-closed
    ...
```

- Mirror that pattern in the HELD preview construction and in `execute_and_verify` where `names_sensitive_path`/`names_code_root` are called; don’t trust `args` to be a safe mapping.  

---

ID 3 / TITLE: `approve()` can raise when `decision.args` is non-dict or mapping with hostile behavior  
SEVERITY: LOW  
LOCATION: collaborator/loop.py:153-171  
CONCRETE INPUT OR BYPASS:  

```python
class BadArgs(dict):
    def items(self):
        raise RuntimeError("boom")

d = Decision(..., args=BadArgs(command=["echo", "hi"]), status=HELD, ...)
approve(session, d)
```

- `approve` does `dict(decision.args)`; for a mapping that raises on `__iter__` or `items`, this will raise, violating the “approve() never raises” guarantee. Similarly, if `decision.args` is not dict-like (e.g., a list from a buggy caller), `dict(decision.args)` will raise `TypeError`.  

WHY IT BREAKS A GUARANTEE:  
- The certification claim explicitly includes “approve() never raise on a hostile `command` type.” While the tests construct `Decision` via `govern_action` (which uses `freeze_args` for HELD), external callers can construct or mutate a `Decision` object directly (including via proposal pool, buggy integrations, or fuzzing). A hostile `decision.args` type will cause `approve` to throw before any of its safety checks, violating robustness.  
WHETHER ANOTHER CHECK CATCHES IT:  
- No. `approve` is a public API; callers don’t wrap it.  
FIX:  
- Harden the snapshot of args:

```python
def approve(session, decision: Decision) -> Decision:
    ...
    try:
        base_args = decision.args if isinstance(decision.args, dict) else {}
        args = freeze_args(dict(base_args))
    except Exception:
        # Fail-closed: treat as empty args; subsequent checks will likely deny or be no-ops.
        args = {}
```

- All subsequent uses in `approve` already tolerate missing keys (they `get("command")`/`get("path")`), so treating pathological args as `{}` keeps the function from raising while failing closed on safety checks.  

---

ID 4 / TITLE: `names_sensitive_path` can hang / be very slow on deeply nested or large hostile structures  
SEVERITY: LOW  
LOCATION: collaborator/sensitivepaths.py:63-81  
CONCRETE INPUT OR BYPASS:  

```python
# Create a very deep list of objects with expensive __str__
class Slow:
    def __str__(self):
        # heavy computation or large allocation
        return "A" * 10_000_000

cmd = [Slow()] * 10_000
names_sensitive_path(cmd)
```

- Because it turns every element into a string and concatenates them, a malicious or pathological input can induce extreme CPU/memory use or effectively hang.  

WHY IT BREAKS A GUARANTEE:  
- The guarantee “never raise” doesn’t explicitly promise latency bounds, but the text does say “fail closed” and treats these recognizers as cheap DiD tags. In a hostile environment, `govern_action`/`approve` can be driven into DoS via this unbounded work on attacker-supplied structures. While this is more of a resilience than a correctness break, it weakens the intent that governance calls are robust to hostile tool-calls.  
WHETHER ANOTHER CHECK CATCHES IT:  
- No separate guard; all callsites trust `names_sensitive_path`.  
FIX:  
- Add lightweight limits on the amount of work:

```python
MAX_PARTS = 256
MAX_TEXT_LEN = 8192

def names_sensitive_path(command) -> str:
    parts = []
    if isinstance(command, (list, tuple)):
        for c in command:
            if len(parts) >= MAX_PARTS:
                break
            try:
                s = str(c)
            except Exception:
                s = ""
            parts.append(s)
        text = " ".join(parts)
    else:
        try:
            text = str(command or "")
        except Exception:
            text = ""
    if len(text) > MAX_TEXT_LEN:
        text = text[:MAX_TEXT_LEN]
    ...
```

- This keeps the function cheap and bounded even for malicious inputs, still consistent with its “porous, not boundary” role.  

---

ID 5 / TITLE: Secret-touch tags can be silently suppressed by hostile `args` mutation between execution and summary  
SEVERITY: LOW  
LOCATION: collaborator/governance.py:131-164; 649-683  
CONCRETE INPUT OR BYPASS:  

- `execute_and_verify` computes:

```python
secret_touch = names_sensitive_path(args.get("command"))
...
Decision(..., secret_touch=secret_touch)
```

- If an external caller subsequently mutates `decision.secret_touch` or `decision.args` before `summary()` is rendered, the “what actually ran” vs “what is recorded” link can be broken. With a hostile extension, you can create a `Decision` for a secret-touching command but clear `secret_touch` and have `summary()` report nothing.  

WHY IT BREAKS A GUARANTEE:  
- The PR text claims “secret_touch is audit-only and never flips a decision” and implicitly that it honestly reflects what ran. There is no structural binding between the executed args and `secret_touch`; it is a plain mutable field. However, the design does not state a hard guarantee that `secret_touch` cannot be post-hoc tampered with by in-process adversaries; it’s intended as DiD only. This is therefore more of a “belt-and-suspenders missing” than a guarantee violation.  
WHETHER ANOTHER CHECK CATCHES IT:  
- No second check binds secret_touch to the execution result. However, the threat model here is in-process adversarial code, which is generally out-of-scope for these seam-level invariants.  
FIX:  
- If you want stronger integrity for the audit tag, compute it on the fly from `args` in `summary()` instead of trusting the stored field:

```python
def summary(self) -> str:
    ...
    # recompute secret_touch from args if tool is run_command
    actual_secret_touch = self.secret_touch
    if self.tool == "run_command" and isinstance(self.args, dict):
        try:
            actual_secret_touch = names_sensitive_path(self.args.get("command"))
        except Exception:
            pass
    if actual_secret_touch:
        tail += f"  ⚑ secret-touch audit: {actual_secret_touch}"
```

- Given the stated scope (no in-process adversary), this is a non-essential hardening; current behavior does not contradict the PR’s explicit guarantees about *authorization*, only audit fidelity.  

---

ID 6 / TITLE: `names_sensitive_path` is never relied on as sole boundary (documented non-finding)  
SEVERITY: N/A (documented NON-FINDING)  
LOCATION: collaborator/governance.py:356-378; 508-515; 649-683; collaborator/loop.py:190-199  
CONCRETE INPUT OR BYPASS:  
- Commands like `["sh", "-c", 'k=$(cat cfg); cat "$k"']` or `["tar", "czf", "-", "~"]` evade `names_sensitive_path` by design.  
WHY IT BREAKS A GUARANTEE:  
- It does not. The PR docstring and tests explicitly state porousness is intended and out of scope, and all uses of `names_sensitive_path` are DiD:  
  - Hard-deny for proposer-originated `run_command` naming secrets (govern_action).  
  - Preview flag `names_sensitive_path` for HELD decisions.  
  - Audit tag `secret_touch` on execution.  
  - Approve-time re-deny only for collaborator-origin HELD commands that currently name a sensitive path, with MINOR‑B seal as the structural arg-integrity boundary.  
- No control path relies on it as the only containment barrier; the filesystem is left deliberately unfenced for human-directed maintenance, as documented.  
WHETHER ANOTHER CHECK CATCHES IT:  
- Structural containment is intentionally deferred to the separate 1b PR (bubblewrap). No additional check is expected here; this is by design.  
FIX:  
- None; behavior matches the stated design.  

---

ID 7 / TITLE: Re-deny at `approve()` is belt-and-suspenders, not load-bearing  
SEVERITY: N/A (design characterization)  
LOCATION: collaborator/loop.py:189-207  
CONCRETE INPUT OR BYPASS:  
- Mutating a collaborator-origin HELD decision’s args after hold to introduce a secret path:

```python
held = govern_action(..., ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
held.origin = "collaborator"
held.args["command"] = ["cat", "/home/op/.ssh/id_rsa"]
d = approve(session, held)
```

WHY IT BREAKS A GUARANTEE:  
- It does not:  
  - The MINOR‑B args seal (`held_action_seal` + verification in `approve`) already fails any post-hold mutation for SEALED_TOOLS (including `run_command`), regardless of whether the mutation touches secrets. This is the structural “approved == executed” boundary.  
  - The new `names_sensitive_path` re-deny adds a more specific reason string and symmetry with code-root re-deny but is not necessary to prevent post-hold secret-touching execution. If the re-deny were removed, the seal mismatch would still cause a DENIED decision before execution.  
WHETHER ANOTHER CHECK CATCHES IT:  
- Yes: MINOR‑B seal independently blocks post-hold mutation; the re-deny is purely defense-in-depth.  
FIX:  
- None required. It may be useful to comment explicitly that the re-deny is non-load-bearing and the seal is the actual boundary (the comment already gestures at this).  

---

HEADLINE CLAIM CERTIFICATION

Claim:  
“A PROPOSER-originated run_command naming an operator secret cannot autonomously run and cannot bypass the hard-DENY (it is refused at govern_action); a USER/HOST-directed command is UNAFFECTED (held-then-run — maintenance preserved); secret_touch is audit-only and never flips a decision; no existing guarantee regressed; govern_action/approve never raise on a hostile `command` type.”

CERTIFICATION LINE:  
NOT-CERTIFIED — the deny/hold logic and scopes are sound, but the “govern_action/approve never raise on a hostile `command` type” part is not currently met due to unguarded use of `names_sensitive_path`/`names_code_root` and assumptions about `args` being benign dicts.

- With respect to *authorization semantics*:  
  - I cannot find any control path where a proposer-originated `run_command` whose `command` literally contains one of `_SENSITIVE_MARKERS` can either:  
    - run autonomously (govern_action floors proposer shells to HELD and the execution path re-asserts autonomy floors), or  
    - evade the hard-DENY keyed on `intent.source == "proposed"` by forging or altering `source`.  
    - `propose.py` is the sole place that sets `source="proposed"`; `toolcall.py` never produces `"proposed"` (it uses `"structured"`, `"content_block"`, `"content_json"`). The model cannot manufacture `"proposed"` through the parsing path.  
  - USER/HOST-directed secret-touching commands (`source != "proposed"`) are not denied by the new check and still go HELD→`approve()`→`RAN` as long as other gates pass; tests confirm this and the code’s source checks are precise (`getattr(intent, "source", "") == "proposed"`).  
  - `secret_touch` is always computed and threaded as an audit-only field and is not used in any gating decision; no decision path flips RAN↔DENIED based on it.  

- With respect to *robustness guarantees*:  
  - `names_sensitive_path` and `names_code_root` can raise or hang on hostile `command` types, and untrusted `args` objects can cause `govern_action` and `approve` to raise. This contradicts the “never raise on hostile `command` type” portion of the claim. Hardening those callsites as suggested would close this gap.  

---

STEELMAN (system strengths)

The integration of `names_sensitive_path` is consistent and minimal: it mirrors the existing code-root machinery, is keyed strictly on `intent.source == "proposed"` for authorization changes, and is otherwise confined to preview/audit roles. The proposer path cannot emit an intent with any other `source` value, and the turn parser has no way to synthesize `"proposed"`, which makes `source == "proposed"` a reliable indicator of “model self-originated on the proposal channel.” The MINOR‑B seal continues to carry the real TOCTOU boundary for run_command/write_file regardless of secret recognition, so no new trust is being placed on a porous recognizer as a structural fence.

---

VERDICT

VERDICT: MINOR_ISSUES — The core authorization and scoping logic for Harm B is sound and matches the design, but robustness against hostile `command` / `args` types is not fully implemented, invalidating the “never raise” robustness guarantee.  

Single highest-value fix:  
- Harden `names_sensitive_path`, `names_code_root`, `govern_action`, and `approve` against hostile/ill-typed `command` and `args` by normalizing `args` into a safe dict at entry, wrapping calls to these recognizers in try/except that returns `""` on error, and bounding work in `names_sensitive_path`; once done, the shipped PR would meet both the authorization and robustness aspects of the stated claim.
