"""Best-effort recognition of a ``run_command`` naming the OPERATOR's sensitive host paths
(SSH keys, cloud/OS credentials) — a DIFFERENT concern from ``codefence`` (which protects the
Collaborator's OWN code). Cross-platform, stdlib-only, NO new dependency.

DEFENCE-IN-DEPTH ONLY — **NOT a boundary, and — unlike ``codefence`` — there is NO planned
structural boundary here.** ``codefence`` has a *deferred* real fence (``code_protection_available``
graduates to an OS-level read-only bind); this module does not, and none is coming: the operator
DELIBERATELY keeps a human-approved ``run_command`` at full filesystem reach so system maintenance is
not broken. Nothing here structurally contains a shell's read of a host secret. This module ONLY

  * hard-denies a PROPOSER self-originating a secret-touching shell command (the confused-deputy
    one-click trap — an adversarial / memory-poisoned proposer surfacing ``cat ~/.ssh/id_rsa`` with a
    plausible rationale for a tired human to approve),
  * surfaces a ``⚠`` in the human approval preview, and
  * carries an audit-only tag on execution.

A shell reaches a secret WITHOUT naming it literally — a variable, ``$HOME``/``~`` built at runtime,
base64, a glob, ``tar czf - ~``, ``/proc/self/environ``, an ``env`` dump — and ALL of those are OUT OF
SCOPE by construction, exactly as in ``codefence.names_code_root``. The marker list therefore errs hard
toward PRECISION over recall: missing some is acceptable (this was never a fence), but a FALSE ``⚠``
that noise-blinds the approving human, or an over-deny of legitimate work, is the real failure. Do NOT
mistake this recognizer for containment; the containment story for the operator's secrets is the 1b
contained-execution view (``bwrap --clearenv`` + least-privilege mount ns) on the *autonomy* path only.
"""

from __future__ import annotations

COLLABORATOR_SENSITIVEPATHS_VERSION = "0.1.0"

# HIGH-PRECISION, cross-platform markers of the OPERATOR's host secrets. Matched as a substring on the
# separator-normalized, LOWERCASED command text (see ``names_sensitive_path``). Directory markers keep a
# trailing "/"; multi-tenant dirs use a TWO-SEGMENT form (".aws/credentials", NOT bare ".aws") so a
# benign mention of the parent (e.g. editing ~/.aws/config region settings) does not fire.
#
# DELIBERATE EXCLUSIONS (documented, not omissions):
#   * ".env" — a substring hit on .env.example/.env.local/.env.sample/.envrc; it would ⚠ nearly every
#     dev command AND over-deny `cp .env.example .env` on the proposer path. It is also the WRONG AXIS:
#     .env is workspace-local project config, not a HOST secret. Excluded — recall loss is acceptable.
#   * ".npmrc" — touched constantly by npm / CI / Dockerfiles → noise.
#   * bare "credentials.json" / "login data" (unanchored) — too generic.
# Accepted porous false-positive: dumb substring means "id_rsa" also hits "grid_rsa" — rare, and on the
# DENY path it only affects a model-authored proposer command (recoverable via human-direction). Kept
# faithful to ``names_code_root`` (dumb substring, no token anchoring) and documented rather than
# complicated. The trailing "borderline tier" is safe to drop first if an FP-audit complains.
_SENSITIVE_MARKERS: "tuple[str, ...]" = (
    # SSH — the whole dir is sensitive, plus the distinctive private-key filenames
    ".ssh/", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "authorized_keys",
    # cloud-provider credentials (two-segment anchored so ~/.aws/config etc. does not fire)
    ".aws/credentials", ".config/gcloud", ".azure/", ".kube/config",
    # generic secret files
    ".netrc", "_netrc", ".pgpass", ".git-credentials", ".gnupg/",
    # OS secret stores (unix)
    "/etc/shadow", "/etc/gshadow", "/etc/sudoers",
    # OS / browser keychains
    "login.keychain", "login.keychain-db", "google/chrome/user data",
    # borderline tier (registry / vcs / registry-auth) — drop first if an FP-audit complains
    ".pypirc", ".docker/config.json",
)


def names_sensitive_path(command) -> str:
    """POROUS, best-effort tag of a ``run_command`` whose ``command`` literally names one of the
    operator's sensitive host paths (``_SENSITIVE_MARKERS``). Returns the matched marker(s) joined by
    ``,`` (empty string = no literal match). DEFENCE-IN-DEPTH ONLY — NOT a boundary (module docstring).

    Mirrors ``codefence.names_code_root`` (join list/tuple argv with spaces, normalize ``\\`` -> ``/``,
    substring test) with ONE deliberate divergence: it also LOWERCASES the haystack. ``names_code_root``
    matches EXACT resolved absolute paths so it needs no folding; these markers are conventional short
    lowercase names, and Windows/macOS filesystems are case-insensitive — folding buys cross-platform
    recall (``C:\\Users\\me\\.SSH\\id_rsa``) at negligible false-positive cost for THIS specific list."""
    if isinstance(command, (list, tuple)):
        text = " ".join(str(c) for c in command)
    else:
        text = str(command or "")
    if not text:
        return ""
    hay = text.replace("\\", "/").lower()
    matched: list[str] = []
    for marker in _SENSITIVE_MARKERS:  # markers are pre-lowercased literals
        if marker in hay and marker not in matched:
            matched.append(marker)
    return ",".join(matched)
