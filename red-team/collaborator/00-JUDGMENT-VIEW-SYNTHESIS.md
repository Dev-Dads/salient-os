# ② The judgment view — synthesis

*Step 2 of the Collaborator: its own surface. Not a chat box — a **view** of what it is
attending to, running, and proposing, with the leashes, the proactivity dial, and a
**pause** as controls you can put a hand on. The proof of Step 2 is that you steer a
running job through the view — tighten its leash, pause it, veto its next step — without
typing a sentence.*

## What shipped

- `collaborator/view.py`:
  - `JudgmentLedger` — the record the view reads: the stream of governed decisions and
    surfaced proposals. The host records into it as it drives (run_turn returns decisions,
    propose returns proposals), so `govern_action`/`propose` stay uncoupled from display.
  - `JudgmentView(session, ledger)` — `snapshot()` (attending / running / proposing +
    leashes + proactivity + capabilities + counts) and `render_html()` (a self-contained,
    theme-aware page; no external assets, no JS).
  - **Controls — host authority, never model-set:** `set_leash` (tighten/loosen a tool's
    leash), `set_proactivity`, `pause` / `resume`, `veto` / `approve` a proposal.
- One seam change: a `PAUSED` status + a **pause gate** in `govern_action` — while paused,
  the agent's action stream is held (nothing runs) until the host resumes; `run_turn`
  halts a running turn on a paused action. The gate is **fail-safe**: it can only hold,
  never run.

## P-01 stays intact

Every control either **restricts** (pause, tighten) or expresses the **host's own
setting** (leash, proactivity) — none grants the model a capability. Proven: on a session
without `shell.exec`, pausing holds a `run_command` and resuming still **DENIES** it; no
sequence of controls ever added the capability. The controls change *scrutiny and whether
the agent may proceed*, never *what a capability permits*.

## Why no external panel (spend matched to risk)

Consistent with the standing rule and the ④ precedent: ② adds a **view** plus controls
that are restrictive or host-own-setting, and one **fail-safe** seam gate (pause can only
hold). It introduces no new authority-granting surface — unlike ①'s origination path,
which did earn a panel. Rigorous tests stand in for it: `tests/test_collaborator_view.py`
(10 tests) pins the pause gate, the leash tighten, the invalid-value rejection, the
veto/approve path, the snapshot, the self-contained render, and the P-01 no-grant property.
Full suite **274 green**.

## Proven — steering a job through the view (`view_proof.py`), 7/7

Driven entirely by **control calls, not model prompts**:

1. a normal act-then-report write **runs**;
2. **[tighten write_file leash]** → the next write is **HELD** (steered to hold);
3. **[pause]** → the next action is **PAUSED**, and a running multi-step turn **halts**
   (`stopped="paused"`);
4. **[resume]**;
5. **[veto a proposal]** → it **never runs** (no artifact);
6. **[approve a proposal]** → it **runs**;
7. **P-01:** controls grant nothing — paused, then still **denied** on an ungranted tool.

The live view renders to `judgment_view.html` (self-contained, theme-aware) — the surface
you'd put your hand on. Output: `view_proof_output.txt`.

## Honest scope

- The collaborator is headless, so `render_html()` is a **snapshot** of the view and the
  controls are **functions** a real UI would wire to buttons; the steering is real (it
  changes what the seam does), the pixels are a faithful render of the state.
- "Steer a *running* job" is shown two ways: discrete governed actions with a control
  flipped between them, and a `run_turn` loop that **halts** when paused. True concurrent
  mid-flight interruption of an async job is the same mechanism against a real event loop.
- Conversation stays; the view is *not the whole interface*, it is the surface that was
  missing. A larger toolset and richer "attending" signals are later.
