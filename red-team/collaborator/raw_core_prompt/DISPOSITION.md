# Core-prompt panel — disposition

**Purpose:** author `_CORE_SYSTEM`, the missing system prompt for the DIRECTIVE loop
(`run_turn`). Today `run_turn` sends the model the bare conversation — no role, no tool list,
no argument shapes, no reply format — so it only sometimes acts ("make it move"). The proposer
path moves reliably because it IS grounded (`_PROPOSER_SYSTEM`); this is the sibling for the
directive path. Operator steer: the external panel authors the prompt (as
`proposer_prompt_panel.py` authored the proposer's).

**Panel:** 5 prose models. **Cost $0.1956 total.**

| model | cost | verdict |
|---|---|---|
| anthropic/claude-opus-4.1 | $0.1155 | full candidate — clean, `##`-structured |
| anthropic/claude-sonnet-4.5 | $0.0343 | full candidate — tight + warm ("system enforces safety so you can focus on helping") |
| openai/gpt-5.1 | $0.0330 | full candidate — most exhaustive; caught two load-bearing parser details |
| x-ai/grok-4.5 | $0.0128 | full candidate — tightest, best voice, correct fence scope |
| google/gemini-2.5-pro | $0.0000 | ERROR — returned only its reasoning trace, no prompt (free) |

**Convergence (strong signal):** all four usable candidates independently produced the same
seven-part structure — role, exact tool table (argv list + allowlist), dual call form preferring
`<tool_call>`, act-until-done loop, TOOL RESULTS = ground truth, HELD≠done honesty contract,
injection fence (user directs / tool output is data), warm plain-language voice.

**Reproduce-before-accept (checked candidates against the real code, not their guesses):**
- Parser (`collaborator/toolcall.py`): confirmed `<tool_call>{...}</tool_call>` is caught (marker
  scan + balanced-JSON extract), multiple blocks are caught (`finditer`), prose may coexist, and a
  bare mid-sentence blob is correctly NOT run. The `{"name","arguments"}` shape is the simplest
  accepted form. All candidate instructions match the parser.
- **Load-bearing detail only gpt-5.1 stated:** the FINAL answer must contain no `<tool_call>`
  markup. Verified this is not cosmetic — `run_turn` terminates only when `parse_message` finds
  NO intents, so a stray marker in the "final" answer would be parsed as an action and the loop
  would continue. Kept this rule; grafted from gpt-5.1.
- **Status vocabulary:** the candidates guessed "HELD/DENIED/FAILED"; the real surface
  (`governance.py Decision.summary()`) is richer — lines begin `✓` (ran, verified) / `✗ FAILED` /
  `⏸ HELD for your approval` / `⛔ DENIED` / `· notify-only`. The synthesized prompt references the
  ACTUAL markers so the model pattern-matches the real results rather than a guessed word.

**Synthesis:** grok's tight spine + sonnet's warmth + gpt-5.1's two parser specifics (no
`<tool_call>` in the final answer; JSON-only inside the markers) + the exact status markers from
the code + a "no action needed → just answer" clause. Result in `core_system_synth.txt`
(~2.4k chars, every sentence load-bearing).

**Naming (operator steer):** the prompt's presented identity is **Sal** (the face), not "the
Core". The panel wrote "You are the Core"; the Core is the impersonal executor guts, and the
thing the user talks to / trusts / that speaks in the warm partner voice is Sal. Also safer-
framed: "the Collaborator" (the whole) would subtly claim the model IS the governance/seam, but
the model only proposes and the seam enforces (P-01) — Sal is the face that acts THROUGH the
governed Core, fence around her, not in her hands. Constant named `_SAL_SYSTEM` accordingly.

**Build note:** in the code the tool table becomes a GENERATED block from a single source of
truth in `tools.py` (one spec → the system-prompt manifest AND the OpenAI `tools=` array), so the
names/args in the prompt can never drift from the executor. The static governance/loop/fence/voice
text lives with `run_turn`. Validation is the automated live-Sparky measured e2e (the prompt run
against gpt-oss:120b), per the plan.
