# Propose-channel red-team: x-ai/grok-4.5

_finish=stop seconds=160.1 usage={'prompt_tokens': 12150, 'completion_tokens': 7655, 'total_tokens': 19805, 'cost': 0.0700124, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0700124, 'upstream_inference_prompt_cost': 0.0240824, 'upstream_inference_completions_cost': 0.04593}, 'completion_tokens_details': {'reasoning_tokens': 4865, 'image_tokens': 0, 'audio_tokens': 0}}_

# Adversarial Design Review — Propose Channel (Step 1)

## Findings

### F1 / RUNTIME CAPABILITY RE-GATE IS ASSERTED BUT NOT PROVIDED / CRITICAL
**LOCATION:** Design §“The one idea that keeps this safe” (bullet: “the capability gate still rules at run time”); §Lifecycle (`approve_proposal ⇒ approve()`); §Fail-closed (“denied again at run time”); §Proof contrast 1; vs reused seam `collaborator/loop.py::approve()` + `governance.py::execute_and_verify()`

**CONCRETE SCENARIO:**  
1. Session has `shell.exec`. Proposer originates `run_command`, `govern_action(..., leash=PROPOSE_FIRST)` returns `HELD` with a directive that granted the capability.  
2. Proposal sits surfaced (or queued). Host later revokes `shell.exec` (or replaces `session.capabilities` / rotates to a tighter policy).  
3. Host calls `approve_proposal` → `approve(decision)`.  
4. `approve()` only checks `status == HELD`, then `execute_and_verify(session, tool, decision.directive, …)` — **no** `issue_policy` / `interpret` / `directive.grants_capability` on the *current* session. The command runs on a stale grant.

**WHY IT MATTERS:** The design’s spine is not only “surfacing is inert” but “approval re-enters the capability gate.” The reused Step-0 `approve()` path **explicitly reuses the origination directive and does not re-gate.** Step 1 makes this worse than reactive hold/approve: proposals are *initiated* to linger. Proof #1’s “denied if approved anyway” is false for revoke-then-approve (and for any forged/stale `HELD` decision). This is exactly the TOCTOU the attack brief calls out.

**SUGGESTED FIX:** In `approve_proposal` (or by extending `approve()` for this channel), **re-run the authority slice** against current session state before execute: fresh `issue_policy` + `interpret` + `grants_capability(tool.capability)` + workspace pre-check; on failure return `DENIED` and do not call `execute_and_verify`. Optionally bind proposals to a capability generation / policy epoch and auto-invalidate on change. Rewrite the design bullets so they match the code path you actually ship.

---

### F2 / NEW `leash` PARAM + EXISTING FALL-THROUGH = UNLEASHED RUN ON INVALID VALUE / HIGH
**LOCATION:** Design §“Per-task adjustable leash” (“invalid value falls back to the safe default rather than running unleashed”); vs `governance.py::govern_action` leash block (anything not `NOTIFY_ONLY` / `PROPOSE_FIRST` → `execute_and_verify`)

**CONCRETE SCENARIO:** Implementer adds `leash: str | None = None` and does `leash = leash if leash is not None else _leash_for(...)`. Host (or a buggy caller) passes `leash="propose-first"`, `"PROPOSE_FIRST"`, `""`, or `"held"`. None equal the constants `propose_first` / `notify_only` → code falls through to **act-then-report and runs**.

**WHY IT MATTERS:** Design claims invalid → safe default. The seam it extends treats unknown as **run**. A single validation miss converts the new host param into an accidental widen-to-execute. This is the highest-likelihood footgun in the delta.

**SUGGESTED FIX:** Explicit allow-list before branching:

```text
ALLOWED = {ACT_THEN_REPORT, PROPOSE_FIRST, NOTIFY_ONLY}
if leash is None: leash = _leash_for(...)
elif leash not in ALLOWED: leash = PROPOSE_FIRST  # or session/tool default — pick one, document it
```

Never “else: run.” Unit-test invalid / wrong-case / empty. Proposer path should pass the constant `PROPOSE_FIRST`, not a string from config merge without validation.

---

### F3 / “CAPABILITY GATE AT RUN TIME” / “DENIED AGAIN IF APPROVED” OVERCLAIM / HIGH
**LOCATION:** Same design sections as F1; `loop.py::approve` docstring already says “using the directive already recorded for it” (honest); design text is not

**CONCRETE SCENARIO:** A reviewer or implementer trusts the design and skips adding a re-gate because “approve already does that.” Red-team proof #1 is written as if approve re-denies ungranted capabilities; under the real seam, an ungranted tool never becomes `HELD` through `govern_action`, so the “approved anyway” branch is untested theater unless you inject a synthetic decision — and then it **runs** if the stored directive says so.

**WHY IT MATTERS:** Central safety narrative is partly false. “Surfacing grants nothing” can still be true while the brochure claim about approve is not. That gap is how F1 ships unnoticed.

**SUGGESTED FIX:** Either implement F1’s re-gate and keep the claim, or rewrite to: “Approval is host authority; execute trusts the held decision’s directive unless/until we add re-validation.” Do not claim runtime capability denial you do not perform.

---

### F4 / PER-TASK `leash` CAN WIDEN THE LEASH AXIS (HOST-INTENDED, WORDING SLIPPERY) / MEDIUM
**LOCATION:** Design §“Per-task adjustable leash” — “can never widen authority (capability is a separate gate…)”; tools.py defaults (`run_command` → `PROPOSE_FIRST`)

**CONCRETE SCENARIO:** Session has no leash override; `run_command` defaults to `PROPOSE_FIRST`. Host calls `govern_action(..., leash=ACT_THEN_REPORT)` on a shell intent. Capability gate passes → command **auto-runs**, skipping the hold the tool default existed to enforce.

**WHY IT MATTERS:** Not a model bypass if `leash` is truly host-only — the host is allowed to loosen their own leash. But the design equates “cannot widen authority” with “cannot touch capability,” which papers over that leash **is** the second control axis. For Step 1, the dangerous case is accidental loosening (F2) or any future path that copies model-supplied fields into `leash`.

**SUGGESTED FIX:** Clarify: “May loosen/tighten leash relative to tool default; cannot grant capability; invalid values never execute.” Keep `leash` out of `ToolIntent` / parser / model JSON by type boundary (keyword-only arg on `govern_action`, never `intent.args.get("leash")`). Document that proposer **hardcodes** `PROPOSE_FIRST` and must not read a model field.

---

### F5 / PROPOSER VS USER ORIGIN NOT IN AUDIT/DECISION MODEL / MEDIUM
**LOCATION:** Design §Lifecycle / §What gets built (`Proposal`, `approve_proposal`); `Decision` in `governance.py` (no origin/provenance field); approve path identical to user-held tool calls

**CONCRETE SCENARIO:** After the fact, audit/bus shows `act-…` / directive / `RAN` for `write_file`. Forensics cannot tell whether the user instructed the write in a turn or the Collaborator originated a proposal that the user later approved. A confused-deputy narrative (“user asked for this”) becomes indistinguishable from “agent suggested, user clicked yes.”

**WHY IT MATTERS:** Design correctly states proposals are collaborator-originated. That distinction is a safety/accountability property, not just UX. Without `origin=proposer|user_turn`, `surfaced_at`, `approved_by_host=true`, and stable link held→ran, provenance collapses at the moment that matters.

**SUGGESTED FIX:** Tag at origination (`Decision` or `Proposal`: `origin="propose_channel"`, proposer rationale id, proactivity/confidence **as non-authority audit fields**). On approve, emit an explicit host-approval audit event that references the held `action_id` and does not rewrite history as a normal user tool-call. Keep confidence out of authority even in audit consumers.

---

### F6 / INERTNESS SLIGHTLY OVERSTATED (ORIGINATION SIDE EFFECTS) / LOW
**LOCATION:** Design §“surfacing grants nothing”; §Proof 4 “changed nothing on disk and nothing in the world”; vs `govern_action` path that `_emit_signals` / `bus.emit(directive)` before `HELD`

**CONCRETE SCENARIO:** Eager dial + many high-confidence proposals → flood of directives/signals on the bus and held decisions in session/UI memory. Disk unchanged; world I/O unchanged; audit and in-process salience bus are not.

**WHY IT MATTERS:** Does not break P-01 if signals remain influence-only (core assumed correct) and learning `consume()` only runs post-execute (true today for `HELD`). Still, “nothing in the world” / full inertness is slightly stronger than “no tool side effects / no capability.” Honest scope avoids future someone treating bus presence as trust.

**SUGGESTED FIX:** Proof 4 language: “no tool execution, no workspace mutation; audit/held records only.” Ensure HELD never calls `consume()` / adaptation (already true if approve is the only execute path).

---

### F7 / CONFIDENCE PATH — SOUND IF IMPLEMENTED AS WRITTEN / LOW (residual)
**LOCATION:** Design §“On the confidence number”; proactivity gate in §Lifecycle

**CONCRETE SCENARIO:** Adversarial model always reports `confidence=1.0` with a plausible rationale and dangerous args. Under `EAGER`/`CONSERVATIVE`, noise increases; under design rules, still `govern_action` → deny-or-hold, no run without host approve.

**WHY IT MATTERS:** No technical authority backdoor **provided** confidence never feeds `importance`/`risk`/verification/approval and never becomes a second gate on `approve_proposal`. Residual risk is human rubber-stamping (UX), which the design already classifies correctly as non-security — **as long as implementers do not “helpfully” sort-by-confidence into an auto-approve or into salience inputs.**

**SUGGESTED FIX:** In `propose.py`, hard boundary: `confidence` local variable → proactivity boolean only; do not put it on `Decision` fields that governance reads. Pin tests: confidence must not appear in `issue_policy` / signals / `approve` arguments. Specify proposal `importance` source explicitly (host default / host focus) — not model confidence.

---

### F8 / FAIL-CLOSED ON PROPOSER ERRORS — CLAIMED WELL; ONE DEPENDENCY / LOW
**LOCATION:** Design §Fail-closed; §Lifecycle (`DENIED` ⇒ DROP); parser reuse from `toolcall.py`

**CONCRETE SCENARIO:** Unparseable suggestion / “nothing to propose” / unknown tool / workspace escape / ungranted capability → no surface. Matches strict parser + `govern_action` deny-before-leash.  

Gap: “if somehow approved anyway, denied again at run time” is only partially true (workspace may fail in `execute_tool`; **capability will not** — F1).

**WHY IT MATTERS:** Origination fail-closed is the right shape and preserves “surfacing grants nothing” for the happy path. The fail-closed story should not lean on a second capability deny that does not exist.

**SUGGESTED FIX:** Keep drop-on-DENIED at origination; add F1 re-gate; tests for malformed model output producing zero `Proposal` objects.

---

## STEELMAN

Reusing the same `govern_action` seam, forcing `PROPOSE_FIRST` so origination cannot act-then-report, and treating the proactivity dial as pure UX is the right P-01 shape: salience/confidence may change how much noise the host sees, not what the process may do. If `leash` is keyword-only host input with an allow-list, and approval is a conscious host act, the channel can be “grows with you” without giving the model a new authority path. The architecture’s best idea — **held proposal ≡ already-mediated HELD decision, execution only via host** — is sound once the approve-time authority story is made literally true.

## VERDICT

**SERIOUS_FLAWS** — Surfacing-as-inert is directionally right, but the design’s central spine falsely claims approve-time capability re-gating the reused `approve()`/`execute_and_verify` path does not perform (F1/F3), and the new `leash` parameter extends a fall-through-to-run seam that will unleash on invalid values unless validation is mandatory (F2).
