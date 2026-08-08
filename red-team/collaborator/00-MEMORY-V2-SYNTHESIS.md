# ④ Memory design v2 (two-agent) — panel synthesis

*Second 5-model panel (`redteam_memory_v2.py`), on the two-agent redesign that resolved v1's
recall contradiction. Reproduce-before-accept applied against the code. This records the result,
the confirmed findings, the severity corrections, and the recommended path.*

## Headline — the split worked; the remaining work is honesty + seam-hardening

**Verdicts:** SERIOUS_FLAWS ×2 (grok, mistral) · MINOR_ISSUES ×2 (glm, deepseek) · kimi
(truncated, no final verdict, but explicit headline: *"No CRITICAL findings — nothing below
defeats the capability gate or creates a new authority path"*).

Compared to v1 (SERIOUS ×3 / MINOR ×1), this is a real improvement, and the *substance* moved the
right way: v1's findings were "the design contradicts itself"; v2's are "two structural claims are
**overstated** and several seams are **under-pinned**." **The authority firewall survived a second,
harder pass** — every model that reached a verdict affirmed that memory reaches no capability
decision and the split creates no new authority path. There is **no architectural problem**; there
is a doc that over-claimed *structural* where some properties are *behavioral*, and a set of seams
that must be made genuinely structural (or honestly labeled).

## API cost (this review)

| model | cost | tokens (in→out) |
|---|---|---|
| deepseek-v4-pro | $0.0042 | 15971 → 9738 |
| glm-5.2 | $0.0087 | 15226 → 12572 |
| mistral-medium-3.5 | $0.0535 | 16133 → 3909 |
| grok-4.5 | $0.0620 | 16067 → 5020 |
| kimi-k3 | $0.2858 | 15283 → 16000 (hit length cap) |
| **TOTAL** | **$0.4143** | |

*(Two panels to date: v1 $0.4142 + v2 $0.4143 = **$0.8285**.)*

## The findings — two buckets, all confirmed

### Bucket 1 — Honesty corrections (I over-sold "structural")

| # | Finding | Who | Assessed |
|---|---|---|---|
| H1 | **"Separation → identity" is overstated.** It's *access* separation (the doer can't see its own history — real, structural) + a *prompt-level* observer-stance for the proposer (behavioral, not a structural identity firewall). The proposer's own system prompt says "You are the… sense" and can self-attribute. | ALL 5 | CONFIRMED |
| H2 | **"`ambiguous` → no self-authored authority" is imprecise.** It bars *scars* (guardrails) — true — but not the self-authored *agenda*: the gist→propose→human-approve→deed→gist loop shapes what gets proposed, within-grant. "Worst case is noise" is false once ③ grants a tool. | grok, mistral, glm | CONFIRMED |
| H3 | **The DATA fence is behavioral, not structural.** Its strength is a property of the model's instruction-following, not the code; it belongs beside "canary regression test," not beside "ambiguous never scars." | glm, deepseek, grok, mistral | CONFIRMED |
| H4 | **Distillation ≠ sanitization (kimi's sharp catch).** Gist tuples are distilled *from* the same adversarial episodic text, by an LLM pass; a payload can survive verbatim in a tuple's object field. So "gist-only keeps injection out" is **false as written** — injection is *reduced*, not *dissolved*; the fence must cover tuples too. | kimi | CONFIRMED |
| H5 | **"Three independent locks" blurs structural and behavioral.** Real structural locks: (A) doer has no history API, (B) proposer has gist-only API, (C) ambiguous-no-scar, (D) ③. Observer-stance + DATA-fence are behavioral defense-in-depth on the critical path, not peer locks. | grok, mistral, glm | CONFIRMED |

### Bucket 2 — Real hardening (make seams structural, or build/define them)

| # | Finding | Who | Assessed |
|---|---|---|---|
| S1 | **Raw-episodic escape hatch.** `ambiguous` surfaces on raw recall (db.py:595), so any `memory.read` with episodic tiers / adapter fallback / bug reaches raw deed text. The gist-only restriction is default-off, not structural. | ALL 5 | CONFIRMED |
| S2 | **Doer history-blindness is intended, not choked.** Only proposer-side code is shown; feature-creep (memory.read to the doer, a shared assembler, an error fallback) re-opens it. | grok, glm, deepseek | CONFIRMED |
| S3 | **Fact fence under-specified.** propose.py concatenates `str(context)` into one user message against a thin JSON-only system prompt; the fence needs a typed renderer (structured records → fixed template, control-char/role/tool-JSON stripping, separate message blocks). | ALL 5 | CONFIRMED |
| S4 | **`ambiguous` repurpose can collide** with other CDMS `ambiguous`/quarantine content in the same store, inflating gist support. Deeds need a distinct `source` tag consolidation won't merge across. | grok, glm, deepseek, mistral | CONFIRMED (audit CDMS + tag) |
| S5 | **Veto "decaying inhibitor" is described, not built.** propose.py's `veto_proposal` is a per-object ban; `propose()` never checks past vetoes, so re-proposing the same action surfaces normally. The feedback-bound claim over-relies on an unbuilt mechanism. | glm, deepseek, grok, mistral | CONFIRMED against code |
| S6 | **Ingest hook must be ledger-only.** Schema = ② ledger fields (tool, args-hash, cleared, exit, project, status=ran\|failed\|vetoed); never model rationale/prose (else injection re-enters at source and "proposed" is confused with "ran"). | kimi | CONFIRMED |
| S7 | **Within-grant human-approve is a social-engineering surface.** Once ③ grants a tool, a gist-biased or fact-injected proposer can frame a within-grant harmful action with a benign rationale; the human is the last hard defense, so the proposal UX must show factual args + grounding, not just prose (optionally a host-side dangerous-pattern heuristic that tightens the leash). | grok, deepseek, glm, mistral | CONFIRMED (residual + UX) |

### Severity corrections (the reproduce pass)

- **mistral's three CRITICALs (F1 separation, F3 fact-fence, F5 raw-episodic) are all
  severity-overstated on the authority axis.** kimi is explicit ("no new authority path"), glm and
  deepseek concur (MINOR_ISSUES). Each underlying finding is real (HIGH honesty/hardening), but
  **none is a CRITICAL authority break** — ③ gates every run. Same pattern as v1 (mistral
  over-escalates; the code check + the other models correct it).
- **The most valuable *new* catch is kimi's H4** (distillation ≠ sanitization): it corrects a claim
  the v2 doc made and means injection is *reduced, not dissolved* — the fence applies to tuples, not
  only facts.
- **S5 verified against the code:** `propose.py` has no veto-history check; the decaying inhibitor
  is genuinely unbuilt, so it cannot yet be cited as a bound.

## What this means for the design (a v3)

None of this is architectural. A v3 folds two things:

1. **The honesty reframe (H1–H5).** Replace "three independent locks" with the honest structure:
   **structural** = (A) doer no history-API, (B) proposer gist-only-API, (C) ambiguous→no-scar
   [CDMS-verified], (D) ③ run-gate; **behavioral, critical-path, canary-tested** = the DATA fence
   (over facts *and* tuples) + observer-stance framing. State injection is *reduced, not dissolved*.
2. **Make the seams structural / build them (S1–S7).** `collaborator/memory.py` exposes only
   `read_gist_tuples(...)` — no episodic API in the package, import-banned; typed
   `FactView`/`HistoryView` handles minted per session (doer's type excludes `HistoryView`);
   ledger-only ingest schema with a `source="collaborator_deed"` tag; a defined veto-inhibitor
   record (key=normalized intent, bar_delta, half_life, floor) or an honest deferral; the fact/tuple
   fence as a typed renderer; proposal UX shows factual args + grounding.

## One genuine choice for the host

**Proposer isolation.** grok/kimi note that "distinct role/context on the same model" makes Lock A's
identity half exactly as strong as a system prompt. Options: (a) require a **distinct model
instance** for the proposer in v0 (stronger separation, more cost/complexity), or (b) keep
**same-model-different-role** + a CI canary eval that fails on first-person self-attribution +
honestly label it a prompt-level mitigation contained by C/D. Recommendation: (b) for v0 with the
canary, since C (no scar) + D (③) contain the failure and A's *access* half (the load-bearing part)
is structural regardless.

## Recommendation on process

Two panels ($0.83) have validated the architecture and converged on a bounded set of honesty +
hardening fixes. A **third design panel would be low-value**: the remaining items are
implementation-hardening that is *better proven by the actual tests* (import-ban tests, typed-view
tests, injection canaries against real consolidation) than by another design review. Recommend:
**fold into a v3 doc, then BUILD with S1–S7 as test-pinned requirements** — not a third panel.
Final call is the host's (spend + sequencing).
