"""Canonical serialization, content addressing, and HMAC signing.

MVP signing is HMAC-SHA256 over canonical JSON. Key distribution is out of
scope here: the policy key signs envelopes, executor keys sign receipts, and
the verifier holds verify-side copies. Asymmetric signatures are a drop-in
replacement later; the payload contract is what matters.
"""

import hashlib
import hmac
import json


def canonical_json(obj) -> bytes:
    """Deterministic byte serialization: sorted keys, no whitespace, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def digest(obj) -> str:
    """Content address of a JSON-representable object."""
    return hashlib.sha256(canonical_json(obj)).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sign(payload: dict, key: bytes) -> str:
    return hmac.new(key, canonical_json(payload), hashlib.sha256).hexdigest()


def signature_valid(payload: dict, signature: str, key: bytes) -> bool:
    if not isinstance(signature, str) or not signature:
        return False
    return hmac.compare_digest(sign(payload, key), signature)
