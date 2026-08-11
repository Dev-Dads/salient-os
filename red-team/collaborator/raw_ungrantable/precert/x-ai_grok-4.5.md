# ungrantable-namespace CODE panel: x-ai/grok-4.5

_finish=stop seconds=145.0 cost=$0.0706 usage={'prompt_tokens': 13031, 'completion_tokens': 7453, 'total_tokens': 20484, 'cost': 0.0705624, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0705624, 'upstream_inference_prompt_cost': 0.0258444, 'upstream_inference_completions_cost': 0.044718}, 'completion_tokens_details': {'reasoning_tokens': 5325, 'image_tokens': 0, 'audio_tokens': 0}}_

# Adversarial Review: ADR 0004 / `offense:` un-grantability

## Findings

### F1 / Collaborator `verify`/`granted_capabilities` do not re-check the reserved namespace (mint is not a chokepoint)
- **SEVERITY:** MEDIUM
- **LOCATION:** `collaborator/policycaps.py` — `mint` ~L77–90 (check), `verify` ~L108–125 (no check), `granted_capabilities` ~L155–163 (returns raw tuple)
- **CONCRETE INPUT / BYPASS:**
```python
from collaborator.policycaps import PolicyCaps, SignedPolicyCaps, sign, verify, granted_capabilities

caps = PolicyCaps(
    capabilities=("offense:evil.com", "fs.write:project"),
    leash_caps=(),
    issuer="admin",
    subject="/ws",
)
signed = SignedPolicyCaps(caps=caps, signature=sign(caps, CAPS_KEY))
assert verify(signed, CAPS_KEY, "/ws")  # True — no ungrantable check
# session.policy_caps = signed; session.enforce_caps = True; session.caps_key = CAPS_KEY
# granted_capabilities(session) -> ("offense:evil.com", "fs.write:project")
```
Mint is only one constructor. `PolicyCaps` + `sign` are public; `verify` never calls `is_ungrantable_capability`.
- **WHY IT BREAKS A GUARANTEE:** Author/ADR text says the operator “cannot even CONSTRUCT a grant naming the prohibited class” and the certification claim lists “not via a signed PolicyCaps.” That is true only for the `mint()` entrypoint, not for the collaborator grant surface that actually authorizes (`granted_capabilities`).
- **ANOTHER LAYER:** Core `Directive.grants_capability` still refuses `offense:` on the interpreter path. No offense executor exists in v0, so nothing currently *acts* on such a string. Residual: any consumer that treats `granted_capabilities(session)` / `cap in session.capabilities` as authority will see a live `offense:` grant if mint is bypassed.
- **FIX:** In `verify` (fail closed) and/or `granted_capabilities` (filter), reject/drop caps where `is_ungrantable_capability(c)`. Optionally make `sign` refuse them too so every signing path is closed.

---

### F2 / `verify_policy` allows a signed interpreter envelope to *carry* `offense:` (carry ≠ grant)
- **SEVERITY:** LOW
- **LOCATION:** `salienceos/interpreter/policy.py` — `issue_policy` strip ~L100–109; `verify_policy` ~L141–175 (no ungrantable check)
- **CONCRETE INPUT:** Hand-build `policy.PolicyCaps` with `granted_capabilities=("offense:evil.com",)`, sign with `policy_key`, pass `verify_policy`. Returns `True`. `interpret` will copy it into `directive.allowed_capabilities`.
- **WHY:** Comments claim “a prohibited-namespace capability never rides in a signed envelope.” That holds for `issue_policy` only, not for every signed `PolicyCaps`.
- **ANOTHER LAYER:** **Yes — load-bearing.** `Directive.grants_capability` returns `False` unconditionally (`directive.py` refusal before membership). Tests pin this (`test_grants_capability_refuses_offense_even_if_present_in_allowed`).
- **FIX:** In `verify_policy`, require `all(not is_ungrantable_capability(c) for c in policy.granted_capabilities)` so carry and grant stay aligned; keeps audit envelopes clean.

---

### F3 / Authority footgun if any consumer reads `allowed_capabilities` directly
- **SEVERITY:** LOW (residual / architectural assumption)
- **LOCATION:** `directive.py` — field `allowed_capabilities`; accessor `grants_capability` ~L54–64
- **CONCRETE INPUT:** Hand-built `Directive(allowed_capabilities=("offense:evil.com",), ...)` then ` "offense:evil.com" in d.allowed_capabilities` → `True`, while `d.grants_capability("offense:evil.com")` → `False`.
- **WHY:** Structural guarantee is entirely inside `grants_capability`. A consumer that membership-checks the tuple bypasses ADR 0004 for any path that still *populates* the tuple (F2, tests, mis-wiring).
- **ANOTHER LAYER:** `issue_policy` strip removes the normal in-band population path. Full-repo consumer audit not in this material; docstring asserts `grants_capability` is the only accessor.
- **FIX:** Repo-wide enforce/grep: no authority decision on `allowed_capabilities` / raw grant tuples; optional `@property` warning or rename to `_allowed_capabilities`.

---

### F4 / No NFKC / homoglyph normalization (not a current grant bypass)
- **SEVERITY:** LOW (informational / non-goal boundary)
- **LOCATION:** `policy.py` — `is_ungrantable_capability` ~L36–41
- **CONCRETE INPUT:** `"ｏｆｆｅｎｓｅ：evil.com"` (fullwidth), `"оffense:evil.com"` (Cyrillic о), `"offense\u200b:evil.com"` (ZW space) → `is_ungrantable_capability` → `False`.
- **WHY:** Does not yield a grant for the real ASCII namespace under exact-string matching; a tool looking for `offense:evil.com` will not match these either. Side-step by *different names* (`pentest:`, `attack:`) is an explicit ADR non-goal (prefix reservation, not semantic detector).
- **ANOTHER LAYER:** Exact-match capability model makes these distinct strings, not aliases.
- **FIX:** None required for this claim; if future tools casefold/NFKC-compare, normalize before the prefix check.

---

### NON-FINDINGS (attacks blocked — valuable)

| Attack | Result |
|--------|--------|
| `grants_capability("offense:evil.com")` with cap in `allowed_capabilities` | **Blocked** — unconditional refusal before membership (`directive.py`) |
| `grants_capability("OFFENSE:x")` / `"Offense:x"` | **Blocked** — `casefold()` then `startswith(("offense:",))` |
| `issue_policy(..., granted_capabilities=("fs.read:project", "offense:evil.com"), ...)` | **Strips** offense; signs only legit; `grants_capability("fs.read:project")` True |
| `mint(("offense:evil.com",), ...)` / mixed mint | **Raises** `ValueError` |
| Prefix boundary: `"offense"`, `"offensive"`, `"offense_shape"`, `"not.offense:x"`, `"offensexyz"` | **Not** ungrantable — correct |
| Legit caps: `fs.*`, `shell.exec`, `net.get:`, `net.post:`, `shell.raw_network`, `shell.contained_autonomy` | **Unaffected** — no prefix collision |
| `is_ungrantable_capability(None/123/bytes/tuple)` | **Total** — returns `False`, no raise |
| `grants_capability(None)` | **False**, no raise |
| Signature coherence on `issue_policy` strip | **Coherent** — payload signed post-strip; tamper-add breaks HMAC; verify fail-closed |
| Empty `RESERVED_UNGRANTABLE_PREFIXES` fail-open | **Pinned** by `test_reserved_prefix_tuple_is_the_single_source` |
| Turkish/ASCII `I`/`OFFENSE:` | **Handled** by `str.casefold` for normal Latin |

**Either belt alone:** `grants_capability` refusal alone is sufficient for the core “no directive grants offense:” guarantee (including hand-built directives). `issue_policy` strip alone is **not** sufficient (hand-built / non-`issue_policy` envelopes). Mint alone is **not** sufficient (F1). The load-bearing control is `grants_capability`; strip + mint are defense in depth (mint currently incomplete on the collaborator read path).

---

## CERTIFICATION LINE

**CERTIFIED** — There is no in-band path through `Directive.grants_capability` that returns True for an `offense:`-namespaced capability (any case variant), including hand-built directives and signed envelopes that still carry the string; no legitimate non-`offense:` capability is stripped or refused by this change. (Collaborator `granted_capabilities` after mint-bypass remains a defense-in-depth gap, not a break of the core accessor invariant.)

---

## STEELMAN

The design correctly puts the invariant where authority is actually decided: an unconditional, case-insensitive refusal in `grants_capability`, so membership and signing bugs cannot authorize the prohibited class. Making `is_ungrantable_capability` total (non-str → False) avoids fail-open raises, and stripping at `issue_policy` keeps honest envelopes clean without being the sole control. Tests pin the structural case (present in tuple, not granted) and the reserved-prefix singleton so a future empty-tuple edit fails closed in CI.

---

## VERDICT

**MINOR_ISSUES** — Core structural un-grantability via `grants_capability` is sound and legit capabilities are intact; the highest-value fix is to enforce `is_ungrantable_capability` in collaborator `verify`/`granted_capabilities` (and optionally `verify_policy`) so mint is not a bypassable belt and “never rides in a signed grant” matches the read path.
