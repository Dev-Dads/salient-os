# ungrantable-namespace CODE panel: qwen/qwen3-max

_finish=stop seconds=38.7 cost=$0.0170 usage={'prompt_tokens': 13466, 'completion_tokens': 1662, 'total_tokens': 15128, 'cost': 0.01698528, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.01698528, 'upstream_inference_prompt_cost': 0.01050348, 'upstream_inference_completions_cost': 0.0064818}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

**FINDINGS**

---

**ID**: F-01  
**TITLE**: No bypass of `offense:` prohibition via normalization or prefix confusion  
**SEVERITY**: LOW (non-finding; documented robustness confirmed)  
**LOCATION**: `salienceos/interpreter/policy.py:31–34` (`is_ungrantable_capability`)  
**CONCRETE INPUT**: `"ｏｆｆｅｎｓｅ：x"` (full-width Unicode), `"OFFENSE :x"` (space after casefolded prefix), `"offensexyz:"`, `"x-offense:y"`, `"not.offense:x"`  
**WHY IT BREAKS A GUARANTEE**: It doesn’t. The function applies `unicodedata.normalize("NFKC", ...).casefold()` before checking `.startswith(RESERVED_UNGRANTABLE_PREFIXES)`. Full-width and mixed-case variants are correctly folded to `"offense:"` and matched. Inputs like `"offensexyz:"` do **not** start with `"offense:"` post-normalization, so they correctly return `False`. Space-containing `"OFFENSE :x"` becomes `"offense :x"` after casefold — which does **not** start with `"offense:"` (note the space), so it is **not** prohibited — but this is correct: `"offense :x"` is not in the reserved namespace (which requires exact prefix `"offense:"`).  
**WHETHER ANOTHER LAYER CATCHES IT**: N/A — the logic is sound.  
**FIX**: None needed.

---

**ID**: F-02  
**TITLE**: Non-string inputs handled safely (totality preserved)  
**SEVERITY**: LOW (non-finding)  
**LOCATION**: `salienceos/interpreter/policy.py:31`  
**CONCRETE INPUT**: `None`, `123`, `b"offense:x"`, `("offense:x",)`  
**WHY IT BREAKS A GUARANTEE**: It doesn’t. The function explicitly checks `if not isinstance(capability, str): return False`. This is correct: only strings can be capability identifiers. Non-strings are not in the prohibited namespace.  
**WHETHER ANOTHER LAYER CATCHES IT**: N/A — behavior is by design and safe.  
**FIX**: None.

---

**ID**: F-03  
**TITLE**: Legitimate capabilities unaffected (no collision with `offense:` reservation)  
**SEVERITY**: LOW (non-finding)  
**LOCATION**: All layers (`policy.py`, `directive.py`, `collaborator/policycaps.py`)  
**CONCRETE INPUT**: `"fs.read:project"`, `"shell.exec"`, `"net.get:example.com"`, `"offense"` (no colon), `"offensive"`  
**WHY IT BREAKS A GUARANTEE**: It doesn’t. The reservation is strictly for strings starting with `"offense:"` **after normalization**. `"offense"` (no colon) and similar are correctly allowed. Tests confirm this.  
**WHETHER ANOTHER LAYER CATCHES IT**: N/A — no issue.  
**FIX**: None.

---

**ID**: F-04  
**TITLE**: No consumer bypasses `grants_capability` to read `allowed_capabilities` directly for authority decisions  
**SEVERITY**: CRITICAL (potential if true) → **NOT FOUND**  
**LOCATION**: Entire codebase (reviewed context + tests)  
**CONCRETE INPUT**: Hand-built `Directive` with `"offense:evil.com"` in `allowed_capabilities`  
**WHY IT BREAKS A GUARANTEE**: If any consumer checked `"offense:evil.com" in directive.allowed_capabilities` instead of calling `directive.grants_capability(...)`, it could mistakenly treat it as authorized.  
**WHY IT DOESN’T**: The system enforces that **`grants_capability` is the only capability accessor**. The docstring in `directive.py` states: “`grants_capability()` is the only capability accessor, so a consumer cannot infer authority from the scalar knobs.” Tests (`test_grants_capability_refuses_offense_even_if_present_in_allowed`) explicitly verify that even when `"offense:evil.com"` is in `allowed_capabilities`, `grants_capability` returns `False`. No evidence exists in provided code of direct `allowed_capabilities` inspection for authorization.  
**WHETHER ANOTHER LAYER CATCHES IT**: The `grants_capability` guard is sufficient by itself.  
**FIX**: None needed — structural guarantee holds.

---

**ID**: F-05  
**TITLE**: Signature coherence between `issue_policy` strip and verification  
**SEVERITY**: MEDIUM (potential mismatch) → **BLOCKED**  
**LOCATION**: `salienceos/interpreter/policy.py:109–112` (`issue_policy` strips before signing)  
**CONCRETE INPUT**: `granted_capabilities=("fs.read:project", "offense:evil.com")`  
**WHY IT BREAKS A GUARANTEE**: If stripping happened **after** signing, an attacker could sign a payload with `"offense:..."` and have it verified but then stripped — creating a mismatch. But here, stripping occurs **before** `PolicyCaps` construction and **before** `sign(caps.signed_payload(), ...)`. The signed payload contains only non-offense caps. Verification (`verify_policy`) checks that `granted_capabilities` are strings and match the signed payload. So coherence is maintained.  
**WHETHER ANOTHER LAYER CATCHES IT**: Yes — the signing input is the stripped list. Even if someone tried to forge a grant with offense caps, `verify_policy` would reject it unless the signature matched the **stripped** content — which it wouldn’t if offense caps were present at signing time. But since `issue_policy` strips before signing, the envelope is clean.  
**FIX**: None — defense-in-depth is coherent.

---

**ID**: F-06  
**TITLE**: `granted_capabilities()` in `collaborator/policycaps.py` filters legacy path  
**SEVERITY**: LOW (defense-in-depth, working as intended)  
**LOCATION**: `collaborator/policycaps.py:158–165`  
**CONCRETE INPUT**: Session with `session.capabilities = ("offense:evil.com",)` and no enforcement  
**WHY IT BREAKS A GUARANTEE**: It doesn’t. Even in legacy (non-enforced) mode, `granted_capabilities()` filters out offense caps. This ensures the prohibited class never enters the seam, regardless of path.  
**WHETHER ANOTHER LAYER CATCHES IT**: Yes — but this layer adds value.  
**FIX**: None.

---

**CERTIFICATION LINE**  
✅ **CERTIFIED**: "no in-band path grants an offense: capability; no legitimate capability is broken"

---

**STEELMAN**  
The change implements a defense-in-depth, normalized, prefix-based reservation that is enforced at mint-time (fail-loud), envelope construction (strip), and runtime authorization (unconditional refusal). The core guarantee lives in `Directive.grants_capability`, which cannot be bypassed by data manipulation. Unicode confusables, case variants, and malformed inputs are handled correctly. Legitimate capabilities remain unaffected. The design acknowledges that single-node Tier-3 unlock is illusory and instead hardens the invariant.

---

**VERDICT**  
**SOUND** — The diff correctly and completely enforces structural ungrantability of the `offense:` namespace with no bypasses, no collateral damage, and robust handling of edge cases.  

**Highest-value fix**: None required — but to future-proof, consider adding a compile-time check (e.g., via static analysis or a linter rule) that no new code calls `in directive.allowed_capabilities` for authorization decisions, reinforcing the `grants_capability` discipline.
