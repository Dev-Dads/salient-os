# Collaborator — Step-0 live proof synthesis

The Collaborator (`collaborator/` package) is a governed agent loop we own: our own
model client, our own tool-call parsing, our own workspace-fenced toolset, with the
`salienceos` judgment core mediating every tool action before it runs. This is the
Step-0 milestone from `docs/collaborator-plain-language.md` — "the governed loop
exists and obeys" — proven both live against real models and deterministically
against the real core.

Runner: `live_proof.py`. Raw output: `live_proof_output.txt`.

## Part A — real models, end to end

Two local models, each emitting its tool call in a form the rig's **structured-only**
path silently drops (the box tool-exec gap). The Collaborator parses and executes
both, under governance, with the write verified on disk:

| Model | What it emitted | Parsed via | Result |
|---|---|---|---|
| `mistral-nemo:12b` | `{"name":"write_file","arguments":{…}}` (bare content JSON) | `content_json` | **ran, verified**; file == requested content ✓ |
| `gemma4:12b` | `<tool_call {"name":"write_file",…}>` (malformed opener) | `content_block` | **ran, verified**; file == requested content ✓ |

Both would have produced **zero** actions on the rig's default path — the model
could then narrate "done" over a write that never happened (the exact failure we
watched before). Here the call is caught, governed, executed, and the file's real
bytes are verified.

*(Parser hardening this round: a balanced-brace extractor now catches every
`<tool_call …>` tag variant real models emit — including gemma's malformed opener
and calls with nested braces — without truncating on the first `}`.)*

## Part B — governance properties (deterministic, real core)

| Property | Result |
|---|---|
| Content-embedded call parsed **and** governed-executed | PASS |
| Low-stakes `act_then_report` runs; the **real** tool result is reported | PASS |
| Higher-stakes `propose_first` is **HELD** for approval, then approved runs | PASS |
| Max-importance `run_command` **DENIED** — capability gate holds (P-01) | PASS |

Two A/B contrasts prove the two novel pieces are load-bearing, not decorative:

- **A/B #1 — the parser.** The same content tool-call: structured-only parse → **0
  actions** (dropped); Collaborator parse → **1 action, ran, file written.**
- **A/B #2 — the workspace fence.** The same escaping path (`../ESCAPED.txt`): a
  naive runner **writes outside the workspace**; the Collaborator **DENIES** it and
  no file is created outside.

## What makes the honesty real (not a slogan)

- **Owned parse** → every real tool intent is caught (structured *and* content),
  strictly; ambiguous text is surfaced, never guessed into running.
- **Verified execution** → `write_file` runs under the verifier's supervisor and its
  claim is checked against the re-hashed file (a corrupted or empty write can't
  clear); `run_command` clears on the **supervisor's** exit code, not the tool's
  self-report.
- **Honest feedback** → the string the loop feeds back to the model is
  `decision.summary()`, derived from the real outcome — a held, denied, or failed
  action is reported as exactly that, never as a success.
- **Fails closed** → a governance error, an ungranted capability, or a
  workspace-escaping path DENIES the action; it is never run to keep the chat moving.

## P-01 at the hands

The **capability** (what a tool may touch at all) is the one authority the core
enforces (`directive.grants_capability`), set only by host policy — a test proves
**max importance cannot open `shell.exec`.** The **leash** (how it may act) is a
second, host-config axis. Salience (importance) moves only scrutiny and compute.
Importance buys effort, never permission.

## Honest scope

- **Part A is a single governed action per model**, deliberately. An earlier run used
  the multi-iteration loop and a chatty model issued a *second* empty write that
  overwrote the file — the loop governed both correctly, but it muddied the
  demonstration. The loop itself (multi-step, honest feedback, `approve()`) is covered
  by the unit tests; Part A isolates the box-gap claim.
- **v0 toolset is small** (`write_file` / `read_file` / `run_command`) and
  **workspace-fenced**; the leash lives in host config (a signed-policy leash field is
  deferred, as the resource governor deferred its own). The judgment-view surface, the
  propose channel, and larger tools are later steps.
- **Stage-4-live** (next) flips `allow_adaptation` on and drives a real risky+important
  action so the memory-RETAIN / weight-BLOCK disagreement fires on the live audit
  trail — the already-built `consumers/` gate, exercised through this host.

**Verification:** 29 collaborator unit tests; full suite 232 pass; live proof above
`STEP-0 PROOF: OK` with the box-gap fix demonstrated on 2/2 real models.
