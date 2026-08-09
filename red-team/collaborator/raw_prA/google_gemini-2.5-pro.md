# PR-A host-emit code panel: google/gemini-2.5-pro

_finish=stop seconds=70.6 cost=$0.1520 usage={'prompt_tokens': 57983, 'completion_tokens': 7953, 'total_tokens': 65936, 'cost': 0.15200875, 'is_byok': False, 'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0, 'audio_tokens': 0, 'video_tokens': 0}, 'cost_details': {'upstream_inference_cost': 0.15200875, 'upstream_inference_prompt_cost': 0.07247875, 'upstream_inference_completions_cost': 0.07953}, 'completion_tokens_details': {'reasoning_tokens': 5893, 'image_tokens': 0, 'audio_tokens': 0}}_

This is a strong delta. The author has clearly internalized the red-team findings and implemented multiple, overlapping defenses. My review focuses on attempts to bypass these new layers.

***

### FINDINGS

#### ID: PROP-1
*   **TITLE:** Proposer Structurally Barred From ANY `net_post` Proposal
*   **SEVERITY:** LOW
*   **LOCATION:** `collaborator/propose.py:160-162`
*   **CONCRETE INPUT OR BYPASS:** A model response to the proposer prompt that correctly forms a `net_post` action:
    ```json
    {"propose": true, "confidence": 0.9, "rationale": "Send status update to webhook",
     "action": {"name": "net_post", "args": {"url": "https://hooks.example.com/status", "body": "ok"}}}
    ```
*   **WHY IT BREAKS A GUARANTEE:** This does not break a core security guarantee; rather, it's an overly broad hardening measure that may conflict with the intended product surface. The `propose` channel's philosophy is to surface valuable, *human-gated* actions. This change structurally blocks the proposer from *ever* suggesting a `net_post` action, even one that would be safely held for approval. This prevents potentially useful proposals (e.g., "propose sending results to a webhook") and makes the downstream `govern_action` logic for handling `source=="proposed"` `net_post` intents effectively dead code, reachable only if another part of the system synthesizes such an intent. It hardens the system by removing a capability from the proposer entirely, but at the cost of expressiveness, and it does so silently.
*   **WHETHER ANOTHER CHECK CATCHES IT:** N/A. This is the terminal check. The downstream `govern_action` logic would have correctly and safely handled such a proposal by flooring its leash to `propose_first`, which is why this structural block is arguably redundant and overly restrictive.
*   **FIX:** Remove the new check at `propose.py:160-162`. The existing, robust floor in `governance.py:273` (`if ... getattr(tool, "egress", False)`) and the emission-specific floor at `governance.py:341` (`if not auto: leash = apply_cap(leash, PROPOSE_FIRST)`) are sufficient to guarantee any proposer-originated emission is held for a human hand.

***

### NON-FINDINGS (ATTACKS CONSIDERED AND BLOCKED)

This delta successfully defends against all prompted high-risk attack vectors. The following attempts to break guarantees failed due to specific, well-implemented controls:

1.  **DEFEAT F1 (Model-driven autonomy):** **BLOCKED.**
    *   **Attack:** Make a model-emitted `net_post` run autonomously.
    *   **Defense:** The emission floor at `governance.py:339` requires `getattr(intent, "source", "") == "host"`. The parser (`toolcall.py`) can only produce `structured`, `content_block`, or `content_json`. The proposer (`propose.py`) produces `proposed`. Neither can produce `host`. Furthermore, the model loop (`run_turn`) does not pass the required keyword `leash=ACT_THEN_REPORT`. The F1 guarantee holds firmly; the model cannot self-originate an autonomous emission. This is confirmed by `tests/test_collaborator_loop.py:NetPostF1ModelCannotSelfOriginate`.

2.  **DEFEAT 'REQUIRE BOTH':** **BLOCKED.**
    *   **Attack:** Trigger autonomous emission with only `net.post.auto:<host>` but no `net_post: act_then_report` leash-cap.
    *   **Defense:** The logic at `governance.py:339` checks `leash == ACT_THEN_REPORT` *after* the signed leash cap has been applied at `governance.py:269`. If `net_post` is unlisted in the grant, `leash_cap()` defaults it to `NOTIFY_ONLY` (`policycaps.py:166`). The `auto` check fails, and the action becomes `NOTIFIED`. The new logic at `governance.py:351` then provides a loud, specific reason, preventing the silent no-op of MINOR-A. This is confirmed by `tests/test_collaborator_egress_gate.py:test_autonomous_emit_without_leash_cap_notifies_loudly_not_silently`.

3.  **CANONICAL HOST CONFUSION:** **BLOCKED.**
    *   **Attack:** Satisfy `net.post.auto:api.example` but POST to a different host via IDNA/case/etc.
    *   **Defense:** The capability check in `governance.py:337` and the credential lookup/connection in `execute_and_verify`/`egress.py` BOTH derive the hostname from the same `egress.canonical_host()` function. There is a single source of truth for the destination, closing the gap between authorization and connection.

4.  **BREAK `approved == sent` (TOCTOU):** **BLOCKED.**
    *   **Attack:** Mutate `decision.args` after `emit()` but before `approve()`.
    *   **Defense:** `loop.py:136` (`args = dict(decision.args)`) creates a snapshot of the arguments upon entering `approve()`. All subsequent operations, including the seal re-check at `loop.py:152` and the call to `execute_and_verify` at `loop.py:173`, use this immutable snapshot. This robustly prevents any mutation of the original `decision.args` from affecting what is checked or sent. Confirmed by `tests/test_collaborator_egress_gate.py:test_snapshot_reads_held_args_once_so_a_proxy_cannot_swap_the_wire`.

5.  **CREDENTIAL LEAK / INJECTION:** **BLOCKED.**
    *   **Attack:** Inject credentials via model args; leak credentials via response echo.
    *   **Defense:**
        *   **Injection:** `execute_and_verify` (`governance.py:465`) sources `egress_auth` exclusively from `session.egress_credentials`, keyed by the canonical host. It does not read `auth` or `authorization` from the `args` dict.
        *   **Leak:** `_exec_net_post` (`tools.py:359`) explicitly calls `_redact_credential` on the response text before it is stored in the `Execution` result, preventing an echoed credential from entering the audit trail. Confirmed by `tests/test_collaborator_egress_gate.py:test_echoed_credential_is_redacted_from_output_and_summary`.
        *   **Cross-Session:** `loop.py:141` adds a cross-subject check, binding a held decision to its origin session's workspace subject and denying approval from a different one. This prevents using one session's payload with another's credentials.

6.  **RAISE on bad input:** **BLOCKED.**
    *   **Attack:** Pass a lone-surrogate string body or non-ASCII `content_type` to `emit()`/`approve()` to make the seam raise an exception.
    *   **Defense:** `egress.post` contains explicit checks for non-UTF8-encodable string bodies (`egress.py:431`) and non-ASCII header values (`egress.py:445`, `egress.py:451`), returning a `FAILED` decision rather than raising. The broader `try...except` block in `execute_and_verify` (`governance.py:472`) acts as a final backstop. The system correctly fails closed.

***

### STEELMAN & VERDICT

**STEELMAN:** This code is exceptionally well-hardened. It not only implements the "require both" rule for autonomous emissions but does so with defense-in-depth at every step. The F1 guarantee (model cannot self-originate autonomy) is enforced by two independent, non-model-reachable signals (`source=='host'` and the keyword `leash`). The `approved == sent` guarantee is robustly enforced by snapshotting arguments at the moment of approval. Credential handling is pristine: injection is from a trusted source only, and redaction prevents leaks into the audit trail. The code consistently fails closed, turning potential exceptions into clean `FAILED` or `DENIED` decisions. The removal of `signed_leash_cap` simplifies the logic, and the new structural bar in the proposer adds yet another layer of safety.

**VERDICT:** **SOUND.** The implementation correctly and robustly enforces all stated security goals for autonomous emission, with multiple layers of defense against the most critical attack vectors.

The highest-value fix is to **re-evaluate the new structural bar in `propose.py` (`PROP-1`)**, as it may be an overly aggressive hardening measure that unnecessarily limits the proposer's utility, while the existing `govern_action` floors already provide sufficient safety.
