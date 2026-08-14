# Seam feel code panel: openai/gpt-5.1

_cost=$0.1026 finish=stop_

ID: C1-1 / TITLE: Conversation history threaded into `run_turn` / SEVERITY: LOW  
LOCATION: collaborator/host.py:321-323; collaborator/loop.py:253-273  

CONCRETE SEQUENCE:  
1. A hostile model, during turn N, emits arbitrary tool calls and text that end up in `result.history`.  
2. `_absorb_result` stores `self._history = result.history`.  
3. On turn N+1, `_handle_turn` calls `run_turn(..., history=self._history)`.  
4. `run_turn` re-asserts its own system message at history[0] and continues.  

WHICH CLAIM IT BREAKS: None. It does *not* influence authority; it only changes what the model sees.  

AUTHORITY ANALYSIS:  
- `history` is passed only into `run_turn`, never into governance or Host controls.  
- `run_turn` immediately overwrites or prepends history[0] with a host-authored system message (`sal_system_prompt() + _now_line()`), so any system-message poisoning is overwritten.  
- All actual authority decisions still derive solely from `govern_action` / `approve` and the signed caps; no branch in governance or host checks `history`, `target`, or clock.  

OTHER LAYERS: `govern_action` remains the sole gate; all tool invocations from `run_turn` are handled as before.  

FIX: None needed for authority. As a hardening nit, you could explicitly type-check `self._history` as a list of `{role, content}` dicts before passing it to `run_turn`, but this is not required for P‑01.  


ID: C1-2 / TITLE: Workspace-grounding context for proposer / SEVERITY: LOW  
LOCATION: collaborator/propose.py:287-309  

CONCRETE SEQUENCE:  
1. A session is created with `workspace` pointing at an attacker-controlled directory name, e.g. containing unusual characters.  
2. `build_proposer_context` lists up to 60 entries and embeds them as a fenced “workspace” block.  
3. The proposer model now “knows” real file names and paths and may propose actions using them.  
4. All proposed actions still go through `govern_action` and are merely *more likely to be real*.  

WHICH CLAIM IT BREAKS: None.  

AUTHORITY ANALYSIS:  
- Only filenames (plus a static explanatory wrapper string) are included.  
- The proposer’s output is turned into a `ToolIntent` and then passed through unchanged `govern_action`.  
- No new flags, leashes, caps, or emission paths are derived from filenames or from this context.  

OTHER LAYERS: `govern_action` already enforces capability, workspace resolution, and controlled-locations.  

FIX: None needed. The docstring already clearly states this is knowledge-only.  


ID: C1-3 / TITLE: Decision/proposal `target` field added to view & surface / SEVERITY: LOW  
LOCATION: collaborator/view.py:27-42, 112-134; collaborator/surface.py:597-606, 620-629  

CONCRETE SEQUENCE:  
1. A hostile tool argument is something like `{"path": "/etc/passwd; rm -rf /"}`, or a URL containing query data.  
2. `_action_target` renders this to a short string (truncated to 200 chars).  
3. `JudgmentView._decision/_proposal` include this `target` string in the snapshot.  
4. The surface’s `decisionLi` / `proposalLi` render it into a `<span class="prop-target">` via `el(..., text)`.  

WHICH CLAIM IT BREAKS: None.  

AUTHORITY ANALYSIS:  
- `target` is computed and used only for display in the view and surface.  
- No host method or governance path reads `target` back as input or uses it to decide leash/cap/autonomy/emission.  
- Any tool run is still triggered only by the original decision/proposal, via Host controls (`approve`, `approve_proposal`), which do not look at `target`.  

OTHER LAYERS: The view remains read-only; the surface’s controls still use fixed IDs and not `target`.  

FIX: None needed. The helper is properly display-only.  


ID: C2-1 / TITLE: Conversation-thread + optimistic echo render — XSS surface check / SEVERITY: LOW  
LOCATION: collaborator/surface.py:534-554, 606-710 (HTML + JS), particularly `el`, `turn`, `salContent`, `renderThread`, `decisionLi`, `proposalLi`  

CONCRETE SEQUENCE (adversarial attempt):  
1. Model returns a reply containing HTML/JS, e.g. `"</div><script>alert(1)</script>"`.  
2. This shows up in `Task.reply` and in snapshot `tasks[].reply`.  
3. The JS side calls `renderThread(s.tasks || [])`; each task becomes:  
   - `turn("You", "you", { text: t.prompt || "" })` and  
   - `turn("Sal", "sal", salContent(t))`.  
4. `turn` uses `el("div", "bubble"...)` and then either `b.appendChild(content.node)` or `b.appendChild(document.createTextNode(...))`.  
5. `el` itself only ever uses `n.textContent = text`.  

WHICH CLAIM IT BREAKS: None.  

XSS ANALYSIS:  
- All user/model/workspace-derived strings (prompt, reply, held summaries, targets, rationale, tools) go through `el` which assigns to `.textContent`, or via `document.createTextNode`. No `innerHTML`, `insertAdjacentHTML`, `eval`, `Function`, or template interpolation from untrusted strings into attributes or URLs.  
- CSS `className` is only set from static literal values (`"turn sal"`, `"prop-target"`, etc.).  
- The single templated JS injection (`const CSRF = "{csrf}"`) is server-side and host-generated, not model-controlled.  

OTHER LAYERS: CSP is strict (`default-src 'none'; script-src/style-src by nonce`), which would additionally prevent injected scripts even if there were an HTML sink.  

FIX: None needed. The textContent discipline is correctly maintained.  


ID: C3-1 / TITLE: Unbounded growth of `self._history` across turns / SEVERITY: MEDIUM  
LOCATION: collaborator/host.py:152-160, 302-307, 407-416; collaborator/loop.py:253-273  

CONCRETE SEQUENCE:  
1. Operator engages in a long conversation: say 1000 turns with long replies (each 16k chars).  
2. After each turn, `_absorb_result` sets `self._history = result.history`, which includes the entire conversation plus system prompt lines and all tool-result messages.  
3. Each subsequent `run_turn` is called with the full `self._history`, and appends another user + assistant + tool-result set of messages.  
4. Memory usage grows roughly O(turns × (avg prompt+reply+tool-results)), and context length can exceed the model’s effective window, causing truncation at the model backend, degraded behavior, and possible perf/DoS on the single worker process.  

WHICH CLAIM IT BREAKS: None of C1/C2/C4, but it does hit C3’s “unbounded growth” concern.  

MEMORY / CORRECTNESS ANALYSIS:  
- Single-Host, single worker means no cross-principal leak, but history is truly unbounded in this implementation.  
- A FAILED / HELD / CANCELLED / PAUSED turn does not corrupt history: `_absorb_result` is always called with the `turn`’s `result.history`, and `run_turn` reasserts system at index 0, so the sequence remains coherent.  
- However, for a long-lived session, the worker could become slow or memory constrained, and the model may repeatedly be called with history far beyond its context window (leading to unpredictable truncation at the model layer).  

OTHER LAYERS: The model client may have internal token limits, but that just results in more truncation/EMPTY responses; it does not bound Host memory.  

FIX:  
- Implement a configurable history window in `Collaborator` before calling `run_turn` — e.g., keep the system message + last N messages (tunable, perhaps by token-estimate, not count).  
- Optionally provide a `reset_conversation()` control that clears `self._history` when the operator decides to start fresh.  
- This should be treated as an engineering/DoS concern; it is not a governance-bypass.  


ID: C3-2 / TITLE: History threading across resume / held paths / SEVERITY: LOW  
LOCATION: collaborator/host.py:362-406  

CONCRETE SEQUENCE:  
1. A task is HELD with some history H. `_absorb_result` stores `task.history = result.history` and `self._history = result.history`.  
2. Operator approves; `_handle_resume` copies `history = task.history` and passes it back to `run_turn` together with a host-authored `note`.  
3. `run_turn` reasserts Sal’s system message at history[0] and appends the `note` and new messages.  

WHICH CLAIM IT BREAKS: None.  

MEMORY / SAFETY ANALYSIS:  
- The host-authored `note` is just another user message. No system grounding is lost because of the explicit overwrite/insert of the system message each resume.  
- HELD/PAUSED/FAILED transitions do not create cross-task pollution; `self._history` is updated only from `result.history` of the active task, and tasks are single-owner in this Host.  

OTHER LAYERS: Serial worker and per-host-session architecture avoid cross-principal bleed.  

FIX: None needed for correctness, aside from the general history window fix above.  


ID: C4-1 / TITLE: Workspace-grounding surface and sensitive filenames / SEVERITY: LOW  
LOCATION: collaborator/propose.py:287-309; collaborator/view.py:27-42; collaborator/surface.py (display)  

CONCRETE SEQUENCE (adversarial attempt):  
1. The operator’s workspace contains a file with a sensitive-looking name, e.g. `aws-secret-access-key.txt` or a private URL in a filename.  
2. `build_proposer_context` includes that file name in the fenced "workspace" block sent to the proposer model.  
3. The proposer may then suggest actions that mention this filename; these, if run, appear as `target` in the view and surface.  

WHICH CLAIM IT BREAKS: None.  

LEAK ANALYSIS:  
- This is all strictly within the same operator’s localhost session. There is no new egress: workspace content or filenames go only from disk → in-process model → local view JSON → local browser.  
- The proposer context includes only filenames and a note that the workspace may be empty; it never reads or sends file content.  
- `target` similarly echoes only arguments the tools already carried and that were already visible to the agent/session; it doesn’t add new locations or export them off-host.  

OTHER LAYERS: Outbound network/egress channels (net_post, web_fetch) remain governed and unchanged; no new calls are triggered by this context.  

FIX: None needed. For paranoia, you could document that workspace filenames may be visible to the model and the operator in these views, but that’s inherent in using this workspace model.  


ID: C4-2 / TITLE: Clock grounding line in system prompt / SEVERITY: LOW  
LOCATION: collaborator/loop.py:118-130, 253-264  

CONCRETE SEQUENCE:  
1. At each turn, `_now_line()` computes a localized timestamp and time zone (e.g. “The current date and time is Thursday, 14 August 2026, 15:23 (PDT).”).  
2. `sal_system_prompt() + _now_line()` is sent as the system message to the model.  
3. The clock value is never surfaced in snapshot or logs to any third party.  

WHICH CLAIM IT BREAKS: None.  

LEAK & HONESTY ANALYSIS:  
- This is host-local time; giving it to the model is not a new egress path.  
- It does not influence capabilities, leashes, or emissions.  
- It is truthful grounding; it does not misrepresent governance state.  

OTHER LAYERS: None needed.  

FIX: None needed.  


ID: C4-3 / TITLE: `target` might embed credential-shaped strings / SEVERITY: LOW  
LOCATION: collaborator/view.py:27-42, 112-134; collaborator/surface.py:597-606, 620-629  

CONCRETE SEQUENCE:  
1. A user (or model) constructs a command or URL argument that includes something that *looks* like a credential, e.g. `https://user:password@example.com/path` or `--token=abcd1234`.  
2. `_action_target` picks `url` or `command` and truncates to 200 characters; this is stored as `target` and rendered in `prop-target`.  

WHICH CLAIM IT BREAKS: None.  

LEAK ANALYSIS:  
- Any such value is already present in the governed intent arguments and is visible to the operator via decision summaries, logs, and the task record.  
- `target` does not send it off the machine; it only surfaces within the operator’s auth-walled view.  
- Given the stated threat model (single-user localhost), exposing credential-shaped strings within the UI to the same user is acceptable.  

OTHER LAYERS: CSP / no-referrer / no external calls ensure this remains on localhost.  

FIX: Optional: if you want a stricter UX, you could mask obvious userinfo portions in URLs for `target` (e.g. strip `user:pass@`), but that’s not required for the stated guarantees.  


---

CERTIFICATION LINES

C1 (Authority separation / P‑01): CERTIFIED  
None of the six feel changes introduce any new path from model/workspace/UI data into authority decisions. All new data flows (history threading, `target`, workspace grounding, clock) affect only what the model knows or what the operator sees; the sole authority boundary remains `govern_action` and `approve`, unchanged.

C2 (Render safety / no XSS): CERTIFIED  
The new conversation UI and `target` displays strictly use `textContent` / `createTextNode` for all untrusted strings; there is no `innerHTML`, attribute injection, or dynamic script construction, and CSP remains strict.

C3 (Memory-threading soundness): NOT-CERTIFIED  
Threading of `self._history` is principal-safe and correctly re-asserts the system message on each turn/resume, but history is unbounded and can grow without limit, posing a realistic long-session DoS/correctness concern that should be addressed with trimming.

C4 (Context surfacing / no new leak or misrepresentation): CERTIFIED  
Workspace grounding, `target`, and the clock line only add context inside the same operator’s localhost session, do not add egress, do not surface raw secret contents beyond existing paths, and do not misrepresent governance state.

---

STEELMAN

From a safety standpoint, this PR is very conservative: it confines all “feel” changes to display and model-grounding paths, keeps authority logic untouched, and carefully uses defensive patterns (fenced/neutralized proposer context, fixed control allowlist, strict CSP, text-only rendering). The only substantive concern is operational: unbounded conversation history may, over long sessions, impair performance or effective behavior, but it does not open new cross-principal or authority channels. Given the single-user localhost design and unchanged governance seam, the PR preserves the core guarantees while notably improving usability.

VERDICT: MINOR_ISSUES  
The highest-value fix is to bound `self._history` by trimming older messages (ideally via a token-budgeted window) before each `run_turn`, so long-lived conversations cannot degrade model behavior or resource usage while still maintaining the P‑01 and render guarantees.
