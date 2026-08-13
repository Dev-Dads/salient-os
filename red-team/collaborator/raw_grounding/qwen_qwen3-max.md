# Grounding panel: qwen/qwen3-max

_cost=$0.0705 finish=stop_

## FINDINGS

### F1 / TOOL MANIFEST SENTINEL CORRUPTION VIA TOOL HINT
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/loop.py:98` (`sal_system_prompt`)  
**CONCRETE INPUT OR BYPASS**: If a tool hint in `_MODEL_FACING` contained the literal string `"__TOOL_MANIFEST__"`, the `.replace()` would splice the manifest into itself, corrupting the prompt. Example malicious entry:
```python
{"name": "evil", "hint": '{"path": "__TOOL_MANIFEST__"}'}
```
**WHY IT BREAKS A GUARANTEE**: Breaks C2 (single source of truth) by allowing the manifest to be corrupted during splicing, potentially causing parsing errors or misleading the model.  
**WHETHER ANOTHER LAYER CATCHES IT**: No — the parser and governance don't validate prompt integrity. However, this is currently theoretical since all hints are controlled and don't contain the sentinel.  
**FIX**: Use a more unique sentinel (e.g., `__TOOL_MANIFEST_7F3A9B1C__`) or escape/validate hints during manifest generation.

### F2 / DOUBLE-PREPEND POSSIBLE VIA MALICIOUS HISTORY INJECTION
**SEVERITY**: LOW  
**LOCATION**: `collaborator/loop.py:130–133` (`run_turn`)  
**CONCRETE INPUT OR BYPASS**: If an external caller passes a history containing a system message with `role=="system"` but content not equal to `sal_system_prompt()`, the loop will not prepend the correct prompt. Worse, if the history contains a non-dict system "message" (e.g., `{"role": "system", "content": 123}`), the `isinstance(m, dict)` check passes but `m.get("role")` works, yet the content is invalid.  
**WHY IT BREAKS A GUARANTEE**: Violates C4 (loop integrity) by allowing degraded or incorrect grounding if the resume path is fed untrusted history.  
**WHY ANOTHER LAYER CATCHES IT**: The model may misbehave, but `govern_action` still enforces authority (C1 holds). The system won’t double-prepend, but it might under-prepend.  
**FIX**: Validate that any existing system message matches `sal_system_prompt()` exactly, or reject/resync history.

### F3 / TOOL NAME DRIFT BETWEEN _MODEL_FACING AND _TOOLS
**SEVERITY**: MEDIUM  
**LOCATION**: `collaborator/tools.py:278–337` (`_MODEL_FACING` vs `_TOOLS`)  
**CONCRETE INPUT OR BYPASS**: `_MODEL_FACING` lists `read_file`, `write_file`, `run_command`, `web_fetch`. `_TOOLS` defines these plus `net_post`, `maint_fetch`. If a developer adds a tool to `_TOOLS` but forgets `_MODEL_FACING`, or vice versa, drift occurs. While tests check existence (`test_every_advertised_tool_actually_exists`), they don’t ensure **non-advertised tools can’t be called** beyond governance.  
**WHY IT BREAKS A GUARANTEE**: Weakens C2 — the “single source” claim assumes perfect maintenance discipline. A missing entry in `_MODEL_FACING` doesn’t break execution but could mislead about callable tools.  
**WHETHER ANOTHER LAYER CATCHES IT**: Yes — `govern_action` checks `get_tool(intent.name)` and denies unknown tools (`UNKNOWN_TOOL` status). So authority is safe (C1 holds).  
**FIX**: Add a test that ensures `_TOOLS.keys() - {"net_post", "maint_fetch"} == {t["name"] for t in _MODEL_FACING}`.

---

## CERTIFICATION

- **C1 (no new authority)**: **CERTIFIED** — All parsed intents flow through `govern_action`; advertising tools does not bypass capability gates, workspace fences, or leash rules. Model-named tools (even unadvertised) are fully governed.
- **C2 (single source of truth)**: **NOT-CERTIFIED** — While `_MODEL_FACING` drives both outputs, there’s no runtime or compile-time guarantee that `_MODEL_FACING` stays in sync with `_TOOLS` or that hints avoid the sentinel. Tests reduce risk but don’t eliminate drift or corruption vectors.
- **C3 (injection fence + prompt integrity)**: **CERTIFIED** — The system prompt is purely host-authored; no untrusted input reaches it. The data fence is a soft prompt rule, but the hard backstop (governed actions) remains intact.
- **C4 (loop integrity / no regression)**: **CERTIFIED** — The idempotent prepend prevents double-splicing; `tools=openai_tools()` doesn’t break `ScriptedClient` (tested); termination correctly requires no tool calls (parser and prompt agree).

---

## STEELMAN

The change significantly improves model reliability by grounding tool use without compromising security. The separation of "what the model is told" (prompt/schema) from "what is allowed" (governance seam) is clean and well-defended. The single-source design for tool manifests reduces drift risk, and extensive red-teaming controls (seals, re-gating, containment) remain untouched.

## VERDICT

**MINOR_ISSUES** — The core invariant (authority unchanged) holds strongly, but maintainability risks in C2 (sentinel collision, tool list drift) could lead to future bugs.  

**Highest-value fix**: Enforce bidirectional consistency between `_MODEL_FACING` and `_TOOLS` via a unit test that asserts the set of model-facing tools equals the set of non-operator-directed tools, preventing silent drift.
