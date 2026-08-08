# PolicyCaps red-team: x-ai/grok-4.5

_finish=stop seconds=152.1 usage={'prompt_tokens': 13308, 'completion_tokens': 6734, 'total_tokens': 20042, 'cost': 0.0668024, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 128, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0668024, 'upstream_inference_prompt_cost': 0.0263984, 'upstream_inference_completions_cost': 0.040404}, 'completion_tokens_details': {'reasoning_tokens': 3134, 'image_tokens': 0, 'audio_tokens': 0}}_

# Adversarial review: Signed PolicyCaps (③)

## Findings

### F1 / STRIP-CAPS REVERTS TO MUTABLE AUTHORITY (LEGACY BYPASS)
**Severity:** HIGH  
**Location:** Design §“The properties it must hold” (Backward compatible); §“every governed action” step 1 (“Invalid/absent-when-required”); seam `govern_action` / `reauthorized_or_denied` (both currently `tuple(session.capabilities)`); planned `granted_capabilities(session)`

**Concrete scenario:** Session is minted with `SignedPolicyCaps` that omit `shell.exec` and cap `run_command` at `propose_first`. A bug or control-surface write does `session.policy_caps = None` (or never copies caps into a forked session) and `session.capabilities = (…, "shell.exec")`, and/or `set_leash(session, "run_command", ACT_THEN_REPORT)`. Every subsequent `govern_action` and, critically, `reauthorized_or_denied` on approve takes the legacy branch and sources authority from the mutable tuple / uncapped leash → `run_command` auto-runs.

**Why it matters:** The no-widen claim is conditional on the artifact *remaining attached*. Clearing one field is a quieter, non-crypto widen than flipping bits inside `caps` (which fail closed). Same class of “injected config flip in a different component” the honest scope says it stops — unless that component only knows how to null the optional field. Hold→approve TOCTOU makes it sharper: hold under a tight grant, strip caps, widen `session.capabilities`, approve; re-gate is exactly where current code re-reads `session.capabilities`.

**Suggested fix:** Distinguish *opt-in at session construction* from *per-action absence*:
- `Session(..., policy_caps=…, caps_key=…)` with caps present ⇒ session enters `enforce_caps=True` for its lifetime; `policy_caps is None` thereafter is fail-closed (zero caps + strictest leash), not legacy.
- Legacy only when constructed with `policy_caps is None`.
- Document explicitly: “no caps at construction = legacy; stripping caps at runtime ≠ legacy.”
- Proof case: attach grant → clear `policy_caps` → action DENIED (not widened).

---

### F2 / ABSENT `leash_caps` ENTRY FAIL-OPENS (PARTIAL MAP)
**Severity:** HIGH  
**Location:** Design §“PolicyCaps: a signed grant” (`leash_caps: {tool: max_looseness}`); §“Leash ordering”; planned `leash_cap(session, tool)` / `apply_cap`

**Concrete scenario:** Authority mints a grant with `capabilities` including `shell.exec` but `leash_caps = {}` or `leash_caps` without `run_command` (template bug, only caps the fs tools). Verify succeeds. Host/view `set_leash(run_command, act_then_report)`. If missing entry means “no cap,” effective leash is uncapped → shell auto-runs despite a “signed grant” being present. Capability no-widen holds; leash no-loosen does not.

**Why it matters:** Leash is the second authority axis the design exists to bind. Default-deny on the capability tuple is natural (empty = zero); default-allow on a partial `leash_caps` map is the opposite fail posture and is not specified.

**Suggested fix:** Specify fail-closed defaults in the design:
- Missing tool key in `leash_caps` ⇒ `notify_only` (strictest), **or** treat as verify failure if `capabilities` imply a tool with no leash entry.
- Unknown/invalid looseness string in the map ⇒ verify fail or strictest, never “ignore.”
- Proof: grant with `shell.exec` and empty `leash_caps` → `run_command` cannot become `act_then_report`.

---

### F3 / APPROVE / RE-GATE PATH MUST VERIFY GRANT + CAP LEASH INPUTS (WIRING GAP)
**Severity:** HIGH  
**Location:** Design §“What gets built” (`govern_action + reauthorized_or_denied`); current `reauthorized_or_denied` (only re-issues policy from `session.capabilities`; **no leash logic**); `view.approve` → `approve_proposal` → re-gate → `execute_and_verify` (runs without leash gate); `govern_action(..., leash=)` host override

**Concrete scenario:**
1. **Caps source:** Implementation wires `granted_capabilities` only into `govern_action` and leaves `reauthorized_or_denied` on `session.capabilities` (easy miss — today’s code is copy-pasted `issue_policy(..., tuple(session.capabilities), ...)` in both). Mutate capabilities after HELD → approve runs with widened authority (the TOCTOU `reauthorized_or_denied` exists to prevent — undoing it for the new layer).
2. **Host leash override:** `govern_action(session, intent, leash=ACT_THEN_REPORT)` is documented host authority. If `stricter(host, cap)` is applied only to `_leash_for` session overrides and not to the keyword `leash` argument, a proposer/UI path loosens past the grant without touching `leash_overrides`.
3. **Display vs gate:** `execute_and_verify` re-reads `_leash_for` only for `Decision.leash` labeling; not a gate — OK — but then **all** gating must happen in `govern_action` resolution and must use the same helper as any future re-check.

**Why it matters:** Incomplete mediation. The design names both functions but does not prescribe a **single** mandatory helper (e.g. `effective_capabilities(session)` / `effective_leash(session, tool, override=None)`) called at every authority read. Approve is the highest-value bypass surface (panel F1 exists for this).

**Suggested fix:** Design must require:
- One internal API used by **both** `govern_action` and `reauthorized_or_denied` for capability tuples after verify.
- One `effective_leash(session, tool, override=None) = apply_cap(_resolve_leash(...), leash_cap(...))` used for the act-path leash branch; keyword override included.
- Re-gate: verify grant + capability check (mandatory). Re-check leash only if you ever add “approve under notify_only is nonsense” — optional; don’t re-open act_then_report via approve without a held propose_first decision.
- Proof: widen `session.capabilities` between HELD and approve → still DENIED; `leash=ACT_THEN_REPORT` kwarg under `propose_first` cap → still HELD.

---

### F4 / `canonical()` UNDERSPECIFIED → MINT/VERIFY AMBIGUITY
**Severity:** MEDIUM  
**Location:** Design §“PolicyCaps: a signed grant” (`HMAC-SHA256(canonical(caps), caps_key)`); `policycaps.py` mint/verify

**Concrete scenario:** Mint serializes `capabilities` as a JSON array without sorting; verify sorts. Or `leash_caps` key order differs; or subject path `"/ws"` vs `"/ws/"`; or capability accidentally passed as list vs tuple producing different encodings; or Python `json` dumps spaces differ. Two different semantic grants collide, or the same grant verifies on one code path and fails closed on another (availability footgun that pushes operators to disable caps). Worse: type coercion (`notify_only` vs alias) accepted in apply but not in bytes signed.

**Why it matters:** Fail-closed integrity rests on one unambiguous byte string. “canonical” is named, not defined — not collision-safe by claim alone.

**Suggested fix:** Normative canonicalization in the design, e.g.:
- UTF-8 JSON, `sort_keys=True`, compact separators, no NaN;
- `capabilities`: sorted unique strings;
- `leash_caps`: fixed enum values only, sorted keys;
- `issuer` / `subject`: NFC strings, subject pre-resolved (see F5);
- reject non-strings / unknown keys at verify (fail closed).
- Golden-vector tests in `policycaps_proof.py`.

---

### F5 / SUBJECT = WORKSPACE PATH NOT NORMALIZED (BINDING WEAKNESS)
**Severity:** MEDIUM  
**Location:** Design §grant `subject` + verify step (`caps.subject == this session's workspace`); `Session.__init__` (`self.workspace = Path(workspace)` — **not** `.resolve()`); `resolve_in_workspace` resolves only tool paths

**Concrete scenario:**
- **Relative subject:** Grant minted with `subject="project"` while cwd is `/a`; attacker starts session with `workspace="project"` under cwd `/b` (different tree). String match succeeds → cross-workspace replay.
- **Symlink / non-resolved:** Mint uses `str(Path("/alias/ws"))`; verify compares to `str(session.workspace)` after different symlink entry points — either false reject (ops bypass via legacy) or false accept if both sides use the unsanitized string the attacker chose.
- Design proof “workspace A vs B” only covers obvious string inequality, not normalization.

**Why it matters:** Subject is the only anti-replay binding in v0. Path strings are not workspace identity without resolve + absolute form at **both** mint and verify.

**Suggested fix:** Define `subject = str(Path(workspace).resolve())` at mint; verify with `hmac.compare_digest` semantics on equality after resolving `session.workspace` the same way; fail closed on resolve error. Document symlink policy (resolve once at session start, freeze `session.workspace_resolved`). Nonce/expiry honestly deferred is fine for v0 **if** subject binding is sound; say so.

---

### F6 / VIEW / SNAPSHOT STILL SURFACES MUTABLE AUTHORITY (OPERATOR OVER-TRUST)
**Severity:** MEDIUM  
**Location:** `view.py` `JudgmentView.snapshot` (`capabilities: list(session.capabilities)`, `_leashes()` from overrides/defaults only); design honesty §; no mention of effective vs configured display

**Concrete scenario:** Grant caps capabilities to fs-only and `run_command` at `propose_first`. Operator or debugger sets `session.capabilities` to include `shell.exec` and `set_leash(..., act_then_report)`. View shows the **widened** config and loose leash chips; seam (if correct) still holds/denies. Operator believes “I granted shell / act_then_report” or, conversely, trusts the view as the grant and assumes PolicyCaps are theater when behavior differs.

**Why it matters:** Design sells “config can tighten but never widen past the grant.” The only host-facing control surface still paints the bypassed knobs as truth — a claim/UX honesty gap that produces misconfiguration and false confidence.

**Suggested fix:** Snapshot (and any audit line) show `effective_capabilities` / `effective_leashes` from verify+cap, and optionally “configured” separately. `set_leash` may still store a looser value, but view must label it “requested; effective = … (capped by grant).”

---

### F7 / FAIL-CLOSED MATRIX INCOMPLETE FOR VERIFY API SHAPE
**Severity:** MEDIUM  
**Location:** Design §tamper/absent key/wrong subject/malformed; planned `verify()`; governance try/except around `issue_policy` only today

**Concrete scenario:**
- `verify()` raises `TypeError` on `caps=None` fields inside a present envelope; caller only special-cases `ok=False` and lets exception propagate past governance → host error path restarts session without caps (F1) or aborts open in a wrapper not shown.
- Present `SignedPolicyCaps` with `signature=b""`, wrong key, or `caps_key is None` — design lists these, but not “verify must be pure total function.”
- `leash_caps` value `None`, capability entries non-str, empty `subject` — unspecified → implementation-defined open/closed.

**Why it matters:** Seam already fails closed on interpret errors; the new layer must not introduce a side channel that is fail-open or fail-crash-then-legacy.

**Suggested fix:** Spec: `verify(signed, key, subject) -> VerifiedCaps | Rejected` never raises; all of {bad sig, bad key, missing key when signed present, subject mismatch, schema fail, empty subject, non-canonical types} → Rejected; governance maps Rejected to zero caps + strictest leash **before** `issue_policy`. Table these cases in the proof file.

---

### F8 / BACKWARD COMPAT HOLE — SCOPING OK, CLAIM WORDING OVERBROAD
**Severity:** LOW (doc) / elevates F1 if wording stays  
**Location:** §“The gap it closes”; §properties “No widening. With a signed grant present…”; §Honest scope

**Concrete scenario:** Reader skims gap statement (“config … can only ever operate within the signed grant”) and honest-scope bullet list, misses the “with a signed grant present” qualifier and the legacy branch. Ships host integration that attaches caps once in a template then allows session clone without them.

**Why it matters:** Honesty section is mostly good (HMAC single-domain, not a hard boundary, ADR 0002). Residual oversell is **universal** no-widen language vs **opt-in, presence-gated** mediation.

**Suggested fix:** One normative sentence: “PolicyCaps is opt-in hardening; the security properties apply only while a grant is enforced on the session (see enforce flag). Absence at construction is legacy and is a deliberate non-goal to close in a later ‘required caps’ mode.” Do not imply config is globally non-authoritative after ③.

---

### F9 / NO EXPIRY/NONCE IN v0 — ACCEPTABLE IF STATED AS REPLAY RESIDUAL
**Severity:** LOW  
**Location:** Design replay proof (subject only); honest scope; decisions “host’s”

**Concrete scenario:** Grant for workspace A stolen from disk/audit and replayed on a new session for the same resolved workspace after intended revocation (key not rotated). Subject check passes; no `exp`/`jti`.

**Why it matters:** Within single trust domain + key rotation as ops countermeasure this is consistent with v0. Not a defect if deferred explicitly; a defect if “replay blocked” is read as general anti-replay.

**Suggested fix:** Narrow proof title to “cross-workspace subject mismatch”; add “same-workspace re-presentation / revocation → key rotation; exp/nonce deferred with asymmetric authority.”

---

### F10 / LEASH RANK ORDERING — SOUND (no issue)
**Severity:** n/a (positive)  
**Location:** §“Leash ordering” `effective_rank = max(host_rank, cap_rank)`

**Check:** `act_then_report=0 < propose_first=1 < notify_only=2`; cap `propose_first` blocks host `act_then_report`; host may still tighten to `notify_only`. No off-by-one that lets `act_then_report` through a `propose_first` cap **if** `apply_cap` uses this total order and invalid strings don’t map to 0. Keep invalid → strictest or reject (F2/F7).

---

## STEELMAN

The design correctly separates **grant** (PolicyCaps) from **per-action policy** (existing `issue_policy`), scopes HMAC to tamper-evidence inside one trust domain without pretending to be an out-of-process boundary, and names the right seam insertion points (`govern_action` + `reauthorized_or_denied`) with the right leash algebra (`max` rank). Backward-compatible opt-in and “tighten still works” are operationally realistic. With a frozen enforce-once flag, total `verify()`, closed defaults on `leash_caps`, resolved subject, and a single effective-leash/caps helper, this is a coherent hardening layer — not theater — for the stated threat (non-`mint` mutation of config/control surface).

## VERDICT

**SERIOUS_FLAWS** — Mediation is presence-gated without a sticky enforce mode (strip/null caps + approve TOCTOU returns to mutable `session.capabilities`), and `leash_caps` underspecification allows fail-open loosening; fix those and the canonical/subject/`verify()` totality gaps before implementation, and the scoped claim holds.
