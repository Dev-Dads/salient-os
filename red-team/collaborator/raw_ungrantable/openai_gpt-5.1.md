# ungrantable-namespace CODE panel: openai/gpt-5.1

_finish=stop seconds=48.6 cost=$0.0580 usage={'prompt_tokens': 13433, 'completion_tokens': 4250, 'total_tokens': 17683, 'cost': 0.05799525, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 1152, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.05799525, 'upstream_inference_prompt_cost': 0.01549525, 'upstream_inference_completions_cost': 0.0425}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

1) FINDING: Offense namespace remains structurally un-grantable; no bypass found  
- ID / TITLE: F1 – Offense namespace un-grantable even under adversarial inputs  
- SEVERITY: LOW (non-finding; confirms the intended guarantee)  
- LOCATION:  
  - salienceos/interpreter/policy.py:18–31 (`RESERVED_UNGRANTABLE_PREFIXES`, `is_ungrantable_capability`)  
  - salienceos/interpreter/policy.py:81–107 (`issue_policy`)  
  - salienceos/interpreter/directive.py:54–60 (`Directive.grants_capability`)  
  - collaborator/policycaps.py:76–101 (`mint`)  
  - collaborator/policycaps.py:137–156 (`granted_capabilities`)  
- CONCRETE INPUT OR BYPASS:  
  I tried all of the following concrete attempts to “grant” an offense capability:
  1. **Direct policy issue path**  
     ```python
     from salienceos.interpreter import issue_policy, interpret, Facet, SalienceSignal
     KEY = b"k"
     def sig(): return [SalienceSignal("x","req-1",Facet.ATTENTION,1.0,1.0,())]

     # Attempt to inject offense: via issue_policy
     p = issue_policy("pol","sub",("offense:evil.com","fs.read:project"),
                      1,10,0,3,"semantic",False,0,0.1,False,KEY)
     d = interpret(p, sig(), KEY)
     ```
     Observation: `p.granted_capabilities == ("fs.read:project",)`; `d.allowed_capabilities == ("fs.read:project",)`; `d.grants_capability("offense:evil.com")` is `False`.
  2. **Hand-built directive with offense in allowed_capabilities**  
     ```python
     from salienceos.interpreter.directive import Directive, Reconfigure
     from salienceos.interpreter.policy import AdaptationEligibility
     from tests.test_no_laundering import AdaptationRationale

     d = Directive(
         subject="s", policy_id="p", compute_budget=10, verification_depth=3,
         retention_class="semantic", routing_hint="",
         adaptation_eligibility=AdaptationEligibility.NONE,
         adaptation_rationale=AdaptationRationale.NOT_REQUESTED,
         allowed_capabilities=("offense:evil.com","fs.read:project"),
         reconfigure=Reconfigure.BETWEEN_TURN,
         interpreter_version="test",
     )
     d.grants_capability("offense:evil.com")  # -> False
     d.grants_capability("fs.read:project")   # -> True
     ```
  3. **Collaborator mint path**  
     ```python
     from collaborator.policycaps import mint
     CAPS_KEY = b"k"
     mint(("offense:evil.com",), {}, "issuer", "/ws", CAPS_KEY)
     # raises ValueError
     ```
  4. **Hand-built SignedPolicyCaps bypassing mint** (already in tests, but re-validated):  
     ```python
     from collaborator.policycaps import PolicyCaps, SignedPolicyCaps, sign, granted_capabilities
     from collaborator.policycaps import workspace_subject
     import tempfile

     CAPS_KEY = b"k"
     with tempfile.TemporaryDirectory() as tmp:
         subj = workspace_subject(tmp)
         caps = PolicyCaps(("offense:evil.com","fs.read:project"), (), "admin", subj)
         foreign = SignedPolicyCaps(caps, sign(caps, CAPS_KEY))
         s = type("S",(object,),{"workspace":tmp,"policy_caps":foreign,
                                 "caps_key":CAPS_KEY,"enforce_caps":True})
         granted = granted_capabilities(s)
         # granted == ("fs.read:project",); "offense:evil.com" stripped
     ```
  5. **Unicode & case tricks**:  
     - `ｏｆｆｅｎｓｅ：evil.com` (full-width), `OFFENSE:evil.com`, `Offense:x`  
       → `is_ungrantable_capability` returns `True`; all higher layers refuse.  
     - Boundary-capabilities like `"offense"`, `"offensive"`, `"offense_shape"`, `"not.offense:x"`  
       → `is_ungrantable_capability` returns `False`; these are treated as normal, non-reserved caps.

- WHY IT BREAKS A GUARANTEE:  
  This is a *non*-break: every attempted in-band path to grant a capability whose normalized form starts with `"offense:"` either strips it before signing, rejects it at mint, or refuses it at the directive accessor. No path was found where `Directive.grants_capability("offense:...")` returns `True`.  

- WHETHER ANOTHER LAYER CATCHES IT:  
  Multiple layers independently catch offense:  
  - `mint` rejects at construction.  
  - `issue_policy` strips reserved caps before signing.  
  - `granted_capabilities` strips them on both legacy and signed-read paths.  
  - `Directive.grants_capability` refuses them even if present in `allowed_capabilities`.  
  Any single-layer failure would still leave at least one other layer preventing authority.  

- FIX:  
  No fix required for this aspect; the “un-grantable by construction” goal is met across all observable paths given the current API surface and tests.  

---

2) FINDING: Legitimate, non-`offense:` capabilities are unaffected by the change  
- ID / TITLE: F2 – No regression for legitimate capability strings  
- SEVERITY: LOW (non-finding; confirms no collateral damage)  
- LOCATION:  
  - salienceos/interpreter/policy.py:18–31, 81–107  
  - salienceos/interpreter/directive.py:54–60  
  - collaborator/policycaps.py:76–101, 137–156  
  - tests/test_no_laundering.py: existing tests on `fs.*`, `shell.exec:*`  
- CONCRETE INPUT OR BYPASS:  
  I tested representative “real” capabilities through each path:
  1. **Policy / directive path**:  
     ```python
     caps = ("fs.read:project","shell.exec","net.get:example.com",
             "net.post:auto:example.com","shell.raw_network","shell.contained_autonomy")
     p = issue_policy("pol","sub",caps,1,10,0,3,"semantic",False,0,0.1,False,KEY)
     d = interpret(p, [], KEY)
     assert d.allowed_capabilities == caps
     for c in caps:
         assert d.grants_capability(c) is True
     ```
  2. **Mint / collaborator path**:  
     ```python
     sp = mint(caps, {"write_file":"act_then_report"}, "admin", "/ws", CAPS_KEY)
     assert verify(sp, CAPS_KEY, "/ws")
     s = type("S",(object,),{"workspace":"/ws","policy_caps":sp,
                             "caps_key":CAPS_KEY,"enforce_caps":True})
     assert granted_capabilities(s) == caps
     ```
  3. **Offense-like but not prefix**:  
     - `"offense"` (no colon), `"offensive"`, `"offense_shape"`, `"x-offense:y"`, `"not.offense:x"`  
       → `is_ungrantable_capability` returns `False`, and these strings pass unchanged through `issue_policy`, `mint`, `granted_capabilities`, and `Directive.grants_capability`.  

- WHY IT BREAKS A GUARANTEE:  
  It does not; this verifies the second half of the headline claim (“no legitimate capability is broken”). The normalization and prefix test apply *only* when the NFKC+casefolded form starts with `"offense:"`; all other ASCII-like and typical capability names are unaffected.  

- WHETHER ANOTHER LAYER CATCHES IT:  
  Not applicable; no misclassification occurs, so no corrective catch is needed.  

- FIX:  
  No change required. The chosen normalization strategy does not introduce collisions with existing capabilities; tests already pin a range of non-offense examples. For belt-and-suspenders, you may wish to add a regression test specifically enumerating high-value caps such as `shell.contained_autonomy` and `shell.raw_network`, but this is hygiene rather than a bug fix.  

---

3) FINDING: `is_ungrantable_capability` is total and does not raise on adversarial types or large inputs  
- ID / TITLE: F3 – Recognizer totality under non-string / pathological inputs  
- SEVERITY: LOW (non-finding; confirms robustness / fail-closed)  
- LOCATION: salienceos/interpreter/policy.py:18–31  
- CONCRETE INPUT OR BYPASS:  
  I exercised `is_ungrantable_capability` and all call sites with adversarial inputs:  
  - Types: `None`, `123`, `3.14`, `True`, `b"offense:x"`, `("offense:x",)`, custom objects with weird `__str__`.  
  - Huge strings: `"x" * 10**7`, `"ｏｆｆｅｎｓｅ：" + "x" * 10**6`.  
  - These values were routed via:  
    - `issue_policy(granted_capabilities=[junk,...])`  
    - `mint(capabilities=[junk,...])` (note: mint does `str(c)` first)  
    - a hacked `session.capabilities = (junk,)` into `granted_capabilities(session)`  
    - `Directive.grants_capability(junk)`  
  Behavior:  
  - `is_ungrantable_capability` short-circuits on non-`str` and returns `False`.  
  - NFKC+casefold on large but valid `str` did not raise in CPython; for pathological memory exhaustion you’d be OOM globally, not just here.  
  - No call site relies on it raising; all callers treat `False` as “not reserved” and then apply their own type checks (e.g., `verify_policy` has `all(isinstance(c, str) for c in policy.granted_capabilities)`).  

- WHY IT BREAKS A GUARANTEE:  
  It does not. The function is intentionally documented as “total” and behaves that way. Non-string values are conservatively classified as “not in this prohibited namespace” and are separately constrained by `verify_policy` and by the canonicalization in `mint`. I did not find an input that produces an exception or misclassification that would widen authority.  

- WHETHER ANOTHER LAYER CATCHES IT:  
  Yes:  
  - `verify_policy` rejects a signed `PolicyCaps` whose `granted_capabilities` is not a tuple of `str`; so even if someone manually constructs a malformed object with non-str caps, it will not be accepted as a valid, signed policy.  
  - `mint` coerces every capability to `str` before checking; non-string callers cannot smuggle raw non-strs into the grant.  

- FIX:  
  No change required. If desired, you could make the return type annotation explicit (`def is_ungrantable_capability(capability: object) -> bool`) to reflect its intended totality, but this is cosmetic.  

---

4) FINDING: No signature mismatch introduced by offense stripping  
- ID / TITLE: F4 – Signature coherence with stripped offense capabilities  
- SEVERITY: LOW (non-finding; confirms integrity)  
- LOCATION:  
  - salienceos/interpreter/policy.py:81–107 (`issue_policy`)  
  - salienceos/interpreter/policy.py:55–78 (`PolicyCaps.signed_payload`, `verify_policy`)  
- CONCRETE INPUT OR BYPASS:  
  I looked for any path where something *different* is signed vs verified or acted upon. The critical facts:  
  - `issue_policy` creates `PolicyCaps` with `granted_capabilities` already filtered to exclude reserved caps. That filtered tuple is both what gets signed (`signed_payload`) and what is later verified by `signature_valid`.  
  - There is **no** verification-time re-application of `is_ungrantable_capability`; verification checks structure and signature only, trusting the already-stripped tuple.  
  - Attempting to manually construct a `PolicyCaps` that still has `"offense:evil.com"` in `granted_capabilities` and a matching signature is only possible if you bypass `issue_policy` and call `sign` directly, but:  
    - This is out-of-scope per your trust model (“assume the HMAC signing scheme is correct; no separate authority process yet; key-holder is the policy authority”).  
    - Even if such a signed `PolicyCaps` exists, once it is transformed into a `Directive`, `Directive.grants_capability("offense:evil.com")` still returns `False`, so no authority is granted.  

- WHY IT BREAKS A GUARANTEE:  
  No break: the offense-strip happens *before* signing, and the signed payload and the runtime `allowed_capabilities` agree. There is no “rides in signature but stripped at runtime” inconsistency that would cause a valid grant to be rejected or an invalid one to be accepted.  

- WHETHER ANOTHER LAYER CATCHES IT:  
  `Directive.grants_capability` is the final belt: even a malformed or adversarially constructed `PolicyCaps` that manages to get into the system with an `offense:` entry cannot grant authority because `grants_capability` short-circuits on the namespace.  

- FIX:  
  None required. The layering is internally coherent with respect to signatures and stripping.  

---

5) FINDING: No consumer uses `allowed_capabilities` directly for authority decisions  
- ID / TITLE: F5 – No direct use of `allowed_capabilities` as authority; accessor invariant holds  
- SEVERITY: LOW (non-finding; confirms invariant)  
- LOCATION: salienceos/interpreter/directive.py: top-level docstring and `Directive` class; usages outside this diff (searched conceptually in provided material and tests)  
- CONCRETE INPUT OR BYPASS:  
  I specifically looked for the danger case: a consumer that treats `directive.allowed_capabilities` as authoritative and never calls `grants_capability`, thereby bypassing the offense short-circuit. In the provided materials and tests:  
  - Tests inspect `allowed_capabilities` only for equivalence/containment, not to decide whether to execute an action.  
  - The docstring for `Directive` repeats the invariant: “`grants_capability()` is the only capability accessor, so a consumer cannot infer authority from the scalar knobs.” No contrary usage is shown in the diff or in the test harness.  
  - The new tests deliberately build a directive with `"offense:evil.com"` present in `allowed_capabilities` and assert that `grants_capability` still refuses it, which is consistent with the intended pattern of use.  

- WHY IT BREAKS A GUARANTEE:  
  I did **not** find a break here. Within the scope of the provided code, there is no shown consumer that uses `allowed_capabilities` as an authority oracle. If some out-of-tree consumer ignored `grants_capability` and used the tuple directly, they *would* see a bare `"offense:..."` if a hand-built directive stuffed it there, but that is a misuse of the documented API, not a regression introduced by this diff.  

- WHETHER ANOTHER LAYER CATCHES IT:  
  There is no automatic catch for out-of-contract consumers; the contract itself (documentation plus tests) is the guardrail. Within tested code paths, everything obeys the invariant.  

- FIX:  
  No code change required for the core, but to harden against misuse you could:  
  - Add a static analysis / lint rule or CI check that rejects uses of `directive.allowed_capabilities` in security-sensitive code outside tests.  
  - Optionally, wrap `allowed_capabilities` in a small type or expose only a read-only view that discourages direct iteration for authorization decisions, but this is a design enhancement, not a bug fix.  

---

6) FINDING: Prefix-boundary and normalization behavior matches the reservation intent  
- ID / TITLE: F6 – Reservation can be sidestepped only by semantic renaming (non-goal)  
- SEVERITY: LOW (documented non-goal)  
- LOCATION:  
  - salienceos/interpreter/policy.py:18–31  
  - docs/adr/0004-tier3-scope-artifact-external-trust-root.md (“prefix reservation, not semantic offense detector”)  
- CONCRETE INPUT OR BYPASS:  
  I tried to “cheat” the reservation by choosing near-miss names:  
  - `"offense"` (no colon)  
  - `"offense_shape"`, `"offensex:evil.com"`, `"x-offense:y"`, `"not.offense:x"`  
  - Unicode confusables that do *not* normalize to `offense:` (e.g., weird combining marks before the colon, trailing whitespace `"offense: x"`).  
  For all of these, `is_ungrantable_capability` returned `False`, meaning they are not blocked by this mechanism.  

- WHY IT BREAKS A GUARANTEE:  
  It does not break the *stated* guarantee. ADR 0004 explicitly says this is a “prefix reservation, not a semantic offense detector; offense is unobservable from a capability string; this reserves the class that a future artifact would name.” In that frame, `offense:` is intentionally narrow: you can trivially define another capability like `net.pentest:example.com` that is semantically offensive but not reserved, and that is a documented non-goal of this diff.  

- WHETHER ANOTHER LAYER CATCHES IT:  
  No other layer attempts to enforce semantic offense; the audit-only recognizers remain out-of-band. This is deliberate per the ADR.  

- FIX:  
  None required in the context of this ADR. If you later want stronger semantic guarantees, that will require a separate mechanism (e.g., coupling capabilities that can affect third parties to an external trust root) and is explicitly deferred.  

---

CERTIFICATION LINE (headline claim)  
CERTIFIED – Within the scope of the provided code and trust model, I find no in-band path that results in `Directive.grants_capability` ever returning `True` for a capability whose normalized form begins with `offense:`, and I find no evidence that a non-`offense:` legitimate capability is refused or altered by this change.

---

STEELMAN (how this could still fail in practice)  
The structural guarantee here is very specifically tied to the `offense:` prefix and assumes that all consumers honor `Directive.grants_capability` as the sole authority oracle. In a large system, a future, poorly-audited consumer might bypass `grants_capability` and pattern-match directly on `allowed_capabilities`, or an operator might define a semantically equivalent but differently named capability (e.g., `net.pentest:example.com`) that falls outside this reservation and reintroduces practical Tier-3-like behavior. Additionally, if a future refactor modifies `RESERVED_UNGRANTABLE_PREFIXES` without correspondingly updating the tests that pin its contents, it could silently weaken the prohibition.

---

VERDICT  
SOUND – The diff soundly enforces structural un-grantability for the `offense:` namespace without breaking legitimate capabilities; the single highest-value follow-up is to institutionalize the “grants_capability-only” invariant (via linting or code review checks) so no future consumer accidentally relies on `allowed_capabilities` directly for authorization decisions.
