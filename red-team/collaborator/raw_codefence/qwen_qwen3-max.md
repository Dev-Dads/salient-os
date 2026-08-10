# PR#33 codefence certification panel: qwen/qwen3-max

_finish=stop seconds=20.1 cost=$0.0676 usage={'prompt_tokens': 38924, 'completion_tokens': 885, 'total_tokens': 39809, 'cost': 0.06762444, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.06762444, 'upstream_inference_prompt_cost': 0.06072144, 'upstream_inference_completions_cost': 0.006903}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

## FINDINGS

### ID-01 / Workspace disjointness bypass via empty PROTECTED_ROOTS / CRITICAL / collaborator/codefence.py:58 / `PROTECTED_ROOTS = ()` (e.g., if both __file__ and salienceos.__file__ are None/unresolvable) / The disjoint_from_code guard becomes a no-op, allowing workspace=collaborator/ — defeating the write_file/read_file fence that assumes code lives outside workspace / NO — governance.py's proposer code-deny and autonomy floor still block autonomous run_command self-rewrite, but write_file could now rewrite code autonomously / Fix: If PROTECTED_ROOTS is empty, raise ImportError at import time — "cannot locate Collaborator's own code; structural protection impossible"

### ID-02 / Autonomy floor bypass via leash aliasing / HIGH / collaborator/governance.py:394 / A run_command with leash="act-then-report" (hyphen instead of underscore) slips past the autonomy floor check (`leash == ACT_THEN_REPORT`), then fails open to HELD but with original leash string, which later gets capped by apply_cap to PROPOSE_FIRST — BUT if a signed policy cap explicitly grants "act-then-report" (with hyphen), apply_cap returns it verbatim (since it's not in _LEASH_RANK), and execute_and_verify's allowlist dispatch runs it because `leash == ACT_THEN_REPORT` is False but the unknown string isn't caught until the final else branch — wait no, the allowlist dispatch only runs on explicit ACT_THEN_REPORT. Actually, an unknown leash always goes to HELD. However, Session construction validates leash_overrides, and policycaps minting validates leash_caps, so this path is closed. / NO REAL BYPASS — all entry paths normalize or validate leashes / NOT A FINDING

### ID-03 / names_code_root false negative on Windows paths / MEDIUM / collaborator/codefence.py:107 / On Windows, a command containing backslashes like ["echo", r"C:\path\to\collaborator\governance.py"] becomes "C:/path/to/collaborator/governance.py" after hay = text.replace("\\", "/"), but PROTECTED_ROOTS[0].as_posix() might be "/c/path/to/collaborator" (if using WSL-style paths) or "C:/path/to/collaborator" — if the latter, it matches; if the former, it doesn't. But Path.resolve() on Windows returns a native path, and as_posix() converts it to forward slashes, so it should match. / UNLIKELY TO BREAK GUARANTEE — the autonomy floor doesn't rely on names_code_root; it's only for proposer hard-deny and approve re-deny, which are DiD / NO FIX NEEDED

### ID-04 / Path traversal via relative workspace / LOW / collaborator/session.py:55 / If workspace="../../../collaborator", Path(workspace).resolve() in Session.__init__ resolves it to the absolute path, which disjoint_from_code then correctly rejects if it overlaps PROTECTED_ROOTS / CAUGHT BY disjoint_from_code / NOT A FINDING

## CERTIFICATION

**CERTIFIED**: "With code_protection_available()==False, an AUTONOMOUS run_command (one that RANs/executes without a human hand) is IMPOSSIBLE — every act_then_report run_command is floored to propose_first (HELD) BEFORE the auto-run dispatch, on EVERY platform."

## STEELMAN

The PR correctly implements a defense-in-depth approach: the workspace disjointness guard prevents fenced tools from reaching code, the proposer hard-deny blocks obvious self-rewrites, and most critically, the autonomy-withheld floor ensures no run_command can auto-execute while code protection is unavailable. The approve() re-deny provides moment-of-use protection against mutation attacks. All paths to autonomous execution are properly gated.

## VERDICT

**MINOR_ISSUES**: The empty PROTECTED_ROOTS case could theoretically disable the workspace guard, though other controls remain. Highest-value fix: Raise ImportError during codefence module import if PROTECTED_ROOTS is empty, ensuring the structural assumption is never violated.
