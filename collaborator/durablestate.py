"""Shared primitives for the host-controlled, integrity-protected DURABLE state stores (F2 provenance
manifest + the emission-quota counter). One implementation of the security-critical bits so they can't
drift: a DOMAIN-SEPARATED derived MAC and an atomic write.

Threat model (shared by every durable store built on this): the AGENT is the adversary. A store MUST
live OUTSIDE the workspace (the contained autonomous run writes only the workspace; the human
maintenance run is the trusted hand) — enforced by ``Session._assert_store_outside_workspace``, not
here. Integrity is an HMAC under the host's ``policy_key`` (an in-process secret, never on disk in the
workspace, unreachable by the contained run), DERIVED per store-kind so a MAC for one kind can never be
replayed as another, and each store binds the workspace SUBJECT into its signed body. The load-bearing
property is the store's UNREACHABILITY; the MAC is defense-in-depth + honest corruption/replay
detection. Stdlib-only, TOTAL (these never raise; callers stay total on top of them).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

COLLABORATOR_DURABLESTATE_VERSION = 1


def mac(key, payload: bytes, domain: bytes) -> str:
    """A DOMAIN-SEPARATED keyed MAC. Derive a dedicated per-store-kind key from ``key`` (so a signing
    oracle for ``key`` elsewhere, or a MAC for a DIFFERENT store kind, can never be replayed as this
    one — external panel gpt), then HMAC-SHA256 the payload under it. Integrity STILL assumes ``key`` is
    a host SECRET; with a public default key it degrades to corruption detection, and the store's
    out-of-workspace UNREACHABILITY is the real property."""
    store_key = hmac.new(bytes(key), bytes(domain), hashlib.sha256).digest()
    return hmac.new(store_key, payload, hashlib.sha256).hexdigest()


def atomic_write(path, text: str) -> bool:
    """Write ``text`` to ``path`` via a temp file + ``os.replace`` (atomic on the same filesystem, so a
    concurrent reader sees the whole old or whole new file, never a torn one). TOTAL — returns True on
    success, False on any error (best-effort durability; the in-memory state stays authoritative)."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(str(tmp), str(p))
        return True
    except Exception:  # noqa: BLE001
        return False
