# SalienceOS — Consolidated Design Review v0.2

**Reviewer:** Claude (Opus 4.8), external design critic
**Date:** 2026-08-03
**Supersedes:** v0.1 (same directory). v0.1's scorecard, evidence, and self-pressure-test still hold; this version reworks the *framing* around what you've since clarified you actually mean. The change is not cosmetic — it flips one finding's sign and corrects the review's central slant.

---

## What changed from v0.1, and why

v0.1 was built on a premise you've since corrected, so its evidence survives but its lean is wrong. Three corrections drive this rewrite:

1. **Salience is a pervasive principle, not a universal algorithm.** v0.1 spent its energy on whether one canonical vector could span every subsystem and treated the collapse of that idea as damaging. You never wanted one vector. Each subsystem may score salience its own way; the OS's commitment is that salience is *used everywhere*, under one rule. So the Phase-0 orthogonality result flips from **critique to support** (Finding A).
2. **The mechanism is a bus + a central interpreter, not a shared schema.** The actual new idea: subsystems publish salience onto a shared bus; a central unit reads it and issues directives. That, plus the verifier, is what nothing else provides — and it is what the review should be about.
3. **The nervous-system MoE hardware is ancestry, not spec.** The conceptual images (custom silicon, physical expert tiles, spine fabric, the Stage 0–5 / $11–55M roadmap) are explicitly **out of scope**. SalienceOS was always the software control plane. Hardware stalling for a while costs it nothing — §18 already frames physical MoE as an execution target, not a redesign.

**On canon:** the Sol/ChatGPT document is one articulation of your intent, not the intent itself. Where it hardened into specifics you didn't ask for — the canonical 10-dimensional vector (§4.1), the fixed projection weights (§4.4), the single-envelope schema (§4.2) — those are *proposals for one subsystem's scorer*, discardable, not architecture. What's canon is below.

---

## Part 1 — What SalienceOS is

**The thesis (survives whole).** An AI control plane that applies salience weighting pervasively: routing, memory, verification, compute budget, and adaptation each spend effort in proportion to what matters. One invariant governs all of it:

> **Salience influences; policy authorizes.** A high salience score may buy more scrutiny, compute, retention, or verification — never a capability. Only policy grants authority. *(The doc's P-01, and the load-bearing rule.)*

**The mechanism (the actual contribution).** Not one algorithm, not one vector, not one schema:

- Each subsystem computes its own salience however suits it — routing, memory, and expert-promotion salience can be entirely different functions over different inputs with different shapes.
- Every subsystem **publishes** its salience onto a shared **bus**.
- A **central interpreter** reads the bus and issues directives (budgets, routes, retention, verification depth, adaptation eligibility).

The interpreter is the only thing subsystems share. This is a genuinely different object from both *"one vector everywhere"* (what v0.1 critiqued) and *"each service invents an ad-hoc float no one can audit"* (which loses the trail). It keeps the audit and the invariant while dropping the universal-algorithm baggage.

**The one design question this raises** — not a flaw, the real fork to decide: the interpreter must *process heterogeneous* salience to arbitrate across subsystems. Two ways to give it that handle — a thin normalization at the bus edge (each publisher emits a comparable influence magnitude + confidence + provenance + subsystem-id, keeping its rich internal scoring private), or per-subsystem adapters inside the interpreter. Either preserves "no shared scoring schema." What the interpreter cannot do is arbitrate a signal it has no handle on, so the bus contract is **thin but real**: enough to compare and combine, not enough to constrain how anyone scores. Deciding that thin contract *is* part of building the interpreter.

---

## Part 2 — What exists, and the two things that don't

> **Standing caveat, applied to every "exists" below:** this rests on documentation, not code. Your own `AGENTS.md` §19 records three doc-derived claims about the Hermes repo that were wrong until someone read source. Treat each mapping as a hypothesis to verify against `quorum_dispatch.py`, `run_agent.py`, CDMS `salience.py`, and the Salient-Tuning trainer.

**Already built (per docs):**

| SalienceOS concern | Realized by | Notes |
|---|---|---|
| Memory salience (score, tier, decay, retrieve, forget) | **CDMS-A** | Deterministic write-time salience; 0 GB VRAM; provenance-fenced; 9 red-team cycles. A working salience subsystem. |
| Routing + enforcement (salience influences, policy authorizes) | **Quorum guard on Hermes** | Single-choke-point `quorum_dispatch.py`; fail-closed; every model path converges on it. |
| Adaptation salience (weight up salient records offline) | **Salient-Tuning** | Loss improves on up-weighted records; behavioral payoff unproven (Finding C). |
| Agent host, tools, sandbox, delegation, cron, skills + curator | **Hermes** (Nous) | Mature; curator already enforces reversibility invariants (never delete, pin-exempt). |

**Not built anywhere — the two true builds:**

1. **The salience bus + central interpreter as an auditable contract.** Every subsystem above computes salience in isolation today; nothing collects it onto a shared bus and arbitrates it under one invariant with one audit trail. This is the integration, and it is the point — not plumbing to wave away as "already exists." *(Correcting v0.1's lean: the pieces existing separately is what makes this buildable; it is not what makes it redundant.)*
2. **The verifier.** Hermes has none; §7.9 has no realized counterpart anywhere in the constellation. The largest gap and the highest-leverage new work.

---

## Part 3 — Findings that still shape the design

### Finding A — Per-subsystem, additive salience is *support*, not a problem
`salient_by_design`'s Phase-0 probe found the runtime-memory salience axes >99.7% jointly independent — cross-terms add ~0; a diagonal additive sum reproduces the score at R²=0.997. Under v0.1's "one unifying vector" reading this looked like failure. Under your actual design it is direct evidence that **salience concerns are separable** — exactly what a per-subsystem, bus-plus-interpreter architecture predicts and needs. Keep salience additive within a subsystem and independent across subsystems; don't reintroduce cross-terms the data doesn't support. *(Confidence caveat, unchanged: Phase-0 was exploratory, on an inadequate ~1-D corpus, measuring CDMS's axes — not a universal proof. Safe claim: interaction structure is empirically unsupported where tested; additive-per-subsystem is the right default until shown otherwise.)*

### Finding B — The verifier is the one true build: deterministic + external, never model-on-model
The literature (Huang 2023: LLMs can't reliably self-correct; Stechly 2024: benefit comes from sound *external* checks; Zhou, *Nature* 2024: larger models give sensible-but-wrong answers supervisors miss) and your own §16 red-team (a verification script that scored differently from the harness it verified generated its own disagreement; four claims published-and-withdrawn in one pass) point the same way. Build it deterministic-first — tests, receipts, hashes, telemetry as ground truth; model review advisory, never status-changing (your AC-009 already says this). Build its *tests* with mutation discipline (break the invariant in source, confirm red) and two-field-agreement properties, "because the recurring failure is a fixture that cannot reach the wrong answer."

### Finding C — Adaptation is unproven behaviorally; keep the channels separate
CDMS tested "disposition produces distinguishable enacted behavior" to a pre-registered **FALSE** (memory channel; identity is per-history, not per-disposition). Salient-Tuning shows real loss improvement on up-weighted records (training channel) but no model-quality/behavioral/retention claim yet. Neither *falsifies* §13; both **constrain** it. Route disposition to the weight channel (LoRA), expect only recall-steering from memory, and don't claim durable behavioral adaptation until a behavioral/retention metric clears §13.3's "transfer beyond memorization" bar.

### Finding D — The interpreter must be the single fail-closed choke point
Your §16 catalogs how "salience influences, policy authorizes" fails *silently*: a private-mode test that passed on sorting arithmetic while the policy did nothing; a descriptor that bypassed every ceiling while disclosure stayed honest ("a disclosure-only check would have reported the system healthy"). The structural fix, already realized in `quorum_dispatch.py`: **one seam every directive converges on**, fail-closed defaults, enforcement independent of any UI ("removing the dashboard must not disable enforcement"). Your central interpreter *is* this seam — build it as the single choke point, not a filter re-applied per subsystem.

### Finding E — Hardware reality (about the box)
Bandwidth-bound (273 GB/s is the decode bottleneck; confirmed across independent reviews). The real unit is a GB10 with **~115–121 GB usable**, a **single NUMA node** (CPU-core pinning yes; memory-slot placement no). **Correction carried from v0.1/PT-1:** the "MoE×4-bit void / 35B≈72 GB" numbers are a *training-time* bitsandbytes fact; for *inference*, vLLM's NVFP4 path makes a 35B-A3B MoE ~18 GB to serve — §11.2's low-active-parameter MoE primary is memory-viable. Re-derive §11 for *multiple sequential passes per turn* (score → reason → verify, each bandwidth-bound) and decide **foreground vs always-on** — your practice of clearing the box for heavy runs argues §10.4's always-on framing is the part to revisit, not the headroom.

### Finding F — Cache cost is latency, not dollars (reframed)
Dynamic per-turn reconfiguration (context budget, tool scope, verification depth from salience) invalidates vLLM's prefix cache and forces re-prefill — a *latency/throughput* cost on a bandwidth-bound box, not the dollar cost Hermes worries about on cloud. Real, re-framed. Mitigation: treat salience-driven mid-conversation reconfiguration as the expensive exception (deferred/next-turn by default, opt-in immediate); push capability to skills injected as user messages, not schema changes. The bus/interpreter should prefer **between-turn** re-planning to **within-turn** churn.

### Finding G — Ledger is a per-stream decision; the bus is the audit surface
The bus is where the auditable contract lives. Quorum deliberately makes its decision feed non-durable and non-retaining (no payloads); §8 wants a hash-chained record of everything. Decide **per stream** what persists (decisions, hashes, receipts — durable) vs what's ephemeral (prompts, bodies, args). Your §9.3 already gestures at this (rationale codes + evidence refs, no CoT). Make it explicit — a total durable record is itself a liability.

### Finding H — Stack reality
The canonical engine is a stdlib-only, synchronous, zero-dependency Python `quorum_core` (mutation-tested to forbid async/deps), embeddable into one binary — the opposite of the doc's FastAPI + async + NATS + Postgres microservices. The host is a fork of Nous Hermes, not greenfield. Describe SalienceOS as sitting *on* `quorum_core` + Hermes + CDMS + Salient-Tuning, not as a rival stack. Inference is Ollama/llama.cpp/vLLM, not vLLM-only.

### Pruned to ancestry (explicitly out of scope)
The nervous-system MoE processor, physical expert tiles, spine fabric, and the Stage 0–5 / $11–55M roadmap are **not** SalienceOS. Custom silicon (roadmap Stage 4–5) is not a barrier you face; it's one you ignore. The only survivor is an **optional application** of the salience principle: a salience-weighted expert scheduler — richer than EPLB's load-only signal — developed against real routing traces on the one box now (record via `expert_map_record_path`, build the co-activation graph + promotion policy offline), that *could* drive real placement if a second Spark ever joins over ConnectX-7. It lives inside SalienceOS as "salience applied to the expert layer," carries **zero hardware commitment**, and is strictly optional to the thesis.

---

## Part 4 — Recommendations, prioritized

1. **Build the verifier first** (Finding B). Deterministic + external; model review advisory; mutation-tested. The one true gap and the highest leverage.
2. **Define the bus contract and build the interpreter as the single fail-closed choke point** (Findings D, G). Decide the thin envelope the interpreter needs (comparable influence + confidence + provenance + subsystem-id) without constraining per-subsystem scoring; prefer between-turn re-planning (Finding F); decide durable-vs-ephemeral per stream.
3. **A/B the salience stack against a deterministic-additive baseline early.** CDMS *is* that baseline and it works; add complexity (LLM scorers, any cross-term) only if it beats additive on a real, adequate corpus (Finding A).
4. **Keep memory and weight adaptation channels separate** (Finding C).
5. **Re-derive §11 for multi-pass, inference-quantized, foreground operation** on the actual GB10; validate on the unit via the S0-003 script rather than trusting cited numbers (Finding E).
6. **Rewrite the doc to sit on the existing stack** (Finding H) and to describe the mechanism as bus-plus-interpreter, retiring the canonical-vector / projection / single-envelope specifics to "one possible memory-subsystem scorer."
7. **Optional, later:** the salience-weighted expert scheduler as an application — trace now, EPLB target if a second Spark appears. Explicitly not a hardware commitment.

---

## Part 5 — Standing caveats

- **Doc-derived, not code-verified** (PT-6, unchanged): every "exists" claim needs source reading against the SalienceOS contracts. No analysis substitutes for it.
- **Directional bias corrected** (PT-5): v0.1 leaned "SalienceOS is redundant." v0.2 corrects to "the bus + interpreter + verifier are the contribution; the pieces existing separately is what makes them buildable." The risk now runs the other way — don't let the clean framing inflate confidence that the integration is easy. It isn't: the interpreter's heterogeneous-salience arbitration and the verifier are both real engineering.
- **Hardware ancestry excluded**: nothing from the nervous-system images enters this review except as the one optional expert-layer application above.
