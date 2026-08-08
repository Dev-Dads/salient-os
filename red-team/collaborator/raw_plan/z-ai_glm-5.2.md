# Collaborator-plan red-team: z-ai/glm-5.2

_finish=stop seconds=144.1 usage={'prompt_tokens': 13627, 'completion_tokens': 11921, 'total_tokens': 25548, 'cost': 0.0120853404, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 12264, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0120853404, 'upstream_inference_prompt_cost': 0.0010703364, 'upstream_inference_completions_cost': 0.011015004}, 'completion_tokens_details': {'reasoning_tokens': 8583, 'image_tokens': 0, 'audio_tokens': 0}}_

# Adversarial Design Review — the Collaborator

---

## F-01 / THE HONESTY CLAIM HAS A REPORTING GAP
**SEVERITY: HIGH**
**PLAN SECTION:** "The thread through all of it" + "What it's made of" §3

**CONCRETE FAILURE:** The plan claims "hands that can't lie about what they did" and "the mind can't paper over a failure with a confident-sounding sentence." But at Step 0 the only surface is the chat window (Step 2's judgment view is explicitly deferred). The tool runs, governed and cleared; the tool returns a result (success, error, or partial output) to the Collaborator; the model then *reports* that result to the human in conversational prose. Nothing in the plan guarantees the human sees the tool's *actual* output rather than the model's *summary* of it. Scenario: the model calls `run_command("rm -rf /tmp/build")`, the command fails with a permission error, the model reports "Cleaned up the build directory successfully." The audit trail has the truth; the human sees the lie. The plan's own motivating example — "we watched it invent a result" — is exactly this class of failure, and the plan claims to have closed it, but has only closed the narrower case (a tool that *didn't run at all* being reported as having run). A tool that *ran and failed* or *ran and returned unexpected output* is still reportable dishonestly.

**WHY IT MATTERS:** This is the plan's central honesty claim and the stated reason for building the Collaborator now. If it doesn't hold at Step 0, the motivating justification is undermined. The gap is between "the hands can't lie" (true — we own tool execution) and "the mind can't paper over a failure" (not yet guaranteed — the mind intermediates the report).

**SUGGESTED FIX:** Add to Step 0's proof: "the tool's actual result (exit code, stdout/stderr, file diff) is shown to the human directly in the session, not solely through the model's prose summary." This makes "each tool reports what it truly did" mean "to the human, not just to the model." The audit trail is the durable record; the inline tool-result display is the real-time honesty guarantee. This is a small addition to Step 0 that closes the claim it's making.

---

## F-02 / THE LEASH IS PRESENTED AS CORE-ENFORCED AUTHORITY BUT ISN'T
**SEVERITY: MEDIUM**
**PLAN SECTION:** "What it's made of" §4 (the governance seam) + "Honest scope"

**CONCRETE FAILURE:** The plan says "policy — your trust setting and the leash — is the only thing that moves the authority dial. That's P-01, made physical." But the real `Directive` dataclass has exactly one authority field: `allowed_capabilities`, copied verbatim from policy, never signal-derived. There is no leash field. The leash (act-then-report / propose-first / notify-only) lives in "host config" — meaning it is enforced by the Collaborator's own code, the same code the model drives. The core cannot enforce that the Collaborator consults a human before acting; it can only enforce capability bounds. Scenario: a bug in the Collaborator's leash-checking code causes a `propose-first` action to execute without waiting for human confirmation. The core's governance seam still runs (capability check, verification, audit), so the action is *governed* but not *held* — the human-in-the-loop guarantee is silently absent. The plan's honest-scope section acknowledges the leash is in host config, but the main body presents it as equivalent to the core's authority enforcement, which it is not.

**WHY IT MATTERS:** The leash is the plan's primary steering mechanism and is described as the physical embodiment of P-01. If a reader (Josh) approves this plan believing the leash is structurally enforced by the judgment core, they'll trust it more than the architecture delivers. The capability boundary is core-enforced; the consultation-timing boundary is Collaborator-enforced. Those are different trust levels.

**SUGGESTED FIX:** Add one sentence to the governance-seam section: "The leash is enforced by the Collaborator's own loop code, not by the salienceos core — the core enforces the *capability* boundary (what the hands may touch); the Collaborator enforces the *consultation* boundary (when the human is asked). Hardening the leash into a core-carried signed policy is the deliberate follow-up noted in Honest Scope." This makes the trust boundary explicit without changing the design.

---

## F-03 / THE "OFF SWITCH" REMOVES GOVERNANCE INSTEAD OF STOPPING THE SYSTEM
**SEVERITY: MEDIUM**
**PLAN SECTION:** "Step 0" (the proof bullets)

**CONCRETE FAILURE:** The plan describes "an off switch that returns it to a plain, ungoverned tool-runner so you can see the governance was load-bearing." An off switch that strips the safety layer and lets the model run tools ungoverned is the opposite of a safe off switch. Scenario: the off switch is toggled during a session (intentionally for debugging, or accidentally), and the model then executes a `run_command` with no governance mediation at all — no capability check, no audit, no human approval. The plan frames this as a demonstration feature, but it introduces a live code path where the model's tool calls run without any mediation, which contradicts the plan's central claim that "every action mediated before it happens" is "not a feature you can turn off."

**WHY IT MATTERS:** The plan says P-01 "is not a feature you can turn off — it *is* the Collaborator." Then it describes a toggle that turns it off. The contradiction is direct. Even for a demo, the existence of an ungoverned code path in the Collaborator is a real attack surface — a bug, a config error, or a model that finds the toggle instruction could land in the ungoverned path during real use.

**SUGGESTED FIX:** Reframe: the Step 0 demo should prove governance is load-bearing by showing a *comparison run* (two separate invocations: one governed, one with a stub governor that always clears) in a controlled test, not by shipping a live toggle that strips governance from the production path. Or, if the toggle must exist, name it "debug-comparison mode," gate it behind an environment variable that cannot be set mid-session, and state explicitly that it never exists in a non-debug build.

---

## F-04 / REVERSIBILITY IS CLAIMED AS A PROPERTY BUT HAS NO MECHANISM
**SEVERITY: MEDIUM**
**PLAN SECTION:** "What it is, in one breath" + leash definitions + Step 0

**CONCRETE FAILURE:** The plan says actions are "reversible" and defines `act-then-report` as "for low-stakes, reversible work." But nothing specifies what makes an action reversible. Scenario: the model writes a file on `act-then-report`, overwriting existing content. The action is governed, cleared, audited. The human later discovers it was wrong. What makes it reversible? There's no backup, no undo log, no snapshot mentioned. The plan claims reversibility as a precondition for the `act-then-report` leash, but the precondition is unenforced — the Collaborator has no way to know whether an action is actually reversible, and no mechanism to reverse it if needed.

**WHY IT MATTERS:** The leash taxonomy depends on reversibility: `act-then-report` is deemed safe *because* the work is reversible. If reversibility isn't guaranteed, the leash is granting autonomy for actions that can't be undone, which breaks the safety reasoning. A file write that overwrites without backup is not reversible; running it on `act-then-report` because "file writes are low-stakes" is a false classification.

**SUGGESTED FIX:** Either (a) add a concrete reversibility mechanism for Step 0's toolset (e.g., file writes create a `.bak` or write to a shadow path; commands run in a working directory that can be reset), or (b) narrow the claim: "act-then-report is for work whose *effects* the human can inspect and correct — not for work that is automatically reversible. The Collaborator does not yet guarantee undo; it guarantees *visibility*." Option (b) is more honest for v0.

---

## F-05 / "EVERY ACTION MEDIATED" IS A CODE-DISCIPLINE GUARANTEE, NOT STRUCTURAL
**SEVERITY: MEDIUM**
**PLAN SECTION:** "What it's made of" §1 + §4

**CONCRETE FAILURE:** The plan says "every proposed action passes through the judgment system before it happens" and "nothing is re-decided by the hands; the hands obey the recorded decision." In the core, this is structurally true — `interpret()` is the only path to a `Directive`, and `decide()` is the only path to a `GovernedOutcome`. But in the Collaborator, the guarantee is procedural: the Collaborator's *code* must call `issue_policy → interpret → govern` before calling a tool. The core cannot enforce that the host calls it. Scenario: the Collaborator has a retry path for failed tool calls. The first call is governed. It fails. The retry code path, written separately, calls the tool directly without re-governing — because "it's the same action, just retried." Or: the Collaborator has a streaming-response handler that starts executing a tool call before the governance call completes (because the stream handler and the governance handler are on different code paths). In both cases, an action runs ungoverned, and the core has no way to prevent it.

**WHY IT MATTERS:** The plan's central safety claim is "every action mediated before it happens." The core enforces this *internally* (structurally). The Collaborator enforces it *externally* (by code discipline). These are different guarantees. The plan should be honest about which one it's making.

**SUGGESTED FIX:** Add to "What it's made of" §1: "The guarantee that every action passes through governance is enforced by the Collaborator's own code structure — a single tool-execution function that wraps every call with a governance check, with no bypass path for retries, streaming, or error recovery. The core cannot enforce that the host calls it; the Collaborator's architecture must." This names the discipline and the specific bypass risks.

---

## F-06 / MULTIPLE AND MID-SENTENCE TOOL CALLS NOT ADDRESSED
**SEVERITY: LOW**
**PLAN SECTION:** "What it's made of" §2 (tool-reading we control)

**CONCRETE FAILURE:** The plan says "every real tool intent is caught" whether structured or plain-text. But it doesn't specify the granularity: if the model emits two tool calls in one response, or interleaves prose with a tool call mid-sentence, or emits a tool call and then *revises it* in the same turn (a streaming partial that changes), does each get its own governance pass? The core's binding key requires each action to use ONE id as both subject and envelope_id. If two tool calls share a governance pass (one policy, one directive for "the turn"), the binding is ambiguous — which action does the directive govern? Scenario: the model emits `read_file("/etc/passwd")` and `write_file("/tmp/x", "data")` in one response. If the Collaborator governs "the turn" rather than each action, the `read_file` (low-risk) and `write_file` (higher-risk) share one directive, one leash, one capability set — the read's low salience dilutes the write's risk.

**WHY IT MATTERS:** The core's design is per-action (one subject, one directive, one verdict). If the Collaborator governs per-turn, the binding-key invariant is undermined and risk salience is averaged across actions of different stakes.

**SUGGESTED FIX:** Add one sentence to §2: "Each tool call is governed as a separate action — its own subject id, its own policy, its own directive — even when multiple appear in one model response." This is a technical-spec detail, but stating it in the plan prevents a per-turn governance design that would break the binding key.

---

## F-07 / AUDIT TRAIL INTEGRITY IS UNADDRESSED
**SEVERITY: LOW**
**PLAN SECTION:** "What it's made of" §4 + Step 0

**CONCRETE FAILURE:** The plan says "every step is written to the audit trail you can read." The core is pure/stdlib and writes nothing — the Collaborator writes the audit trail. The audit trail lives in the Collaborator's storage, outside the core's protection. Scenario: a tool action writes to the same storage that holds the audit trail. A `write_file` that targets the audit log directory (governed, cleared, because file writes are within the capability set) can tamper with the record of past actions. The plan doesn't specify that the audit trail is append-only, stored separately, or protected from the toolset's reach.

**WHY IT MATTERS:** The audit trail is the plan's accountability mechanism — "a plan you confirm, a reach you can see, a leash in your hand, an action that gets written down." If the toolset can write to the audit trail's storage, the "written down" guarantee is mutable by the very actions it's supposed to record.

**SUGGESTED FIX:** Add to the toolset description (§3): "The audit trail is written to a path outside the toolset's reach — the starting toolset cannot write to it, only the governance loop can." Or defer explicitly: "Audit tamper-resistance (append-only, separate storage) is a deliberate follow-up; at Step 0 the audit trail is a file the toolset *can* technically reach, and the toolset's starting scope is chosen to not exercise that." Either is honest; silence is not.

---

## STEELMAN

The plan gets the architecture right in the ways that matter most: it correctly identifies that the Collaborator must be a *sibling* package consuming the core as a library (respecting the stdlib/no-async constraint), it correctly separates salience (scrutiny/compute) from policy (authority) in its prose, and it correctly identifies the box tool-exec gap as a real failure to fix rather than a cosmetic one. The Step-4 tie-in claim — that the Collaborator brings the two learning channels' disagreement to life on real activity — is sound for the disagreement case: setting `allow_adaptation=True` and running a risky action through the interpreter will produce `RISK_EXCEEDED`, which triggers the inhibitor hand-off (memory retains, weights block) without requiring a `VERIFIED` verdict. The plan's honest-scope section is genuinely honest about what's deferred, and the decision-to-build-now reasoning (no desktop needed, closes a real gap, enables the growth story) is coherent and well-argued.

---

## VERDICT

**MINOR_GAPS** — The plan is architecturally sound and honestly scoped for a v0 personal system, but the honesty claim (F-01) is oversold relative to what Step 0's chat-only surface actually guarantees, and the leash/off-switch/reversibility findings (F-02 through F-04) should be tightened before approval so Josh understands which guarantees are core-enforced and which are Collaborator-enforced code discipline.
