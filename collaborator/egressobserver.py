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

STRICTLY SCOPED, fail-safe blast radius: this module ONLY ever creates/deletes ``table inet salient_obs``
(via `nft delete table`, error-tolerant) — it NEVER runs `nft flush ruleset` or touches any other table, so
it cannot disturb a host firewall. The rule is `policy accept` and purely observational (`update`+`counter`),
so it never drops a packet. Linux-only, stdlib-only, NO new dependency (nft is a system binary).

Reconciliation is EVIDENCE, not a gate (P-01: it INFORMS/flags, never grants): an observed destination the
record does not explain raises `egress_reconciled=False` + a discrepancy note; a clean strong-tier match is
the "hands didn't lie" upgrade. Confinement so nothing CAN egress unobserved (an enforced netns chokepoint)
is the deferred full-parity follow-up; this observes, it does not enforce.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

COLLABORATOR_EGRESSOBSERVER_VERSION = "0.1.0"

# The ONE table this module ever creates or deletes. Teardown is `nft delete table inet salient_obs` —
# scoped, error-tolerant, NEVER `flush ruleset`, so a host firewall is untouchable by this observer.
_NFT_TABLE = "salient_obs"

# Honest tier labels (mirrors the netns/contained honest-flag posture).
TIER_STRONG = "nft"        # privileged kernel OUTPUT hook — race-free, uid-attributed, unbypassable
TIER_PROC = "proc"         # unprivileged /proc/net/tcp sampling — point-in-time, no bytes
TIER_UNAVAILABLE = "none"  # no vantage on this host → egress_observed=False


def _resolve(candidates, name: str) -> str:
    """Absolute path to a system binary, resolved at import (before any run_command could plant a shadow),
    identical in spirit to netns._resolve."""
    for p in candidates:
        if os.path.isfile(p):
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


def _nft_ruleset(uid: int) -> str:
    """The passive, uid-scoped OUTPUT observer. Both address families; new-connection (pure SYN) only;
    `update` a dynamic set of ``daddr . dport`` + bump a counter; `policy accept` (never drops)."""
    return f"""table inet {_NFT_TABLE} {{
  set dests4 {{ type ipv4_addr . inet_service; flags dynamic,timeout; timeout 10m; }}
  set dests6 {{ type ipv6_addr . inet_service; flags dynamic,timeout; timeout 10m; }}
  counter conns {{}}
  chain out {{
    type filter hook output priority 0; policy accept;
    meta skuid {uid} ip  daddr != 127.0.0.0/8 tcp flags & (syn|ack) == syn \
      update @dests4 {{ ip daddr  . tcp dport }} counter name conns
    meta skuid {uid} ip6 daddr != ::1         tcp flags & (syn|ack) == syn \
      update @dests6 {{ ip6 daddr . tcp dport }} counter name conns
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


def _parse_nft_set(json_text: str) -> "set[tuple[str, int]]":
    import json
    out: set = set()
    try:
        doc = json.loads(json_text)
    except (ValueError, TypeError):
        return out
    for obj in doc.get("nftables", []):
        s = obj.get("set") if isinstance(obj, dict) else None
        if not s:
            continue
        for elem in s.get("elem", []):
            val = elem.get("elem", elem) if isinstance(elem, dict) else elem
            concat = val.get("val", val).get("concat") if isinstance(val, dict) else None
            if isinstance(concat, list) and len(concat) == 2:
                ip, port = concat[0], concat[1]
                try:
                    out.add((str(ip), int(port)))
                except (ValueError, TypeError):
                    continue
    return out


def _nft_snapshot() -> "EgressSnapshot | None":
    """Read both dest sets + the counter as JSON. None if the read fails (table gone / lost privilege)."""
    import json
    dests: set = set()
    try:
        for setname in ("dests4", "dests6"):
            r = _run_nft(["-j", "list", "set", "inet", _NFT_TABLE, setname])
            if r.returncode != 0:
                return None
            dests |= _parse_nft_set(r.stdout)
        rc = _run_nft(["-j", "list", "counter", "inet", _NFT_TABLE, "conns"])
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
    _available_tier = TIER_UNAVAILABLE
    if sys.platform != "linux":
        return _available_tier
    if install():
        try:
            if _nft_snapshot() is not None:
                _available_tier = TIER_STRONG
        finally:
            teardown()
        if _available_tier == TIER_STRONG:
            return _available_tier
    if _proc_snapshot() is not None:
        _available_tier = TIER_PROC
    return _available_tier


@dataclass(frozen=True)
class ReconcileResult:
    """The independent-observation verdict for one egress action. EVIDENCE, not a gate.

    ``reconciled`` is TRI-STATE and honest about fidelity:
      * ``True``  — the STRONG (race-free) vantage confirmed every observed destination is accounted for
        by the record (the "hands didn't lie" upgrade).
      * ``False`` — a discrepancy was OBSERVED (a destination the record does not explain, or — strong tier
        only — a claimed connection the kernel never saw). Positive evidence of a mismatch, at any tier.
      * ``None``  — UNCHECKED: no vantage (UNAVAILABLE), or the racy `/proc` fallback saw no discrepancy but
        cannot positively confirm a clean run. Never a false "verified"."""
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
    claimed_set = frozenset((str(ip), int(port)) for ip, port in claimed if ip)
    # New destinations attributable to THIS action = what appeared after but not before.
    observed = frozenset(after.dests - before.dests)
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
        install()                                      # fresh, empty → BEFORE is empty by construction
        return EgressSnapshot(tier=TIER_STRONG)
    if tier == TIER_PROC:
        return _proc_snapshot() or EgressSnapshot(tier=TIER_PROC)
    return EgressSnapshot(tier=TIER_UNAVAILABLE)


def end(before: EgressSnapshot, claimed) -> ReconcileResult:
    """Close the window opened by `begin()` and reconcile against the record's ``claimed`` ``(ip, port)``
    destinations. STRONG: read the table, tear it down (scoped), reconcile. PROC: re-sample. Never raises."""
    tier = before.tier
    if tier == TIER_STRONG:
        after = _nft_snapshot() or EgressSnapshot(tier=TIER_UNAVAILABLE)
        teardown()
        return reconcile(before, after, claimed)
    if tier == TIER_PROC:
        after = _proc_snapshot() or EgressSnapshot(tier=TIER_PROC)
        return reconcile(before, after, claimed)
    return reconcile(before, EgressSnapshot(tier=TIER_UNAVAILABLE), claimed)


def _reset_probe_cache_for_tests():
    """Test hook: forget the cached availability tier so a test can re-evaluate under a patched platform."""
    global _available_tier
    _available_tier = None
