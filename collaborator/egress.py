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

``fetch`` (GET) is Tier 1; ``post`` (Tier 2, ADR 0003 revisit #2) is the outbound EMISSION path
and reuses this exact contract, adding a capped/hashed request body, a host-injected (never
model-supplied, never logged) Authorization credential, and a body-free-vs-bounded-preview audit
split. Emitting to a host authorizes on a SEPARATE capability (``net.post:<host>``, not
``net.get:<host>``) and is human-gated by default (the gate/seam owns those decisions).

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
# ADR 0003 Tier 2 — outbound EMISSION. A POST authorizes on a SEPARATE capability namespace
# (reading a host is not emitting to it — net.get:H does NOT grant net.post:H), and a host may
# be emitted-to AUTONOMOUSLY only via a distinct signed grant net.post.auto:<host>; the gate
# owns those checks. This module only enforces the transport contract, now for POST too.
EGRESS_POST_CAP_PREFIX = "net.post:"
EGRESS_AUTO_PREFIX = "net.post.auto:"
MAX_POST_BODY = 65536           # 64 KiB — the outbound PAYLOAD ceiling (the real exfil surface)
DEFAULT_POST_CONTENT_TYPE = "application/json"
_BODY_PREVIEW_BYTES = 512       # bounded body preview recorded ONLY for human-gated emissions
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


def required_capability(url: str, method: str = "GET") -> "str | None":
    """The per-destination egress capability ``url`` requires, or None if the URL is ineligible
    (the governance gate turns None into a DENY). Method-aware (ADR 0003): a GET needs
    ``net.get:<host>``, a POST needs ``net.post:<host>`` — a SEPARATE namespace, so read access
    to a host never confers emit access. Anything other than POST maps to the read capability."""
    host = canonical_host(url)
    if host is None:
        return None
    prefix = EGRESS_POST_CAP_PREFIX if str(method or "GET").upper() == "POST" else EGRESS_CAP_PREFIX
    return prefix + host


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
    # ADR 0003 Tier 2 — the outbound POST payload. Body-free by default (hash + length only);
    # request_body_preview is populated ONLY for a HUMAN-GATED (propose_first) emission, so an
    # AUTONOMOUS (act_then_report) emission leaves nothing but a hash in the durable trail while
    # a hand-approved one keeps a bounded preview of exactly what was sent (Josh's steer). A GET
    # has no body -> these stay empty/0.
    request_body_hash: str = ""
    request_body_len: int = 0
    request_body_preview: str = ""
    # ADR 0003 Tier 2 (transport red-team C1): the content-type actually put on the wire — it is a
    # model-reachable outbound header, so the audited surface must include it or the channel-
    # integrity record under-counts what was sent. Empty for a GET / a refusal before validation.
    request_content_type: str = ""


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
             status=None, redirect=None, ip=None, method: str = "GET",
             body_hash: str = "", body_len: int = 0, body_preview: str = "") -> EgressResult:
    return EgressResult(EgressRecord(
        canonical_dest=dest, method=method, request_target_hash=target_hash,
        request_bytes=request_bytes, status=status, response_hash=None, response_len=0,
        redirect_location=redirect, resolved_ip=ip, ok=False, error=error,
        request_body_hash=body_hash, request_body_len=body_len, request_body_preview=body_preview))


def _has_control_chars(text: str) -> bool:
    """True if ``text`` contains a control char (CR/LF/NUL/tab/DEL/any < 0x20). Such a char in a
    header value OR the request target would split the request; http.client raises on it, so we
    check first and return a clean refusal instead of letting an exception escape ``post``/``fetch``."""
    return any(ord(c) < 0x20 or ord(c) == 0x7f for c in text)


def _is_clean_request_target(target: str) -> bool:
    """The request target (path+query) rides the ASCII request line. Reject control chars AND any
    non-ASCII char: a real URL path is percent-encoded ASCII, whereas a raw non-ASCII char would
    raise UnicodeEncodeError out of the client (the request line is ASCII-encoded) — return a clean
    refusal instead of letting that escape (a space likewise raises InvalidURL, an HTTPException we
    already catch)."""
    if _has_control_chars(target):
        return False
    try:
        target.encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def _sanitize_location(loc):
    """Bound + strip control chars from a redirect Location before it enters the audit record: a
    real response parser can obs-fold a CRLF into a header value (which would forge audit lines),
    and a 60 KB Location must not outweigh our own request-target cap (transport red-team M1)."""
    if not loc:
        return loc
    cleaned = "".join(c for c in loc if ord(c) >= 0x20 and ord(c) != 0x7f)
    return cleaned[:MAX_URL_TARGET]


def _is_clean_header_value(value: str, max_len: int = 8192) -> bool:
    """A single-line, **ASCII** header value within a length bound and free of control chars. A
    content-type and a Bearer credential are ASCII in practice; requiring ASCII (not just latin-1)
    ALSO rejects the C1 control range U+0080–U+009F (NEL etc.) which is latin-1-encodable and would
    otherwise reach the wire inside a header. Turns non-ascii / control-char injection / oversize
    into a clean refused record instead of a UnicodeEncodeError escaping the client (the ADR's
    'never raises' boundary; net.post transport red-team C1/S1)."""
    if not isinstance(value, str) or not value or len(value) > max_len:
        return False
    if _has_control_chars(value):
        return False
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def emission_seal(url: str, body, content_type: str = "") -> str:
    """A stable digest of an emission's consequential identity — canonical destination + request
    target (path+query) + content-type + exact body — captured when the emission is HELD, so the
    approval path can REFUSE a payload mutated after the human saw it. Tier 2 has no verifier, so
    'the human approved exactly what is sent' has to be bound by this seal rather than observed
    after the fact (ADR 0003; net.post panel: approved != sent)."""
    host = canonical_host(url) or ""
    try:
        parts = urlsplit((url or "").strip())
        target = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
    except ValueError:
        target = ""
    if isinstance(body, (bytes, bytearray)):
        body_bytes = bytes(body)
    else:
        # surrogatepass so a lone-surrogate body (legal JSON, refused later by post()) still SEALS
        # deterministically instead of raising here at hold time — the seal only needs consistency
        # between hold and approve, not validity.
        body_bytes = str(body if body is not None else "").encode("utf-8", "surrogatepass")
    h = hashlib.sha256()
    for part in (host, target, str(content_type or "")):
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\x00")
    h.update(body_bytes)
    return h.hexdigest()


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
    if not _is_clean_request_target(target):  # CRLF/control/non-ascii would split or crash the request
        return _refused(host, "", len(target), "illegal request target (control/non-ascii chars)")
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
        conn.putrequest("GET", target, skip_host=True)   # we set the canonical Host ONCE ourselves
        conn.putheader("Host", host)                      # (skip_host avoids a duplicate Host header)
        conn.putheader("User-Agent", _USER_AGENT)
        conn.putheader("Accept", "*/*")
        conn.putheader("Connection", "close")
        conn.endheaders()                        # no model-supplied Authorization/Cookie, ever
        resp = conn.getresponse()
        status = int(resp.status)
        if 300 <= status < 400:                  # FAIL CLOSED on redirect — do not follow
            loc = _sanitize_location(resp.getheader("Location"))
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


def post(url: str, body, *, content_type: str = DEFAULT_POST_CONTENT_TYPE, auth: "str | None" = None,
         keep_preview: bool = False, timeout: int = DEFAULT_TIMEOUT,
         max_response: int = DEFAULT_MAX_RESPONSE, resolver=_resolve,
         connection_factory=None) -> EgressResult:
    """Perform ONE mediated, safety-contracted POST — the Tier-2 EMISSION path (ADR 0003).

    Never raises; a refusal is a non-ok EgressRecord. Reuses the WHOLE Tier-1 transport contract
    verbatim (canonical host == connect host, no-redirect fail-closed, IP-pin + private/metadata
    block, HTTPS, bounds). The BODY is the outbound payload — hard-capped, hashed, and (only when
    ``keep_preview``) a bounded preview is recorded; body-free otherwise, matching the audit
    discipline for autonomous emissions.

    ``auth`` is a HOST-INJECTED credential (the governance seam supplies it from host config for
    the consented host). It is the ONLY way an Authorization header is ever set — the model's args
    never carry one — and it is never logged. Because a 3xx is never followed, the body AND the
    credential can never be re-sent to a redirect target. The capability check
    (``net.post:<host>``) is the CALLER's job (the gate); this module enforces transport only."""
    host = canonical_host(url)
    if host is None:
        return _refused("", "", 0, "ineligible url: not https / bad host / userinfo / non-443 port",
                        method="POST")

    parts = urlsplit(url.strip())
    target = parts.path or "/"
    if parts.query:
        target = target + "?" + parts.query
    if len(target) > MAX_URL_TARGET:
        return _refused(host, "", len(target), "request target exceeds cap (exfil guard)",
                        method="POST")
    if not _is_clean_request_target(target):  # CRLF/control/non-ascii would split or crash the request
        return _refused(host, "", len(target), "illegal request target (control/non-ascii chars)",
                        method="POST")
    target_hash = hashlib.sha256(target.encode("utf-8", "replace")).hexdigest()
    request_bytes = len(target)

    # The body is the outbound PAYLOAD. Encode a str as UTF-8 (a lone surrogate is legal JSON but
    # NOT utf-8-encodable -> refuse, never raise: transport red-team S1); accept bytes as-is; refuse
    # anything else. Hash BEFORE the cap so an over-cap refusal stays linkable to what was attempted
    # (M4). Hard-cap the length — the payload is the real exfil surface for an emission.
    if isinstance(body, str):
        try:
            body_bytes = body.encode("utf-8")
        except UnicodeError:
            return _refused(host, target_hash, request_bytes,
                            "body not utf-8 encodable (lone surrogate?)", method="POST")
    elif isinstance(body, (bytes, bytearray)):
        body_bytes = bytes(body)
    else:
        return _refused(host, target_hash, request_bytes, "body must be str or bytes", method="POST")
    body_len = len(body_bytes)
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    if body_len > MAX_POST_BODY:
        return _refused(host, target_hash, request_bytes,
                        f"body exceeds cap ({body_len} > {MAX_POST_BODY}; exfil guard)",
                        method="POST", body_hash=body_hash, body_len=body_len)
    # The preview is recorded ONLY for a human-gated emission (keep_preview) — the durable trail
    # is otherwise body-free (Josh's steer: body-free for autonomous, bounded preview for gated).
    body_preview = body_bytes[:_BODY_PREVIEW_BYTES].decode("utf-8", "replace") if keep_preview else ""

    ctype = str(content_type or DEFAULT_POST_CONTENT_TYPE)
    if not _is_clean_header_value(ctype, max_len=256):  # a content-type is short + ASCII
        return _refused(host, target_hash, request_bytes, "illegal content-type (header injection?)",
                        method="POST", body_hash=body_hash, body_len=body_len, body_preview=body_preview)
    # An EMPTY host credential is "no credential" (skip the header), not a refusal (M5); a NON-empty
    # one is fail-closed on any control/non-ascii char (the source is host config, not the model,
    # but the transport point stays honest).
    if auth and not _is_clean_header_value(str(auth)):
        return _refused(host, target_hash, request_bytes, "illegal authorization value",
                        method="POST", body_hash=body_hash, body_len=body_len, body_preview=body_preview)

    def _rec(status, response_hash, response_len, ok, error, *, truncated=False, redirect=None):
        return EgressRecord(
            canonical_dest=host, method="POST", request_target_hash=target_hash,
            request_bytes=request_bytes, status=status, response_hash=response_hash,
            response_len=response_len, redirect_location=_sanitize_location(redirect),
            resolved_ip=pinned, ok=ok, error=error, truncated=truncated, request_body_hash=body_hash,
            request_body_len=body_len, request_body_preview=body_preview, request_content_type=ctype)

    try:
        ips = resolver(host)
    except Exception as exc:  # noqa: BLE001 — resolution failure fails closed
        return _refused(host, target_hash, request_bytes, f"resolve failed: {type(exc).__name__}",
                        method="POST", body_hash=body_hash, body_len=body_len, body_preview=body_preview)
    pinned = next((ip for ip in (ips or []) if is_safe_public_ip(ip)), None)
    if pinned is None:
        return _refused(host, target_hash, request_bytes,
                        "no safe public IP (loopback/private/link-local/metadata blocked)",
                        method="POST", body_hash=body_hash, body_len=body_len, body_preview=body_preview)

    if connection_factory is None:
        ctx = ssl.create_default_context()
        conn = _PinnedHTTPSConnection(host, pinned, context=ctx, timeout=timeout)
    else:
        conn = connection_factory(host, pinned)

    try:
        conn.putrequest("POST", target, skip_host=True)   # we set the canonical Host ourselves
        conn.putheader("Host", host)
        conn.putheader("User-Agent", _USER_AGENT)
        conn.putheader("Content-Type", ctype)
        conn.putheader("Content-Length", str(body_len))
        conn.putheader("Connection", "close")
        if auth:  # HOST-INJECTED ONLY — never sourced from model args, never logged
            conn.putheader("Authorization", str(auth))
        conn.endheaders()
        conn.send(body_bytes)
        resp = conn.getresponse()
        status = int(resp.status)
        if 300 <= status < 400:                  # FAIL CLOSED — never re-POST body/credential
            loc = resp.getheader("Location")
            return EgressResult(_rec(status, None, 0, False,
                                     f"redirect not followed ({status}); re-gate the target as a "
                                     "new intent", redirect=loc))
        raw = resp.read(max_response + 1)
        truncated = len(raw) > max_response
        resp_body = raw[:max_response]
        ok = 200 <= status < 300
        return EgressResult(
            _rec(status, hashlib.sha256(resp_body).hexdigest(), len(resp_body), ok,
                 ("" if ok else f"status {status}"), truncated=truncated),
            body=resp_body)
    except (ssl.SSLError, OSError, http.client.HTTPException) as exc:
        return EgressResult(_rec(None, None, 0, False, f"egress failed: {type(exc).__name__}: {exc}"))
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
