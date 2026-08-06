# Red-team (consumers): z-ai/glm-5.2

_finish=length seconds=398.0 usage={'prompt_tokens': 31693, 'completion_tokens': 12000, 'total_tokens': 43693, 'cost': 0.0663237, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0663237, 'upstream_inference_prompt_cost': 0.0285237, 'upstream_inference_completions_cost': 0.0378}, 'completion_tokens_details': {'reasoning_tokens': 12000, 'image_tokens': 0, 'audio_tokens': 0}}_

Let me carefully analyze the code for concrete defects under the stated invariants. I'll go through each attack vector systematically.

## Attack Vector 1: DISAGREEMENT property failures

The disagreement property: high-salience high-risk content should be a memory RETAIN (pinned inhibitor) AND weight HARD BLOCK simultaneously.

Let me trace the flow:
1. `interpret()` produces a directive with `adaptation_rationale = RISK_EXCEEDED` when risk > cap AND risk is asserted (in agg)
2. `decide()` composes directive + verdict into GovernedOutcome
3. `consume()` calls `nominate(outcome)` then `retain(outcome, now_days, handoff=decision.handoff)`
4. `nominate()` checks `outcome.adaptation_allowed` (False for risk_exceeded), then checks rationale == RISK_EXCEEDED → creates handoff
5. `retain()` receives the handoff, validates attribution, sets inhibitor=True

Let me look for paths where the inhibitor can be:
- Manufactured without an asserted over-cap risk
- Lost despite one
- Misattributed to wrong subject
- Made to decay

**Manufactured without asserted over-cap risk:**
The handoff is created in `nominate()` only when `rationale is AdaptationRationale.RISK_EXCEEDED`. The rationale comes from the directive, which is stamped by `interpret()`. The only way to get RISK_EXCEEDED is `risk > policy.adaptation_max_risk and Facet.RISK in agg`. So this seems correct.

But wait - what about hand-built directives reaching `decide()`? The prompt says "hand-built DIRECTIVES reaching decide() are in scope." So someone could construct a Directive with `adaptation_rationale = AdaptationRationale.RISK_EXCEEDED` directly and pass it to `decide()`. Let's check `_valid_directive`:

```python
def _valid_directive(directive) -> bool:
    return (
        type(directive) is Directive
        and isinstance(directive.verification_depth, int)
        and not isinstance(directive.verification_depth, bool)
        and isinstance(directive.adaptation_rationale, AdaptationRationale)
        and (
            (directive.adaptation_rationale is AdaptationRationale.ELIGIBLE)
            == (directive.adaptation_eligibility is AdaptationEligibility.CANDIDATE)
        )
    )
```

This checks ELIGIBLE <=> CANDIDATE coherence, but does NOT check that RISK_EXCEEDED implies adaptation_eligibility is NONE. Actually, the coherence check only checks ELIGIBLE iff CANDIDATE. So RISK_EXCEEDED with eligibility NONE passes (correct), and RISK_EXCEEDED with eligibility CANDIDATE would also pass since RISK_EXCEEDED is not ELIGIBLE, and CANDIDATE would make the right side True but left side False, so `False == True` → False → invalid. OK, so RISK_EXCEEDED + CANDIDATE is rejected. Good.

But what about a hand-built directive with RISK_EXCEEDED + NONE? That passes `_valid_directive`. Then `decide()` would produce an outcome with `adaptation_allowed=False` (since eligibility is NONE). Then `nominate()` would see rationale == RISK_EXCEEDED and create a handoff. Then `retain()` would accept it.

But wait - the prompt says hand-built directives reaching `decide()` are IN SCOPE. So this is a valid attack: someone constructs a Directive with RISK_EXCEEDED rationale but without an actual asserted over-cap risk signal, passes it to `decide()`, and gets an inhibitor manufactured.

Hmm, but actually - is this really a defect? The directive is the interpreter's output. If someone hand-builds a directive, they're bypassing the interpreter. The prompt says "hand-built DIRECTIVES reaching decide()" are in scope. So yes, this is in scope.

But wait - let me re-read the prompt more carefully: "Hand-forged GovernedOutcome/Verdict/MemoryRetention objects and holders of the policy signing key are OUT OF SCOPE (equivalent to bypassing the verifier) — but hand-built DIRECTIVES reaching decide(), and adversarial bus FILES, are in scope."

So hand-built directives reaching `decide()` are in scope. A hand-built directive with `adaptation_rationale = RISK_EXCEEDED` and `adaptation_eligibility = NONE` passes `_valid_directive` and would produce an inhibitor through the consumer gates. This means an inhibitor can be manufactured without an asserted over-cap risk.

But wait - is this really a meaningful attack? The directive is supposed to be produced by `interpret()`. If someone can construct arbitrary directives, they can do anything. The question is whether `_valid_directive` is supposed to prevent this. Looking at the design, `_valid_directive` validates the rationale-eligibility coherence, but it doesn't validate that RISK_EXCEEDED actually came from an asserted risk. The rationale is just an enum value stamped on the directive.

Actually, I think the design intent is that the directive is always produced by `interpret()`, and `_valid_directive` is just a structural check. The real question is whether there's a path through `interpret()` that can produce RISK_EXCEEDED without an asserted over-cap risk. Let me re-check...

In `interpret()`:
```python
elif risk > policy.adaptation_max_risk:
    rationale = (AdaptationRationale.RISK_EXCEEDED if Facet.RISK in agg
                 else AdaptationRationale.RISK_UNKNOWN)
```

`risk = agg.get(Facet.RISK, 1.0)` - so if RISK is absent, risk=1.0, but `Facet.RISK not in agg` → RISK_UNKNOWN. If RISK is in agg, it means there was at least one signal with positive confidence weight. So RISK_EXCEEDED requires both `risk > cap` and `Facet.RISK in agg`. This seems correct.

But what about the hand-built directive case? The prompt explicitly says hand-built directives reaching `decide()` are in scope. So a directive with RISK_EXCEEDED + NONE eligibility passes `_valid_directive`, and the consumer gates would manufacture an inhibitor. This is a real finding.

Actually wait, let me think about this more carefully. The prompt says the consumer gates "CONSUME the recorded decision, never re-derive it." The whole point is that the gates trust the directive's recorded rationale. If someone hand-builds a directive with a fake RISK_EXCEEDED, the gates will honor it. But is this a defect? The design says the directive is the interpreter's output, and the gates consume it. If you bypass the interpreter, you bypass the enforcement. But the prompt says hand-built directives are in scope...

Let me think about what attack this enables. An attacker who can construct a Directive object (but doesn't have the signing key) can:
1. Create a directive with `subject="victim"`, `adaptation_rationale=RISK_EXCEEDED`, `adaptation_eligibility=NONE`
2. Pass it to `decide()` along with a VERIFIED verdict for "victim"
3. Get an outcome with `adaptation_allowed=False`
4. Call `consume()` → gets an inhibitor pinned on "victim" forever

But wait - to get a VERIFIED verdict, you need the verifier pipeline. And the prompt says hand-forged Verdict objects are out of scope. So you'd need a real verdict. But `decide()` is a pure function that takes (directive, verdict). If you have a real verdict for some action, you could construct a directive for that same action with RISK_EXCEEDED...

Actually, I think this is a legitimate finding. The `_valid_directive` check is supposed to be the boundary validation, and it doesn't validate that RISK_EXCEEDED is consistent with the directive's other fields (beyond the ELIGIBLE<=>CANDIDATE check). But actually, what would it even check? The directive doesn't carry the risk value or the cap. The rationale IS the record of the decision.

Hmm, let me reconsider. The design says "the nomination predicate is exactly bool(outcome.adaptation_allowed)" and "the inhibitor trigger is the interpreter's RECORDED RISK_EXCEEDED rationale." The gates consume the recorded decision. If the directive says RISK_EXCEEDED, the gate trusts it. The security comes from the directive being produced by `interpret()` against a signed policy. A hand-built directive bypasses this.

But the prompt explicitly says hand-built directives reaching `decide()` are in scope. So I should report this. However, I need to think about whether this is actually exploitable in a meaningful way. The attacker would need to:
1. Construct a Directive (no signing key needed - just construct the dataclass)
2. Have a Verdict for the same subject (hand-forged verdicts are out of scope, but real verdicts are in scope)

Actually, `decide()` takes a directive and a verdict. If the attacker can construct both, they can get any outcome. But hand-forged verdicts are out of scope. So the attacker needs a real verdict. But if they have a real verdict for action X, they can construct a directive for action X with RISK_EXCEEDED, and `decide()` would bind them (same subject) and produce an outcome with the inhibitor trigger.

This is a real finding. Let me assess severity. The attacker can pin content as an inhibitor without an actual over-cap risk. This violates "an ASSERTED over-cap risk (RISK_EXCEEDED) is the ONLY inhibitor trigger."

Actually, wait. Let me re-read the scope more carefully: "Hand-forged GovernedOutcome/Verdict/MemoryRetention objects and holders of the policy signing key are OUT OF SCOPE (equivalent to bypassing the verifier) — but hand-built DIRECTIVES reaching decide(), and adversarial bus FILES, are in scope."

So hand-built directives are explicitly in scope. A hand-built directive with RISK_EXCEEDED can manufacture an inhibitor. But... is this really a defect in the implementation? The design says the gates consume the recorded decision. The directive IS the recorded decision. If you hand-build a directive, you're forging the decision record. The `_valid_directive` check is meant to catch structural malformation, not semantic forgery.

I think the question is: should `_valid_directive` reject a directive where RISK_EXCEEDED is paired with eligibility NONE but the directive wasn't produced by `interpret()`? There's no way to check this from the directive alone - the directive doesn't carry the risk value or cap.

Let me move on and look for other issues.

**Lost despite an asserted over-cap risk:**

Can an inhibitor be lost? The handoff flows from `nominate()` to `retain()` via `consume()`. In `consume()`:
```python
def consume(outcome, now_days) -> tuple:
    decision = nominate(outcome)
    retention = retain(outcome, now_days, handoff=decision.handoff)
    return decision, retention
```

If `nominate()` produces a handoff, it's passed to `retain()`. Can `retain()` drop it? `retain()` validates attribution and raises `HandoffMismatchError` if it fails. But if the attribution is correct, it accepts it. So the handoff shouldn't be lost.

But what if `nominate()` doesn't produce a handoff when it should? Let's check: `nominate()` produces a handoff only when `rationale is AdaptationRationale.RISK_EXCEEDED`. This requires `outcome.directive` to be non-None and `outcome.subject` to be non-empty (otherwise it returns early with no handoff). And it requires `outcome.adaptation_allowed` to be False (otherwise it returns early with nomination).

Can `adaptation_allowed` be True when rationale is RISK_EXCEEDED? In `decide()`:
```python
adaptation_allowed = (
    cleared
    and directive.adaptation_eligibility is AdaptationEligibility.CANDIDATE
    and verdict.status is Status.VERIFIED
)
```

RISK_EXCEEDED implies eligibility is NONE (from `interpret()`), so `adaptation_allowed` would be False. Good. But with a hand-built directive, RISK_EXCEEDED + CANDIDATE is rejected by `_valid_directive`. So this path is closed.

What about the case where the outcome is unbound? If `directive.subject != verdict.envelope_id`, the outcome is unbound, `directive=None`, `subject=""`. Then `nominate()` returns early with no handoff. But the directive had RISK_EXCEEDED... the inhibitor is lost! 

Wait, but if the directive is unbound, should there be an inhibitor? The design says "an unbound directive's identity is withheld from the outcome." If the directive doesn't bind to the verdict, the outcome is denied, and no inhibitor is produced. Is this correct? The RISK_EXCEEDED rationale means there was an asserted over-cap risk, but if the directive doesn't bind to the verdict, the outcome is about a different action. So it makes sense not to produce an inhibitor for an unbound outcome.

But wait - what if the directive IS bound but the outcome is denied for other reasons (e.g., FAILED verdict)? Let's check: if the verdict is FAILED, `cleared=False`, `adaptation_allowed=False`. The directive is bound (subject matches), so `outcome.directive` is non-None and `outcome.subject` is non-empty. Then `nominate()` proceeds to check the rationale. If rationale is RISK_EXCEEDED, it creates the handoff. Good.

What if the directive is bound but `adaptation_allowed` is False because of under-verification? Same path - `nominate()` checks rationale. If it's RISK_EXCEEDED, handoff is created. But wait - can RISK_EXCEEDED coexist with under-verification? In `interpret()`, the priority chain is:
1. POLICY_DISALLOWED
2. NOT_REQUESTED
3. UNDER_VERIFIED
4. RISK_EXCEEDED / RISK_UNKNOWN
5. ELIGIBLE

So UNDER_VERIFIED is checked before RISK_EXCEEDED. If both conditions fail, UNDER_VERIFIED is recorded, not RISK_EXCEEDED. So RISK_EXCEEDED only happens when verification is sufficient. Good.

**Misattributed to wrong subject:**

The handoff carries `subject=outcome.subject`. In `retain()`, the attribution check requires `handoff.subject == outcome.subject`. So the handoff is always for the same subject as the outcome. Can the outcome's subject be wrong? It comes from `directive.subject` (when bound), which comes from `policy.subject`. So the subject is correct.

But what about `consume()`? It calls `nominate(outcome)` which creates a handoff with `subject=outcome.subject`, then calls `retain(outcome, now_days, handoff=decision.handoff)`. The handoff's subject matches the outcome's subject. So no misattribution.

**Made to decay:**

Inhibitors are exempt from decay in `effective_weight()`:
```python
if retention.inhibitor:
    return retention.base_weight + reinforcement_sum
```

Can `retention.inhibitor` be False when it should be True? In `retain()`, `inhibitor = handoff is not None`. If the handoff is passed and accepted, `inhibitor=True`. If no handoff, `inhibitor=False`. So the inhibitor flag is correct as long as the handoff is correctly passed.

Can someone call `effective_weight()` on a retention record that should be an inhibitor but isn't? Only if `retain()` was called without the handoff. But `consume()` always passes the handoff. If someone calls `retain()` directly without the handoff, the inhibitor is lost - but that's a caller error, not a code defect.

## Attack Vector 2: Nomination exceeds single predicate

The nomination predicate is `bool(outcome.adaptation_allowed)`. Let me check `nominate()`:

```python
if outcome.adaptation_allowed:
    return AdaptationDecision(
        subject=outcome.subject, nominated=True, ...
    )
```

This is the only path to `nominated=True`. Can `adaptation_allowed` be True without the directive being CANDIDATE and verdict being VERIFIED? In `decide()`:
```python
adaptation_allowed = (
    cleared
    and directive.adaptation_eligibility is AdaptationEligibility.CANDIDATE
    and verdict.status is Status.VERIFIED
)
```

And `cleared = bound and verdict.status is not Status.FAILED and achieved >= required`.

So `adaptation_allowed` requires: bound, not FAILED, achieved >= required, CANDIDATE, VERIFIED. This seems correct.

Can unverified content be nominated? Only if `adaptation_allowed` is True, which requires VERIFIED. So no.

**Promote/apply surface:**

`AdaptationDecision` has fields: subject, nominated, rationale, handoff, gate_version, reasons. No promote/apply field. The schema pin test checks this. Good.

**Capability leak through consumers:**

The consumers don't access `allowed_capabilities` at all. `nominate()` reads `outcome.directive.adaptation_rationale` and `outcome.adaptation_allowed`. `retain()` reads `outcome.directive.retention_class` and `outcome.cleared`. No capability access. Good.

## Attack Vector 3: Seam boundary - directive/outcome shape that reaches gates and crashes/lies/acts on withheld identity

Let me think about what happens when a hand-built directive reaches `decide()` with various malformed shapes.

`_valid_directive` checks:
- `type(directive) is Directive`
- `isinstance(directive.verification_depth, int)` and not bool
- `isinstance(directive.adaptation_rationale, AdaptationRationale)`
- ELIGIBLE iff CANDIDATE

What about other fields? `directive.subject` is used in binding. What if it's not a string? `directive.subject == verdict.envelope_id` - if subject is, say, 42, and envelope_id is "act-1", they won't match, so it's unbound. No crash.

What about `directive.retention_class`? In `retain()`:
```python
if bound and d.retention_class in RETENTION_ORDER:
    retention_class = d.retention_class
else:
    retention_class = RETENTION_ORDER[0]
```

If `retention_class` is not a string (e.g., None), `None in RETENTION_ORDER` is False, so it floors. No crash.

What about `directive.adaptation_eligibility`? In `decide()`:
```python
directive.adaptation_eligibility is AdaptationEligibility.CANDIDATE
```

If it's not an AdaptationEligibility, this is just False. No crash.

What about a directive where `adaptation_rationale` is a valid AdaptationRationale but `adaptation_eligibility` is not an AdaptationEligibility? `_valid_directive` checks the rationale is an AdaptationRationale and the ELIGIBLE<=>CANDIDATE coherence. If eligibility is, say, "candidate" (string), then `directive.adaptation_eligibility is AdaptationEligibility.CANDIDATE` is False, so the right side of the coherence check is False. If rationale is ELIGIBLE, left side is True, `True == False` → False → invalid. If rationale is not ELIGIBLE, left side is False, `False == False` → True → valid. So a directive with non-enum eligibility but non-ELIGIBLE rationale passes `_valid_directive`. Then in `decide()`, `directive.adaptation_eligibility is AdaptationEligibility.CANDIDATE` is False, so `adaptation_allowed` is False. No crash, no lie.

What about `directive.subject` being empty string? `bound = bool(directive.subject) and directive.subject == verdict.envelope_id`. Empty string is falsy, so `bound=False`. The outcome is unbound, directive=None, subject="". No crash.

What about a directive with `subject=""` but `adaptation_rationale=RISK_EXCEEDED`? `_valid_directive` passes (RISK_EXCEEDED is not ELIGIBLE, eligibility would need to not be CANDIDATE). `decide()` produces an unbound outcome (subject="" is falsy). `nominate()` sees `outcome.directive is None` → returns early, no handoff. So the inhibitor is lost. But is this a problem? The directive has an empty subject, which means it's from `_hard_deny` (unsigned policy). An unsigned policy shouldn't produce inhibitors. This seems correct.

Actually wait - what if someone hand-builds a directive with `subject=""` and `rationale=RISK_EXCEEDED`? `_valid_directive` passes. `decide()` sees `bool("")` is False → unbound. The outcome has `directive=None`, `subject=""`. `nominate()` returns early. No inhibitor. This is correct - an empty subject can't be attributed to anything.

Let me think about whether there's a way to make the gates crash. The gates have type fences:
- `nominate()`: `if type(outcome) is not GovernedOutcome: raise TypeError`
- `retain()`: `if type(outcome) is not GovernedOutcome: raise TypeError`

But these are for the outcome, not the directive. The directive is validated by `_valid_directive` in `decide()`. If `_valid_directive` returns False, `decide()` returns a denied outcome. The gates then see a denied outcome and handle it.

What about `GovernedOutcome` itself? It's a frozen dataclass. Can someone construct one with weird fields? The prompt says hand-forged GovernedOutcome objects are out of scope. So I won't pursue this.

Let me think about the "act on withheld identity" angle. The design says `subject=""` is the fail-closed marker. In `nominate()`:
```python
if d is None or not outcome.subject:
    return AdaptationDecision(subject="", ...)
```

And in `retain()`:
```python
bound = d is not None and bool(outcome.subject)
```

So both gates check `outcome.subject` for emptiness. If the outcome is unbound, `subject=""`, and both gates act on nothing. Good.

But what if `outcome.subject` is a non-empty string that's not the real subject? This would require a hand-forged GovernedOutcome, which is out of scope.

## Attack Vector 4: Bus replay/reader correctness

Let me look at `_replay()` more carefully.

```python
def _replay(self, path) -> None:
    with open(path, encoding="utf-8") as fh:
        lines = [ln for ln in (raw.strip() for raw in fh) if ln]
    prev = ""
    for i, line in enumerate(lines):
        try:
            e = json.loads(line)
            base = {"kind": e["kind"], "payload": e["payload"], "prev": e["prev"]}
            intact = (
                set(e) == {"kind", "payload", "prev", "hash"}
                and isinstance(e["payload"], dict)
                and e["prev"] == prev
                and digest(base) == e["hash"]
            )
        except Exception:
            intact = False
        if not intact:
            raise ValueError(...)
        if e["kind"] == "signal":
            p = dict(e["payload"])
            p["provenance"] = tuple(p.get("provenance", ()))
            try:
                signal = SalienceSignal(**p)
            except TypeError:
                signal = None
            if signal is None or not valid_signal(signal):
                raise ValueError(...)
            self._signals.append((e["hash"], signal))
        else:
            self._directives.append((e["hash"], e["payload"]))
        self._entries.append(e)
        prev = e["hash"]
    self._head = prev
```

Issues I see:

1. **What if `e["kind"]` is neither "signal" nor "directive"?** The code goes to the `else` branch and appends to `self._directives`. So a line with `kind="malicious"` would be stored as a directive payload. Then `directives_for()` would serve it. But wait - the hash chain verification passes (the hash is correct), and the payload is a dict (checked). So a crafted line with `kind="evil"` and a valid hash would be accepted and stored as a directive. Is this a problem?

Actually, let me check: `directives_for()` filters by `p.get("subject") == subject`. So the payload would need to have a "subject" field. If it does, it would be served. But the payload is just a dict - it could have any fields. This could be a smuggling channel.

Wait, but the hash chain verifies the payload. The payload is part of the digest base. So the payload is authenticated (within the scope of ADR 0001 - accidental corruption). A crafted line with a correct hash is not accidental corruption - it's malicious, which is out of scope (consistent malicious rewrite).

Actually, no - a crafted line with a correct hash that's inserted into the file IS in scope if it's accidental corruption. But if someone crafts a line with a correct hash, that's malicious. Hmm, but the prompt says "accidental corruption IS in scope." A crafted line with a correct hash is not accidental.

Let me think about this differently. What kinds of accidental corruption could cause problems?

2. **The `else` branch for non-signal kinds:** If `e["kind"]` is something other than "signal" or "directive", it's stored as a directive. This means a corrupted kind field (e.g., "singal" instead of "signal") would cause a signal to be stored as a directive. But the hash would still need to match, so this would only happen if the kind field was corrupted AND the hash was recomputed. That's not accidental corruption - that's malicious rewrite.

Actually, for accidental corruption, the hash check would catch any change to the kind field. So a corrupted kind field would fail the hash check and raise. Good.

3. **Signal reconstruction:** The code does:
```python
p = dict(e["payload"])
p["provenance"] = tuple(p.get("provenance", ()))
try:
    signal = SalienceSignal(**p)
except TypeError:
    signal = None
```

What if the payload has extra fields? `SalienceSignal(**p)` would raise `TypeError` (unexpected keyword argument), and `signal` would be None, then `valid_signal(signal)` would be called on None... wait, `signal = None`, then `if signal is None or not valid_signal(signal)` - `signal is None` is True, so it raises. Good.

What if the payload is missing a field? `SalienceSignal(**p)` would raise `TypeError` (missing required argument), `signal = None`, raises. Good.

What if `provenance` is not a list? `tuple(p.get("provenance", ()))` - if it's a string, `tuple("abc")` = `('a', 'b', 'c')`. Then `SalienceSignal` is constructed, but `valid_signal` checks `isinstance(s.provenance, tuple)` and `all(_ref_token(p) for p in s.provenance)`. Each character would need to be a valid ref token (1-128 chars). Single characters pass `_ref_token` (length 1, which is > 0 and <= 128). So a provenance string "abc" would become `('a', 'b', 'c')` and pass validation. But the hash would need to match - the original payload had `"provenance": "abc"` (a string), and the hash was computed over that. So this is the original payload, not corrupted. The question is: could a signal with `provenance="abc"` (string) be published in the first place?

In `publish()`:
```python
entry = {"kind": "signal", "payload": asdict(signal), "prev": self._head}
```

`asdict(signal)` would produce `{"provenance": (...)}` (a tuple, which JSON serializes as a list). So the payload would have `"provenance": []` or `"provenance": ["ref1", "ref2"]`. On replay, `tuple([...])` reconstructs the tuple correctly. So this is fine.

But what if someone crafts a bus file with `"provenance": "abc"` (a string) and a correct hash? That's malicious, not accidental. And the hash check would catch any modification to an existing entry.

4. **Directive payloads:** The directive payload is stored as-is (`e["payload"]`) without reconstruction or validation. This means a corrupted directive payload (e.g., with wrong types) would be served by `directives_for()`. But the hash check catches corruption. A crafted payload with a correct hash is malicious (out of scope).

5. **The `directives_for()` reader:** It does `json.loads(json.dumps(p))` for deep copy. This is correct for JSON-serializable data. But what if the payload contains non-JSON-serializable data? In `_replay()`, the payload comes from `json.loads()`, so it's always JSON-serializable. Good.

6. **Audit fence - can anything prompt-sized become durable?** Signals are validated by `valid_signal()`, which bounds all string fields to 128 chars and provenance to 16 refs. Directive payloads are constructed by `emit()`, which uses fixed fields from the Directive. The Directive's fields are all bounded (subject, policy_id, etc. are strings from the policy, which is signed). But wait - `reasons` is a tuple of strings. Could a reason string be prompt-sized? In `interpret()`, reasons are hardcoded strings like `"dropped_invalid_signals=3"`. In `_hard_deny`, reasons are also hardcoded. So reasons are bounded by the code, not by validation.

But what about a hand-built directive with huge reason strings? `emit()` would serialize them to the bus. But hand-built directives reaching `decide()` are in scope, not hand-built directives reaching `emit()`. Actually, `emit()` just checks `type(directive) is not Directive`. So a hand-built Directive with huge strings in any field would be emitted to the bus. But the bus is the audit surface - the question is whether prompt-sized content can become durable.

Actually, the Directive fields come from the policy (signed) or from hardcoded strings. A hand-built directive bypasses the policy. But `emit()` doesn't validate field sizes. So a hand-built directive with `subject="x" * 1000000` would be emitted to the bus. But is this in scope? The prompt says "hand-built DIRECTIVES reaching decide()" are in scope. `emit()` is a different path. Let me check if there's a path from `decide()` to `emit()`.

`decide()` produces a GovernedOutcome, not a Directive. The Directive is an input to `decide()`. `emit()` takes a Directive. So the path would be: hand-built directive → `emit()` → bus. This doesn't go through `decide()`. But the prompt says hand-built directives reaching `decide()` are in scope, and adversarial bus files are in scope. A hand-built directive reaching `emit()` is... not explicitly in scope? But it's also not explicitly out of scope.

Actually, I think the audit fence question is about the bus's structural properties. The bus's `publish()` validates signals. `emit()` validates that the input is a Directive but doesn't validate field sizes. However, in normal operation, directives are produced by `interpret()` which produces bounded fields. A hand-built directive with huge fields is a different attack.

Let me focus on the bus file replay path. Can an adversarial bus file smuggle prompt-sized content? The replay validates signals with `valid_signal()`. Directive payloads are not validated. So a crafted directive payload with a huge string field would be accepted by replay (hash is correct) and served by `directives_for()`. But crafting a correct hash is malicious (out of scope per ADR 0001).

For accidental corruption: if a directive payload is corrupted, the hash check catches it. So no prompt-sized content can enter through accidental corruption.

Hmm, let me think about this differently. What if a legitimate directive payload has a large `reasons` field? In normal operation, `interpret()` produces small reasons. But `emit()` doesn't bound them. If someone modifies the interpreter to produce large reasons... that's a code change, not an attack on the bus.

I think the audit fence is sound for the bus's scope.

## Attack Vector 5: Test honesty

Let me look for tests that would stay green if the code were sabotaged.

1. **`test_inhibitor_is_not_forced_by_mere_refusal`:** This tests that `attested_eligible_outcome()` produces `ret.inhibitor == False`. If someone changed `nominate()` to set `handoff` whenever `nominated=False`, this test would red. Good.

2. **`test_the_only_true_path_is_adaptation_allowed`:** This tests that a forged outcome with `adaptation_allowed=True` (but UNVERIFIED verdict) still nominates. If the gate re-checked `verdict.status`, this would red. Good.

3. **`test_disagreement_flows_through_consume`:** Tests that risk_reject produces `dec.nominated == False` and `ret.inhibitor == True`. If the channels were collapsed, this would red. Good.

4. **`test_inhibitor_never_decays`:** Tests that `effective_weight(ret, NOW) == effective_weight(ret, NOW + 3650.0)` for an inhibitor. If the inhibitor flag were ignored, this would red. Good.

5. **`test_under_verified_wins_over_an_over_cap_risk`:** Tests that when both verification and risk fail, UNDER_VERIFIED is recorded, not RISK_EXCEEDED. If the priority chain were reordered, this would red. Good.

6. **`test_not_requested_wins_over_an_over_cap_risk`:** Similar. Good.

Let me look for weaker tests...

7. **`test_handoff_attribution_matrix_raises`:** Tests various mismatch cases. But does it test that a handoff with the WRONG `reasons` field is accepted? The `InhibitorHandoff` has a `reasons` field, but `retain()` doesn't check it. Is this a problem? The `reasons` field is not part of the attribution check. But it's also not used for anything - it's just metadata. So this is fine.

8. **`test_returned_copies_cannot_mutate_the_record`:** Tests that modifying a returned copy doesn't affect the original. But it only checks `compute_budget` and `allowed_capabilities`. What about other fields? If `directives_for()` returned a shallow copy, modifying `compute_budget` (an int, immutable) wouldn't affect the original anyway. But modifying `allowed_capabilities` (a list) would. The test checks both. But what about `reasons` (a list)? If the copy is shallow, `reasons` would be aliased. The test doesn't check this. But `directives_for()` does `json.loads(json.dumps(p))`, which is a deep copy. So this is fine.

Actually, wait. Let me look at the test more carefully:
```python
def test_returned_copies_cannot_mutate_the_record(self):
    bus = self._bus_with_two_subjects()
    p = bus.directives_for("req-1")[0]
    p["compute_budget"] = 999999
    p["allowed_capabilities"].append("fs.write:/")
    fresh = bus.directives_for("req-1")[0]
    self.assertNotEqual(fresh["compute_budget"], 999999)
    self.assertNotIn("fs.write:/", fresh["allowed_capabilities"])
    self.assertTrue(bus.verify_chain())
```

This test would stay green even if `directives_for()` returned a shallow copy, because:
- `compute_budget` is an int (immutable), so `p["compute_budget"] = 999999` replaces the value in the copy, not mutating the original.
- `allowed_capabilities` is a list, so `p["allowed_capabilities"].append(...)` would mutate the original if it's a shallow copy. This WOULD red if shallow.

Actually, for a shallow copy, `p["allowed_capabilities"]` would be the same list object as in the original. So `append` would mutate it. The test checks `fresh["allowed_capabilities"]` doesn't contain the appended value. If shallow, it would contain it. So this test does catch shallow copies. Good.

But what about `reasons`? If shallow, `p["reasons"].append(...)` would mutate the original. But the test doesn't check this. However, the implementation uses `json.loads(json.dumps(p))` which is a deep copy, so it doesn't matter.

Let me look for more subtle test honesty issues...

9. **The `test_max_risk_one_with_absent_risk_stays_eligible` test:** This tests that with `adapt_max_risk=1.0` and absent risk, the directive is CANDIDATE/ELIGIBLE. This is a behavior-preservation test. If someone changed `risk > policy.adaptation_max_risk` to `risk >= policy.adaptation_max_risk`, this test would red (1.0 >= 1.0 is True → RISK_UNKNOWN). Good.

10. **Channel separation test:** This checks that `memory.py` and `adaptation.py` don't import each other. But it doesn't check that they don't communicate through other channels (e.g., a shared global). However, the discipline test checks for no globals (implicitly, by checking imports). And the code is pure. So this is fine.

Let me now look for more concrete defects...

## Deeper analysis of the bus replay

Looking at `_replay()` again:

```python
if e["kind"] == "signal":
    ...
else:
    self._directives.append((e["hash"], e["payload"]))
```

What if `e["kind"]` is `"directive"` but the payload is a signal payload? It would be stored as a directive. Then `directives_for()` would serve it. But the hash check ensures the payload matches what was hashed. So this can only happen if someone crafted a line with `kind="directive"` and a signal payload, with a correct hash. That's malicious (out of scope).

For accidental corruption: if the kind field is corrupted, the hash check catches it. Good.

But wait - what about the `verify_chain()` method? It doesn't check the kind field. It only checks the hash chain. So a line with `kind="malicious"` and a correct hash would pass `verify_chain()`. But `_replay()` would store it as a directive (in the `else` branch). Is this a problem?

Actually, for `verify_chain()`:
```python
def verify_chain(self) -> bool:
    prev = ""
    for e in self._entries:
        base = {"kind": e["kind"], "payload": e["payload"], "prev": e["prev"]}
        if e["prev"] != prev or digest(base) != e["hash"]:
            return False
        prev = e["hash"]
    return prev == self._head
```

This recomputes the hash over `{"kind": e["kind"], "payload": e["payload"], "prev": e["prev"]}`. So the kind IS part of the hash. A line with a different kind would have a different hash. So `verify_chain()` does verify the kind. Good.

But `_replay()` also verifies the hash. So a corrupted kind would be caught. Good.

## Looking at the `kind` field handling in `_replay()`

The `else` branch handles everything that's not `"signal"`. This includes `"directive"` but also any other kind value. If a line has `kind="directive"`, it's stored as a directive. If it has `kind="signal"`, it's processed as a signal. If it has any other kind, it's stored as a directive.

But as I noted, the hash check ensures the kind matches what was hashed. So for accidental corruption, a changed kind would be caught. For malicious crafting, it's out of scope.

However, there's a subtle issue: what if a legitimate bus file has a line with `kind="directive"` but the payload looks like a signal payload? This would be stored as a directive and served by `directives_for()`. But this can only happen if `emit()` was called with a directive that had signal-like fields. But `emit()` constructs the payload from the directive's fields, so the payload would have directive fields. This is fine.

## Looking at the `directives_for()` reader

```python
def directives_for(self, subject: str) -> tuple:
    return tuple(json.loads(json.dumps(p)) for _, p in self._directives
                 if p.get("subject") == subject)
```

This filters by `p.get("subject") == subject`. If `subject` is None, `p.get("subject")` would need to be None too. But `subject` is a string in normal operation. If someone passes `subject=None`, it would match payloads with `"subject": null`. But that's a caller error, not a code defect.

What if `p` is not a dict? In `_replay()`, `isinstance(e["payload"], dict)` is checked. So `p` is always a dict. Good.

## Looking at the signal reconstruction in `_replay()`

```python
p = dict(e["payload"])
p["provenance"] = tuple(p.get("provenance", ()))
try:
    signal = SalienceSignal(**p)
except TypeError:
    signal = None
```

What if the payload has extra keys? `SalienceSignal(**p)` would raise `TypeError` (unexpected keyword argument). `signal = None`. Then `valid_signal(None)` would... wait, `signal is None` is checked first: `if signal is None or not valid_signal(signal)`. So it raises. Good.

What if `provenance` is missing from the payload? `p.get("provenance", ())` returns `()`. `tuple(())` = `()`. Then `SalienceSignal` is constructed with `provenance=()`. This is fine.

What if `provenance` is present but is a list of non-strings? `tuple([...])` would preserve the elements. Then `valid_signal` checks `all(_ref_token(p) for p in s.provenance)`. Non-strings would fail `_ref_token`. Good.

What if `provenance` is an integer? `tuple(5)` raises `TypeError`. This is caught by the `try/except TypeError` around `SalienceSignal(**p)`. Wait, no - the `tuple()` call is OUTSIDE the try/except. Let me re-read:

```python
p = dict(e["payload"])
p["provenance"] = tuple(p.get("provenance", ()))
try:
    signal = SalienceSignal(**p)
except TypeError:
    signal = None
```

If `p.get("provenance", ())` returns an integer (e.g., 5), `tuple(5)` raises `TypeError`. This is NOT caught by the try/except (it's before the try). So `_replay()` would raise `TypeError`, which would propagate up. But wait - the whole thing is inside a `try/except Exception` block:

```python
try:
    e = json.loads(line)
    base = {"kind": e["kind"], "payload": e["payload"], "prev": e["prev"]}
    intact = (
        set(e) == {"kind", "payload", "prev", "hash"}
        and isinstance(e["payload"], dict)
        and e["prev"] == prev
        and digest(base) == e["hash"]
    )
except Exception:
    intact = False
```

No, the `tuple()` call is AFTER the `intact` check. Let me re-read the full code:

```python
for i, line in enumerate(lines):
    try:
        e = json.loads(line)
        base = {"kind": e["kind"], "payload": e["payload"], "prev": e["prev"]}
        intact = (
            set(e) == {"kind", "payload", "prev", "hash"}
            and isinstance(e["payload"], dict)
            and e["prev"] == prev
            and digest(base) == e["hash"]
        )
    except Exception:
        intact = False
    if not intact:
        raise ValueError(...)
    if e["kind"] == "signal":
        p = dict(e["payload"])
        p["provenance"] = tuple(p.get("provenance", ()))
        try:
            signal = SalienceSignal(**p)
        except TypeError:
            signal = None
        if signal is None or not valid_signal(signal):
            raise ValueError(...)
        self._signals.append((e["hash"], signal))
    else:
        self._directives.append((e["hash"], e["payload"]))
    self._entries.append(e)
    prev = e["hash"]
```

So if `provenance` is an integer, `tuple(5)` raises `TypeError` which is NOT caught. This would propagate as a `TypeError`, not a `ValueError`. The bus would fail to open, but with a `TypeError` instead of a `ValueError`. Is this a problem? The bus still fails closed (doesn't extend), but the error type is wrong. The docstring says "raises rather than silently appending" - it does raise, just not `ValueError`.

But wait - can this actually happen? The payload comes from `json.loads()`. JSON doesn't have integers as dict values... actually, it does. `{"provenance": 5}` is valid JSON. And the hash check would pass if the original entry had `"provenance": 5`. But `publish()` uses `asdict(signal)`, which produces `"provenance": [...]` (a list, since tuples are serialized as lists). So a legitimate bus file would never have `"provenance": 5`. This could only happen with a crafted file, which is malicious (out of scope).

Actually, wait. For accidental corruption: if a bit flip changes `"provenance": []` to `"provenance": 0` or something, the hash check would catch it (the payload changed). So this can't happen through accidental corruption.

For a crafted file: the attacker would need to compute the correct hash. That's malicious (out of scope per ADR 0001).

So this is not a real finding.

But actually, let me think about this more. What if `provenance` is a string? `tuple("abc")` = `('a', 'b', 'c')`. This doesn't raise. Then `SalienceSignal(**p)` is called with `provenance=('a', 'b', 'c')`. `valid_signal` checks `all(_ref_token(p) for p in s.provenance)`. Each character is a 1-char string, which passes `_ref_token` (0 < 1 <= 128). So the signal would be accepted. But the hash would need to match the original payload with `"provenance": "abc"`. This can only happen with a crafted file.

Hmm, but what about `provenance` being a list of integers? `tuple([1, 2, 3])` = `(1, 2, 3)`. `SalienceSignal(**p)` is called. `valid_signal` checks `all(_ref_token(p) for p in s.provenance)`. `_ref_token(1)` is `isinstance(1, str)` → False. So the signal is rejected. Good.

## Looking at the `kind` field more carefully

In `_replay()`, the `else` branch stores any non-signal entry as a directive. This means if `kind` is `"directive"`, it's stored as a directive. But what if `kind` is something else, like `"config"` or `"admin"`? It would still be stored as a directive. This is a minor issue - the code doesn't validate that `kind` is either `"signal"` or `"directive"`. But as I noted, the hash check ensures the kind matches, so this can only happen with a crafted file (out of scope).

Actually, wait. Let me think about this from the perspective of the bus's own `_append()` method. `_append()` is called by `publish()` and `emit()`. `publish()` creates `{"kind": "signal", ...}` and `emit()` creates `{"kind": "directive", ...}`. So legitimate entries always have `kind` of "signal" or "directive". The `else` branch in `_replay()` handles the case where `kind` is "directive" (or anything else). This is fine for legitimate files.

For adversarial files: a crafted line with `kind="evil"` and a correct hash would be stored as a directive. But crafting a correct hash is malicious (out of scope).

## Let me look at the `emit()` method more carefully

```python
def emit(self, directive) -> str:
    if type(directive) is not Directive:
        raise TypeError("SalienceBus.emit accepts only a Directive")
    payload = {
        "subject": directive.subject,
        "policy_id": directive.policy_id,
        ...
        "reasons": list(directive.reasons),
    }
    entry = {"kind": "directive", "payload": payload, "prev": self._head}
    return self._append(entry, ("directive", payload))
```

The payload is constructed from the directive's fields. There's no size validation on the fields. A hand-built Directive with `subject = "x" * 1000000` would be emitted to the bus. But the prompt says hand-built directives reaching `decide()` are in scope, not hand-built directives reaching `emit()`. And `emit()` is not a gate - it's the audit recorder.

But the audit fence question is: "can anything prompt-sized become durable?" If a hand-built directive with huge fields reaches `emit()`, yes. But is this in scope? The prompt says "hand-built DIRECTIVES reaching decide()" are in scope. `emit()` is a different path. I think this is out of scope for this review.

Actually, let me re-read the prompt: "the bus's reader/replay" and "the audit fence (can anything prompt-sized become durable?)." The audit fence question seems to be about the bus's structural properties, not about hand-built directives. The bus's `publish()` validates signals (bounded fields). `emit()` doesn't validate directive field sizes. But directives are produced by `interpret()`, which produces bounded fields. So in normal operation, nothing prompt-sized becomes durable.

But what about the `reasons` field? In `interpret()`, reasons are hardcoded strings. But what if a future code change adds a reason that includes user input? That's a future risk, not a current defect.

## Let me look for more subtle issues

### Issue: `_replay()` doesn't validate directive payloads

When `_replay()` encounters a directive entry, it stores the payload as-is without any validation. The payload could have any structure. But the hash check ensures it matches the original. For accidental corruption, the hash catches changes. For malicious crafting, it's out of scope.

But there's a subtle issue: what if a legitimate directive payload is corrupted in a way that changes its structure but not its hash? That's impossible - the hash is over the payload. Any change to the payload changes the hash.

### Issue: `directives_for()` returns dicts, not Directive objects

The docstring says: "Deliberately dicts, not reconstructed Directive objects: presence on the bus is NOT authorization." This is a design decision, not a defect.

### Issue: `signals_for()` returns SalienceSignal objects

```python
def signals_for(self, subject: str) -> tuple:
    return tuple(s for _, s in self._signals if isinstance(s, SalienceSignal) and s.subject == subject)
```

This returns the actual SalienceSignal objects, not copies. Could a caller mutate them? SalienceSignal is a frozen dataclass, so its fields can't be mutated. But the `provenance` tuple is immutable too. So this is fine.

Wait, but what about the `_signals` list? If a caller gets a reference to a SalienceSignal object and it's the same object stored in `_signals`, could they... well, it's frozen, so they can't mutate it. Good.

### Issue: `consume()` passes the handoff from `nominate()` to `retain()`

```python
def consume(outcome, now_days) -> tuple:
    decision = nominate(outcome)
    retention = retain(outcome, now_days, handoff=decision.handoff)
    return decision, retention
```

If `nominate()` raises, `retain()` is never called. But `nominate()` only raises `TypeError` for non-GovernedOutcome input. For valid outcomes, it always returns. Good.

If `retain()` raises (e.g., `HandoffMismatchError`), the exception propagates. But in `consume()`, the handoff comes from `nominate()`, which creates it from the outcome's directive. The attribution check in `retain()` should pass because the handoff's subject matches the outcome's subject, and the directive's rationale is RISK_EXCEEDED. Let me verify...

In `nominate()`:
```python
if rationale is AdaptationRationale.RISK_EXCEEDED:
    handoff = InhibitorHandoff(
        subject=outcome.subject,
        source=HANDOFF_SOURCE_RISK_REJECT,
        rationale=rationale.value,
    )
```

In `retain()`:
```python
attributable = (
    bound
    and handoff.subject == outcome.subject
    and handoff.source == HANDOFF_SOURCE_RISK_REJECT
    and handoff.rationale == AdaptationRationale.RISK_EXCEEDED.value
    and d.adaptation_rationale is AdaptationRationale.RISK_EXCEEDED
)
```

- `bound`: `d is not None and bool(outcome.subject)` - the outcome is bound (directive is not None, subject is non-empty) because `nominate()` only creates a handoff when `d is not None and outcome.subject` is truthy.
- `handoff.subject == outcome.subject`: Yes, by construction.
- `handoff.source == HANDOFF_SOURCE_RISK_REJECT`: Yes, by construction.
- `handoff.rationale == AdaptationRationale.RISK_EXCEEDED.value`: Yes, `rationale.value` where `rationale is AdaptationRationale.RISK_EXCEEDED`.
- `d.adaptation_rationale is AdaptationRationale.RISK_EXCEEDED`: Yes, this is the same `rationale` that `nominate()` checked.

So the attribution always passes in `consume()`. Good.

But wait - what if someone calls `retain()` directly with a handoff from a different outcome? The attribution check would catch this. Good.

### Issue: What about the `reasons` field in `InhibitorHandoff`?

`InhibitorHandoff` has a `reasons` field (default `()`). `nominate()` doesn't set it. `retain()` doesn't check it. Is this a problem? The `reasons` field is just metadata. It doesn't affect the inhibitor logic. But could someone craft a handoff with misleading reasons? Yes, but it doesn't affect the outcome - the attribution check doesn't look at reasons.

### Issue: `_valid_directive` doesn't check all fields

`_valid_directive` checks:
- `type(directive) is Directive`
- `isinstance(directive.verification_depth, int)` and not bool
- `isinstance(directive.adaptation_rationale, AdaptationRationale)`
- ELIGIBLE iff CANDID
