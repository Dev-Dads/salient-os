"""③ Signed PolicyCaps — the Collaborator's authority as a verified, signed grant.

Authority — which capabilities the worker may use, and how loose each tool's leash may
get — is bound into a signed artifact the host verifies on EVERY governed action, instead
of living in mutable session config. So the config and the Step-2 control surface can only
ever operate WITHIN the grant: tighten, never widen. Tamper, wrong subject, or an absent
key fails closed (zero capabilities, strictest leash).

Honest scope: symmetric HMAC, a single trust domain (the verifier holds the signing key)
— this is tamper-evidence + provenance + fail-closed integrity, NOT a hard boundary
against a fully in-process re-signer. Asymmetric / a separate authority process is the
deliberate next step, consistent with ADR 0002.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path

from collaborator.tools import ACT_THEN_REPORT, NOTIFY_ONLY, PROPOSE_FIRST

COLLABORATOR_POLICYCAPS_VERSION = "0.1.0"

# Leash strictness rank: higher = stricter (less permissive). A cap is "no looser than".
_LEASH_RANK = {ACT_THEN_REPORT: 0, PROPOSE_FIRST: 1, NOTIFY_ONLY: 2}


@dataclass(frozen=True)
class PolicyCaps:
    """A grant: exactly what the authority permitted. Immutable; the signature covers it."""

    capabilities: tuple = ()
    leash_caps: tuple = ()   # sorted ((tool, max_looseness_leash), ...) — canonical + hashable
    issuer: str = ""
    subject: str = ""        # binds the grant to ONE workspace (its resolved path)

    def leash_cap_for(self, tool_name: str):
        for name, leash in self.leash_caps:
            if name == tool_name:
                return leash
        return None


@dataclass(frozen=True)
class SignedPolicyCaps:
    caps: PolicyCaps
    signature: str


def _canonical(caps: PolicyCaps) -> bytes:
    """Deterministic, type-stable serialization for signing (sorted keys + sorted values,
    so equal grants always sign identically and no two distinct grants collide)."""
    return json.dumps(
        {"capabilities": sorted(str(c) for c in caps.capabilities),
         "leash_caps": sorted([str(t), str(l)] for t, l in caps.leash_caps),
         "issuer": str(caps.issuer),
         "subject": str(caps.subject)},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def sign(caps: PolicyCaps, key: bytes) -> str:
    return hmac.new(key, _canonical(caps), hashlib.sha256).hexdigest()


def mint(capabilities, leash_caps, issuer: str, subject: str, key: bytes) -> SignedPolicyCaps:
    """Mint a signed grant. ``leash_caps`` is a {tool: max_looseness_leash} mapping. Only
    the policy authority (holder of ``key``) can mint or re-sign a grant. A leash-cap value must
    be one of the three defined levels — an unrecognised string is rejected at mint time (red-team
    F0: an invalid leash inside a correctly-SIGNED grant must never reach the seam, where it could
    slip past the ``== PROPOSE_FIRST`` checks), matching Session's own leash validation."""
    items = sorted((str(k), str(v)) for k, v in dict(leash_caps or {}).items())
    for _name, level in items:
        if level not in _LEASH_RANK:
            raise ValueError(f"leash_cap must be one of {tuple(_LEASH_RANK)}, got {level!r}")
    caps = PolicyCaps(
        capabilities=tuple(str(c) for c in capabilities),
        leash_caps=tuple(items),
        issuer=str(issuer), subject=str(subject),
    )
    return SignedPolicyCaps(caps=caps, signature=sign(caps, key))


def verify(signed: "SignedPolicyCaps | None", key: "bytes | None",
           expected_subject: "str | None" = None) -> bool:
    """True only if the signature matches under ``key`` AND (when given) the grant's
    subject matches ``expected_subject`` (no replay onto another workspace). Total: any
    missing piece, malformed grant, or exception -> False (fail closed, never raises)."""
    try:
        if signed is None or key is None:
            return False
        expected = sign(signed.caps, key)
        if not hmac.compare_digest(expected, str(signed.signature or "")):
            return False
        if expected_subject is not None and str(signed.caps.subject) != str(expected_subject):
            return False
        return True
    except Exception:  # noqa: BLE001 — any error is a failed verification, never a pass
        return False


def workspace_subject(workspace) -> str:
    """The canonical subject a grant binds to: the RESOLVED absolute workspace path (so a
    relative or symlinked path can't be replayed across directories). Mint a grant with
    ``subject=workspace_subject(ws)``; verification resolves the session's workspace the
    same way."""
    return str(Path(workspace).resolve())


def _valid_grant(session):
    """Return the verified SignedPolicyCaps for this session, or None. Present-but-invalid
    (tamper / wrong subject / absent key / unresolvable workspace) returns None — the
    caller fails closed."""
    signed = getattr(session, "policy_caps", None)
    if signed is None:
        return None
    key = getattr(session, "caps_key", None)
    try:
        subject = workspace_subject(getattr(session, "workspace", ""))
    except Exception:  # noqa: BLE001 — can't establish the subject -> fail closed
        return None
    return signed if verify(signed, key, expected_subject=subject) else None


def _enforced(session) -> bool:
    """Is this session under caps enforcement? Sticky: set once at construction when a
    grant was provided. So STRIPPING the grant at runtime (policy_caps=None) fails closed,
    it does not silently revert to the mutable-config legacy path (panel: grant-stripping)."""
    return bool(getattr(session, "enforce_caps", False))


def enforced(session) -> bool:
    """Public: is this session governed by a SIGNED grant (vs the legacy mutable-config path)?
    The seam requires this before honouring the highest-stakes signal — an autonomous-emission
    lift — so it can never rest on mutable ``session.capabilities`` (red-team F5)."""
    return _enforced(session)


def granted_capabilities(session) -> tuple:
    """The capabilities the worker actually holds. Under enforcement, the VERIFIED caps are
    authoritative — the mutable ``session.capabilities`` cannot widen them, and a grant that
    is absent-when-required or invalid -> () (fail closed). Not enforced (constructed with
    no grant) -> legacy ``session.capabilities``."""
    if not _enforced(session):
        return tuple(getattr(session, "capabilities", ()))
    grant = _valid_grant(session)
    return tuple(grant.caps.capabilities) if grant is not None else ()


def leash_cap(session, tool_name: str):
    """The signed max-looseness for a tool. Not enforced -> None (no cap, legacy). Under
    enforcement: the tool's cap if listed, else NOTIFY_ONLY (strictest) — a granted tool
    the authority did not give a leash cap defaults to the strictest, never uncapped; an
    absent/invalid grant likewise -> NOTIFY_ONLY (fail closed)."""
    if not _enforced(session):
        return None
    grant = _valid_grant(session)
    if grant is None:
        return NOTIFY_ONLY
    cap = grant.caps.leash_cap_for(tool_name)
    return cap if cap is not None else NOTIFY_ONLY


def signed_leash_cap(session, tool_name: str):
    """The signed max-looseness a grant EXPLICITLY set for ``tool_name`` — or None if unlisted /
    not enforced / no valid grant. Unlike ``leash_cap`` (which defaults an unlisted tool to
    NOTIFY_ONLY), this distinguishes 'the operator did not pin this tool' (None) from a real cap,
    so the emission auto-lift can let a per-host ``net.post.auto`` grant permit act_then_report
    UNLESS the operator ALSO explicitly capped the tool tighter (red-team F2)."""
    if not _enforced(session):
        return None
    grant = _valid_grant(session)
    return grant.caps.leash_cap_for(tool_name) if grant is not None else None


def apply_cap(leash: str, cap) -> str:
    """The stricter of ``leash`` and ``cap`` (cap = 'no looser than this'). cap None -> leash
    unchanged (no ceiling). An UNRECOGNISED value on EITHER side fails closed to NOTIFY_ONLY —
    never returned verbatim (red-team F0: the old code ranked an unknown strictest but RETURNED it,
    so a typo'd 'propose-first' slipped past every downstream `== PROPOSE_FIRST`/`== NOTIFY_ONLY`
    check and ran autonomously, and a signed ceiling could never tighten it)."""
    if leash not in _LEASH_RANK:
        return NOTIFY_ONLY
    if cap is None:
        return leash
    if cap not in _LEASH_RANK:
        return NOTIFY_ONLY
    return leash if _LEASH_RANK[leash] >= _LEASH_RANK[cap] else cap
