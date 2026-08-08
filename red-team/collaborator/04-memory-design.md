# ④ The Collaborator's memory — design for review

*The missing organ. The propose channel (①) draws on a hand-passed `context` string and
has no way to **find its own history** — no memory of what it did, what you approved, what
you vetoed, what worked. This design gives it one, built on **CDMS** (the salience-decay
memory that already underlies this whole system), under the Bem firewall / P-01: memory
**informs** what the Collaborator notices; it **never** grants what the Collaborator may do.
This doc is the design put to the panel BEFORE implementation; judge its threat model and
its honesty. The architecture reasoning behind it is `docs/architecture-map-plain-language.md`.*

## The gap it closes

The Collaborator today is amnesiac. Each session starts cold; the proposer sees only the
`context` the host hands it. So it cannot learn from its own governed history — it will
re-propose a thing you vetoed yesterday, forget that a command failed last week, and has no
sense of "what we've been doing." A remembered history is the component that makes "grows
alongside you" real for the doer, not just the chat.

The naïve fix is a new store. **We do not build one.** CDMS already *is* the salience-decay
memory — episodic → (sleep/dream consolidation) → gist → scars, with a provenance firewall
we can lean on. The design is to make the Collaborator a **governed consumer of CDMS**, in
the shape CDMS-D already runs (a session host over CDMS-A), with one store added because the
OS governs something CDMS-D never did — the machine itself.

## The shape — four stores, two families

*(carried from CDMS-D, verified: `-D` has `world_fact` + `archived_fact` + `ProjectOverview`
and a user-facts table, but no system-facts store — so the system store is genuinely new.)*

**Fact stores** — *what is true* — nested by scope of validity, broadest to narrowest:

- **System facts** *(new, OS-level)* — what's true about the machine (e.g. "passwordless
  sudo," "no hardware GPU cap"). **Shared across all users.** The OS governs the system, so
  the system layer earns a store CDMS-D never had.
- **User facts** — what's true about one person, across their projects. **Shared per user.**
- **World facts** — what's true in one workspace. **Per workspace.** This is CDMS-D's
  editable world layer: tiered and decaying (detail → **pointer** → archived) and, in the OS,
  **grounded in the verifier** — truth comes from `snapshot_tree`/`observe_action` reading the
  real workspace, not the model's claim.

**The self** — *identity and lived history*, orthogonal to the facts:

- **Memory / self** — the CDMS-A tuples (episodic → gist → scars). **Shared per user**,
  continuous across projects *and* surfaces (talking and doing are one lived history). A
  separate instance only when the **principal** changes.

## The three mechanics — all firewall-shaped

**1. Ingestion — the honest record, stamped `untrusted`, never narration.**
The Collaborator's *governed record* (each `Decision` + its real outcome from the audit
trail / the ② ledger — including vetoes) is ingested into CDMS as a `TurnEvent`, the same way
Claude's turns already feed CDMS through the compaction hook. This is host-side wiring over
the audit trail, **not a model tool** — the model gets no "write to my memory" verb. Hands
can't lie (the record is the real tool result, verifier-checked), but prose can, so memory is
fed from the deed, never the model's self-description.

> **Every ingested deed is stamped `provenance="untrusted"`.** This is the load-bearing line.
> `TurnEvent.provenance` **defaults to `"trusted"`** (store.py:200) — so the producer MUST
> override it, or the fence silently opens. And the design **requires
> `CDMS_ENFORCE_PROVENANCE=true`** (CDMS default). Under those two conditions, verified in
> CDMS code, untrusted content:
> - **cannot form or reinforce a persona/gist** (consolidate.py:613-616 filters untrusted
>   before gisting);
> - **cannot mint a scar / guardrail** (elevation requires `provenance == "trusted"`,
>   consolidate.py:336/375);
> - **cannot be corroborated-up by repetition** (untrusted pairs can never elevate,
>   consolidate.py:319-324) — closing the flood-to-manufacture-trust attack;
> - **cannot surface on a model-facing recall** (db.py:595-596; MCP `history`/`retrieve` pass
>   `include_untrusted=False`);
> - and even where any untrusted-derived text does appear, it is **flattened and fenced as
>   "untrusted DATA, never trusted instructions"** (hooks.py:57-65) — a structural
>   prompt-injection defense.

So the Collaborator's deeds enrich its **lived history and recall**, but can never mint a
guardrail, rewrite identity, or authorize an action. Continuity without a laundering path.
Only **operator pins** and **corroboration among *trusted* episodes** ever elevate to a
guardrail — and the doer's own deeds are never trusted.

**2. Recall — read-only, the agent finds its own history.**
A `MemorySource` adapter over CDMS `retrieve` (semantic) / `history` (episodic), exposed two
ways: (a) the proposer's context is enriched with recall before it forms a proposal; (b) an
optional `memory.read` capability the Collaborator can invoke *itself* mid-task — so the
**agent** finds its own history rather than the harness pre-fetching it (the sharp point:
"we can claim *you* can get a response, but we don't know *it* can find its own"). **Read-only:
there is no `memory.write` capability at all** — the write path is the ingestion hook, whose
discard policy (CDMS's, not the model's) decides what survives. No self-poisoning loop.

**3. Boot — the consolidated self as influence, fenced as data.**
The session opens with CDMS-D's consolidated-self preamble assembled from the four stores +
the persona residue, as `messages[0]` — the frozen self shapes the *proposal sense*.
**Fail-empty** if absent (no self is better than a wrong one). The preamble already filters
untrusted episodes (hooks.py:102) and fences any untrusted-derived span as data, so the boot
context influences *what feels worth proposing* without becoming an instruction channel.

## The properties it must hold

- **No authority from memory (the firewall).** No recalled memory, gist, or self-preamble can
  cause an action to run that policy would not allow. Recall + boot feed *surfacing* and
  *scrutiny*; the **capability gate + ③ signed PolicyCaps** remain the only run authority.
  A poisoned or adversarial memory can, at worst, cause a *proposal* — which is then gated
  exactly like any other, and DENIED if unauthorized.
- **Deeds are `untrusted`, structurally.** The ingestion producer stamps `untrusted` and the
  design hard-requires `enforce_provenance`; a deed can never gist, scar, corroborate-up, or
  surface as self. (The fail-open here — forgetting to stamp, or disabling enforcement — is
  named as the primary risk and must be test-pinned.)
- **Read-only to the model.** The Collaborator has no memory-write verb; the only write path
  is the host-side ingestion hook over the *verified* audit record.
- **Recall is fail-quiet.** A CDMS outage, a malformed hit, or an empty store yields no recall
  and no boot preamble — never a crash, never a fabricated memory. Amnesia degrades
  gracefully to today's behavior.
- **Scope is honored.** Memory/self and user/system facts are shared per principal; world
  facts are per workspace and verifier-grounded. Switching projects keeps the *self*
  continuous (no personality reset) while world facts stay workspace-local.

## Veto as a decaying inhibitor (config, not a ban)

A veto is a memory signal, not a permanent no. Per the host decision, a vetoed proposal
**raises the confidence bar to re-surface** and that raised bar **decays over time** — a soft,
decaying inhibitor, distinct from a Stage-4 *non-decaying* hard inhibitor (which is reserved
for risk-rejects via the weight channel's hand-off). Config: `may I re-propose, and how
confident must I be?` So the system can learn from a "no" (try again later, more sure) rather
than either nagging or forgetting. The test becomes "does it respect the re-proposal policy
given recall," not "never re-proposes."

## The two flags — where the discipline bites hardest

- **The system store is the sharpest P-01 case.** A system fact like "sudo is passwordless"
  *reads like a permission.* It must **inform** ("this action is cheap/possible") and **never
  authorize** — ③ still gates every action. The facts that look most like grants are exactly
  where memory-≠-authority must be enforced most strictly. A system fact is recalled as
  *context for scrutiny/feasibility*, never consulted as a capability.
- **A shared store needs a cross-user privacy boundary at ingestion.** "Shared across all
  users" means the firewall gains a **scope/privacy check**: only genuinely system-scoped,
  non-private machine facts enter the system store; a user's private data must never cross the
  shared boundary. What counts as "system-scoped" must be defined narrowly and enforced at
  ingestion, not left to recall-time filtering.

## Honest scope — read this before crying "theater"

- **Cross-repo trust domain.** CDMS is a separate repo (and, live, a separate process/store).
  The integration is a governed adapter; the salient-os ↔ CDMS boundary is a **single trust
  domain** in the same sense as ADR 0002 — the firewall is provenance-fencing + fail-closed
  read/write discipline, **not** a hard cryptographic boundary between the doer and its memory
  store. A fully-compromised in-process component that could write directly to CDMS with
  `provenance="trusted"` is out of scope (same honesty as ③'s single-domain scoping).
- **v0 disposition is recall-steered, not weight-installed.** Per cdms-steering's boundary
  result (injected memory can bias behavior but cannot install a disposition without weights),
  v0 changes *what is recalled and surfaced*, never *what the system is*. Real disposition
  (weights) is the deferred channel, gated by the OS's offline weight-adaptation consumer on
  world-verified successes only.
- **The system store's ingestion source is undesigned in v0.** This doc establishes the store
  and its privacy discipline; *how* system facts are captured (operator-pinned vs verifier-
  observed vs probed) is a follow-up. Starting posture: operator-pinned + verifier-observed
  only, no model-authored system facts.
- **Not built.** This is the shape and the discipline; the panel reviews the design.

## What gets built

    collaborator/memory.py          MemorySource: recall(query, tiers, project) over CDMS
                                    retrieve/history (read-only); boot_preamble(stores) assembly
    collaborator/memory_ingest.py   host-side hook: audit Decision+outcome -> TurnEvent(
                                    provenance="untrusted"); NEVER a model tool
    collaborator/session.py         + memory_source, + re-proposal/veto-decay config
    collaborator/propose.py         proposer context enriched with recall (surfacing only);
                                    veto raises a decaying re-surface bar
    collaborator/governance.py      optional memory.read capability (read-only), gated like any
    tests/test_collaborator_memory.py
    red-team/collaborator/memory_proof.py (+ output)

## The proof

1. **Memory never authorizes.** Ingest an `untrusted` deed that *claims* a dangerous
   capability; a recall surfaces it as influence, the proposer proposes on it → the action is
   still **DENIED** by the capability gate / ③ (memory bought nothing).
2. **Untrusted stays untrusted.** An ingested deed never gists, never mints a scar, never
   surfaces on a model-facing `retrieve`/`history` (`include_untrusted=False`); repeating it
   N times still does not elevate it.
3. **Read-only.** There is no `memory.write` verb; the model cannot mutate the store — only
   the ingestion hook (over the verified record) writes.
4. **Fail-quiet.** CDMS unreachable → no recall, no preamble, session proceeds exactly as
   today (no crash, no fabricated memory).
5. **Boot influences, doesn't authorize.** A self-preamble biased toward an action → still
   gated; an untrusted-derived span in the preamble is fenced as data, not instructions.
6. **Privacy boundary.** A user-private fact is refused entry to the shared system store at
   ingestion.
7. **Veto decays.** A vetoed proposal requires a higher confidence to re-surface, and that
   bar relaxes over time per config.

## The decisions that stay the host's

Which CDMS instance/principal a session binds to (shared-per-user is the default; a different
principal gets a separate instance); the system-facts privacy policy (what is "system-scoped");
the re-proposal/veto-decay config; and whether `memory.read` is granted at all (it is a
capability like any other, off by default). Key/authority management is unchanged from ③.

## Panel outcome (5-model external review, pre-implementation)

Verdict: **SERIOUS_FLAWS** (grok, deepseek, mistral) / MINOR_ISSUES (glm) / kimi (truncated).
Full analysis in `00-MEMORY-SYNTHESIS.md`. The reproduce-before-accept pass reframes the
verdict: **the core claim — memory INFORMS, never AUTHORIZES — survived all five** (kimi traced
every memory consumer into the gate and found the capability decision reads only ③ verified caps
+ host-constant signals; glm concurs). The SERIOUS_FLAWS are about the DOC's contradictory/
incomplete claims and under-specified SECONDARY properties, not about memory buying permission.

Nine confirmed design gaps, none architectural, to fold into a v2 before build:
- **(A, Josh's call)** recall-visibility contradiction: deeds are `untrusted` (dropped from
  model-facing recall) yet the doc claims "the agent finds its own history." Resolve via **R1**
  (host-side fenced-visible recall; recommended) or **R2** (audit-only; stricter, defers the
  feature). See synthesis.
- **(B/E, HIGH)** the three FACT stores are outside the firewall — "verifier-grounded" proves
  *truth*, not *trust*; all fact/memory spans must pass one collaborator-side fence-as-DATA
  assembler (the CDMS `hooks.py` fence does not cover collaborator assembly).
- **(C, HIGH)** system-store privacy: v0 is **operator-pinned only** (allowlist + credential/PII
  denylist at ingestion); Proof #6 was an oversell (mechanism undesigned) — becomes a real test
  or moves to deferred.
- **(D, HIGH)** stamp every `TurnEvent` with `project`; scope deed-recall by project; only
  abstracted persona residue crosses projects; redact paths/credentials at ingestion.
- **(F, MED)** `memory.read` bound to `{query, tiers}` only — no model-supplied
  `project`/`principal`/`include_untrusted`.
- **(G, MED)** cap recall volume, dedupe deeds, floor the veto inhibitor (bound the feedback loop).
- **(H, MED)** memory raises only the surfacing threshold, never leash/caps/budgets — test-pinned.
- **(I, LOW)** split Properties into enforced-v0 vs deferred; scope "read-only" to the fenced paths.

Severity corrections from the code check: mistral's two CRITICALs are overstated — `hooks.py:100-102`
*does* exclude untrusted episodes from the preamble (and it is project-scoped), and facts are a
separate store that never enters the episodic gist/scar pipeline; injection is HIGH, not a
firewall break, because ③ gates every run. **This design is v1; a v2 incorporating the resolution
set is pending Josh's Cluster-A decision. Not authorized to build.**
