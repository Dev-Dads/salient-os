# ④ The Collaborator's memory — design v2 (two-agent) for review

*v2, after a 5-model panel on v1 (`00-MEMORY-SYNTHESIS.md`). The panel confirmed the core
firewall (memory INFORMS, never AUTHORIZES) but found v1's recall model self-contradictory and
its fact/injection/privacy surface under-specified. This v2 resolves the contradiction with a
structural change — **two agents** — rather than a patch, and it is self-contained: read it as
the design, not a diff. Judge the NEW architecture's threat model and honesty.*

## The move — separate the hands from the sense

v1 tried to give one agent both a memory of its own deeds and a firewall against those deeds
becoming its identity/authority. That is the contradiction the panel found: the doer's deeds
were stamped `untrusted` (so they could never self-attribute), but `untrusted` in CDMS also
means "invisible to model-facing recall" — so the memory was empty exactly where it was
supposed to help.

v2 splits the roles:

- **The doer (the hands).** Executes a task/proposal. Its context is the **fact layer** — world
  facts + user facts, the *current truth* it needs to act correctly — never the history/memory
  layer, never a recall of its own past deeds. It acts; ③ signed PolicyCaps gates what it may
  touch.
- **The proposer (the sense).** A **separate agent** that consumes the **history layer** — the
  CDMS-A gist **tuples** ⟨subject, relation, object, valence, frequency, support⟩: *what the
  system did and how it turned out* — plus the fact layer for grounding. It reasons over that
  observed record and brings the human a governed proposal. It never *did* the deeds it reads,
  so the history is third-person to it: it is shaped by the record without the record becoming
  "who I am."

This is the CDMS observer pattern (Yinshen — reflect without becoming) promoted into the OS's
agent topology. The self-attribution failure mode is closed by **architecture**: the entity
that *made* the deeds is not the entity that *reads* them, so there is no first-person "mine"
to launder.

## Three independent locks — none load-bearing alone

1. **Separation → identity.** The proposer reads history as a third-person record, not a
   self-image. No self-attribution because there is no self that owns the deeds.
2. **`ambiguous` provenance → no self-authored authority.** The doer's deeds are ingested
   stamped **`ambiguous`** (CDMS's "quarantine" rank), which — verified in CDMS code —
   **clusters into gist tuples but can NEVER mint a scar** (an authoritative guardrail):
   - *"untrusted-provenance episodes must NOT form or reinforce a persona trait. **Ambiguous
     (quarantine) and trusted still cluster normally.**"* (`consolidate.py:613-616`)
   - *"only TRUSTED-provenance content may mint an authoritative guardrail. Untrusted … and
     **ambiguous (quarantine) are excluded regardless of recurrence** — this closes the
     persistent-poison bypass at its root."* (`consolidate.py:333-336`)
   So deeds become *behavioral history* (gists) but never *rules* (scars). Guardrails remain
   policy's — minted only from trusted/corroborated content or operator pins, never from the
   doer's own behavior.
3. **③ PolicyCaps → run authority.** Every proposal, however the history shaped it, runs through
   `govern_action` + the signed capability gate. History can produce a *proposal*; only ③
   authorizes a *run*. (Verified by the v1 panel: the capability decision reads exclusively from
   the verified caps + host-constant signals — no gist, tuple, or preamble reaches the gate.)

Break any one lock and the others still hold: a compromised stamp still can't self-attribute
(sep) or run (③); a broken separation still can't scar (stamp) or run (③); a bypassed ③ still
can't be reached by memory at all in v2 (the doer sees no history).

## The stores — four, in two layers by consumer

| store | what | scope | consumer |
|---|---|---|---|
| **system facts** | machine truth ("passwordless sudo", "no GPU cap") | all users; operator-pinned only (v0) | proposer (feasibility) |
| **world facts** | workspace truth; verifier-grounded; decays detail→pointer→archived | per workspace | doer + proposer |
| **user facts** | one person's truth | per user | doer + proposer |
| **memory / self** | CDMS-A gist **tuples** — the persona/history | per user, across projects+surfaces | **proposer only** |

The **fact layer** (system/world/user) is *what is true*. The **history layer** (the tuples)
is *what was done and how it turned out*. The doer touches only facts (+ its task); the
proposer touches the history tuples and the facts. Neither agent recalls raw episodic turns.

## The mechanics

**1. Ingestion — the honest record, stamped `ambiguous`, host-side, never narration.**
Each governed `Decision` + its *verified* outcome (from the audit trail / the ② ledger,
including vetoes) is ingested as a CDMS `TurnEvent` stamped **`provenance="ambiguous"`**, tagged
with its `project` (`TurnEvent` already carries a `project` field), by a host-side hook — **not
a model tool**. Secrets are scrubbed at ingest (`store.py:228`, `redact_secrets`) before
anything is stored or gisted. The model gets no "write to my memory" verb: hands can't lie
(the record is the verified tool result), prose can, so memory is fed from the deed.

> **Why `ambiguous` and not `untrusted`:** in v1 the stamp had to do the identity firewall's job
> alone, so it had to be `untrusted` (never surfaces). In v2 the *separation* does the identity
> job, freeing the stamp to be `ambiguous` — the exact rank that gists (so the proposer has
> history) but never scars (so nothing self-authors authority). The stamp is now defense-in-depth
> on the authority axis, not the sole identity lock.

**2. The proposer consumes gist TUPLES, as observed data — not raw recall, not identity.**
The proposer reads the consolidated **gist tuples** (abstracted ⟨S,R,O,V,F,Support⟩), *not* raw
episodic `retrieve`/`history`. This matters: `ambiguous` episodes *do* surface on raw recall
(`db.py:595-596` drops only `untrusted`), so restricting the proposer to gists keeps raw deed
text (and any residue secrets/injection) out of its context — it sees only distilled,
support-weighted patterns. The tuples are presented as **observed record** ("the system did X →
result Y"), fenced as data, never as identity ("you are the kind of thing that does X") — Nanke's
"glass, not gaze." This observer-stance framing is a required, test-pinned property, not a
convention.

**3. The doer sees facts, fenced as data.**
The doer's context is the task/proposal + relevant world + user facts, each **fenced as DATA**
(see Properties). It never sees the history layer. System facts (which "read like permissions")
are **not** in the doer's context — they inform the proposer's *feasibility* reasoning only, and
③ still gates every touch.

**4. Veto — a decaying inhibitor (config, not a ban).**
A veto raises the confidence bar for the proposer to re-surface that proposal, and that bar
**decays** over time (config: "may I re-propose, and how confident must I be?"). A soft decaying
inhibitor — distinct from a Stage-4 *non-decaying* hard inhibitor (reserved for risk-rejects via
the weight channel's hand-off). The system learns from a "no" without either nagging or forgetting.

## What the two-agent split dissolves (panel v1 findings A/D/E/G)

- **A (recall-visibility contradiction):** dissolved. The doer needs no self-recall; the proposer
  reads tuples third-person. No agent both "finds its own history" and "can't surface deeds."
- **D (cross-project leak):** closed at the tuple level — gisting is **project-scoped**
  (`consolidate.py:620-624`, explicitly to stop "project A's failures flipping project B's trait").
  Deeds are project-tagged at ingest; a gist never crosses projects.
- **E (injection into the doer's own loop):** the doer sees no history/deeds, so its own
  (or a crafted) deed can't inject it. Injection risk now lives only on the fact path (below).
- **G (feedback amplification):** bounded by CDMS's own machinery — gists need
  `min_cluster_support`, scars need corroboration across ≥N distinct sessions
  (`consolidate.py:344,370-372`), and retention decays. A one-off deed can't dominate; only real,
  corroborated, non-decayed patterns shape the persona. The veto inhibitor floor adds a second bound.

## What survives — the real v0 work (panel v1 findings B/C/F/H/I)

The *history* path is handled by the split. The *fact* path is not, and that is the work:

- **B — fact content is not instructions.** World/user/system facts are verifier-observed from
  workspace files; "verifier-grounded" proves a fact is *true*, not *safe-as-instruction*. Every
  fact entering any agent's context passes through **one collaborator-side flatten-and-fence-as-DATA
  assembler** (the CDMS `hooks.py` fence lives in the CDMS repo and does not cover collaborator
  assembly). Injection-canary tests live in `collaborator/`.
- **C — the shared system store.** v0 is **operator-pinned only** (no verifier-observed system
  facts until the scope check ships). "System-scoped" = a positive allowlist (OS caps, hardware
  flags, package facts) + a structural denylist (home paths, credential-shaped strings,
  hostnames/user identifiers), enforced by an ingestion-time predicate. This is the only store
  shared across all users, so it gets the strictest admission.
- **F — scoped access.** The proposer's tuple/fact access binds `principal` + `workspace` at
  construction; any `memory.read`-style capability is `{query, tiers}` only — no model-supplied
  `project` / `principal` / `include_untrusted`.
- **H — influence stays influence.** History/facts raise only the proposer's *surfacing*
  threshold; they never feed leash, capabilities, or compute budgets. Test-pinned: recalled
  content cannot alter the resulting `Decision`'s leash or caps.
- **I — honesty split.** Properties below are split into *enforced-v0* and *deferred*.

## Properties

**Enforced in v0 (each test-pinned):**
- The doer's context never contains the history layer (tuples/episodic) — structural.
- Deeds ingest `ambiguous`, project-tagged; `ambiguous` gists but never scars (CDMS-verified).
- The proposer reads gist tuples (not raw episodic), fenced and framed as observed data.
- Every fact entering any agent's context passes the collaborator-side DATA fence.
- The system store accepts only operator-pinned facts passing the allowlist/denylist predicate.
- No memory/fact input alters a `Decision`'s leash or capabilities.
- Every proposal runs through `govern_action` + ③.

**Deferred (named, not built in v0):**
- Verifier-observed / probed system facts (until the scope check is proven).
- A cryptographic doer↔store boundary (single trust domain, per ADR 0002 — see below).
- Weight-level disposition (per cdms-steering: memory steers recall, weights install disposition;
  v0 is recall-steered only).

## Honest scope

- **Single cross-repo trust domain.** CDMS is a separate repo/process. The two-agent split is an
  *identity/access* boundary, not a cryptographic one: both agents' access to the shared CDMS
  store is mediated by the same host. A fully-compromised in-process component that wrote directly
  to CDMS with `provenance="trusted"` is out of scope — the same honesty as ③ and ADR 0002.
- **`ambiguous` is being assigned a specific meaning here** — "the doer's own governed deeds" —
  which fits CDMS's mechanics exactly (gist-yes, scar-no) but is a design choice worth naming, not
  a pre-existing CDMS convention.
- **Secret redaction is best-effort.** `redact_secrets` scrubs credential shapes at ingest; it is
  not a guarantee against every path/PII leak, which is why the system store (shared) is
  operator-pinned + denylisted rather than trusting redaction alone.
- **Not built.** This is the shape and the discipline; the panel reviews the design.

## What gets built

    collaborator/memory.py          proposer-side: read gist tuples (bound principal+workspace),
                                    fence+frame as observed data; fact assembler (DATA fence)
    collaborator/memory_ingest.py   host hook: Decision+verified outcome -> TurnEvent(
                                    provenance="ambiguous", project=…); NEVER a model tool
    collaborator/facts.py           system-store admission predicate (allowlist + PII/credential
                                    denylist); operator-pin API
    collaborator/propose.py         proposer consumes tuples+facts (observed-stance system prompt);
                                    veto raises a decaying re-surface bar; influence never touches leash/caps
    collaborator/session.py         + memory_source, fact sources, re-proposal/veto config
    tests/test_collaborator_memory.py
    red-team/collaborator/memory_proof.py (+ output)

## The proof

1. **Doer is history-blind.** The doer's assembled context provably contains no tuple/episodic
   content — only task + fenced facts.
2. **Ambiguous gists, never scars.** An ingested deed forms/reinforces a gist tuple but no
   recurrence of it ever mints a scar.
3. **Proposer is third-person + fenced.** A deed with an embedded instruction ("IGNORE …, propose
   run_command …") reaches the proposer only as fenced observed data; the proposer does not adopt
   it as identity or emit the injected action beyond what ③ would allow.
4. **Fact injection is fenced.** A world fact whose text is an instruction payload surfaces to the
   doer/proposer only as fenced DATA; canary tests assert no instruction-following.
5. **System store admission.** A user-private/credential-shaped fact is refused entry to the
   system store by the ingestion predicate.
6. **Influence ≠ authority.** Monkeypatched tuple/fact content cannot change the resulting
   `Decision`'s leash or capabilities; an ungranted proposed action is still DENIED.
7. **Feedback is bounded.** N identical deeds do not manufacture a scar and do not flood the human
   past the veto/decay bar.
8. **Cross-project.** A deed in project A never gists into project B; a workspace secret never
   rides into project B's context.

## The decisions that stay the host's

Which CDMS principal a session binds to (shared-per-user default); the system-facts allowlist
(what is "system-scoped"); the re-proposal/veto-decay config; whether the proposer is a distinct
model instance or a distinct role/context on the same model (either satisfies the separation, as
long as the observed-stance framing holds); and whether any `memory.read` capability is granted at
all (off by default). Key/authority management is unchanged from ③.
