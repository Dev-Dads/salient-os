# codefence completeness panel: qwen/qwen3-max

_finish=stop seconds=34.9 cost=$0.0149 usage={'prompt_tokens': 10938, 'completion_tokens': 1640, 'total_tokens': 12578, 'cost': 0.01492764, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.01492764, 'upstream_inference_prompt_cost': 0.00853164, 'upstream_inference_completions_cost': 0.006396}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

**FINDINGS**

---

**ID**: F1  
**TITLE**: Namespace package (`__file__` = None) causes fail-closed, which is correct but worth confirming intent  
**SEVERITY**: LOW  
**LOCATION**: `collaborator/codefence.py:60` (`if not mod_file: continue`)  
**CONCRETE INPUT OR BYPASS**: Install `salienceos` as a PEP 420 namespace package (no `__init__.py`, no `__file__`). Then `salienceos.__file__` is `None`.  
**WHY IT BREAKS A GUARANTEE**: It does **not** break the completeness guarantee — it correctly fails closed because the slot did not resolve. The question is whether this is the *right* behavior for a valid deployment.  
**WHETHER ANOTHER CHECK CATCHES IT**: No other check — this *is* the completeness check.  
**FIX**: None needed if fail-closed on namespace packages is intentional (which it is for a governance guard prioritizing safety over availability).

> ✅ **Certification note**: This is **not a bug** — it's a deliberate safety choice. Namespace packages are rare in this context (the core `salienceos/` is a concrete package), and failing closed is the correct conservative posture.

---

**ID**: F2  
**TITLE**: No residual basename dependency — confirmed  
**SEVERITY**: NONE (non-finding)  
**LOCATION**: N/A  
**CONCRETE INPUT OR BYPASS**: Tested via `test_completeness_is_slot_based_not_basename` with odd basenames like `SalienceOS-v2`.  
**WHY IT BREAKS A GUARANTEE**: It doesn’t. Completeness is purely by slot name (`"salienceos"` vs `"collaborator"`), not directory name.  
**WHETHER ANOTHER CHECK CATCHES IT**: The test explicitly confirms correctness.  
**FIX**: N/A  

> ✅ **Blocked attack**: The "odd basename false-fail" regression is **fully mitigated**. No residual basename logic remains.

---

**ID**: F3  
**TITLE**: No desync between `PROTECTED_ROOTS` and `_RESOLVED_PACKAGES` — confirmed  
**SEVERITY**: NONE (non-finding)  
**LOCATION**: `collaborator/codefence.py:75–78`  
**CONCRETE INPUT OR BYPASS**: Attempt to construct a state where a slot is in `_RESOLVED_PACKAGES` but its path is missing from `PROTECTED_ROOTS`, or vice versa.  
**WHY IT BREAKS A GUARANTEE**: Impossible — both are derived from the same `_RESOLVED` tuple without filtering or deduplication beyond the shared resolution logic. Even in the degenerate "both packages in one dir" case, the dir appears twice in `_RESOLVED` (once per slot), so both `PROTECTED_ROOTS` (as paths) and `_RESOLVED_PACKAGES` (as names) reflect full presence.  
**WHETHER ANOTHER CHECK CATCHES IT**: The derivation is atomic and single-source.  
**FIX**: N/A  

> ✅ **Guarantee upheld**: No desync possible. The removal of `p not in seen` **does not introduce fail-open** — it correctly allows duplicate protection of a shared dir under both slots.

---

**ID**: F4  
**TITLE**: No in-band consumer of incomplete roots — confirmed  
**SEVERITY**: NONE (non-finding)  
**LOCATION**: `collaborator/session.py:58` (calls `disjoint_from_code` unconditionally in `__init__`)  
**CONCRETE INPUT OR BYPASS**: Try to access `PROTECTED_ROOTS` or `_RESOLVED_PACKAGES` before `disjoint_from_code` runs.  
**WHY IT BREAKS A GUARANTEE**: Cannot happen — `Session.__init__` calls `disjoint_from_code` immediately after converting `workspace` to a `Path`, and **before** any other logic. If completeness fails, it raises **before** the `Session` object is fully constructed.  
**WHETHER ANOTHER CHECK CATCHES IT**: The gate is at construction; no session exists if incomplete.  
**FIX**: N/A  

> ✅ **No in-band exposure**: `names_code_root` and other readers only run **after** a `Session` is successfully created, which requires completeness.

---

**ID**: F5  
**TITLE**: `_EXPECTED_PACKAGES` cannot drift from `_code_slots()`  
**SEVERITY**: NONE (non-finding)  
**LOCATION**: `collaborator/codefence.py:52`  
**CONCRETE INPUT OR BYPASS**: Add a third package to `_code_slots()` but forget to update `_EXPECTED_PACKAGES`.  
**WHY IT BREAKS A GUARANTEE**: Impossible — `_EXPECTED_PACKAGES = tuple(name for name, _ in _code_slots())`. It’s derived, not duplicated.  
**WHETHER ANOTHER CHECK CATCHES IT**: The derivation prevents drift by construction.  
**FIX**: N/A  

> ✅ **Single source of truth enforced**: The two-slot assumption is **not hardcoded** — it scales automatically.

---

**ID**: F6  
**TITLE**: Error message is safe (no info leak)  
**SEVERITY**: NONE (non-finding)  
**LOCATION**: `collaborator/codefence.py:105`  
**CONCRETE INPUT OR BYPASS**: Trigger error with `missing = ["salienceos"]`.  
**WHY IT BREAKS A GUARANTEE**: It doesn’t — the message only reveals static slot names (`"collaborator"`, `"salienceos"`), which are public API surface. No paths, host details, or internal state leaked.  
**WHY IT’S SAFE**: Slot names are fixed and non-sensitive.  
**FIX**: N/A  

> ✅ **Deterministic and safe**: Message content is bounded and static.

---

**ID**: F7  
**TITLE**: "Decoy directory" cannot cause false PASS  
**SEVERITY**: NONE (non-finding)  
**LOCATION**: Entire resolution logic  
**CONCRETE INPUT OR BYPASS**: Create a decoy dir `/tmp/salienceos` and hope it gets protected without being the real `salienceos` module.  
**WHY IT BREAKS A GUARANTEE**: Cannot happen — `PROTECTED_ROOTS` comes **only** from `salienceos.__file__`, which points to the **actually imported module**. A decoy dir is irrelevant unless it **is** the real import location — in which case protecting it is correct.  
**FIX**: N/A  

> ✅ **No filesystem scan**: Resolution is module-based, not path-globbing. Decoy concern is moot.

---

### CERTIFICATION LINE

**CERTIFIED** — The implementation fails closed on empty or partial slot resolution, introduces no regression on odd-named package directories, and ensures no Session is constructed with incomplete protection, thereby preventing any in-band consumer from observing unfenced code roots.

---

### STEELMAN

The design correctly shifts completeness checking from fragile filesystem basenames to robust import-time module slots, eliminating an entire class of false negatives (partial fencing) and false positives (odd layouts). By deriving all state from a single `_code_slots()` source and enforcing the check at Session construction, it achieves both safety and maintainability.

---

### VERDICT

**SOUND** — The delta fully closes the partial-resolution fail-open identified in PR #34 without introducing regressions or new bypasses. The highest-value fix shipped is the **slot-based completeness check**, which replaces brittle basename matching with a module-centric model that aligns with Python’s actual import semantics.
