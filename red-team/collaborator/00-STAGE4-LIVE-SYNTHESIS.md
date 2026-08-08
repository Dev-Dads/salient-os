# Stage-4-live — the disagreement, fired through a real host

## What this is (and what it is NOT)

The **memory-retention governor + weight-adaptation gate** — SalienceOS's two
deliberately *disagreeing* channels — **already exist and are tested** in
`salienceos/consumers/` (build stage 4, PR #5). This work does **not** rebuild them.

The gap it closes: the disagreement was *library-real but host-dormant*. As the
`consumers/__init__` docstring says outright — "a host policy with
`allow_adaptation=False` therefore produces no inhibitors at all: the disagreement
property is library-real and host-dormant until an adaptation path exists there."
Nothing had ever driven that path on a real governed action. **The Collaborator is now
that host.**

*(This re-scope was flagged for Josh when the integration scout found the core already
built — task #18. If a genuine rebuild/extension was intended, it's a redirect.)*

## The wiring (small, gated, additive)

When a Collaborator session sets `allow_adaptation=True`, the governance seam:
1. emits a `Facet.ADAPTATION` signal (requesting the outcome be considered for learning),
   alongside the host-computed ATTENTION and RISK signals;
2. after the governed outcome, calls `consume(outcome, now_days)` and records the
   `(AdaptationDecision, MemoryRetention)` pair plus a `disagreement` flag.

`disagreement := (not adaptation.nominated) and adaptation.handoff is not None and memory.inhibitor`
— i.e. the weight gate refused to learn it *and* originated the inhibitor hand-off *and*
the memory governor pinned it.

Nothing is re-derived: `consume` is the pure gate; the collaborator only supplies the
bound outcome and the clock, and reads the two records. `allow_adaptation=False` (the
default) emits no ADAPTATION signal, calls no `consume`, and produces no inhibitors.

## The live proof (`stage4_live_proof.py`)

A real risky + important governed write (`importance=0.9, risk=0.9` — over the `0.4`
adaptation-risk cap), through the real seam:

```
action ran (file written) : True    verified: False
recorded rationale        : AdaptationRationale.RISK_EXCEEDED
WEIGHT gate  -> nominated_for_learning=False (HARD BLOCK)  handoff=True
MEMORY gate  -> inhibitor=True  class='ephemeral'  (RETAIN as warning)
DISAGREEMENT : True   <- the same event refused as a skill, kept as a warning
inhibitor weight: day 0 = 1.0   day 100000 = 1.0   -> NO DECAY
audit bus    : chain_intact=True  signals_recorded_for_action=3
```

- **Contrast 1** — a low-risk action (adaptation on): `disagreement=False`, not inhibited.
- **Contrast 2** — adaptation off (default): dormant — no records, no inhibitor.

This is the roadmap's Stage-1 disagreement proof — "the *same* risky, important event is
**kept** as a permanent warning and simultaneously **refused** as a skill" — promoted from
a unit fixture to a **live governed worker**.

## Honest notes

- **The action ran but did not verify.** `risk=0.9` drives verification depth to FULL,
  which one host observation source can't satisfy, so the write is `cleared=False`. That is
  correct and by design: the disagreement fires from the recorded `RISK_EXCEEDED` rationale
  on a **bound** outcome, independent of clearance (verified only in code, then here). The
  claim is "the disagreement fires," not "the action was verified."
- **The inhibitor's `retention_class` is `ephemeral`** (no `MEMORY` signal was emitted, so
  the class floored). That is orthogonal to the pin: an inhibitor is exempt from decay
  regardless of class (`effective_weight` day-0 == day-100000 == 1.0 proves it). The class
  ladder and the inhibitor dimension are deliberately separate (`memory.py`).
- **The `consume` call fails safe to no-records** so a learning-path error can't block the
  action's real report; the action itself and its verification are unaffected.

## External panel review (general panel, reproduce-before-accept)

Verdicts: deepseek `SERIOUS_FLAWS`, grok `MINOR_ISSUES`, mistral `SERIOUS_FLAWS`, glm
`MINOR_ISSUES`, kimi (truncated). Reproduce-before-accept on the actual code put the
verified consensus at **MINOR_ISSUES bordering SERIOUS** — faithful and P-01-safe as
shipped, with **two real findings, both fixed:**

1. **[HIGH] Swallowed `consume()` exception fails open on the inhibitor.** The blanket
   `except` re-introduced exactly the fail-open the memory gate *raises*
   `HandoffMismatchError` to prevent (a dropped warning reported as a clean line).
   **Fixed:** the failure is captured into a new `Decision.learning_error`, the summary
   renders "⚠ LEARNING ERROR — inhibitor may be lost", and `disagreement` is never
   silently set False as if nothing happened. `now_days` is now validated at Session
   construction (finite, non-negative), closing the NaN/negative path that would trip it.
2. **[MEDIUM] ADAPTATION signal emitted for tools that are never consumed.** `run_command`
   (exit-mode) and `read_file` (read) produce no `GovernedOutcome`, so their ADAPTATION
   signal was a promise the wiring couldn't keep. **Fixed:** the signal now fires only for
   artifact-verified tools (where `consume` follows).

Overturned by reading the code (not applied): the two "CRITICAL/HIGH P-01" claims — the
model cannot set `risk`/`importance` (host params, not on `ToolIntent`), and a nomination
has **no apply surface** (schema-pinned), so nothing risky can be "learned as a skill";
the `disagreement` read cannot false-positive (`memory.inhibitor` is reachable only via an
attributed hand-off, else `retain()` raises); and "fires live" is honest (the proof prints
`verified: False`). Raw reviews in `raw_stage4/`.

## Verification

8 Stage-4-live unit tests (disagreement fires; inhibitor never decays; summary reports it;
low-risk not inhibited; adaptation-off dormant; **consume-failure surfaces a learning_error;
Session rejects bad now_days; ADAPTATION signal only for consumable tools**). Full suite
**240 pass**. Live proof: `STAGE-4-LIVE: OK`.
