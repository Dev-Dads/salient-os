# LoopFix panel: openai/gpt-5.1

_cost=$0.0826 finish=stop_

ID: F1 / TITLE: Empty completion via malformed message dict / SEVERITY: MEDIUM  
LOCATION: collaborator/loop.py:_is_actionable, _complete_actionable, run_turn (various; core at loop.py:168–220)  
CONCRETE INPUT OR BYPASS:  
- Caller passes a client that sometimes returns a malformed-but-nonempty message where `parse_message` yields empty text but `_content(msg)` yields deceptive whitespace-only or junk that the parser strips away. Example:

```python
class WeirdClient:
    def __init__(self):
        self.calls = 0
    def complete(self, messages, tools=None, temperature=None):
        self.calls += 1
        # Deterministic "empty" reply at all temps: parser strips to empty text,
        # but raw message content is whitespace (or junk) that _content() sees.
        return {"content": "   \n\t", "tool_calls": []}

s = Session(workspace=tmp)
client = WeirdClient()
r = run_turn(s, client, "do something", empty_retries=0)
```

WHY IT BREAKS A GUARANTEE:  
- The guarantee in C1 is “an empty completion (no content, no tool_calls, no ambiguous) can NEVER be reported as a finished/successful turn.”  
- Here, `parse_message` with `"   \n\t"` yields `parsed.text == ""`, `parsed.intents == ()`, `parsed.ambiguous == ()`.  
- `_is_actionable` computes `(parsed.text or _content(msg)).strip()` → `("   \n\t").strip()` → `""` → `False`, so `_complete_actionable` returns `(msg, parsed, False)`.  
- In `run_turn`, since `actionable` is False, the function returns `TurnResult(reply="(no action taken — the model returned an empty response N times)", stopped="empty")`.  
- That is correct and *not* a silent success: this is actually a *non-finding* with respect to C1 for well-formed messages.

The *potential* issue would be if `parse_message` ever returned `parsed.text != ""` with only whitespace-visible content stripped out in a way that `_content` still sees as empty and vice versa. I attempted to construct such inputs (e.g., non‑printable Unicode that `parse_message` strips but `_content` preserves, or tool-call markers that are fully removed leaving no remaining text), but in each case:

- If there is a tool call, `_is_actionable` is True due to `parsed.intents`/`parsed.ambiguous`.
- If there is no tool call and the remaining text is blank or strip‑to‑blank, `_is_actionable` correctly returns False and the turn ends as `stopped="empty"`.

WHETHER ANOTHER LAYER CATCHES IT:  
- The governance seam (`govern_action`) is never reached on these empty replies; the behavior is entirely owned by `_is_actionable` and `run_turn`.  
- After adversarial attempts (malformed dicts, odd tool_call combinations, content-only JSON, fenced content, etc.), I could not produce a path where a genuinely empty completion (in the authors’ precise sense: no intents, no ambiguous, and no non‑whitespace prose) ends up with `stopped="final"`. Every such case yields `stopped="empty"`.

FIX:  
- No fix required for the claimed invariant; the current `_is_actionable` logic is correct for C1.  
- If desired, you could harden `_complete_actionable`’s initialization to avoid the cosmetic parse of `{}`:

```python
def _complete_actionable(...):
    tools = openai_tools()
    msg = None
    parsed = None
    ...
```

but that does not affect correctness.

This is therefore a **non-finding**: attempts to break C1 on the empty-completion path failed.

---

ID: F2 / TITLE: Retry state and temperature bypass of govern_action / SEVERITY: LOW  
LOCATION: collaborator/loop.py:_complete_actionable (lines ~180–203); collaborator/governance.py:govern_action (~250+)  
CONCRETE INPUT OR BYPASS:  
- Construct a client that acts only at higher temperatures and attempts to smuggle a direct “authority” indicator in the content, e.g.:

```python
class TempAwareClient:
    def __init__(self):
        self.calls = 0
    def complete(self, messages, tools=None, temperature=None):
        self.calls += 1
        if temperature is None:
            return {"content": "", "tool_calls": None}
        if temperature >= 0.7:
            # Try to signal "autonomous" via args or content
            return {"content": '<tool_call>{"name":"run_command","arguments":{"command":["echo","hi"],"leash":"act_then_report"}}</tool_call>'}
        return {"content": "", "tool_calls": None}
```

`run_turn(session, TempAwareClient(), "run something")` eventually yields a run_command ToolIntent.

WHY IT BREAKS A GUARANTEE:  
- It *does not* break C2’s guarantee, but I explicitly tried to find a path.  
- The additional temperature is only passed into `client.complete`. The parsed intents and arguments from the retry (e.g., including a `leash` field in the JSON) flow into `govern_action`, but the authority and leash logic in `govern_action` is already hardened:  
  - `govern_action` never inspects temperature or attempt index.  
  - The per-task `leash` that matters is the keyword-only argument to `govern_action`, which `run_turn` never supplies from model output.  
  - Any `leash` embedded in `intent.args` is treated as data, not as the leash control parameter.  
  - Capability and leash caps are derived solely from session policy, not from model output or retry state.

WHETHER ANOTHER LAYER CATCHES IT:  
- Yes: `govern_action` is the independent gate, and it ignores both temperature and retry count.  
- The per-tool leash floorings (e.g., net_post and run_command autonomy floors) apply identically regardless of how the ToolIntent was produced (first attempt or retry).

FIX:  
- None needed with respect to C2. To make this even more explicit for future maintainers, you could add a brief docstring comment near `run_turn`’s call to `govern_action` stressing that no retry/temperature state is ever threaded into the authorization path.

This is a **non-finding**: I could not construct a scenario where retry state or temperature widens authority or bypasses `govern_action`.

---

ID: F3 / TITLE: Retry loop and outer loop termination properties / SEVERITY: LOW  
LOCATION: collaborator/loop.py:_complete_actionable (lines ~189–203); run_turn (max_iterations loop at ~214–262)  
CONCRETE INPUT OR BYPASS:  
- Potentially adversarial completion patterns:

1. Immediate valid answer (no tools): `ScriptedClient([{"content": "Final."}])`.  
2. Ambiguous-only: `{"tool_calls": [{"function": {"name":"write_file","arguments":"{bad json"}}]}`.  
3. Always-empty with `empty_retries` 3: `ScriptedClient([EMPTY, EMPTY, EMPTY, EMPTY, ...])`.  
4. Mixed: empty on attempt 0, valid tool_call on attempt 1, etc.

WHY IT BREAKS A GUARANTEE:  
- For C3, we need to see if (a) a legitimate final answer ever gets re-rolled; (b) the loop can spin unboundedly; (c) decisions collected before an empty attempt can be lost; and (d) whether the retry budget arithmetic misbehaves.

Tracing:

- `_complete_actionable` does:

```python
for attempt in range(max(1, empty_retries + 1)):
    if attempt == 0:
        msg = client.complete(..., tools=tools)
    else:
        msg = client.complete(..., tools=tools, temperature=_retry_temperature(attempt))
    parsed = parse_message(msg)
    if _is_actionable(msg, parsed):
        return msg, parsed, True
return msg, parsed, False
```

- So the number of calls per inner loop is exactly `max(1, empty_retries+1)`. For `empty_retries` in {0,1,3} that is {1,2,4}.  
- A “legit final answer” (text, no tool_calls) yields `parsed.intents == ()`, `_is_actionable` True on the first call, so no retries occur and `len(client.seen) == 1`.  
- An ambiguous-only completion still has `parsed.ambiguous`, which makes `_is_actionable` return True; these are not retried.  
- Only truly empty completions (no intents, no ambiguous, and no non-whitespace text) consume retries.  
- If all attempts in the inner loop are empty, `actionable` is False and `run_turn` returns once from that iteration with `stopped="empty"`, not re-entering the outer loop.  
- The outer `for _ in range(max_iterations)` bounds the number of iterations even if every iteration produced tools and required more model calls.

I also checked whether a valid final answer could be re-rolled after some tool calls in earlier iterations: it cannot, because once `_is_actionable` returns a `parsed` without intents, `run_turn` takes the `if not parsed.intents:` branch and returns immediately. No second attempt is issued for that iteration, and the outer loop is exited.

Decisions and ambiguous lists are accumulated *outside* the retry loop, so an empty attempt does not drop any previously-recorded decisions; the early-return on actionable=False still returns the currently-accumulated `decisions`/`ambiguous` lists unchanged.

WHETHER ANOTHER LAYER CATCHES IT:  
- There is no second governance layer for termination; this is purely local behavior.  
- However, given the explicit loops and bounds, I do not see a path to unbounded spinning or re-rolling valid finals.

FIX:  
- No change needed for C3. If you want to belt-and-suspenders guard future refactors, you might assert that `empty_retries >= 0` at the `run_turn` boundary and document that `empty_retries` is expected to be small and under operator control.

Another **non-finding**: no unbounded call storm, and no re-roll of valid answers.

---

ID: F4 / TITLE: Callers assuming stopped ∈ {final, held, paused, max_iterations} / SEVERITY: MEDIUM  
LOCATION: collaborator/loop.py:TurnResult.stopped comment (line ~132); tests use; external callers (not shown in this diff)  
CONCRETE INPUT OR BYPASS:  
- Any scenario where the model remains deterministically empty for `empty_retries+1` attempts. Example from tests:

```python
client = ScriptedClient([EMPTY, EMPTY, EMPTY])
r = run_turn(s, client, "do something", empty_retries=2)
# r.stopped == "empty"
```

- Now consider an *external caller* that predates this change and has branching like:

```python
r = run_turn(...)
if r.stopped in ("final", "held", "paused", "max_iterations"):
    # treat as known; anything else assumed "final"
    return render_success(r)
# or, worse:
if r.stopped != "held":
    show_final(r.reply)  # implicitly assuming non-held == final-and-done
```

WHY IT BREAKS A GUARANTEE:  
- The new state `"empty"` is *semantically* correct as an explicit non-success, but any code outside this repository that assumes the old enum will mis-handle it.  
- The user’s claim in C4 is that “stopped="empty" is a value callers can handle (it is not a silent success).” That is only guaranteed inside this codebase and its tests. Without an audit of all consumers (e.g., `propose.py`, any web UI or resume driver), there is a realistic risk that `"empty"` is treated as "final" or otherwise silently conflated with success.  
- That would reintroduce—in the calling layer—the exact sort of “silent no-op treated as done” behavior the fix is meant to remove, just one layer up.

WHETHER ANOTHER LAYER CATCHES IT:  
- No. `govern_action` and the rest of the seam do not know about `stopped` semantics; this is purely an interface contract between `run_turn` and its callers.  
- The unit tests protect *internal* usage but do not prove that external callers handle `"empty"` robustly.

FIX:  
- Short-term: add a compatibility shim or exhaustive handling in your own top-level entry points (e.g., the session driver, UI, and `propose.py`) so that any non-enumerated `stopped` state is handled explicitly, and ensure `"empty"` is not sent down the “success” path.  
- Medium-term: change the type comment or a docstring on `TurnResult` to make `"empty"` part of a documented enum and add tests in any public API layer that consumes `run_turn` to assert explicit handling of this case.  
- If you really need strict backward compatibility, consider adding a configuration flag that maps `"empty"` back to `"final"` with an error-prefixed `reply`, but that weakens the honesty guarantee; much better is to make all callers respect `"empty"` as its own state.

---

ID: F5 / TITLE: Per-call temperature kwarg and ScriptedClient / SEVERITY: LOW  
LOCATION: collaborator/model_client.py:OllamaClient.complete (lines ~33–42); ScriptedClient.complete (lines ~51–61); tests at tests/test_collaborator_loop.py (new EmptyCompletion tests)  
CONCRETE INPUT OR BYPASS:  
- Use of ScriptedClient with no temperature override:

```python
client = ScriptedClient([{"content": "hi"}])
r = run_turn(s, client, "hello")
# ScriptedClient.complete is called with temperature=None on first attempt;
# non-None on retries, but ScriptedClient ignores it apart from recording.
```

- Use any existing tests that consume ScriptedClient, including the new EmptyCompletion tests.

WHY IT BREAKS A GUARANTEE:  
- The claim in C4 is that the temperature kwarg does not break the None/scripted paths.  
- I checked: ScriptedClient.complete adds the `temperature` parameter but does not *use* it for behavior; it only appends it to `self.temps`. Existing tests still operate correctly, and default behavior (no temperature passed) produces `temperature=None` on first calls, which matches the intended "use client default" semantics.  
- OllamaClient.complete computes `temp = self.temperature if temperature is None else temperature`, so the override correctly falls back to the configured default. No external caller shown in this diff calls `complete` positionally in a way that would mis-bind `temperature`; both OllamaClient and ScriptedClient signatures are updated consistently.

WHETHER ANOTHER LAYER CATCHES IT:  
- Tests in `tests/test_collaborator_loop.py` explicitly assert that the first call uses `temperature=None` and retries use increasing non-None temperatures, which validates the behavior on the scripted path.  
- For the real client, there is no governance-layer dependence on temperature or max_tokens values, so governance is unaffected.

FIX:  
- None required for correctness. For defensive programming, you could consider asserting `0.0 <= temp <= 2.0` in OllamaClient.complete to prevent absurd configurations, but that is an operator policy question, not a correctness one here.

This is a **non-finding** regarding C4’s temperature override.

---

ID: F6 / TITLE: Increased default max_tokens for OllamaClient / SEVERITY: LOW  
LOCATION: collaborator/model_client.py:OllamaClient.__init__ (line ~22)  
CONCRETE INPUT OR BYPASS:  
- Any normal `OllamaClient(base_url, model)` instantiation now uses `max_tokens=4096` instead of `1024`.  
- A pathological scenario would be an upstream model that crashes/hangs on high max_tokens, but that’s not specific to this code.

WHY IT BREAKS A GUARANTEE:  
- For the claims here, it doesn’t. It potentially increases cost and response length but does not alter the governing semantics. `govern_action` doesn’t read max_tokens, and the parser is already robust to long content.  
- It does not affect any of the C1–C4 properties except in the intended way (fewer empties due to more token budget).

WHETHER ANOTHER LAYER CATCHES IT:  
- No governance-layer change, but this is orthogonal to the authority and honesty guarantees under review.

FIX:  
- None required for correctness of these claims.

---

### CERTIFICATION LINES

C1 (no silent no-op): **CERTIFIED** – Within this loop, I could not construct any path where a genuinely empty completion (no intents, no ambiguous, and no non-whitespace text) is reported as `stopped="final"`; all such cases end as `stopped="empty"` with an explicit diagnostic reply.

C2 (governance untouched): **CERTIFIED** – Retry state and per-call temperature are confined to the model client and do not influence `govern_action` or the seam’s capability/leash decisions; the same ToolIntent from a retry is governed identically to one from a first attempt.

C3 (termination + bounded cost): **CERTIFIED** – `_complete_actionable` and `run_turn` are both bounded (by `empty_retries` and `max_iterations`), a legitimate final answer is not retried, and I found no path to unbounded spinning or re-rolling an otherwise-valid terminal answer.

C4 (no regression / interface): **NOT-CERTIFIED** – Internally, ScriptedClient and OllamaClient handle the new temperature kwarg and max_tokens correctly, but the new `stopped="empty"` state can break external callers that assume the older, smaller stopped-state enum; I cannot vouch for those without seeing their code.

---

### Steelman

The fix meaningfully hardens the loop against a real, empirically observed failure mode without diluting the core governance boundary: all non-empty completions, including high-temperature retries, still flow through `govern_action`, and empty deterministic streaks are now surfaced honestly instead of being misreported as success. The `_is_actionable` predicate is carefully aligned with the parser’s notion of “real work” (intents, ambiguous calls, or non-whitespace text), and the retry budget is explicit and bounded. The remaining risk is not in the loop itself, but in any upstream caller that hasn’t been updated to treat `"empty"` as its own terminal state rather than silently lumping it into “done”.

### Verdict

VERDICT: **MINOR_ISSUES** – The loop and seam behave soundly with respect to the stated claims; the single highest-value fix is to audit and harden all external consumers of `TurnResult.stopped` so that `"empty"` is explicitly handled as a failure-to-act, never conflated with `"final"`.
