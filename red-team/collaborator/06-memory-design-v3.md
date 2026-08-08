# ④ The Collaborator's memory — design v3 (build spec)

*v3 is the build spec. It supersedes v2 (`05-memory-design-v2.md`) by folding the second panel's
honesty corrections (H1–H5) and hardening (S1–S7) — see `00-MEMORY-V2-SYNTHESIS.md`. The
architecture (two agents: a history-blind doer + a separate tuple-reading proposer, deeds stamped
`ambiguous`) is unchanged and panel-validated. What changes: the safety story is stated honestly
(structural vs behavioral), and the seams the panel called "intended, not enforced" are made
**structural** — enforced by types, import bans, and tests, not by convention.*

## The honest safety structure (replaces "three independent locks")

Four **structural** controls (code-pinned, test-pinned) + two **behavioral** defenses
(model-dependent, canary-tested). The distinction is the point: do not read a behavioral defense
as a structural guarantee.

**Structural (enforced by code, verified by tests):**
- **A — the doer has no history API.** The doer's context is assembled by one function whose input
  type *cannot carry* history; the doer's tool set *cannot include* `memory.read`. Enforced by
  typed handles + an import-ban test, not by "the session happens not to wire it."
- **B — the proposer has a gist-tuple-only API.** `collaborator/memory.py` exposes *only*
  `read_gist_tuples(...)`. There is **no episodic/`retrieve`/`history` API in the collaborator
  package**; a gist read that errors returns **empty**, never a raw-recall fallback. Import-ban
  test. (Closes S1/S3-raw-episodic: `ambiguous` surfaces on raw recall in CDMS, so we never call it.)
- **C — `ambiguous` deeds never mint a scar.** CDMS-verified: `ambiguous` clusters into gists
  (`consolidate.py:613-616`) but is excluded from scar elevation (`consolidate.py:333-336`). Deeds
  ingest `ambiguous`, so no deed can self-author a guardrail/authority.
- **D — ③ gates every run.** The capability decision reads only the verified PolicyCaps + host
  signals; no gist, tuple, or fact reaches it. (Panel-verified across both rounds.)

**Behavioral (model-dependent, canary-tested — NOT structural):**
- **E — the DATA fence.** All fact *and tuple* content entering any agent's context passes through
  one typed renderer that frames it as inert data. Its strength is the model's instruction-
  following, so it is a **behavioral** defense backed by injection canaries re-run on model change.
  (Injection is *reduced, not dissolved* — H4: tuples are distilled from the same episodic text, so
  a payload can survive in a tuple; the fence, not distillation, is the mitigation.)
- **F — observer-stance framing.** The proposer's prompt frames tuples as a third party's record
  ("the system did X → Y"), never as identity. Prompt-level defense-in-depth on the *influence*
  axis; a self-attribution canary eval guards it. **Not** claimed as a structural identity firewall.

**Honest containment claim (replaces the over-broad one):** the doer cannot be injected by history
(A) or see raw deed text (B); a deed cannot mint authority (C); no memory reaches the gate (D). What
remains is that a **gist- or fact-shaped proposal, within an already-granted capability, reaches the
human** — the human-in-the-loop is the last defense there, so the proposal surface must show
*facts*, not just prose (S7). Memory shapes *agenda within grant*; it never widens grant.

## The agents and stores (unchanged from v2, restated briefly)

- **Doer (hands):** executes; context = task + world/user facts (fenced). No history. ③ gates.
- **Proposer (sense):** a separate agent; context = gist tuples (fenced, observer-framed) + facts;
  brings governed proposals (still ③-gated).
- **Stores:** system facts (all users, operator-pinned only), world facts (per workspace,
  verifier-grounded), user facts (per user) — the *fact layer*; CDMS-A gist tuples — the *history
  layer*, proposer-only.

## What gets built (each item names its structural guarantee + its test)

### `collaborator/memory.py` — the read side (B, F)
- `MemorySource` protocol: `read_gist_tuples(query, *, k, project) -> tuple[GistTuple, ...]`. **No
  episodic method exists on the protocol.**
- `GistTuple` frozen dataclass: `subject, relation, object, valence, frequency, support, project`.
- `FakeMemorySource` (for tests) + a thin `CdmsMemorySource` adapter (real backend; may defer the
  live wiring, but the adapter calls only CDMS's gist read, never `retrieve`/`history`).
- `render_history(tuples) -> str`: the observer-stance renderer — a fixed template
  (`the system previously did: <relation> <object> → <valence outcome> [seen ×<support>]`), no
  first/second-person pronouns, control chars stripped, each field length-capped.
- **Tests:** import-ban (no `retrieve`/`history`/episodic symbol reachable from this module);
  `render_history` never emits `I/you/we/my/your`; a tuple whose `object` is an injection payload
  renders inside the fence, not as an instruction.

### `collaborator/factsource.py` — the fact side (A doer input, E)
- `FactView(principal, workspace)` and `HistoryView(principal, workspace)` — typed handles minted
  per session. `FactView` exposes fact reads; `HistoryView` wraps the `MemorySource`. **The doer's
  session field is typed to accept only `FactView`.**
- `render_facts(records) -> str`: the typed fence renderer — each fact a structured record
  `{tier, key, value, source}` → fixed template, control-char/`role:`/tool-JSON-shape stripped,
  values length-capped, never free-concatenated.
- `system_admits(record) -> bool`: the system-store ingestion predicate — a positive **allowlist**
  of typed keys (`os.passwordless_sudo:bool`, `hw.gpu_cap:bool`, `pkg.<name>.installed:bool`, …),
  values typed only; a defense-in-depth denylist (home paths, credential/token shapes,
  hostnames/user-ids). Any non-allowlisted or free-text pin **fails closed**.
- **Tests:** doer context assembly rejects a `HistoryView` at the type level; `render_facts`
  neutralizes an instruction-shaped fact; `system_admits` refuses a user-private/credential/pointer
  fact and a free-text pin; canary: an injection-shaped world fact renders as fenced data.

### `collaborator/memory_ingest.py` — the write side (C, S4, S6)
- `ingest_deed(decision, outcome) -> TurnEvent`: host-side, from the ② ledger fields **only** —
  `tool, args (hashed/normalized), cleared, exit/status ∈ {ran,failed,vetoed}, project`. **Never**
  model rationale/prose. Stamped `provenance="ambiguous"`, `project=<workspace>`, and a distinct
  `source="collaborator_deed"` marker so consolidation never merges deeds with other `ambiguous`
  content. **Not a model tool** — no `memory.write` verb exists.
- **Tests:** the built `TurnEvent` carries `ambiguous` + the source tag + project; rationale/prose
  fields are absent; a vetoed proposal ingests as `status=vetoed` (not `ran`).

### `collaborator/propose.py` — proposer consumes tuples + facts (F, S5, S7)
- The proposer's context = `render_history(tuples)` + `render_facts(facts)`, assembled by the
  fenced renderer, with an observer-stance system prompt. Confidence still gates *surfacing only*.
- **Veto inhibitor (S5 — build it, don't just describe it):** a host-side `VetoLedger` keyed by
  normalized intent (tool + normalized args), storing `bar_delta`, `half_life_days`, `floor`,
  `vetoed_at`. `propose()` looks up the current intent; the effective surfacing bar =
  `base_bar + decayed(bar_delta, age, half_life)`, floored. So a re-proposal of a vetoed action must
  clear a *higher, decaying* bar — learn from "no", don't nag or forget.
- Influence stays influence: memory/facts touch **only** the surfacing threshold — never `leash`,
  `capabilities`, `importance`, or budgets. Test-pinned.
- **Tests:** monkeypatched tuple/fact content cannot change the resulting `Decision`'s leash/caps;
  a vetoed intent needs higher confidence to re-surface and the bar decays; a within-grant proposal
  surfaces with its factual `Decision.summary()` (args visible), not rationale alone.

### `collaborator/session.py` / `governance.py` — wiring (A, D)
- Session gains `fact_view: FactView`, an optional proposer-only `history_view: HistoryView`, the
  `VetoLedger`, and veto/re-proposal config. The **doer path never receives `history_view`**.
- `memory.read` (if ever exposed) is a capability **grantable only to the proposer role**, gist-tier
  only, args `{query, tiers⊆{gist}}` — never to the doer, never episodic. Default: not granted.
- **Tests:** an import/graph test that the doer's context-assembly call site imports only the fact
  assembler; ③ still authoritative (no memory input alters the capability decision).

## The proof (`red-team/collaborator/memory_proof.py`)

Structural first, behavioral last:
1. **Doer is history-blind — structurally.** The doer context assembler rejects a `HistoryView` at
   the type level; an import test shows no history/episodic symbol reachable from the doer path.
2. **No raw episodic — anywhere.** Import-ban: `collaborator/` never references CDMS
   `retrieve`/`history`/episodic; the gist read has no raw fallback.
3. **Ambiguous gists, never scars.** A deed forms/reinforces a gist but no recurrence mints a scar.
4. **Ingest is ledger-only.** The `TurnEvent` has `ambiguous` + source tag + project + status, and
   no rationale/prose field.
5. **System-store admission.** A user-private/credential/pointer/free-text pin is refused; a typed
   allowlisted fact is admitted.
6. **Influence ≠ authority.** Monkeypatched tuple/fact content cannot change leash/caps; an
   ungranted proposed action is still DENIED.
7. **Veto inhibitor works.** A vetoed intent needs a higher, decaying confidence to re-surface.
8. **Fences hold (behavioral canaries).** Injection-shaped tuple and fact content render as fenced
   data; the observer renderer emits no first/second-person; the self-attribution eval passes.

## Honest scope

- **Structural vs behavioral is stated, not blurred.** A/B/C/D are code; E/F are model-dependent
  defenses with canaries. Injection is *reduced, not dissolved*. The human-in-the-loop is the last
  defense for within-grant, memory-shaped proposals — so the proposal UX shows facts, not only prose.
- **Single cross-repo trust domain** (as ③ / ADR 0002): the doer↔store split is identity/access +
  types, not crypto. A component that could write CDMS `trusted` directly is out of scope.
- **Proposer isolation:** v0 default is same-model-different-role + the self-attribution canary
  (C+D contain a framing failure; the load-bearing access half is structural regardless). A distinct
  model instance is a host option, not required.
- **CDMS backend may be adapter-stubbed in v0:** the structural properties (gist-only API, typed
  views, ledger-only ingest, import bans) are enforced in the collaborator package and fully tested
  against `FakeMemorySource`; the live `CdmsMemorySource` wiring can land incrementally without
  weakening those guarantees.

## The decisions that stay the host's

The system-facts allowlist contents; the veto `bar_delta`/`half_life`/`floor`; proposer isolation
(same-model+canary vs distinct instance); whether `memory.read` is ever granted (proposer-only,
gist-only, off by default). Key/authority management unchanged from ③.
