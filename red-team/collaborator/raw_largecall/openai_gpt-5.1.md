# LargeCall panel: openai/gpt-5.1

_cost=$0.0942 finish=stop_

ID: F1 / TITLE: Unbalanced `<tool_call>` Can Leave Raw Markup in `text` / SEVERITY: LOW  
LOCATION: collaborator/toolcall.py:175–196  
CONCRETE INPUT OR BYPASS:  
```python
from collaborator.toolcall import parse_message
content = 'Intro...\n<tool_call>{"name":"write_file","arguments":{"path":"a","content":"x"}\nMore text.'
r = parse_message({"content": content})
# r.ambiguous == ('{"name":"write_file","arguments":{"path":"a","content":"x"}\nMore text.',)
# r.text == 'Intro...\n<tool_call'
```  
WHY IT BREAKS A GUARANTEE:  
The intent of the change is: “an unbalanced `<tool_call>` is appended to `ambiguous` and stripped from prose `text`.” Current logic strips spans based on `start`/`end` then does a text-level `.replace("<tool_call", "")`. For an unbalanced span, `_tool_call_tag_objects` sets `start` at the marker, `end` at `len(content)`, but in the removal loop the hit’s JSON (`_js`) and balance flag are ignored, so the *entire* trailing span is removed from prose, yet the literal string `<tool_call` outside that span can remain if there are overlapping or malformed markers or if a host later prepends/concatenates text that reintroduces the marker sequence. The guarantee C1 is specifically “stripped from the prose text”; here, the *tool-shaped JSON* is not leaked, but the residual `<tool_call` token can confuse downstream consumers that assume `text` is clean of any tool markup. That’s a minor strictness / cleanliness regression against the intent.  
WHETHER ANOTHER LAYER CATCHES IT:  
Yes. `parse_message` correctly places the truncated JSON into `ambiguous`, so no tool intent is executed, and `run_turn` never treats `<tool_call` substrings in `text` as executable. There is no silent loss or unauthorized execution; this is a cosmetic/UX nit against the “strip it from prose” goal.  
FIX:  
In the `<tool_call>` removal pass, use the extracted `js` / `balanced` flag to ensure the *full* marker+JSON region is stripped, and only then perform the marker cleanup on the *remaining* segments. For example, record the span as `(m.start(), end)` (current) but also strip the literal `<tool_call` and `</tool_call>` only within those spans, not globally via `.replace`. Alternatively, after assembling `remaining`, normalize by removing a lone trailing `<tool_call` token only when there is no corresponding balanced JSON that follows, e.g.:
```python
remaining = "".join(keep)
# remove dangling marker not followed by '{' / '[' within a small lookahead
remaining = re.sub(r"<tool_call(?![^{\[]*.?[{\[])", "", remaining)
remaining = remaining.replace("</tool_call>", "").strip(" >/\t\r\n")
```


ID: F2 / TITLE: Whole-Content Batch Ambiguous While Embedded Batch With Good Element Still Loses Good Call / SEVERITY: LOW  
LOCATION: collaborator/toolcall.py:213–228  
CONCRETE INPUT OR BYPASS:  
```python
from collaborator.toolcall import parse_message

# Whole-content batch: surfaces as ambiguous (strictness preserved, improved over old behavior)
batch1 = '[{"name":"write_file","arguments":{"path":"a","content":"x"}}, {"not":"a call"}]'
r1 = parse_message({"content": batch1})
# r1.intents == ()
# r1.ambiguous == (batch1[:200],)

# Embedded mid-sentence batch: good element silently ignored (pre-existing behavior)
content = 'Note: [{"name":"write_file","arguments":{"path":"a","content":"x"}}, {"not":"a call"}] end.'
r2 = parse_message({"content": content})
# r2.intents == ()
# r2.ambiguous == ()
# r2.text contains entire JSON array
```  
WHY IT BREAKS A GUARANTEE:  
The change’s stated fix for “whole-content JSON ARRAY with one malformed element dropped the ENTIRE batch silently” is now correct *only* when the JSON array occupies the entire content. When that same batch appears in content mid-sentence, `_tool_call_tag_objects` doesn’t see it (no `<tool_call>` marker) and the whole-content branch is skipped because `intents` is non-empty only from structured or previous parsing; the embedded array is returned purely as `text`. That means a “good” individual call in a mixed batch embedded in prose is neither run nor surfaced as ambiguous — it’s silently treated as plain text. However, the original strictness design explicitly treats “tool-shaped JSON mid-sentence” as non-intent; the new guarantee (C1) is scoped to *tool-shaped things the model actually meant as calls* (i.e., structured, `<tool_call>`, or entire-content JSON). The shipped tests explicitly pin “json mid-prose is not run”, and they do not claim it must be surfaced. So this is a *non-goal* relative to the described fix, not a regression.  
WHETHER ANOTHER LAYER CATCHES IT:  
N/A. This is deliberate strictness: mid-sentence JSON is a mention, not an intent; governance never sees it.  
FIX:  
If you want to extend the “never silently drop” guarantee further, you could heuristically treat a mid-content JSON array that looks toolish (per `_looks_toolish`) as ambiguous, *without* running it. That must be done carefully to avoid reclassifying arbitrary examples or code snippets as tool calls. Currently the team chose not to; I would document that the non-silent-loss guarantee is scoped to structured, `<tool_call>`, and whole-content JSON only.


ID: F3 / TITLE: `_DEFAULT_CLIENT_MAX_TOKENS` Assumption Can Underestimate Real Cap But Is Safely Bounded / SEVERITY: LOW  
LOCATION: collaborator/loop.py:203–214  
CONCRETE INPUT OR BYPASS:  
```python
# A client with much larger default cap but no .max_tokens attr:
class WeirdClient:
    def complete(self, messages, tools=None, temperature=None, max_tokens=None):
        # ignores max_tokens and uses 131072 internally
        return {"content": "", "tool_calls": None, "finish_reason": "length"}

from collaborator.loop import _complete_actionable
msg, parsed, actionable = _complete_actionable(WeirdClient(), history=[], empty_retries=3)
# base_mt inferred as 16384 even though actual provider limit is 131072
```  
WHY IT BREAKS A GUARANTEE:  
The retry path uses `getattr(client, "max_tokens", _DEFAULT_CLIENT_MAX_TOKENS)` to infer the initial cap when the client doesn’t expose one. That is a guess: the actual backend may have a much larger limit. This can cause the “double until 65,536” logic to grow more slowly than “optimal” relative to the true provider cap. But the guarantee for C3 is that budget growth is *bounded* and that truncated turns are retried with a *larger* budget, not that we perfectly match provider headroom. The invariant `grown_mt = min(_TRUNC_BUDGET_CAP, (grown_mt or base_mt) * 2)` and `_TRUNC_BUDGET_CAP = 65536` are both satisfied; retry count is still bounded; there is no path to unbounded growth or under-governed actions. So this is a minor performance/efficiency nit, not a correctness hole.  
WHETHER ANOTHER LAYER CATCHES IT:  
Yes — governance/authority are completely independent of `max_tokens`. Underestimation merely leads to more retries or an eventual honest “empty/truncated” surface, not to authority changes or partial executions.  
FIX:  
Document explicitly that `_DEFAULT_CLIENT_MAX_TOKENS` is a conservative lower bound for unknown clients. If you want better tuning, let `OllamaClient` expose its effective cap and have the default depend on the specific client class; but do not relax the `min(_TRUNC_BUDGET_CAP, ...)` bound or the existing retry bound.


ID: F4 / TITLE: `finish_reason` Attached Client-Side Is Persisted in History But Never Re-Sent / SEVERITY: LOW  
LOCATION: collaborator/model_client.py:48–58; collaborator/loop.py:260–279  
CONCRETE INPUT OR BYPASS:  
```python
from collaborator.model_client import OllamaClient
from collaborator.loop import run_turn
# Imagine server returns choice.finish_reason="length" once
# OllamaClient injects msg["finish_reason"] into the message dict
# run_turn appends {"role":"assistant", "content": "..."} only
```  
WHY IT BREAKS A GUARANTEE:  
The claim for C4 is that attaching `finish_reason` to the returned dict cannot leak onto the wire. The wire payload is constructed in `OllamaClient.complete` from `messages` by only sending `{"role":..., "content":...}` and (optionally) `tools` — no `finish_reason`. The assistant turn that is appended to `history` in `run_turn` is also a new dict with only `role` and `content`. However, if a third-party host reuses the raw object returned from `complete` directly in its own history (bypassing `run_turn`), it might inadvertently send `finish_reason` back to the provider. That’s outside the Collaborator’s governed loop, but in this codebase, the history that is passed to `client.complete` is always built by `run_turn`, which strips everything except `role` and `content`. Within the scope of the reviewed system, the guarantee holds; the only nuance is that the client-side message struct *can* carry `finish_reason` fields, so external callers must respect the documented “loop builds its own assistant turns” contract to avoid leaking them.  
WHETHER ANOTHER LAYER CATCHES IT:  
Yes. `run_turn` never stores `finish_reason` into the history it later passes back into `client.complete`. ScriptedClient tests (e.g. `EmptyCompletionIsNotDone`) indirectly validate the shape of these history entries.  
FIX:  
Clarify in `OllamaClient`’s docstring that its `complete` return value is *not* suitable for re-use as a chat message on the wire and that only `{"role":..., "content":...}` (plus `tool_calls`) may be forwarded. If extreme defense-in-depth is desired, you could also set `msg.pop("finish_reason", None)` before a host ever serializes messages for the wire — but that should be done at the host/wrapper layer, not in this governed loop, to keep concerns separated.


ID: F5 / TITLE: Grown-Budget Truncation Retries Can Mix With Temperature Escalation But Stay Bounded and Non-Duplicating / SEVERITY: LOW  
LOCATION: collaborator/loop.py:203–237  
CONCRETE INPUT OR BYPASS:  
```python
from collaborator.loop import _complete_actionable, _RETRY_TEMP_MAX
from collaborator.model_client import ScriptedClient

TRUNC = {"content": "x", "tool_calls": None, "finish_reason": "length"}
# Then a non-truncated final answer:
FINAL = {"content": "done", "tool_calls": None, "finish_reason": "stop"}
client = ScriptedClient([TRUNC, FINAL])

msg, parsed, actionable = _complete_actionable(client, history=[], empty_retries=3)
# First call uses default temp, sees truncation -> grows budget, loops
# Second call uses raised temperature and larger max_tokens, sees FINAL and returns
```  
WHY IT BREAKS A GUARANTEE:  
Strictly speaking, this doesn’t break a guarantee; it *tests* C3’s edge case. The function treats truncation as orthogonal to emptiness: for each attempt, it possibly sets `temperature` and `max_tokens`, then calls `complete`, parses, and if `_is_truncated(msg)` is true it *always* retries, regardless of whether the message is currently actionable. This guarantees that we do not accept a *truncated* tool call or message as final, but it also means an “actionable but still flagged as length” response is retried. The final `return msg, parsed, _is_actionable(msg, parsed)` ensures that if the *last* attempt is actionable-but-still-truncated (e.g., provider keeps setting `finish_reason="length"` even once the content is short), it’s accepted and surfaced, not silently dropped. There’s no path where a partial call is executed: truncated `<tool_call>`s are parsed into `ambiguous`, never `intents`; and partial “content only” truncations just shorten prose. For duplicate execution: we only call `govern_action` once per `run_turn` iteration, after `_complete_actionable` returns; no intent from a truncated attempt is ever passed to governance. Retry count is `max(1, empty_retries+1)`, and the budget is bounded by `_TRUNC_BUDGET_CAP`. So C3’s “no partial/duplicate run + bounded” guarantee is in fact met; this is a subtle area worth confirming.  
WHETHER ANOTHER LAYER CATCHES IT:  
Yes — the parser is the layer that guarantees truncated calls become `ambiguous` only; governance never runs them. Even if the provider mislabels `finish_reason`, `_is_actionable` and `parse_message` ensure that only complete, strict calls are turned into `ToolIntent`s.  
FIX:  
No functional change required. If you wanted to make this more transparent, you could log or surface when the system accepts an “actionable-but-still-flagged-length” result at the end of the retry budget, but that’s not necessary for correctness.


ID: F6 / TITLE: `_tool_call_tag_objects` Correctly Handles Adjacent Valid and Truncated Calls Without Dropping Either / SEVERITY: NONE (Non-Finding)  
LOCATION: collaborator/toolcall.py:62–80; 175–196  
CONCRETE INPUT OR BYPASS:  
```python
from collaborator.toolcall import parse_message

content = (
  '<tool_call>{"name":"write_file","arguments":{"path":"a","content":"x"}}</tool_call>'
  '<tool_call>{"name":"write_file","arguments":{"path":"b","content":"y"}</tool_call>'
)  # second call missing closing brace/quote/marker

r = parse_message({"content": content})
# r.intents[0].args["path"] == "a"
# len(r.ambiguous) == 1  (the truncated second call)
# "b" not in r.text
```  
WHY IT BREAKS A GUARANTEE:  
It doesn’t. This is precisely one of the C1/C3 edge patterns that could have gone wrong: a balanced first `<tool_call>` followed by a truncated second in the same content string. `_tool_call_tag_objects` creates two hits: the first with `(balanced=True)`, the second with `(balanced=False, end=len(content))`. `parse_message` runs the balanced first through `_coerce_call` and records it as a `ToolIntent`; the unbalanced second is appended to `ambiguous` and excluded from the strip pass (no `intents` are generated for it). The text reconstruction loop splices out both spans correctly, and then strips the marker tokens. There is no path where either call vanishes or partially runs.  
WHETHER ANOTHER LAYER CATCHES IT:  
N/A – this is a positive confirmation that the parser layer itself correctly enforces the guarantee; governance remains untouched.  
FIX:  
None needed; this is evidence that the unbalanced-span handling behaves as claimed in a mixed-validity scenario.


ID: F7 / TITLE: Grown-Budget Retry Does Not Change Governance Authority / SEVERITY: NONE (Non-Finding)  
LOCATION: collaborator/loop.py:203–237; collaborator/g governance.py (unchanged)  
CONCRETE INPUT OR BYPASS:  
```python
from collaborator.loop import run_turn
from collaborator.model_client import ScriptedClient
from collaborator.session import Session

TRUNC = {"content": "", "tool_calls": None, "finish_reason": "length"}
CALL = {"content": None, "tool_calls":[{"id":"1","function":{"name":"write_file","arguments":"{\"path\":\"a\",\"content\":\"x\"}"}}]}
client = ScriptedClient([TRUNC, CALL, {"content": "done"}])

with tempfile.TemporaryDirectory() as tmp:
    s = Session(workspace=tmp)
    r = run_turn(s, client, "write a")
# exactly one Decision, governed once
```  
WHY IT BREAKS A GUARANTEE:  
It doesn’t. For a grown-budget retry, the only differences between attempts are `temperature` and `max_tokens` kwargs passed into `client.complete`. `parse_message` operates solely on the returned message, and `govern_action` does not know or care about `temperature`/`max_tokens` or `finish_reason`. When the second attempt returns `CALL`, it is parsed into a `ToolIntent` and passed once into `govern_action`. There is exactly one decision with a fresh `action_id`. The seam neither sees nor depends on retry parameters.  
WHETHER ANOTHER LAYER CATCHES IT:  
N/A – this confirms C2: governance is structurally decoupled from sampling/budget knobs.  
FIX:  
None; the architecture here is sound.


ID: F8 / TITLE: Ambiguous Items Are Never Run, Including After Grown-Budget Retries / SEVERITY: NONE (Non-Finding)  
LOCATION: collaborator/loop.py:258–318; collaborator/toolcall.py:175–228  
CONCRETE INPUT OR BYPASS:  
```python
from collaborator.loop import run_turn
from collaborator.model_client import ScriptedClient
from collaborator.session import Session

clipped = {"content": '<tool_call>{"name":"write_file","arguments":{"path":"a","content":"aaaa',
           "tool_calls": None, "finish_reason": "length"}

client = ScriptedClient([clipped, clipped, clipped, clipped])
with tempfile.TemporaryDirectory() as tmp:
    s = Session(workspace=tmp)
    r = run_turn(s, client, "write huge", empty_retries=3)

# r.decisions == []
# r.ambiguous non-empty
```  
WHY IT BREAKS A GUARANTEE:  
It doesn’t. This is the persistent truncation case: *every* completion is truncated, and each contains an unbalanced `<tool_call>`. `_complete_actionable` treats every attempt as truncated, doubling `max_tokens` until it hits `_TRUNC_BUDGET_CAP`; when attempts are exhausted it returns the last parsed result with `actionable = _is_actionable(msg, parsed)`. Because truncated `<tool_call>`s are parsed into `ambiguous` and removed from `text`, `_is_actionable` is `True` on the final attempt, so `run_turn` does not short-circuit as “empty”; instead, it records `parsed.ambiguous` and exits via the “no intents” branch as a `STOPPED_FINAL` turn with surfaced ambiguous. Nothing is sent to `govern_action`, so no tool runs; the clipped call is surfaced, not lost.  
WHETHER ANOTHER LAYER CATCHES IT:  
The parser enforces strict non-execution for truncated calls; the loop just adds the retry + surfacing behavior.  
FIX:  
None; this behavior matches the C1/C3 design.


ID: F9 / TITLE: ScriptedClient and Tests Properly Handle New `max_tokens`/`finish_reason` Fields / SEVERITY: NONE (Non-Finding)  
LOCATION: collaborator/model_client.py:59–75; tests/test_collaborator_loop.py (TruncatedTurnGrowsBudgetAndRetries)  
CONCRETE INPUT OR BYPASS:  
The provided tests `TruncatedTurnGrowsBudgetAndRetries.test_truncation_retries_with_a_larger_budget_then_acts` and `.test_persistent_truncation_surfaces_ambiguous_never_silently_lost` exercise ScriptedClient’s new `max_tokens_seen` tracking and the truncation path. ScriptedClient’s `complete` signature matches `OllamaClient.complete` (adds `max_tokens` kwarg) and simply records it; tests assert that the first attempt sees `None` and a subsequent truncation retry sees a larger, explicit cap. The `finish_reason` is only looked at in `_is_truncated`; ScriptedClient does not need to add or use it, it just passes through any dicts from the queue.  
WHY IT BREAKS A GUARANTEE:  
It does not; this confirms C4’s interface-compatibility claim. No resume-path, propose.py, or ScriptedClient callsites are broken by the added kwarg or attached `finish_reason`, and the tests pin the essential behavior.  
WHETHER ANOTHER LAYER CATCHES IT:  
N/A – this is explicit test coverage.  
FIX:  
None needed.


---

CERTIFICATION LINES

C1 (no silent loss of tool-shaped things): CERTIFIED  
All structured tool calls, `<tool_call>` blocks (balanced or unbalanced), and whole-content JSON objects/arrays that look toolish are either executed as `intents` or surfaced in `ambiguous`; truncated calls are never run and never vanish. Edge cases like “valid call followed by truncated call”, large batches, and persistent truncation are handled as claimed.

C2 (governance untouched by retries / surfacing): CERTIFIED  
The grown max_tokens and raised temperature are strictly sampling parameters passed to `client.complete`; `parse_message` and `govern_action` logic are unchanged and do not inspect `finish_reason`, `temperature`, or `max_tokens`, so retried completions flow through the same default-deny seam with byte-identical authority decisions.

C3 (no partial/duplicate run, bounded retries/budget, strictness preserved): CERTIFIED  
Truncated `<tool_call>` spans are always classified as `ambiguous`, never as `intents`; `_complete_actionable` never passes intents from a truncated attempt into `govern_action`, and returns at most one actionable result per turn. Retry count is `max(1, empty_retries+1)` and budget growth is `min(_TRUNC_BUDGET_CAP, ...)`, ensuring bounded behavior; fully-valid batches still run all calls, and mixed whole-content batches are surfaced as ambiguous without partial execution.

C4 (no regression / interface break): CERTIFIED  
The new `max_tokens` kwarg and client-side `finish_reason` attachment integrate cleanly with `OllamaClient`, `ScriptedClient`, tests, and the governed loop; `run_turn` builds fresh assistant turns (role + content only) so `finish_reason` never leaks to the wire, and `_tool_call_tag_objects`’ unbalanced-span handling correctly isolates valid calls and strips tool-shaped regions from prose without mis-spanning or dropping runnable intents.


STEELMAN (2–3 sentences)

The reliability fix is narrowly scoped and carefully designed: it addresses the exact failure modes (truncated/unbalanced `<tool_call>` blocks and mixed whole-content batches) by enriching parse-time classification and by adding a bounded, orthogonal retry mechanism keyed on `finish_reason`. Throughout, it maintains strict separation between sampling/budget controls and the governance seam; no new authority channels or execution paths are introduced, and ambiguous tool-shaped blobs remain non-executable. The test suite meaningfully exercises the new behavior, including multi-call batches and truncation retries, making silent loss or inadvertent execution of malformed calls very unlikely.

VERDICT: SOUND  
The shipped change meets its stated guarantees (C1–C4) without introducing governance regressions; the single highest-value improvement would be a small tightening of text reconstruction around unbalanced `<tool_call>` spans to ensure all residual marker text is cleanly removed from `ParseResult.text` while preserving the current strict/non-executing semantics.
