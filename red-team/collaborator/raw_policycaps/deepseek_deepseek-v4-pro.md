# PolicyCaps red-team: deepseek/deepseek-v4-pro

_finish=stop seconds=160.4 usage={'prompt_tokens': 13055, 'completion_tokens': 7110, 'total_tokens': 20165, 'cost': 0.00437092785, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.00437092785, 'upstream_inference_prompt_cost': 0.00209211597, 'upstream_inference_completions_cost': 0.00227881188}, 'completion_tokens_details': {'reasoning_tokens': 5459, 'image_tokens': 0, 'audio_tokens': 0}}_

**Adversarial Design Review — Signed PolicyCaps (③)**

---

### Finding 1
**ID:** PC‑001  
**TITLE:** Missing default for tools absent from `leash_caps` can silently widen the leash  
**SEVERITY:** HIGH  
**LOCATION:** `03-policycaps-design.md` – “PolicyCaps: a signed grant” (definition of `leash_caps`) and “Leash ordering” (no default rule)  
**CONCRETE SCENARIO:**  
A grant authorises `run_command` but the issuer forgets to include `run_command` in `leash_caps`. The design does not state what the effective leash cap is for an unlisted tool. If the implementation defaults to no cap (i.e., `act_then_report`), the host or a compromised view can set the leash to `act_then_report` and auto‑execute shell commands, directly contradicting the claim that `set_leash`/`leash_overrides` cannot loosen past the grant.  
**WHY IT MATTERS:**  
The “no widening” property for the leash axis becomes conditional on the issuer remembering to list every tool. An accidental omission or a crafted grant that omits a tool would let the leash be loosened arbitrarily, breaking the hardening.  
**SUGGESTED FIX:**  
Define an explicit default: any tool not present in `leash_caps` MUST be treated as having the **strictest** leash (`notify_only`). Alternatively, require that the grant include an entry for every tool the capabilities cover, and reject the grant at verify time if a granted capability lacks a corresponding leash cap. Update the proof to cover this case.

---

### Finding 2
**ID:** PC‑002  
**TITLE:** Unspecified canonicalisation algorithm risks signature collision and tamper‑evasion  
**SEVERITY:** HIGH  
**LOCATION:** `03-policycaps-design.md` – “PolicyCaps: a signed grant” (`canonical(caps)`)  
**CONCRETE SCENARIO:**  
The design says the signature is over `canonical(caps)` but never defines the canonical form. If the implementation uses a non‑deterministic or ambiguous serialisation (e.g., Python’s `repr()`, `str()`, or JSON without sorted keys), two semantically different `PolicyCaps` objects could produce the same canonical byte string. An attacker could swap capabilities or leash caps while keeping the signature valid, bypassing tamper‑evidence.  
**WHY IT MATTERS:**  
The entire integrity guarantee rests on the signature. A collision‑prone canonicalisation breaks the “tamper → fail closed” property and allows widening without detection.  
**SUGGESTED FIX:**  
Specify a concrete, deterministic canonicalisation algorithm. For example: JSON with sorted keys, no whitespace, and stable type representations (e.g., lists for tuples). Include a test vector in the design. The `mint()` and `verify()` functions must use exactly this algorithm.

---

### Finding 3
**ID:** PC‑003  
**TITLE:** Workspace subject binding may be fragile due to path normalisation differences  
**SEVERITY:** LOW  
**LOCATION:** `03-policycaps-design.md` – “subject binds it to ONE workspace” and verify step (`caps.subject == this session's workspace`)  
**CONCRETE SCENARIO:**  
The session’s `workspace` is a `Path` object; the grant’s `subject` is a plain string. If the host sets the workspace to `"/home/user/project"` but the `Path` object normalises it to `"/home/user/project/"` (trailing slash) or resolves a symlink, the string comparison will fail. An otherwise valid grant is rejected → denial of service, not a bypass.  
**WHY IT MATTERS:**  
While not a privilege escalation, it violates the “fail‑closed” expectation for a legitimate grant and could be exploited by an attacker who can influence the workspace path to cause a denial‑of‑governance.  
**SUGGESTED FIX:**  
Normalise both sides before comparison: resolve the workspace to an absolute path with no trailing slash, and require the subject to be stored in the same normalised form. Document the normalisation rule.

---

### Finding 4
**ID:** PC‑004  
**TITLE:** No expiry or nonce allows indefinite replay within the same workspace  
**SEVERITY:** LOW  
**LOCATION:** `03-policycaps-design.md` – “PolicyCaps: a signed grant” (no `exp` or `nonce` field)  
**CONCRETE SCENARIO:**  
A grant minted for workspace `A` is valid forever. If the signed blob is accidentally leaked (e.g., logs, backup), it can be presented in any future session on workspace `A` that uses the same `caps_key`, effectively re‑granting the authority.  
**WHY IT MATTERS:**  
The design honestly defers this, but the absence of temporal binding weakens the “provenance” claim (you cannot tell *when* the grant was intended) and makes revocation impossible without key rotation. Acceptable for v0 in a single trust domain, but should be explicit.  
**SUGGESTED FIX:**  
Document that grants are perpetual and that revocation requires key rotation. Consider adding an optional `expires_at` field in a future version.

---

### Finding 5
**ID:** PC‑005  
**TITLE:** Backward‑compatible fallback can be subverted if an attacker strips the `PolicyCaps` from the session  
**SEVERITY:** LOW  
**LOCATION:** `03-policycaps-design.md` – “Backward compatible.” and `collaborator/session.py` (optional `policy_caps`)  
**CONCRETE SCENARIO:**  
An attacker who can modify the session object (e.g., through a bug in session setup) sets `session.policy_caps = None`. The governance code sees “no PolicyCaps” and falls back to the mutable `session.capabilities` and `leash_overrides`, completely bypassing the hardening.  
**WHY IT MATTERS:**  
This is within the single‑trust‑domain threat model (the attacker who can do this can already mutate capabilities directly), so it does not create a *new* bypass. However, the design’s claim that “the config and the Step‑2 control surface can only ever operate within the signed grant” implicitly assumes the grant cannot be removed. The limitation should be stated.  
**SUGGESTED FIX:**  
Add a note: “The hardening assumes the session’s `policy_caps` reference is integrity‑protected. If an attacker can remove the grant, they can revert to mutable config. In a future cross‑domain boundary, the presence of a grant would be mandatory and enforced by the verifier.”

---

### Steelman
The design correctly scopes the security to a single trust domain and honestly states that symmetric HMAC provides integrity, provenance, and fail‑closed behaviour against non‑crypto tampering, not a hard boundary against an in‑process re‑signer. It integrates cleanly into the existing governance seam, reusing the same key management pattern as the executor key, and the fail‑closed properties are well‑defined for the main paths. The backward‑compatible opt‑in is a pragmatic choice that allows incremental hardening without breaking existing deployments.

### Verdict
**MINOR_ISSUES** — The design is sound but has a few specification gaps (missing default for `leash_caps`, unspecified canonicalisation) that could lead to bypass if not addressed during implementation; these are easily fixed with explicit rules.
