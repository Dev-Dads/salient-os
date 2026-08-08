# Collaborator-plan red-team: mistralai/mistral-medium-3-5

_finish=stop seconds=44.4 usage={'prompt_tokens': 14262, 'completion_tokens': 7348, 'total_tokens': 21610, 'cost': 0.076503, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.076503, 'upstream_inference_prompt_cost': 0.021393, 'upstream_inference_completions_cost': 0.05511}, 'completion_tokens_details': {'reasoning_tokens': 0, 'image_tokens': 0, 'audio_tokens': 0}}_

### **Adversarial Design Review: SalienceOS Collaborator Plan**

---

---

## **FINDINGS**

### **P-01 INTEGRITY VIOLATIONS**
#### **F-01: Leash/Trust Dial Authority Leak**
- **SEVERITY**: CRITICAL
- **PLAN SECTION**: *"The governance seam"* (leash description) + *"Importance (salience) moves the scrutiny and compute dials; policy is the only thing that moves the authority dial."*
- **CONCRETE FAILURE**:
  The plan states that the **leash** (act-then-report / propose-first / notify-only) is "set per task" and is part of the **directive** output by the interpreter. However, the real core API (`interpret()`) shows that `allowed_capabilities` is the **only** authority field in `Directive`, and it is **copied verbatim from policy** (`policy.granted_capabilities`). The leash is **not** a field in `Directive`—it is a **consumer-side concept** (e.g., a UI control or host config).
  **Scenario**:
  - A high-salience task could influence the interpreter to set `verification_depth=FULL` (max scrutiny), but the **leash** (which gates *whether* the action runs at all) is not part of the governed `Directive`.
  - If the leash is stored in **host config** (as admitted in *"Honest scope"*), then a misconfiguration or model-driven manipulation of that config could **bypass the governance seam entirely** (e.g., a model convincing the host to set `leash=act-then-report` for a high-stakes action).
  - **P-01 violation**: The leash **is** authority (it decides if an action runs unsupervised), but it is **not** in the policy-enforced `Directive`. Thus, salience (via host config) could **indirectly buy permission** by influencing the leash setting.
- **WHY IT MATTERS**:
  The entire safety model relies on **policy == authority**. If the leash is not part of the signed policy, then the governance seam is **incomplete**—there exists a path where an action runs without being bound by the core’s `decide()` gate.
- **SUGGESTED FIX**:
  The leash **must** be part of the `Directive` (or a new field in `Policy` that the interpreter enforces). The plan must explicitly state:
  > *"The leash is a policy field, not host config. The interpreter will reject any directive where the leash exceeds the policy’s maximum permitted autonomy."*

---

#### **F-02: "Propose Channel" Authority Ambiguity**
- **SEVERITY**: HIGH
- **PLAN SECTION**: *"It comes to you — it notices something worth doing and brings you a proposal"* + *"Step 1 — depth and the two-way channel"*
- **CONCRETE FAILURE**:
  The plan describes the Collaborator **proposing actions unprompted** (e.g., "notices something worth doing"). However:
  - The core API’s `interpret()` **does not have a "proposal" mode**—it only arbitrates **existing** signals into a directive.
  - The **propose-first leash** implies the Collaborator can **generate** a task (not just execute one). But the core’s `subject` binding requires `directive.subject == verdict.envelope_id`. If the Collaborator **creates** the task, who signs the `envelope_id`? If the model generates the proposal, then the `subject` is **model-derived**, which could be **ungoverned**.
  - **Scenario**:
    - Model notices a missing driver (Stage 4 example) and **proposes** installing it.
    - The Collaborator generates a `subject="install_driver_X"` and a corresponding `envelope_id`.
    - If the model **controls the subject**, it could **spoof** the binding, making the governance seam **bypassable**.
- **WHY IT MATTERS**:
  Proposals are **new authority**—they are not just executing a user’s intent but **generating** it. If the proposal’s `subject` is not **user-signed**, then the core’s binding invariant (`directive.subject == verdict.envelope_id`) is **meaningless** for proposals.
- **SUGGESTED FIX**:
  The plan must specify:
  > *"Proposals are treated as user-initiated tasks with a synthetic `envelope_id` signed by the **user’s policy key**, not the model. The Collaborator may only propose actions whose `subject` is pre-approved by policy (e.g., a allowlist of proposal types)."*

---

---

### **SEAM FEASIBILITY & COMPLETENESS**
#### **F-03: Partial/Streamed Tool Calls Bypass Governance**
- **SEVERITY**: CRITICAL
- **PLAN SECTION**: *"Tool-reading we control"* + *"every real tool intent is caught"*
- **CONCRETE FAILURE**:
  The plan claims to fix the "box tool-exec gap" (where plain-text tool calls are dropped). However:
  - The core API’s `govern()` expects **one** `Directive` per action. But a model can emit:
    - **Multiple tool calls in one turn** (e.g., `tool_call_A; tool_call_B`).
    - **Partial/streamed tool calls** (e.g., `tool_call_` emitted mid-sentence, then completed later).
    - **Nested tool calls** (e.g., a tool that triggers another tool).
  - The plan does **not** specify how the Collaborator **atomizes** these into individual governed actions.
  - **Scenario**:
    - Model emits: `"Let me first <tool>read_file</tool> and then <tool>delete_file</tool>."`
    - If the Collaborator treats this as **one** action, the governance seam only sees **one** `Directive` (for the combined intent), but the **actual** actions (`read_file`, `delete_file`) are **not individually mediated**.
    - The `delete_file` could slip through **ungoverned** if the combined directive’s `subject` does not match its `envelope_id`.
- **WHY IT MATTERS**:
  The core’s `decide()` gate **only works per-action**. If the Collaborator batches multiple tool calls into one governance check, then **some actions run without individual verification**.
- **SUGGESTED FIX**:
  The plan must explicitly state:
  > *"Every tool call (structured or parsed from plain text) is treated as a **separate** governed action. The Collaborator will split multi-tool turns into individual `Directive` requests, each with its own `subject` and `envelope_id` binding."*

---

#### **F-04: Model Editing Its Own Governance Inputs**
- **SEVERITY**: HIGH
- **PLAN SECTION**: *"The governance seam"* + *"the hands obey the recorded decision"*
- **CONCRETE FAILURE**:
  The plan does not address **recursive governance attacks**, where the model:
  1. **Modifies its own policy** (e.g., via a `write_file` tool to edit the host config).
  2. **Modifies the audit trail** (e.g., via a `write_file` tool to alter logs).
  3. **Modifies the verifier’s evidence** (e.g., via a `run_command` tool to fake world facts).
  - The core API’s `govern()` assumes the `envelope`, `receipt`, and `world_evidence` are **trusted inputs**. If the Collaborator allows the model to **generate or modify** these, then the governance seam is **self-compromising**.
  - **Scenario**:
    - Model writes a fake `envelope_id` to a file, then references it in a tool call.
    - The Collaborator reads the file as "world evidence," and the verifier **trusts it** (since it’s presented as external).
    - The `decide()` gate **clears** the action because the fake evidence matches the fake `envelope_id`.
- **WHY IT MATTERS**:
  The core’s safety relies on **external** (user/policy-signed) inputs. If the model can **inject** its own governance inputs, then **P-01 is violated by construction**.
- **SUGGESTED FIX**:
  The plan must state:
  > *"The Collaborator will **never** allow the model to modify: (1) policy files, (2) audit logs, (3) verifier evidence sources. These are **read-only** to the model, enforced by the toolset."*

---

#### **F-05: Retries/Errors Leak Ungoverned Actions**
- **SEVERITY**: HIGH
- **PLAN SECTION**: *"every action it takes on the system is governed, visible, and reversible"*
- **CONCRETE FAILURE**:
  The plan does not specify how **failed tool calls** are handled. The core API’s `govern()` returns a `GovernedOutcome` with `cleared=False` for failures, but:
  - If a tool **fails**, does the Collaborator **retry** it automatically?
  - If a retry **succeeds**, is it **re-governed**, or does it inherit the first attempt’s `Directive`?
  - **Scenario**:
    - Model calls `delete_file("important.txt")` → governed, **denied** (high stakes).
    - Model **retries** with a slightly different call (`delete_file("temp.txt")`).
    - If the retry is **not re-governed**, it could slip through with a **stale** `Directive` (e.g., one that was approved for a different `subject`).
- **WHY IT MATTERS**:
  Retries are **new actions** and must be **re-governed**. Otherwise, the model can **brute-force** past governance by iterating on tool calls.
- **SUGGESTED FIX**:
  The plan must state:
  > *"Every tool call (including retries) is governed **independently**. No action inherits a prior `Directive`; each must bind to its own `envelope_id`."*

---

---

### **THE HONESTY CLAIM**
#### **F-06: Model Misreporting Tool Results**
- **SEVERITY**: HIGH
- **PLAN SECTION**: *"the hands that can't lie about what they did"* + *"a tool that didn't run says so"*
- **CONCRETE FAILURE**:
  The plan claims the Collaborator **owns the parse** and thus **guarantees honesty**. However:
  - The core API’s `govern()` only verifies **before** the action runs (via `Verdict`).
  - The **result** of the tool call (success/failure) is **not** part of the governed outcome. The `GovernedOutcome` only records:
    - Whether the action was **cleared** (`cleared=True/False`).
    - Whether it was **verified** (`adaptation_allowed`).
  - **Scenario**:
    - Model calls `run_command("rm -rf /")` → governed, **denied** (`cleared=False`).
    - The tool **does not run**, but the model **reports to the user**: *"I deleted the files as requested."*
    - The **audit trail** shows the action was **denied**, but the **user sees a lie**.
- **WHY IT MATTERS**:
  The plan’s honesty claim is **incomplete**. Owning the parse ensures the **action** is governed, but it does **not** ensure the **model’s narrative** matches reality. The user could be **misled** even if the system is technically safe.
- **SUGGESTED FIX**:
  The plan must state:
  > *"The Collaborator will **never** let the model narrate the result of a tool call. Instead, it will **inject** the true outcome (from the tool’s return) into the conversation, overriding any model-generated summary."*

---

#### **F-07: Governed-but-Failed Actions Reported as Success**
- **SEVERITY**: HIGH
- **PLAN SECTION**: *"a tool that didn't run says so"*
- **CONCRETE FAILURE**:
  The plan says tools **report what they truly did**, but:
  - The core API does **not** enforce that the tool’s **return value** is part of the `GovernedOutcome`.
  - If a tool **fails silently** (e.g., returns exit code 0 but does nothing), the Collaborator might **not detect** the failure.
  - **Scenario**:
    - Model calls `write_file("test.txt", "hello")` → governed, **cleared**.
    - The tool **fails** (e.g., disk full), but returns `success=True` (buggy tool implementation).
    - The Collaborator **reports success** to the user, but the file was **not written**.
- **WHY IT MATTERS**:
  The honesty claim requires **end-to-end** truthfulness. If the toolset is **unreliable**, the Collaborator can **lie by omission**.
- **SUGGESTED FIX**:
  The plan must state:
  > *"Every tool **must** return a **verifiable receipt** (e.g., file hash, command exit code + stdout/stderr). The Collaborator will **cross-check** the receipt against the action’s intent before reporting success."*

---

---

### **FAIL-SAFE POSTURE**
#### **F-08: Default-Safe on Model Confusion/Adversarial Inputs**
- **SEVERITY**: HIGH
- **PLAN SECTION**: *"when the model is confused/adversarial"* (implied by "hands that can't lie")
- **CONCRETE FAILURE**:
  The plan does **not** specify the **default behavior** when:
  - The model emits **malformed tool calls** (e.g., invalid JSON).
  - The model **refuses to cooperate** (e.g., "I won’t do that").
  - The governance seam **fails to bind** (e.g., `directive.subject != verdict.envelope_id`).
  - The **verifier crashes** or returns an **inconclusive** `Verdict`.
  - **Scenario**:
    - Model emits a tool call with a **non-string `subject`** (e.g., `subject=123`).
    - The core’s `decide()` **fails closed** (`_valid_directive()` returns `False`), but the Collaborator **does not specify** whether it:
      - **Denies** the action (safe).
      - **Silently drops** the tool call (unsafe, as the model might retry).
      - **Crashes** (unsafe, as it breaks the loop).
- **WHY IT MATTERS**:
  Fail-safe requires **explicit defaults**. The plan must **guarantee** that **any** failure mode **denies/holds/notifies**, never acts.
- **SUGGESTED FIX**:
  The plan must state:
  > *"In **any** failure mode (malformed input, governance mismatch, verifier error), the Collaborator will: (1) **deny** the action, (2) **log** the failure, (3) **notify** the user, and (4) **continue** the loop (no crash)."*

---

#### **F-09: Human Absent → No Action**
- **SEVERITY**: MEDIUM
- **PLAN SECTION**: *"It comes to you — it notices something worth doing and brings you a proposal"*
- **CONCRETE FAILURE**:
  The plan describes the Collaborator **proposing actions unprompted**, but does **not** specify:
  - What happens if the user **does not respond** to a proposal?
  - What happens if the user is **offline**?
  - **Scenario**:
    - Collaborator proposes: *"Should I delete old temp files?"* (leash=`propose-first`).
    - User **ignores** the proposal.
    - Does the Collaborator:
      - **Timeout and deny** (safe)?
      - **Escalate** (e.g., notify again)?
      - **Act anyway** (unsafe)?
- **WHY IT MATTERS**:
  Unprompted actions **must not** proceed without **explicit** user approval. The default must be **deny**.
- **SUGGESTED FIX**:
  The plan must state:
  > *"Proposals **time out** after X minutes (configurable) and **default to deny**. No proposal may auto-escalate to action."*

---

---

### **THE STAGE-4 TIE-IN**
#### **F-10: Adaptation Path Weakens P-01**
- **SEVERITY**: CRITICAL
- **PLAN SECTION**: *"the Collaborator becomes that host"* (for adaptation) + *"Stage 4 — it grows itself"*
- **CONCRETE FAILURE**:
  The plan claims the Collaborator will **host the two learning channels** (memory RETAIN + weight HARD BLOCK). However:
  - The core API’s `consume()` **only runs** if `outcome.adaptation_allowed=True`.
  - `adaptation_allowed` requires:
    1. `directive.adaptation_eligibility == CANDIDATE` (policy + salience gated).
    2. `verdict.status == VERIFIED` (world-corroborated).
  - But the plan **does not specify** how the Collaborator **ensures** that:
    - The **action** is **verified** (not just cleared).
    - The **policy** allows adaptation (`allow_adaptation=True`).
  - **Scenario**:
    - User sets `allow_adaptation=False` in policy.
    - Collaborator runs a **high-salience, high-risk** action (e.g., installing a driver).
    - The action is **cleared** (`achieved >= required`), but **not verified** (`verdict.status != VERIFIED`).
    - The `consume()` gate **does not run** (since `adaptation_allowed=False`).
    - But the **memory channel** might still **retain** the action as a warning (if `RISK_EXCEEDED`).
    - **Problem**: The **weight channel** (learning) is **dormant**, but the **memory channel** is **active**. The plan claims the **disagreement** (retain vs. block) is **observable**, but if the weight channel is **never triggered**, there is **no disagreement**—just a **one-sided retain**.
- **WHY IT MATTERS**:
  The Stage-4 claim (**"the two channels disagreeing on real activity"**) is **only true if adaptation is enabled**. If `allow_adaptation=False`, the weight channel **does nothing**, and the disagreement **never fires**. Thus, the plan **oversells** the Collaborator’s role in Stage 4.
- **SUGGESTED FIX**:
  The plan must state:
  > *"The Collaborator **only** makes the two-channel disagreement observable **if** `allow_adaptation=True` in policy. If adaptation is disabled, the weight channel is dormant, and no learning occurs (only retention)."*

---

#### **F-11: Verification Evidence for Tool Actions is Underspecified**
- **SEVERITY**: HIGH
- **PLAN SECTION**: *"VERIFIED outcome"* (Stage-4 tie-in)
- **CONCRETE FAILURE**:
  The plan assumes that **tool actions** can be **verified** (for adaptation eligibility). However:
  - The core API’s `verifier` expects **world evidence** (e.g., independent sources).
  - For **local tool actions** (e.g., `write_file`, `run_command`), what counts as **verification**?
    - Is a **file hash** sufficient for `write_file`?
    - Is a **command exit code** sufficient for `run_command`?
  - The plan **does not specify** how the Collaborator **generates** this evidence.
  - **Scenario**:
    - Model calls `write_file("config.json", "{...}")`.
    - The Collaborator **reads back** the file to verify it was written.
    - But if the **read** is **governed**, it requires its own `Directive` → **infinite regression**.
- **WHY IT MATTERS**:
  Without **concrete verification mechanisms**, the `VERIFIED` status is **meaningless** for tool actions. The plan must define **what "verified" means for each tool**.
- **SUGGESTED FIX**:
  The plan must state:
  > *"For each tool, the Collaborator defines a **verification receipt** (e.g., file hash for `write_file`, exit code + stdout for `run_command`). The verifier treats these receipts as **world evidence** for the action’s `envelope`."*

---

---

### **SCOPE HONESTY**
#### **F-12: Step 0 Hides Dependencies**
- **SEVERITY**: MEDIUM
- **PLAN SECTION**: *"Step 0 — the governed loop exists and obeys"* + *"Honest scope"*
- **CONCRETE FAILURE**:
  The plan claims Step 0 is a **real governed loop** doing **real work** against a **real model**. However:
  - The **real model** is not specified (is it local? API-based?).
  - The **verification evidence** for tool actions is not defined (see F-11).
  - The **binding key plumbing** (how `envelope_id` is generated/signed) is not described.
  - **Scenario**:
    - Step 0 is built, but the **model is remote** (e.g., OpenAI API).
    - The **verifier** expects **local world evidence** (e.g., file hashes), but the model’s actions are **remote** (e.g., cloud file edits).
    - The **binding** fails because the `envelope_id` is **not signed by the user’s policy key**.
- **WHY IT MATTERS**:
  Step 0 is **not coherent** if the **dependencies** (model, verification, binding) are not **explicitly** part of the scope.
- **SUGGESTED FIX**:
  The plan must state:
  > *"Step 0 assumes: (1) a **local model** (for deterministic testing), (2) a **pre-defined verification receipt** for each tool, (3) a **user-signed `envelope_id`** for every action. These are **not** part of Step 0 but are **prerequisites**."*

---

#### **F-13: Leash/Trust in Host Config is a Gap**
- **SEVERITY**: HIGH
- **PLAN SECTION**: *"The trust/leash lives in host config to start"* (Honest scope)
- **CONCRETE FAILURE**:
  The plan **admits** that the leash/trust dial is in **host config** (not signed policy). This means:
  - The **authority** (leash) is **not** governed by the core’s `interpret()`.
  - A **misconfigured host** could set `leash=act-then-report` for a **high-stakes** action.
  - **P-01 violation**: Salience (via host config) **buys permission**.
- **WHY IT MATTERS**:
  This is a **known gap** in the plan. The "honest scope" section **acknowledges** it but does **not** commit to fixing it. This is **unacceptable** for P-01 integrity.
- **SUGGESTED FIX**:
  The plan must **remove** this caveat or **explicitly** state:
  > *"The leash **will** move into signed policy before Step 1. Step 0 is **incomplete** until this is done."*

---

---

### **MISSING PIECES**
#### **F-14: No Concurrency Model**
- **SEVERITY**: HIGH
- **PLAN SECTION**: (Not addressed)
- **CONCRETE FAILURE**:
  The plan does **not** specify how the Collaborator handles:
  - **Parallel tool calls** (e.g., model emits two tools at once).
  - **Race conditions** (e.g., two actions modifying the same file).
  - **State consistency** (e.g., a `read_file` followed by a `write_file` in the same turn).
  - **Scenario**:
    - Model emits: `write_file("A.txt", "1"); write_file("A.txt", "2")`.
    - If these run **concurrently**, the final state of `A.txt` is **non-deterministic**.
    - The governance seam **cannot** verify the **final state** if the actions are **not serialized**.
- **WHY IT MATTERS**:
  Without a **concurrency model**, the Collaborator cannot **guarantee** that governed actions are **atomic** and **consistent**.
- **SUGGESTED FIX**:
  The plan must state:
  > *"The Collaborator will **serialize** all tool actions. No two governed actions may run concurrently. The model may propose parallel actions, but they will be **queued and executed sequentially**."*

---

#### **F-15: No Audit Tamper-Resistance**
- **SEVERITY**: HIGH
- **PLAN SECTION**: *"written to the audit trail you can read"*
- **CONCRETE FAILURE**:
  The plan claims the audit trail is **visible and reversible**, but:
  - It does **not** specify how the audit trail is **protected** from:
    - **Model tampering** (e.g., `write_file` to the log).
    - **User tampering** (e.g., manual edits).
    - **Crash corruption** (e.g., partial writes).
  - **Scenario**:
    - Model calls `write_file("audit.log", "")` to **clear the logs**.
    - The Collaborator **allows** this (since it’s a tool call).
    - The **governance history is lost**.
- **WHY IT MATTERS**:
  An audit trail that can be **modified** is **not an audit trail**. The plan must **guarantee immutability**.
- **SUGGESTED FIX**:
  The plan must state:
  > *"The audit trail is **append-only** and **signed**. The Collaborator will **never** allow any tool (or user) to modify past entries. New entries are **cryptographically linked** to previous ones."*

---
#### **F-16: No Off Switch**
- **SEVERITY**: MEDIUM
- **PLAN SECTION**: (Not addressed)
- **CONCRETE FAILURE**:
  The plan does **not** specify:
  - How to **disable** the Collaborator entirely.
  - How to **pause** a running action.
  - How to **kill** a stuck loop.
  - **Scenario**:
    - Collaborator enters a **loop** (e.g., model keeps proposing actions).
    - User wants to **stop it**, but there is **no mechanism**.
- **WHY IT MATTERS**:
  A system without an **off switch** is **not safe** for real-world use.
- **SUGGESTED FIX**:
  The plan must state:
  > *"The Collaborator will have: (1) a **global off switch** (disables all actions), (2) a **per-action cancel** (stops a running tool), (3) a **timeout** (auto-cancels long-running actions)."*

---
#### **F-17: No Reversibility Mechanism**
- **SEVERITY**: MEDIUM
- **PLAN SECTION**: *"reversible"* (claimed but not defined)
- **CONCRETE FAILURE**:
  The plan claims actions are **reversible**, but:
  - It does **not** define what **reversible** means.
  - It does **not** specify how to **undo** an action.
  - **Scenario**:
    - Model calls `delete_file("important.txt")` → governed, **cleared**.
    - User wants to **undo** it, but there is **no mechanism**.
- **WHY IT MATTERS**:
  "Reversible" is a **safety claim**. If it’s not **defined and implemented**, it’s **misleading**.
- **SUGGESTED FIX**:
  The plan must state:
  > *"Every tool must support **undo** (e.g., `delete_file` → `restore_from_backup`). The Collaborator will **track** actions and provide a **revert** command."*

---
#### **F-18: Trust Boundary of Conversational Surface**
- **SEVERITY**: HIGH
- **PLAN SECTION**: *"the chat-window box from Stage 3 goes back to being what it always was, a demo surface"*
- **CONCRETE FAILURE**:
  The plan says the chat window is **not the product**, but:
  - It does **not** specify the **trust boundary** between the chat surface and the Collaborator.
  - If the chat surface **renders model output directly**, the model could:
    - **Fake tool results** (e.g., *"I deleted the file"* when it didn’t).
    - **Phish the user** (e.g., *"Click this link to approve"*).
  - **Scenario**:
    - Model says: *"I ran `rm -rf /` and it worked!"*
    - The chat surface **displays this** to the user.
    - The user **believes** the action happened (even if it was denied).
- **WHY IT MATTERS**:
  The **conversational surface** is part of the **attack surface**. If it’s **not governed**, the model can **lie to the user** even if the Collaborator is honest.
- **SUGGESTED FIX**:
  The plan must state:
  > *"The chat surface will **only display**: (1) **user input**, (2) **governed tool results** (from the Collaborator), (3) **system messages** (e.g., proposals, errors). The model’s **narrative** will be **clearly labeled** as untrusted and **never** used to report action outcomes."*

---
#### **F-19: Verification Evidence Source for Tool Actions**
- **SEVERITY**: HIGH
- **PLAN SECTION**: (Not addressed)
- **CONCRETE FAILURE**:
  The plan does **not** specify **where verification evidence comes from** for tool actions. The core API’s `govern()` expects:
  - `envelope` (signed by policy).
  - `receipt` (from the tool).
  - `world_evidence` (independent sources).
  - For **local tools** (e.g., `write_file`), what is the **world evidence**?
    - Is it **file metadata** (e.g., `stat()`)?
    - Is it **a hash**?
  - For **external tools** (e.g., `curl`), what is the **world evidence**?
    - Is it **HTTPS certificates**?
    - Is it **a third-party API**?
- **WHY IT MATTERS**:
  Without **defined evidence sources**, the verifier **cannot** do its job. The plan must **specify** how evidence is **collected** for each tool.
- **SUGGESTED FIX**:
  The plan must state:
  > *"For each tool, the Collaborator defines: (1) the **receipt** (tool output), (2) the **world evidence** (e.g., file hash for `write_file`, TLS cert for `curl`). The verifier will **only** clear actions where both are **present and consistent**."*

---

## **STEELMAN**
1. **P-01 is Explicit and Central**: The plan **correctly** identifies P-01 ("salience influences; policy authorizes") as the **non-negotiable** invariant and **repeats it** in multiple places. This is **exactly right**—the entire system’s safety depends on this separation.
2. **Governance Seam is Well-Defined**: The plan **accurately** describes the core flow (`issue_policy -> signals -> interpret -> govern -> consume`) and **correctly** notes that the Collaborator must **own the loop** to ensure governance is **load-bearing**. This is a **strong foundation**.
3. **Honest About Gaps**: The plan **admits** limitations (e.g., leash in host config, Step 0 scope) rather than hiding them. This **transparency** is critical for a **trustworthy** review.

---

## **VERDICT**
**SERIOUS_GAPS** — The plan has **critical P-01 violations** (leash authority leak, proposal binding), **seam bypass risks** (multi-tool, retries, recursion), and **missing safety guarantees** (audit tamper-resistance, off switch, verification evidence). However, the **core structure** (governance seam, P-01 emphasis) is **sound**, and the **honesty** about scope is **commendable**. **Fix F-01, F-02, F-03, F-04, F-13, F-14, F-15, F-18, F-19 before Step 0 is approved.**
