"""The Collaborator's single mediated egress client — ADR 0003 Tier 1.

This module is the ONLY thing in the Collaborator that touches the network, and it is the
enforcement point for the outbound boundary. Every request is default-deny (a host is
reachable only if the signed PolicyCaps grants ``net.get:<canonical-host>``; that check lives
in the governance seam) and passes through this module's safety contract — which is *where
allowlists actually fail*:

  * ONE canonical parse builds the capability key AND is the connect host, so the host we
    AUTHORIZE is the host we CONNECT to. Closes authorize-one / connect-another: userinfo
    (``user@host``), case, IDN/punycode homographs, trailing-dot labels, explicit ports.
  * HTTPS only (a bare host or ``http://`` is refused, never defaulted).
  * Redirects FAIL CLOSED — a 3xx is returned, never followed; a redirect target is a NEW
    destination that must be re-gated against its own capability, not silently trusted.
  * The resolved IP is PINNED for the connection and refused if it is loopback / private /
    link-local / metadata / reserved / multicast. Closes DNS-rebind and SSRF-to-metadata:
    the allowlist checks the NAME, the socket uses the IP, so we resolve once, safety-check
    the IP, and connect to THAT pinned IP (TLS validated against the canonical name).
  * Bounded — connect/read timeout, a response byte ceiling, and a query-length cap. No
    unbounded stream into the proposer context; a GET's query is a bounded exfil surface.

Honest scope (ADR 0003): this is CHANNEL-INTEGRITY LOGGING, not the verifier's "hands can't
lie" property. The client both makes and records the request (same channel), so it proves
what was sent through the sanctioned channel — not that no bytes left by another path
(``run_command`` still reaches the network until the box network namespace lands, ADR 0003
revisit #1). GET is treated as an EXFILTRATION channel, not "read-only, no side effect":
the request target (path+query) is hashed and length-capped, and no model-supplied
``Authorization`` / ``Cookie`` headers are ever sent.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import socket
import ssl
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit

COLLABORATOR_EGRESS_VERSION = "0.1.0"

# Tier-1 bounds (host config in a later rev; conservative defaults now).
DEFAULT_TIMEOUT = 15            # connect/read seconds
DEFAULT_MAX_RESPONSE = 262144   # 256 KiB response ceiling
MAX_URL_TARGET = 2048           # cap the FULL GET request target (path+query) — the whole
                                # outbound surface, not just the query (a path exfils identically)
EGRESS_CAP_PREFIX = "net.get:"
_USER_AGENT = "SalienceOS-Collaborator/0.1 (+egress-mediated)"
_HTTPS_PORT = 443
# A canonical DNS host is ASCII letters/digits/hyphen/dot only. The stdlib ``idna`` codec
# passes an all-ASCII label through WITHOUT validating its charset, so an IPv6 literal
# (``::1``) or other junk can survive encoding — this guard rejects anything that is not a
# plain hostname (IPv4 literals, all-digit+dot, are allowed and get IP-safety-checked later).
_HOST_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-.")


class EgressError(Exception):
    """An egress request is refused (fail closed). Not raised across the tool boundary —
    ``fetch`` returns a non-ok EgressRecord instead — but available for direct callers."""


# --- canonical destination (the capability key AND the connect host) ----------------------

def canonical_host(url_or_host: str) -> "str | None":
    """The canonical connect host for an ``https://`` URL, or None if ineligible (fail closed).

    The returned string is punycode, lowercased, with userinfo/port/trailing-dot stripped, and
    it is used for BOTH the capability key (``net.get:<host>``) and the actual socket connect —
    a single source of truth for "which host", which is what closes the authorize-one /
    connect-another bypass class. ANY parse ambiguity returns None so the caller denies rather
    than guessing.
    """
    if not isinstance(url_or_host, str) or not url_or_host.strip():
        return None
    raw = url_or_host.strip()
    if "://" not in raw:                       # a bare host could default a scheme — refuse
        return None
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme != "https":                # HTTPS only in v0
        return None
    if "@" in parts.netloc:                     # embedded credentials — top allowlist bypass
        return None
    host = parts.hostname                        # urlsplit lowercases + strips userinfo/port
    if not host:
        return None
    try:
        if parts.port is not None and parts.port != _HTTPS_PORT:  # non-443 = different endpoint
            return None
    except ValueError:                           # malformed port
        return None
    host = unicodedata.normalize("NFC", host).rstrip(".")
    try:
        canon = host.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):           # bad/empty/over-long label, IPv6 literal, etc.
        return None
    if not canon or canon.startswith(".") or canon.endswith(".") or ".." in canon:
        return None
    if any(ch not in _HOST_CHARS for ch in canon):  # reject IPv6 literals / non-hostname junk
        return None
    if "." not in canon:  # reject dotless hosts: numeric/hex IP forms (2130706433, 0x7f000001),
        return None       # single-label junk. A real public FQDN and a dotted-quad both have a dot.
    return canon


def egress_capability(host: str) -> str:
    """The capability string that authorizes an allowlisted GET to ``host`` (already canonical)."""
    return EGRESS_CAP_PREFIX + host


def required_capability(url: str) -> "str | None":
    """The ``net.get:<canonical-host>`` capability an egress to ``url`` requires, or None if the
    URL is ineligible (the governance gate turns None into a DENY)."""
    host = canonical_host(url)
    return egress_capability(host) if host is not None else None


# --- IP safety (the rebind / SSRF-to-metadata guard) --------------------------------------

def is_safe_public_ip(ip: str) -> bool:
    """True ONLY for a globally-routable unicast address. Loopback, RFC1918 private, link-local
    (incl. 169.254.169.254 cloud-metadata), CGNAT/shared space (100.64.0.0/10 — Tailscale's
    default tailnet range), multicast, reserved (incl. NAT64 64:ff9b::/96), and unspecified all
    fail closed."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    # Classify an IPv4-mapped IPv6 address (::ffff:a.b.c.d) by its EMBEDDED IPv4, so a mapped
    # private/metadata address cannot slip through. Do NOT depend on the interpreter projecting
    # mapped properties onto the wrapper (that fix landed in 3.11.9 / 3.12.4; older ones leak it).
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    # POSITIVE contract: is_global is what excludes CGNAT / shared address space (100.64.0.0/10),
    # which none of the boolean flags below catch (a red-team finding: an allowlisted host
    # resolving into the operator's tailnet is an SSRF target). The denylist stays as belt-and-
    # suspenders — and it, not is_global, is what catches NAT64 (marked globally-routable).
    return addr.is_global and not (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_multicast or addr.is_reserved or addr.is_unspecified)


def _resolve(host: str) -> "list[str]":
    """Resolve ``host`` to its IP strings (injectable for tests)."""
    infos = socket.getaddrinfo(host, _HTTPS_PORT, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection that connects to a PRE-RESOLVED, safety-checked IP while keeping the
    canonical hostname for SNI + certificate validation — so DNS cannot rebind between the
    allowlist check and the socket connect."""

    def __init__(self, host: str, pinned_ip: str, *, context, timeout):
        super().__init__(host, _HTTPS_PORT, context=context, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self):  # noqa: D401
        sock = socket.create_connection((self._pinned_ip, _HTTPS_PORT), self.timeout)
        # server_hostname=self.host -> cert is validated against the CANONICAL name, not the IP.
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


# --- the egress record (audit; body-free) + result (body handed to the caller) ------------

@dataclass(frozen=True)
class EgressRecord:
    """Channel-integrity audit of one egress. Body-free by design (hash + length only), so a
    fetched secret is not persisted into the trail (ADR 0001/0002 body-free discipline)."""

    canonical_dest: str
    method: str
    request_target_hash: str        # sha256(path+query) — the exfil surface, hashed
    request_bytes: int              # length of the request target
    status: "int | None"
    response_hash: "str | None"
    response_len: int
    redirect_location: "str | None"
    resolved_ip: "str | None"
    ok: bool
    error: str = ""
    truncated: bool = False


@dataclass
class EgressResult:
    """The audit record plus the (capped) body bytes for the caller to consume. Only ``record``
    is logged; ``body`` is handed to perception/research and never persisted raw."""

    record: EgressRecord
    body: bytes = b""

    def text(self, limit: "int | None" = None) -> str:
        b = self.body if limit is None else self.body[:limit]
        return b.decode("utf-8", "replace")


def _refused(dest: str, target_hash: str, request_bytes: int, error: str,
             status=None, redirect=None, ip=None) -> EgressResult:
    return EgressResult(EgressRecord(
        canonical_dest=dest, method="GET", request_target_hash=target_hash,
        request_bytes=request_bytes, status=status, response_hash=None, response_len=0,
        redirect_location=redirect, resolved_ip=ip, ok=False, error=error))


def fetch(url: str, *, timeout: int = DEFAULT_TIMEOUT, max_response: int = DEFAULT_MAX_RESPONSE,
          resolver=_resolve, connection_factory=None) -> EgressResult:
    """Perform ONE mediated, safety-contracted GET. Never raises; a refusal is a non-ok
    EgressRecord. ``resolver`` and ``connection_factory`` are injectable so the safety contract
    is unit-testable without live network. The capability check is the CALLER's job (the
    governance gate); this module enforces the transport-level contract only."""
    host = canonical_host(url)
    if host is None:
        return _refused("", "", 0, "ineligible url: not https / bad host / userinfo / non-443 port")

    parts = urlsplit(url.strip())
    target = parts.path or "/"
    if parts.query:
        target = target + "?" + parts.query
    if len(target) > MAX_URL_TARGET:  # cap the WHOLE target — path and query exfil identically
        return _refused(host, "", len(target), "request target exceeds cap (exfil guard)")
    target_hash = hashlib.sha256(target.encode("utf-8", "replace")).hexdigest()
    request_bytes = len(target)

    try:
        ips = resolver(host)
    except Exception as exc:  # noqa: BLE001 — resolution failure fails closed
        return _refused(host, target_hash, request_bytes, f"resolve failed: {type(exc).__name__}")
    pinned = next((ip for ip in (ips or []) if is_safe_public_ip(ip)), None)
    if pinned is None:
        return _refused(host, target_hash, request_bytes,
                        "no safe public IP (loopback/private/link-local/metadata blocked)")

    if connection_factory is None:
        ctx = ssl.create_default_context()
        conn = _PinnedHTTPSConnection(host, pinned, context=ctx, timeout=timeout)
    else:
        conn = connection_factory(host, pinned)

    try:
        conn.putrequest("GET", target)          # Host header defaults to the canonical self.host
        conn.putheader("Host", host)
        conn.putheader("User-Agent", _USER_AGENT)
        conn.putheader("Accept", "*/*")
        conn.putheader("Connection", "close")
        conn.endheaders()                        # no model-supplied Authorization/Cookie, ever
        resp = conn.getresponse()
        status = int(resp.status)
        if 300 <= status < 400:                  # FAIL CLOSED on redirect — do not follow
            loc = resp.getheader("Location")
            return _refused(host, target_hash, request_bytes,
                            f"redirect not followed ({status}); re-gate the target as a new intent",
                            status=status, redirect=loc, ip=pinned)
        raw = resp.read(max_response + 1)
        truncated = len(raw) > max_response
        body = raw[:max_response]
        ok = 200 <= status < 300
        return EgressResult(
            EgressRecord(canonical_dest=host, method="GET", request_target_hash=target_hash,
                         request_bytes=request_bytes, status=status,
                         response_hash=hashlib.sha256(body).hexdigest(), response_len=len(body),
                         redirect_location=None, resolved_ip=pinned, ok=ok,
                         error=("" if ok else f"status {status}"), truncated=truncated),
            body=body)
    except (ssl.SSLError, OSError, http.client.HTTPException) as exc:
        return _refused(host, target_hash, request_bytes,
                        f"egress failed: {type(exc).__name__}: {exc}", ip=pinned)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
