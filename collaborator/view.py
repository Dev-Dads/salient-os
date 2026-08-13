"""② The judgment view — the Collaborator's own surface.

Not a chat box: a VIEW of what the Collaborator is attending to, running, and
proposing, with the leashes, the proactivity dial, and a pause as controls the host can
put a hand on — steer a job without typing a sentence.

The view is read-only display; the controls are HOST authority (they mutate session
config/state, never model output), and every one of them is either restrictive (pause,
tighten) or the host's own setting (leash, proactivity) — none grants the model new
authority. P-01 is untouched: the controls change *scrutiny and whether the agent may
proceed*, never *what a capability permits*.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from collaborator.governance import FAILED, PAUSED, RAN, Decision
from collaborator.policycaps import apply_cap, granted_capabilities, leash_cap
from collaborator.propose import PROPOSED, Proposal, approve_proposal, veto_proposal
from collaborator.tools import ACT_THEN_REPORT, NOTIFY_ONLY, PROPOSE_FIRST, toolset

COLLABORATOR_VIEW_VERSION = "0.1.0"

_VALID_LEASHES = (ACT_THEN_REPORT, PROPOSE_FIRST, NOTIFY_ONLY)
_VALID_PROACTIVITY = ("off", "conservative", "eager")


class JudgmentLedger:
    """What the view shows: the stream of governed decisions and surfaced proposals. The
    host records into it as it drives (run_turn returns decisions; propose returns
    proposals), so govern_action/propose stay uncoupled from any display."""

    def __init__(self) -> None:
        self.decisions: list[Decision] = []
        self.proposals: list[Proposal] = []

    def record_decision(self, d: "Decision | None") -> None:
        if d is not None:
            self.decisions.append(d)

    def record_decisions(self, ds) -> None:
        for d in ds or ():
            self.record_decision(d)

    def record_proposal(self, p: "Proposal | None") -> None:
        if p is not None:
            self.proposals.append(p)

    def record_proposals(self, ps) -> None:
        for p in ps or ():
            self.record_proposal(p)


# --- controls: host authority, never model-set -------------------------------

def set_leash(session, tool_name: str, leash: str) -> bool:
    """Set a per-tool leash from the view. Returns True if applied. An invalid leash is
    REJECTED (no change) — the view never loosens the seam to an undefined state."""
    if leash not in _VALID_LEASHES:
        return False
    session.leash_overrides = dict(getattr(session, "leash_overrides", {}) or {})
    session.leash_overrides[tool_name] = leash
    return True


def set_proactivity(session, level: str) -> bool:
    if level not in _VALID_PROACTIVITY:
        return False
    session.proactivity = level
    return True


def pause(session) -> None:
    """Hold the agent's action stream (the next governed action is PAUSED, not run)."""
    session.paused = True


def resume(session) -> None:
    session.paused = False


def veto(session, ledger: "JudgmentLedger | None", proposal: Proposal) -> Proposal:
    """Veto a proposal from the view (nothing runs)."""
    return veto_proposal(session, proposal)


def approve(session, ledger: "JudgmentLedger | None", proposal: Proposal) -> Decision:
    """Approve a proposal from the view; the run record is added to the ledger."""
    d = approve_proposal(session, proposal)
    if ledger is not None:
        ledger.record_decision(d)
    return d


# --- the view ----------------------------------------------------------------

@dataclass
class JudgmentView:
    session: object
    ledger: JudgmentLedger

    def _leashes(self) -> dict:
        # The EFFECTIVE leash the seam will enforce — host config capped by any signed
        # grant — not the raw override, so the surface never shows a looseness the grant
        # forbids (panel: the view must display effective authority, not mutable config).
        overrides = getattr(self.session, "leash_overrides", {}) or {}
        return {name: apply_cap(overrides.get(name, tool.default_leash),
                                leash_cap(self.session, name))
                for name, tool in toolset().items()}

    def _decision(self, d: Decision) -> dict:
        return {"tool": d.tool, "status": d.status, "leash": d.leash,
                "origin": getattr(d, "origin", "direct"), "summary": d.summary()}

    def _proposal(self, p: Proposal) -> dict:
        return {"id": p.proposal_id, "tool": p.decision.tool, "confidence": p.confidence,
                "rationale": p.rationale, "leash": p.decision.leash, "summary": p.summary()}

    def snapshot(self) -> dict:
        ds = self.ledger.decisions
        pending = [p for p in self.ledger.proposals if p.status == PROPOSED]
        return {
            "paused": bool(getattr(self.session, "paused", False)),
            "proactivity": getattr(self.session, "proactivity", "conservative"),
            "capabilities": list(granted_capabilities(self.session)),  # effective, grant-verified
            "leashes": self._leashes(),
            "attending": [self._decision(d) for d in ds[-8:]],
            "ran": [self._decision(d) for d in ds if d.status in (RAN, FAILED)][-8:],
            "proposals": [self._proposal(p) for p in pending],
            "counts": {
                "governed": len(ds),
                "ran": sum(1 for d in ds if d.status == RAN),
                "held": sum(1 for d in ds if d.status not in (RAN, FAILED)),
                "paused": sum(1 for d in ds if d.status == PAUSED),
                "proposals_pending": len(pending),
            },
        }

    def render_html(self) -> str:
        """A self-contained, theme-aware snapshot of the view (no external assets)."""
        s = self.snapshot()
        e = html.escape

        def badge(status: str) -> str:
            color = {"ran": "var(--ok)", "failed": "var(--bad)", "held": "var(--warn)",
                     "paused": "var(--warn)", "denied": "var(--bad)",
                     "notified": "var(--muted)"}.get(status, "var(--muted)")
            return f'<span class="badge" style="background:{color}">{e(status)}</span>'

        leashes = "".join(
            f'<div class="chip"><b>{e(name)}</b><span>{e(leash)}</span></div>'
            for name, leash in s["leashes"].items())
        attending = "".join(
            f'<li>{badge(d["status"])} <code>{e(d["tool"])}</code> '
            f'<span class="leash">{e(d["leash"])}</span> '
            f'<span class="origin">{e(d["origin"])}</span>'
            f'<div class="sum">{e(d["summary"])}</div></li>'
            for d in reversed(s["attending"])) or '<li class="empty">nothing yet</li>'
        proposals = "".join(
            f'<li><span class="conf">{p["confidence"]:.2f}</span> '
            f'<code>{e(p["tool"])}</code> — {e(p["rationale"])}'
            f'<div class="acts">⟨approve⟩ ⟨veto⟩</div></li>'
            for p in s["proposals"]) or '<li class="empty">no proposals waiting</li>'
        caps = "".join(f'<span class="cap">{e(c)}</span>' for c in s["capabilities"])
        paused = s["paused"]
        c = s["counts"]

        return f"""<!-- collaborator judgment view (self-contained) -->
<style>
  :root {{ --bg:#f7f7f8; --card:#fff; --ink:#1a1a1f; --muted:#8a8a94; --line:#e5e5ea;
    --ok:#1f9d55; --bad:#c0392b; --warn:#c77d0a; --accent:#4b6bfb; }}
  @media (prefers-color-scheme: dark) {{ :root:not([data-theme=light]) {{
    --bg:#16161a; --card:#1f1f26; --ink:#f0f0f4; --muted:#9a9aa6; --line:#2c2c36;
    --ok:#2ecc71; --bad:#e74c3c; --warn:#e0a028; --accent:#7d92fb; }} }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font:14px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }}
  .wrap {{ max-width:920px; margin:0 auto; padding:24px; }}
  h1 {{ font-size:19px; margin:0 0 2px; }}
  .sub {{ color:var(--muted); font-size:13px; margin-bottom:18px; }}
  .status {{ display:inline-block; padding:2px 10px; border-radius:999px; font-weight:600;
    font-size:12px; color:#fff; background:{"var(--warn)" if paused else "var(--ok)"}; }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  @media (max-width:680px) {{ .grid {{ grid-template-columns:1fr; }} }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; }}
  .card h2 {{ font-size:12px; text-transform:uppercase; letter-spacing:.06em;
    color:var(--muted); margin:0 0 12px; }}
  .chip {{ display:inline-flex; gap:8px; align-items:center; border:1px solid var(--line);
    border-radius:8px; padding:5px 10px; margin:0 6px 6px 0; font-size:13px; }}
  .chip span {{ color:var(--accent); font-weight:600; }}
  .cap {{ display:inline-block; background:var(--line); border-radius:6px; padding:2px 8px;
    margin:0 6px 6px 0; font-size:12px; font-family:ui-monospace,monospace; }}
  ul {{ list-style:none; margin:0; padding:0; }}
  li {{ padding:9px 0; border-top:1px solid var(--line); }}
  li:first-child {{ border-top:0; }}
  .badge {{ color:#fff; border-radius:6px; padding:1px 7px; font-size:11px; font-weight:600; }}
  code {{ font-family:ui-monospace,monospace; font-size:13px; }}
  .leash,.origin {{ font-size:11px; color:var(--muted); }}
  .origin {{ font-style:italic; }}
  .sum {{ color:var(--muted); font-size:12px; margin-top:3px; word-break:break-word; }}
  .conf {{ display:inline-block; min-width:34px; font-weight:700; color:var(--accent); }}
  .acts {{ color:var(--muted); font-size:12px; margin-top:3px; }}
  .empty {{ color:var(--muted); font-style:italic; }}
  .counts {{ display:flex; gap:18px; flex-wrap:wrap; margin-top:6px; }}
  .counts div {{ font-size:12px; color:var(--muted); }}
  .counts b {{ display:block; font-size:18px; color:var(--ink); }}
</style>
<div class="wrap">
  <h1>Collaborator &mdash; Judgment View
    <span class="status">{"PAUSED" if paused else "ACTIVE"}</span></h1>
  <div class="sub">what it is attending to, running, and proposing &mdash; and the
    leashes you can put a hand on. Proactivity: <b>{e(s["proactivity"])}</b></div>
  <div class="card" style="margin-bottom:16px">
    <h2>Controls (host authority)</h2>
    <div>{leashes}</div>
    <div style="margin-top:8px">Capabilities: {caps or '<span class="empty">none</span>'}</div>
    <div class="counts">
      <div><b>{c["governed"]}</b>governed</div>
      <div><b>{c["ran"]}</b>ran</div>
      <div><b>{c["held"]}</b>held</div>
      <div><b>{c["paused"]}</b>paused</div>
      <div><b>{c["proposals_pending"]}</b>proposals</div>
    </div>
  </div>
  <div class="grid">
    <div class="card"><h2>Attending &amp; running</h2><ul>{attending}</ul></div>
    <div class="card"><h2>Proposing (awaiting you)</h2><ul>{proposals}</ul></div>
  </div>
</div>"""
