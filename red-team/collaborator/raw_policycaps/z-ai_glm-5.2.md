# PolicyCaps red-team: z-ai/glm-5.2

_finish=stop seconds=93.0 usage={'prompt_tokens': 12308, 'completion_tokens': 6883, 'total_tokens': 19191, 'cost': 0.0066288378, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 11077, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.0066288378, 'upstream_inference_prompt_cost': 0.0008746498, 'upstream_inference_completions_cost': 0.005754188}, 'completion_tokens_details': {'reasoning_tokens': 3926, 'image_tokens': 0, 'audio_tokens': 0}}_

# Adversarial Design Review — Signed PolicyCaps

## FINDINGS

---

### F1 / LEASH CAP NOT ENFORCED ON THE EXECUTION PATH
**SEVERITY:** CRITICAL
**LOCATION:** `collaborator/governance.py` — `execute_and_verify()` line `leash = _leash_for(session, tool)`

**SCENARIO:** A signed grant caps `run_command` at `propose_first`. The view calls `set_leash(session, "run_command", "act_then_report")`, writing directly into `session.leash_overrides`. An action is then approved (or an `act_then_report` action fires). `govern_action` resolves the leash via `_resolve_leash` (where the design says `apply_cap` will live), but then calls `execute_and_verify`, which **re-derives the leash independently** via `_leash_for(session, tool)` — reading raw `session.leash_overrides` with **no cap applied**. The loosened `act_then_report` leash is used for execution. The shell command auto-runs.

**WHY IT MATTERS:** This is a direct bypass of the central "no loosening" property. The design claims `set_leash`/`leash_overrides` cannot loosen past `leash_cap`, but the execution path reads the leash from a different function than the one the design says will apply the cap. `execute_and_verify` is the terminal enforcement point for both the `act_then_report` path and the approve path — if the cap isn't applied there, it isn't enforced at the moment of execution.

**SUGGESTED FIX:** Centralize leash resolution through a single function — e.g., `effective_leash(session, tool, override)` — that always calls `apply_cap(_resolve_leash(...), leash_cap(session, tool))`. Both `govern_action` and `execute_and_verify` must call this same function. Do not allow `_leash_for` to be called independently without the cap. Alternatively, apply the cap at write time in `set_leash` AND at read time (defense in depth), but the read-time enforcement is mandatory.

---

### F2 / `reauthorized_or_denied` DOES NOT RE-CHECK THE LEASH
**SEVERITY:** HIGH
**LOCATION:** `collaborator/governance.py` — `reauthorized_or_denied()`

**SCENARIO:** A `propose_first` action is held. While it lingers, the host (or a compromised view) calls `set_leash(session, "run_command", "act_then_report")`. The user approves the proposal. `reauthorized_or_denied` re-checks the **capability** and the **path** — but never re-derives or re-caps the leash. It receives the origination `leash` as a parameter but only uses it to populate a DENIED Decision's field. Execution then proceeds via `execute_and_verify`, which (per F1) reads the now-loosened `session.leash_overrides`.

**WHY IT MATTERS:** The re-gate exists specifically for TOCTOU — "a held decision may have sat while the session's capabilities changed." The leash is a second authority axis that can change during the same window. The re-gate checks one axis (capability) but not the other (leash), creating an asymmetric TOCTOU gap. The design's proof #2 ("a grant caps run_command at propose_first; the view calls set_leash(run_command, act_then_report) → the action is still HELD") would pass if tested on the origination path but **fail on the approve path**.

**SUGGESTED FIX:** `reauthorized_or_denied` must re-derive the effective leash (capped) and verify it still permits execution. If the effective leash is `propose_first` or `notify_only` at approval time, the action should not auto-run — it should return HELD or NOTIFIED, or DENIED if the user's approval was predicated on a leash that no longer holds.

---

### F3 / SEAM CODE STILL SOURCES CAPS FROM `session.capabilities` — DESIGN DOESN'T SHOW THE FIX
**SEVERITY:** HIGH
**LOCATION:** `collaborator/governance.py` — `govern_action()` and `reauthorized_or_denied()`, both lines `tuple(session.capabilities)`

**SCENARIO:** The design says "govern_action + reauthorized_or_denied source caps from the verified grant." But the seam code as presented — the reference implementation reviewers are asked to judge — still passes `tuple(session.capabilities)` directly to `issue_policy` in **both** functions. The "What gets built" section lists the change as future work but doesn't show the modified code. An implementer could change `govern_action` (the more visible path) and miss `reauthorized_or_denied` (the re-gate), leaving the approve path sourcing from mutable config.

**WHY IT MATTERS:** This is the exact class of bug the design exists to prevent: incomplete mediation. The design's own proof #1 (mutate `session.capabilities` → still denied) would pass on the act path but fail on the approve path if only `govern_action` is updated. The design must show — or at minimum explicitly enumerate — every call site that must change, and the proof must cover both paths.

**SUGGESTED FIX:** The design should include a wiring table: {call site → current source → required source}. Both `govern_action` and `reauthorized_or_denied` must call `granted_capabilities(session)` (which verifies the grant and returns the caps) instead of `tuple(session.capabilities)`. The proof must include a test case for the approve path: mutate `session.capabilities` while a proposal is held, then approve → still denied.

---

### F4 / CANONICALIZATION FOR THE SIGNATURE IS UNSPECIFIED
**SEVERITY:** MEDIUM
**LOCATION:** Design §"PolicyCaps: a signed grant" — `signature: HMAC-SHA256(canonical(caps), caps_key)`

**SCENARIO:** `canonical(caps)` is never defined. If implemented as `json.dumps(caps.__dict__)` with default settings, two different grants could produce the same canonical form: `{"capabilities": ["shell.exec", "fs.read"]}` and `{"capabilities": ["fs.read", "shell.exec"]}` are semantically identical (a set) but serialize differently — or if the implementer sorts the list, a tuple `("shell.exec",)` and a list `["shell.exec"]` serialize the same in JSON but are different types in Python. An attacker who can inject a `None` issuer field (serialized as `null` and dropped by some serializers) could produce a valid signature for a semantically different grant.

**WHY IT MATTERS:** The entire tamper-evidence property rests on `canonical` being injective — no two distinct PolicyCaps produce the same bytes. If it isn't, the signature doesn't bind what the design claims it binds.

**SUGGESTED FIX:** Specify `canonical` explicitly: JSON with `sort_keys=True, separators=(",", ":")`, `ensure_ascii=False`, capabilities as a sorted tuple of strings, leash_caps as a dict with sorted keys, all fields required (no `None`), and a schema version field to prevent cross-version confusion. State this in the design, not just the implementation.

---

### F5 / NO `require_caps` FLAG — BACKWARD COMPAT IS A STRIPPABLE BYPASS
**SEVERITY:** MEDIUM
**LOCATION:** Design §"Backward compatible" and §"The properties it must hold"

**SCENARIO:** A session is configured with a signed grant. A bug or injected config path sets `session.policy_caps = None` (or never attaches it). The session silently falls back to legacy behavior — full mutable `session.capabilities`, no leash caps. The hardening is gone with no alarm.

**WHY IT MATTERS:** The design says this is "opt-in hardening" and acceptable. Within the single-trust-domain threat model, an attacker who can mutate session config can already do anything — so this is consistent. But the design should at minimum document this as a known gap and provide a `require_caps: bool` flag (defaulting False for compat, settable True by hosts who want fail-closed-on-absence). Without it, the hardening is silently disableable, which undermines the "fail-closed" claim for any deployment that expects the grant to be present.

**SUGGESTED FIX:** Add `session.require_policy_caps: bool = False`. When True, absence or verification failure of the grant yields zero caps + strictest leash (not legacy). Document that until this is defaulted True, the hardening is opt-in and bypassable by stripping the grant. This is cheap and closes the gap for hosts who want it.

---

### F6 / SUBJECT BINDING — WORKSPACE PATH COMPARISON UNRESOLVED
**SEVERITY:** MEDIUM
**LOCATION:** Design §"PolicyCaps: a signed grant" — `caps.subject == this session's workspace`

**SCENARIO:** A grant is minted with `subject = "/home/alice/project"`. A session is started with `workspace = "../project"` (relative) or via a symlink `/home/alice/link → /home/alice/project`. `Session.__init__` stores `self.workspace = Path(workspace)` — **not resolved**. The verify step compares `caps.subject == str(session.workspace)` — which is `"../project"`, not `"/home/alice/project"`. The grant is rejected (false deny). Conversely, if the grant is minted with a relative path and the session uses an absolute one, it could false-accept if the comparison is loose.

**WHY IT MATTERS:** The subject binding is the anti-replay mechanism (proof #4). If it false-denies, the hardening breaks operability. If it false-accepts, a grant minted for workspace A is replayed on workspace B via a symlink. The design must specify the comparison uses resolved absolute paths on both sides.

**SUGGESTED FIX:** Specify that `verify()` compares `Path(caps.subject).resolve() == Path(session.workspace).resolve()`. The mint function should also resolve the subject before signing. Document that symlinks within the workspace are fine (the workspace root is what's bound), but the root itself must be canonicalized.

---

### F7 / "STRICTEST LEASH ON FAIL-CLOSED" NOT LITERALLY DELIVERED
**SEVERITY:** LOW
**LOCATION:** Design §"The properties it must hold" — "zero capabilities + strictest leash"; `governance.py` — leash resolved before verify

**SCENARIO:** In `govern_action`, the leash is resolved (`_resolve_leash`) **before** the policy/interpret block where verification would occur. If verification fails and returns zero caps, the action is DENIED — but with the already-resolved (uncapped) leash, not "strictest leash." The design claims "strictest leash" on failure.

**WHY IT MATTERS:** Operationally moot — a DENIED action doesn't execute, so the leash value is cosmetic. But the design makes an explicit claim ("strictest leash") that the wiring doesn't deliver. A reviewer or auditor reading the design would expect the leash to be `notify_only` on failure, and it isn't. This is an honesty gap between claim and implementation.

**SUGGESTED FIX:** Either (a) move verification before leash resolution and force `leash = NOTIFY_ONLY` on verify failure, or (b) soften the claim to "zero capabilities (action DENIED); the leash is moot." Option (b) is simpler and honest.

---

### F8 / NO EXPIRY OR NONCE ON GRANTS
**SEVERITY:** LOW
**LOCATION:** Design §"PolicyCaps: a signed grant" — no `expiry` or `nonce` field

**SCENARIO:** A grant is minted and written to config. It is valid forever. A grant read from a leaked config file (or a backup) can be presented indefinitely. Within the single-trust-domain model this is acceptable — the config is as durable as the authority it replaces — but the design doesn't acknowledge the lack of expiry.

**WHY IT MATTERS:** Minor for v0, but the design should at least name the deferral. A reader comparing to the signed-policy/receipt patterns elsewhere in salienceos (which do have temporal binding via `action_id`) might assume temporal scoping exists here.

**SUGGESTED FIX:** Add one sentence: "v0 grants carry no expiry or nonce; they are as durable as the config they replace. Expiry/rotation is deferred to the asymmetric-key future." This is honest and costs nothing.

---

## STEELMAN

The design's threat model is honestly scoped: it names the single-trust-domain boundary, explicitly disclaims protection against an in-process re-signer, and correctly identifies the realistic threat (bugs and injected config flips in components that don't hold the key) that HMAC tamper-evidence does stop. The leash-rank ordering (`max(host_rank, cap_rank)`) is correct with no off-by-one, and the two-layer architecture (grant = authority, policy = request) is clean. The backward-compat decision is defensible for an opt-in hardening layer, and the proof matrix covers the right cases.

## VERDICT

**SERIOUS_FLAWS** — The leash cap is not enforced at the execution enforcement point (`execute_and_verify` reads `_leash_for` without `apply_cap`), and `reauthorized_or_denied` doesn't re-check the leash at all, so the design's central "no loosening" property is unenforceable on the approve path as wired; the design must centralize leash resolution through a single capped function and show the modified seam code for both `govern_action` and `reauthorized_or_denied` before it can be considered sound.
