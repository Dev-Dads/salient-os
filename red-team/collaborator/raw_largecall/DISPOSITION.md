# LargeCall panel — disposition

Change: never silently drop a large/truncated/malformed/batched tool call (surface it) +
retry-on-truncation (grow max_tokens). Three external panels (5 vendors each,
`redteam_largecall.py`, ~$3.25 total). Reproduce-before-accept on every finding.

## Bugs found and FIXED (all reproduced first)

1. **Truncated `<tool_call>` block silently dropped** (round 1, pre-panel self-review): an
   unbalanced span was skipped entirely — not even ambiguous. → surfaced as ambiguous.
2. **Whole-content JSON array all-or-nothing** (round 1): a batch with one bad element dropped
   the whole batch. → surfaced as ambiguous.
3. **grok F1 — truncation retry discarded a complete call co-emitted with a truncated tail**
   (round 2 panel, reproduced: `a.txt` write lost). → `_is_truncated(msg) and not parsed.intents`
   guard: never discard completed work; run the complete calls, surface the clipped tail.
4. **grok/qwen F1 — truncated whole-content JSON (no `<tool_call>`, `_try_json` fails) silently
   lost** (round 3 panel, reproduced). → `_text_looks_toolish` surfaces an unparseable-but-
   tool-shaped whole-content candidate as ambiguous. Closes the silent-loss class across ALL
   parser paths.

Final panel: **grok-4.5 SOUND, qwen3-max SOUND** (C1–C4 certified); gemini C1–C3 certified.

## Findings REFUTED by reproduction (not bugs)

- opus "unbounded retry loop" (CRITICAL) — `continue` in a `for range` is bounded; an
  all-truncated client terminates in exactly `empty_retries+1` calls (measured 4).
- opus "double execution on truncation+success" (HIGH) & round-3 "duplication" (SERIOUS) —
  the complete call runs **once**; `_complete_actionable` executes nothing (run_turn governs
  after it returns). Reproduced: 1 RAN decision, one file written.
- opus "base_mt=0 breaks budget growth" (SERIOUS) — `getattr(client,"max_tokens",_DEFAULT) or
  _DEFAULT` catches 0 and None; no crash (reproduced with both).
- opus/gemini "text-stripping corrupts prose" / prose-between-calls — prefix and inter-call
  prose are preserved (reproduced: `A  B`, `Reply prefix.`); no residual `<tool_call` marker.
- opus F3 whole-content batch example — its `tool`/`args` keys DO coerce (`_coerce_call`
  accepts them); both calls run (reproduced: 2 intents).

## Accepted nit (not fixed, by design)

- gpt-5.1/opus/gemini: trailing prose AFTER a **malformed (unbalanced, non-truncated)**
  `<tool_call>` marker lands in `ambiguous` rather than the clean `text` reply. This is **not
  silent loss** — the content is surfaced to the user (the loop prints ambiguous items). An
  unbalanced span's JSON never closes, so there is no non-heuristic way to split "broken JSON"
  from "prose that follows"; surfacing the whole tail as ambiguous is the conservative, honest
  choice. For the real truncation case (finish_reason=length) there is no trailing prose (cut
  at EOF), so this edge does not arise there. grok + qwen classified it a nit, not a break.

## Separate follow-up (filed, not in this PR)

- `run_turn` does not catch a client HTTP error / timeout (a transient backend 500 crashes the
  turn). Observed live when forcing tiny max_tokens below the backend's reasoning floor (a test
  artifact, not a production path at 16384). A general loop-robustness item for a later PR.
