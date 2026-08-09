# ④ Memory — the long multi-turn e2e (all pieces, 26 turns, gpt-oss:120b)

A proposer-driven working session against the destination model on Sparky's fast NVMe, every
piece live: doer + separate proposer, real gists (CDMS-A copy) + real curated facts (CDMS-D
copy), ③ governance + leash + verifier, `ambiguous` ingest each turn, midpoint consolidation.
Harness: `e2e_long_run.py`; transcript: `e2e_long_run_output.json`.

## Run 1 — the plumbing passed; the proposer DEGENERATED

Infra/memory/isolation PASS (23 `ambiguous` ingests, consolidation grew persona 110→114, both
live stores untouched). But the proposer, after writing a README, proposed `read_file
README.md` **~20 times in a row**. Root causes: no recent-action awareness; open-ended prompt +
near-empty workspace + no goal → trivial convergence; auto-approve removed the veto brake.

## The fix — a prose/instruction-design panel

5 prose models (opus-4.1, sonnet-4.5, gpt-5.1, gemini-2.5-pro, grok-4.5; $0.1474) converged:
feed a fenced `<<recent-actions>>` block + forbid re-proposing the same tool+arg on an unchanged
workspace; enumerate a RICHER proposal space as examples-not-menu (keep "surprise me"); tie
confidence to MARGINAL value; make decline competitive with trivial busywork. Applied: revised
`_PROPOSER_SYSTEM` + `build_proposer_context(recent_actions=...)`.

## Run 2 — the fix worked, and it surfaced the research gap

Outcomes: **ran 6 / failed 10 / declined 10** (both live stores untouched).

**The repetition is gone, and the outputs are genuinely good.** The proposer produced a coherent
project-scaffolding sequence — README, PROJECT_PLAN, SETUP_GUIDE, .gitignore, LICENSE,
CONTRIBUTING — grounded in the real memory. The README used the actual facts (Tales of the Tao,
wuxia 4X, Unity 6, `D:\Repo\tales-of-tao`), pulled the **jcode reference from the gists**, and
folded in the **3D-printing note from the user prefs**. The `.gitignore` is a proper Unity one;
the generated `GameManager.cs` is clean idiomatic Unity C#. Real, useful, memory-shaped work.

**The research gap, demonstrated (turns 8–26).** It tried to write
`Assets/Scripts/GameManager.cs` and **failed 10 times** — the parent dir doesn't exist and
`write_file` doesn't create parents. The proposer *saw* the failures (recent-actions showed
`failed`; a rationale literally reads "addressing repeated write failures") but **could not
diagnose why**, because it cannot research (list the dir, see `Assets/` is absent). So it retried
the same failing write, blind. The code was fine; the failure was environmental; a research-
capable proposer would have listed the workspace and proposed `mkdir` first (or a flat path).

The 10 declines are a WIN of the revised prompt — it declined rather than manufacture busywork.

## What this establishes

- **The proposer's instructions are fixed** (variety + substance + anti-repetition + honest
  decline), validated live at 26 turns against 120b.
- **The proposer is single-shot and blind** — one LLM call, seeing only fenced memory/facts + a
  file *list*, never actual state. Its recommendations are not researched. This is the next gap.
- **Next build: a governed read-only RESEARCH loop** — let the proposer read/list within the
  workspace + recall memory itself, for a budgeted few steps, before emitting a grounded
  proposal. Bounded by a per-proposal **trust-level config** (local-only / read-only research /
  sandboxed creation) and a **salience-modulated budget** (importance buys research depth). Also
  a candidate quick complement: `write_file` could `mkdir -p` its parent, but research is the
  principled fix (understand the workspace, don't paper over it).
- **Output legibility** (the human-facing rationale/presentation) is a separate, later pass.
