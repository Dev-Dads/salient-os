# Stage-4-live red-team: moonshotai/kimi-k3

_finish=error seconds=677.2 usage={'prompt_tokens': 11964, 'completion_tokens': 11670, 'total_tokens': 23634, 'cost': 0, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.2003949, 'upstream_inference_prompt_cost': 0.0340974, 'upstream_inference_completions_cost': 0.1662975}, 'completion_tokens_details': {'reasoning_tokens': 11670, 'image_tokens': 0, 'audio_tokens': 0}}_

Let me carefully review this Stage-4-live wiring. The task is to adversarially review the wiring in `collaborator/governance.py`, `collaborator/session.py`, the tests, and the live proof, against the gate in `salienceos/consumers/` (which I should assume correct).

Let me go through the attack vectors in order:

## 1. FAITHFULNESS

The `disagreement` flag is computed as:
```python
disagreement = ((not adaptation.nominated) and adaptation.handoff is not None
                and bool(memory.inhibitor))
```

Is this a correct read of the two records? The disagreement is defined as: weight channel BLOCKS learning (not nominated) AND originated the inhibitor handoff (handoff is not None) AND memory channel RETAINS as inhibitor (memory.inhibitor). 

- `not adaptation.nominated` — weight gate refused to learn. Correct.
- `adaptation.handoff is not None` — the weight gate originated an inhibitor handoff, which per the gate only happens on RISK_EXCEEDED. Correct.
- `bool(memory.inhibitor)` — memory retained as inhibitor. Per memory.py, `inhibitor = handoff is not None`, and retain() raises HandoffMismatchError if the handoff can't be attributed. So if consume() returned without raising, and adaptation.handoff is not None, then memory.inhibitor must be True (since consume passes decision.handoff to retain). So the flag is a correct read.

Could it report a disagreement that didn't happen? The flag is derived from the actual records returned by consume(). Since consume() passes the handoff from nominate to retain, and retain validates attribution (raising on mismatch), the only way to get handoff not None and memory.inhibitor True is a genuine RISK_EXCEEDED path. So no false positive from the wiring itself.

Could it miss one that did? If consume() raises (e.g., HandoffMismatchError, or TypeError), the except swallows it and sets disagreement=False, adaptation=memory=None. That's a fail-open on the learning bookkeeping — see fail-safe section.

Is consume() called on the RIGHT outcome? In `execute_and_verify`, consume(outcome, now_days) is called with the `outcome` from `govern(...)` in the artifact verify path. The outcome is the GovernedOutcome for THIS action (bound to action_id via envelope). The directive is the same directive. So yes, same action, bound outcome.

BUT — wait. There's a critical path issue. consume() is only called in the `verify_mode == "artifact"` branch of execute_and_verify! Let me re-read.

```python
def execute_and_verify(session, tool, directive, action_id, args):
    # Read-only: gate already passed; execute and report (nothing is mutated).
    if tool.verify_mode == "none":
        ...
        return Decision(...)  # NO consume() call

    if tool.verify_mode == "exit":
        ...
        return Decision(...)  # NO consume() call

    # verify_mode == "artifact":
    ...
    outcome = govern(...)
    ...
    if getattr(session, "allow_adaptation", False):
        try:
            now_days = ...
            adaptation, memory = consume(outcome, now_days)
            ...
```

So consume() is ONLY called for artifact-verified tools (write_file). For read-only tools (read_file, verify_mode "none") and exit-code tools (run_command, verify_mode "exit"), there is NO outcome produced at all (govern() is never called), so consume() can't be called. Is that a faithfulness gap?

Hmm. The claim is scoped: "this makes the dormant disagreement fire on a real governed action (a risky+important write)". The demo is a write. For read-only and exit-mode tools, no GovernedOutcome is produced by this wiring at all — so there's nothing to consume. Is that a gap? The adaptation signal IS emitted for those tools (in _emit_signals, which is called in govern_action regardless of tool). So a Facet.ADAPTATION signal is emitted for read_file and run_command too, but the outcome is never consumed. 

Is that a problem? For run_command (risk 0.5 > 0.4 cap), if allow_adaptation is on, an ADAPTATION signal is emitted, the directive would record RISK_EXCEEDED, the command runs supervised (exit mode), but no GovernedOutcome is produced, so no consume, no inhibitor. So a risky run_command action would NOT be pinned as an inhibitor even though the disagreement "should" fire. Is that a faithfulness gap or scope?

Actually wait — is that a real scenario? run_command has _TOOL_RISK 0.5, over the 0.4 cap. With allow_adaptation=True and leash act_then_report, a run_command intent would: emit ADAPTATION signal, interpret → directive with adaptation_rationale=RISK_EXCEEDED (asserted risk 0.5 > 0.4 cap), capability gate (needs shell.exec granted), then execute_and_verify → verify_mode "exit" → runs, returns Decision with no outcome, no consume. So the risky command is NOT retained as an inhibitor. The warning is lost.

Is this in scope? The claim is specifically about the write path. The wiring's docstring says "after the governed outcome, calls consume(outcome, now_days)". For exit/none modes there IS no governed outcome in this wiring (govern() isn't called). So the wiring is faithful to its claim for the artifact path. But one could argue this is a gap: the ADAPTATION signal is emitted for all tools, but only artifact-mode tools get consumed. However, the review scope says "is the WIRING faithful" — the claim is about a risky write. I'd flag this as a MEDIUM/LOW note: the disagreement only fires for artifact-verified (mutating file) tools; exit-mode tools (run_command, risk 0.5, over cap) emit the ADAPTATION signal but never produce an outcome to consume, so a risky command is neither learned nor pinned. That's a real gap worth noting — the signal requests consideration for learning but the host never follows through for non-artifact tools. It's not a P-01 violation (no learning happens, which is the safe direction), but it means the inhibitor (the safety warning) is NOT retained for the riskiest tool class (shell). That's a fail-safe-ish gap: the most dangerous tool doesn't get its warning pinned.

Actually, let me reconsider severity. The inhibitor is a safety mechanism (a permanent warning). For run_command at risk 0.5, the warning is NOT pinned because no outcome is produced. So the safety benefit of the disagreement is absent for exactly the riskiest built-in tool. But note: run_command requires shell.exec capability which is NOT granted by default. Still, a session could grant it. I'd call this MEDIUM — a faithfulness/coverage gap, honestly scoped in the claim but worth surfacing.

Let me also check: is consume() called on the outcome regardless of cleared? Yes — consume is called on the outcome whether cleared or not. The design says "the disagreement fires from RISK_EXCEEDED on a BOUND outcome regardless of clearance." The outcome here: the write ran, verification failed (risk 0.9 → FULL depth, one source → not cleared). The directive is bound (subject == envelope_id == action_id). So outcome.directive is the bound directive with adaptation_rationale=RISK_EXCEEDED. nominate() reads outcome.adaptation_allowed (False, since not cleared), then rationale RISK_EXCEEDED → handoff. retain() gets handoff, attributes it (bound, subject matches, rationale matches) → inhibitor=True. Good — faithful.

Now, is the outcome BOUND? govern(session.verifier, directive, env, receipt, world) — env = issue_envelope(action_id, ...), directive.subject = action_id. decide() binds only when directive.subject == verdict.envelope_id. The receipt is issued with action_id. So binding holds. Good.

One more faithfulness check: `now_days = float(getattr(session, "now_days", 0.0) or 0.0)`. The `or 0.0` — if session.now_days is 0.0, `0.0 or 0.0` = 0.0, fine. If now_days is None, `None or 0.0` = 0.0. If now_days is a valid positive float, fine. But what if now_days is NaN? `float(nan) or 0.0` — nan is truthy! So nan passes through. Then consume → retain raises TypeError (now_days must be finite). Caught by except → adaptation=memory=None, disagreement=False. Fail-open but swallowed. Minor. Also negative now_days → retain raises TypeError → swallowed. Minor.

Actually the `or 0.0` is slightly wrong: it conflates "0.0" (falsy but valid) with None, but since 0.0 is the default anyway it's harmless. The real issue is NaN/negative pass through to retain which raises and gets swallowed. But session.now_days is host-set, so this is host config. Low.

## 2. P-01 INTEGRITY

Does emitting Facet.ADAPTATION let salience buy learning/permission it shouldn't?

The ADAPTATION signal: `SalienceSignal("collaborator", action_id, Facet.ADAPTATION, importance, 1.0, ())`. Note it carries `importance` as its value. The adaptation eligibility is gated by the interpreter: allow_adaptation policy flag AND positive ADAPTATION salience AND verification depth AND risk ≤ cap. The signal only influences eligibility (CANDIDATE), and the adaptation gate's only true path is outcome.adaptation_allowed which requires cleared AND CANDIDATE AND world-VERIFIED. And nomination is only for OFFLINE review — no promote/apply. So even if the model could max the ADAPTATION signal, it can only get a nomination for offline review, never learning. P-01 holds structurally.

Can the model influence whether its own risky action is learned or inhibited? The risk is host-computed: `rk = _TOOL_RISK.get(tool.name, 0.3) if risk is None else risk`. Wait — `risk` is a PARAMETER of govern_action! `def govern_action(session, intent, importance=None, risk=None)`. So the caller can pass risk explicitly. Who calls govern_action? The host. But could the model choose the risk? The signature allows risk to be passed in. In the tests/proof, risk=0.9 is passed explicitly. The docstring says "host-computed ATTENTION + RISK; bounded ref-tokens". _TOOL_RISK is "Not model-selectable." But govern_action accepts a `risk` argument that overrides _TOOL_RISK!

So the question: is the `risk` parameter a P-01 hole? If the model (or the agent loop) can call govern_action with a chosen risk, then the model could pass risk=0.0 for a risky action, making the directive record ELIGIBLE instead of RISK_EXCEEDED, and... what? Then adaptation_allowed would still require cleared + verified. For a write that clears verification, adaptation_allowed could be True → nominated for offline review. Still not learned (offline review ceiling). But the inhibitor would NOT be pinned (no RISK_EXCEEDED → no handoff → no inhibitor). So a model that can set risk=0.0 could EVADE the inhibitor pin for its risky action. That's the "can the model influence whether its own risky action is inhibited" attack.

But is `risk` model-selectable in this wiring? govern_action is called by the host agent loop. The material says "signals (influence only: host-computed ATTENTION + RISK...)". The `risk` parameter defaulting to None → host-computed from _TOOL_RISK. The override exists for the host (and the tests use it). Whether the model can reach it depends on the agent loop, which isn't shown. The ToolIntent carries name/args — not risk. So in the shown wiring, risk comes from the host caller, not the ToolIntent. The model produces a ToolIntent (name, args, "structured"), which does NOT include risk or importance. So the model cannot select risk through the shown path. The `risk`/`importance` params are host-side knobs (the agent loop could compute them, e.g., from its own assessment). 

Hmm, but importance: `imp = session.default_importance if importance is None else importance`. Same deal. The demo passes importance=0.9, risk=0.9 explicitly. Is that "host-computed"? In the proof it's hardcoded. The claim says risk is host-computed and not model-selectable. The wiring supports that IF the caller is the host and doesn't take risk from the model. This is a potential concern but not a demonstrated hole — the ToolIntent doesn't carry risk. I'd note it as LOW/MEDIUM: govern_action's `risk` parameter is an override that bypasses _TOOL_RISK; the wiring is only safe if the agent loop never forwards model-influenced values into it. Worth a note: consider making the override host-only by construction, or document that the agent loop must not source risk/importance from model output. Actually the demo itself passes risk=0.9 — which is fine, it's the host asserting the risk. The concern is purely about who calls govern_action. I'll flag as LOW with a concrete scenario.

Could allow_adaptation + a crafted risk value nominate a risky action FOR learning (over-cap risk NOT inhibited)? If risk is set BELOW cap (e.g., 0.3) for a genuinely risky action, the directive records ELIGIBLE (if other conditions met), and if the action clears verification, adaptation_allowed=True → nominated for offline review. But nomination ≠ learning (offline review ceiling, no promote/apply). And the inhibitor is not pinned. So the failure is "risky action not pinned as warning + nominated for offline review." The offline review is the backstop. P-01 is not violated (no capability bought, no learning applied). But the safety pin is evaded. This ties to the risk-override concern. The structural ceiling (nomination is only offline review) means even a crafted risk can't buy learning. Good — that's the P-01 saving grace. I'll note the ceiling holds.

Also: does the ADAPTATION signal carry importance as its value — could a high importance value inflate adaptation? The interpreter uses ADAPTATION salience to decide CANDIDATE vs not. But the risk cap is independent. And nomination ceiling is offline review. So no.

One more: the ADAPTATION signal is emitted with the SAME importance value as ATTENTION. Is that right? The signal value for ADAPTATION is "importance" — meaning "this is important, consider learning it." That's the host asserting the action's importance as the adaptation-request strength. Seems fine as influence.

## 3. HONESTY OF THE CLAIM

The demo action ran but did NOT verify (risk 0.9 → FULL depth, one source → not cleared). Is it honest to call this "the disagreement fires live"?

The design says the disagreement fires from RISK_EXCEEDED on a BOUND outcome regardless of clearance. The proof output shows:
- action ran (file written): True, verified: False
- recorded rationale: RISK_EXCEEDED
- WEIGHT gate nominated=False, handoff=True
- MEMORY gate inhibitor=True, class='ephemeral'
- DISAGREEMENT: True
- weight day 0 = day 100000 = 1.0, NO DECAY

Is the claim honest? The disagreement genuinely fired: the weight gate refused to learn (nominated=False) with a handoff, and memory pinned an inhibitor. The claim doesn't say the action verified — it explicitly notes "verified: False" and the design says clearance isn't required. The summary line for a FAILED action: `[write_file ✗ FAILED — not verified] ... ⟂ LEARNING BLOCKED + RETAINED AS INHIBITOR (channels disagree)`. That's honest — it says FAILED not verified, and reports the disagreement.

Wait — but the proof prints "action ran (file written): True". The status would be FAILED (not cleared). The summary would say "✗ FAILED — not verified". But the proof's ok check: `ok &= (d.disagreement and not d.adaptation.nominated and d.memory.inhibitor and w0 == wfar)`. It doesn't check d.cleared. And it prints verified: False honestly. So the proof is honest that the action didn't verify.

Is there overclaiming? "promoting the Stage-1 disagreement proof from a unit fixture to a live worker." The disagreement did fire on a real governed action (real file write, real interpret, real govern, real consume). That's a fair claim. The word "live" — it's a real governed action through the host, not a fixture. OK.

Does the proof imply the write was verified? No — it prints verified: False. Good.

Does 'ephemeral' class mean something it doesn't? The memory record has retention_class='ephemeral' (the floor) BUT inhibitor=True. The proof prints `class='ephemeral'  (RETAIN as warning)`. Is this honest? The retention_class is ephemeral (lowest durability rung) but the inhibitor flag makes it never decay (effective_weight returns base_weight regardless of age for inhibitors). So the "NO DECAY" claim rests on the inhibitor flag, NOT the retention_class. The proof shows weight 1.0 at day 0 and day 100000 — demonstrating no decay. Is this sound? effective_weight for an inhibitor returns `retention.base_weight + reinforcement_sum` = 1.0, ignoring age. So yes, no decay. The 'ephemeral' class is a bit odd — an "ephemeral" record that never decays because it's an inhibitor. Is that a contradiction the proof glosses over? 

Let me think. retention_class comes from the BOUND directive: `if bound and d.retention_class in RETENTION_ORDER: retention_class = d.retention_class`. The directive's retention_class — for this action, what is it? The policy is issued with "semantic" retention? Let me check: `issue_policy("collab-policy", action_id, tuple(session.capabilities), 10, 1000, 0, 3, "semantic", ...)`. The args to issue_policy — hard to know positions, but "semantic" appears as a retention class hint. But the output shows class='ephemeral'. So the directive's retention_class ended up ephemeral (maybe the interpreter floors it for high risk, or the policy's class is capped). Actually the output says class='ephemeral'. So the bound directive carried retention_class='ephemeral'. 

Hmm, interesting — so the memory record is classed ephemeral (would decay in 0.02 days half-life) BUT the inhibitor flag pins it (no decay). The proof's "NO DECAY" claim is sound because it rests on the inhibitor flag, demonstrated by effective_weight. But is it honest to show class='ephemeral' alongside "RETAIN as warning" and "NO DECAY"? The class is ephemeral but the inhibitor overrides decay. The proof could be clearer that the pin comes from the inhibitor flag, not the class. It does say "(RETAIN as warning)" and demonstrates no decay via effective_weight. I think it's honest but slightly confusing — the 'ephemeral' label might mislead someone into thinking it decays. Actually the proof explicitly shows day 0 = day 100000 = 1.0, so it's demonstrating the pin empirically. I'd call this honest, maybe a LOW note that the ephemeral class + inhibitor pin is subtle but correctly demonstrated.

Actually, wait. Let me reconsider whether the "no decay" evidence is sound. effective_weight(d.memory, 0.0) and effective_weight(d.memory, 100_000.0). For an inhibitor, both return base_weight = 1.0. So near == far == 1.0. The test asserts equal. Sound. But note: this only tests the inhibitor pin, which is a property of the gate (assumed correct). The wiring's contribution is just producing d.memory with inhibitor=True. The test is really testing the gate's effective_weight, not the wiring. That's fine — it's demonstrating the end-to-end result. But one could note the "no decay" is entirely a gate property; the wiring only had to set inhibitor=True. Not a flaw, just an observation that the test's weight is mostly re-testing the gate. Low.

Is there any overclaim in "HARD BLOCK"? nominated=False means not nominated for offline review. "HARD BLOCK" of learning — since there's no promote/apply surface, nothing was ever going to be learned; the block is on nomination. "HARD BLOCK" is fair (the weight channel refuses to even nominate). OK.

## 4. FAIL-SAFE

The consume() call is wrapped:
```python
try:
    now_days = ...
    adaptation, memory = consume(outcome, now_days)
    disagreement = (...)
except Exception:
    adaptation = memory = None
    disagreement = False
```

Is fail-open (no records) the right direction, or could a swallowed exception hide a real inhibitor?

The memory governor's design philosophy: "silently dropping an inhibitor is the fail-OPEN direction" — retain() RAISES HandoffMismatchError rather than drop an inhibitor. But the wiring CATCHES that exception and sets adaptation=memory=None, disagreement=False. So if retain() raises HandoffMismatchError (a handoff that can't be attributed — a genuine anomaly indicating something is wrong), the wiring swallows it and reports NO inhibitor and NO disagreement. The inhibitor is LOST silently. This directly contradicts the gate's fail-closed-on-inhibitor design!

Concrete scenario: suppose a bug or clock issue causes retain() to raise (e.g., now_days negative → TypeError; or a handoff attribution failure → HandoffMismatchError). The wiring catches it, sets memory=None, disagreement=False. The action's report says nothing about learning. A real inhibitor that should have been pinned is silently dropped. The gate deliberately raises to avoid exactly this. The wiring re-introduces the fail-open the gate was designed to prevent.

Is this CRITICAL or HIGH? The comment says "learning bookkeeping must never break the action's report." The intent is that a learning-path error shouldn't mask the action's result. That's reasonable for the ACTION report. But swallowing the exception means a HandoffMismatchError (which signals a serious integrity problem — a handoff that can't be attributed) is invisible. The right design: catch the exception but SURFACE it — e.g., record the error on the Decision, or set a flag, or log to the bus, so a learning-path failure is auditable. The current code loses it entirely.

Note the asymmetry the gate establishes: retain() raising HandoffMismatchError is the fail-CLOSED direction (refuse to misattribute/lose an inhibitor). The wiring catching it and producing no records is fail-OPEN (the inhibitor vanishes). For a safety reviewer, this is the sharpest finding: the wiring defeats the gate's deliberate fail-closed-on-inhibitor posture. Severity: HIGH. The fix: on consume() failure, surface the error (e.g., Decision.learning_error field, or emit to bus, or at minimum don't silently clear disagreement — though you can't know disagreement without records). Actually the honest thing: record that the learning path errored so it's auditable, distinct from "no disagreement." Possibly: `except Exception as exc: learning_error = exc` and include in Decision/summary. The action result (RAN/FAILED) should still be reported — that part is fine — but the learning failure should not be silent.

Let me double check: could a swallowed exception hide a real inhibitor that WAS retained? No — if consume() raises, retain() either raised before returning (no record produced) or nominate raised. consume() does nominate first, then retain. If retain raises, no memory record is returned, so no inhibitor is persisted anywhere (these are in-memory records; nothing writes durably — "no I/O in this package"). So a raise means the inhibitor record was never created. The wiring can't "keep" it. So the swallow genuinely loses the inhibitor. The only correct behavior is to surface the failure. Confirmed HIGH.

But wait — is there a scenario where retain() raises in normal operation? HandoffMismatchError requires the handoff to not attribute — but consume() passes nominate's handoff directly, and nominate only creates a handoff when rationale is RISK_EXCEEDED, and retain checks d.adaptation_rationale is RISK_EXCEEDED. Since both read the same bound directive, attribution should succeed in the normal disagreement path. So HandoffMismatchError shouldn't fire in the happy path. TypeError on now_days could fire if host sets a bad now_days. So the swallow mostly matters for: (a) host misconfiguration (bad now_days), (b) a genuine anomaly/bug. In both cases, silence is wrong. HIGH stands, maybe MEDIUM-HIGH. I'll say HIGH because it defeats an explicit, documented fail-closed design decision in the gate, and the review explicitly asks about it.

Actually, let me reconsider severity calibration. The review prompt says "could a swallowed exception hide a real inhibitor (fail-open on a warning that should have been pinned)? Should a learning-path error be surfaced?" This is attack vector #4. The finding is real. But is it CRITICAL? The action itself still runs and is reported (the safety-relevant capability gate is unaffected). What's lost is the inhibitor pin (a recall-steering warning) and the audit of the failure. Since the memory channel is recall-steering only (not capability), losing the inhibitor doesn't grant capability. So it's not a P-01 breach. It's a loss of a safety warning + loss of auditability. I'd say HIGH (not CRITICAL) because capability authorization is unaffected, but the deliberate fail-closed inhibitor design is defeated and the failure is unauditable.

## 5. SCOPE/DORMANCY

Is it true that allow_adaptation=False produces no ADAPTATION signal, no consume, no inhibitors?

_emit_signals: `if getattr(session, "allow_adaptation", False): sigs.append(ADAPTATION signal)`. So no ADAPTATION signal when off. Good.

consume: `if getattr(session, "allow_adaptation", False): ... consume(...)`. So no consume when off. adaptation=memory=None, disagreement=False. Good.

Also the policy is issued with `bool(getattr(session, "allow_adaptation", False))` as the allow_adaptation arg. So when off, the policy disallows adaptation → directive rationale POLICY_DISALLOWED → even if consume were called, no handoff (POLICY_DISALLOWED is not RISK_EXCEEDED). But consume isn't called. Dormant. Good.

Any path where an inhibitor leaks when adaptation is off? The only way memory.inhibitor is True is via consume() with a handoff, which requires allow_adaptation on AND RISK_EXCEEDED. When off, consume isn't called. No leak. Good.

Any path where a non-risky action gets inhibited? The inhibitor requires RISK_EXCEEDED (asserted risk > cap). A low-risk action (risk 0.0) → rationale ELIGIBLE or UNDER_VERIFIED etc., not RISK_EXCEEDED → no handoff → no inhibitor. The test confirms (risk 0.0 → no inhibitor). Good.

But wait — what about a low-risk action that fails verification? rationale would be... ELIGIBLE (if risk ≤ cap and other conditions) but not allowed due to clearance → "unverified_novelty_excluded", no handoff. So no inhibitor. Good — only RISK_EXCEEDED triggers.

Hmm, one more: the disagreement flag requires handoff is not None. handoff only on RISK_EXCEEDED. So disagreement only on over-cap risk. A non-risky action never gets disagreement=True. Good.

## 6. MISUSE OF THE API

- consume(outcome, now_days): correct signature. outcome is the GovernedOutcome from govern(). now_days is a float. Correct.
- nominate/retain: not called directly by wiring (only via consume). Good — wiring uses the seam.
- effective_weight(d.memory, 0.0) and (d.memory, 100_000.0): correct usage in tests/proof. d.memory is a MemoryRetention. now_days non-negative. Good.
- now_days handling: `float(getattr(session, "now_days", 0.0) or 0.0)`. Discussed — the `or 0.0` masks 0.0 (harmless) but passes NaN through (nan is truthy) → retain raises → swallowed. Minor. Also negative now_days → retain raises → swallowed. The wiring should validate now_days or let the error surface. Tied to finding 4.
- Does the wiring re-derive what it should only consume? The disagreement flag reads adaptation.nominated, adaptation.handoff, memory.inhibitor — all consumed from the records, not re-derived from raw salience or verdict.status. Good — faithful to Finding D (consume, don't re-decide). The wiring does NOT re-derive RISK_EXCEEDED from risk value; it reads the records. 

Actually, let me double-check: the wiring computes disagreement from the records, not from `rk > 0.4`. Good — it consumes the gate's outputs. Faithful.

- Is consume() called on the RIGHT outcome? Yes, the outcome from govern() for this action_id. But note: consume is called BEFORE the Decision is constructed, and the outcome is also stored on the Decision. Same object. Good.

One more API check: `d.outcome.directive.adaptation_rationale` in the proof — outcome.directive is the bound directive. Fine.

Let me reconsider the verify_mode "exit" and "none" paths not calling consume. Is there an outcome to consume? No — govern() is only called in the artifact path. So for read_file and run_command, no GovernedOutcome exists. The ADAPTATION signal is still emitted (in _emit_signals, called from govern_action for all tools). So the signal says "consider this for learning" but no outcome is ever produced for these tools. This is a real asymmetry. For run_command (risk 0.5 > cap), the disagreement would fire IF there were an outcome, but there isn't. So the riskiest tool class never gets the inhibitor pin. 

Is this in scope? The claim is about the write path. But the wiring emits the ADAPTATION signal universally. A faithful wiring would either (a) only emit ADAPTATION for tools that produce a consumable outcome, or (b) produce an outcome for all governed actions. As wired, the ADAPTATION signal for run_command/read_file is a no-op request that's never followed through. For read_file (risk 0.0) it doesn't matter (no inhibitor needed). For run_command (risk 0.5) it matters — the warning is never pinned. MEDIUM.

Actually, let me reconsider: is run_command's risk 0.5 actually over the 0.4 cap? _TOOL_RISK = {"read_file": 0.0, "write_file": 0.1, "run_command": 0.5}. Cap is 0.4 (from issue_policy arg `0.4`). So run_command at 0.5 > 0.4 → RISK_EXCEEDED if the ADAPTATION signal is present and risk is asserted. So yes, with allow_adaptation on, a run_command would record RISK_EXCEEDED on its directive, but no outcome → no consume → no inhibitor. The warning is lost for the riskiest tool. This is a genuine coverage gap. But it requires shell.exec granted (not default). Still, the wiring's own _TOOL_RISK marks run_command as the riskiest, and the disagreement mechanism is exactly for over-cap risk. MEDIUM, arguably HIGH for safety-relevance but I'll say MEDIUM given it needs explicit capability grant and the claim is scoped to writes.

Hmm, wait. Let me reconsider. Is it even true that run_command produces no outcome? verify_mode for run_command — the wiring has three branches: "none" (read_file), "exit" (run_command), "artifact" (write_file). The "exit" branch returns after execute without calling govern(). So no GovernedOutcome. Correct — no consume possible. So the finding holds: the disagreement cannot fire for run_command. 

Let me also reconsider: does the proof/test only exercise write_file? Yes. So the gap is untested and unclaimed. It's a scope note. MEDIUM/LOW. I'll say MEDIUM with the run_command scenario.

Now let me also check the `importance` for the ADAPTATION signal more carefully for P-01. The ADAPTATION signal value = importance (0.9 in demo). The interpreter requires "positive ADAPTATION salience" for CANDIDATE. A high importance → strong ADAPTATION salience → more likely CANDIDATE. But CANDIDATE + over-cap risk → RISK_EXCEEDED (block + inhibitor). CANDIDATE + under-cap risk + cleared + verified → nominated for offline review. So importance can push toward nomination, but the ceiling is offline review. No learning bought. P-01 holds.

Could the model set importance? importance is a govern_action param, default session.default_importance (0.3, host-set). ToolIntent doesn't carry importance. So model can't set it via the shown path. Same note as risk.

Let me now also verify the "no decay" claim isn't overclaimed in a subtle way. The inhibitor never decays — but is it ever RETRIEVED/used? The memory channel is recall-steering. The proof shows the weight stays 1.0. It doesn't claim the inhibitor is acted on. "a pin, not a fading memory" — accurate. OK.

Let me reconsider finding: the summary() for a FAILED action with disagreement: `[write_file ✗ FAILED — not verified] <err> ⟂ LEARNING BLOCKED + RETAINED AS INHIBITOR (channels disagree)`. Honest. For a RAN action with disagreement (if a risky write somehow cleared): `[write_file ✓ ran, verified] ... ⟂ LEARNING BLOCKED + RETAINED AS INHIBITOR`. Also honest. Good.

Now, is there a scenario where disagreement=True but the action CLEARED (RAN)? RISK_EXCEEDED means over-cap risk was asserted. If risk 0.9 → FULL depth. Could a FULL-depth verification clear? The demo says one source → not cleared. But in principle, if verification cleared, outcome.cleared=True, adaptation_allowed still False (RISK_EXCEEDED → not CANDIDATE-allowed... actually adaptation_allowed requires CANDIDATE eligibility; RISK_EXCEEDED means eligibility is not CANDIDATE, so adaptation_allowed=False). So nominate → not nominated, handoff (RISK_EXCEEDED) → inhibitor. disagreement=True even if cleared. Is that correct? Yes — the disagreement is about learning (weight block + memory pin), independent of whether the action cleared. The design says "regardless of clearance." Faithful. Good.

Let me reconsider the fail-safe direction for the action itself. The wiring fails closed on governance errors (DENIED). The learning bookkeeping fails open (no records). The review asks if that's the right direction. For the ACTION, fail-closed is right. For the LEARNING records, fail-open (no records) means losing an inhibitor — which the gate says is the fail-open direction it was designed to avoid. So the learning bookkeeping should at least surface the error. Confirmed finding 4.

Let me also check: is there any issue with `getattr(session, "allow_adaptation", False)` being checked in THREE places (emit_signals, issue_policy, consume)? They're consistent (all read the same session attr). No race (single-threaded). Fine.

Let me reconsider the now_days `or 0.0` once more. `float(getattr(session, "now_days", 0.0) or 0.0)`:
- now_days = 5.0 → 5.0. Good.
- now_days = 0.0 → 0.0 or 0.0 = 0.0. Good.
- now_days = None → None or 0.0 = 0.0. Good (but Session.__init__ does float(now_days), so None would fail there first — actually Session requires now_days as float, defaults 0.0).
- now_days = nan → nan or 0.0 = nan (nan truthy) → float(nan)=nan → retain raises TypeError → swallowed → no records. Minor.
- now_days = -1 → -1 or 0.0 = -1 (truthy) → retain raises → swallowed. Minor.

Session.__init__ does `self.now_days = float(now_days)` — no validation. So a host could set nan/negative. Then consume raises, swallowed. Ties to finding 4. Low on its own.

Now let me think about whether the disagreement flag could be a FALSE positive in any wiring scenario. disagreement = not nominated AND handoff not None AND memory.inhibitor. handoff not None requires nominate to have created it (RISK_EXCEEDED). memory.inhibitor requires retain got a handoff and attributed it. Since consume wires nominate's handoff to retain, and retain validates, both are consistent. No false positive. Could the wiring produce disagreement=True when the gate didn't disagree? No — it reads the gate's records. Faithful.

Could it MISS a disagreement (false negative) other than the swallow? If consume succeeds, the flag is a correct read. The only false negative is the swallow (consume raised) or the exit/none paths (no outcome). Covered.

Let me also double check the claim "no ADAPTATION signal, no consume, no inhibitors (host-dormant)" for allow_adaptation=False. The proof CONTRAST 2 shows adaptation=None memory=None. And _emit_signals doesn't append ADAPTATION. And issue_policy gets allow_adaptation=False. So dormant. True. But note: the ATTENTION and RISK signals ARE still emitted (and published to bus). So "no ADAPTATION signal" is true, but signals are still emitted. The claim is specifically about the ADAPTATION signal. Accurate. Good.

Wait, one subtle thing: when allow_adaptation=False, the policy's allow_adaptation is False, so the directive rationale would be POLICY_DISALLOWED (not RISK_EXCEEDED), even for the risky write. So even if consume were called, no handoff. Double protection. Good.

Now let me reconsider the honesty of "HARD BLOCK" and the overall claim once more. The proof says "the weight gate HARD BLOCKS the skill." nominated=False means not nominated for offline review. Since nomination is the only path to (offline) learning, not-nominated = blocked. "HARD BLOCK" is fair. And "RETAIN as a non-decaying inhibitor (a permanent warning)" — memory.inhibitor=True, no decay. Fair. The claim is honest.

Is there any overclaim about verification? The proof explicitly shows verified: False. The test asserts the file exists (ran) but doesn't claim verified. The summary says FAILED not verified. Honest. Actually — the design INTENDED the action to not clear (risk 0.9 → FULL depth, one source). So the disagreement firing on an unverified action is by design. The claim doesn't hide this. Good.

Let me reconsider: is it a problem that the DEMO action didn't verify? The review says "the demo action ran but did not verify. Is it honest to call this 'the disagreement fires live'?" The disagreement is about LEARNING (weight vs memory), not about verification. The design says it fires from RISK_EXCEEDED on a bound outcome regardless of clearance. So yes, honest — the disagreement genuinely fired (two real records produced by the real gate from a real governed outcome). The unverified status is disclosed. The claim is honest. I'll note this in the verdict/steelman.

Now, is the outcome actually BOUND for the unverified write? govern() → decide() binds directive to verdict when subject == envelope_id. The envelope was issued with action_id, directive.subject = action_id. So bound. The memory record's retention_class comes from the bound directive (ephemeral per output). And the handoff attribution requires bound. Since inhibitor=True in output, attribution succeeded → bound. Confirmed bound. Good.

Let me reconsider one more P-01 angle: the ADAPTATION signal is published to the bus and passed to interpret. Could the ADAPTATION signal influence the CAPABILITY gate? No — allowed_capabilities copied verbatim from policy; "the interpreter has no code path from a signal to this field." So no. Could it influence verification depth or retention class in a way that buys something? Verification depth is influence (scrutiny), not authority. Retention class is salience-bought but policy-capped. The ADAPTATION signal specifically drives adaptation eligibility. Even at max, it can only reach CANDIDATE → nomination for offline review. No capability, no applied learning. P-01 holds.

Now, is there a concern that emitting ADAPTATION with value=importance lets a high-importance risky action get CANDIDATE + then... no, over-cap risk → RISK_EXCEEDED regardless of CANDIDATE. The risk cap is the backstop. And risk is host-computed (modulo the override param). So the only way to get nominated is under-cap risk + cleared + verified + CANDIDATE. That's the designed path. Fine.

Let me finalize findings:

**Finding 1 (HIGH): Swallowed consume() exception defeats the gate's fail-closed inhibitor design; a learning-path error is silently dropped.**
Location: collaborator/governance.py, execute_and_verify, the try/except around consume().
Scenario: retain() raises HandoffMismatchError (or TypeError on a bad now_days) — the wiring catches it, sets adaptation=memory=None, disagreement=False. A real inhibitor that should be pinned is silently lost, and the failure is unauditable. The gate deliberately raises (HandoffMismatchError) because "silently dropping an inhibitor is the fail-OPEN direction." The wiring re-introduces that fail-open.
Fix: catch but surface — record the error on the Decision (e.g., learning_error field), emit to the bus, or include in summary; keep the action's RAN/FAILED report but make the learning failure audible. Don't collapse to "no disagreement."

**Finding 2 (MEDIUM): The disagreement can only fire for artifact-verified tools; run_command (the riskiest, 0.5 > cap) emits an ADAPTATION signal but never produces an outcome to consume, so its warning is never pinned.**
Location: collaborator/governance.py, execute_and_verify — the "none" and "exit" branches return without govern()/consume(); _emit_signals emits ADAPTATION for all tools.
Scenario: session grants shell.exec, allow_adaptation=True, model calls run_command (risk 0.5 > 0.4 cap). ADAPTATION signal emitted, directive records RISK_EXCEEDED, command runs supervised (exit mode), but no GovernedOutcome → no consume → no inhibitor. The riskiest action class gets no warning pin, and the ADAPTATION signal is a no-op request.
Fix: either produce a GovernedOutcome (and consume) for exit/none-mode tools too, or scope the ADAPTATION signal to tools that yield a consumable outcome; at minimum document that the disagreement currently covers only artifact-verified (mutating file) actions.

**Finding 3 (LOW): govern_action's `risk` (and `importance`) parameters override the host-computed _TOOL_RISK; the wiring is only P-01-safe if the agent loop never forwards model-influenced values into them.**
Location: collaborator/governance.py, govern_action signature + `rk = _TOOL_RISK.get(...) if risk is None else risk`.
Scenario: if the agent loop (not shown) ever sources `risk` from model output, the model could pass risk=0.0 for a genuinely risky action → directive records ELIGIBLE instead of RISK_EXCEEDED → no inhibitor pin (and, if it clears, a nomination for offline review). The structural ceiling (nomination = offline review only, no promote/apply) means no learning is bought, so P-01 is not breached — but the safety pin is evaded.
Fix: document that risk/importance must be host-derived only; consider removing the override or asserting host provenance; ToolIntent correctly carries no risk/importance (good).

**Finding 4 (LOW): now_days handling — `float(... or 0.0)` passes NaN/negative through to retain(), which raises and is swallowed (ties to Finding 1); Session doesn't validate now_days.**
Location: collaborator/governance.py (`now_days = float(getattr(session, "now_days", 0.0) or 0.0)`), collaborator/session.py (`self.now_days = float(now_days)`).
Scenario: host sets now_days=-1 or nan → consume raises TypeError → swallowed → no records, no disagreement, silently.
Fix: validate now_days in Session (finite, non-negative) and/or let the error surface per Finding 1.

Let me reconsider whether there are any other findings.

**Honesty check on 'ephemeral':** The proof prints class='ephemeral' alongside "RETAIN as warning" and "NO DECAY." The no-decay rests on the inhibitor flag (effective_weight returns base_weight for inhibitors regardless of class), demonstrated empirically (day 0 = day 100000 = 1.0). So it's sound, but the 'ephemeral' label is superficially confusing (an "ephemeral" record that never decays). Is this a finding? It's honest (the data shows no decay) but could note the subtlety. I'll fold this into a LOW honesty note or mention in verdict. Actually, let me make it a small LOW: the proof could mislead by showing retention_class='ephemeral' without explaining that the inhibitor flag (not the class) is what pins it; a reader might think "ephemeral" implies decay. But since it demonstrates no decay empirically, it's sound. LOW.

Hmm, actually, is there a real question here about whether the inhibitor SHOULD be classed ephemeral? The memory governor classes by the bound directive's retention_class. The directive's retention_class for this action came out ephemeral. That's the gate/interpreter's call (out of scope). The wiring just reads d.memory.retention_class. So not a wiring flaw. I'll note it as an observation, not a finding, or a LOW.

Let me reconsider the "no decay evidence sound" question. The test test_inhibitor_never_decays calls effective_weight(d.memory, 0.0) and (d.memory, 100_000.0). Both return 1.0 (inhibitor). Asserts equal. This is sound but entirely a gate property. The wiring's role was to produce d.memory with inhibitor=True. So the test is mostly re-testing the gate. Not a flaw, but the "no decay" claim's evidentiary weight is: the wiring correctly produced an inhibitor record, and the gate (assumed correct) pins it. Sound. I'll note it's sound.

Let me reconsider whether consume() is called at the right time relative to the Decision. consume is called after govern(), before constructing the Decision. The outcome is final at that point. Good. No issue.

Let me reconsider: does the wiring ever call consume() on a DENIED/HELD/NOTIFIED action? No — those return before execute_and_verify. So no outcome, no consume. Is that right? A DENIED action (capability not granted) — should it be consumed/pinned? The gate's design: deny-shaped outcomes produce refusal records. But the wiring only consumes outcomes from govern() in the artifact path. DENIED actions never reach govern(). So a denied risky action isn't pinned. Is that a gap? The claim is about a RAN risky action. A denied action didn't run, so arguably no incident to pin. But the gate's philosophy says "a denial is an auditable event." However, the wiring doesn't produce a GovernedOutcome for denials (it returns early). So there's nothing to consume. This is consistent with the exit/none gap (Finding 2) — the wiring only consumes artifact-path outcomes. I'll fold the denial case into Finding 2's scope note or leave it. Actually, denials are a different case (no execution). The disagreement is about a risky action that RAN. A denied action didn't run, so no incident. I'll not over-reach; Finding 2 covers the main gap (run_command). 

Actually, let me reconsider — should I flag that HELD (propose_first) actions, when later approved, go through execute_and_verify? Yes — "Used both for act_then_report and for an approved propose_first action." So an approved held action would reach the artifact path and get consumed. Good — consistent.

Let me reconsider the bus.publish / bus.emit exception swallowing. "audit is best-effort; never break the action." That's a separate swallow (audit bus). Is that a concern? The audit bus is best-effort; losing an audit signal is a (minor) audit gap but doesn't affect the action or the learning records. The proof checks chain_intact=True and signals_recorded=3. If publish failed silently, the audit trail would be incomplete but the action proceeds. This is a deliberate best-effort audit choice. Low/not a finding — it's disclosed ("audit is best-effort"). I'll not flag it heavily; maybe mention.
