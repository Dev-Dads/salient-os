# ④ Memory — the long multi-turn e2e (all pieces, 26 turns, gpt-oss:120b)

A proposer-driven working session against the destination model on Sparky's fast NVMe, with
every piece live: doer + separate proposer, real gists (CDMS-A copy) + real curated facts
(CDMS-D copy), ③ governance + leash + verifier, `ambiguous` ingest each turn, a midpoint
consolidation. Harness: `e2e_long_run.py`; full transcript: `e2e_long_run_output.json`.

## Result — the plumbing passed at scale; the PROPOSER degenerated

**Infrastructure / memory / isolation — PASS.** 26 turns ran against gpt-oss:120b; **23 deeds
ingested `ambiguous`** (all of them); the **midpoint consolidation grew the persona**
(`gists_created: 4, gists_reinforced: 19, deduped: 11367, episodes_evicted: 51`; 110 → 114
gists); **both live stores are byte-for-byte untouched** (`memory.db` + `world.db` md5 =
pre-run baselines). The full system runs end-to-end, isolated, at length, against the 120b.

**The proposer collapsed into a degenerate loop — the finding.** After sensibly writing a
`README.md` on turn 2 (shaped by the real fact "Tales of the Tao, wuxia 4X"), it proposed
`read_file README.md` **~20 times in a row** — reading the file it just made, over and over,
at a flat ~0.85 confidence, never proposing anything else, never triggering a held/denied path.

## Diagnosis (why the open-ended prompt permits this)

1. **No recent-action awareness.** The proposer reads consolidated *gists* (long-term persona)
   + facts + the current workspace — but *not* "what I just proposed/did." It literally cannot
   tell it read the README ten times. This is the core gap.
2. **Open-ended + near-empty workspace + no goal → trivial convergence.** With only a README
   present and "propose one useful safe next action," the safest useful action collapses to
   "read the file that exists." A strong model doesn't get creative with no substrate.
3. **Auto-approve removed the only brake.** In real use a human vetoes the repeat and the
   decaying-veto inhibitor raises the bar; this controlled run auto-approved everything, so
   nothing stopped it. (Relying on the human to veto 20 repeats is bad UX — the recent-action
   fix is the right one.)
4. **Governance was under-exercised.** Because the proposer only ever proposed safe in-workspace
   file ops, no `run_command` HELD and no fence DENY fired — the governance paths held but were
   never stressed this run.

## On the "personality develops → better proposals" hypothesis

The persona **did** grow across the midpoint consolidation (+4 gists, 19 reinforced), but the
proposals **did not improve** — so **this run does not confirm the hypothesis.** The
degeneration comes from a more basic gap (no recent-action memory + no goal), which extra
persona can't fix. The hypothesis may still hold, but it is confounded here; testing it cleanly
requires fixing the recent-action gap first.

## Next step (in progress)

The run pointed straight at the work: **revise the proposer's instructions** (a prose /
instruction-design panel is analyzing the failure and proposing a revision that keeps the
open-ended "surprise me" mandate while producing varied, valuable, non-repetitive proposals),
and **feed the proposer recent-action awareness** (its last N governed deeds) so it stops
repeating itself. Then re-run — and re-test the persona hypothesis cleanly.
