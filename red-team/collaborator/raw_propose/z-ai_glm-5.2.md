# Propose-channel red-team: z-ai/glm-5.2

_finish=stop seconds=112.1 usage={'prompt_tokens': 11113, 'completion_tokens': 8517, 'total_tokens': 19630, 'cost': 0.0080302664, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 9446, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0080302664, 'upstream_inference_prompt_cost': 0.0009100544, 'upstream_inference_completions_cost': 0.007120212}, 'completion_tokens_details': {'reasoning_tokens': 5949, 'image_tokens': 0, 'audio_tokens': 0}}_

# Adversarial Design Review — Propose Channel

## FINDING 1

**ID:** AUTH-01
**TITLE:** `approve()` does not re-check the capability gate — the design's claim "the capability gate still rules at run time" is false
**SEVERITY:** CRITICAL
**LOCATION:** `collaborator/loop.py` — `approve()`; design section "The one idea that keeps this safe"
**CONCRETE SCENARIO:**
1. Session grants `shell.exec`. The proposer originates a `run_command` proposal. `govern_action` checks capability → granted → HELD. The directive is stored on the Decision.
2. The host (or a session reconfiguration) revokes `shell.exec` from `session.capabilities`.
3. The user approves the surfaced proposal. `approve()` calls `execute_and_verify(session, tool, decision.directive, …)` directly — it never re-enters `govern_action()`, so the capability gate in `govern_action()` is never re-evaluated. The stale `decision.directive` (which encodes the old grant) is trusted. The command runs despite the capability having been revoked.

The same applies to workspace-fence changes: `_exec_write` re-checks the path (good), but `_exec_command` does not re-check anything beyond running the command.

**WHY IT MATTERS:** The design's central safety argument is: *"surfacing grants nothing; approving runs it through the same approve()/capability-gate path; therefore the model influencing how many proposals surface can at most create noise, never get an action run."* The second clause is false. `approve()` bypasses `govern_action()` entirely and goes straight to `execute_and_verify()`, which contains no capability check. The capability gate ruled at *origination*, not at *run time*. The propose channel makes this materially worse than Step-0: a proposal can sit surfaced for minutes or hours before approval, creating a wide TOCTOU window during which capabilities, workspace roots, or policy keys can change. The design explicitly claims "if somehow approved anyway, denied again at run time" — this is not true for capability revocation.

**SUGGESTED FIX:** `approve()` must re-validate authority against the *current* session state before executing. Either (a) re-call `govern_action()` with the same intent and check for DENIED, or (b) add an explicit `directive.grants_capability(tool.capability)` check inside `approve()` (and re-resolve the workspace path) using the *current* session, not the stored directive. At minimum, re-issue the policy from the current `session.capabilities` and re-interpret. The design text must also correct the false claim.

---

## FINDING 2

**ID:** CONF-01
**TITLE:** The `importance` value passed by the proposer to `govern_action` is unspecified — risk of confidence leaking into salience
**SEVERITY:** MEDIUM
**LOCATION:** Design "Lifecycle" section; `govern_action(session, intent, importance, leash=PROPOSE_FIRST)`
**CONCRETE SCENARIO:** The proposer calls `govern_action(session, intent, importance, leash=PROPOSE_FIRST)`. The design never states what `importance` is. A natural implementation would derive it from the model's self-rated confidence (high confidence → high importance) or from the rationale's perceived urgency. If `importance` is set from confidence, then confidence *does* feed salience — it drives the ATTENTION signal in `_emit_signals`, which influences verification depth and retention. This directly contradicts the design's own invariant: *"confidence is never read again after the gate — it does not feed salience, verification depth, or approval."*

**WHY IT MATTERS:** The design's safety argument for confidence rests on it being *powerless*. If confidence silently becomes importance, it gains influence over verification depth — exactly what the design says cannot happen. This wouldn't violate P-01 (salience influencing scrutiny is allowed), but it would make the design's central claim about confidence dishonest, and it would create a path where a model's self-rating buys less scrutiny for its own proposal.

**SUGGESTED FIX:** The design must explicitly state that `importance` for originated proposals is host-computed (e.g., `session.default_importance`, or a host heuristic over workspace context), never derived from the model's confidence, rationale, or any model output. Add a test asserting `importance` is independent of `confidence`.

---

## FINDING 3

**ID:** AUDIT-01
**TITLE:** No provenance distinction between collaborator-originated and user-instructed actions in the audit trail
**SEVERITY:** MEDIUM
**LOCATION:** `Decision` dataclass (`collaborator/governance.py`); design "Lifecycle" section
**CONCRETE SCENARIO:** A `run_command` proposal is originated by the Collaborator, approved by the user, and runs. Later, a `run_command` is directly instructed by the user and runs. Both produce `Decision` records with `status=RAN`, `leash` computed from session config, and `source` on the ToolIntent indicating parse format ("structured"/"content_json") — not origination source. In a post-hoc audit of the bus or Decision log, the two are indistinguishable. If a proposal caused harm, the audit couldn't tell whether the user asked for it or the Collaborator suggested it.

**WHY IT MATTERS:** The design introduces a new class of action (collaborator-originated) that the existing audit trail doesn't mark. For incident review and trust calibration, knowing *who initiated* an action is essential. The design says "The Collaborator never runs a proposal itself" — but if you can't tell proposals from instructions after the fact, you can't verify that claim from the audit trail alone.

**SUGGESTED FIX:** Add an `originated_by: str` field to `Decision` (values: `"user"` / `"collaborator"`), set by the caller of `govern_action`. The proposer sets `"collaborator"`; the turn loop sets `"user"`. This is additive and doesn't affect authority.

---

## FINDING 4

**ID:** CLAIM-01
**TITLE:** "Changed nothing on disk and nothing in the world" is slightly overclaimed — origination emits salience signals and audit entries
**SEVERITY:** LOW
**LOCATION:** Design "The proof" contrast #4 (Inertness); "The one idea that keeps this safe"
**CONCRETE SCENARIO:** A proposal is originated and governed as HELD. `govern_action` calls `_emit_signals`, publishing `ATTENTION` and `RISK` `SalienceSignal`s to the bus, and the directive is emitted to the bus. These are state changes in the salience system and audit trail. If the bus is persistent (`SalienceBus(path=...)`), these are durable writes. The design says "a surfaced-but-unapproved proposal has changed nothing on disk and nothing in the world" — but it has changed the salience bus state and the audit log.

**WHY IT MATTERS:** The overclaim is minor (salience signals are influence-only and don't grant authority), but it undermines the precision of the central claim. A reviewer or implementer who takes "nothing in the world" literally might not consider whether those signals could influence other actions' scrutiny in a way that matters.

**SUGGESTED FIX:** Refine the claim to: "a surfaced-but-unapproved proposal has changed no *workspace* state and run no *action*; it has emitted influence-only salience signals and an audit record, neither of which confers authority." The contrast test should assert no *workspace artifact* exists, not "nothing in the world."

---

## FINDING 5

**ID:** FAIL-01
**TITLE:** Retention and approvability of unsurfaced HELD proposals is unspecified
**SEVERITY:** LOW
**LOCATION:** Design "Lifecycle" — proactivity gate; `approve()` in `loop.py`
**CONCRETE SCENARIO:** Under CONSERVATIVE dial, a proposal with confidence 0.50 is governed as HELD (capability checked, directive stored) but not surfaced (below 0.80 threshold). The HELD `Decision` object exists in memory. If the proposer or loop retains it, and something later calls `approve()` on it — programmatically, or because the dial is raised to EAGER and a UI re-render surfaces old HELD decisions — the action runs through `execute_and_verify` without re-checking capability (see AUTH-01). Even without the capability issue, a proposal the user never saw could be approved if the retention/approval surface isn't clearly bounded.

**WHY IT MATTERS:** The design says "surface only if confidence ≥ dial threshold" but doesn't say what happens to HELD proposals below threshold. If they're dropped, fine. If they're retained, they're approvable `Decision` objects that the user never reviewed — a gap between "surfacing grants nothing" and "only surfaced things can be approved."

**SUGGESTED FIX:** Specify that proposals below the proactivity threshold are dropped (not retained as HELD). Only surfaced proposals are retained for approval. Add a test: an unsurfaced proposal's `Decision` is not retainable/approvable.

---

## FINDING 6

**ID:** LEASH-01
**TITLE:** The `leash` fallback for invalid values is ambiguous — "safe default" could mean the tool default (which may be ACT_THEN_REPORT)
**SEVERITY:** LOW
**LOCATION:** Design "Per-task adjustable leash" — Bounds; `govern_action` (future `leash` param)
**CONCRETE SCENARIO:** The host passes `leash="auto"` or `leash=None` or `leash=""` to `govern_action`. The design says "an invalid value falls back to the safe default rather than running unleashed." If "safe default" means `_leash_for(session, tool)` (the existing resolution), then for `write_file` the default is `ACT_THEN_REPORT` — the action runs. This is not "unleashed" (capability gate still applies), but it contradicts the implication that an invalid leash doesn't run.

**WHY IT MATTERS:** Minor — the capability gate still protects. But the design's language ("rather than running unleashed") implies the fallback holds the action, which isn't necessarily true. An implementer reading this might code the fallback as PROPOSE_FIRST (over-conservative) or as ACT_THEN_REPORT (under-conservative), and neither is clearly specified.

**SUGGESTED FIX:** Specify explicitly: "an invalid leash value falls back to `_leash_for(session, tool)` — the same resolution as today. This may result in ACT_THEN_REPORT for tools whose default is to run; the capability gate still applies. The fallback never *widens* authority beyond what the session already permits." Or, more conservatively, specify that invalid values fall back to PROPOSE_FIRST.

---

## STEELMAN

The design's core insight — that surfacing is a UX/noise concern separated from authority by the capability gate and the user's approval — is architecturally sound and faithful to P-01. The proactivity dial gating only surfacing (not authority), confidence being clamped and single-use, the forced `leash=PROPOSE_FIRST` at origination, and the strict tool-call parser all contribute to a channel where the model's influence is genuinely bounded to "how chatty is the Collaborator." The fail-closed behaviours (DENIED proposals dropped, unparseable suggestions produce nothing, workspace fence enforced) are well-specified and consistent with Step-0.

## VERDICT

**SERIOUS_FLAWS** — The central claim "surfacing grants nothing" is literally true, but the design's broader safety argument is built on a false sub-claim: `approve()` does not re-check the capability gate at run time, creating a TOCTOU authority leak that the longer origination-to-approval window of the propose channel makes exploitable; this must be fixed before implementation.
