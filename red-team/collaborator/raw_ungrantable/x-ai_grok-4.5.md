# ungrantable-namespace CODE panel: x-ai/grok-4.5

_finish=stop seconds=134.6 cost=$0.0730 usage={'prompt_tokens': 14336, 'completion_tokens': 7425, 'total_tokens': 21761, 'cost': 0.0730044, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0730044, 'upstream_inference_prompt_cost': 0.0284544, 'upstream_inference_completions_cost': 0.04455}, 'completion_tokens_details': {'reasoning_tokens': 5527, 'image_tokens': 0, 'audio_tokens': 0}}_

## Findings

### NF-1 / BLOCKED: Hand-built Directive with `offense:` in `allowed_capabilities`
- **SEVERITY:** NON-FINDING (blocked attack)
- **LOCATION:** `salienceos/interpreter/directive.py` (grants_capability); pinned at `tests/test_no_laundering.py` (`test_grants_capability_refuses_offense_even_if_present_in_allowed`)
- **CONCRETE INPUT:** `Directive(..., allowed_capabilities=("offense:evil.com", "fs.read:project"), ...)` then `d.grants_capability("offense:evil.com")`
- **WHY IT DOES NOT BREAK:** Refusal runs *before* membership; returns `False` even when the tuple still contains the string. This is the load-bearing belt.
- **OTHER LAYER:** N/A (this *is* the layer)
- **FIX:** None

### NF-2 / BLOCKED: Signed envelope via `issue_policy` carrying `offense:`
- **SEVERITY:** NON-FINDING (blocked attack)
- **LOCATION:** `salienceos/interpreter/policy.py` (`issue_policy` strip + sign)
- **CONCRETE INPUT:** `issue_policy(..., granted_capabilities=("fs.read:project", "offense:evil.com"), ...)`
- **WHY IT DOES NOT BREAK:** Strip runs before `signed_payload()` / `sign(...)`. Verifier checks the same stripped payload. Coherent: nothing stripped “rides” under an old signature, and legit caps still sign/verify/grant.
- **OTHER LAYER:** `grants_capability` would still refuse if strip were removed
- **FIX:** None

### NF-3 / BLOCKED: Case / full-width confusables
- **SEVERITY:** NON-FINDING (blocked attack)
- **LOCATION:** `salienceos/interpreter/policy.py` (`is_ungrantable_capability`)
- **CONCRETE INPUTS:** `"OFFENSE:evil.com"`, `"Offense:x"`, `"ｏｆｆｅｎｓｅ：x"` (full-width letters + full-width colon)
- **WHY IT DOES NOT BREAK:** NFKC then `casefold` then `startswith("offense:")`. Mint rejects, `issue_policy` strips, `grants_capability` refuses.
- **OTHER LAYER:** All three belts
- **FIX:** None

### NF-4 / BLOCKED: Collaborator mint + foreign hand-signed grant
- **SEVERITY:** NON-FINDING (blocked attack)
- **LOCATION:** `collaborator/policycaps.py` (`mint`, `granted_capabilities`)
- **CONCRETE INPUTS:** `mint(("offense:evil.com",), ...)`; `SignedPolicyCaps(PolicyCaps(("offense:evil.com","fs.read:project"),...), sign(...))` then `granted_capabilities(session)`
- **WHY IT DOES NOT BREAK:** Mint fail-loud; read path filters both legacy and verified-grant paths so `offense:` never enters the seam API.
- **OTHER LAYER:** Core `grants_capability` (interpreter path)
- **FIX:** None

### NF-5 / BLOCKED: Prefix-boundary false positives (legit caps)
- **SEVERITY:** NON-FINDING
- **LOCATION:** `is_ungrantable_capability`
- **CONCRETE INPUTS:** `"offense"`, `"offensive"`, `"offense_shape"`, `"not.offense:x"`, `"net.get:example.com"`, `"fs.read:project"`, `"shell.exec"`, `"shell.raw_network"`, `"shell.contained_autonomy"`
- **WHY IT DOES NOT BREAK:** Match is `startswith("offense:")` only after normalize — no collision with documented legit caps; behaviour unchanged for them.
- **OTHER LAYER:** N/A
- **FIX:** None

### NF-6 / BLOCKED: Totality on junk types
- **SEVERITY:** NON-FINDING
- **LOCATION:** `is_ungrantable_capability`
- **CONCRETE INPUTS:** `None`, `123`, `("offense:x",)`, `b"offense:x"`
- **WHY IT DOES NOT BREAK:** Non-`str` → `False` (documented); no raise. `issue_policy(None)` still TypeErrors on iterate — same as pre-change `tuple(granted_capabilities)`.
- **OTHER LAYER:** `verify_policy` requires `all(isinstance(c, str) for c in granted_capabilities)` — fail-closed on non-str caps in signed policies
- **FIX:** None

### F-1 / Residual: `allowed_capabilities` / raw grant tuples remain visible
- **SEVERITY:** LOW
- **LOCATION:** `directive.py` (`allowed_capabilities` field); `collaborator/policycaps.py` (`grant.caps.capabilities` via `_valid_grant`); `policy.py` `verify_policy` (no ungrantable reject)
- **CONCRETE INPUT / BYPASS:** Hand-built or key-holder-signed `PolicyCaps`/`Directive` with `"offense:evil.com"` in the tuple; consumer does `cap in directive.allowed_capabilities` or reads `session.policy_caps.caps.capabilities` **without** `grants_capability` / `granted_capabilities()`.
- **WHY IT MATTERS:** Does **not** make `grants_capability` return `True`. It *does* mean strip/filter are belt-and-suspenders on the *authoring/read APIs*, not a proof that every possible attribute read is clean. Design/doc claim “THE authority accessor” is `grants_capability`; this residual is “discipline not mechanically enforced on field reads.”
- **OTHER LAYER CATCHES?** Yes for authorization **if** callers use `grants_capability` / `granted_capabilities()`. No offense executor in-band (ADR non-goal). `verify_policy` will still accept a signed envelope that lists `offense:` (only `issue_policy` strips).
- **FIX (defense-in-depth, optional):** Reject ungrantable strings in `verify_policy` (and optionally collaborator `verify`) so a verified envelope cannot list them; keep unconditional refuse in `grants_capability` as load-bearing.

### F-2 / Informational: Reservation is prefix-ID, not glyph/ZWJ canonicalization
- **SEVERITY:** LOW (documented non-goal / not a grant of `offense:`)
- **LOCATION:** `policy.py` `is_ungrantable_capability`
- **CONCRETE INPUT:** `"offense\u200b:evil.com"` (ZWSP), `"offense :evil.com"` (space before colon), Cyrillic lookalike `"оffense:evil.com"` (U+043E)
- **WHY IT DOES NOT BREAK THE CLAIM:** These are **different** capability strings; they do not `startswith("offense:")` after NFKC+casefold, so they are not the reserved namespace. Nothing in this diff routes “offense-shaped” strings to authority. Brief marks semantic/naming side-steps out of scope.
- **OTHER LAYER:** No Tier-3 executor; exact-string caps elsewhere
- **FIX:** Only if you want stronger confusable hardening (strip ZW/bidi, etc.) — not required for the stated guarantee

---

## CERTIFICATION LINE

**CERTIFIED** — There is no in-band path where `grants_capability` returns True for an `offense:`-namespace string (including case/NFKC confusables and hand-built directives); `issue_policy`/mint/read-path belts are coherent with signing; no legitimate listed capability is stripped or refused by this reservation.

---

## STEELMAN

The design correctly puts the **unconditional** refuse in `Directive.grants_capability` (so membership and hand-wiring cannot authorize the class), treats `issue_policy` strip and collaborator mint/read filters as defense-in-depth, and makes the recognizer total with NFKC+casefold against the exact confusable class called out by the panel. Signature coherence is right: strip then sign over the stripped payload. Tests pin the load-bearing structural case (present in tuple, not granted) and the single source of truth for the prefix.

---

## VERDICT

**SOUND** — The structural guarantee holds under adversarial exercise; residuals are field-read discipline and non-semantic naming side-steps, not an authorization break.  

**Single highest-value fix:** In `verify_policy`, reject any `granted_capabilities` entry where `is_ungrantable_capability(c)` (fail closed at the trust boundary) so `allowed_capabilities` cannot carry `offense:` even on key-holder hand-built envelopes — without weakening the unconditional `grants_capability` refuse.
