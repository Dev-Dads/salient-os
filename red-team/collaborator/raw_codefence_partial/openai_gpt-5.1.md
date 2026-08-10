# codefence completeness panel: openai/gpt-5.1

_finish=stop seconds=35.9 cost=$0.0486 usage={'prompt_tokens': 10917, 'completion_tokens': 3494, 'total_tokens': 14411, 'cost': 0.04858625, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.04858625, 'upstream_inference_prompt_cost': 0.01364625, 'upstream_inference_completions_cost': 0.03494}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID 1 / INCOMPLETE NAMESPACE-PACKAGE SUPPORT FAILS CLOSED  
SEVERITY: LOW  
LOCATION: collaborator/codefence.py:28–45, 65–74  

- CONCRETE INPUT OR BYPASS  
  - Deploy `salienceos` as a PEP 420 namespace package (no `__file__` on the imported module).  
  - Example: install `salienceos` as a namespace-only top-level where the imported `salienceos` object has `__file__ = None`.  
  - Then `codefence._code_slots()` returns `("salienceos", None)`, `_resolved_roots()` drops that slot, `_RESOLVED_PACKAGES == {"collaborator"}`, and any `Session(workspace=...)` raises `WorkspaceOverlapsCodeError` via `missing = ["salienceos"]`.

- WHY IT BREAKS A GUARANTEE  
  - This is intentionally fail-closed, but it does reject a potentially “valid” namespace deployment of `salienceos`.  
  - It is not a *safety* break (no fail-open), but it is a correctness/availability tradeoff: the guard refuses to run at all in an environment where the core is a namespace package with no `__file__`, even if the host would consider this a legitimate layout.  
  - The design as written chooses safety over availability. That is consistent with the governance intent, but it is useful to explicitly call out that any namespace-only deployment of `salienceos` is unsupported and will be hard-rejected.

- WHETHER ANOTHER CHECK CATCHES IT  
  - No other check “catches” this in a more nuanced way; this is the primary completeness gate and it unconditionally fails the process. There is no softer path around it.

- FIX  
  - If the product decision is that namespace-only `salienceos` deployments should be supported, the implementation would need a different way to locate the protected root (e.g. probing `importlib.util.find_spec("salienceos")` and walking its `submodule_search_locations`), and `_code_slots()` / `_resolved_roots()` would have to handle namespace semantics explicitly.  
  - If, as seems intended from the comments, namespace-only layouts are considered out-of-scope for a governance guard, the code is correct; the “fix” is just to document that `salienceos` must be a file-backed package and that namespace-only installs are unsupported and intentionally fail-closed.  

---

ID 2 / ERROR MESSAGE REVEALS WHICH PACKAGE SLOT FAILED TO RESOLVE  
SEVERITY: LOW  
LOCATION: collaborator/codefence.py:69–74  

- CONCRETE INPUT OR BYPASS  
  - Set up an environment where `salienceos` cannot be imported with a resolvable `__file__` (e.g., partial install, broken packaging, or deliberate removal of its file).  
  - Call `Session(workspace=...)`.  
  - The exception message includes `missing: ['salienceos']`, revealing that the system expects both `collaborator` and `salienceos` and exactly which one is missing.

- WHY IT BREAKS A GUARANTEE  
  - The claim states the raise is safe and non–information-leaking: “the message now interpolates only `missing`, a subset of the static slot names.”  
  - That’s mostly true, but there is still a minor info channel: an attacker code path that can observe the exception can distinguish between a “collaborator-only” install and environments where both are present, based on the contents of `missing`.  
  - This does not expose secrets; it only exposes whether `salienceos` is present and resolvable. In a threat model where module presence is already obvious (stack traces, import behavior), this is negligible, but strictly speaking the error is slightly more specific than “all roots not found.”

- WHETHER ANOTHER CHECK CATCHES IT  
  - This is not a bypass of another guard; it’s a minor deviation from the strict “no info leak” goal. There is no additional layer masking this detail.

- FIX  
  - If desired to further minimize information leakage, change the error to a generic message that does not enumerate which packages are missing, for example:  
    ```python
    raise WorkspaceOverlapsCodeError(
        "could not locate all of the Collaborator's own code roots — "
        "refusing to construct a session (partial or absent code protection would be a silent no-op)"
    )
    ```  
  - This still clearly communicates the governance failure without disclosing per-slot presence.

---

ID 3 / NO-IN-BAND CONSUMER OF INCOMPLETE ROOTS – ATTEMPTED BYPASS, BLOCKED  
SEVERITY: LOW (NON-FINDING)  
LOCATION: collaborator/codefence.py:52–60, 79–96; collaborator/session.py:38–49; tests/test_collaborator_codefence.py:111–139  

- CONCRETE INPUT OR BYPASS  
  - Hypothetical attack: try to get a `Session` constructed when `_RESOLVED_PACKAGES` is incomplete, then call `names_code_root` or other code that reads `PROTECTED_ROOTS` to consume an incomplete perimeter.  
  - For example, imagine `salienceos.__file__` is broken and only `collaborator` resolves. You attempt `Session(workspace=...)` and then `names_code_root("...")`.

- WHY IT BREAKS A GUARANTEE (ATTEMPT)  
  - The claim is: “completeness is enforced at Session construction, so no Session with incomplete roots exists (no in-band consumer sees incomplete roots).”  
  - The question is whether there is any path that lets you:  
    1. Have `_RESOLVED_PACKAGES` incomplete,  
    2. Still construct a `Session`,  
    3. Then call consumers like `names_code_root` that use `PROTECTED_ROOTS` and thereby rely on an incomplete root set.

- WHETHER ANOTHER CHECK CATCHES IT  
  - The attempted bypass is blocked. `Session.__init__` imports `disjoint_from_code` and calls it immediately, before any other Session state is really usable (session.py:38–49).  
  - `disjoint_from_code` first checks completeness: if any slot is missing, it raises `WorkspaceOverlapsCodeError` and `Session` is not constructed.  
  - Any other consumer (e.g., `names_code_root`) will still see the incomplete `PROTECTED_ROOTS` module globals, but there will be no live `Session` object with those roots. Given the design (no other governance paths that rely on a Session but bypass `disjoint_from_code`), this meets the stated guarantee.

- FIX  
  - None required. The code correctly enforces completeness at Session construction, and there is no in-band consumer of incomplete roots reachable via a constructed Session.  

---

ID 4 / PROTECTED_ROOTS AND _RESOLVED_PACKAGES DESYNC ATTEMPT – BLOCKED  
SEVERITY: LOW (NON-FINDING)  
LOCATION: collaborator/codefence.py:47–60  

- CONCRETE INPUT OR BYPASS  
  - Hypothetically, if `PROTECTED_ROOTS` were computed from a different source than `_RESOLVED_PACKAGES` (or subject to path dedup while `_RESOLVED_PACKAGES` isn’t), you might have a slot marked as resolved in `_RESOLVED_PACKAGES` while its directory is not actually in `PROTECTED_ROOTS` (fail-open).  
  - Also, if path-dedup removed one of multiple slots sharing a directory, you might have a mismatch.

- WHY IT BREAKS A GUARANTEE (ATTEMPT)  
  - The guarantee is: “PROTECTED_ROOTS and _RESOLVED_PACKAGES both derive from the same _RESOLVED tuple (no dedup), so they cannot desync. [...] confirm a slot's name and its path always land together, and that removing the dedup introduced no fail-open.”

- WHETHER ANOTHER CHECK CATCHES IT  
  - The code satisfies the guarantee:  
    - `_RESOLVED` is built once in `_resolved_roots()` as `(name, path)` tuples with no dedup (codefence.py:47–60).  
    - `PROTECTED_ROOTS` is `tuple(p for _, p in _RESOLVED)` and `_RESOLVED_PACKAGES` is `frozenset(name for name, _ in _RESOLVED)`.  
    - There is no opportunity for them to diverge: any slot included in `_RESOLVED_PACKAGES` necessarily contributes its path to `PROTECTED_ROOTS`.  
    - In the degenerate “both packages in one dir” case, two entries in `_RESOLVED` share the same path; this means `PROTECTED_ROOTS` contains that path twice, and `_RESOLVED_PACKAGES` contains both slot names. That’s conservative and safe: the shared directory is fenced for both slots.  
  - No fail-open or desync is observable; the removal of the previous path-dedup has the intended effect and does not introduce a new hole.

- FIX  
  - None required; the implementation matches the stated intent.

---

ID 5 / “DECOY DIRECTORY” FALSE-PASS ATTEMPT – BLOCKED  
SEVERITY: LOW (NON-FINDING)  
LOCATION: collaborator/codefence.py:28–45; tests/test_collaborator_codefence.py:140–179  

- CONCRETE INPUT OR BYPASS  
  - Hypothetical: create a fake directory that looks like `salienceos` on disk but is not the actual imported core, and rely on a filesystem scan or basename match so that a decoy root is protected while the real core remains unfenced.  
  - For example: create `/opt/decoy/salienceos` containing no actual code; hope that `PROTECTED_ROOTS` is derived from scanning paths or basenames rather than from the imported module.

- WHY IT BREAKS A GUARANTEE (ATTEMPT)  
  - The code claims to avoid this entirely by resolving only from imported modules’ `__file__` attributes (“roots come ONLY from imported modules’ __file__ (not a filesystem scan), confirm/deny that a ‘decoy directory’ can cause a false PASS”).

- WHETHER ANOTHER CHECK CATCHES IT  
  - The attempt fails: `_code_slots` uses `__file__` from the *already imported* `salienceos` and from this module (collaborator/codefence.py itself).  
  - There is no filesystem scan based on basenames, and `_resolved_roots` only uses those module file paths. A decoy directory that is not backing the imported module cannot be introduced into `PROTECTED_ROOTS`.  
  - If an attacker somehow causes Python to import their decoy `salienceos` instead of the real one, then that decoy *is* the effective core, and protecting its directory is the correct behavior for the governance model.

- FIX  
  - None required; the “decoy” false-pass concern is not realized under this design.

---

ID 6 / REGRESSION: LEGITIMATE ODD-NAMED PACKAGE DIRS FALSE-FAIL – ATTEMPT, BLOCKED  
SEVERITY: LOW (NON-FINDING)  
LOCATION: collaborator/codefence.py:28–60, 65–79; tests/test_collaborator_codefence.py:123–139  

- CONCRETE INPUT OR BYPASS  
  - Use a legitimate layout where the `collaborator` and/or `salienceos` package directories have basenames that differ from the import names (e.g., a case-insensitive checkout, a symlinked vendor directory, or an editable install with custom paths).  
  - Example in tests: set `PROTECTED_ROOTS` to `("SalienceOS-v2", "collab-impl")` while `_RESOLVED_PACKAGES == {"collaborator", "salienceos"}`.

- WHY IT BREAKS A GUARANTEE (ATTEMPT)  
  - A previous version did basename matching and could false-fail in such environments. The new design claims that completeness is by slot (which module resolved), not by basename.

- WHETHER ANOTHER CHECK CATCHES IT  
  - The attempt is blocked. `_EXPECTED_PACKAGES` and `_RESOLVED_PACKAGES` track only slot names; completeness is entirely independent of directory base names.  
  - `disjoint_from_code` checks only `_EXPECTED_PACKAGES` vs `_RESOLVED_PACKAGES`. As long as both modules resolved (regardless of where their files live), `missing` is empty and the completeness gate passes.  
  - The test `test_completeness_is_slot_based_not_basename` explicitly patches odd basenames and verifies `disjoint_from_code` and `Session` construction succeed. There is no remaining basename dependency in the completeness check.

- FIX  
  - None required; the regression is fixed as claimed.

---

ID 7 / TWO-SLOT ASSUMPTION AND LOCKSTEP DRIFT ATTEMPT – BLOCKED  
SEVERITY: LOW (NON-FINDING)  
LOCATION: collaborator/codefence.py:28–41  

- CONCRETE INPUT OR BYPASS  
  - Hypothetical maintenance bug: add a third F1-relevant package to `PROTECTED_ROOTS` but forget to add it to the completeness list (or vice versa), causing a drift between “what’s fenced” and “what must resolve.”  
  - The current code asserts that `_EXPECTED_PACKAGES` is derived from `_code_slots()` and that both `PROTECTED_ROOTS` and `_RESOLVED_PACKAGES` derive from `_RESOLVED` built from `_code_slots()`.

- WHY IT BREAKS A GUARANTEE (ATTEMPT)  
  - The guarantee is “SINGLE source of truth: _code_slots() returns (name, __file__) per package; _EXPECTED_PACKAGES is DERIVED from it (not a duplicate literal), so the resolution sources and the completeness list cannot drift.”

- WHETHER ANOTHER CHECK CATCHES IT  
  - In the current repo, that guarantee holds: there is exactly one function `_code_slots`, which is the only place that defines the slot set. `_EXPECTED_PACKAGES` is computed from it, `_RESOLVED` is computed from it, and everything else flows from `_RESOLVED`.  
  - There is no second hand-maintained list of expected packages; adding a third slot requires changing `_code_slots` and thus automatically updates both the completeness set and the root resolution.  
  - The “two-slot assumption” is accurate for today’s code: there are exactly two slots (`"collaborator"`, `"salienceos"`), both referenced only via `_code_slots`. For a future third package, maintaining the single-source-of-truth invariant across `_code_slots` remains sufficient.

- FIX  
  - None required for the current code; a comment could be added noting that any new perimeter package must be added only via `_code_slots`, which is already how it’s written.

---

CERTIFICATION LINE  

CERTIFIED — The shipped completeness delta enforces fail-closed behavior whenever either code-root package slot fails to resolve, introduces no regression on legitimate odd-basename layouts, and ensures no Session is constructed with incomplete roots (no in-band consumer sees an incomplete perimeter), with only intentional fail-closed behavior for namespace-only deployments and a minor, optional-tightening info detail in the error message.

---

STEELMAN (2–3 sentences)  

The slot-based design is robust: it unifies perimeter definition, resolution, and completeness checks through a single `_code_slots` source, eliminating path/basename drift and making it very hard to accidentally fail open. By resolving from imported modules’ `__file__` and tying completeness to slots instead of directory names, it avoids both the earlier basename brittleness and any filesystem-scan decoy issues, while ensuring that no Session exists without both roots fenced. The one meaningful tradeoff is an intentional availability sacrifice for namespace-only core deployments, which is defensible for a governance guard that must never silently no-op.

---

VERDICT  

SOUND — The completeness hardening is structurally sound, enforces the intended fail-closed semantics for both empty and partial resolves, and does not introduce the regressions or fail-open paths the panel was concerned about; the single highest-value tweak, if desired, is to simplify the error message to avoid enumerating which specific package slots are missing.
