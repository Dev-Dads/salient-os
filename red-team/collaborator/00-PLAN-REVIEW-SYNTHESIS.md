# Collaborator Stage plan — external panel review synthesis

The plain-language Collaborator plan (`docs/collaborator-plain-language.md`) was
reviewed by the general (reasoning) panel before any code, per the H-series process.
Reviewer prompt + bundle: `redteam_collaborator_plan.py` (the plan + the roadmap + the
real `salienceos` core API, so reviewers judged feasibility, not just prose). Raw
per-model output in `raw_plan/`.

## Panel verdicts

| Model | Verdict |
|---|---|
| deepseek-v4-pro | SERIOUS_GAPS |
| x-ai/grok-4.5 | SERIOUS_GAPS |
| mistral-medium-3-5 | SERIOUS_GAPS |
| moonshotai/kimi-k3 | (truncated — finish=error, mid-analysis) |
| z-ai/glm-5.2 | MINOR_GAPS |

Headline consensus was SERIOUS_GAPS (3/5) — **but reproduce-before-accept flipped the
picture.** The lone MINOR_GAPS reviewer (glm) was the one who traced the real code
correctly; the three "serious" verdicts rested partly on a shared misreading (below).
Adjudicated: **architecturally sound and honestly scoped; every real gap closed with a
one-to-two-sentence plan edit; none forced a v0 redesign.**

## The overturned consensus (why reproduce-before-accept matters)

Three models (deepseek F-04, grok F-06, mistral F-10/F-11) claimed the plan's Stage-4
tie-in was oversold — that the memory-RETAIN-inhibitor + weight-HARD-BLOCK disagreement
needs a VERIFIED verdict and world-evidence, so it couldn't fire from a chat/tool loop.
**Traced in code and rejected:** the *disagreement* fires from
`AdaptationRationale.RISK_EXCEEDED` (`interpreter.py:119-124` → `adaptation.py:86-93` →
`memory.py:96-125`). That path needs only a **bound** outcome (`subject==envelope_id`)
carrying an asserted over-cap RISK; `adaptation_allowed`/VERIFIED gates *nomination*, not
the disagreement. The plan's claim was correct; the panel over-corrected. The plan edit
here only *sharpens* the precondition wording, it does not retract the claim.

## Accepted gaps → plan edits (all applied)

1. **[HIGH] Tools could reach their own governance substrate** (5/5). With
   `write_file`+`run_command` and no boundary, the model could overwrite the trust/leash
   config, the policy-signing key, or the audit trail — governing its own governance.
   → Plan now **fences tools to a declared workspace**; config, key, and audit live
   outside it, unreachable; `run_command` is one opaque action on the strictest leash.
2. **[HIGH] Authority under-specified; leash over-elevated** (5/5). `Directive` has no
   leash field — its only authority gate is `allowed_capabilities` / `grants_capability`
   (core-enforced). The plan had called the host-config leash "the authority dial."
   → Plan now names the **capability gate as the one core-enforced authority**, the leash
   as a **second, Collaborator-enforced** axis, and states capability+leash are policy/
   config inputs **never selected by salience or model output**.
3. **[HIGH] No fail-closed default; off-switch stripped governance** (5/5). The plan
   didn't say what happens if the seam errors/unbinds, and its "off switch → ungoverned
   runner" contradicted "P-01 is not a feature you can turn off." → Plan now **denies +
   logs on any seam/tool error (never run-anyway)**, and reframes the off-switch as a
   **build-time A/B side-by-side**, not a live in-session toggle.
4. **[HIGH] Honesty claim oversold at Step 0's chat-only surface** (4/5). Owning the
   parse only closes "never-run narrated as success"; a tool that **ran and failed**
   could still be narrated "done," since Step 0's only surface is chat. → Step-0 proof now
   **shows the tool's real result (exit/stdout/diff) directly**, model narration labeled
   untrusted; the thread's "hands that can't lie" is re-grounded on the audit trail.
5. **[MEDIUM] Parser completeness overclaimed** (2/5). "Every intent caught; nothing
   narrated executed" is a two-way hard claim; a `rm -rf` shown as an example is a mention.
   → Plan now runs **only strict, unambiguous tool calls; ambiguous text fails closed**.
6. **[MEDIUM] Per-action vs per-turn granularity** (5/5). "Governs each action" didn't
   rule out one averaged pass per turn. → Plan now states **each tool call is its own
   governed action** (multiple-in-one-response and retries each judged separately).
7. **[MEDIUM] "Reversible" claimed with no mechanism** (4/5). → Softened to "held to a
   leash / easy to see and correct"; "reversible" reserved for tools that support undo.

## Deferred to the technical spec (not plan-level)

Binding-key plumbing (`subject==signal.subject==envelope_id` per action);
retry/streaming/partial-call handling; propose-first confirmation bound to a frozen
action digest (confirm→execute TOCTOU); action serialization (no concurrent governed
actions); audit append-only + hash-chaining; proposal-timeout-defaults-to-deny and
human-absent behavior; treating untrusted file contents as data never authority; naming
the RISK_EXCEEDED disagreement's four preconditions in the Stage-4-live step's own note.

## Rejected as overstated/invalid (verified against code)

- Stage-4 tie-in "needs VERIFIED verdict" — **overturned above.**
- "Step 0 needs policy-signing infra" — `issue_policy(...)` *is* the signing mechanism;
  the host holds the key (`policy.py:66-95`). The deferred bit is *user-managed*
  provenance, already in honest-scope.
- "Model spoofs the subject/envelope_id binding" — the id is host-minted, the policy
  host-signed; `interpret()` only reads signals whose `subject==policy.subject`, else
  fail-closed (`interpreter.py:70,134`).
- "Verifying a write needs a governed read → infinite regression" — evidence-gathering is
  host plumbing, not a model-issued governed tool call.
- "Model minimizes its own scrutiny via provenance" — absent RISK ⇒ 1.0 ⇒ max
  verification; a model asserting low risk can only lower scrutiny to the policy floor
  (influence, P-01-compliant).

**Outcome:** plan revised on all seven accepted gaps; the design holds. Step 0 is the
build that follows.
