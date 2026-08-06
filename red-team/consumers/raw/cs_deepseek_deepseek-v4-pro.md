# Red-team (consumers): deepseek/deepseek-v4-pro

_finish=length seconds=237.8 usage={'prompt_tokens': 34135, 'completion_tokens': 12000, 'total_tokens': 46135, 'cost': 0.038438862, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.038438862, 'upstream_inference_prompt_cost': 0.022570062, 'upstream_inference_completions_cost': 0.0158688}, 'completion_tokens_details': {'reasoning_tokens': 12000, 'image_tokens': 0, 'audio_tokens': 0}}_

We need to red-team the implementation for defects under the stated invariants. The focus is on the concrete code, looking for paths that violate invariants, create inhibitor misattribution, allow unverified content nomination, cause crashes on boundary, bus issues, test honesty, etc. We need to produce findings with concrete triggers.

Let's carefully examine each module and the tests. The invariants:

- P-01: salience influences, policy authorizes. grants_capability() only accessor. That seems structurally enforced: allowed_capabilities is a pass-through from policy, no signal touches it. However, is there any path where a signal could affect capability? Interpreter's interpret() only sets allowed_capabilities=tuple(policy.granted_capabilities). So that's fine.

- Finding C: memory and weight-adaptation channels strictly separate AND able to DISAGREE. That's architectural: they are separate modules, only share InhibitorHandoff. The handoff is explicitly passed. The import-graph test confirms they never import each other. But can we manufacture an inhibitor without an asserted over-cap risk? Or lose one despite one? Or misattribute?

Review how inhibitor handoff is created and matched. In adaptation.py, nominate() creates an InhibitorHandoff only when the RECORDED rationale is RISK_EXCEEDED. That checks `d.adaptation_rationale`. In memory.py, retain receives handoff and validates that the directive's adaptation_rationale is RISK_EXCEEDED, that the handoff's subject matches, source matches, etc. So attribution seems tight.

But could there be a scenario where the interpreter records RISK_EXCEEDED but the actual risk wasn't over the cap? That's a logic error in interpreter. In interpret(), the check for RISK_EXCEEDED vs RISK_UNKNOWN:

```python
        risk = agg.get(Facet.RISK, 1.0)
        ...
        if not policy.allow_adaptation:
            rationale = AdaptationRationale.POLICY_DISALLOWED
        elif not agg.get(Facet.ADAPTATION, 0.0) > 0.0:
            rationale = AdaptationRationale.NOT_REQUESTED
        elif v_depth < policy.adaptation_min_verification:
            rationale = AdaptationRationale.UNDER_VERIFIED
        elif risk > policy.adaptation_max_risk:
            rationale = (AdaptationRationale.RISK_EXCEEDED if Facet.RISK in agg
                         else AdaptationRationale.RISK_UNKNOWN)
        else:
            adaptation = AdaptationEligibility.CANDIDATE
            rationale = AdaptationRationale.ELIGIBLE
```

Here, `risk` is agg.get(RISK, 1.0). So if there is no RISK signal, risk=1.0. The check `if risk > policy.adaptation_max_risk` will be True if max_risk < 1.0. Then it checks `if Facet.RISK in agg`. If there's no RISK signal, Facet.RISK not in agg, so `rationale = AdaptationRationale.RISK_UNKNOWN`. That's correct.

If there is a RISK signal with low risk (e.g., 0.1), risk=0.1, and max_risk=0.4, then risk > max_risk is False, so it proceeds to make adaptation CANDIDATE. So far so good.

Now, what if there is a RISK signal with high risk (0.9) > max_risk (0.4), and also Facet.RISK in agg True, so RISK_EXCEEDED is recorded. That seems correct.

But what if the RISK signal's confidence is zero? The aggregation `_aggregate` groups signals by facet, and if weight (sum of confidences) > 0, it computes the weighted mean. If a RISK signal has confidence zero, and there are no other RISK signals, weight = 0, so the facet is omitted entirely from agg. Then risk defaults to 1.0, and Facet.RISK not in agg -> RISK_UNKNOWN. So zero-confidence risk signals are ignored, which is correct per design: no information. However, if there is another RISK signal with positive confidence, the zero-confidence one won't affect the mean (weighted mean, confidence zero => term zero, weight sum includes zero? Actually, the aggregation sums confidences as weight, so zero confidence adds to weight? Let's see:

```python
    for facet, group in by_facet.items():
        weight = sum(s.confidence for s in group)
        if weight > 0:
            agg[facet] = sum(s.influence * s.confidence for s in group) / weight
```

If there is a signal with confidence 0, it adds 0 to weight, but still is in the group. weight sum of all confidences includes zero; if all confidences are zero, weight=0 and facet omitted. If there is also a signal with positive confidence, weight>0, the zero-influence signal contributes 0 to the weighted sum, but it still affects the weight? Actually, the weight is sum of all confidences, so a zero-confidence signal does not change the weight denominator because its confidence is 0. So the aggregated influence would be based solely on the non-zero confidence signals. The zero-confidence signal is effectively ignored. The question: Does its presence count for Facet.RISK in agg? Facet.RISK in agg will be True because the group list is non-empty; but wait, `by_facet` only includes signals with `s.facet in KNOWN_FACETS`. So if there's at least one RISK signal with any confidence (including zero), it will be in the group. Then `if weight > 0` checks if sum of confidences > 0. If all confidences are zero, weight=0, and we don't set agg[facet]. So Facet.RISK would NOT be in agg. So that's consistent: zero-confidence signal does not assert risk. So no issue.

Now, could there be an inhibitor triggered for a subject that didn't actually have an adaptation request? The interpreter checks `elif not agg.get(Facet.ADAPTATION, 0.0) > 0.0` before the risk check. So if no ADAPTATION signal with positive influence? The condition is `not agg.get(Facet.ADAPTATION, 0.0) > 0.0`. agg.get(ADAPTATION) could be 0.0 (or absent => 0.0). So if it's 0.0 or absent, `not 0.0 > 0.0` is True, so it goes to NOT_REQUESTED. So a risk_exceeded would only be recorded when ADAPTATION > 0.0 AND risk > cap. So inhibitor is tied to an adaptation request, which is correct.

But could we trigger an inhibitor through an otherwise invalid policy that still yields RISK_EXCEEDED? The interpreter's early hard deny (policy unsigned/invalid) yields POLCY_DISALLOWED, not RISK_EXCEEDED. So that's safe.

Now, a possible defect: the interpreter's priority chain records UNDER_VERIFIED before checking risk. If v_depth < adaptation_min_verification, it goes to UNDER_VERIFIED, skipping risk check entirely. That's intentional: under-verification prevents adaptation regardless of risk, and they want to not manufacture an inhibitor for under-verified requests. So if a request has high risk but insufficient verification, the rationale will be UNDER_VERIFIED, not RISK_EXCEEDED. That's correct per tests. So no inhibitor is produced. But is it possible that despite v_depth < min_verification, the risk is still over cap and we might miss an incident? The design says: under-verified means we can't trust the risk signal enough to act on it as an inhibitor? But wait: risk signal is used to compute verification depth (verif_salience = max(risk, verify_request)). So if risk is high, verification depth should be pushed up. But if the policy's max_verification is lower than adapt_min_verification? The policy validation in verify_policy() requires `adaptation_min_verification <= max_verification`. So that is prevented. However, there's a subtle case: the verification depth formula uses `risk` (which could be high) to compute verif_salience, and then rounds. It is possible that the computed v_depth falls short of adapt_min_verification even with high risk if max_verification is low and min_verification is 0? But adapt_min_verification is <= max_verification. If risk is high, v_depth will be at least min_verification + round_half_up(risk * span). Since risk<=1.0, it could be anywhere from min to max. So it is possible that high risk still yields v_depth less than adaptation_min_verification? For example: policy: min_ver=0, max_ver=2 (INDEPENDENT), adapt_min_ver=2. So adaptation min is 2. With risk=0.9, verif_salience=0.9, span=2, v_depth = 0 + round(0.9*2)=round(1.8)=2. So meets min. If risk=0.4, 0.4*2=0.8 -> round up=1, so v_depth=1. So under-verified. So high risk tends to increase depth, but if risk is 0.5 in that scenario, depth=1, but risk > cap? But if risk 0.5, that might be over cap 0.4. Then v_depth=1, adapt_min_ver=2, so UNDER_VERIFIED. That's deliberate: if verification depth is insufficient, we don't trust the risk signal enough to treat it as an incident because we haven't verified deeply. So that's intentional. So no defect there.

Now, finding C: channels must be able to DISAGREE. That is tested in test_consumers_e2e: high risk leads to inhibitor in memory and hard block in weight. The implementation appears correct.

Next, nomination predicate: docx says "nomination predicate is exactly bool(outcome.adaptation_allowed)". In nominate() function, the only true path is `if outcome.adaptation_allowed`. That's it. It does not check anything else. So that's correct. But does nominate() ever nominate for a subject that wasn't verified? outcome.adaptation_allowed is set by decide() only when cleared, directive CANDIDATE, and verdict.status is VERIFIED. So it requires VERIFIED. So unverified cannot be nominated. Could a hand-crafted outcome with adaptation_allowed=True bypass? The test test_nomination_predicate constructs a forged outcome where adaptation_allowed=True but underlying verdict is UNVERIFIED. The gate still nominates because it follows the field. That's out of scope (forge) but the code does follow the field. So no leak.

However, there's a possible subtlety: what if the directive has eligible CANDIDATE but the seam denies adaptation_allowed due to non-VERIFIED, but the consumer still could nominate if someone misuses the adaptation_allowed field? That's a caller problem. Not a defect.

Now, capability leak through consumers: MemoryRetention and AdaptationDecision have no capability field; they are internal records. They don't carry allowed_capabilities. The outcome's directive carries capabilities but that's not leaked by consumers; they only use subject, rationales, etc. So safe.

Next, the seam boundary: we need to see if a directive or outcome shape can crash the gates. The gates have type fences and fail-closed handling. In nominate():

```python
    if type(outcome) is not GovernedOutcome:
        raise TypeError(...)
    d = outcome.directive
    if d is None or not outcome.subject:
        return AdaptationDecision(subject="", nominated=False, ...)
    ...
```

So it checks for None directive or empty subject. Then later it accesses `d.adaptation_rationale`. The directive could be a real Directive but its adaptation_rationale could be something unexpected? The Directive dataclass has adaptation_rationale: AdaptationRationale (enum). But via forge, it could be anything. However, nominate() checks `if not isinstance(rationale, AdaptationRationale):` and handles it. So no crash. Also in retain(), similar checks.

But could an adversary construct a directive that passes the seam (decide()) with a valid AdaptationRationale but then the outcome's directive is None? That's only possible if decide() returns outcome with directive=None. As we saw, decide() sets directive=None only when unbound or invalid. So then nominate() sees d is None, returns early. So no crash.

One potential: In nominate(), after checking d is None and subject, it does `rationale = d.adaptation_rationale`. d is a Directive, which is frozen; adaptation_rationale is an AdaptationRationale enum. No crash.

In retain(), they access d.retention_class, which is a string. It could be an invalid string? They check `if bound and d.retention_class in RETENTION_ORDER` else floor. So no crash.

So gates are robust.

Now, the bus: adversarial files within ADR 0001 scope: accidental corruption is in scope, consistent rewrite is out. The replay and verification functions should catch accidental corruption. The bus's _replay function does:

- Read lines, parse JSON, check keys exactly {"kind","payload","prev","hash"} and payload is dict, prev matches, digest matches.
- For signal kind: reconstruct SalienceSignal and validate with valid_signal. Reject if fails.
- For directive: just store payload dict; no further validation beyond payload being dict and key set. But directives_for returns copies.

Is there any way an adversarial file could cause a crash (like a malicious JSON that causes a raise) that is caught? The code uses try...except around load and checking. If JSON loading fails (malformed line), it sets intact=False and raises ValueError. So that's good. The replay is fail-closed.

One possible issue: The exact key set check `set(e) == {"kind", "payload", "prev", "hash"}` is vulnerable if the JSON contains duplicate keys? JSON spec says keys should be unique, but Python's json.loads will use the last value. So if an attacker crafts a line with duplicate keys, the resulting dict might have fewer keys? Actually, duplicate keys will result in one key, so set might be subset? For example, if the line has "kind": "signal", "payload": ..., "kind": "something", then the result dict will have "kind" set to the last value, and the set will be exactly those four keys. So the duplicate won't cause an extra key. So no smuggling.

However, what about the `isinstance(e["payload"], dict)` check? That's fine.

Now, the bus's _replay for signal validates the signal. But what about directive payload? It doesn't reconstruct Directive; it just stores dict. That's okay because directives_for just returns copies. Nothing else relies on it being a valid Directive. The emit() method only accepts a Directive object, so the stored payload came from emit previously and is safe. For adversarial bus file, an attacker could craft a malicious directive line that passes the exact key check and payload is dict, but contains malicious data. That could be replayed and served by directives_for. However, that data is not used for any authorization; it's just passive record. But could it affect consumers? No, consumers read from the bus indirectly? The bus is not used by the consumers in this scope. The bus is a record. The consumers operate on GovernedOutcome from the seam, not from the bus. So that's out of scope for consumer gates. However, the bus is used to replay signals for interpretation? Actually, the bus stores signals and directives. In a full system, one might replay signals and then interpret. But in this branch, the consumers don't consume from the bus. So the bus's integrity is about accidental corruption. The _replay will reject any line that doesn't match hash or key set. That's good.

Potential subtle: The _replay's JSON load uses `json.loads(line)`. If the line contains a very large string, it could consume memory, but that's a DoS, not a correctness issue. The scope says accidental corruption; we don't need to consider DoS unless it leads to a crash. The code doesn't limit line size. That's a minor robustness issue but not a high severity in this red-team scope unless it can be used to break invariants. Could a huge payload bypass the key set check? No, it would still be in payload.

Are there any logic errors where the bus fails to detect corruption? For example, if the file is empty, _replay does `lines = [ln for ln ... if ln]`. Empty file leads to empty lines, no entries. That's fine. If the file contains only whitespace lines? It skips them. So empty bus starts with head "".

But suppose an attacker injects a correctly formed line that is a signal but with an empty subject? valid_signal requires _ref_token on subject, which checks non-empty and length <=128. So empty subject fails validation. So the replayed signal must have non-empty subject. That's good.

Now, could an attacker craft a signal that passes valid_signal but has malicious content in provenance? provenance must be tuple of ref tokens (strings, non-empty, <=128). So no large strings. So audit fence structural.

But the bus's exact key set check: `set(e) == {"kind", "payload", "prev", "hash"}`. Is it possible for an attacker to include extra keys that are not strings? In JSON, keys must be strings. So only strings. So this is fine.

Now, tests: We need to see if any test claims an invariant that would stay green if the code were sabotaged. For instance, test_handoff_attribution_matrix tests various mismatches and asserts HandoffMismatchError. If the retain function was modified to not raise but silently drop, tests might still pass? No, those tests assert raises. So they'd fail. So that's honest.

But maybe there's a test that isn't actually testing the claimed invariant. For example, test_eligible_but_not_allowed_is_unverified_novelty in NominationPredicate: it uses attested_eligible_outcome, which yields outcome.adaptation_allowed=False. The test checks that nominate returns false and reasons contain "unverified_novelty_excluded". That's fine.

But could we sabotage the interpreter's rationale so that RISK_EXCEEDED is recorded for a subject that had no ADAPTATION signal? The priority chain prevents it. But what about a scenario where a RISK signal is present but influence is exactly 0? The aggregation: influence=0.0, confidence positive. Then agg[RISK] will be 0.0. So risk=0.0. So it would be below cap. So no inhibitor. That's fine.

Now, the finding D: consumer gates consume recorded decision, never re-derive. That's tested: test_the_only_true_path_is_adaptation_allowed shows that even with forged outcome where adaptation_allowed=True, the gate nominates. That's correct.

But could there be a path where the inhibitor handoff is created but the recorded rationale is not RISK_EXCEEDED? In nominate(), it checks `if rationale is AdaptationRationale.RISK_EXCEEDED:`. So yes.

Now, seam boundary: could a directive with a subject that is an empty string bypass something? In decide(), they check `bool(directive.subject)` for binding. directive.subject is "" -> bound = False, and they treat as unbound, which leads to directive=None in outcome. So safe.

But what if the directive has subject set but the verdict's envelope_id is also empty string? They check `directive.subject == verdict.envelope_id`. If envelope_id is "", then it could match a directive with subject ""? But decide() checks `bool(directive.subject)`? No: `bound = bool(directive.subject) and directive.subject == verdict.envelope_id`. So if directive.subject is "", bound=False. So no. Good.

Now, a concerning area: the control seam's _valid_directive checks for `isinstance(directive.verification_depth, int) and not isinstance(directive.verification_depth, bool)`. That's for the seam. But in govern(), it also calls _valid_directive(directive) before running verifier. So a malformed directive is denied early.

But what about the adaptation_rationale validation in _valid_directive: it checks that (rationale is ELIGIBLE) == (eligibility is CANDIDATE). That's strict. So a forged directive with ELIGIBLE but eligibility NONE will be rejected.

Now, could we crash the seam by providing a directive with a huge string in subject? Directive.frozen, but no limit on subject length. However, the seam's decide() doesn't do any large string processing that could crash, just comparisons. So no.

Now, I need to look for concrete defects that could violate invariants. Let's scrutinize the interpreter's adaptation priority chain again. There's a subtle bug: The chain checks `not agg.get(Facet.ADAPTATION, 0.0) > 0.0`. The get returns 0.0 if not present. Then `not 0.0 > 0.0` is True. That's correct. But what if the ADAPTATION signal has influence 0.0 and confidence > 0? Then agg[ADAPTATION] will be 0.0. Condition: `not 0.0 > 0.0` is True, so it goes to NOT_REQUESTED. That's correct: no positive request.

But what if there is an ADAPTATION signal with influence > 0 but the aggregation weight is zero? The aggregation only includes if weight > 0; so if all ADAPTATION signals have confidence zero, they are omitted, and then not requested. So correct.

Now, what about the risk default: risk = agg.get(Facet.RISK, 1.0). If there are RISK signals but all have confidence zero, they are omitted from agg, so risk=1.0, and Facet.RISK not in agg, so RISK_UNKNOWN. That's fine. However, note: the interpreter still computes verification depth using risk=1.0, which may increase v_depth. That could cause an under-verified check to pass. If no RISK signal, risk=1.0, so verif_salience = max(1.0, verify_request). That pushes verification up, making the system more cautious. That's intentional per design: absent risk => assume high risk => verify thoroughly.

But could that cause a false inhibitor via RISK_EXCEEDED? No, because Facet.RISK not in agg, so it's RISK_UNKNOWN. So safe.

Now, another potential issue: In adaption gate's nominate, after checking `if outcome.adaptation_allowed`, it returns a nomination with rationale=d.adaptation_rationale. That's ELIGIBLE. That's fine.

But what about the handoff construction: it uses `rationale=d.adaptation_rationale.value`. That's a string. Then in retain, the check: `handoff.rationale == AdaptationRationale.RISK_EXCEEDED.value` and `d.adaptation_rationale is AdaptationRationale.RISK_EXCEEDED`. So both must match.

Now, check the risk_unknown_outcome fixture: it uses directive with rationale=RISK_UNKNOWN. The nominate() should produce no handoff. Let's test: nominate(risk_unknown_outcome) -> rationale is RISK_UNKNOWN, not RISK_EXCEEDED, so goes to else branch, reasons = (rationale.value,). No handoff. So correct.

Now, are there any edge cases where the inhibitor handoff is created but the memory governor's attribution check fails because of case mismatch? The handoff.rationale is set from AdaptationRationale.RISK_EXCEEDED.value which is "risk_exceeded". In retain, they compare `handoff.rationale == AdaptationRationale.RISK_EXCEEDED.value`. That's fine.

Now, consider the memory governor's decay: effective_weight uses half-life decay. The HALF_LIFE_DAYS dict has keys for retention order. If retention_class is a valid class not in HALF_LIFE_DAYS? The RETENTION_ORDER and HALF_LIFE_DAYS are defined with matching keys: ephemeral, working, episodic, semantic. So all valid classes. The get defaults to ephemeral's half-life. So no KeyError.

Now, a potential CRITICAL bug: The bus's _replay function for directives does NOT validate that the payload contains the required fields or that they have correct types, but that's fine because it's just data. However, directives_for returns dicts; it does deep copies via json round-trip. That ensures immutability. But could an attacker inject a directive line with a "subject" field that is not a string, causing a crash in directives_for? directives_for does `p.get("subject") == subject`. If p["subject"] is not a string, .get returns that value, and comparison could raise? Actually, p.get("subject") could be None if missing, but p is a dict, .get returns None, and comparing None to a string via `==` is safe (False). No crash. But what if the payload dict contains non-serializable objects? The replay loads from JSON, so all values are JSON types, so safe.

Now, let's look at the interpret function's handling of one-shot iterator and exception: `try: signals = tuple(signals) except Exception: signals = ()`. That's fine.

Now, consider the overall system: There is a path where the adaptation eligibility is CANDIDATE, but the world verification fails (UNVERIFIED). The seam's decide sets adaptation_allowed=False because verdict.status is not VERIFIED. The nominate() will then see adaptation_allowed=False and go to the refusal branch. The rationale from directive is ELIGIBLE. The code:

```python
    elif rationale is AdaptationRationale.ELIGIBLE:
        reasons = ("unverified_novelty_excluded",)
    else:
        reasons = (rationale.value,)
```

That's correct. But does that create a handoff? No. So no inhibitor. Good.

Now, is there any way that the memory governor's handoff attribution can be bypassed by setting the handoff's subject to something else? It raises HandoffMismatchError, so it's safe.

Now, a subtle bug: In the control seam's decide function, the required level is computed as max(directive.verification_depth, _stakes_floor(effective_stakes)). However, _stakes_floor depends on effective_stakes which comes from the verdict. But what if effective_stakes is not a Stakes enum member? _stakes_floor handles that: if not isinstance(stakes, Stakes): return FULL. So fails closed. Good.

But in decide, they also have a range guard: `required = NONE if required < NONE else FULL if required > FULL else required`. Since verification_depth is from directive (int, but validated not bool), and _stakes_floor returns int 0-3, this is fine.

Now, a potential issue: In govern(), they call `escalate = escalation_for(directive.verification_depth)` and pass to verifier. They also call `decide(directive, verdict)`. In decide, they compute required level using effective_stakes from verdict. So the two policies (escalation and stakes floor) are consistent.

Now, look at the test for smuggling key on bus: test_smuggled_key_refuses_to_open. That's good.

But is there a scenario where the bus's _replay succeeds on a file that has been tampered in a way that the hash chain is still valid but the entry's payload contains a "prev" that does not match the previous hash? The check inside the try block: `e["prev"] == prev`. So it's covered. Also, they compute `base = {"kind": e["kind"], "payload": e["payload"], "prev": e["prev"]}`; then digest(base) vs e["hash"]. This ensures that the hash was computed over exactly those three fields. The exact key set check ensures no extra fields. So the chain is tight.

Now, what about the initial head? In a new bus, head is "" (empty string). When reopening a file, prev starts as "". So the first entry must have prev "". So if the file is empty, head remains "" and no verification issues. So that's fine.

Now, consider the bus's emit function: it records a directive payload. The payload includes "adaptation_eligibility" and "adaptation_rationale" as values (enum.value). So they're strings. That's fine.

Now, could there be a mismatch between the interpreter's recorded rationale and what the consumer sees if the directive is tampered between interpret and emit? The emit is called by the orchestrator, presumably right after interpret. So no.

Now, the red-team instructions ask to find defects that the two internal reviews missed. Let's think about more subtle bugs.

Potential bug: In the interpreter, the adaptation rationale is chosen based on a priority chain. But the chain's order may produce an incorrect rationale in some corner cases. Specifically, the condition for reaching RISK_EXCEEDED is after checking `v_depth < policy.adaptation_min_verification`. If v_depth >= adaptation_min_verification, then risk > max_risk triggers RISK_EXCEEDED if RISK in agg. However, what if risk > max_risk but also the policy disallows adaptation? That's earlier, POLICY_DISALLOWED. So fine.

What if risk > max_risk but also there is no adaptation request? That's earlier, NOT_REQUESTED. So fine.

But there is a subtlety: The adaptation request condition is `not agg.get(Facet.ADAPTATION, 0.0) > 0.0`. If there is an ADAPTATION signal with influence 0.0 and confidence positive, then agg[ADAPTATION] = 0.0. The condition is `not 0.0 > 0.0` => True, so it treats as not requested. So a zero-strength request is ignored. That's likely intended: adaptation request must be positive. But what if the signal has influence -0.1? influence is validated to be [0,1] by valid_signal. So negative can't happen. So safe.

Now, a possible flaw: The interpreter aggregates signals from any subsystem, but does not check for conflicting subsystem IDs. That's fine; they are all combined.

Now, consider the nomination gate: it doesn't check that the nomination is for offline review; it just sets nominated=True. That's fine.

Now, could an inhibitor handoff be created for a directive that has RISK_EXCEEDED but the outcome is not cleared? I.e., the action itself failed verification. Then the seam's decide would have verdict.status=FAILED -> adaptation_allowed=False. The nominate() would still get a non-nil outcome with directive carrying RISK_EXCEEDED. Would it still create handoff? Yes, because it checks `rationale is AdaptationRationale.RISK_EXCEEDED`. The outcome's adaptation_allowed is False, so it enters the refusal branch and checks rationale. So it would create a handoff even if the action failed? Let's see: if the action failed, the memory governor might receive a handoff for a failed action, and the inhibitor would be pinned. Is that correct per design? The docx 4.4: "preserved as an incident or inhibitor, not learned as a capability" — it says high-salience high-risk content is a memory RETAIN and weight HARD BLOCK. It does not say the action must be cleared. So even if the action itself failed, the risk signal might still indicate an attempted malicious or dangerous operation, and that should be remembered as an inhibitor. So that seems correct.

But let's check if the memory governor's retain would accept handoff for an uncleared outcome. The handoff attribution only checks the directive's rationale and subject matching. It does not check cleared bit. So it would accept. That's consistent with the design: inhibitors may retain uncleared content for incident preservation. The MemoryRetention record has a `cleared` field to indicate if the action was cleared, but the inhibitor flag is orthogonal.

So that's fine.

Now, what about the case where the directive's risk_exceeded rationale is present, but the outcome's subject is empty (unbound)? Then nominate would early return with empty subject and no handoff, because d is None or not outcome.subject. So no inhibitor leak.

Now, the consume seam calls nominate then retain with handoff=decision.handoff. If nominate's handoff is None, retain gets None; if not None, passes it. That's correct.

Now, test_disagreement_flows_through_consume ensures that for risk_reject outcomes, consume yields inhibitor and block. That ties the channels.

Now, let's examine the e2e test more carefully. It uses a real subprocess action. The interpret uses signals with RISK=0.9 and MEMORY=0.9, etc. It checks the directive's retention_class is "semantic" (max retention) and adaptation_rationale is RISK_EXCEEDED. Then act runs and returns outcome cleared and adaptation_allowed=False. So far so good. consume yields decision with handoff and retention with inhibitor. So e2e passes.

Now, potential defect: the adaptation gate's nominate produces a handoff with `rationale=rationale.value`. In the handoff, we set `rationale=rationale.value` which is "risk_exceeded". But in the handoff definition, `rationale: str`. That's okay. In retain, they compare `handoff.rationale == AdaptationRationale.RISK_EXCEEDED.value`. That's consistent.

But could an attacker manipulate the outcome's directive such that it carries RISK_EXCEEDED but the handoff attribute is set to a different subject? The handoff is constructed inside nominate with `subject=outcome.subject`. So it's tied.

Now, one more area: the control seam's _valid_directive checks that adaptation_rationale is an AdaptationRationale instance. However, in nominate(), they do `if not isinstance(rationale, AdaptationRationale):` as a belt. So that's belt-and-suspenders.

Now, I'm struggling to find a CRITICAL or HIGH flaw. Let's think about the bus's replay and the "prompt-sized" question. The audit fence ensures that no field can hold a prompt because all string fields are bounded (ref tokens <=128, provenance tuple of tokens). That's structural. The bus's emission of directive payload includes things like subject, policy_id, etc., which are also strings from policies. Could subject be longer than 128? Policy's subject is just a string; there's no length limit in issue_policy. So a policy could have a subject longer than 128, and that would be recorded in the bus. The bus's directive payload does not restrict subject length. So someone could craft a policy with a huge subject string (e.g., 1MB) and that would be stored in the bus, making the file large. But that's a DoS, not a correctness issue. However, the invariants mention "can anything prompt-sized become durable?" The scope says audit fence: can prompt-sized become durable? The bus is the durable record. If a policy's subject can be of arbitrary length, then a prompt-sized string could be placed into the directive payload and thus into the bus file. That would violate the audit fence? The audit fence is about signals: the signal's fields are bounded, but the directive payload is also going onto the bus. The bus is the durable record. The design says the bus holds signals (which are bounded) and directives. The directive's subject comes from the policy, which is signed. The policy is signed and could contain a huge subject; but the policy is an authority envelope, not a user-provided prompt. However, the threat model might consider that an attacker who can craft a policy with a huge subject could make the bus store large data. But is that in scope? The red-team says: "the audit fence (can anything prompt-sized become durable?)". The policy is signed by the policy key; forging a policy is out of scope. But a legitimate (but malicious) policy with a huge subject could be used. The invariants around audit fence (Finding G) say "a total durable record is itself a liability" and structural bounds on signal fields prevent prompts. But directives are also recorded. If a policy subject can be arbitrary length, then the durable record can contain large strings. Should that be restricted? The code does not restrict subject length in Directive or in policies. This could be a MEDIUM finding, as it could allow a large blob to be persisted, not necessarily a prompt but could be used to store arbitrary data. However, the policy itself is an authority, and the admin signing the policy could include a huge subject. The system is not designed to protect against the policy signer. So maybe not a defect. But the audit fence might need to constrain directive subject as well. The design doc 3.1 says deletion/tombstone is policy's alone; but doesn't mention size limits on subject. Since it's a design consideration, we could note that the bus could store large subjects, but the scope of audit fence originally was about signals not carrying prompts (Finding G). The bus also holds directives. However, the threat model here is about the bus replay and adversarial files. If someone can write arbitrary files into the bus path (i.e., they have filesystem access), they could insert arbitrary data anyway; but the audit fence concerns what the system itself writes durably. If a policy with a big subject is signed, the interpreter will produce a directive with that big subject, and emit will write it. That could be seen as a bypass of the "no prompt in durable record" principle because the subject field is unbounded. But is that really a concrete defect? I'll note it as a potential LOW/MEDIUM finding: subject length unbounded, allowing a large string to be stored durably via a crafted policy. However, the policy signer is trusted not to do that. The red-team says "hand-forged GovernedOutcome/Verdict/MemoryRetention objects and holders of the policy signing key are OUT OF SCOPE". So tampering with the policy content using the signing key is out of scope. So an attacker without the signing key cannot create a signed policy with a huge subject. If they can forge a policy, it's out of scope. So this is not a vulnerability because the policy must be signed. So no defect.

Now, look for logic errors in the aggregation function: `by_facet.setdefault(s.facet, []).append(s)`. This uses setdefault incorrectly? It should be `setdefault` but that's a typo? The code actually uses `.setdefault`? Wait, the code in the given material: `by_facet.setdefault(s.facet, []).append(s)`. That's a TypeError because dict has no method `setdefault`. That would cause a crash! I need to double-check: In the interpreter.py snippet, it says `by_facet.setdefault(s.facet, []).append(s)`. That's a bug! The method is `setdefault` not `setdefault`. But Python dicts have `setdefault`? Actually, dicts have `setdefault` since Python 3.x? Wait, `dict.setdefault` was added in Python 3.9? Let's recall: `setdefault` is a method of dict? I think it's `dict.setdefault`? No, it's `dict.setdefault` doesn't exist. The correct method is `defaultdict` or using `dict.setdefault`? Actually, there is `dict.setdefault(key, default)`. I think it's been there forever. Wait: `mydict.setdefault(key, default)` is a method of dict. Let me verify: In Python, `dict` objects have a `setdefault(key[, default])` method. Yes, it was added in Python 2.0? Actually, `setdefault` exists. Let me check quickly: `{'a': 1}.setdefault('b', 2)` returns 2 and sets 'b' to 2. So `setdefault` is correct. I recall that `setdefault` is a standard method. So that is fine.

But wait, the code snippet: `by_facet.setdefault(s.facet, []).append(s)`. That's valid. So no crash.

Now, a potential bug: In `_aggregate`, they do `for facet, group in by_facet.items(): weight = sum(s.confidence for s in group)`. If weight > 0, they compute weighted mean. That's correct.

But there is a subtlety: `by_facet` may have a facet with signals from different subsystems. That's fine.

Now, consider the risk unknown vs risk exceeded distinction: It relies on `Facet.RISK in agg`. But `agg` is a dict built from `by_facet` groups where weight > 0. So if there is a RISK signal with positive confidence, agg[Facet.RISK] will be present. So Facet.RISK in agg will be True. That's correct. But what if there is a RISK signal with confidence > 0 but influence is 0, and it's the only RISK signal, then agg[RISK] will be 0.0, and Facet.RISK in agg True. So risk=0.0, not over cap, so no inhibitor. That's correct. What if there are multiple RISK signals, some with confidence zero (which are excluded) and one with positive confidence? The positive confidence one will be aggregated. So fine.

Now, I need to look for a scenario where a RISK signal is present but its confidence is very low and the aggregation yields a risk value over the cap, but the verification depth from that risk is low because verification depth uses the same risk value. But that's fine.

Now, an important finding: The inhibitor handoff is produced only when the recorded rationale is RISK_EXCEEDED. But could a subject achieve RISK_EXCEEDED without an ADAPTATION request? As we saw, NOT_REQUESTED blocks before risk check. But there is a potential bypass: What if there is an ADAPTATION signal with positive influence but the policy's allow_adaptation is False? Then the first check is policy_disallowed, so it records POLICY_DISALLOWED, never reaches risk. So no inhibitor. Good.

But what if the policy is changed to allow adaptation, but the adaptation signal's influence is zero? Then NOT_REQUESTED. So no inhibitor.

Now, could an attacker trigger RISK_EXCEEDED for a subject that they don't control by publishing a RISK signal for that subject? They would need to publish a signal with that subject. The bus allows any subsystem id, but the interpreter only uses signals that match the policy's subject. So if the policy's subject is a specific request id, only signals for that subject count. So they would need to spoof that subject. But the bus publishing is likely controlled, so that's out of scope.

Now, think about the nomination predicate: The docx says nomination predicate is exactly bool(outcome.adaptation_allowed). But the adaptation gate also checks `outcome.adaptation_allowed` which is a boolean. It could be True even if the outcome is not cleared? decide ensures adaptation_allowed requires cleared AND adaptation eligibility AND VERIFIED. So it's fine.

Now, I'm wondering if there's an issue with the seam's achieved_level function and the attestation-compatible reasons. The achieved_level function's attestation check: `if ( verdict.status is Status.UNVERIFIED and Reason.INTEGRITY_ATTESTED in verdict.reasons and set(verdict.reasons) <= _ATTESTATION_COMPATIBLE )`. So if there's any reason outside that set, it doesn't reach RECEIPT. That seems correct.

But is there a scenario where a verdict with INTEGRITY_ATTESTED plus REASON.NO_CONTRACT (which is a hard failure) would be treated as RECEIPT? No, because NO_CONTRACT is not in the compatible set, so set <= compatible would be false. So it's safe.

Now, look at the memory governor's effective_weight: `retention.base_weight * 0.5 ** (age / half_life) + reinforcement_sum`. The reinforcement_sum is added after decay, which could make the weight exceed base. That's by design: reinforcement events add. So that's fine.

Now, test honesty: The test for disagreement e2e uses `self.assertEqual(effective_weight(retention, NOW), effective_weight(retention, NOW + 3650.0))` to assert the inhibitor never decays. That's a good test. But what if the implementation of effective_weight for inhibitors simply returned base_weight+reinforcement without checking inhibitor? That test would still pass because base_weight is 1.0 and reinforcement_sum defaults 0.0, so both equal 1.0. But a sabotaged effective_weight that ignores inhibitor and applies decay would cause decay over a decade, so the weight at NOW+3650 would be less than at NOW unless the half-life is infinite. Since inhibitor class is "semantic", but inhibitor flag is True, the code checks inhibitor first and returns base_weight+reinforcement. So it's pinned. If someone sabotaged by removing the inhibitor check, the test would fail because after a decade, semantic half-life 180 days, so weight would be near 0. So the test would catch that. So it's honest.

Another test: test_risk_unknown_is_not_an_incident checks that nominate for risk_unknown_outcome yields no handoff. That's good.

Now, a possible defect: In the interpreter, when computing verification depth, they use `round_half_up(verif_salience * span)`. What if span is 0? If min_verification == max_verification, span=0, then v_depth = policy.min_verification + round_half_up(0) = min_verification. So fine.

Now, a potential bug in the priority chain for adaptation rationale: The test `test_under_verified_wins_over_an_over_cap_risk` ensures that if both under-verified and risk exceeded conditions are true, UNDER_VERIFIED is recorded. That's correct. But what about the condition ordering: first, `if not policy.allow_adaptation`, then `elif not agg.get(Facet.ADAPTATION, 0.0) > 0.0`, then `elif v_depth < policy.adaptation_min_verification`, then `elif risk > policy.adaptation_max_risk:`. So if v_depth < adapt_min_verification, it stops before checking risk. So risk exceeded won't be recorded. That's intended. But could there be a scenario where risk > max_risk but v_depth >= adapt_min_verification, and the policy's adaptation_min_verification is 0? Then v_depth is at least 0, so it would check risk. That's fine.

Now, a subtle bug: In the aggregation, `by_facet` is built from signals, but what if there are multiple RISK signals with different confidences, the weighted mean might be below the cap even if one signal is very high influence with low confidence? That's by design.

Now, I think I might have found a concrete defect: In the bus's _replay, when replaying a directive line, the payload is stored as a dict. However, the `directives_for` method returns `json.loads(json.dumps(p))` for each payload. This deep copies the dict. But what if the payload contains a string that is not valid UTF-8? JSON only allows valid UTF-8 strings, but the file is text, so should be fine. But what if the original emit produced a payload with a non-serializable object? The emit function builds a dict with primitive types, so it's JSON-serializable. So fine.

Now, a more subtle bus issue: The _replay checks set(e) == {"kind", "payload", "prev", "hash"} and isinstance(e["payload"], dict). But what if the payload dict contains a key "prev"? That's inside payload, not top-level. That's fine. However, there is a potential for confusion: the check for exact key set ensures no extra top-level keys, but it doesn't prevent the payload from containing a key named "hash" or "kind", which would be inside payload. That's not a problem because the payload is treated as data. The hash is computed over the base dict which includes the payload as a whole, so even if payload has a key "hash", it doesn't affect the integrity check because the hash of the base includes that payload. So fine.

Now, could an attacker insert a directive line where the payload's subject is an empty string? That would be stored, and directives_for might return it. But it's not a functional issue. But could that cause a crash in any consumer? Not directly.

Now, the bus's replay also checks for signal validity by constructing a SalienceSignal and calling valid_signal. But note that it passes the payload as kwargs: `SalienceSignal(**p)`. If the payload has extra fields (like a "smuggled" field inside payload), that would cause a TypeError because SalienceSignal.__init__ doesn't accept unknown fields? The dataclass is frozen with no __init__ that accepts extra fields? Actually, by default, dataclass __init__ accepts only defined fields. So if p has an extra key, it will raise TypeError, caught by the except, and intact=False. So that's good. The exact key set check at top-level prevents extra keys in the entry, but inside payload for a signal, if there's an extra key, it will fail to construct. So the audit fence is tight.

Now, the test for smuggled key in bus test_smuggled_key_refuses_to_open tests that an extra top-level key "smuggled" causes rejection. That's good. But what about an extra key inside the payload of a directive? The bus doesn't validate directive payload structure beyond being a dict. So a malicious directive line could have extra keys in its payload, and it would be accepted and stored. That could be used to store extra data. However, the directive payload is emitted by the system, not by external input; but an adversarial file could contain such lines crafted by an attacker with filesystem access. The scope of ADR 0001 is accidental corruption, not malicious rewrite. So it's not a defect if the system is not designed to detect malicious insertion of extra keys inside directive payload, because the hash and key set checks prevent tampering that alters the payload without changing the hash. If an attacker can rewrite the file entirely, they can craft a valid hash chain with extra keys inside payload, but that's consistent rewrite out of scope. So not a finding.

Now, I'll look at the priority of findings: CRITICAL, HIGH, MEDIUM, LOW. I suspect there are no CRITICAL or HIGH issues. But let's examine if there's a way to make the two channels' DISAGREEMENT fail: i.e., an inhibitor can be lost despite an asserted over-cap risk. The handoff is created only when rationale is RISK_EXCEEDED. Could a subject with RISK_EXCEEDED be processed but the handoff is lost because nominate never creates it if outcome is invalid? For a bound outcome with RISK_EXCEEDED, nominate should create handoff. That seems solid. But what if the seam's decide sets adaptation_allowed to True erroneously even when rationale is RISK_EXCEEDED? That's impossible because adaptation_allowed requires directive eligible CANDIDATE; if rationale is RISK_EXCEEDED, eligibility is NONE, so adaptation_allowed=False. So no.

Could the handoff be created but then memory governor's retain raises HandoffMismatchError due to a subtle mismatch? The attribution checks all conditions. There's a check: `d.adaptation_rationale is AdaptationRationale.RISK_EXCEEDED`. In the risk_reject_outcome fixture, the directive's adaptation_rationale is RISK_EXCEEDED. So it works. But suppose the directive's adaptation_rationale is RISK_EXCEEDED but the handoff.rationale is also "risk_exceeded", it matches. The subject is same. So it works.

Could there be a scenario where the adaptation_rationale is RISK_EXCEEDED but the outcome's subject is empty? Then retain will see bound=False, because d is None or not outcome.subject. In retain, `bound = d is not None and bool(outcome.subject)`. In risk_reject_outcome, subject is "act-1", so bound=True. So okay.

Now, a potential flaw: In the control seam's decide, when directive/verdict action mismatch, they set required = FULL. But they also set bound=False? They set bound=False and then cleared=False. The outcome's directive is None, subject "". So then the consumer gates will see outcome.subject empty, so they won't act. But could a mismatch scenario still produce an inhibitor? Let's see: if the directive has subject "A", rationale RISK_EXCEEDED, but the verdict's envelope_id is "B", mismatch. Then decide() returns outcome with directive=None, subject="". This outcome is unbound. If passed to nominate(), it returns early with subject="" and no handoff. So inhibitor is lost. Is that correct? If there is a mismatch, the action wasn't for the right subject. The risk exceed was for subject A, but the outcome is unbound. The inhibitor would be lost. But is that a problem? The seam is supposed to prevent acting on a directive for a different action. If the directive's subject doesn't match the verdict's action, the whole outcome is untrusted, so we shouldn't create an inhibitor for that. That seems correct: you don't pin an incident for an action that wasn't actually the one verified.

Now, could an attacker deliberately cause a mismatch to hide an inhibitor? They'd need to produce a verdict with a wrong envelope_id. Forging a verdict is out of scope. So not an issue.

Now, what about the impossibility of inhibitor being manufactured without asserted over-cap risk? We already confirmed that only RISK_EXCEEDED creates handoff. So safe.

Now, I think the code is quite robust. But I recall a potential issue: The interpreter's adaptation rationale can be RISK_EXCEEDED even when the policy's adaptation_max_risk is set to 1.0, but risk > 1.0? risk is in [0,1], so 1.0 > 1.0 is False. So no RISK_EXCEEDED. So fine.

Now, let's check the harness of the seam's achieved_level for the case where verdict.status is VERIFIED but effective_stakes is None or not a Stakes. achieved_level does: `if verdict.status is Status.VERIFIED: return FULL if effective_stakes in (Stakes.HIGH, Stakes.CRITICAL) else INDEPENDENT`. If effective_stakes is None, it's not in that tuple, so returns
