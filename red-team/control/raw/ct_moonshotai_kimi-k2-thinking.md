# Red-team (control seam): moonshotai/kimi-k2-thinking

_finish=stop seconds=330.0 usage={'prompt_tokens': 13074, 'completion_tokens': 12769, 'total_tokens': 25843, 'cost': 0.0397669, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0397669, 'upstream_inference_prompt_cost': 0.0078444, 'upstream_inference_completions_cost': 0.0319225}, 'completion_tokens_details': {'reasoning_tokens': 11598, 'image_tokens': 0, 'audio_tokens': 0}}_

**FINDING 1: Verdict Confusion in `decide` allows cross-action clearance (HIGH)**

*   **ID:** GOV-001
*   **LOCATION:** `salienceos/control/govern.py`, function `decide`
*   **CONCRETE TRIGGER:**  
    ```python
    # Verdict for action A (verified at HIGH stakes)
    verdict_a = Verdict(status=Status.VERIFIED, reasons=(), ...)
    
    # Directive and envelope for action B (LOW stakes, subject "B")
    directive_b = Directive(subject="B", verification_depth=NONE, ...)
    
    # Buggy caller passes verdict_a for envelope_b
    outcome = decide(directive_b, "B", Stakes.LOW, verdict_a, Stakes.HIGH)
    # outcome.cleared is True, but action B was never verified
    ```
*   **WHY IT MATTERS:**  
    `decide` is exported as a public pure function for testing, but it does not validate that the `verdict` argument actually corresponds to the `envelope_id` (and `envelope_stakes`) provided. A buggy or confused caller (e.g., a caching layer that replays stale verdicts, or a race-condition bug) can pass a `VERIFIED` verdict from a high-stakes action A to clear a low-stakes (or completely different) action B. This violates **Invariant 2 (Fail-closed clearance)** because the action is cleared without having been verified at the required level for *that specific action*. The `verdict` object lacks an `envelope_id` field to bind it to the action it certifies.
*   **SUGGESTED FIX:**  
    Add `envelope_id: str` to the `Verdict` dataclass (populated by `compose` from the contract), and assert `verdict.envelope_id == envelope_id` in `decide` before processing clearance.

---

**FINDING 2: `max_stakes` crashes on non-enum input (LOW)**

*   **ID:** ENV-001
*   **LOCATION:** `salienceos/verifier/envelope.py`, function `max_stakes`
*   **CONCRETE TRIGGER:**  
    ```python
    # Buggy caller passes a string instead of Stakes enum
    max_stakes(Stakes.HIGH, "HIGH")  # Raises ValueError
    # or in Verifier.verify via escalate_to="HIGH"
    ```
*   **WHY IT MATTERS:**  
    A misfiring subsystem passing an incorrectly typed `escalate_to` argument (e.g., a string literal `"HIGH"` instead of `Stakes.HIGH`) causes an unhandled `ValueError` crash rather than failing closed to a safe `UNVERIFIED` verdict. While this does not weaken verification (it denies service), robust fail-closed behavior should handle malformed inputs gracefully.
*   **SUGGESTED FIX:**  
    Add defensive type checking: `if not isinstance(a, Stakes): return b` (and vice-versa), or raise a specific `TypeError` caught by the caller to return `UNVERIFIED`.

---

**FINDING 3: `decide` crashes on None inputs (LOW)**

*   **ID:** GOV-002
*   **LOCATION:** `salienceos/control/govern.py`, function `decide`
*   **CONCRETE TRIGGER:**  
    ```python
    decide(None, "id", Stakes.LOW, verdict, Stakes.LOW)  # AttributeError on directive.subject
    decide(directive, "id", Stakes.LOW, None, Stakes.LOW)  # AttributeError on verdict.status
    ```
*   **WHY IT MATTERS:**  
    As an exported function, `decide` should defend against `None` inputs from buggy callers. Currently it raises `AttributeError` instead of failing closed.
*   **SUGGESTED FIX:**  
    Add explicit `None` checks at the start of `decide` and return a `GovernedOutcome` with `cleared=False` and appropriate reasons.

---

**STEELMAN (What is genuinely right):**  
The escalation logic is rigorously upward-only: `max_stakes` uses a total order that cannot be subverted to lower scrutiny, and `decide` defensively re-applies `max_stakes(envelope_stakes, effective_stakes)` to ensure the policy-signed floor is respected even if a buggy caller passes a lowered `effective_stakes`. The mapping between `VerificationDepth` and `Stakes` is total and consistent: `_stakes_floor` correctly translates the envelope's stakes to a required level, while `achieved_level` correctly interprets the verdict's status and the effective stakes to determine what was actually proven. The adaptation gate strictly requires `Status.VERIFIED`, preventing any learning from merely `INTEGRITY_ATTESTED` receipts, satisfying the sealed-gate invariant. The `decide` function is purely functional with no side effects, making it deterministic and testable.

**VERDICT:** **MINOR_ISSUES** — The implementation correctly upholds the three core invariants for the intended call path (`govern` → `decide`). The identified issues are confined to the exported `decide` function's vulnerability to confused/deputy callers (which should be mitigated by adding envelope_id to Verdict) and robustness against malformed inputs (type safety). No flaw allows an in-scope adversary to lower scrutiny, bypass clearance without verification, or open the adaptation gate without a verified verdict when using the primary `govern` entry point.
