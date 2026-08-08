# Propose-channel red-team: deepseek/deepseek-v4-pro

_finish=stop seconds=124.3 usage={'prompt_tokens': 11765, 'completion_tokens': 5562, 'total_tokens': 17327, 'cost': 0.0042550651, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0042550651, 'upstream_inference_prompt_cost': 0.0021871135, 'upstream_inference_completions_cost': 0.0020679516}, 'completion_tokens_details': {'reasoning_tokens': 3203, 'image_tokens': 0, 'audio_tokens': 0}}_

# ADVERSARIAL DESIGN REVIEW — PROPOSE CHANNEL (①)

## Findings

### 1. Approval Does Not Re‑Verify the Capability Gate (TOCTOU Authority Leak)
**ID:** F‑01  
**SEVERITY:** CRITICAL  
**LOCATION:** Design claim in `01-propose-design.md` (claim: “approving … runs through … the capability gate”) and the existing `approve()` implementation in `collaborator/loop.py` (provided as reused code).  
**CONCRETE SCENARIO:**  
1. A session is created with `capabilities = ("fs.read:project", "fs.write:project", "shell.exec")`.  
2. The Collaborator originates a `run_command` proposal; `govern_action` checks capability, sees it granted, returns `HELD`, and the proposal is surfaced.  
3. Before the host approves, something (a host‑side event, a configuration reload, or a malicious plugin) removes `shell.exec` from `session.capabilities`.  
4. The host calls `approve(session, decision)`. The current `approve()` runs `execute_and_verify` without re‑running the capability gate; the tool executes with the now‑revoked privilege.  
**WHY IT MATTERS:** The design’s central safety claim is “surfacing grants nothing; capability gate still rules at run time.” This is false because `approve()` trusts the directive produced at origination and never checks whether the capability is **still** granted when the action actually runs. The core `govern_action` performs the gate only once; `approve()` bypasses it. This is a textbook TOCTOU (time of check to time of use) authority leak that breaks P‑01 — a capability can be used after it has been removed.  
**SUGGESTED FIX:** Either (a) mandate that `session.capabilities` is immutable for the lifetime of a session (and enforce it, e.g. by making it a frozen property, or by documenting the invariant and removing any code that could reassign it) **and** update the design text to stop claiming a second check occurs; or (b) modify `approve()` to re‑run the capability check by calling `govern_action` with a force‑run leash instead of bypassing it. Option (b) is the true re‑gating and aligns with the design’s promise.

---

### 2. Confidence Is Powerless, but Social Engineering Risk Exists (No Authority Leak)
**ID:** F‑02  
**SEVERITY:** LOW (informational)  
**LOCATION:** Proactivity dial and confidence gate in `01-propose-design.md`, plus the lifecycle section.  
**CONCRETE SCENARIO:** An adversarial model consistently assigns high confidence to a sequence of low‑risk‑looking proposals that together accomplish a harmful action, all of which would individually pass the capability gate. The EAGER dial surfaces all of them; a distracted, impatient, or fatigued host approves them one by one without careful scrutiny.  
**WHY IT MATTERS:** The architecture does not leak authority through confidence because confidence is never used in salience scoring, verification depth, or approval. However, “surfacing grants nothing” only covers the machine layer; it does not inoculate against operator error. While this is not a design flaw in the propose channel (the host is still the deliberate decider), the design’s claim that the dial “only trades quiet vs chatty” and “is safe for the model to influence” overlooks a real‑world social engineering vector that could benefit from an EAGER setting.  
**SUGGESTED FIX:** Add a note in the design or user guidance that EAGER plus high‑confidence proposals that are individually benign but chained can erode human vigilance; recommend that the host optionally require an additional approval‑step confirmation or maintain the CONSERVATIVE default.

---

### 3. Per‑Task Leash Parameter Design Is Safe if Implemented as Described
**ID:** F‑03  
**SEVERITY:** LOW  
**LOCATION:** Section “Per-task adjustable leash” in `01-propose-design.md`.  
**CONCRETE SCENARIO:** The design states that `govern_action` will accept an optional host‑supplied `leash` parameter, defaulting to the session/determined leash, and that an invalid value will fall back to a safe default. The existing `govern_action` code does NOT (yet) accept this parameter — it always uses `_leash_for(session, tool)`. The design must ensure the implementation (a) only ever uses the three defined constants, (b) cannot be set by a model‑originated value, (c) falls back to the tool’s default or `PROPOSE_FIRST` on garbage input, and (d) never silently upgrades to `ACT_THEN_REPORT`.  
**WHY IT MATTERS:** If the parameter were accidentally surfaced to the model (e.g., the proposer mistakenly passes a model‑sourced leash) or if a typo in the constant name were treated as `ACT_THEN_REPORT`, an action that should be HELD would auto‑run. As a design, the intent is safe; the risk is in implementation fidelity.  
**SUGGESTED FIX:** The implementation must validate the `leash` value strictly against the allowed set, treat any unknown value as `PROPOSE_FIRST` (or a configurable safe fallback), and the parameter must be typed such that only the host can supply it (not threaded through any model‑controlled input path). The design should explicitly state this validation rule.

---

### 4. Origination Exceptions Must Fail Closed — Design Promises It but Implementation Must Deliver
**ID:** F‑04  
**SEVERITY:** LOW  
**LOCATION:** Fail‑closed behaviour bullet in `01-propose-design.md`.  
**CONCRETE SCENARIO:** The model produces a tool‑call candidate that the parser cannot parse, or the proposer raises an exception. The design says “no proposal (never a spurious or half‑formed one).” The future `propose()` function must catch all exceptions from the model, parser, and `govern_action`, and return no proposal. If it instead surfaces a generic “error” or an incomplete `Decision`, the host might misinterpret it.  
**WHY IT MATTERS:** The safety guarantee depends on the actual implementation handling failures exactly as described. This is a low risk because the design already demands it; the review merely notes that the implementation must be audited for this property.  
**SUGGESTED FIX:** In the implementation, wrap the entire proposal generation and governance in a try‑except that drops any exception silently (logging it to the audit bus) and returns nothing to surface. Also add a regression test for a malformed model output.

---

### 5. Honesty of the Central Claim: “Approval + Capability Gate Rule at Run Time” Is Misleading
**ID:** F‑05  
**SEVERITY:** HIGH (integrity of safety claim)  
**LOCATION:** `01-propose-design.md` — the **“The one idea that keeps this safe”** section and the **Lifecycle** diagram.  
**CONCRETE SCENARIO:** The text asserts that “approving a proposal runs it through the same `approve()` → `execute_and_verify` as any action; the capability gate still rules at run time.” As shown in F‑01, the gate does **not** rule at run time because `approve()` bypasses it; it was only checked at origination. The claim is therefore false unless the design is accompanied by a modification to `approve()` or a strict constraint that capabilities cannot change.  
**WHY IT MATTERS:** The entire safety argument rests on the user’s belief that “no action ever runs without a fresh capability check at the moment of approval.” Without that, the user might be lulled into thinking that revoking a capability after seeing a proposal will stop it — which it will not. This overclaim undermines trust in the security model.  
**SUGGESTED FIX:** Amend the design to either (a) explicitly add a capability re‑check in `approve()` (preferred), or (b) clarify that “the capability gate is checked once at origination; a session’s capabilities are immutable and are not expected to be revoked mid‑session, so a second check is unnecessary.” In both cases, the claim must be corrected to reflect reality.

---

### 6. Audit/Provenance Does Not Distinguish Originated vs. User‑Initiated HELD Actions
**ID:** F‑06  
**SEVERITY:** LOW  
**LOCATION:** Lifecycle and audit trail in `01-propose-design.md`; the `Decision` dataclass in `collaborator/governance.py`.  
**CONCRETE SCENARIO:** A proposal originates through `govern_action(…, leash=PROPOSE_FIRST)` and returns a `Decision` with status `HELD`. A user‑initiated `PROPOSE_FIRST` action (from a reactive turn) also returns `HELD`. The `Decision` object has no field indicating origination source. Later forensic analysis cannot tell whether a given `HELD` action was collaborator‑proposed or user‑driven.  
**WHY IT MATTERS:** This does not grant authority and therefore does not violate P‑01. However, if the system ever needs to treat originated proposals differently (e.g., for auditing, throttling, or policy), the lack of provenance could become a problem. It also reduces audit transparency.  
**SUGGESTED FIX:** Add an optional `originator: str` field (e.g., `"user"` / `"collaborator"`) to the `Decision` dataclass, set appropriately in the propose channel. This preserves audit fidelity without complicating the safety model.

---

## Steelman

The propose channel reuses the same `govern_action` seam, ensuring that every proposal is fully governed—capability‑checked, workspace‑fenced, and salience‑scored—**before** surfacing. The confidence dial controls only *surfacing*, not authority, and surfacing is genuinely inert: no tool runs, no side‑effects occur, and the worst the model can do is create noise. With the exception of the missing approval‑time capability re‑check (F‑01/F‑05), the design faithfully upholds P‑01 and the core claim that surfacing grants nothing.

## Verdict

**SERIOUS_FLAWS** — The central safety claim is undermined because the approval path does **not** re‑verify the capability gate, creating a TOCTOU authority leak that violates the promise of “the capability gate still rules at run time.” The design must either modify `approve()` to re‑check the capability or explicitly declare session capabilities immutable and stop claiming a second check occurs. Without this fix, the propose channel cannot be called “safely growing with you.”
