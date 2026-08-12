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
        egress_credentials=None,
        emission_quota=None,
    ) -> None:
        self.workspace = Path(workspace)
        # F-6 Harm A: the workspace must be DISJOINT from the Collaborator's own code root
        # (collaborator/ + salienceos/), both directions. Fail LOUD at construction — the fenced
        # write_file/read_file already can't escape the workspace, so a disjoint workspace means
        # they can never name the code, and a governed shell has no in-workspace path to the rules
        # it runs under. See collaborator/codefence.py.
        from collaborator.codefence import disjoint_from_code
        disjoint_from_code(self.workspace)
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
        # Host per-tool leash overrides — validated like proactivity/research_trust, because an
        # unrecognised leash string (a typo, "propose-first" for "propose_first") otherwise flows
        # verbatim through the seam and fails OPEN (red-team F0). Fail LOUD at construction instead.
        from collaborator.tools import ACT_THEN_REPORT, NOTIFY_ONLY, PROPOSE_FIRST
        _valid_leashes = {ACT_THEN_REPORT, PROPOSE_FIRST, NOTIFY_ONLY}
        self.leash_overrides = dict(leash_overrides or {})
        for _tool, _level in self.leash_overrides.items():
            if _level not in _valid_leashes:
                raise ValueError(f"leash_overrides[{_tool!r}] must be one of "
                                 f"{tuple(sorted(_valid_leashes))}, got {_level!r}")
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
        # ADR 0003 Tier 2 — HOST-INJECTED egress credentials for outbound emission (net_post),
        # keyed by CANONICAL host (e.g. {"openrouter.ai": "Bearer …"}), typically built from env.
        # The seam looks one up for the consented host and sets it as the Authorization header
        # itself; the model's args NEVER carry a credential and it is never logged. Authority
        # (which hosts may be emitted-to) lives in the SIGNED caps — this map only supplies the
        # secret for a host already authorized. Default empty = no credential injected.
        self.egress_credentials = dict(egress_credentials or {})
        # ADR 0003 residual sweep — per-DESTINATION emission quota (host config, never model-chosen). The
        # egress caps + byte/time caps bound WHICH host + HOW BIG each emission is, but nothing bounded HOW
        # MANY: once a `net.post:<host>` (or an autonomous net.post.auto:<host>) grant is in hand, a session
        # could emit to that host unbounded — a drip exfil / spam channel, worst on the autonomous path.
        # This caps the emission COUNT per canonical host for the session's life. Shape:
        #   None          -> unlimited (default; unchanged behaviour)
        #   int (>=0)     -> the same cap for EVERY host
        #   {host: int}   -> a per-host cap; a host not listed is unlimited
        # Consumed at the actual dispatch point in execute_and_verify (covers BOTH the autonomous and the
        # human-approved path), fail-closed: an emission over quota does NOT go out. Session-lived in-memory
        # count (a cross-session durable store is a documented follow-up, not needed to bound a runaway run).
        self.emission_quota = self._validate_emission_quota(emission_quota)
        self._emission_counts = {}

    @staticmethod
    def _validate_emission_quota(q):
        """Fail LOUD at construction on a malformed quota (like proactivity/leash_overrides), so a typo
        can't silently disable the bound. Accepts None, a non-negative int, or a {str: non-negative int}."""
        if q is None:
            return None
        if isinstance(q, bool):  # a bool is an int subclass but never a meaningful count
            raise ValueError("emission_quota must be None, a non-negative int, or a {host: int} dict")
        if isinstance(q, int):
            if q < 0:
                raise ValueError("emission_quota int must be >= 0")
            return q
        if isinstance(q, dict):
            from collaborator import egress   # lazy: avoid any import cycle at module load
            out = {}
            for host, cap in q.items():
                if (not isinstance(host, str) or isinstance(cap, bool)
                        or not isinstance(cap, int) or cap < 0):
                    raise ValueError("emission_quota dict must map host:str -> non-negative int")
                # Key on the SAME canonical host the runtime lookup uses (egress.canonical_host), so a
                # natural mixed-case / IDN key still applies instead of SILENTLY disabling the bound
                # (external-panel: the rest of the surface fails LOUD on a typo — this must too). A key
                # that is not a valid bare host, or two keys colliding to the same canonical host, is an
                # operator error -> ValueError, never a quiet no-op.
                canon = egress.canonical_host("https://" + host)
                if canon is None:
                    raise ValueError(f"emission_quota host key {host!r} is not a valid canonical host")
                if canon in out:
                    raise ValueError(f"emission_quota has two keys canonicalizing to {canon!r}")
                out[canon] = cap
            return out
        raise ValueError("emission_quota must be None, a non-negative int, or a {host: int} dict")

    def _emission_limit(self, host):
        """The emission cap for `host`, or None (unlimited)."""
        q = self.emission_quota
        if isinstance(q, int):
            return q
        if isinstance(q, dict):
            return q.get(host)
        return None

    def emission_allowed(self, host) -> bool:
        """True iff another emission to canonical `host` is within the per-destination quota. An
        ineligible host (None) is allowed here — the egress gate already denies it upstream — so this
        method only ever ADDS a bound, never a new allow path."""
        if host is None:
            return True
        limit = self._emission_limit(host)
        if limit is None:
            return True
        return self._emission_counts.get(host, 0) < limit

    def consume_emission(self, host) -> None:
        """Count one emission against `host`'s quota. Called at the dispatch point right before the bytes
        leave, so it bounds ATTEMPTS (a retry of a failed emission still consumes quota)."""
        if host is not None:
            self._emission_counts[host] = self._emission_counts.get(host, 0) + 1
