# Verifier — Final Recommendation (v3)

**Provenance.** This is the third and authoritative pass. It supersedes `00-SYNTHESIS-recommendation.md` (v1, synthesis of seven independent proposals) and `red-team/00-RED-TEAM-SYNTHESIS.md` (v2, after 42 blind round-robin critiques). v3 folds in a *third* round: all seven models then reviewed v1+v2 and rated the coordinator's synthesis **7/7 `PARTIALLY_SOUND`**, catching several real errors in v2 (`red-team/synthesis-review/00-READOUT.md`). v3 corrects them. The design principles are stable across all three rounds; what changed is the framing of the core problem and the buildability of the fix.

**Corrected thesis (3–4 sentences).** Build the verifier as a separate-process, side-effect-free evidence pipeline that folds typed, provenanced evidence into a three-state verdict via an explicit, pure, fail-closed aggregation predicate — the predicate, not the check list, is the component. Its one non-negotiable rule: a status reaches `VERIFIED` only when at least one **world-side fact the executor did not write** agrees, with a *distinct failure mode*, with the executor's claim. The earlier draft over-reached by calling the whole panel guilty of "verifying nothing"; the accurate problem is narrower and fixable — **independence was under-enforced and stakes-gated, and the expectation oracle was under-grounded** — and the cheap fix (host-side runtime observation + envelope-derived expectations) is buildable on the target node without mandatory replay.

---

## 1. Commit to a threat model — this is the *first* design decision, not a footnote

Every prior round tripped on this, so it goes first and is binding:

- **In scope (v1 target):** a *wrong or misfiring model* that proposes a bad action or asserts success it didn't achieve; a *buggy executor/tool*; non-malicious corruption or partial failure. This is exactly what the brief's **Req 3** targets — the verifier must be external to *the model* (the same instance / prompt / reasoning chain), and **Req 1** deliberately names executor-produced artifacts (exit codes, output/file hashes, telemetry) *as* ground truth.
- **Out of scope (needs separate work):** an *actively malicious, kernel-capable executor* that spoofs the observation channel itself (forges audit events, presents decoy mounts). Defeating that requires a **hardware root of trust** — measured launch / TPM / enclave attestation of the observation path — which no software-only design on a single shared-kernel node provides. Do **not** ship claiming compromise-resistance you don't have.

**Consequence that the whole design rests on:** because the executor is *trusted-not-hostile*, "independent" means **not derived from the executor's own written artifacts or its self-reported receipt** — *not* "resistant to a kernel-level attacker." Under this definition, a host-side re-hash of a bind-mounted output, or the container runtime's own exit status, **is** a genuine second fact. This is the point v2 got wrong: it silently demanded kernel-independence (a compromised-executor standard) and then declared the panel's host-side observation worthless. It isn't — for the in-scope adversary it is exactly the independent evidence Req 1/Req 7 want.

If the project later needs the adversarial executor in scope, that is a **new requirement** (add attestation), not a bug in this verifier.

---

## 2. Architecture

`executor → signed receipt → Verifier → {reporter, salience updater, adaptation gate}`. Separate process from executor and inference server; holds no handle to the policy grant function; writes no memory. Six parts:

`ReceiptIngress → ObligationBuilder → CheckRunner → EvidenceLog (append-only, content-addressed; CLAIM and WORLD evidence in physically separate typed stores) → VerdictComposer (pure, versioned) → VerdictEmitter`, with an optional, sheddable `AdvisoryReviewer`.

The one structural change from v1: **CLAIM-side and WORLD-side evidence live in separate stores / phantom-typed channels**, so the composer *cannot* count an executor-derived fact as an independent one. In v1 (Kimi's design) this was a runtime `side: CLAIM|WORLD` enum field — the reviewers correctly noted a one-line refactor could drop the filter. Make it a type, not a flag.

---

## 3. The recommended design (corrected mandates)

**M1 — One executor-independent world-side fact per verified obligation, enforced by construction.** `VERIFIED` requires ≥1 WORLD fact that (a) is observed through a channel the executor did not write, and (b) has a *distinct failure mode* from the CLAIM fact it agrees with. Enforced by the separate-store typing of §2, not by convention. **Softened per review (Qwen):** the *required* independent channel is declared **per action class, scaled by stakes** — not an unconditional "replay everything."

**M2 — The cheap independent fact is host-side observation, not replay.** The always-on WORLD facts are nearly free and CPU-only:
- **container-runtime exit status** read from the runtime/supervisor (not the receipt's self-reported code);
- **host-namespace re-hash** of the declared output artifacts (bind-mounted; read from outside the sandbox mount namespace after teardown);
- **host-observed write-set diff** (declared paths changed; no undeclared paths changed).

Reserve **idempotency replay** for *stakes-scaled, CPU-deterministic, idempotent* ops only — and label it honestly as a **repeatability** check (catches non-idempotency and flaky corruption), *not* an independence oracle (it re-runs the same executor). GPU/LLM-adjacent and non-idempotent actions (send, delete) are **not** replayed.

**M3 — Ground the oracle in the authorized action envelope.** Expected values come from the **policy-signed action envelope**, which for deterministic ops derives the expected post-state directly from the authorized args — e.g. `write(path, bytes) ⇒ expected_artifact_hash = hash(bytes)`; `mkdir(p) ⇒ p exists`. This is independent of the model's *narrative* because it is the *authorized args*, not the model's after-the-fact claim. (v2 wrongly called this "ungrounded"; it is grounded for the common case — the real gap is the authoring/review/completeness *workflow* for non-trivial contracts.) A **minimum-obligation floor** per action class cannot be dropped by any contract: independent exit observation + independent artifact/side-effect check + boundary check. **Missing or empty contract ⇒ `UNVERIFIED`**, never `VERIFIED`.
Honest limit: **generative/semantic-intent actions** ("improve this code", "summarize") and **artifact-less external actions** (email, API) have no deterministic oracle. Under the brief's "no cloud" assumption they resolve to `UNVERIFIED`, or to acceptance-scoped user confirmation / (later) external attestation. State this; do not paper over it.

**M4 — The aggregation predicate is explicit, pure, fail-closed, and the primary mutation-test target.** (Every reviewer called this the strongest, most buildable mandate.)
```
def compose(contract, claim_ev, world_ev, stakes):   # claim_ev/world_ev are SEPARATE typed inputs
    for o in contract.required_obligations:           # floor obligations are always present
        if conclusive_contradiction(o, claim_ev, world_ev): return FAILED
    for o in contract.required_obligations:
        if not agrees_with_distinct_failure_mode(o, claim_ev, world_ev, stakes):
            return UNVERIFIED                          # incl. only-CLAIM evidence, budget-exhausted, no contract
    return VERIFIED
```
`stakes` is a **policy-signed input**, not a mutable request field. There is no code path from an `AdvisoryReviewer` note to the verdict; notes may cap downward or *request an already-typed check*, never satisfy an obligation.

**M5 — `INTEGRITY_ATTESTED` is a reason sub-code of `UNVERIFIED`, not a fourth state.** (Corrects v2, which reviewers flagged as violating Req 6.) When the receipt is authentic and self-consistent but no independent world-side fact was obtained, the status is `UNVERIFIED` with reason `INTEGRITY_ATTESTED`. It is **hard-blocked from adaptation and from user-facing "success,"** exposed via a dedicated `require_attested()` accessor so consumers must handle it explicitly, and — critically — a **sealed consumer gate**: the receipt store rejects any row where `reported_success = true ∧ status ≠ VERIFIED`, so an operator can't quietly map "attested" to "success." This keeps three top-level states while preventing the `--trust-unverified` laundering the reviewers warned about.

---

## 4. Cost / scaling (corrected)

| Tier | Checks | Independent? | Cost |
|------|--------|--------------|------|
| **Always** | receipt sig/schema, image-digest vs policy, **host-runtime exit**, **host-namespace artifact re-hash**, **write-set diff** | **yes (≥1)** | CPU, ms |
| **Stakes-scaled** | eBPF/audit correlation, idempotency **replay (CPU-deterministic idempotent only)**, scoped tests, acceptance-scoped user confirmation | partial | CPU/GPU, bounded |
| **High/Crit** | mandatory two-source per obligation, full tests, (later) external attestation | strong | expensive, rare |

Key correction: **the always-on tier already contains an executor-independent fact**, so ordinary deterministic file/tool actions earn a *real* `VERIFIED` cheaply — they do **not** all collapse to `INTEGRITY_ATTESTED`, and replay is **not** on the hot path. Controls: Merkle/incremental hashing (re-verify O(change)); memoization keyed on `(op, args_digest, pre_state_digest, contract_version)` **with an epoch/TTL guard** so a stale `VERIFIED` on since-mutated state is re-checked, not trusted forward. This keeps Req 8 (bounded, stakes-scaled) intact — which v2's "replay-always" mandate violated.

---

## 5. Verifier self-test

Mutation-test in two directions: mutate the system-under-test (some check goes red) **and** mutate the pure composer against golden `(claim_ev, world_ev) → verdict` fixtures. The four fixtures that matter, each must turn a check red:
1. **Byte-flip** post-write → host-namespace re-hash disagrees → `FAILED`.
2. **Exit-code laundering** (wrapper exits 0 on real failure) → host-runtime exit status ≠ receipt claim → `FAILED`.
3. **Right bytes, wrong path** (writes to unauthorized path) → write-set diff shows undeclared mutation / declared path unchanged → not `VERIFIED`.
4. **Drop the CLAIM/WORLD separation** (the refactor risk) → composer fixture that offered only CLAIM evidence must yield `UNVERIFIED`, never `VERIFIED`.
Plus a coverage gate: for every named invariant in a contract, *some* mutant must trip *some* check; green-on-mutant is a verifier bug.

---

## 6. MVP (buildable, narrow, honest) + graduated rollout

Ship:
- Three-state enum + `INTEGRITY_ATTESTED` sub-code; both leak-locks + `require_attested()` + sealed stakes.
- The **explicit fail-closed aggregation predicate** and its four mutation fixtures. *(the spine)*
- Always-on checks **including the ≥1 executor-independent one** (host-runtime exit + host-namespace artifact re-hash + write-set diff) → real `VERIFIED` for deterministic file/tool ops on day one.
- **Envelope-derived expected values** for the top deterministic op classes; **minimum-obligation floor**; missing contract ⇒ `UNVERIFIED`.
- CLAIM/WORLD evidence in separate typed stores.

**Graduated deployment (DeepSeek's point):** ship this, then *log the disagreement rate* between receipt-consistency and the independent host-side checks in production, and tune stakes thresholds and which action classes demand replay/eBPF **from that data** — rather than pre-committing to expensive independence everywhere.

**Defer:** advisory LLM (the type fence means adding it later can't weaken anything), external-attestation adapters, calibrated confidence, broad replay/eBPF, and hardware-rooted attestation (the whole compromised-executor workstream).

---

## 7. Coordinator's pitfalls (what *I* got wrong across these rounds)

The user asked for this explicitly, and it's the most useful part for anyone re-running this exercise. My synthesis errors were not random — they cluster into a few failure modes worth naming:

1. **I let an adversarial harness redefine the spec, then scored the panel against the redefinition.** The red-team prompt said "attack, not balance." I then read the resulting **42/42 `SERIOUS_FLAWS`** as *convergence on truth* rather than as *what the instruction guaranteed*. An attack-only rubric measures "can a flaw be articulated," never "is the design unsound." I manufactured the consensus and then cited it. **Lesson:** an adversarial pass needs a symmetric steelman pass, and its output must be checked back against the actual requirements before it's treated as a verdict.

2. **Threat-model substitution — the load-bearing error.** The critiques quietly upgraded "independent of the *model*" (Req 3) to "independent of a *compromised executor*," and I propagated it without checking it against Req 1 (which names executor artifacts as ground truth). That single unexamined move turned a real-but-narrow finding ("independence under-enforced") into a false sweeping one ("the panel verified nothing"). **Lesson:** when a critique's force depends on a threat model, state the threat model and verify the spec actually asks for it.

3. **Double-counting dressed as evidence.** I presented "Req 7 flagged 55×" as strong corroboration, when one flaw (correlated receipt checks) was being logged under Reqs 1, 3, *and* 7 by the same judge. Correlated observations of one issue, counted as independent votes.

4. **A corroboration claim I never checked.** I wrote that the blind severity ranking "independently reproduces my first synthesis's ordering." It doesn't (I'd ranked MiniMax #7 and Qwen #2; the heatmap put both mid-pack), and it wouldn't be independent even if it had — same models, same rubric.

5. **I mischaracterized a proposal and contradicted my own prior text.** v1 praised Kimi's envelope-derived obligations as "fixing Req 3 at the root"; v2 called the same design's oracle "ungrounded." Both can't be true — and the first was closer to right.

6. **I strawmanned my own earlier MVP** to make the "reversal" look larger than it was, and **invented a mechanism (`INTEGRITY_ATTESTED`) and attributed it to the panel** ("several judges effectively demand"). No judge proposed it; I did.

7. **I shipped an internally contradictory recommendation:** v2 called shared-kernel telemetry spoofable in one section and mandated kernel-audit-from-a-namespace as *the* independent channel in the next. That's a tell that I was pattern-matching to "independence = good" instead of reasoning about a specific mechanism on the specific hardware.

8. **Recency/severity bias.** I consistently weighted the newest and most aggressive input as the most authoritative — first the proposals, then the red-team over the proposals, and I would have done it again had the reviews not forced the correction.

9. **Minor execution pitfalls** worth logging for the next run: OpenRouter rejected the raw brief until it was `.Trim()`-ed (`400 messages.0.content`), and PowerShell 5.1's `Invoke-RestMethod` silently mis-decoded UTF-8 as Latin-1 (mojibake) until I switched to `Invoke-WebRequest` + explicit UTF-8 decoding. Neither affected content, but both could have.

The through-line: **the adversarial rounds were valuable but not self-correcting.** What actually caught the errors was the *non-adversarial* round — asking the same models to review the synthesis. If you take one process lesson from this exercise: **pair every adversarial pass with a review pass, and re-anchor both to the original spec.**

---

## 8. Open questions carried into the build

- **Contract authoring/review/completeness workflow** — the real weakest point (not "the oracle is ungrounded"). Who writes and signs per-action-class contracts, and how is completeness itself mutation-tested?
- **Generative & artifact-less actions** — accept permanent `UNVERIFIED` (honest but limits usefulness), or add acceptance-scoped user confirmation / external attestation? A product decision, not a verifier decision.
- **Threat-model boundary** — is the trusted-executor assumption acceptable for the product, or is a hardware-attestation workstream in scope? This bounds everything above.

---

### Bottom line

Three rounds converge on a modest, buildable design that the first two rounds each overstated in opposite directions. Verify the executor's story against **one cheap fact it didn't write** (host-side runtime + artifact re-hash), ground expectations in the **policy-signed action envelope**, decide the verdict with an **explicit fail-closed predicate you mutation-test**, keep **three states** with `INTEGRITY_ATTESTED` as an un-launderable sub-code of `UNVERIFIED`, and **say plainly that this detects bugs and model-error, not a compromised executor.** Ship that, measure the disagreement rate, and let the data tell you where to spend the expensive checks.
