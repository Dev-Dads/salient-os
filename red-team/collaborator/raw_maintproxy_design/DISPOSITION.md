# DISPOSITION — maintproxy DESIGN panel (ADR 0006)

5-vendor external design panel on the PROPOSED maintenance egress proxy (destination-allowlist CONNECT
forward proxy). **Verdict: 5/5 SERIOUS_FLAWS.** Cost: **$0.7534** (gpt-5.1 $0.0946, gemini-2.5-pro
$0.1005, opus-4.1 $0.4235, grok-4.5 $0.1113, qwen3-max $0.0235).

## The headline finding — and a 4-vs-1 split I reproduced LIVE before accepting

**"The netns default-deny nft rules are editable by the in-netns shell → mediation is theater."**
- gpt-5.1, gemini, opus-4.1, qwen: **CRITICAL**. The maintenance shell is bwrap mapped-root and holds
  CAP_NET_ADMIN in its own netns; the default-deny lives in that same netns; `nft flush ruleset` bypasses.
- **grok-4.5 DISSENTED (HIGH, conditional-CRITICAL):** the threat model is *wrong as written*. nft/netlink
  admin requires `ns_capable(net->user_ns, CAP_NET_ADMIN)`. A netns created by **real root (init userns)**
  has `net->user_ns == init_user_ns`; a process in bwrap's **child** userns is NOT capable in init_user_ns,
  so `nft flush` → EPERM. Un-editable **iff** the netns is init-userns-owned and never re-created inside
  the sandbox userns. The other four assumed the `--map-root-user` (child-userns-owned) model of netns.py.

### Reproduced on Sparky (aarch64, live), 2026-08-12 — `scratchpad/crux.sh`
- **Test A (ADR 0006 model — real-root helper creates the netns):** bwrap child-userns mapped-root shell,
  `CapEff=0000000000000000`. `nft flush ruleset` → **`Operation not permitted` (rc=1)**; `nft list
  ruleset` → **`Operation not permitted (you must be root)`** (can't even READ the ruleset); the
  default-deny rules **persist** after the shell exits. **UN-editable.**
- **Test B (contrast — `unshare --map-root-user --net` self-created netns, netns.py model):**
  `CapEff=000001ffffffffff` (full caps in own userns), `nft flush ruleset` rc=0, `ip link add` rc=0.
  **Editable.**
- **Conclusion:** netns OWNERSHIP is decisive. grok is right; the four CRITICALs over-called on an
  ownership assumption. The design's mechanism is **not theater** — BUT the ownership invariant was
  under-specified in the ADR (a real, durable trap: a refactor that "reuses netns.py's --map-root-user"
  silently re-opens it).

## What survives as genuine, must-fix design changes (unanimous / cross-model)
1. **Host-side veth nft filter as the PRIMARY enforcement** (all 5): drop from `iifname veth_host` except
   to the proxy ip:port, in the HOST netns — unreachable by the sandbox regardless of userns ownership.
   In-netns default-deny becomes the secondary belt. Also the robust floor on `ip_forward=1`/Docker hosts
   where in-netns rules are genuinely load-bearing (grok F-01, F-02).
2. **Assert the init-userns netns-ownership invariant** normatively + in the guard (grok F-01 fix #1).
3. **Helper TCB hardening** (gpt ID4, grok F-03, gemini F2, qwen #2): setns via an **O_PATH|O_NOFOLLOW fd**,
   not a path (TOCTOU on /run/netns/<name>); fixed sudoers (absolute path, no wildcard, `--run-id=[a-f0-9]{32}`);
   privilege-drop order setgroups→setgid→setuid + verify + NNP before exec; CLOEXEC/closefrom all fds
   (a leaked root netlink/netns fd = full bypass); parent-only teardown; env scrub (LD_*/NFT_*/IPTABLES_*).
4. **Proxy bind scope**: bind ONLY the veth host IP (never 0.0.0.0) + `SO_BINDTODEVICE` + host filter so it
   isn't a host-wide/LAN open proxy (gpt ID5, grok F-06, gemini F4, opus #2).
5. **CONNECT parse hardening**: strict grammar, reject IP-literal authorities for net.maint, header budget +
   timeout (slowloris), drain-to-`\r\n\r\n` and never interpret post-CONNECT bytes as HTTP (smuggling),
   443-only (gpt ID6, grok F-05, gemini F3).
6. **SSRF deny**: reject pinned IPs in the veth /31, host-secondary IPs, loopback even if is_global passes
   (gpt ID8/9, grok F-06, qwen #3).
7. **Byte/duration caps** per connection+session (not just idle timeout) to bound exfil even without MITP
   (gpt ID11, grok F-07, gemini F5).
8. **Fail-closed ladder**: never run bwrap WITHOUT --unshare-net when the helper failed (else host-netns
   shell with full FS — worse than today); guard proves the PREPARED netns (inode==helper value, not
   "!=host"); per-run guard mandatory even on warm cache; canary = a known-unroutable TEST-NET addr
   (grok F-04/F-10, gpt ID12, gemini). 

## The strategic verdict — 4/5 "do the alternative" for the STATED use case
- **grok, gpt, qwen, opus:** for the stated trigger (fetch non-vendorable artifacts — proprietary drivers,
  licensed binaries), the better trade is a **mediated `maint_fetch` tool through egress.py** (bounded,
  hashed, audited GET → writes the artifact into the workspace; shell stays routeless), NOT a privileged
  shell proxy that adds a root-helper TCB AND demotes egress.py's content guarantee to a raw pipe for the
  maintenance path (F-09/F5/ID11 — "where not what" is a real semantic demotion).
- **gemini:** the lone "worth building — once F1 (host-side filter) is fixed."
- The proxy earns its TCB only if **interactive networked maintenance** (apt/git/pip driven IN the shell)
  is a hard requirement — a bigger capability than the stated artifact-fetch need.

## Outcome
Reproduced the crux (design is fixable, not theater) → surfaced the real fork to the operator:
**maint_fetch (serves the stated need, small, keeps guarantees) vs. the hardened interactive proxy**.
Awaiting operator steer before build. If the proxy is chosen, the redesign above is normative.
