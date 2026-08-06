# Red-team (consumers): moonshotai/kimi-k3

_finish=length seconds=751.9 usage={'prompt_tokens': 32128, 'completion_tokens': 12000, 'total_tokens': 44128, 'cost': 0.276384, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.276384, 'upstream_inference_prompt_cost': 0.096384, 'upstream_inference_completions_cost': 0.18}, 'completion_tokens_details': {'reasoning_tokens': 12000, 'image_tokens': 0, 'audio_tokens': 0}}_

Let me work through this carefully. The task is an adversarial red-team of the consumer gates build stage of SalienceOS. I need to find concrete defects with concrete triggering inputs, anchored to file+function.

Let me go through the attack priorities:

1. DISAGREEMENT property failures: inhibitor manufactured without asserted over-cap risk, lost despite one, misattributed to wrong subject, or made to decay.
2. Nomination exceeding single predicate, unverified content nominated, promote/apply surface, capability leak.
3. Seam boundary: directive/outcome shapes that crash gates.
4. Bus: replay/reader correctness under adversarial files (accidental corruption in scope; consistent rewrite and tail-truncation-across-reopen excluded); audit fence.
5. Test honesty: invariant whose test stays green under sabotage.

Let me trace through the code carefully.

### The interpreter's rationale chain

```python
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

Key question: can RISK_EXCEEDED be stamped without an asserted over-cap risk? `risk = agg.get(Facet.RISK, 1.0)`. The branch requires `risk > policy.adaptation_max_risk` AND `Facet.RISK in agg`. If Facet.RISK is in agg, then risk is the aggregated value. So RISK_EXCEEDED requires an actual RISK signal with positive total confidence whose weighted mean exceeds the cap. That seems right.

Wait — but there's a subtlety. `risk` is used for verification depth too: `verif_salience = risk if risk > verify_request else verify_request`. So an asserted high risk raises verification depth. For RISK_EXCEEDED to be reached, we need `v_depth >= policy.adaptation_min_verification`. Since high risk pushes v_depth up, that's usually satisfiable.

Hmm, but here's a thought: the aggregation is a confidence-weighted MEAN. So an attacker who wants to manufacture an inhibitor... wait, manufacturing an inhibitor requires an asserted over-cap risk, which is exactly what the invariant says is the only trigger. The concern is manufacturing one WITHOUT an asserted over-cap risk. Can that happen? The chain order: UNDER_VERIFIED is checked before the risk clause. Test pins that. NOT_REQUESTED before that. POLICY_DISALLOWED first.

What about losing an inhibitor despite an asserted over-cap risk? If risk is asserted over cap but v_depth < adaptation_min_verification, rationale is UNDER_VERIFIED, not RISK_EXCEEDED. Is that "lost despite one"? The design says the priority chain mirrors the original conjunction's condition order, and the test `test_under_verified_wins_over_an_over_cap_risk` explicitly pins this as intended behavior. So per spec, that's intended — the eligibility predicate is unchanged and the rationale records the FIRST failing clause. Hmm, but wait — is that actually a DISAGREEMENT failure? The invariant says "an inhibitor that can be... lost despite one [asserted over-cap risk]". With asserted risk 0.5 > cap 0.4 and adapt_min_v=3, v_depth would be... risk=0.5, verify_request=0, verif_salience=0.5, span = max_v - min_v. With policy min_v=0, max_v=3: v_depth = 0 + round_half_up(0.5*3) = round_half_up(1.5) = 2. 2 < 3, so UNDER_VERIFIED. The inhibitor is lost even though risk 0.5 > 0.4 was asserted. But this is explicitly pinned by test as intended ("a reorder that records RISK_EXCEEDED here would MANUFACTURE an inhibitor for a merely under-verified request"). Hmm, the test's reasoning is that recording RISK_EXCEEDED when the FIRST failing clause is under-verification would be manufacturing. But one could argue the opposite: an asserted over-cap risk occurred, and the incident should be recorded regardless of which clause failed first. The design doc (docx 4.4) says "an ASSERTED over-cap risk (RISK_EXCEEDED) is the ONLY inhibitor trigger". It doesn't say "every asserted over-cap risk MUST trigger an inhibitor". The priority chain is a deliberate design decision, behavior-preserving vs the original. The test pins it. I should note it but it's arguably intended. Actually wait — let me think harder. Is there a case where the FIRST-failing-clause logic loses an inhibitor in a way that contradicts the stated invariant? The invariant under review says: "an inhibitor that can be manufactured without an asserted over-cap risk, lost despite one, misattributed to the wrong subject, or made to decay."

Hmm, "lost despite one" — the UNDER_VERIFIED-wins case is a designed-in loss. But it's pinned by a test that explicitly calls the alternative "manufacture". This is a design tension, not a defect per se. The internal reviews presumably hashed this out. I could mention it as a LOW/design observation. Actually, let me think about whether there's a more clear-cut loss.

What about `policy.allow_adaptation=False` with asserted over-cap risk? POLICY_DISALLOWED wins, no inhibitor. The consumers/__init__.py docstring explicitly acknowledges: "a host policy with allow_adaptation=False therefore produces no inhibitors at all: the disagreement property is library-real and host-dormant until an adaptation path exists there." So that's documented.

NOT_REQUESTED wins over over-cap risk — pinned by test ("no request must not become an incident (the worst manufacture)"). Intended.

OK so the chain itself seems deliberately pinned. Let me look elsewhere.

### nominate() and the handoff

```python
if outcome.adaptation_allowed:
    return AdaptationDecision(... nominated=True ...)
rationale = d.adaptation_rationale
if not isinstance(rationale, AdaptationRationale):
    ... refuse ...
handoff = None
if rationale is AdaptationRationale.RISK_EXCEEDED:
    handoff = InhibitorHandoff(...)
```

Note: `nominate` checks `d = outcome.directive; if d is None or not outcome.subject: refuse`. Good.

But wait — the handoff is originated whenever rationale is RISK_EXCEEDED and adaptation_allowed is False. Can adaptation_allowed be True while rationale is RISK_EXCEEDED? adaptation_allowed requires eligibility CANDIDATE, and _valid_directive requires ELIGIBLE <=> CANDIDATE coherence. So a coherent directive with CANDIDATE has ELIGIBLE rationale. So no.

Hmm, but here's a subtle one: in nominate, the handoff is created when `rationale is AdaptationRationale.RISK_EXCEEDED` regardless of whether the outcome was cleared. Consider: a bound outcome, directive has RISK_EXCEEDED rationale, verdict FAILED. Then cleared=False, adaptation_allowed=False. nominate produces a handoff. Then consume() passes it to retain(), which validates attribution: bound, subject match, source match, rationale string match, d.adaptation_rationale is RISK_EXCEEDED. All true. So an inhibitor is created for content whose action CONCLUSIVELY FAILED. Is that a problem? The memory governor retains uncleared content deliberately ("inhibitors deliberately may retain UNcleared content: incident preservation"). So intended.

### retain() attribution check

```python
attributable = (
    bound
    and handoff.subject == outcome.subject
    and handoff.source == HANDOFF_SOURCE_RISK_REJECT
    and handoff.rationale == AdaptationRationale.RISK_EXCEEDED.value
    and d.adaptation_rationale is AdaptationRationale.RISK_EXCEEDED
)
```

bound = d is not None and bool(outcome.subject). Good.

Misattribution to wrong subject: handoff.subject must equal outcome.subject. But wait — can two different outcomes share a subject? Yes, subject is just a string. But the handoff is validated against THIS outcome's recorded rationale, so a handoff from a different outcome with the same subject and same rationale would be accepted. Is that misattribution? The handoff carries no unique ID tying it to a specific decision — just subject/source/rationale. If two outcomes for the same subject both have RISK_EXCEEDED, a handoff from one could be applied to the other. But the result is the same: an inhibitor record for that subject. The subject is the attribution key. Seems acceptable — the invariant is about wrong SUBJECT, and subject matching is enforced.

Hmm wait, actually there's something. Let me look at `consume()`:

```python
def consume(outcome, now_days) -> tuple:
    decision = nominate(outcome)
    retention = retain(outcome, now_days, handoff=decision.handoff)
    return decision, retention
```

Fine.

### effective_weight and decay

```python
if retention.inhibitor:
    return retention.base_weight + reinforcement_sum
```

Inhibitors never decay. Good. Non-inhibitor: `base_weight * 0.5 ** (age / half_life) + reinforcement_sum`. age clamped >= 0. half_life from HALF_LIFE_DAYS.get(class, floor). Good.

Wait — what about `retention.base_weight`? retain() sets base_weight=1.0. But MemoryRetention is a frozen dataclass; a caller can hand-build one with base_weight=100. The docstring says hand-built MemoryRetention is the caller's problem (out of scope per the calibration: "hand-forged ... MemoryRetention objects ... are OUT OF SCOPE").

Hmm, but wait. Is there a path where retain() itself produces a record whose effective_weight exceeds base + reinforcement? base_weight=1.0 always, recorded_at_days=float(now_days) finite non-negative. age >= 0 after clamp. 0.5**(age/half_life) <= 1. So weight <= 1 + reinforcement. Fine.

What about half_life for "ephemeral": 0.02 days. age/half_life huge → 0.5**huge → 0.0. No overflow (0.5**big → underflow to 0.0, fine). What about age=0 and half_life... 0.5**0 = 1.0. Fine.

### The bus replay

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
            ...
        else:
            self._directives.append((e["hash"], e["payload"]))
        self._entries.append(e)
        prev = e["hash"]
    self._head = prev
```

Wait — `e["kind"]` is checked for "signal", and anything else is treated as a directive. So a hash-correct line with kind="bogus" would be... let me check: base = {"kind": "bogus", ...}, digest matches if crafted. set(e) == the four keys. payload is dict. prev matches. digest matches. So intact=True. Then `if e["kind"] == "signal": ... else: self._directives.append(...)`. So a crafted line with kind="bogus" and a dict payload is ACCEPTED and appended to _directives! Then directives_for would serve it if payload["subject"] matches.

Is this in scope? Adversarial bus FILES are in scope (accidental corruption is the stated scope, but the calibration says "adversarial bus FILES, are in scope"). Hmm wait, ADR 0001 scope: "the bus hash chain detects accidental corruption; consistent malicious rewrite AND tail-truncation-across-reopen need an externally anchored head". A crafted line with kind="bogus" requires the attacker to compute the digest correctly — that's a "consistent malicious rewrite"? Not exactly a rewrite — it's injecting a line with a valid hash. The hash chain is not keyed (digest is unkeyed SHA-256, not HMAC). So anyone can append a well-formed line. The replay fence validates structure: exact key set, payload dict, prev continuity, digest correctness, and for signals, valid_signal re-validation. But for the kind field, there's no validation that kind ∈ {"signal", "directive"}. A kind="bogus" line with a dict payload gets silently treated as a directive and served by directives_for.

What's the impact? directives_for serves dicts, not Directive objects, and "presence on the bus is NOT authorization". So a bogus-kind line served as a directive dict... the consumer of directives_for gets a dict that wasn't emitted by emit(). Since these are just dicts and explicitly not authorization, the impact is limited to audit-record pollution: the reader can't distinguish a "directive" kind from a "bogus" kind. Also _entries includes it and verify_chain would pass (since digest is correct). Hmm.

Actually wait, more importantly: is this "accidental corruption"? No — it requires crafting a correct digest. The scope says the chain detects ACCIDENTAL corruption; a crafted-hash line is malicious. But the fence in _replay is explicitly designed to catch semantic violations in hash-correct lines (the tests: test_persisted_invalid_signal_refuses_to_open, test_smuggled_key_refuses_to_open, test_non_dict_payload_refuses_to_open are all hash-correct crafted lines!). So the design intent is clearly that hash-correct but semantically-invalid lines must be rejected. The kind-field validation is missing: `else:` treats ANY non-"signal" kind as directive. The test `test_non_dict_payload_refuses_to_open` uses kind="directive" with non-dict payload — caught by the isinstance check. But kind="bogus" with dict payload sails through.

Severity: this lets an adversarial file inject a line that gets served by directives_for as if it were a recorded directive. Since directives are served as dicts and explicitly not authorization, the direct security impact is low-ish, but it violates the replay fence's own stated invariant ("a corrupt, tampered, discontinuous, or key-smuggling record raises"). It's an inconsistency in the fence: signals are strictly re-validated, directive kinds are not even checked for kind equality. Also note: a hash-correct "directive" line with arbitrary dict payload (e.g. missing keys, wrong types, huge strings) is also accepted — directives are not schema-validated on replay at all. Is that a problem? The payload could contain prompt-sized strings! The audit fence (Finding G): "can anything prompt-sized become durable?" — via publish/emit, no (valid_signal bounds tokens; emit requires a Directive whose fields come from interpret). But via a crafted FILE line with kind="directive" and a payload containing a 10MB string, the replay accepts it (hash-correct), and directives_for serves it. The smuggled-key test catches extra top-level keys, but the payload VALUES of a directive-kind line are never validated. So a prompt-sized blob CAN become durable and served, through an adversarial file. Hmm — but is an adversarial file with correct hashes "consistent malicious rewrite" (excluded) or in-scope? The calibration says "adversarial bus FILES, are in scope" and the exclusions are only "consistent rewrite and tail-truncation-across-reopen". A single crafted line appended with correct hash is... well, appending a line with a correct hash requires knowing the head, which is in the file. It's a malicious modification, but the _replay fence is explicitly designed and tested to reject hash-correct-but-semantically-invalid lines (invalid signal, smuggled key, non-dict payload). So the fence's scope clearly includes crafted-hash lines. The missing kind check and missing directive-payload validation are gaps in that fence. I'd rate this MEDIUM: concrete trigger (crafted line), violates the fence's own design, but impact limited because directives_for output is explicitly non-authoritative dicts. Actually the prompt-sized durable payload angle pushes it toward the audit-fence concern (attack priority 4: "the audit fence (can anything prompt-sized become durable?)"). Through the file path, yes. But wait — through the file path, anything can become durable; the file is the durable store. The audit fence is about what the BUS API lets through. Hmm, but the replay fence is the thing that decides what a reopened bus will serve from the file. The design clearly intends replay to be a validating gate ("fails CLOSED: a corrupt, tampered, discontinuous, or key-smuggling record raises"). A kind="bogus" line is none of those four categories exactly... it's a semantic violation like the invalid-signal one. I think this is a legitimate MEDIUM finding: `_replay` accepts `kind` values other than "signal"/"directive" and serves their payloads as directives; and never validates directive payload shape at all (contrast with signals, which are re-validated against valid_signal).

Let me double check the exact code path:

```python
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
```

Yes. kind="bogus" → else branch → appended to _directives. Confirmed. Fix: `elif e["kind"] == "directive": ... else: raise`.

Also, another replay issue: `SalienceSignal(**p)` — what if payload has EXTRA keys beyond SalienceSignal fields? TypeError → caught → signal=None → raise. Good. What if payload is missing keys? TypeError → raise. Good. What about `p["provenance"] = tuple(p.get("provenance", ()))` — if provenance is a string "abc", tuple("abc") = ('a','b','c') — each a 1-char string, valid ref tokens! So a persisted signal with provenance="abc" (a string, not list) would be accepted as provenance ('a','b','c'). valid_signal requires isinstance(s.provenance, tuple) — after conversion it IS a tuple. Hmm, is this a real issue? The original publish path requires provenance to be a tuple at valid_signal time, and asdict converts to... asdict keeps tuples? Actually asdict recursively converts; tuples become... let me think. dataclasses.asdict converts tuples to lists? No — asdict converts dataclass instances to dicts, and for tuples/lists/dicts it recurses; a tuple field becomes... Actually `dataclasses.asdict` preserves the type for tuples? Let me recall: asdict uses `_asdict_inner` which handles list/tuple: `type(obj)(_asdict_inner(v) for v in obj)` — so a tuple stays a tuple. Then json.dumps converts tuple → JSON array. On replay, json.loads gives a list, and `p["provenance"] = tuple(...)` converts back. Fine. The string-provenance case: a crafted file line with provenance: "abc" and correct hash → tuple("abc") = ('a','b','c'), all valid 1-char tokens, passes valid_signal. So a signal whose provenance was a string gets accepted with mangled provenance. Impact: negligible (provenance is refs; the signal is otherwise valid and hash-correct). Not worth reporting beyond maybe a note. Actually it IS a semantic alteration of the record that passes verification... but the hash covers the payload as-is (with the string), so the record served differs from... no wait, the record served is built FROM the file; there's no "original" to differ from. The file says provenance="abc"; the bus serves a signal with provenance=('a','b','c'). The hash still verifies because the hash is over the file content. This is a crafted-line semantic quirk. LOW at most. Skip or fold into the replay finding.

Now, another bus thing: `_append` writes to file AFTER updating in-memory state. If the write fails (disk full), in-memory and file diverge; on reopen, replay would fail (missing tail → actually missing tail is fine, head derived from file... the in-memory entries beyond file are lost on reopen, no error). Not a security finding; single-threaded contract. Skip.

What about `directives_for` returning `json.loads(json.dumps(p))` — deep copy. If payload contains non-JSON-serializable values... payloads come from emit() (all JSON-safe) or replay (json.loads output, JSON-safe). Fine.

`signals_for` returns the actual SalienceSignal objects — frozen dataclasses, immutable. Fine.

### The seam: decide() and _valid_directive

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

Hand-built directives reaching decide() are IN SCOPE ("hand-built DIRECTIVES reaching decide()... are in scope"). So let's attack decide() with hand-built directives.

What does decide() read from the directive?
- directive.verification_depth (validated int, not bool)
- directive.subject (used in binding: `bound = bool(directive.subject) and directive.subject == verdict.envelope_id`)
- directive.adaptation_eligibility (coherence-checked)
- directive.adaptation_rationale (type-checked)

What does it NOT validate?
- directive.retention_class: not checked in decide; but retain() checks `d.retention_class in RETENTION_ORDER` and floors otherwise. OK.
- directive.compute_budget, routing_hint, allowed_capabilities, etc.: not read by decide or the consumers. OK.
- directive.subject type: could be a non-string! `bool(directive.subject)` and `== verdict.envelope_id`. If subject is an int 0, bool is False → unbound. If subject is a weird object with a pathological __eq__... type(directive) is Directive, frozen dataclass, but fields can be any object. E.g. subject could be an object whose __eq__ raises or returns True for anything. Hmm — `directive.subject == verdict.envelope_id` — if subject is an object with __eq__ returning True always, then bound=True with bool(subject) presumably True. Then outcome.subject = that object. Then nominate: `if d is None or not outcome.subject` — truthiness of the object; could raise if __bool__ raises... A hand-built directive with a subject object whose __bool__ raises would crash nominate. But wait — is that in scope? "hand-built DIRECTIVES reaching decide()... are in scope". A directive with a non-string subject is pretty adversarial. But the consumers do `bool(outcome.subject)` — outcome.subject is stamped from directive.subject. If directive.subject is an object with raising __bool__, then decide() itself: `bound = bool(directive.subject) and ...` — bool() raises inside decide → decide CRASHES. "a crash is not a deny". Hmm, but is a directive with a non-string subject a realistic "shape that reaches the gates"? The invariant says "a directive or outcome shape that reaches the gates and makes them crash". Directives are supposed to come from interpret(), which always stamps strings. Hand-built directives are in scope per calibration. A subject that's not a string... Let me think about whether this rises above LOW. The calibration says "A finding needs a CONCRETE triggering input; no concrete trigger means LOW at most." I have a concrete trigger: `Directive(subject=BadBool(), ...)` where BadBool.__bool__ raises. decide() crashes instead of denying. But honestly, _valid_directive validates only the fields the seam/consumers depend on for safety decisions; subject is compared with == and bool(). A non-string subject like 123: bool(123)=True, 123 == "act-1" is False → unbound → denied. Fine. A subject of b"act-1" (bytes): == str is False → unbound. Fine. Only a pathological object with raising __bool__/__eq__ crashes. That's quite adversarial; the type fence is `type(directive) is Directive`, and field-level validation is limited to safety-relevant fields. I'd call this LOW: decide() can be crashed by a hand-built Directive whose subject has a raising __bool__/__eq__, violating "a crash is not a deny" — but only with a deliberately pathological object, not a plausible malformed shape. Actually, hmm, let me reconsider — is there a more plausible crash shape?

What about verification_depth being a huge int, like 10**9? `required = max(directive.verification_depth, _stakes_floor(...))` then `required = NONE if required < NONE else FULL if required > FULL else required`. Clamped. Fine. Negative? Clamped to NONE... wait, NONE if required < NONE → required=NONE=0. Then achieved >= 0 always true. So a directive with verification_depth=-5 → required = max(-5, floor). If floor is RECEIPT (LOW stakes), required=RECEIPT. Fine, the floor saves it. If effective_stakes is None (verdict with effective_stakes=None — the _NULL_VERDICT has effective_stakes=None; but a real verdict could have None too — Verdict.effective_stakes default is None!). _stakes_floor(None) → not isinstance(None, Stakes) → FULL. Good, fail-closed.

Hmm wait, actually — what about `Stakes` being an IntEnum or Enum? `isinstance(stakes, Stakes)` — Stakes is imported from salienceos.verifier. It's presumably an Enum (STAKES_ORDER exists, max_stakes). If it's an IntEnum, then isinstance(2, Stakes) is False. Fine.

What about verdict.effective_stakes being a Stakes but verdict being a real Verdict — forged verdicts are out of scope. OK.

Now the achieved_level function: reads verdict.status, verdict.reasons. For a real verdict these are fine.

Let me look at decide()'s binding more carefully:

```python
bound = bool(directive.subject) and directive.subject == verdict.envelope_id
```

verdict.envelope_id is stamped by the verifier pipeline. For a real verdict it's a string. OK.

```python
cleared = bound and verdict.status is not Status.FAILED and achieved >= required
```

verdict.status for a real verdict is a Status. OK.

```python
adaptation_allowed = (
    cleared
    and directive.adaptation_eligibility is AdaptationEligibility.CANDIDATE
    and verdict.status is Status.VERIFIED
)
```

Good.

Outcome stamping: `directive=directive if bound else None, subject=directive.subject if bound else ""`. Good.

Now — here's a thought about `_denied`:

```python
def _denied(reasons, verdict=None) -> GovernedOutcome:
    return GovernedOutcome(
        verdict=verdict if type(verdict) is Verdict else _NULL_VERDICT,
        required_level=FULL, achieved_level=NONE, effective_stakes=None,
        cleared=False, adaptation_allowed=False, reasons=tuple(reasons),
    )
```

directive=None, subject="" by default. Good.

### govern()

```python
def govern(verifier, directive, envelope, receipt, world_evidence):
    if not _valid_directive(directive):
        return _denied(("invalid_directive",))
    escalate = escalation_for(directive.verification_depth)
    verdict = verifier.verify(envelope, receipt, world_evidence, escalate_to=escalate)
    return decide(directive, verdict)
```

Fine.

### Now the consumers again, looking for crashes

nominate(outcome): type-fenced to GovernedOutcome. GovernedOutcome is a frozen dataclass but hand-built ones are... out of scope ("hand-forged GovernedOutcome... OUT OF SCOPE"). So outcomes come from decide(). decide() stamps directive only when bound, and the directive passed _valid_directive. So outcome.directive is always a valid Directive or None. outcome.subject is a string (from directive.subject — which could be a non-string if hand-built directive... but wait, bound requires bool(directive.subject) and == verdict.envelope_id; envelope_id is a string; so a bound subject == a string envelope_id... no! `directive.subject == verdict.envelope_id` — if subject is an object with __eq__ returning True, bound could be True with a non-string subject. Then outcome.subject is that object. Then nominate does `not outcome.subject` → __bool__ → could crash or be False... if __bool__ returns False, `not outcome.subject` is True → refuse path. If __bool__ raises → crash. Again the pathological-object case. LOW.

Let me now look for the more interesting stuff: test honesty (priority 5).

### Test honesty analysis

Claim: "nomination's only predicate is outcome.adaptation_allowed" — test_the_only_true_path_is_adaptation_allowed builds a forged outcome with adaptation_allowed=True over an UNVERIFIED verdict and asserts nominate() nominates. This pins that nominate does NOT re-check verdict.status. Good — but does any test pin that nominate doesn't nominate when adaptation_allowed is False but everything else looks eligible? Yes: test_eligible_but_not_allowed_is_unverified_novelty. And the e2e. OK.

Claim: "inhibitors are exempt from decay" — test_inhibitor_never_decays. Solid.

Claim: Finding C import-graph separation — test parses AST of memory.py and adaptation.py. Solid-ish. It checks `"adaptation" in m` for imports of memory and vice versa. Could someone smuggle an import via importlib? importlib isn't in the allowlist, discipline test would catch the import of importlib. OK.

Claim: schema pins — exact field lists. Solid.

Claim: "a crash is not a deny" for malformed rationale — test_malformed_rationale_refuses_never_crashes uses a forged outcome (hand-built GovernedOutcome) — fine as a belt test.

Now, which invariant's test would stay green under sabotage? Let me look for weak tests.

test_under_verified_wins_over_an_over_cap_risk: pins chain order. If someone reordered the chain to check risk before verification, this test goes red. Good.

Hmm, what about the DISAGREEMENT e2e: test_high_risk_retains_as_inhibitor_and_blocks_weights. If consume() dropped the handoff (called retain without handoff), retention.inhibitor would be False → red. Good.

What about the bus reader test: test_reads_filter_by_subject_oldest_first uses different budgets to pin order. Good.

What about test_returned_copies_cannot_mutate_the_record: mutates the returned dict and checks fresh read. If directives_for returned the stored dict directly (no copy), mutation would reach the stored payload → fresh read would show 999999 → red. Good.

Hmm, what about the audit fence test: test_signal_carries_no_body_fields checks field NAMES only. The real fence is MAX_TOKEN_LEN=128. A 128-char token isn't prompt-sized. But provenance: 16 refs × 128 chars = 2048 chars. Plus subject/subsystem/facet 128 each. Total ~2.4KB per signal. Is 2KB "prompt-sized"? A prompt can be 2KB! Hmm. 16 × 128 = 2048 chars of provenance — that's a paragraph. The fence bounds it, but "prompt-sized" is vague. A short prompt ("ignore previous instructions and...") fits in 128 chars. But the fence is explicitly about bounded tokens, and 128 chars is ref-shaped (a hash is 64 hex chars). I think this is within design. Not a finding.

Let me now hunt for the test-honesty weakness more systematically. The prompt says: "an invariant claimed above whose test would stay green if the code were sabotaged."

Candidate: the invariant "no gate reads verdict.status or raw salience" — is there a test that would catch nominate() reading verdict.status? test_the_only_true_path_is_adaptation_allowed: forged outcome with verdict=UNVERIFIED but adaptation_allowed=True; asserts nominated. If nominate sabotaged to ALSO require verdict.status is VERIFIED, this test goes red. Good.

What about retain() reading verdict.status? Is there a test where retain's behavior would differ if it read verdict.status? retain uses outcome.cleared (carried). If retain were sabotaged to floor retention_class when verdict.status is FAILED... test_binding_not_clearance_selects_the_class uses a FAILED verdict with retention="semantic" and asserts semantic. So that sabotage goes red. Good.

What about effective_weight reading... it takes a MemoryRetention. Fine.

Hmm, what about the claimed invariant "the nomination predicate is exactly bool(outcome.adaptation_allowed)" — sabotage: nominate nominates when adaptation_allowed is True OR rationale is ELIGIBLE. Then attested_eligible_outcome (ELIGIBLE, not allowed) would nominate → test_eligible_but_not_allowed_is_unverified_novelty goes red. Good.

Sabotage: handoff created for RISK_UNKNOWN too → test_risk_unknown_is_not_an_incident goes red. Good.

Sabotage: retain accepts handoff without checking d.adaptation_rationale → test_handoff_attribution_matrix_raises goes red (cases with allowed_outcome/risk_unknown_outcome). Good.

Sabotage: inhibitor decays → test_inhibitor_never_decays red. Good.

Sabotage: decide() stamps directive even when unbound → test_unbound_outcome_withholds_directive_and_subject red. Good.

Sabotage: _valid_directive drops the coherence check → test_desynced_rationale_eligibility_pair_is_denied red. Good.

Sabotage: _replay drops the exact-key-set fence → test_smuggled_key_refuses_to_open red. Drops prev continuity → test_spliced_intact_lines red. Drops signal re-validation → test_persisted_invalid_signal red. Drops digest check → test_tampered_middle red.

Hmm, what about the kind-field gap I found — is there a test that pins kind validation? No! test_non_dict_payload_refuses_to_open uses kind="directive". There's no test with kind="bogus". So my finding is also a test-gap: the fence's kind handling is untested. Consistent.

Now let me look for deeper issues.

### The `emit` payload and `allowed_capabilities`

emit() records `allowed_capabilities: list(directive.allowed_capabilities)` onto the durable bus. The bus docstring says recording a directive is not authorization. directives_for serves these dicts including allowed_capabilities. A consumer reading directives_for could see capabilities — but P-01 says grants_capability() is the only capability accessor, and bus dicts are explicitly non-authoritative. Is serving capability lists from the bus a "capability leak through the consumers"? The consumers (nominate/retain) never read the bus. Hmm, no leak.

### Interpreter: `_aggregate` and the RISK_EXCEEDED condition

One more look at the manufacture angle. `Facet.RISK in agg` — agg includes RISK only if total confidence > 0. risk = weighted mean. Condition: risk > cap. So an asserted over-cap risk = at least one RISK signal with positive confidence pushing the mean over cap. That's the definition. OK.

But wait — what about the interaction with `verify_request`? v_depth uses max(risk, verify_request). For RISK_EXCEEDED we need v_depth >= adapt_min_v. High risk gives high v_depth. With adapt_min_v=2, max_v=3, min_v=0: risk 0.9 → verif_salience 0.9 → v_depth = round_half_up(0.9*3)=round_half_up(2.7)=3 ≥ 2. Good.

Edge: policy with adapt_min_v = max_v = 3, risk asserted 0.9, cap 0.4: v_depth = 3 ≥ 3, risk clause: 0.9 > 0.4 → RISK_EXCEEDED. Good.

Edge: risk asserted EXACTLY at cap: 0.4 > 0.4 False → ELIGIBLE (if other conditions pass). At-cap is not over-cap. Fine.

### Now, a careful look at `retain()`'s bound check and the handoff attribution

```python
d = outcome.directive
bound = d is not None and bool(outcome.subject)
```

For an outcome from decide(), bound outcome ⇒ directive passed _valid_directive and subject non-empty string (well, subject == envelope_id; envelope_id could be ""? If verdict.envelope_id == "" and directive.subject == ""... bound = bool("") and ... = False. So bound requires non-empty subject. But what if envelope_id is "" and directive.subject is ""? bool("") is False → unbound. Good. What if directive.subject == envelope_id == "x"? bound. Fine.)

Attribution requires handoff.subject == outcome.subject. In consume(), the handoff comes from nominate(outcome), which sets handoff.subject = outcome.subject. So they match by construction. External callers passing their own handoff must match subject + source + rationale + the outcome's recorded rationale. Solid.

BUT — here's a subtle one. In `retain()`, the attribution check requires `d.adaptation_rationale is AdaptationRationale.RISK_EXCEEDED`. And `bound` requires `bool(outcome.subject)`. Now consider an outcome where the directive is bound and rationale is RISK_EXCEEDED, but the outcome is adaptation_allowed=True? Impossible for coherent directives (CANDIDATE<=>ELIGIBLE). OK.

What about `handoff.rationale == AdaptationRationale.RISK_EXCEEDED.value` — string comparison. Fine.

### consume() order

nominate first, then retain with decision.handoff. If nominate raises (non-outcome), consume raises — fine, type fence.

### Now let me reconsider the interpreter's `_hard_deny` and the rationale

_hard_deny stamps POLICY_DISALLOWED with eligibility NONE — coherent. Good.

### Interpreter: signals materialization

`signals = tuple(signals)` in try/except. If signals is a dict, tuple(dict) gives keys — then valid_signal filters. Fine.

### verify_policy

Checks signature then coherence. Note: `signature_valid(policy.signed_payload(), ...)` — signed_payload includes all fields. If policy has extra fields... PolicyCaps is a fixed dataclass. Fine.

Hmm, one thing: `verify_policy` catches (TypeError, ValueError) from signature_valid. signature_valid calls sign → hmac.new(key, ...) — if key is not bytes, hmac.new raises TypeError. Caught. Good.

### Back to the bus: `emit` type check

`if type(directive) is not Directive: raise TypeError`. A subclass of Directive rejected — deliberate (type fence precedent). Fine.

### `SalienceBus.__init__` with path that exists but is a directory?

open() raises IsADirectoryError — not caught. Crash on open. Is that in scope? "adversarial bus FILES" — a directory at the path is an environment issue, not a file-content issue. The constructor would raise IsADirectoryError, which is... a crash, but at construction, not a silent corruption. Eh, LOW/non-finding.

### `_replay` and duplicate lines / self-prev

A line whose prev equals its own hash? prev continuity requires e["prev"] == prev (previous line's hash). For the first line, prev="". A crafted first line with prev="" and correct hash is fine — that's the genesis shape. OK.

### Empty file

lines=[] → head="". Reopen continues with prev="". Fine.

### Now — the `directives_for` deep-copy via json round-trip

`json.loads(json.dumps(p))`. If p contains a non-string dict key... payloads from emit have string keys; from replay, json.loads gives string keys. Fine.

### Bigger question: the `consume()` seam and the "weight gate runs FIRST" requirement

Fine as implemented.

### Let me reconsider priority 1 more: "misattributed to the wrong subject"

The handoff carries subject; retain validates handoff.subject == outcome.subject. But consider: nominate() creates the handoff with subject=outcome.subject. What if outcome.subject is valid but the DIRECTIVE's subject differs from outcome.subject? In decide(), outcome.subject = directive.subject when bound. So they're identical. OK.

### Priority 2: "any promote/apply surface"

AdaptationDecision fields: subject, nominated, rationale, handoff, gate_version, reasons. No promote/apply. Schema-pinned. OK.

"capability leak through the consumers": MemoryRetention and AdaptationDecision have no capability fields. nominate/retain never read allowed_capabilities. OK.

### Priority 3: crash shapes

Let me think about what shapes reach the gates from decide():
- outcome.directive is None or a _valid_directive-passing Directive.
- outcome.subject is "" or directive.subject (a string equal to envelope_id... only if envelope_id is a string; for real verdicts yes).

Hmm wait — actually, can decide() produce a bound outcome where directive.retention_class is a non-string? _valid_directive doesn't check retention_class. retain(): `if bound and d.retention_class in RETENTION_ORDER` — `in` on a tuple with an unhashable... `in` uses ==, not hash. `[] in RETENTION_ORDER` → False, no crash. So unhashable retention_class is fine. What about a retention_class object with pathological __eq__? Again pathological. LOW.

What about outcome.cleared? decide sets it to a bool expression. `cleared=bool(outcome.cleared)` in retain. Fine.

What about now_days? Fenced with _real_number and >= 0. bool excluded. nan/inf excluded via (x-x)==0. Wait: inf - inf = nan, nan == 0 is False → excluded. -inf likewise. nan - nan = nan → excluded. Good. What about now_days = 1e308? finite, non-negative. recorded_at_days = 1e308. effective_weight at now_days=1e308: age=0 → base. At now_days=2e308 → inf → fenced. OK. What about age computation overflow: now_days - recorded_at_days with both ~1e308 → fine, floats.

`0.5 ** (age / half_life)` — age/half_life could be huge (age=1e308, half_life=0.02 → 5e309 → inf). 0.5**inf = 0.0. No crash. Good. age/half_life where half_life... HALF_LIFE_DAYS values are all > 0. Unknown class → floor 0.02. Good.

reinforcement_sum: fenced finite. Could be negative! effective_weight returns base*decay + negative → could go negative. Is a negative weight a problem? The docstring says "never above base + reinforcement" — negative reinforcement is allowed by the fence (_real_number, no >= 0 check — wait, let me check: `if not _real_number(reinforcement_sum): raise TypeError`. No non-negativity check! So reinforcement_sum=-100 → weight = -99. Is that a defect? The parameter is "reinforcement_sum" — nothing yet produces reinforcement events ("the parameter is honored in the decay formula"). A negative reinforcement could make an inhibitor's effective_weight negative: `retention.base_weight + reinforcement_sum` = 1 - 100 = -99. Inhibitors "never decay" but negative reinforcement reduces their weight — is that "made to decay"? No — reinforcement is an explicit input, not decay. The inhibitor pin is about the decay term. Negative reinforcement is a caller choice. Hmm, but should the fence reject negative reinforcement? The now_days fence rejects negatives; the reinforcement fence doesn't. Asymmetric but arguably deliberate (reinforcement could model penalties). Not a clear defect. Skip or LOW.

### Let me look again at the one thing that caught my eye: `test_the_only_true_path_is_adaptation_allowed`

This test forges an outcome with adaptation_allowed=True over an UNVERIFIED verdict and asserts nomination. The comment says a gate that re-checks verdict.status would red this. OK, that's the Finding D pin. Fine.

### Now the interpreter's adaptation chain and `v_depth` — one more manufacture angle

RISK_EXCEEDED requires reaching the risk clause, which requires NOT under-verified. v_depth depends on max(risk, verify_request). An attacker wanting to manufacture an inhibitor (asserted over-cap risk is required — so they assert risk 0.9). That's... the designed trigger. The invariant is that asserted over-cap risk is the ONLY trigger. Confirmed.

But wait — there's a flip side: can an attacker SUPPRESS an inhibitor despite asserted over-cap risk by adding signals? Scenario: attacker asserts risk 0.9 (over cap) — that should be an incident. But the chain: if v_depth < adapt_min_v, rationale is UNDER_VERIFIED, not RISK_EXCEEDED. Can v_depth be low despite risk 0.9? v_depth = min_v + round_half_up(max(risk, verify_request) * span). With risk=0.9, verif_salience=0.9. To get v_depth < adapt_min_v, need round_half_up(0.9*span) < adapt_min_v - min_v. E.g. policy min_v=0, max_v=1, adapt_min_v=1: span=1, 0.9*1=0.9 → round_half_up(0.9)=1 → v_depth=1 ≥ 1. Not under-verified. Hmm, need max_v small: min_v=0, max_v=1, adapt_min_v=1, risk=0.4, cap=0.3: verif_salience=0.4 → round(0.4)=0 → v_depth=0 < 1 → UNDER_VERIFIED. Asserted risk 0.4 > cap 0.3, but rationale UNDER_VERIFIED → no inhibitor. But again, this is the pinned priority-chain behavior (test_under_verified_wins_over_an_over_cap_risk). The design explicitly chose first-failing-clause. The invariant "lost despite one" is in tension, but the test suite explicitly pins this as intended and the design doc says the chain mirrors the original conjunction order. I'll note it as a design-tension observation (LOW), not a defect — the reviews clearly already decided this. Actually, the prompt says "your value is finding what those reviews missed, not re-reporting the design." The priority chain IS the design, pinned by tests. So reporting "UNDER_VERIFIED can mask an over-cap risk" is re-reporting the design. Skip it as a finding; maybe mention in steelman/verdict context. Hmm. Actually, let me reconsider once: is there any case where the FIRST-failing-clause logic produces a rationale that is factually FALSE? E.g., risk asserted over cap AND under-verified → records UNDER_VERIFIED. Is UNDER_VERIFIED true? Yes, v_depth < adapt_min_v is true. So the recorded rationale is true; it's just not the whole story. The consumer acts on RISK_EXCEEDED only. The design accepts that an under-verified over-cap-risk request doesn't pin an inhibitor. It's defensible: the content wasn't even adaptation-ready. OK, moving on.

### Now let me look VERY carefully at `_replay` again for the kind issue and any other gap

```python
base = {"kind": e["kind"], "payload": e["payload"], "prev": e["prev"]}
intact = (
    set(e) == {"kind", "payload", "prev", "hash"}
    and isinstance(e["payload"], dict)
    and e["prev"] == prev
    and digest(base) == e["hash"]
)
```

Note: `digest(base) == e["hash"]` — if e["hash"] is not a string (e.g., a list), == is False → rejected. Fine. If e["kind"] is a list → base contains a list → digest works on JSON-serializable... json.dumps with sort_keys on {"kind": [...]} works. digest fine, == e["hash"] — crafted hash could match. Then set(e) check passes (keys are strings). isinstance payload dict. prev matches. So kind=["signal"] (a list) with crafted hash → intact. Then `if e["kind"] == "signal":` — list != str → else → appended to _directives. Same kind-gap. Covered by the same finding.

Also: what about a line where kind="signal" but the payload is a valid signal with a HUGE string within bounds? MAX_TOKEN_LEN=128 enforced by valid_signal on replay. Good.

Directive payloads on replay: NO validation at all. So a crafted directive-kind line can carry arbitrary JSON in payload, including a 10MB string, and it will be served by directives_for. The audit fence (Finding G) is about the durable record not holding prompt-sized content. The publish/emit path enforces it; the replay path re-validates signals but NOT directives. So via an adversarial file (in scope), prompt-sized content becomes durable AND served. But hold on — is "via adversarial file" the right frame for the audit fence? The fence's threat model: the bus as a durable record could be a liability if prompt-sized content flows through the API. The file is already durable storage; an attacker writing the file directly doesn't need the bus. The fence that matters for replay is: does the reopened bus SERVE garbage as if it were a legitimate record? For signals, no (re-validated). For directives, yes (no validation). The asymmetry is the defect: the design clearly intends replay-time validation (three tests pin it for signals/keys/payload-type), but directive payloads and the kind field escape it.

Impact assessment: directives_for returns dicts explicitly marked non-authoritative. Consumers of directives_for get a dict with attacker-controlled content. If any downstream code trusts p["allowed_capabilities"] from a bus read... that would be a P-01 issue, but no code in the material does. So the concrete harm today: (1) kind confusion — a non-directive record served as a directive; (2) arbitrary-size/arbitrary-shape directive payloads served on reopen, bypassing the emit() well-formedness fence. I'll rate MEDIUM, anchored at bus.py _replay.

Actually wait, I want to double-check the "prompt-sized" angle once more. emit() requires a Directive and copies its fields. Directive fields from interpret() are bounded? subject comes from policy.subject — policy is signed, subject could be long? issue_policy doesn't bound subject length! A signed policy with a 1MB subject → directive.subject is 1MB → emit payload carries it → durable. But that requires the policy signing key → out of scope. Signals: bounded by valid_signal. So the API path is fenced (modulo the signing key). The replay path is the gap. OK.

### Now, test honesty — let me find the weakest test

Look at test_consumers_e2e.py::test_low_risk_twin_nominates_and_decays:

```python
self.assertLess(effective_weight(retention, NOW + 180.0),
                effective_weight(retention, NOW))
```

retention class for act-cool: MEMORY 0.9 → retention ladder: max_ret="semantic", idx = clamp(scale(0.9, 0, 3)) = round_half_up(2.7)=3 → semantic. Half-life 180 days. At NOW+180: 0.5. At NOW: 1.0. 0.5 < 1.0 ✓.

Hmm OK. What about the claim "no gate reads verdict.status or raw salience" — for retain(): is there a test that would catch retain() reading outcome.verdict.status? retain reads outcome.directive, outcome.subject, outcome.cleared. If sabotaged to, say, set inhibitor=True when verdict.status is FAILED... is there a test with a FAILED verdict through retain asserting not-inhibitor? test_binding_not_clearance_selects_the_class uses FAILED verdict, asserts retention_class semantic and cleared False — but doesn't assert ret.inhibitor is False! Let me check:

```python
def test_binding_not_clearance_selects_the_class(self):
    denied = decide(directive(depth=FULL, retention="semantic"),
                    verdict(Status.FAILED))
    ret = retain(denied, NOW)
    self.assertFalse(denied.cleared)
    self.assertEqual(ret.retention_class, "semantic")
    self.assertFalse(ret.cleared)
```

No assertion on ret.inhibitor. So a sabotage "retain sets inhibitor on any FAILED verdict" stays green here. But would it stay green across the suite? test_unbound_and_invalid_floor_to_ephemeral asserts not inhibitor for unbound/invalid. The e2e tests assert inhibitor only in the risk case. A FAILED-verdict outcome: does any test assert inhibitor False with a FAILED verdict? Scanning... test_inhibitor_is_not_forced_by_mere_refusal uses attested_eligible_outcome (UNVERIFIED, not FAILED). Hmm. So the specific sabotage "inhibitor on FAILED" — would anything red it? The attribution fence: retain only sets inhibitor when handoff is not None. For the sabotage to matter it must set inhibitor WITHOUT a handoff. Tests asserting inhibitor False: unbound/invalid (no handoff passed), attested (no handoff), allowed outcome (no handoff), low-risk twin e2e (no handoff). A FAILED-verdict-with-no-handoff case asserting inhibitor False: none explicitly. But the sabotage "set inhibitor when verdict FAILED and no handoff" is a weird sabotage. The more natural sabotage "set inhibitor whenever not nominated" IS caught (test_inhibitor_is_not_forced_by_mere_refusal). I think the suite is fairly honest here. The FAILED case is a small gap but the natural sabotages are caught. Not worth a finding? The prompt asks for "an invariant claimed above whose test would stay green if the code were sabotaged" — I need a plausible sabotage that stays green. Let me keep looking.

Candidate: `test_max_risk_one_with_absent_risk_stays_eligible` — pins `>` vs `>=`. Good.

Candidate: the `achieved_level` RECEIPT allowlist — test_attested_with_hard_failure_reason_is_none pins it. Good.

Candidate: `_stakes_floor` fail-closed on non-Stakes — test_unhashable_effective_stakes_denies. Good.

Candidate: escalation upward-only — pinned.

Candidate: bus `verify_chain` — is there a test that verify_chain returns False on corruption? Hmm! Scanning test_bus.py: tests assert verify_chain() is TRUE after operations, and replay REFUSES to open corrupt files (ValueError). But is there a test that verify_chain() itself returns False when the in-memory record is corrupted? The in-memory record can't be corrupted via the API (append-only). The docstring
