# Resource Governor v0 — live demo synthesis

**The agent yields real GPU compute to the human's foreground activity.** Where the
salience compute knob (H1–H3) governs *permitted effort in loop steps*
(`max_iterations`), this governor governs *real compute share* on a machine the agent
shares with a human. On the destination single box — the two-layer vision's "lived
desktop" + "governed layer" — the human games on the same GPU the agent thinks on. An
agent pegging that GPU while the human plays is exactly the failure the governed layer
exists to prevent. So the governed layer **yields**: drops to a cheaper model, paces,
or unloads the big model, to defer to the human.

This is **P-01 at the hardware level**: the human's foreground demand is the
**authority** that caps the agent (it can only make the agent yield *more*); salience
only **influences** how aggressively the agent uses whatever slack the human leaves. It
mirrors the control seam's `required = max(depth, stakes_floor)` idiom, inverted in
direction — a ceiling the world may lower, versus a floor salience may raise. It is the
compute analog of the verifier's Verdict: a second, non-salience, real-world input,
consumed at a seam, that can only make the outcome *more* restrictive.

## Why this demo exists

A live 25-turn run against gpt-oss:120b on Sparky (GB10 DGX Spark) showed the DGX
dashboard pinned at ~94–95% GPU on nearly every turn, while the iteration budget never
even bound. **Iteration count is not compute cost.** Josh's framing: "the GPU
utilization would still read ~95% throughout either way — that's a problem for a gamer."
This governor answers that: it governs the thing that actually competes with the human.

## What we measure (real cost, not iteration count)

- **GPU availability** — util % + **power draw (watts)** + SM clock, read from Sparky's
  NVML via a tiny `gpu-probe` endpoint (`box/sparky_gpu_probe.py`, :11502), tunneled to
  the box. This is exactly what the DGX dashboard reads, so the numbers match what Josh
  watches. On GB10's unified memory, VRAM/per-process telemetry is N/A, so **power draw
  is the real compute-intensity proxy**.
- **The lever is voluntary.** GB10 exposes no forcible GPU cap (power-limit / clock-lock
  / MIG all N/A, probed read-only). So the governed layer yields by choosing a cheaper
  model / pacing / unloading — the philosophically correct "governed layer defers to the
  lived desktop" model, not a hardware clamp.

## The live trajectory (real hardware, Sparky)

Driven by `demo_resource_governor.py` against the **real** governor, the **real** Sparky
GPU, and the **real** ollama lever (model swap + `keep_alive:0` unload on the :11500
NVMe instance). The "game" is a second ollama model (qwen2.5:7b) generating in a loop —
a genuine GB10 GPU consumer, started/stopped over ssh.

| Step | Scenario | Pressure | Salience | Tier | agent.model | GPU util | gpt-oss loaded? |
|------|----------|---------:|---------:|------|-------------|---------:|-----------------|
| **S1** | agent alone (foreground off) | 0.00 | 0.0 | **FULL** | gpt-oss:120b | **96%** peak | ✅ loaded |
| **S2** | human sits down (foreground on), GPU idle | 0.60 | 0.0 | **LIGHT** | llama3.2:3b | 0% | ❌ **unloaded** |
| **S3** | game hammering GPU + **high** salience | 0.93 | 0.9 | **LIGHT** | llama3.2:3b | 93% | ❌ unloaded |
| **S4** | same heavy GPU + **low** salience | 0.93 | 0.0 | **PAUSE** | llama3.2:3b (+3s pace) | 93% | ❌ unloaded |
| **S5** | human leaves (foreground off, game off) | 0.00 | 0.0 | **FULL** | gpt-oss:120b | 0% | restored* |

Self-check: `tiers = [full, light, light, pause, full]` == expected. **TRAJECTORY OK.**

**What each step proves:**

- **S1 — the baseline Josh named.** A *real governed turn* ran end-to-end: gpt-oss:120b
  answered the prompt ("*'Salience influences' means the system first notices or
  prioritizes information…*") while the live GPU sample peaked at **96% util / 32.7W**.
  Governor: FULL. This is the agent legitimately using the whole GPU when no human
  competes for it.
- **S2 — presence alone makes it yield.** The instant the foreground flag goes on — even
  with the GPU still idle — the `foreground_min_pressure=0.6` floor pushes pressure to
  0.60 → **LIGHT**. The agent swaps to llama3.2:3b and **gpt-oss:120b physically unloads
  off the GPU** (`ollama ps` drops it). The agent steps aside *before* the game even
  spins up.
- **S3 — importance buys slack, never the human's frames.** Now the game pegs the GPU to
  93%. Under the same pressure a low-salience task would PAUSE, but salience 0.9 supplies
  relief 0.9×0.4 = 0.36, so effective pressure = 0.93 − 0.36 = 0.57 → **holds LIGHT**.
  Important work stays alive and responsive; it does *not* preempt the human (still on
  the small model, big model still unloaded).
- **S4 — trivial work fully yields.** Same 93% GPU, but salience 0.0 → effective pressure
  0.93 ≥ 0.75 → **PAUSE**: small model + big model unloaded + 3s pace. The agent's GPU
  footprint is minimal; the 93% on the dashboard is now *the human's game*, not the agent.
- **S5 — restore.** Human leaves, pressure returns to 0 → **FULL**, `agent.model`
  restored to gpt-oss:120b.

\* **S5 honesty note:** `gpt_oss_loaded` reads `false` in the S5 snapshot because FULL
re-*selects* gpt-oss:120b but ollama reloads it **lazily on the next generation**, not at
tier-change time. The tier and `agent.model` are restored; the weights re-page on the
next turn (the S1 turn already showed that reload path working).

## Honest scope — real vs. supplied

**Real in this demo:** the tier decision (`decide_tier`), the GPU measurement (NVML via
gpu-probe), the model swap on the live agent, and the gpt-oss:120b unload/reload you can
watch in `ollama ps` and on the DGX dashboard.

**Supplied (scenario inputs, not measured):**
- **The foreground flag** — stands in for "the human is at the machine / a game is up."
  On the single-box destination this becomes OS foreground detection (a real
  fullscreen/high-GPU app). The two-box demo can't detect a real game on Sparky, so it's
  toggled.
- **The salience value** (0.9 / 0.0) — passed directly so each step is reproducible. In
  production it comes from the H3 directive's `compute_budget` via `normalize_salience`
  (`(budget − floor) / headroom`, clamped) — already unit-tested. Here 0.9 ≈ a budget of
  38–39 in the box's [25, 40] window; 0.0 ≈ the floor.

**Two more honest notes:**
- **Both LIGHT and PAUSE unload the big model** (`plan_actions`: both `unload=[full]`,
  swap to `fast`). The physical footprint collapse happens at LIGHT; PAUSE *additionally*
  paces the turn cadence (3s) for near-idle. That is why gpt-oss stays unloaded across
  S2→S4.
- **Voluntary yield, not a hardware clamp** — see above; GB10 has no forcible GPU cap, so
  this is application-level tiering/pacing/unloading, which is the correct model anyway.

## Verification

- **34 unit tests** on the pure policy (`decide_tier` / `foreground_pressure` /
  `plan_actions` / `normalize_salience`), mutation-verified: floor-bounded (never below
  LIGHT unless `allow_pause` + low salience), monotonic in pressure, salience holds a
  higher tier under equal pressure, bad/missing GPU reads **fail open to FULL** (never
  brick the resident). ruff + ty clean.
- **This live demo** on Sparky: the trajectory above, ground-truthed against the same
  NVML the DGX dashboard reads.

## Reproduce

```
# tunnels: -L 11500:127.0.0.1:11500 (ollama-nvme) and -L 11502:127.0.0.1:11502 (gpu-probe)
# from the fork repo root:
python box/demo_resource_governor.py out.jsonl
# env (defaults target the Sparky tunnels): BOX_MODEL, BOX_FAST_MODEL,
# BOX_OLLAMA_OPENAI_URL, BOX_OLLAMA_NATIVE_URL, BOX_GPU_METRICS_URL, SPARKY_SSH
```

## Files

- `governor_trajectory.jsonl` — the 5-step trajectory, one JSON record per step.
- `live_demo_output.txt` — the driver's console log (the run above).
- `demo_resource_governor.py` — the demo harness (copy of the fork's `box/`).

**Deferred to v1:** OS foreground detection (single-box); a signed `PolicyCaps`
foreground-yield field as the principled "policy authorizes" home; richer per-task
salience arbitration. Core untouched (H3 pattern) — the governor lives entirely
host-side in the box.
