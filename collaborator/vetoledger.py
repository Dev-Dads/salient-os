"""The veto inhibitor — a decaying re-surface bar (design v3, S5).

The v1/v2 panels caught that the "decaying inhibitor" was described but not built: a veto
only marked one proposal object, so re-proposing the same action walked right past it.
This is the real thing. A veto keyed by NORMALIZED INTENT (tool + the stable arg subset)
raises the confidence bar the proposer must clear to re-surface that intent, and the raise
DECAYS over time — so the system learns from a "no" (try later, more sure) without either
nagging or forgetting. Distinct from a Stage-4 NON-decaying hard inhibitor (risk-rejects
via the weight channel's hand-off); this is the soft, decaying, host-side kind.

Host-side state (never in CDMS gist valence): influence on SURFACING only — it can never
touch leash, capability, or budget.
"""

from __future__ import annotations

import json
import os.path
from dataclasses import dataclass

COLLABORATOR_VETO_VERSION = "0.1.0"

# Defaults (host-configurable): a veto adds +0.15 to the surfacing bar, halving every 7
# days, and stops applying once it decays below this epsilon (fully forgotten).
DEFAULT_BAR_DELTA = 0.15
DEFAULT_HALF_LIFE_DAYS = 7.0
_EPSILON = 0.02


def normalize_intent(tool: str, args: dict) -> str:
    """A stable key for 'the same action'. Keys on the tool + the identifying arg — the
    path for file tools, the command for run_command — NOT the full content (so a
    re-proposal with the same target but different body is still recognized)."""
    tool = str(tool or "")
    args = args or {}
    if tool in ("write_file", "read_file"):
        # Canonicalize the path so `./a.txt`, `a/../a.txt`, trailing slashes and (on a
        # case-insensitive FS) `A.TXT` all collapse to the SAME key — otherwise a trivial
        # alias re-surfaces a vetoed action past the inhibitor.
        ident = os.path.normcase(os.path.normpath(str(args.get("path") or "")))
    elif tool == "run_command":
        # Canonicalize a command: drop empty args, and SORT the args after the program so
        # option-reordering (`ls -a -l` vs `ls -l -a`) maps to the same key. This can slightly
        # over-inhibit a positional-arg command (`mv a b` ~ `mv b a`), which is acceptable for
        # a soft, decaying SURFACING control (over-suppressing a re-nag is the safe direction).
        cmd = args.get("command")
        toks = ([str(x) for x in cmd if str(x).strip() != ""]
                if isinstance(cmd, (list, tuple)) else str(cmd or "").split())
        ident = " ".join(toks[:1] + sorted(toks[1:]))
    else:
        try:
            ident = json.dumps(args, sort_keys=True, default=str)[:256]
        except (TypeError, ValueError):
            ident = str(args)[:256]
    return f"{tool}::{ident}"


@dataclass
class _VetoRecord:
    vetoed_at: float          # now_days at veto time
    bar_delta: float
    half_life_days: float


class VetoLedger:
    """Records vetoes and yields the current (decayed) surfacing-bar delta for an intent."""

    def __init__(self, bar_delta: float = DEFAULT_BAR_DELTA,
                 half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> None:
        self.bar_delta = float(bar_delta)
        self.half_life_days = max(1e-6, float(half_life_days))
        self._records: "dict[str, _VetoRecord]" = {}

    def record_veto(self, tool: str, args: dict, now_days: float) -> None:
        key = normalize_intent(tool, args)
        prev = self._records.get(key)
        # Re-vetoing refreshes (and compounds up to a sane ceiling) the inhibitor.
        base = self.bar_delta if prev is None else min(1.0, prev.bar_delta + self.bar_delta * 0.5)
        self._records[key] = _VetoRecord(float(now_days), base, self.half_life_days)

    def surfacing_bar_delta(self, tool: str, args: dict, now_days: float) -> float:
        """The extra confidence a re-proposal of this intent must clear right now. Decays
        by half every ``half_life_days``; once below epsilon it is forgotten (returns 0)."""
        rec = self._records.get(normalize_intent(tool, args))
        if rec is None:
            return 0.0
        age = max(0.0, float(now_days) - rec.vetoed_at)
        delta = rec.bar_delta * (0.5 ** (age / rec.half_life_days))
        return delta if delta >= _EPSILON else 0.0
