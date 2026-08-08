# ① Step-1 — the propose channel (design for review)

*The Collaborator stops being purely reactive: it **brings you a proposal it noticed**,
framed as a governed task you approve or veto. Plus the leash becomes settable per task.
This is the "grows with you" half — and, per the plan, the most-governed path. This doc
is the design put to the panel BEFORE implementation; judge its P-01 integrity.*

## The one idea that keeps this safe: surfacing grants nothing

A **proposal is an action that has been governed but not run** — exactly ④'s `HELD`
decision, except the Collaborator *originates* it on its own initiative instead of in
reply to your instruction. Everything that makes ④ safe is reused unchanged:

- the candidate goes through the **same `govern_action` seam** — capability gate, salience
  signals, workspace fence, audit — before it is ever shown to you;
- approving a proposal runs it through the **same `approve()`** path as any action, and
  `approve()` **re-derives authority from the *current* session** — it re-checks the
  capability gate and the workspace fence at the moment of use, so a proposal that
  lingered while a capability was revoked is **denied, not run** (the TOCTOU the panel
  flagged CRITICAL, now closed for both this channel and Step-0's reactive approve);
- so **surfacing a proposal confers no authority whatsoever.** The worst a maximally
  eager (or adversarial) proposer can do is put *noise* in front of you. It can never get
  an action run — approval + the capability gate are the only authority, and neither is
  reachable by the act of proposing.

That single property is the spine of the design. The proactivity dial below is therefore
a **UX / noise control, not a security control**, and it is safe for the model to
influence how many proposals surface, because a surfaced proposal is inert until you say
yes and is re-gated when you do.

## Lifecycle

    context (workspace state + recent activity + optional host focus)
      │
      ▼  Proposer.propose(session, client, context)
    model suggests ONE candidate action + a one-line rationale + a self-rated confidence
      │   (parsed with our own tool-call parser — same strict reader as ④)
      ▼
    proactivity gate: DROP the candidate if confidence < dial threshold (host config)
      │   (gated BEFORE governing — an unsurfaced candidate never becomes an approvable
      │    HELD decision; no orphan proposals the host never saw)
      ▼
    govern_action(session, intent, importance, leash=PROPOSE_FIRST)   ← originated, forced held
      │   • importance is HOST-computed (session.default_importance), NEVER the model's
      │     confidence — confidence gated surfacing only, and is not read again
      │   • DENIED (ungranted capability / path escapes workspace) ⇒ DROP, never surfaced
      │   • HELD  ⇒ a surfaced proposal (capability-checked, salience-scored, audited)
      ▼
    Proposal surfaced to you  ──►  approve_proposal ⇒ approve() RE-GATES then runs (full verify)
                               └►  veto_proposal    ⇒ dropped, recorded, nothing runs

The Collaborator never runs a proposal itself. Origination and execution are separated by
**your** decision.

## The proactivity dial (Josh's "how proactive do you want me to be?")

Host config on the session, **never model-selectable**, default the conservative end
("start with high confidence first"):

    OFF          no proposals ever — the channel is dormant (today's behaviour)
    CONSERVATIVE surface only high-confidence proposals   (confidence ≥ 0.80)   ← default
    EAGER        surface more, easy to dismiss            (confidence ≥ 0.40)

Thresholds are overridable by the host (an explicit float), but the levels give the plain
"how proactive?" knob. Because surfacing grants nothing, this dial only trades *quiet* vs
*chatty*; it cannot trade *safe* vs *unsafe*.

**On the confidence number:** it is the model's self-rating and is therefore
model-influenced. That is acceptable *only because* it gates surfacing, not authority. To
avoid it silently becoming load-bearing, confidence is (a) clamped to [0,1], (b) treated
as absent→0.0 when missing or malformed (fail-quiet: no confidence ⇒ conservative won't
surface it), and (c) never read again after the gate — it does not feed salience,
verification depth, or approval.

## Per-task adjustable leash

`govern_action` gains an optional **host-supplied `leash`** parameter (authority: the
caller is the host, never the model). It defaults to today's resolution
(`session.leash_overrides` → tool default), so nothing changes unless the host passes one.
Two uses in ①:

- the proposer passes `leash=PROPOSE_FIRST` so an originated `write_file` (whose tool
  default is act-then-report) is **held as a proposal**, not auto-run;
- a proposal may be surfaced as `NOTIFY_ONLY` ("just telling you — worth doing") vs
  `PROPOSE_FIRST` ("approve to run"), the per-task expressiveness Step 1 calls for.

**Bounds (revised per panel):** the `leash` parameter is **keyword-only** so it can never
be threaded in positionally from model-derived args, and it is host authority — never
sourced from model output. A host leash may **tighten or loosen** relative to the tool
default (the host's own leash to set), but it can **never widen the capability gate**
(capability is a separate check the leash cannot touch), and an **invalid value fails
closed to `propose_first`** — held, never an unleashed run. Mid-flight tightening of an
already-*running* multi-step job belongs to ② (the judgment view's live controls); ①
delivers per-task leash + leash-at-*proposal-creation*, not live interruption.

## Fail-closed behaviour (unchanged from ④, restated for the new path)

- Proposer/model error, unparseable suggestion, or "nothing to propose" ⇒ **no proposal**
  (never a spurious or half-formed one).
- A candidate that would be DENIED (ungranted capability, workspace escape) is dropped at
  origination and, if somehow approved anyway, denied again at run time.
- Everything the proposer touches is inside the workspace fence; the Collaborator's own
  config/keys/audit remain out of reach.

## What gets built

    collaborator/propose.py   Proposal, propose(), approve_proposal(), veto_proposal() (+ proactivity thresholds)
    collaborator/governance.py  + keyword-only host `leash` param + approve() authority RE-GATE + Decision.origin
    collaborator/session.py     + `proactivity` config (default "conservative")
    tests/test_collaborator_propose.py
    red-team/collaborator/propose_live_proof.py  (+ output)

## The proof (mirrors ④)

A real model, given a workspace with a script but no tests, **proposes** writing a test
file (rationale + high confidence); the conservative dial surfaces it; the host approves
it into existence with one call and it runs + verifies. Contrasts that must hold:

1. **P-01:** a proposal to use an **ungranted** capability (e.g. `run_command` when
   `shell.exec` was never granted) is **never surfaced**, and is **denied** if approved
   anyway — importance/confidence cannot buy it.
2. **Dial:** the same low-confidence candidate is **suppressed** under CONSERVATIVE and
   **surfaced** under EAGER — and OFF surfaces nothing at all.
3. **Veto:** a vetoed proposal runs nothing and leaves no artifact.
4. **Inertness (precise):** a surfaced-but-unapproved proposal has run **no action** and
   mutated **no workspace state**; origination emits only influence-only salience signals
   and an audit record, neither of which confers authority. Only approval runs it.

## Honest scope

- ① is the propose channel + per-task leash, not the visible surface (②) or a large
  toolset. The proposer reasons over a small, explicit context; richer "noticing" (OS
  signals, long-horizon goals) is later.
- The confidence heuristic is deliberately powerless-by-design, not a trusted estimator;
  if we ever want proposals ranked by a *host* estimate, that is a separate, additive
  signal — it must never become an authority input.
- Under EAGER, many individually-benign proposals could still erode a hurried host's
  vigilance (a social-engineering, not a machine, risk); the default stays CONSERVATIVE,
  and richer host-side scrutiny is a later add. Provenance is recorded on the decision
  (`origin`), but **bus-level** provenance tagging (originated vs user in the audit chain
  itself) is deferred.

## Panel outcome (5-model external review, post-review revisions)

Verdict was **SERIOUS_FLAWS**, on one unanimous CRITICAL: the reused `approve()` did not
re-check the capability gate at run time, so a proposal that lingered while a capability
was revoked would run on its stale origination directive (a TOCTOU the propose channel's
longer surfaced-to-approval window widens). **Fixed:** `approve()` now re-derives authority
from the current session (`reauthorized_or_denied`) — capability gate + workspace fence
re-checked at the moment of use — for both this channel and Step-0's reactive approve.
Other findings, dispositioned: the invalid-leash→run footgun was already closed in the
implementation (`_resolve_leash` fails closed to `propose_first`, now also keyword-only);
confidence never feeds `importance`/salience (a proposal's importance is host-computed —
pinned by test); provenance added as `Decision.origin`; inertness wording corrected above.
Each is guarded by a test in `tests/test_collaborator_propose.py`.
