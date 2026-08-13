# Grounding panel: anthropic/claude-opus-4.1

_cost=$0.7514 finish=stop_

## SECURITY REVIEW FINDINGS

### Finding 1
**ID:** F1  
**TITLE:** Model can inject system prompt via tool output containing `__TOOL_MANIFEST__`  
**SEVERITY:** HIGH  
**LOCATION:** collaborator/loop.py:104-106  
**CONCRETE INPUT:** A malicious web_fetch returning `__TOOL_MANIFEST__` in its body, or a read_file of content containing the sentinel sentinel  
**WHY IT BREAKS A GUARANTEE:** Violates C2 (no double-splice/corruption) and C3 (untrusted input cannot rewrite prompt). The `.replace()` is unbounded - if tool output contains `__TOOL_MANIFEST__`, a second call to `sal_system_prompt()` would replace it with the manifest again, corrupting the prompt.  
**WHETHER ANOTHER LAYER CATCHES IT:** No - the prompt is generated once per turn and tool outputs flow back unchanged  
### Finding 2
**ID:** F2  
**TITLE:** Tool manifest hint contains unescaped JSON that could break prompt structure  
**SEVERITY:** MEDIUM  
**LOCATION:** collaborator/tools.py:314 (hint field in _MODEL_FACING)  
**CONCRETE INPUT:** If a future tool's hint contained literal `{}` braces or the sentinel `__TOOL_MANIFEST__`, the splice could corrupt  
**WHY IT BREAKS A GUARANTEE:** Violates C2 - the hint field is spliced raw into a prompt full of JSON examples. While current hints are safe, there's no validation preventing future corruption.  
**WHETHER ANOTHER LAYER CATCHES IT:** No direct validation, though current hints are hardcoded safe  
**FIX:** Escape or validate hint content before splice

### Finding 3  

### CERTIFICATION

**C1 (no new authority):** CERTIFIED - The system prompt and tools schema are purely informational. Every action still flows through govern_action unchanged. The grounding test explicitly proves a grounded model asking for shell.exec is still DENIED.

**C2 (single source of truth):** NOT-CERTIFIED - While _MODEL_FACING successfully drives both tool_manifest() and openai_tools() from one source, the `__TOOL_MANIFEST__` sentinel replacement is vulnerable to corruption if the sentinel appears in tool output (Finding F1).

**C3 (injection fence + prompt integrity):** NOT-CERTIFIED - The prompt template is static and the manifest is host-generated, but the unbounded `.replace()` means tool output containing the sentinel could cause corruption on subsequent prompt generations (Finding F1).

**C4 (loop integrity / no regression):** CERTIFIED - The idempotent prepend check correctly prevents double-prepending, role=="system" can only be set by the host, and the "no <tool_call> in final answer" termination rule matches parse_message's logic.

## STEELMAN

The change successfully grounds the model with consistent tool specification from a single source (_MODEL_FACING), maintaining the critical property that advertising tools tool grants no new authority - govern_action remains the sole gate. The idempotent system message prepend and proper role isolation prevent most injection attempts. The tests prove grounding doesn't bypass the capability checks.

## VERDICT

**MINOR_ISSUES** - The grounding achieves its core goal of improving model emission quality without preserving the authority boundary, but the unbounded `__TOOL_MANIFEST__` replacement creates a corruption path. **Single highest-value fix:** Make sal_system_prompt() cache its result after first generation, or use a sentinel that cannot appear in tool output (e.g., a UUID generated at module start).
