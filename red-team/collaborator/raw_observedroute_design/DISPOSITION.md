# Observed-route DESIGN panel — disposition (ADR 0005, ADR 0003 revisit #1c)

5-vendor external DESIGN panel on the PROPOSED forced-observed-egress-route, run BEFORE any code
(empirical-adversarial discipline: pressure-test the plan externally before building).

## Verdicts + cost + meta-answer

| model | verdict | worth building? | cost |
|---|---|---|---|
| openai/gpt-5.1 | SERIOUS_FLAWS | No — keep network-less (or a mediating proxy) | $0.0801 |
| google/gemini-2.5-pro | SERIOUS_FLAWS | No — network-less; pasta if network is required | $0.0978 |
| anthropic/claude-opus-4.1 | SERIOUS_FLAWS | No — keep network-less | $0.4198 |
| x-ai/grok-4.5 | SERIOUS_FLAWS | No (except dedicated break-glass hosts with fixes) | $0.0961 |
| qwen/qwen3-max | SERIOUS_FLAWS | No — keep network-less | $0.0271 |
| **TOTAL** | | | **$0.7208** |

**Unanimous: 5/5 SERIOUS_FLAWS, 5/5 "not worth building — keep maintenance network-less."**

## Load-bearing reasons (consensus)

1. **Demotes the sole-IP-path guarantee.** Routeless made `egress.py` the sole IP path; the observed
   route is a second IP path that logs dest ip:port but not content — a human-approved shell could POST
   to a non-allowlisted host, observed-but-not-blocked. "Observed, not mediated" is a weaker posture than
   the rest of the egress model (net.get/net.post caps + content).
2. **Incomplete observation → false "observed".** (a) IP fragments carry no L4 header → a `meta l4proto`
   FORWARD hook logs the first fragment and forwards the rest unlogged while the guard emits a positive
   token (qwen, grok). (b) Masquerade is POSTROUTING, after the FORWARD verdict → FORWARD-accept does not
   prevent pivoting to LAN / docker nets / cloud metadata `169.254.169.254` (grok F-03); INPUT-drop only
   protects host-local sockets, not east-west FORWARD.
3. **Fragile privileged TCB.** A root helper that ultimately execs the payload needs a parent/child split
   (no exec-after-drop in one process), strict sudoers (no arg injection), CSPRNG per-run names, NNP-
   before-drop, FD_CLOEXEC, verified drop, netns/setns race + symlink hardening on `/run/netns/`.

## Disposition

Operator accepted the panel: **do NOT build the observed route.** Maintenance stays network-less; the
PR-#42 deputy seal already made that state sound (`egress.py` sole IP path, deputies closed). ADR 0005
recorded REJECTED (design preserved for the record). If network-for-maintenance ever becomes a hard
requirement, revisit as a **content-mediating proxy** (SNI/allowlist at egress.py fidelity), not a
dest-only route. Next: the residual hardening sweep (net.put/net.delete verbs, per-destination emission
quota, F2 shared-workspace dropper).

This is the design-panel-before-build rule paying off: a large privileged build was declined on
independent evidence BEFORE any code, and a real erosion of the sole-path guarantee was avoided.
