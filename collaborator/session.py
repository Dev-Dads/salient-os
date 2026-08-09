"""The Collaborator session — the standing, host-set context every action is
governed against.

Everything here is AUTHORITY or configuration, set by you (the host), never chosen
by salience or the model: which capabilities the worker may use, the policy-signing
key, the executor identity the verifier trusts, the workspace root, and the per-tool
leash. Salience (importance) is supplied per action as influence only.
"""

from __future__ import annotations

from pathlib import Path

from salienceos.interpreter.bus import SalienceBus
from salienceos.verifier import Verifier

# Least privilege by default: file read/write in the workspace, but NOT shell
# execution — a session must explicitly grant "shell.exec" to enable run_command.
DEFAULT_CAPABILITIES = ("fs.read:project", "fs.write:project")


class Session:
    def __init__(
        self,
        workspace,
        capabilities=DEFAULT_CAPABILITIES,
        policy_key: bytes = b"collab-policy-key",
        executor_id: str = "collab-exec",
        executor_key: bytes = b"collab-exec-key",
        leash_overrides: "dict | None" = None,
        allow_adaptation: bool = False,
        default_importance: float = 0.3,
        proactivity: str = "conservative",
        now_days: float = 0.0,
        bus: "SalienceBus | None" = None,
        verifier: "Verifier | None" = None,
        policy_caps=None,
        caps_key: "bytes | None" = None,
        fact_view=None,
        history_view=None,
        veto_ledger=None,
        veto_bar_delta: float = 0.15,
        veto_half_life_days: float = 7.0,
        research_trust: str = "read_only_research",
        research_budget: int = 4,
        controlled_paths=(".github",),
        proposal_pool=None,
    ) -> None:
        self.workspace = Path(workspace)
        self.capabilities = tuple(capabilities)
        # ③ optional SIGNED authority grant. When present, the VERIFIED caps are
        # authoritative (the mutable .capabilities above cannot widen them) and each
        # tool's leash is capped by the grant. Verified per action in the seam; a grant
        # that is present but invalid fails closed (zero capabilities, strictest leash).
        self.policy_caps = policy_caps
        self.caps_key = caps_key
        # STICKY: a session built WITH a grant enforces caps for life. Stripping the grant
        # at runtime (policy_caps=None) then fails closed, it does NOT revert to the
        # mutable-config legacy path. Legacy only when constructed with no grant at all.
        self.enforce_caps = policy_caps is not None
        self.policy_key = policy_key
        self.executor_id = executor_id
        self.executor_key = executor_key
        self.leash_overrides = dict(leash_overrides or {})
        self.allow_adaptation = bool(allow_adaptation)
        self.default_importance = float(default_importance)
        # How eagerly the propose channel (Step 1) surfaces unprompted proposals —
        # host config, NEVER model-selectable. Default the conservative end. Because a
        # surfaced proposal grants no authority (it is re-gated on approval), this dial
        # trades quiet vs chatty, never safe vs unsafe.
        if proactivity not in ("off", "conservative", "eager"):
            raise ValueError("proactivity must be 'off', 'conservative', or 'eager'")
        self.proactivity = proactivity
        # Runtime steering state (set from the judgment view, not construction): while
        # paused, the agent's action stream is held. Host authority, never model-set.
        self.paused = False
        nd = float(now_days)  # injected clock for the memory governor
        if nd != nd or nd < 0:  # NaN or negative -> the memory gate would reject it
            raise ValueError("now_days must be a finite non-negative number")
        self.now_days = nd
        # An in-memory audit bus by default; pass SalienceBus(path=...) to persist.
        self.bus = bus if bus is not None else SalienceBus()
        self.verifier = verifier if verifier is not None else Verifier(
            policy_key, {executor_id: executor_key})
        # ④ Memory (design v3). The two typed handles keep the fact/history split
        # STRUCTURAL: the DOER is given only ``fact_view`` (the fact layer); the
        # PROPOSER alone gets ``history_view`` (the gist-tuple layer). The doer's context
        # assembler rejects a HistoryView at the type level, so history-blindness is not a
        # convention. Both may be None (memory simply absent -> the agent runs as before).
        self.fact_view = fact_view          # FactView | None — doer + proposer
        self.history_view = history_view    # HistoryView | None — PROPOSER ONLY
        # The decaying veto inhibitor (surfacing influence only, never authority).
        if veto_ledger is not None:
            self.veto_ledger = veto_ledger
        else:
            from collaborator.vetoledger import VetoLedger
            self.veto_ledger = VetoLedger(veto_bar_delta, veto_half_life_days)
        # The proposer's research trust level + step budget (host config, never model-chosen):
        # how far the proposer may INVESTIGATE (read-only) before proposing, and for how many
        # steps. See collaborator/research.py. Default: local read-only research, 4 steps.
        from collaborator.research import TRUST_LEVELS
        if research_trust not in TRUST_LEVELS:
            raise ValueError(f"research_trust must be one of {TRUST_LEVELS}")
        self.research_trust = research_trust
        self.research_budget = max(0, int(research_budget))
        # Controlled locations (host config, never model-chosen): workspace subtrees that
        # CONFIGURE or EXECUTE the project — default ``.github`` (CI workflows/hooks/actions),
        # which carry repo-level authority. A self-originated PROPOSER write into one is
        # hard-denied so it stages to scratch instead; the placement is a separately-approved
        # act the Collaborator executes. See collaborator/tools.is_controlled_location.
        self.controlled_paths = tuple(controlled_paths or ())
        # The proposal stage pool: a durable home for surfaced-but-undecided proposals, so a
        # proposal the human neither approved nor vetoed is never lost — it stays PENDING and
        # findable (the natural feed for a dashboard's pending queue). Grants no authority.
        if proposal_pool is not None:
            self.proposal_pool = proposal_pool
        else:
            from collaborator.proposalpool import ProposalPool
            self.proposal_pool = ProposalPool()
