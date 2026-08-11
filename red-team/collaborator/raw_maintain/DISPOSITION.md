# Deputy-seal CODE panel — disposition (ADR 0003 revisit #1b, `collaborator/maintain.py`)

5-vendor external panel on the SHIPPED maintenance-sandbox diff, then reproduce-before-accept on a live
host (Sparky, aarch64, bwrap 0.9.0, non-root uid 1000).

## Verdicts + cost

| model | verdict | cost |
|---|---|---|
| openai/gpt-5.1 | SERIOUS_FLAWS | $0.0418 |
| google/gemini-2.5-pro | SERIOUS_FLAWS | $0.1541 |
| anthropic/claude-opus-4.1 | SERIOUS_FLAWS | $0.5499 |
| x-ai/grok-4.5 | SERIOUS_FLAWS | $0.1269 |
| qwen/qwen3-max | SERIOUS_FLAWS | $0.0287 |
| **TOTAL** | | **$0.9014** |

All five returned SERIOUS_FLAWS, converging on ONE headline (setuid escape) plus grok's unique second
(/proc/pid/root). Both were REPRODUCED against the real sandbox and **refuted** — a 4-vs-1 disagreement
resolved by empirical test in favour of the lone precise dissent.

## Findings + disposition

1. **setuid-root escape via `--bind / /` without `nosuid`** — gemini/opus/qwen CRITICAL, gpt HIGH-ish;
   **grok: NON-FINDING.** Claim: exec a visible setuid-root binary (`su`/`mount`/`fusermount`/`pkexec`) to
   regain CAP_SYS_ADMIN and `umount` the mask. **REFUTED (live):** `NoNewPrivs: 1` — bwrap sets
   `PR_SET_NO_NEW_PRIVS`, so the kernel ignores setuid bits AND file capabilities on execve; the binaries
   ARE visible but cannot escalate. grok was correct; the majority missed `no_new_privs`.
   **Hardening applied (verify-not-trust):** the guard now ASSERTS `NoNewPrivs==1` and all four cap sets
   (Eff/Prm/Bnd/Amb) all-zero, so a future host/bwrap that failed to set them fails CLOSED. Pinned:
   `test_setuid_is_defanged_by_no_new_privs`.

2. **`/proc/<pid>/root` cross-mount-ns reach of a MASKED socket** — grok CRITICAL (unique). Claim: without
   `--unshare-pid`, host `/proc` is visible, so reach the UNMASKED host `docker.sock` via
   `/proc/<host-pid>/root/...`. **REFUTED (live):** both a same-uid host process and pid 1 give
   `PermissionError [Errno 13]` — bwrap runs the sandbox in a CHILD userns, and ptrace-access from a child
   userns to a parent-userns process is denied, so every host process's `/proc/<pid>/root` is EACCES. No
   `--unshare-pid` needed (host-process visibility kept for maintenance). Pinned:
   `test_host_proc_root_cannot_reach_a_masked_socket`.

3. **Abstract-namespace sockets / host-loopback TCP** (opus HIGH, qwen MEDIUM) — REFUTED by design: the
   fresh `--unshare-net` netns has its own abstract-socket namespace and its own loopback, so a host
   daemon's abstract socket and `127.0.0.1:2375`/`127.0.0.53` are unreachable. Already handled by netns.

4. **Blocklist / `network_isolated` oversell** (gpt HIGH) — DOCUMENTED residual, not a defect: the mask is
   an honest blocklist and the flag means "IP-network isolated (routeless netns)", accurate in both the
   sandbox and the fallback. ADR 0003 revisit #1b + the module docstring state the blocklist + async-
   laundering residuals explicitly.

5. **TOCTOU: deputy set changes between probe and run** (gemini HIGH) — partly a misread: the real run
   computes its OWN present-deputy set at wrap time (not the probe's) and the guard verifies those are
   masked. The residual micro-race (a daemon starting in the ms between wrap-compute and exec) is the same
   class as the documented blocklist residual; masking absent paths unconditionally was rejected (bwrap
   setup would fail on absent parent dirs → break the sandbox on hosts lacking a daemon).

## Net

Headline CERTIFIED after reproduction: the two "SERIOUS" escapes do not hold against the real
bwrap 0.9 + non-root baseline; the seal is un-removable as designed. One verify-not-trust hardening
(assert NoNewPrivs + all cap sets) applied and re-proven live. Remaining items are documented residuals.
