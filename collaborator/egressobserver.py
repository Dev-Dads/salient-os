"""Independent egress observer — ADR 0003 revisit #1b ("hands can't lie" for egress).

Today's egress audit is CHANNEL-INTEGRITY LOGGING, not verification: `collaborator/egress.py` both MAKES
and RECORDS each request, so the observer *is* the executor. A compromised/buggy egress client — or a SECOND
in-process HTTP client that never goes through `egress.py` — can log a benign destination while bytes leave
elsewhere, and netns does not catch it (it only proves bytes left *some* socket in the namespace, never that
they match the record's claimed dest). This module adds an INDEPENDENT vantage: it reads the KERNEL's own
account of which destinations the Collaborator's uid actually connected to, from OUTSIDE the egress client,
and the seam reconciles that against the `EgressRecord` — the same "re-observe the world after the executor
finished" property `salienceos/verifier/observers.py` gives the filesystem verifier.

Two tiers, probed like `netns.netns_available()` / `contained.containment_available()`, honest flag either way:

  * STRONG (`nft`, privileged) — a PASSIVE nftables OUTPUT hook, scoped to the Collaborator's uid
    (`meta skuid`), records every NEW outbound TCP connection's ``ip daddr . dport`` into a dynamic set +
    a counter. Read-only before/after snapshots around an egress action → the set of destinations THAT
    action reached, race-free (the kernel sees every SYN) and attributable (uid-scoped). The client cannot
    remove or forge a root-installed rule. Requires privilege to install AND read (root, or passwordless
    `sudo` on the bare deploy) — a genuine "observer outside the executor".
  * FALLBACK (`/proc/net/tcp`, unprivileged) — sample the Collaborator uid's own live sockets around the
    call. Point-in-time (a sub-sample connection can be missed) and no byte counts, but needs no privilege.
  * UNAVAILABLE — non-Linux / no nft+privilege / no /proc → ``egress_observed=False``. Never a fake claim.

STRICTLY SCOPED, fail-safe blast radius: this module ONLY ever creates/deletes its OWN reserved, uid-scoped
``table inet salient_obs_u<uid>`` (via `nft delete table`, error-tolerant) — it NEVER runs `nft flush ruleset`
or touches any other table, and the uid-scoped name cannot collide with an operator's real firewall table, so
it cannot disturb a host firewall. The rule is `policy accept` and purely observational (`update`+`counter`),
so it never drops a packet. Linux-only, stdlib-only, NO new dependency (nft is a system binary).

Reconciliation is EVIDENCE, not a gate (P-01: it INFORMS/flags, never grants): an observed destination the
record does not explain raises `egress_reconciled=False` + a discrepancy note; a clean strong-tier match is
the "hands didn't lie" upgrade. Confinement so nothing CAN egress unobserved (an enforced netns chokepoint)
is the deferred full-parity follow-up; this observes, it does not enforce.

RESIDUALS (honest — the strong tier OBSERVES the Collaborator uid's outbound TCP to non-loopback dests, per
action; these evade it and are the enforced-chokepoint follow-up's job, NOT v0 defects):
  * A second channel on a DIFFERENT uid (a setuid/helper) — the hook is uid-scoped by design.
  * A LOOPBACK / local forward proxy (a client → 127.0.0.1 → the proxy egresses): the loopback hop is
    excluded (to avoid noise from legit localhost services), and the proxy's onward hop is a different uid.
    The same residual netns lists (a "local forward proxy").
  * UDP / DNS / QUIC exfil — the hook is TCP-only (matching UDP would flag every DNS lookup as noise).
  * Egress whose bytes fall ENTIRELY BETWEEN windows (a connection that neither opens nor sends during any
    observed action) — per-action windows only cover the action; continuous observation is the follow-up.
  * A ROOT-compromised client that flushes/edits the kernel state — the root-compromised-host residual (same
    stance as the bwrap CVE work).
  * Same-uid CO-TENANT egress (another honest process of the uid) → a false-positive discrepancy, not a miss.
    The all-outbound-TCP rule (needed to catch a reused pre-established connection) WIDENS this vs a SYN-only
    rule: any same-uid connection with traffic in the window is observed, so on a SHARED-uid host (a busy CI
    runner) a legit egress can reconcile False. On the real deploy the Collaborator's uid is dedicated, so
    this is quiet; either way it's EVIDENCE (a loud ⚠), never a deny, and never a miss.
  * A same-uid SYN in the narrow delete-then-create window INSIDE `begin()` (not the fetch body): the hook is
    up before `begin()` returns, so this is a setup-race miss only, under the serial-executor assumption.
  * PORT policing is egress.py's job, not the observer's: it reconciles ``(ip, port)`` exactly as claimed
    (`_egress_claimed` pins :443) and does not independently enforce the HTTPS-only contract — a record that
    lied about the PORT to an already-claimed IP is egress.py's contract to keep, out of this vantage's scope.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

COLLABORATOR_EGRESSOBSERVER_VERSION = "0.1.0"

# The ONE table this module ever creates or deletes — a RESERVED, uid-scoped name so it can never
# collide with (and therefore never clobber) an operator's real firewall table. Teardown is
# `nft delete table inet <this>` — scoped, error-tolerant, NEVER `flush ruleset`, so a host firewall is
# untouchable by this observer. The uid suffix (external-panel qwen) makes the idempotent per-action
# reinstall unable to delete a foreign same-name table; it stays stable across a process's restarts (same
# uid → same name → cleans only OUR own leftover), unlike a pid suffix which would leak a table per run.
_UID_AT_IMPORT = os.getuid() if hasattr(os, "getuid") else -1
_NFT_TABLE = f"salient_obs_u{_UID_AT_IMPORT}" if _UID_AT_IMPORT >= 0 else "salient_obs"

# Honest tier labels (mirrors the netns/contained honest-flag posture).
TIER_STRONG = "nft"        # privileged kernel OUTPUT hook — race-free, uid-attributed, unbypassable
TIER_PROC = "proc"         # unprivileged /proc/net/tcp sampling — point-in-time, no bytes
TIER_UNAVAILABLE = "none"  # no vantage on this host → egress_observed=False


def _resolve(candidates, name: str) -> str:
    """Absolute path to a system binary, resolved at import (before any run_command could plant a shadow),
    identical in spirit to netns._resolve. Requires an EXECUTABLE regular file: a present-but-non-exec
    binary, or a `sudo` wrapper that ignores `-n` and prompts, would otherwise let `_run_nft` block until
    its timeout on every call (external-panel fix gpt-F3). shutil.which already only returns executables."""
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return shutil.which(name) or candidates[0]


_NFT_BIN = _resolve(["/usr/sbin/nft", "/sbin/nft", "/usr/bin/nft"], "nft")
_SUDO_BIN = _resolve(["/usr/bin/sudo", "/bin/sudo"], "sudo")

# nft needs root/CAP_NET_ADMIN. As root, call it directly; otherwise go through NON-INTERACTIVE sudo
# (`sudo -n`) so a host without passwordless sudo fails the availability probe (→ fallback) rather than
# blocking on a password prompt. Decided once at import.
_NFT = ([_NFT_BIN] if os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0
        else [_SUDO_BIN, "-n", _NFT_BIN])

_available_tier = None  # cached host-property probe (None = not yet probed)


def _uid() -> int:
    return os.getuid() if hasattr(os, "getuid") else -1


def _canon_ip(ip) -> str:
    """Canonical (compressed) IP text so the record's resolved_ip and the kernel's rendering compare equal
    regardless of textual form (esp. IPv6). Fail-safe: an unparseable value is returned unchanged."""
    import ipaddress
    try:
        return ipaddress.ip_address(str(ip)).compressed
    except (ValueError, TypeError):
        return str(ip)


def _nft_ruleset(uid: int) -> str:
    """The passive, uid-scoped OUTPUT observer. Both address families; EVERY outbound TCP packet (NOT just
    the SYN) `update`s a dynamic set of ``daddr . dport`` + bumps a counter; `policy accept` (never drops).

    Matching every packet (not only the SYN) is the fix for the pre-established-connection evasion (external
    panel F-01): a second in-process client that reuses a connection opened BEFORE this window still sends
    data packets DURING it, and each one adds its destination to the set — so it cannot exfil unobserved
    while a benign new fetch reconciles clean. (A connection that is idle for the whole window sends nothing
    to observe; a connection whose bytes fall entirely BETWEEN windows is the documented between-windows
    residual the enforced chokepoint closes.)"""
    return f"""table inet {_NFT_TABLE} {{
  set dests4 {{ type ipv4_addr . inet_service; flags dynamic,timeout; timeout 10m; }}
  set dests6 {{ type ipv6_addr . inet_service; flags dynamic,timeout; timeout 10m; }}
  counter pkts {{}}
  chain out {{
    type filter hook output priority 0; policy accept;
    meta skuid {uid} ip  daddr != 127.0.0.0/8 meta l4proto tcp \
      update @dests4 {{ ip daddr  . tcp dport }} counter name pkts
    meta skuid {uid} ip6 daddr != ::1         meta l4proto tcp \
      update @dests6 {{ ip6 daddr . tcp dport }} counter name pkts
  }}
}}
"""


def _run_nft(args, *, stdin: "str | None" = None, timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run([*_NFT, *args], input=stdin, capture_output=True, text=True,
                          timeout=timeout, check=False)


def install() -> bool:
    """Install the passive observer table (idempotent: delete-then-create ONLY our table). True on success.
    Privileged. A failure (no nft, no privilege, non-Linux) returns False — the caller then has no strong
    vantage and falls back / flags honestly."""
    if sys.platform != "linux":
        return False
    uid = _uid()
    if uid < 0:
        return False
    try:
        _run_nft(["delete", "table", "inet", _NFT_TABLE])          # error-tolerant; ignore result
        r = _run_nft(["-f", "-"], stdin=_nft_ruleset(uid))
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def teardown() -> None:
    """Delete ONLY our table (scoped, error-tolerant). NEVER `flush ruleset`."""
    if sys.platform != "linux":
        return
    try:
        _run_nft(["delete", "table", "inet", _NFT_TABLE])
    except (OSError, subprocess.SubprocessError):
        pass


@dataclass(frozen=True)
class EgressSnapshot:
    """An independent-vantage reading: the set of ``(ip, port)`` destinations observed for the Collaborator's
    uid, plus a monotonic new-connection counter (strong tier only), tagged with the tier that produced it."""
    dests: frozenset = field(default_factory=frozenset)   # frozenset[tuple[str, int]]
    conn_count: int = 0
    tier: str = TIER_UNAVAILABLE

    def observed(self) -> bool:
        return self.tier != TIER_UNAVAILABLE


def _elem_concat(elem) -> "list | None":
    """Best-effort extract the ``[ip, port]`` concat from ONE nft set element, tolerating both the bare
    (`{"concat":[…]}`) and the counter-wrapped (`{"elem":{"val":{"concat":[…]}}}`) shapes. Returns None if
    the element does not carry a 2-tuple concat. Total — never raises on an unexpected shape."""
    val = elem.get("elem", elem) if isinstance(elem, dict) else elem
    inner = val.get("val", val) if isinstance(val, dict) else val
    concat = inner.get("concat") if isinstance(inner, dict) else None
    return concat if (isinstance(concat, list) and len(concat) == 2) else None


def _parse_nft_set(json_text: str) -> "set | None":
    """Parse `nft -j list set` output into ``{(ip, port)}``. Returns **None** on ANY parse/structural failure
    so the caller treats a failed read as UNCHECKED, never as a clean EMPTY observation — the fail-closed
    posture that stops a false "verified" when a real connection went unparsed (external-panel fix qwen
    ID-03). None is returned when: JSON is unparseable; the top level isn't a dict; NO ``set`` object appears
    (nft always emits exactly one for `list set`); ``elem`` is present but not a list; OR an element is
    present that does NOT decode to a valid ``(ip, port)`` — a present-but-undecodable element (nft JSON
    schema skew, or an injected non-concat element) is UNTRUSTWORTHY, because the element we dropped could be
    the very destination a discrepancy would flag. Only a ``set`` object with a genuinely EMPTY element list
    returns an empty set — the one legitimate "observed nothing" reading."""
    import json
    try:
        doc = json.loads(json_text)
    except (ValueError, TypeError):
        return None
    if not isinstance(doc, dict):
        return None
    out: set = set()
    found_set = False
    for obj in doc.get("nftables", []):
        s = obj.get("set") if isinstance(obj, dict) else None
        if s is None:
            continue
        found_set = True                       # nft emitted the set object; its element list is the truth
        elems = s.get("elem", [])
        if elems is None:
            elems = []
        if not isinstance(elems, list):
            return None                        # 'elem' present but not a list => schema mismatch, UNCHECKED
        for elem in elems:
            concat = _elem_concat(elem)
            if concat is None:
                return None                    # element present but undecodable => UNCHECKED (fail closed)
            try:
                out.add((str(concat[0]), int(concat[1])))
            except (ValueError, TypeError):
                return None                    # a malformed ip/port in a real element => UNCHECKED
    return out if found_set else None          # no set object => a schema mismatch/failure, NOT "empty"


def _nft_snapshot() -> "EgressSnapshot | None":
    """Read both dest sets + the counter as JSON. None if the read fails (table gone / lost privilege)."""
    import json
    dests: set = set()
    try:
        for setname in ("dests4", "dests6"):
            r = _run_nft(["-j", "list", "set", "inet", _NFT_TABLE, setname])
            if r.returncode != 0:
                return None
            parsed = _parse_nft_set(r.stdout)
            if parsed is None:                 # a parse/schema failure is UNCHECKED, never a clean empty
                return None
            dests |= parsed
        rc = _run_nft(["-j", "list", "counter", "inet", _NFT_TABLE, "pkts"])
        count = 0
        if rc.returncode == 0:
            try:
                doc = json.loads(rc.stdout)
                for obj in doc.get("nftables", []):
                    c = obj.get("counter") if isinstance(obj, dict) else None
                    if c:
                        count = int(c.get("packets", 0))
            except (ValueError, TypeError):
                count = 0
        return EgressSnapshot(dests=frozenset(dests), conn_count=count, tier=TIER_STRONG)
    except (OSError, subprocess.SubprocessError):
        return None


def _hex_to_endpoint(rem: str) -> "tuple[str, int] | None":
    """Parse a /proc/net/tcp{,6} remote address 'HEXIP:HEXPORT' into (dotted-or-colon ip, port)."""
    try:
        hexip, hexport = rem.split(":")
        port = int(hexport, 16)
        if len(hexip) == 8:  # IPv4, little-endian per word
            b = bytes.fromhex(hexip)
            ip = ".".join(str(x) for x in reversed(b))
        elif len(hexip) == 32:  # IPv6, per-32-bit-word little-endian
            import ipaddress
            words = [hexip[i:i + 8] for i in range(0, 32, 8)]
            raw = b"".join(bytes(reversed(bytes.fromhex(w))) for w in words)
            ip = str(ipaddress.IPv6Address(raw))
        else:
            return None
        return (ip, port)
    except (ValueError, TypeError):
        return None


def _proc_snapshot() -> "EgressSnapshot | None":
    """Unprivileged fallback: the Collaborator uid's live remote TCP endpoints from /proc/net/tcp{,6}
    (states ESTABLISHED=01 or SYN_SENT=02), excluding loopback. Point-in-time; no byte/conn accounting."""
    uid = _uid()
    dests: set = set()
    found = False
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path, encoding="ascii", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        found = True
        for line in lines[1:]:
            f = line.split()
            if len(f) < 8:
                continue
            st = f[3]
            row_uid = f[7]
            if st not in ("01", "02"):      # ESTABLISHED / SYN_SENT — an active outbound connection
                continue
            if row_uid != str(uid):
                continue
            ep = _hex_to_endpoint(f[2])     # rem_address
            if ep and not ep[0].startswith("127.") and ep[0] not in ("::1", "0.0.0.0", "::"):
                dests.add(ep)
    if not found:
        return None
    return EgressSnapshot(dests=frozenset(dests), conn_count=len(dests), tier=TIER_PROC)


def snapshot() -> EgressSnapshot:
    """Read the current independent observation at the best available tier. Never raises — an unreadable
    vantage degrades to the next tier and finally to an honest UNAVAILABLE snapshot."""
    if sys.platform == "linux":
        snap = _nft_snapshot()
        if snap is not None:
            return snap
        snap = _proc_snapshot()
        if snap is not None:
            return snap
    return EgressSnapshot(tier=TIER_UNAVAILABLE)


def observer_available() -> str:
    """The best egress-observation tier ACTUALLY working on THIS host — VERIFIED, not assumed: for the
    strong tier it really installs the table, reads it back, and tears it down; for the fallback it confirms
    /proc/net/tcp is readable. Cached (a host property). Returns TIER_STRONG / TIER_PROC / TIER_UNAVAILABLE."""
    global _available_tier
    if _available_tier is not None:
        return _available_tier
    if sys.platform != "linux":
        _available_tier = TIER_UNAVAILABLE      # platform is immutable — safe to cache permanently
        return _available_tier
    tier = TIER_UNAVAILABLE
    if install():
        try:
            if _nft_snapshot() is not None:
                tier = TIER_STRONG
        finally:
            teardown()
    if tier != TIER_STRONG and _proc_snapshot() is not None:
        tier = TIER_PROC
    # Cache ONLY a POSITIVE result. A transient 'none' (a momentary sudo/nft hiccup at first probe) is left
    # UNCACHED so a later egress re-probes and self-heals, instead of pinning the observer off for the whole
    # process lifetime (external-panel fix gpt-F4/F6, grok-F-04). Evidence-only, so re-probe cost is bounded
    # by egress frequency and dominated by the egress's own network I/O.
    if tier != TIER_UNAVAILABLE:
        _available_tier = tier
    return tier


@dataclass(frozen=True)
class ReconcileResult:
    """The independent-observation verdict for one egress action. EVIDENCE, not a gate.

    ``reconciled`` is TRI-STATE and honest about fidelity:
      * ``True``  — the STRONG (race-free) vantage confirmed every observed DESTINATION is accounted for
        by the record (the "hands didn't lie" upgrade).
      * ``False`` — a discrepancy was OBSERVED (a destination the record does not explain, or — strong tier
        only — a claimed DESTINATION the kernel never saw). Positive evidence of a mismatch, at any tier.
      * ``None``  — UNCHECKED: no vantage (UNAVAILABLE), or the racy `/proc` fallback saw no discrepancy but
        cannot positively confirm a clean run. Never a false "verified".

    SCOPE (external-panel gpt-F1): the verdict is over the DESTINATION SET — WHICH ``(ip, port)`` the uid's
    egress reached — because that is what "hands can't lie about WHERE data went" requires. It deliberately
    does NOT assert per-destination connection MULTIPLICITY: the strong-tier counter tallies PACKETS (TCP
    retransmits/keepalives inflate it), so it is not a connection count, and comparing it to a claim count
    would manufacture false discrepancies on every multi-packet request. A record over-claiming a repeat
    connection to an ALREADY-observed, already-authorized destination is therefore reconciled ``True`` — it
    is not concealed egress (the bytes still went only where the record says). The dangerous direction — a
    connection to a destination the record HID — is a NEW dest and is caught by ``unexpected`` at any tier."""
    reconciled: "bool | None"
    tier: str
    observed_dests: frozenset = field(default_factory=frozenset)
    unexpected: frozenset = field(default_factory=frozenset)          # observed, NOT in the record's claim
    claimed_unobserved: frozenset = field(default_factory=frozenset)  # claimed, NOT observed (strong tier)
    observed_conn_count: int = 0
    note: str = ""


def reconcile(before: EgressSnapshot, after: EgressSnapshot, claimed) -> ReconcileResult:
    """Compare what the kernel INDEPENDENTLY observed the Collaborator uid connect to during an egress
    action against what the `EgressRecord` CLAIMS. ``claimed`` is an iterable of ``(ip, port)``.

    The load-bearing, tier-robust check is UNEXPECTED — a destination the kernel saw that the record does
    not explain (a second client / a wrong dest / an extra request); observing one is a real mismatch at
    ANY tier. `claimed_unobserved` (a record claiming a connection the kernel never saw) is a discrepancy
    only at the STRONG tier — the `/proc` fallback is racy, so a claimed-but-unsampled connection is
    'unchecked', not a lie. A clean run is confirmed (`reconciled=True`) ONLY at the strong tier; a clean
    `/proc` pass or no vantage is `None` (unchecked), never a false clean claim."""
    tier = after.tier
    if tier == TIER_UNAVAILABLE:
        return ReconcileResult(reconciled=None, tier=tier,
                               note="egress not independently observed on this host (no vantage)")
    # Canonicalize IPs on BOTH sides (compressed form) so a textual difference between the record's
    # resolved_ip and the kernel's rendering (esp. IPv6) can't manufacture a false discrepancy (F-05).
    claimed_set = frozenset((_canon_ip(ip), int(port)) for ip, port in claimed if ip)
    # New destinations attributable to THIS action = what appeared after but not before.
    observed = frozenset((_canon_ip(ip), port) for ip, port in (after.dests - before.dests))
    unexpected = frozenset(observed - claimed_set)
    claimed_unobserved = frozenset(claimed_set - observed) if tier == TIER_STRONG else frozenset()
    conn_delta = max(0, after.conn_count - before.conn_count) if tier == TIER_STRONG else len(observed)
    if unexpected:
        reconciled, note = False, f"observed {sorted(unexpected)} not accounted for by the egress record"
    elif claimed_unobserved:
        reconciled, note = False, (f"record claims {sorted(claimed_unobserved)} but no connection was "
                                   "independently observed")
    elif tier == TIER_STRONG:
        reconciled, note = True, "independently observed (nft); all destinations accounted for"
    else:  # proc fallback, no discrepancy seen — cannot positively confirm (racy)
        reconciled, note = None, "no discrepancy seen (proc fallback, racy — not a positive confirmation)"
    return ReconcileResult(reconciled=reconciled, tier=tier, observed_dests=observed, unexpected=unexpected,
                           claimed_unobserved=claimed_unobserved, observed_conn_count=conn_delta, note=note)


def begin() -> EgressSnapshot:
    """Open an independent-observation window around ONE egress action and return the BEFORE snapshot.
    STRONG: install a FRESH (empty) uid-scoped nft table so the AFTER read is exactly this action's egress
    (clean attribution; assumes the governed executor runs egress actions serially — true today). PROC:
    the current live-socket sample. UNAVAILABLE: an honest empty marker. Never raises."""
    tier = observer_available()
    if tier == TIER_STRONG:
        if not install():                              # install FAILED → no clean fresh table this window;
            return EgressSnapshot(tier=TIER_UNAVAILABLE)  # unchecked, never a stale-table result (F-03)
        return EgressSnapshot(tier=TIER_STRONG)        # fresh, empty → BEFORE is empty by construction
    if tier == TIER_PROC:
        return _proc_snapshot() or EgressSnapshot(tier=TIER_PROC)
    return EgressSnapshot(tier=TIER_UNAVAILABLE)


def end(before: EgressSnapshot, claimed) -> ReconcileResult:
    """Close the window opened by `begin()` and reconcile against the record's ``claimed`` ``(ip, port)``
    destinations. STRONG: read the table, tear it down (scoped), reconcile. PROC: re-sample. Never raises."""
    tier = before.tier
    if tier == TIER_STRONG:
        after = _nft_snapshot()
        teardown()
        if after is None:
            # The strong tier was set up for THIS action but the read failed (table removed/unreadable/
            # unparseable). Honest UNCHECKED that is DISTINCT from "this host has no vantage" (gpt-F2 /
            # qwen-ID01): tier stays STRONG, reconciled=None — never masked as a clean or a no-vantage run.
            return ReconcileResult(reconciled=None, tier=TIER_STRONG, note="strong-tier observer read failed "
                                   "for this action (table missing/unreadable/unparseable) — unchecked")
        return reconcile(before, after, claimed)
    if tier == TIER_PROC:
        after = _proc_snapshot() or EgressSnapshot(tier=TIER_PROC)
        return reconcile(before, after, claimed)
    return reconcile(before, EgressSnapshot(tier=TIER_UNAVAILABLE), claimed)


def _reset_probe_cache_for_tests():
    """Test hook: forget the cached availability tier so a test can re-evaluate under a patched platform."""
    global _available_tier
    _available_tier = None
