# ungrantable-namespace CODE panel: openai/gpt-5.1

_finish=stop seconds=40.6 cost=$0.0500 usage={'prompt_tokens': 12167, 'completion_tokens': 3479, 'total_tokens': 15646, 'cost': 0.04999875, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.04999875, 'upstream_inference_prompt_cost': 0.01520875, 'upstream_inference_completions_cost': 0.03479}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

ID 1 / TITLE: Direct use of collaborator `capabilities` bypasses offense-strip logic (but not structural refusal)  
SEVERITY: MEDIUM  
LOCATION: collaborator/policycaps.py:89-107; collaborator/policycaps.py:146-170; any consumer that reads `SignedPolicyCaps.caps.capabilities` directly  

CONCRETE INPUT OR BYPASS  
- Construct a grant with an offense capability directly, bypassing `mint` and any future helper that calls it:  
  ```python
  from collaborator.policycaps import PolicyCaps, SignedPolicyCaps, sign

  key = b"k"
  raw_caps = PolicyCaps(
      capabilities=("offense:evil.com", "fs.read:project"),
      leash_caps=(),
      issuer="admin",
      subject="/ws",
  )
  signed = SignedPolicyCaps(caps=raw_caps, signature=sign(raw_caps, key))

  # Attach to a session; _valid_grant/enforced() will happily accept it:
  session = type("S", (), {})()
  session.policy_caps = signed
  session.caps_key = key
  session.workspace = "/ws"
  session.enforce_caps = True

  from collaborator.policycaps import granted_capabilities
  granted_capabilities(session)   # -> ("offense:evil.com", "fs.read:project")
  ```
- Any downstream code that treats `session.capabilities` or `grant.caps.capabilities` as authoritative – instead of translating them into a core `Directive` and using `Directive.grants_capability` – will now see `offense:evil.com` present and could accidentally act on it.

WHY IT BREAKS A GUARANTEE  
- The ADR/claim is specifically about “no in-band path by which an `offense:` capability is granted.”  
- In the core path (`issue_policy` → `interpret` → `Directive.grants_capability`), offense is stripped/refused, but collaborator-side artifacts are not normalized: a hand-built, correctly-signed `SignedPolicyCaps` can contain `offense:` entries, and `_valid_grant()/verify()` accept them without further checking.  
- This is not a *direct* authorization bypass (because nothing in `salienceos/interpreter` consumes these capabilities without going through `Directive.grants_capability`), but it does leave a realistic footgun: an operator or extension that treats `SignedPolicyCaps.caps.capabilities` as “the grant” might consider an offense cap to be live authority, violating the “no in-band path” intent at the ecosystem level.

WHETHER ANOTHER LAYER CATCHES IT  
- Core structural guarantee *does* hold: once you come through `issue_policy`/`interpret` and use `Directive.grants_capability`, `offense:` is ungrantable.  
- No layer inside `collaborator/policycaps.py` or `_valid_grant()` rejects or strips `offense:` in non-mint code paths; the only belt there is `mint()`. Signed grants built by other code – or by future non-Python collaborators – can carry offense caps. Nothing in verifier/interpreter re-parses those collaborator caps.

FIX  
- If the intent is *strictly* “no in-band path yields an offense capability anywhere an authority decision might reasonably be made”, add a second belt at `_valid_grant()` to sanitize collaborator grants before exposing them:  
  ```python
  # collaborator/policycaps.py

  from salienceos.interpreter.policy import is_ungrantable_capability

  def _valid_grant(session):
      ...
      grant = signed if verify(signed, key, expected_subject=subject) else None
      if grant is None:
          return None
      # Strip prohibited namespace even from non-minted grants
      cleaned_caps = tuple(
          c for c in grant.caps.capabilities if not is_ungrantable_capability(c)
      )
      if cleaned_caps != grant.caps.capabilities:
          # Re-wrap in a new PolicyCaps so downstream code never sees offense:*
          grant = SignedPolicyCaps(
              caps=PolicyCaps(
                  capabilities=cleaned_caps,
                  leash_caps=grant.caps.leash_caps,
                  issuer=grant.caps.issuer,
                  subject=grant.caps.subject,
              ),
              signature=grant.signature,  # signature covers the original set, but we already
                                          # know the operator is in-process; this is belt-only
          )
      return grant
  ```
- Optionally, add a unit test alongside `test_mint_rejects_the_prohibited_offense_namespace` that constructs a “foreign” `SignedPolicyCaps` containing `offense:` to assert that `granted_capabilities(session)` never returns it.


ID 2 / TITLE: `is_ungrantable_capability` ignores non-string Unicode-like inputs (total, but slightly underspecified)  
SEVERITY: LOW  
LOCATION: salienceos/interpreter/policy.py:29-36  

CONCRETE INPUT OR BYPASS  
- Inputs such as `b"OFFENSE:evil.com"` or objects with tricky `__str__`/Unicode representations:  
  ```python
  from salienceos.interpreter.policy import is_ungrantable_capability

  is_ungrantable_capability(b"OFFENSE:evil.com")  # -> False
  class Weird:
      def __str__(self): return "offense:evil.com"
  is_ungrantable_capability(Weird())  # -> False
  ```
- `issue_policy` and `Directive.grants_capability` both call `is_ungrantable_capability` directly on their capability inputs; collaborator `mint` also casts to `str` only *after* the check.

WHY IT BREAKS A GUARANTEE  
- It does not break the current *security* guarantee because all three enforcement sites either:  
  - already convert capabilities to `str` before they are stored/signed (`mint`, `PolicyCaps.verify_policy`), or  
  - are only called in code paths where capabilities are already strings (`interpret` / `Directive` construction).  
- However, the docstring claims “Total – a non-string is not a capability string, so it is not (this) prohibited namespace (False).” For future changes or external callers, it is easy to imagine someone passing non-string but string-convertible values and assuming the offense check runs on their `str(...)` form; it currently does not, mildly weakening the robustness of the helper as a general-purpose recognizer.

WHETHER ANOTHER LAYER CATCHES IT  
- Yes, in practice:  
  - `collaborator.mint()` coerces `capabilities` to `str` *before* checking: `caps_list = tuple(str(c) for c in capabilities)` and then iterates over `caps_list`. So non-string values are converted, and offense-like content is caught.  
  - `issue_policy`’s `granted_capabilities` are normally string-typed (see `verify_policy` enforcing `all(isinstance(c, str)...)`); malformed policies failing that check are rejected before any directive is built.  
  - Directive construction in tests (and, by design, interpreter code) always passes strings. So this is effectively a non-finding under current usage.

FIX  
- Either tighten the helper to be robust for all callers, or clarify the contract:  
  - Robust version (preferred, very small change):  
    ```python
    def is_ungrantable_capability(capability) -> bool:
        if capability is None:
            return False
        try:
            s = str(capability)
        except Exception:
            return False
        return s.casefold().startswith(RESERVED_UNGRANTABLE_PREFIXES)
    ```  
  - Or explicitly document that callers must ensure `capability` is a normalized string and that non-strings are treated as “not prohibited”.  
- Add a test case that exercises `issue_policy` with a non-string value in `granted_capabilities` (via a forged `PolicyCaps` instance) to assert that `verify_policy` rejects it; this pins the current “string-only” assumption.


ID 3 / TITLE: Collaborator `granted_capabilities(session)` can expose non-string capability entries from a foreign grant  
SEVERITY: LOW  
LOCATION: collaborator/policycaps.py:146-170  

CONCRETE INPUT OR BYPASS  
```python
from collaborator.policycaps import PolicyCaps, SignedPolicyCaps, sign, granted_capabilities

key = b"k"
# Deliberately smuggle a non-str into capabilities
raw_caps = PolicyCaps(
    capabilities=("fs.read:project", 123),
    leash_caps=(),
    issuer="admin",
    subject="/ws",
)
signed = SignedPolicyCaps(caps=raw_caps, signature=sign(raw_caps, key))

session = type("S", (), {})()
session.policy_caps = signed
session.caps_key = key
session.workspace = "/ws"
session.enforce_caps = True

granted_capabilities(session)  # -> ("fs.read:project", 123)
```

WHY IT BREAKS A GUARANTEE  
- The core claim is about `offense:` being structurally ungrantable; this does not directly violate it.  
- However, it introduces a type-robustness hole: collaborator code promises capability strings, but `granted_capabilities(session)` can return arbitrary objects if the grant was constructed externally and signed. Any downstream consumer incorrectly assuming `str` capabilities might crash or mis-handle authority decisions.  
- The core (`issue_policy` and `verify_policy`) has explicit type checks that reject non-string `granted_capabilities`; collaborator `_valid_grant()` lacks analogous validation, which is an asymmetry.

WHETHER ANOTHER LAYER CATCHES IT  
- No: collaborator `verify()` only checks HMAC and subject; it does not enforce types on `caps.capabilities`. `_valid_grant()` and `granted_capabilities()` faithfully return what they see.  
- The interpreter never sees these collaborator capabilities directly; they are used on the “tooling” side. So core’s structural guarantees remain intact, but collaborator-side code remains exposed to malformed or hostile grants.

FIX  
- Harden `_valid_grant()` or `granted_capabilities()` to enforce the same type discipline core uses:  
  ```python
  def granted_capabilities(session) -> tuple:
      if not _enforced(session):
          return tuple(getattr(session, "capabilities", ()))
      grant = _valid_grant(session)
      if grant is None:
          return ()
      # Enforce string-only capabilities
      caps = tuple(c for c in grant.caps.capabilities if isinstance(c, str))
      return caps
  ```  
- Add a test to assert that a `SignedPolicyCaps` with a non-str capability causes `granted_capabilities(session)` either to drop that entry or to return an empty tuple, aligning collaborator semantics with `verify_policy`’s expectations.


NON-FINDINGS / BLOCKED ATTACKS (EXPLICITLY NOTED)

1. Offense capabilities granted via core `issue_policy` / `interpret` / `Directive.grants_capability`  
   - Attempt: pass `"offense:evil.com"`, `"OFFENSE:evil.com"`, `"Offense:x"`, etc. into `issue_policy.granted_capabilities` and into hand-constructed `Directive.allowed_capabilities`.  
   - Result:  
     - `issue_policy` strips them from the signed `PolicyCaps.granted_capabilities` using `is_ungrantable_capability`.  
     - Even if present in `Directive.allowed_capabilities` (hand-built), `grants_capability("offense:evil.com")` returns `False` because the ungrantable check runs before the membership test.  
   - No path was found where a `Directive` ever returns `True` for an `offense:`-prefixed capability. This attack is blocked; the structural refusal is sound.

2. Casefold / Unicode prefix confusion  
   - Attempt: use upper/mixed case (`"OFFENSE:..."`, `"Offense:..."`), or simple prefix variants (`"offense"`, `"offensive"`, `"offense_shape"`, `"x-offense:y"`).  
   - Result: `is_ungrantable_capability` uses `.casefold().startswith(RESERVED_UNGRANTABLE_PREFIXES)`, and tests pin both positive and negative examples. Only strings whose *casefolded* form starts with `offense:` are refused; boundary cases without the colon or with extra characters before the colon are correctly treated as non-reserved.  
   - No misclassification path (accepting a true offense or rejecting a legit cap) was found. This attack is blocked.

3. Legitimate capabilities (fs.*, shell.exec, net.*, shell.raw_network, shell.contained_autonomy) being broken  
   - Attempt: run `issue_policy` and `interpret` with combinations of known-good capabilities and confirm `Directive.grants_capability` results.  
   - Result: For non-`offense:` prefixed capabilities, behavior is unchanged. Tests already verify that normal caps (e.g., `"fs.write:project"`, `"net.get:example.com"`, `"shell.exec"`) still grant as expected; manual reasoning over the predicate (`casefold().startswith("offense:")`) shows no collision with the known set.  
   - No false negatives were observed or reasoned. This attack is blocked.

4. Signature coherence between stripped and unstripped envelopes  
   - Attempt: See if `issue_policy` signs data that does not correspond to what `verify_policy` later sees; try attaching an `offense:` cap after signing.  
   - Result:  
     - `issue_policy` strips offense capabilities *before* constructing the `PolicyCaps` and calling `sign()`. `PolicyCaps.signed_payload()` always reflects the actual `granted_capabilities` field that was signed.  
     - `verify_policy` recomputes the signature over the same `signed_payload()` and additionally enforces type and bound checks; it does not try to reintroduce any stripped capability.  
   - No mismatch path was found; a stripped cap cannot “ride” in the signature. This attack is blocked.

5. Totality / exceptions in `is_ungrantable_capability` / `issue_policy` / `mint`  
   - Attempt: pass `None`, `bytes`, integers, large strings, non-iterable `granted_capabilities`, and malformed `PolicyCaps` into these functions.  
   - Result:  
     - `is_ungrantable_capability` is total (returns `False` for non-strings, never raises).  
     - `issue_policy` expects an iterable `granted_capabilities`; passing a non-iterable would be a misuse at the call site, but within “normal” use the function does not raise for capability contents.  
     - `collaborator.mint` explicitly casts each capability to `str` and validates; invalid leash values raise `ValueError` as documented.  
   - Within the intended types and interpreter path, no crash or unintended success case was found. Misuse cases are outside the formal claim; they fail in a straightforward way.

CERTIFICATION LINE (HEADLINE CLAIM)

CERTIFIED – Under the core `salienceos/interpreter` capability path, there is no in-band path by which an `offense:`-namespaced capability is ever granted, and no legitimate (non-offense) capability is broken by this diff.

STEELMAN (2–3 sentences)  
- The defense-in-depth design – collaborator `mint` reject, `issue_policy` strip, and `Directive.grants_capability` unconditional refusal – is coherent and correctly implemented, with tests pinning both the recognizer’s behavior and the structural property that even hand-built directives cannot grant `offense:`.  
- The only residuals are at the ecosystem edge: collaborator artifacts can still *carry* offense or non-string capabilities if minted externally, and any consumer that bypasses `Directive.grants_capability` might misinterpret those, though core structural guarantees remain intact.  
- Tightening collaborator-side validation and normalizing the helper would fully align the periphery with the strong guarantees already enforced in core.

VERDICT

SOUND – The core structural change is correct and enforces the intended ungrantability; the highest-value fix is to harden `_valid_grant()` / `granted_capabilities(session)` in `collaborator/policycaps.py` to strip or reject offense-prefixed and non-string capabilities from foreign grants, closing the remaining ecosystem footguns.
