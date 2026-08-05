# SalienceOS Central Interpreter — Correctness / Fail-Closed Review

Scope: `salienceos/interpreter/{signal,policy,directive,interpreter,bus,scorers}.py`
plus `salienceos/verifier/signing.py` (the signature primitive `verify_policy` relies on).
Invariant under test: **P-01 — salience influences; policy authorizes.** `interpret()` must be
pure and fail-closed (errors/edge cases → most cautious directive).

Overall: the P-01 fence holds structurally. `allowed_capabilities` is copied verbatim from the
signed policy with no signal path, adaptation is hard-gated behind `policy.allow_adaptation`, the
signature covers every substantive field, and NaN/inf/bool are rejected at the signal boundary.
The findings below are the substantive ones; most are robustness / semantic-caution issues rather
than authority escapes. No path was found that lets a signal widen capabilities or forge a policy.

---

## Findings

### F1 — MEDIUM — A low-influence VERIFICATION/RISK signal drops verification BELOW the uninformed max default
File/function: `salienceos/interpreter/interpreter.py`, `interpret()` lines 66-73.

```python
v_inputs = [agg[f] for f in (Facet.VERIFICATION, Facet.RISK) if f in agg]
if v_inputs:
    v_depth = _scale(max(v_inputs), policy.min_verification, policy.max_verification)
else:
    v_depth = policy.max_verification
```

Triggering input: signed policy with `min_verification=0` (NONE), `max_verification=3` (FULL);
a single valid `SalienceSignal(subject=policy.subject, facet=Facet.RISK, influence=0.0,
confidence=1.0)` (or the same on `Facet.VERIFICATION`).

Result: `v_inputs=[0.0]` → `v_depth = _scale(0.0, 0, 3) = 0` (NONE). With **no** signal the code
would default to `max_verification = 3` (FULL). So a subsystem asserting "zero risk / no
verification salient" collapses verification from FULL to the policy floor.

Why it matters: this is exactly the "more permissive than the cautious fail-closed default"
category. The module docstring and `Facet` doc say verification "scales up (safety)" and RISK
"raises verification"; here a trivially-cheap low-influence signal *lowers* a safety knob below the
uninformed baseline. It stays within the policy `[min,max]` window (so it is not an authority
escalation — the policy did authorize `min_verification=0`), but it defeats "never under-verify"
whenever a policy leaves headroom, and lets any single subsystem pull verification to the floor.

Minimal fix (choose per intended semantics):
- If VERIFICATION/RISK are meant to only *raise*: floor the result at the uninformed default,
  e.g. treat a present-but-low signal as no worse than absent:
  `v_depth = max(policy.max_verification if <uninformed-baseline-desired> else policy.min_verification, _scale(...))`
  — or more simply, only let these facets raise above `min_verification`, never reduce below the
  ceiling that absence would give.
- If reduction is genuinely intended, update the docstrings so "risk raises verification" no longer
  contradicts the behavior, and gate the reduction on a confidence floor so a near-noise signal
  cannot flip FULL→NONE.

---

### F2 — LOW/MEDIUM — `verify_policy` does not type-check the boolean/int cap fields (issuer footgun defeats the adaptation gate)
File/function: `salienceos/interpreter/policy.py`, `verify_policy()` lines 98-110; interacts with
`interpret()` line 84 (`policy.allow_adaptation and ...`) and line 94 (`allow_immediate_reconfigure`).

`verify_policy` validates numeric windows and `granted_capabilities` is a tuple, but never asserts
`isinstance(policy.allow_adaptation, bool)` / `isinstance(policy.allow_immediate_reconfigure, bool)`,
nor bounds `adaptation_min_verification`. Because `interpret` uses truthiness
(`if policy.allow_adaptation and ...`), a policy whose `allow_adaptation` is a truthy non-bool
(e.g. the string `"false"`, or `1`) would enable adaptation to reach CANDIDATE.

Triggering input: a *validly signed* `PolicyCaps` where `allow_adaptation="false"` (a mistaken
issuer, or `issue_policy` called with a non-bool since the `: bool` annotation is not enforced),
plus an ADAPTATION signal and low/absent-but-permitted risk. `"false"` is truthy → gate passes.

Why it matters: this is not a signal-driven escalation (the field is signed, so no remote input can
set it), but the interpreter's last line of defense against a malformed authority envelope is
weaker than it should be for a fail-closed choke point. `verify_policy` is the trust boundary and
should reject a structurally-incoherent policy rather than let truthiness stand in for a real bool.

Minimal fix: in `verify_policy` add
`and isinstance(policy.allow_adaptation, bool) and isinstance(policy.allow_immediate_reconfigure, bool)`
and validate `isinstance(policy.min_budget, int)` etc. (reject `bool` where int is required) and
`0 <= policy.adaptation_min_verification <= int(VerificationDepth.FULL)`.

---

### F3 — LOW — `dropped` invalid-signal count is wrong when `signals` is a one-shot iterator
File/function: `salienceos/interpreter/interpreter.py`, `interpret()` lines 48-49.

```python
valid = tuple(s for s in signals if valid_signal(s))
dropped = len(tuple(signals)) - len(valid)
```

Triggering input: pass a generator/iterator for `signals` (the signature and docstring say
"`signals` may contain anything"). The first comprehension exhausts the iterator; `tuple(signals)`
then yields `()`, so `dropped = 0 - len(valid) = -len(valid)` — a negative count, and
`reasons` gets `dropped_invalid_signals=-N`.

Why it matters: cosmetic/audit-integrity only — `valid` is materialized first, so the directive
itself is computed from the correct signals. But the reason string (which lands in the append-only
bus audit trail via `emit`) is wrong/negative, and any caller trusting `dropped` is misled.

Minimal fix: materialize once — `signals = tuple(signals)` at the top of `interpret`, then compute
`valid` and `dropped` from that.

---

### F4 — LOW — Non-iterable `signals` or wrong-typed `policy_key` raises instead of fail-closing
File/function: `salienceos/interpreter/interpreter.py` line 48; `salienceos/interpreter/policy.py`
`verify_policy` → `signing.sign`.

`valid = tuple(s for s in signals ...)` runs *before* the policy check, so `signals=None` raises
`TypeError` rather than returning `_hard_deny`. Likewise, if `policy_key` is not `bytes` (e.g. a
`str`), `hmac.new` inside `sign` raises `TypeError` out of `verify_policy` / `interpret`.

Why it matters: an uncaught exception is not a *more permissive* directive, so it is not an
authority leak — but a fail-closed choke point should degrade to the hardest deny on malformed
input, not crash the caller. A crash also means no `_hard_deny` reason is recorded.

Minimal fix: materialize `signals` defensively (`try/except TypeError → treat as empty`), and make
`verify_policy` exception-safe (wrap the signature check so any error returns `False` → `_hard_deny`).

---

### F5 — LOW / informational — Arbitration is vulnerable to volume-based influence inflation (bounded by policy)
File/function: `salienceos/interpreter/interpreter.py`, `_aggregate()` lines 135-143.

The confidence-weighted mean does not dedup or cap per subsystem, so one subsystem publishing many
high-influence/high-confidence signals for a facet dominates the mean over other subsystems.
Example: 100× `(ATTENTION, influence=1.0, confidence=1.0)` from subsystem A vs 1×
`(influence=0.0, confidence=1.0)` from B → mean ≈ 0.99 → budget near `max_budget`.

Why it matters: this is an arbitration-fairness issue, **not** a P-01 violation — every knob it
touches (budget, verification, retention) is still clamped into the signed policy window, so no
subsystem can exceed the policy's authorized ceiling. Noting it because "salience influences" here
means one noisy subsystem can drive a knob to its policy max.

Minimal fix (if desired): aggregate per `(subsystem_id, facet)` first (e.g. take each subsystem's
max or latest), then combine across subsystems; or cap total weight per subsystem.

---

## Categories that are CLEAN

- **Category 1 (a signal widening `allowed_capabilities`, or adaptation → CANDIDATE without
  `policy.allow_adaptation`): CLEAN.** `allowed_capabilities=tuple(policy.granted_capabilities)`
  (interpreter.py:108) is the only assignment and has no signal input; `grants_capability` is the
  only accessor. The adaptation branch (interpreter.py:84-90) short-circuits on
  `policy.allow_adaptation`, so no signal at any influence/confidence can reach CANDIDATE without
  the signed policy switch (see F2 only for the non-bool-truthiness caveat, which is issuer-side,
  not signal-side). Absent risk defaults to `1.0` and blocks adaptation unless the policy
  explicitly permits `adaptation_max_risk == 1.0`.

- **Category 3 (integer/float/rounding in `_scale`/`_clamp`/`_aggregate`/`_retention`; div-by-zero):
  CLEAN.** `agg` values are provably in `[0,1]` (only valid signals with unit influence/confidence
  feed it), and `hi >= lo` is guaranteed by `verify_policy` for both budget and verification, so
  `_scale(frac,lo,hi)=lo+round(frac*(hi-lo))` lands in `[lo,hi]`; every consumer additionally
  `_clamp`s, which is a correct final safety net. `_retention` bounds the index by `max_idx`.
  `_aggregate` guards the division with `if weight > 0 else 0.0` (and `0.0` is the most cautious
  fallback), so no division-by-zero. Confidence weighting `sum(inf*conf)/sum(conf)` is a correct
  weighted mean and cannot exceed 1.0 (`inf<=1`). Duplicating identical signals does not amplify
  the mean.

- **Category 4 (bypass signature check / accept a tampered `PolicyCaps`): CLEAN.**
  `PolicyCaps.signed_payload()` (policy.py:49-63) enumerates all 12 substantive fields —
  `granted_capabilities` and every cap bound (`min/max_budget`, `min/max_verification`,
  `max_retention`, `allow_adaptation`, `adaptation_min_verification`, `adaptation_max_risk`,
  `allow_immediate_reconfigure`) — omitting only `signature` itself (correct). Any mutation to a
  covered field changes the HMAC input, so a tampered copy fails `signature_valid`.
  `signature_valid` (signing.py:34-37) rejects empty/non-str signatures and uses
  `hmac.compare_digest` (constant-time). `verify_policy` also requires `type(policy) is PolicyCaps`
  and a coherent window. No bypass found.

- **Category 5 (NaN/inf handling): CLEAN.** `signal._unit` (signal.py:46-47):
  `isinstance(x,(int,float)) and not isinstance(x,bool) and 0.0 <= float(x) <= 1.0`. For
  `float('nan')` the comparison chain is `False`; for `float('inf')` the `inf <= 1.0` term is
  `False`; `bool` is explicitly excluded so `True` cannot masquerade as `1.0`. `valid_signal`
  therefore drops any signal with NaN/inf/bool influence or confidence at the interpreter boundary,
  and such signals never reach `_aggregate`.

---

## Notes on purity / fail-closed defaults (confirmed good)

- `interpret` uses no I/O, clock, or globals beyond module constants; dict iteration in `_aggregate`
  is insertion-ordered and deterministic → pure.
- `_hard_deny` is genuinely the hardest directive: `compute_budget=0`,
  `verification_depth=FULL(3)` (ignores untrusted policy bounds, uses absolute max),
  `retention=ephemeral`, `adaptation=NONE`, `allowed_capabilities=()`, `reconfigure=BETWEEN_TURN`.
- "No subject signals" path yields min budget, max verification, ephemeral retention — cautious.
- Unknown facets are aggregated but never read by any knob (`agg.get` only queries known facets),
  so an unrecognized signal grants nothing.
