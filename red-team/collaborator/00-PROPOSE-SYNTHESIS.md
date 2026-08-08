# ① The propose channel — synthesis

*Step 1 of the Collaborator: it stops being purely reactive and **brings you a proposal it
noticed**, framed as a governed task you approve or veto — plus the leash becomes settable
per task. Designed → 5-model external panel → fixed → proven live. The panel earned its
keep: it found a real CRITICAL that lived in the reused Step-0 code, not just the new
channel.*

## What shipped

- `collaborator/propose.py` — `Proposal`, `propose()`, `approve_proposal()`,
  `veto_proposal()`. A proposal is an action **governed but not run** (a `HELD` decision),
  except the Collaborator originates it from workspace context. It reuses the whole seam:
  the candidate passes the capability gate + salience + workspace fence + audit before you
  see it, and approving runs it through the same `approve()` path.
- The **proactivity dial** (`Session.proactivity`, default `"conservative"`, Josh's "how
  proactive do you want me to be?" as a host config, never model-selectable):
  `off` / `conservative` (confidence ≥ 0.80) / `eager` (≥ 0.40). Because surfacing grants
  no authority, the dial only trades quiet-vs-chatty.
- The **per-task leash**: `govern_action` gains a keyword-only host `leash` param, and the
  new `Decision.origin` records provenance (`"collaborator"` vs `"direct"`).

## The safety spine

**Surfacing a proposal grants no authority.** The dial and the model's self-rated
confidence gate only whether a proposal is *shown*; approval + the capability gate are the
only things that run anything. So an eager or adversarial proposer can at most add noise —
never get an action run. Confidence is clamped, absent→0.0, gates surfacing once, and never
feeds importance/salience/verification (a proposal's importance is host-computed).

## The panel (5 models, 4 substantive; reproduce-before-accept)

**Verdict: SERIOUS_FLAWS — unanimous on one real CRITICAL**, which I confirmed against the
code and fixed:

- **CRITICAL — approval did not re-gate the capability (TOCTOU).** The reused Step-0
  `approve()` ran a held action on its *origination* directive and never re-checked
  `grants_capability` against the current session. A proposal — designed to linger — could
  be surfaced while a capability was granted, the capability revoked, and then approved,
  running on the stale grant. Real, and it lived in ④'s `approve()` too. **Fixed:**
  `approve()` now calls `reauthorized_or_denied` — re-derives authority from the current
  session (capability gate + workspace fence at the moment of use) and DENIES if it no
  longer holds. Strengthens both the propose channel and Step-0's reactive approve.

Everything else, dispositioned by reading the actual implementation (several panel
"CRITICAL/HIGH" items were already closed in code — the panel reviewed the design doc):

- **Invalid leash → unleashed run** (mistral rated CRITICAL): already closed — `_resolve_leash`
  fails **closed to `propose_first`** on any invalid value; now also **keyword-only** so it
  can't be threaded positionally from model args.
- **Confidence → importance → verification depth** (mistral/glm HIGH/MEDIUM): already safe —
  `propose()` passes `session.default_importance`, never confidence. Pinned with a test
  asserting two different-confidence proposals get identical verification depth.
- **Provenance** (grok/deepseek/glm MEDIUM): added `Decision.origin`; bus-level tagging
  deferred (noted).
- **Inertness overclaim** (LOW): origination emits influence-only salience signals + an
  audit record; corrected the wording to "no action run, no workspace mutation."
- **Orphan below-threshold HELD** (glm LOW): doesn't apply — the code gates confidence
  *before* governing, so an unsurfaced candidate never becomes an approvable HELD.

All fixes guarded by `tests/test_collaborator_propose.py` (20 tests). Full suite **264 green**.

## Proven live

`red-team/collaborator/propose_live_proof.py` against `mistral-nemo:12b` (local, real model)
— output in `propose_live_proof_output.txt`, **8/8**:

- **Part A (live):** given a workspace with `calc.py` and no tests, the model **proposed**
  writing `test_calc.py` (confidence 0.95); the **conservative** dial surfaced it;
  `origin="collaborator"`, inert (nothing on disk); the host **approved it into existence**
  and it ran + verified (98 bytes written). The full propose → approve → run cycle, live.
- **Part B (P-01):** a `run_command` proposal without `shell.exec` is **never surfaced**;
  and a `write_file` proposal surfaced then had its capability revoked is **DENIED at
  approval** (the TOCTOU re-gate, live).
- **Part C (dial):** confidence 0.5 → off=0, conservative=0, eager=1 (quiet vs chatty).
- **Part D (veto):** a vetoed proposal runs nothing, even if approved afterwards.

One implementation improvement fell out of the live run: the small model first emitted an
off-schema proposal (`filename` instead of `path`, no `content`), which the workspace fence
correctly DROPPED — a live demonstration of fail-closed, and the prompt now specifies each
tool's exact argument schema so a real model proposes runnable actions. (Part A retries a
flaky small model a few times; the governance properties don't depend on it, and a competent
model surfaces first try.)

## Honest scope

- ① is the propose channel + per-task leash — not the visible surface (②, next) or a large
  toolset. The proposer reasons over a small explicit context; richer "noticing" is later.
- `approve_proposal` runs the held action; leash is expressed **at proposal creation**
  (propose-first vs notify-only), not adjusted mid-run — live interruption of a running job
  is ②'s job.
- Under EAGER, many benign proposals could still tax a hurried host's attention (a
  social-engineering, not a machine, risk); the default stays CONSERVATIVE.
