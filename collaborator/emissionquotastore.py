"""Cross-session DURABLE store for the per-destination emission-quota COUNTER (ADR 0003 residual-sweep
follow-up). The quota (`Session.emission_quota`) bounds HOW MANY emissions may go to a canonical host,
but the COUNTER (`session._emission_counts`) was session-lived — so a restart RESET the count and a
patient drip/spam channel could refresh its budget every new session. This persists the counter across
sessions, HOST-controlled and integrity-protected, closing the restart-to-reset gap.

Same threat model + machinery as the provenance store (collaborator/durablestate.py): the AGENT is the
adversary; the store MUST live OUTSIDE the workspace (unreachable by the contained run); integrity is a
domain-separated HMAC under the host's policy_key + a workspace-subject binding.

FAIL DIRECTION differs from provenance: this is a RESTRICTIVE bound, so an UNTRUSTED store (bad MAC /
wrong subject / corrupt) must NOT loosen it. `load` returns ok=False and the caller FAILS CLOSED —
emissions to a quota-LIMITED host are denied until the operator resolves the store (a MISSING store is
still a fresh, trusted start; only a present-but-untrusted one blocks). The agent cannot cause an
untrusted store (out of workspace), so this only guards against disk corruption silently REFRESHING the
bound. Stdlib-only, TOTAL (never raises).
"""

from __future__ import annotations

import hmac
import json
from pathlib import Path

from collaborator import durablestate

COLLABORATOR_EMISSIONQUOTASTORE_VERSION = 1
_DOMAIN = b"salient-emission-quota-store-v1"   # store-kind domain separation (distinct from provenance)


def _canonical(subject, counts) -> bytes:
    """Deterministic signed bytes — counts sorted by host and int-coerced, so a benign reserialization
    still verifies while any changed/added/removed count breaks the MAC."""
    clean = {str(k): int(v) for k, v in (counts or {}).items()}
    return json.dumps(
        {"v": COLLABORATOR_EMISSIONQUOTASTORE_VERSION, "subject": str(subject),
         "counts": {k: clean[k] for k in sorted(clean)}},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _mac(key, payload: bytes) -> str:
    return durablestate.mac(key, payload, _DOMAIN)


def load(path, subject, key):
    """Return ``(counts: dict, ok: bool)``.

    - MISSING file -> ``({}, True)`` — a fresh, trusted start (no persisted budget yet).
    - PRESENT + valid MAC + matching subject -> ``(counts, True)``.
    - PRESENT but UNTRUSTED (bad/missing MAC, subject mismatch, corrupt, any error) -> ``({}, False)``.
      The caller FAILS CLOSED — a quota store it cannot trust must not silently refresh the budget.

    TOTAL — never raises (Session construction + govern paths must not blow up on a bad store)."""
    try:
        p = Path(path)
        if not p.exists():
            return {}, True                                    # fresh, trusted
    except Exception:  # noqa: BLE001
        return {}, False
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        body = doc.get("body")
        mac = doc.get("mac")
        if not isinstance(body, dict) or not isinstance(mac, str):
            return {}, False
        payload = _canonical(body.get("subject"), body.get("counts") or {})
        if not hmac.compare_digest(_mac(key, payload), mac):
            return {}, False                                   # tampered / wrong key -> untrusted
        if str(body.get("subject")) != str(subject):
            return {}, False                                   # a store for a DIFFERENT workspace
        counts = {}
        for k, v in (body.get("counts") or {}).items():
            # A legit counter only ever increments from 0, so a NEGATIVE or non-integer count is
            # malformed — treat the whole store as UNTRUSTED (fail closed) rather than silently FILTER
            # it (external panel gemini: verify-then-filter returned trusted data != the signed data).
            try:
                iv = int(v)
            except (TypeError, ValueError):
                return {}, False
            if iv < 0:
                return {}, False
            counts[str(k)] = iv
        return counts, True
    except Exception:  # noqa: BLE001 — any parse/read error -> untrusted, never a raise
        return {}, False


def save(path, subject, key, counts) -> bool:
    """Persist ``counts`` under ``subject``, HMAC'd with ``key``. TOTAL — never raises; best-effort
    durability (the in-memory counter stays authoritative for the running session)."""
    try:
        clean = {str(k): int(v) for k, v in (counts or {}).items()}
        body = {"subject": str(subject), "counts": {k: clean[k] for k in sorted(clean)}}
        mac = _mac(key, _canonical(body["subject"], body["counts"]))
        doc = json.dumps({"v": COLLABORATOR_EMISSIONQUOTASTORE_VERSION, "body": body, "mac": mac},
                         separators=(",", ":"))
        return durablestate.atomic_write(Path(path), doc)
    except Exception:  # noqa: BLE001
        return False
