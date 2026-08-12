"""Cross-session DURABLE store for autonomy-authorship provenance (F2 follow-up).

The F2 manifest (``session._autonomous_authored``) is session-lived: an autonomous drop in one session
is forgotten by the next, so a human in a LATER session runs the dropped file with no ⚠. This persists
the manifest across sessions — HOST-controlled and integrity-protected — so the provenance survives.

THREAT MODEL — the AGENT is the adversary (F2 is a deceived human running agent-authored bytes):
  * The store MUST live OUTSIDE the workspace. The contained autonomous run can write ONLY the workspace
    (deny-by-default bwrap), so an out-of-workspace store is beyond its reach; the human maintenance run
    has full FS reach but is the TRUSTED hand. Session construction fails LOUD if the store path is inside
    the workspace (a store the agent could rewrite would let a drop launder away its own taint).
  * Integrity is an HMAC-SHA256 under the host's ``policy_key`` — an in-process secret, never written to
    the workspace, unreachable by the contained run — plus a workspace-SUBJECT binding. So the agent
    cannot FORGE a "clean" store, and a store for a DIFFERENT workspace cannot be replayed in.
  * It does NOT defend against a human-AUTHORIZED deletion (a human-approved run with full reach could
    `rm` the store). That degrades to an EMPTY store = honest loss of prior taints, NEVER a false "clean"
    — and the ⚠ still fires at approval BEFORE such a run. Same trust boundary as the rest of the system:
    the human owns the machine; the AGENT is what this bounds.

ADVISORY, like the manifest itself. A load that cannot be TRUSTED (missing/mismatched HMAC, wrong
subject, corrupt) fails HONEST: it returns an empty set + a not-ok signal the caller surfaces as
degraded tracking (never a silent fake-trust, never a raise). Stdlib-only, TOTAL.

CONCURRENCY (documented scope, external panel): the store is designed for SEQUENTIAL sessions over a
workspace — each session loads the latest, mutates, and writes back (``os.replace`` is atomic on the
same filesystem, so a reader never sees a torn file). Two sessions on the SAME workspace running
CONCURRENTLY are NOT serialized: a last-writer-wins ``os.replace`` could drop the other's taints. That
is an advisory data-loss edge (a missed ⚠) in an unsupported concurrent mode, not a forge/tamper — a
file-lock for that mode is a documented follow-up. A save FAILURE is surfaced honestly by the caller
(``Session._persist_provenance`` marks tracking degraded), never silently trusted-as-complete later.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

COLLABORATOR_PROVENANCESTORE_VERSION = 1


def _canonical(subject, authored, incomplete) -> bytes:
    """The deterministic bytes the HMAC is taken over — sorted authored set so a reorder/dup of the
    stored list (without changing the SET) still verifies, while any added/removed path breaks it."""
    return json.dumps(
        {"v": COLLABORATOR_PROVENANCESTORE_VERSION, "subject": str(subject),
         "authored": sorted(str(x) for x in (authored or ())), "incomplete": bool(incomplete)},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _mac(key, payload: bytes) -> str:
    # KEY SEPARATION (external panel gpt): derive a DEDICATED store key from policy_key rather than
    # HMAC'ing under policy_key directly, so a hypothetical signing oracle for policy_key elsewhere
    # cannot produce a valid store MAC (and vice versa). Integrity STILL assumes policy_key is a host
    # SECRET — with the shipped public DEFAULT key it degrades to corruption-detection, but the
    # load-bearing property is the store's UNREACHABILITY by the agent (kept outside the workspace),
    # not the MAC; the MAC is defense-in-depth + honest corruption/replay detection.
    store_key = hmac.new(bytes(key), b"salient-provenance-store-v1", hashlib.sha256).digest()
    return hmac.new(store_key, payload, hashlib.sha256).hexdigest()


def load(path, subject, key):
    """Return ``(authored: set, incomplete: bool, ok: bool)``.

    - MISSING file -> ``(set(), False, True)`` — a fresh, trusted start (no store yet).
    - PRESENT + valid HMAC + matching subject -> the persisted ``(authored, incomplete, True)``.
    - PRESENT but UNTRUSTED (bad/missing HMAC, subject mismatch, corrupt, any error) ->
      ``(set(), True, False)`` — the caller marks tracking DEGRADED rather than trust it or crash.

    TOTAL — never raises (govern paths + Session construction must not blow up on a bad store file)."""
    try:
        p = Path(path)
        if not p.exists():
            return set(), False, True                      # fresh start
    except Exception:  # noqa: BLE001 — a pathological path -> untrusted, honest
        return set(), True, False
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        body = doc.get("body")
        mac = doc.get("mac")
        if not isinstance(body, dict) or not isinstance(mac, str):
            return set(), True, False                      # corrupt shape -> untrusted
        payload = _canonical(body.get("subject"), body.get("authored") or [], body.get("incomplete"))
        if not hmac.compare_digest(_mac(key, payload), mac):
            return set(), True, False                      # tampered / wrong key -> untrusted
        if str(body.get("subject")) != str(subject):
            return set(), True, False                      # a store for a DIFFERENT workspace -> untrusted
        authored = set(str(x) for x in (body.get("authored") or []))
        return authored, bool(body.get("incomplete")), True
    except Exception:  # noqa: BLE001 — any parse/read error -> untrusted, never a raise, never fake-trust
        return set(), True, False


def save(path, subject, key, authored, incomplete) -> bool:
    """Persist ``authored``/``incomplete`` under ``subject``, HMAC'd with ``key``. Returns True on
    success. TOTAL — never raises (best-effort durability; the in-memory manifest stays authoritative
    for the running session, so a save failure degrades durability, not correctness)."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        authored_sorted = sorted(str(x) for x in (authored or ()))
        body = {"subject": str(subject), "authored": authored_sorted, "incomplete": bool(incomplete)}
        mac = _mac(key, _canonical(body["subject"], body["authored"], body["incomplete"]))
        doc = json.dumps({"v": COLLABORATOR_PROVENANCESTORE_VERSION, "body": body, "mac": mac},
                         separators=(",", ":"))
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(doc, encoding="utf-8")
        os.replace(str(tmp), str(p))                       # atomic-ish swap over the live store
        return True
    except Exception:  # noqa: BLE001
        return False
