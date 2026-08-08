# ④ The Collaborator's memory — panel synthesis

*Design put to a 5-model external panel BEFORE any code (`redteam_memory.py`;
deepseek-v4-pro, grok-4.5, mistral-medium-3.5, kimi-k3, glm-5.2). Reproduce-before-accept
applied: every finding checked against the design doc and the actual code (propose.py, the
CDMS firewall) before acceptance. This records what the panel found, what survived
verification, the severity corrections, and the one decision that is Josh's.*

## Headline — the architecture held; the design doc did not

**Verdicts:** SERIOUS_FLAWS ×3 (grok, deepseek, mistral) · MINOR_ISSUES ×1 (glm) · kimi
truncated at F5 (no final verdict). At face value that reads alarming. It is not, and the
reproduce pass is why:

**The core claim — memory INFORMS, never AUTHORIZES — survived all five.** kimi traced every
memory consumer into `govern_action`/`propose`/`approve_proposal` and found the capability
decision derives *exclusively* from `granted_capabilities(session)` (③ verified caps) + host-
constant signals; "no ingested deed, gist, scar, or preamble reaches the gate." glm's steelman
independently confirms it. So P-01 is intact by construction. The "SERIOUS_FLAWS" are **not**
about memory buying permission — they are about (1) the design doc making internally
**contradictory** claims, and (2) **secondary** safety properties (injection, cross-user
privacy, cross-project scope) being under-specified. For a pre-build design review, that is the
panel doing exactly its job: the shape is right, the doc needs one hard revision pass before a
line is written.

## API cost (this review)

| model | cost | tokens (in→out) |
|---|---|---|
| deepseek-v4-pro | $0.0045 | 15370 → 11456 |
| glm-5.2 | $0.0068 | 14610 → 9816 |
| mistral-medium-3.5 | $0.0513 | 15458 → 3750 |
| grok-4.5 | $0.0676 | 15472 → 6143 |
| kimi-k3 | $0.2840 | 14668 → 16000 (hit length cap) |
| **TOTAL** | **$0.4142** | |

## The findings — clusters, verified

Nine distinct issues, every one **confirmed** as a real design gap (with two severity
corrections from the code check). Ordered by how load-bearing.

| # | Cluster | Who | Sev (assessed) | Status |
|---|---|---|---|---|
| A | **Recall-visibility contradiction** — deeds are `untrusted` (dropped from model-facing recall) yet the doc also says "the agent finds its own history." Both can't hold. | grok(CRIT), deepseek, kimi, glm | **HIGH — genuine contradiction** | CONFIRMED · **Josh's decision** (R1/R2 below) |
| B | **Fact stores outside the firewall** — the verified firewall covers only CDMS-A *episodic* tuples; world/user/system facts reach the proposer/boot with no stated provenance or injection fence. "Verifier-grounded" proves a fact is *true*, not *safe-as-instruction*. | ALL 5 | **HIGH** | CONFIRMED |
| C | **System-store privacy is a proof for an undesigned mechanism** — Proof #6 claims ingestion-time refusal while Honest Scope admits ingestion is undesigned; store is shared across ALL users (widest blast radius). | ALL 5 | **HIGH** | CONFIRMED (oversell) |
| D | **Cross-project secret leak via shared self** — deed *content* (paths, command args, tool output) rides the shared-per-user self from project A into project B's context. Identity should be continuous; raw episodic content should not. | grok, deepseek, glm, mistral | **HIGH** | CONFIRMED |
| E | **Injection at the collaborator-side assembly** — propose.py merges `context` as a raw string into the user message; CDMS's `hooks.py` fence lives in the CDMS repo and does not cover the collaborator's own boot/recall assembly. | mistral(CRIT), grok, deepseek, glm, kimi | **HIGH (not CRITICAL)** | CONFIRMED w/ correction |
| F | **`memory.read` confused-deputy** — if the tool passes through `include_untrusted`/`project`/`principal`, the model can un-fence or cross-scope-read. | grok, deepseek, glm, kimi | **MEDIUM** | CONFIRMED |
| G | **Feedback-loop amplification** — recall→proposal→deed→recall can drift surfacing bias / manufacture *in-context* apparent-corroboration; veto-decay is an unbounded monotone. Authority intact. | grok, deepseek, mistral | **MEDIUM (UX/drift, not authority)** | CONFIRMED |
| H | **propose.py wiring is recall-naive** — no recall handle today; underspecified integration invites feeding memory into leash/importance (influence axes), which a bug could couple to hold-vs-run. | grok | **MEDIUM** | CONFIRMED (good invariant to pin) |
| I | **Honesty split** — "read-only to the model" / Proof #6 / fact-fencing asserted but not code-pinned; should be split into enforced-v0 vs deferred. | grok, mistral, glm | **LOW** | CONFIRMED |

### Severity corrections (the reproduce pass earning its keep)

- **mistral's two CRITICALs are both overstated.** F1 ("hooks.py:102 does not filter untrusted
  episodes from the preamble, only fences them") is **inaccurate**: `hooks.py:100-102` *excludes*
  untrusted episodes from the preamble source under `enforce_provenance` (verified), and the
  preamble is already project-scoped (line 101). F2 ("system-facts default to trusted and can
  form a gist/scar") is **off**: facts are a separate store (the `world_fact`/facts tables), not
  episodes — they never enter the episodic gist/scar elevation pipeline. The *residual* real
  issue in both is the **fact-store / collaborator-assembly** injection surface (Cluster B/E),
  which is HIGH, not CRITICAL, because ③ still gates every run — injection yields at worst a
  *misleading gated proposal* (+ within-granted-capability harm if the human approves), never a
  capability bypass.
- **Cluster D is partly already supported by CDMS**, which strengthens the fix: `TurnEvent` has a
  `project` field (store.py:196) and the preamble builder is project-scoped (hooks.py:101). The
  gap is that the design didn't *specify* stamping the project tag and scoping recall by it —
  a spec fix, not new machinery.

## The one decision that is Josh's — Cluster A (recall visibility)

The doc asserts both "deeds cannot surface on model-facing recall" and "the agent finds its own
history." They contradict because the doer's own deeds are stamped `untrusted`, and `untrusted`
in CDMS means *both* "cannot elevate" *and* "dropped from model-facing recall." Two honest
resolutions:

- **R1 — fenced-visible (recommended).** Deeds stay `untrusted` for *elevation* (never gist into
  a guardrail, never scar, never become self). A **single host-side assembler** surfaces them to
  the proposer/boot with `include_untrusted=True` **in that one host path only**, every deed
  wrapped as immutable DATA; the model-controlled `memory.read` hard-pins `include_untrusted=False`.
  Delivers "the agent finds its own history" while keeping the firewall. Requires re-scoping the
  "cannot surface" claim to the model-controlled path and folding B/E/F fencing in.
- **R2 — audit-only (stricter, smaller).** Deeds never surface to the model at all; veto-awareness
  becomes host-side session state (the veto-decay config already is that); memory's v0 doer-side
  value is the operator-audit trail + whatever *trusted* facts exist. Safer and simpler, but the
  "find its own history" feature is deferred — the component does less.

R1 is the recommendation (it's the feature we set out to build, and the firewall survives it
hardened). R2 is the honest smaller-scope alternative. **This is an intent call, so it waits for
Josh.**

## The resolution set (folds into a v2 design once A is decided)

All confirmed, none changes the architecture:
- **B/E:** every fact-store and memory span entering *any* model-facing context (boot, recall,
  `memory.read`) passes through one collaborator-side flatten-and-fence-as-DATA assembler;
  "verifier-grounded" documented as a *truth*, not *trust*, property; injection-canary tests in
  `collaborator/`, not only CDMS.
- **C:** v0 system store is **operator-pinned only** (drop verifier-observed system facts until the
  scope check ships); "system-scoped" = a positive allowlist (OS caps, hardware flags, package
  facts) + structural denylist (home paths, credential-shaped strings, hostnames/user-ids),
  enforced at ingestion; Proof #6 becomes a test over that predicate or moves to "deferred."
- **D:** stamp every `TurnEvent` with its `project`; recall/boot filter deed-derived content by
  the current project; only abstracted gist/persona residue crosses projects; ingestion redaction
  of workspace-absolute paths / env-like / credential-shaped values.
- **F:** bind principal + workspace at `MemorySource` construction; `memory.read` schema =
  `{query, tiers}` only (no model-supplied `project`/`principal`/`include_untrusted`).
- **G:** recall token budget cap, dedupe near-identical deeds, veto inhibitor floor independent of
  CDMS elevation; down-weight memory-only-rationale proposals.
- **H:** normative wiring — memory raises only the *surfacing* threshold, never leash / capabilities
  / budgets; pin a test that recalled content cannot alter the resulting `Decision`'s leash or caps.
- **I:** split Properties into *enforced-v0* vs *deferred*; scope "read-only to the model" to the
  fenced paths.

## Where this leaves us

The architecture is validated by the panel (memory ≠ authority holds by construction). The design
doc needs one revision pass — the resolution set above — gated on Josh's Cluster-A call. **Nothing
is authorized to build.** Next: Josh picks R1/R2, I fold the resolution set into a v2 design doc
(optionally a quick second panel on the revised doc if he wants), then build + prove.
