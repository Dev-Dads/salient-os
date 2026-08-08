# Collaborator-plan red-team: deepseek/deepseek-v4-pro

_finish=stop seconds=166.4 usage={'prompt_tokens': 14080, 'completion_tokens': 9382, 'total_tokens': 23462, 'cost': 0.0094360812, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0094360812, 'upstream_inference_prompt_cost': 0.004045184, 'upstream_inference_completions_cost': 0.0053908972}, 'completion_tokens_details': {'reasoning_tokens': 6904, 'image_tokens': 0, 'audio_tokens': 0}}_

**ADVERSARIAL DESIGN REVIEW — SalienceOS Collaborator Plan**

---

### FINDINGS

**ID: F-01**
**TITLE: Unrestricted “run a command” tool bypasses governance seam, breaking P-01**
**SEVERITY: CRITICAL**
**PLAN SECTION:** “What it's made of” — §3 (A small, honest toolset)
**CONCRETE FAILURE:**
The toolset includes “run a command.” Once the collaborator authorises the tool call, the spawned process can execute arbitrary actions (delete files, access network, modify system configuration) with no further governance. The governance seam (issue_policy → interpret → govern → consume) mediates only the *decision to invoke the tool*, not what the command actually does. Importance (salience) or model output can influence the command arguments, effectively granting arbitrary permission—e.g., a highly salient task might receive `rm -rf /` as a parameter. This directly violates P‑01: salience can indirectly expand the system's effective reach far beyond the static `allowed_capabilities` tuple.

**WHY IT MATTERS:**
The entire safety architecture rests on “importance buys effort, never permission.” A tool that can do anything after a single capability check hands unlimited permission to every governed execution of that tool. The leash (act‑then‑report) cannot make the command reversible; a destructive command cannot be undone.

**SUGGESTED FIX:**
Either restrict the toolset so that every command is individually governed (e.g., a command executor that only runs a fixed, pre‑approved list of sub‑commands with parameter validation, each of which itself passes through the governance seam), or sandbox the tool severely (e.g., a container with read‑only filesystem) and document that the user accepts the residual risk. At minimum, any “run command” capability must always be bound to `propose-first` leash, and the plan must explicitly state that the command's effects are the user's responsibility—the OS cannot guarantee they are governed.

---

**ID: F-02**
**TITLE: No specified fail‑closed default on collaborator governance pipeline errors**
**SEVERITY: HIGH**
**PLAN SECTION:** “Step 0” and “The governance seam” (§4)
**CONCRETE FAILURE:**
The plan describes the loop (read → think → act → report) and the governance seam (pass through judgment system), but never states what happens if the collaborator cannot reach the core (network timeout, interpreter crash, malformed directive) or the govern() call raises an exception. The core itself is fail‑closed (hard deny on invalid input), but the collaborator is a new piece of code that could silently skip the governance step or proceed with the action if the call fails. The “off switch that returns it to a plain, ungoverned tool‑runner” suggests that bypass is explicitly possible—potentially even the default under error conditions.

**WHY IT MATTERS:**
An adversarial model or a simple transient error could cause dangerous actions to execute ungoverned. The system must default SAFE when the governance infrastructure is unavailable.

**SUGGESTED FIX:**
Add an explicit requirement: “Every code path that would result in a tool being executed MUST pass through the governance seam. If the seam returns an error, a `cleared=False` outcome, or cannot be reached, the action MUST be denied and the event logged.” The loop must implement a strict deny‑by‑default pattern; any “off switch” must be a deliberate, signed policy change, not a runtime fallback.

---

**ID: F-03**
**TITLE: Leash determination may allow salience to influence authority**
**SEVERITY: MEDIUM**
**PLAN SECTION:** “What it's made of” — §4 (The governance seam) & “The leash is the steering wheel, set per task”
**CONCRETE FAILURE:**
The plan states that importance only moves scrutiny/compute, while policy moves authority (the leash). However, the mechanism for choosing a leash per‑task is unspecified. If the collaborator uses salience signals (e.g., task confidence, risk assessments) to decide whether to use `act‑then‑report` vs. `propose‑first`, then salience is effectively moving the authority dial. The plan’s current wording (“trust/leash lives in host config to start”) does not prevent a future engineer from implementing a salience‑based leash selector under the guise of “convenience.”

**WHY IT MATTERS:**
A leak here would directly break P‑01. Even in a personal system, the rule must be architectural rather than advisory.

**SUGGESTED FIX:**
Mandate that the leash for a given task is derived *only* from the signed policy object (initially the host config). The directive or policy should carry a `leash` field that is never touched by salience signals. The interpreter already leaves `allowed_capabilities` untouched; extend the same pattern to leash. The plan should state: “The leash is read from the policy and is not subject to salience, budget, or model output.”

---

**ID: F-04**
**TITLE: Missing credible verification evidence source for tool actions undermines adaptation claim**
**SEVERITY: MEDIUM**
**PLAN SECTION:** “Honest scope” — Stage‑4 tie‑in and “the learning gate”
**CONCRETE FAILURE:**
The plan claims the Collaborator will be the first host to fire the two‑channel disagreement (memory inhibit vs. weight block) on real activity. This requires adaptation‑eligible (`allow_adaptation=True`) **and** verified (`VERIFIED` verdict) outcomes. For tool actions like file writes or command executions, obtaining two independent world facts (needed for `FULL` verification) is far from trivial. The plan does not describe any verifier pipeline, receipts, or world evidence. Without a concrete verification strategy, the collaborator will only produce `RECEIPT` or `UNVERIFIED` verdicts, meaning `adaptation_allowed` will be `False` and the learning gates will remain dormant.

**WHY IT MATTERS:**
The tie‑in to Stage‑1 proof is oversold; if verification is impossible in this context, the claim that “this disagreement first fires on real activity” is false or will require a degenerate verifier that returns `VERIFIED` without real evidence, which would undermine the honesty claim.

**SUGGESTED FIX:**
Either descope the adaptation tie‑in for initial collaborator releases (acknowledge that only memory retention works) or specify a minimal verification scheme (e.g., a single source such as file checksums plus trust in the OS process exit code, mapped to `INDEPENDENT` or `RECEIPT` with clear documentation that deeper verification is future work). The “honest scope” should explicitly note that full adaptation eligibility may not be reachable in Step 0.

---

**ID: F-05**
**TITLE: Step 0 is infeasible without a policy‑signing infrastructure (hidden dependency)**
**SEVERITY: HIGH**
**PLAN SECTION:** “Step 0” and the core API (`interpret()` requirement)
**CONCRETE FAILURE:**
The core’s `interpret(policy, signals, policy_key)` calls `verify_policy(policy, policy_key)`. If the policy is not signed or the key is invalid, the interpreter returns a `_hard_deny` directive with empty capabilities. The plan promises a governed loop where actions are cleared, but the “trust/leash lives in host config” approach must be translated into a valid signed policy object to even get past the interpreter. The plan does not mention how policies will be signed or keys managed. Without this, Step 0 either cannot demonstrate governance (all actions denied) or must bypass the core, defeating the purpose.

**WHY IT MATTERS:**
Step 0 is the first milestone; if it can't demonstrate a working governed loop with cleared actions, the entire plan rests on an untested foundation.

**SUGGESTED FIX:**
Include in Step 0 a minimal policy‑signing mechanism: the collaborator signs a self‑generated policy with an embedded key (or a host‑provided key) for the session. Clarify that this is not the hardened, user‑managed policy of the future, but it is sufficient to exercise the governance seam end‑to‑end. Acknowledge this as a deliberate scaffold in the “honest scope.”

---

**ID: F-06**
**TITLE: Multi‑action turns, retries, and chained tools may escape individual governance**
**SEVERITY: MEDIUM**
**PLAN SECTION:** “The loop we own” and “Tool‑reading we control”
**CONCRETE FAILURE:**
The plan says “every action … passes through the judgment system before it happens,” but it does not specify that each discrete tool invocation is governed independently. If a model emits multiple tool calls in one response, a naïve implementation might batch them under one governance decision or execute all if the first passes. Furthermore, if a tool action fails and the collaborator retries, the retry might not be re‑governed. The core API requires a unique subject/envelope binding per action; batching would bind multiple dissimilar actions to one directive, granting blanket permission.

**WHY IT MATTERS:**
This could allow a tool call that would have been denied on its own to slip through as part of a batch or a retry loop.

**SUGGESTED FIX:**
The plan should explicitly state: “Each tool invocation—including retries and subsequent steps in a chained sequence—is a separate action that must be independently governed, with its own unique envelope_id. The loop must iterate over tool calls, govern each, and execute only those that are individually cleared.” This requirement belongs in the “What it's made of” section under the governance seam.

---

**ID: F-07**
**TITLE: “Reversible” claim is unsupported by the described toolset**
**SEVERITY: LOW**
**PLAN SECTION:** “What it is, in one breath”
**CONCRETE FAILURE:**
The one‑breath summary claims “visible, and reversible.” For commands that modify files or execute side‑effects, reversal is often impossible without pre‑existing snapshots. The plan does not describe any undo mechanism (e.g., backup, transactions), so the claim is misleading.

**WHY IT MATTERS:**
Overpromising damages trust when the user discovers a command cannot be undone.

**SUGGESTED FIX:**
Qualify the statement: “visible, and where the toolset allows, reversible (e.g., file operations are backed up).” Or remove “reversible” until a concrete undo mechanism is built.

---

### STEELMAN

The plan correctly identifies the critical gap in the existing rig (unparsed plain‑text tool calls) and proposes owning the entire loop to close it, which is a genuine architectural improvement. It faithfully maintains the P‑01 separation in its stated intent: salience influences scrutiny/compute/retention, while policy—embodied in the leash and trust dial—governs permission. The decision to build the Collaborator as a sibling package that consumes the judgment core without modifying it is exactly the right discipline, and the “honest scope” section admirably surfaces what is deferred.

---

### VERDICT: **SERIOUS_GAPS**

The plan contains a critical violation of the system's core invariant (unrestricted command execution breaking “importance never grants permission”) and lacks essential fail‑safe defaults; until these are resolved, the design cannot be safely built.
