"""The Host — one presence that owns the Collaborator's loop, propose channel, view, and
record, and drives them on a single serial worker thread.

② (the seam/partner surface). Every existing piece was proven alone by a throwaway
script; the Host is the thing that assembles them so a caller (a CLI, the local web
surface) talks to ONE object instead of hand-wiring `run_turn` + `propose` + the ledger
every time.

THE LINCHPIN INVARIANT: everything that touches ``run_turn`` / ``govern_action`` /
``execute_and_verify`` / ``propose`` runs on the ONE worker thread, serially, fed by a
job queue. Controls ENQUEUE work; they never call those directly. Two turns on one
session would interleave governance + ledger writes + double-execute; a single worker
makes that impossible by construction.

Thread discipline: a single ``RLock`` guards the compound data structures only — the
task registry, the ledger (append AND the multi-pass snapshot read), and the Host's
proposal index — held for micro-sections, NEVER across a model call or a turn (or the
surface's ``/state`` would freeze). The scalar steering flags (``session.paused`` /
``session.proactivity``) are read lock-free inside ``govern_action`` — that live read
IS the pause/steer feature.

P-01 stays intact: the Host is a new WORKER, never a new AUTHORITY path. Its controls
only restrict (pause, tighten) or express host config (leash, proactivity); every
action still flows through ``govern_action``. Nothing here grants a capability.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field

from collaborator.governance import DENIED, HELD, RAN
from collaborator.loop import (
    STOPPED_AWAITING,
    STOPPED_HELD,
    STOPPED_SUCCESS,
    approve as approve_held_decision,
    run_turn,
)
from collaborator.propose import (
    PROPOSED,
    approve_proposal,
    build_proposer_context,
    propose,
    veto_proposal,
)
from collaborator.view import (
    JudgmentLedger,
    JudgmentView,
    set_leash as view_set_leash,
    set_proactivity as view_set_proactivity,
)

COLLABORATOR_HOST_VERSION = "0.1.0"

# Task lifecycle states.
QUEUED = "queued"
RUNNING = "running"
AWAITING_APPROVAL = "awaiting_approval"  # a propose_first action is held for you
PAUSED = "paused"                        # the session was paused mid-turn
DONE = "done"                            # ran to a normal end
FAILED = "failed"                        # empty / max_iterations / exception — an HONEST failure
CANCELLED = "cancelled"                  # you declined a held task


def _clip(text: str, cap: int) -> str:
    """Show up to ``cap`` chars; if it bites, mark it with an ellipsis (never a silent mid-word cut)."""
    text = text or ""
    return text if len(text) <= cap else text[:cap] + "…"


@dataclass
class Task:
    task_id: str
    prompt: str
    state: str = QUEUED
    reply: str = ""
    decisions: list = field(default_factory=list)  # all governed Decisions across the task
    held: list = field(default_factory=list)        # HELD decisions still awaiting your approval
    approved_ran: list = field(default_factory=list)  # summaries of held actions run across rounds
    history: list = field(default_factory=list)      # the loop's running message history
    error: str = ""

    # Display caps — generous enough that a normal conversational reply is shown IN FULL (the old
    # 2000-char reply cap chopped Sal's answers mid-word, which read as broken "truncation"). A cap
    # still exists so a pathological dump can't bloat every /state poll; when it bites we say so with
    # an ellipsis rather than cut silently.
    _PROMPT_CAP = 4000
    _REPLY_CAP = 16000

    def view(self) -> dict:
        """A display-safe projection (no raw history / args) for the surface."""
        return {
            "id": self.task_id,
            "prompt": _clip(self.prompt, self._PROMPT_CAP),
            "state": self.state,
            "reply": _clip(self.reply, self._REPLY_CAP),
            "decisions": len(self.decisions),
            "held": [d.summary() for d in self.held],
            "error": self.error[:400],
        }


# --- job types (worker-only execution) ---------------------------------------

@dataclass
class _TurnJob:
    task_id: str
    user_message: str


@dataclass
class _ResumeJob:
    task_id: str


@dataclass
class _ApproveProposalJob:
    proposal_id: str


@dataclass
class _ProposeJob:
    pass


_STOP = object()  # sentinel to stop the worker


class Collaborator:
    """The Host. Construct with a session and one or two model clients, then ``start()``.
    ``submit(text)`` queues a turn; ``snapshot()`` is what a surface renders; the control
    methods (``pause``/``resume``/``set_leash``/``set_proactivity``/``veto``/``approve``/
    ``decline``) are host authority — pure-state ones act inline, executing ones enqueue."""

    def __init__(self, session, doer_client, proposer_client=None, *,
                 ledger=None, idle_seconds: float = 45.0, propose_cooldown: float = 120.0,
                 tick_seconds: float = 5.0, clock=time.monotonic) -> None:
        self.session = session
        self.doer_client = doer_client
        # The proposer is a separate complete() consumer; default to the doer so a caller
        # need only pass one, but keep the seam so tests can inject two ScriptedClients.
        self.proposer_client = proposer_client if proposer_client is not None else doer_client
        self.ledger = ledger if ledger is not None else JudgmentLedger()
        self.view = JudgmentView(session, self.ledger)

        self._lock = threading.RLock()
        self._jobs: "queue.Queue" = queue.Queue()
        self._tasks: dict = {}
        self._proposals: dict = {}       # Host index; same Proposal objects the pool holds
        self._worker_busy = False
        # THE CONVERSATION. Sal is "one presence you TALK TO" — so each new turn continues the same
        # running history instead of starting blank (else Sal can't remember a file it just read or a
        # thing you just said). Worker-confined (only the serial worker reads/writes it); advanced on
        # every completed turn/resume in _absorb_result. run_turn re-prepends its system grounding
        # idempotently, so threading a prior history is safe.
        self._history = None

        self._idle_seconds = float(idle_seconds)
        self._propose_cooldown = float(propose_cooldown)
        self._tick_seconds = float(tick_seconds)
        self._clock = clock
        self._last_activity = clock()
        self._last_propose = clock() - self._propose_cooldown  # allow an early first proposal
        self._propose_pending = False

        self._stopping = threading.Event()
        self._worker = threading.Thread(target=self._run_worker, name="collab-worker", daemon=True)
        self._ticker = threading.Thread(target=self._run_ticker, name="collab-ticker", daemon=True)
        self._started = False

    # --- lifecycle -----------------------------------------------------------

    def start(self) -> "Collaborator":
        if not self._started:
            self._started = True
            self._worker.start()
            self._ticker.start()
        return self

    def stop(self, join_timeout: float = 5.0) -> None:
        self._stopping.set()
        self._jobs.put(_STOP)
        if self._worker.is_alive():
            self._worker.join(timeout=join_timeout)

    # --- public API ----------------------------------------------------------

    def submit(self, user_message: str) -> str:
        """Queue a new user turn. Returns the task id."""
        task = Task(task_id="task-" + uuid.uuid4().hex[:16], prompt=str(user_message))
        with self._lock:
            self._tasks[task.task_id] = task
            self._touch()
        self._jobs.put(_TurnJob(task.task_id, str(user_message)))
        return task.task_id

    def snapshot(self) -> dict:
        """What a surface renders. The whole read is under the lock so the view's multi-pass
        snapshot is atomic w.r.t. worker appends (no internally inconsistent dashboard)."""
        with self._lock:
            snap = self.view.snapshot()
            snap["tasks"] = [t.view() for t in self._tasks.values()]
            snap["busy"] = self._worker_busy
            return snap

    def get_task(self, task_id: str) -> "dict | None":
        with self._lock:
            t = self._tasks.get(task_id)
            return t.view() if t is not None else None

    # --- controls: pure-state (inline, under lock) ---------------------------

    def pause(self) -> None:
        with self._lock:
            self.session.paused = True
            self._touch()

    def resume(self) -> None:
        with self._lock:
            self.session.paused = False
            # re-queue any task the pause halted so it continues
            resumable = [t.task_id for t in self._tasks.values() if t.state == PAUSED]
            for tid in resumable:
                self._tasks[tid].state = QUEUED
            self._touch()
        for tid in resumable:
            self._jobs.put(_ResumeJob(tid))

    def set_leash(self, tool_name: str, leash: str) -> bool:
        with self._lock:
            ok = view_set_leash(self.session, tool_name, leash)
            self._touch()
            return ok

    def set_proactivity(self, level: str) -> bool:
        with self._lock:
            ok = view_set_proactivity(self.session, level)
            self._touch()
            return ok

    def veto(self, proposal_id: str) -> bool:
        """Veto a surfaced proposal. Pure state flip (nothing runs) — safe inline."""
        with self._lock:
            p = self._proposals.get(proposal_id)
            if p is None or p.status != PROPOSED:
                return False
            veto_proposal(self.session, p)
            self._touch()
            return True

    def decline(self, task_id: str) -> bool:
        """Wave off a task holding actions for you (nothing runs)."""
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None or t.state != AWAITING_APPROVAL:
                return False
            t.state = CANCELLED
            self._touch()
            return True

    # --- controls: executing (ENQUEUE to the worker — they run actions) ------

    def approve(self, task_id: str) -> bool:
        """Approve the held action(s) of a task and resume it. Enqueued — the approval
        executes ``loop.approve`` (real I/O) on the worker, never on the caller's thread."""
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None or t.state != AWAITING_APPROVAL:
                return False
            t.state = RUNNING
            self._touch()
        self._jobs.put(_ResumeJob(task_id))
        return True

    def approve_proposal(self, proposal_id: str) -> bool:
        """Approve a surfaced proposal into existence (runs it). Enqueued — executes on the
        worker (the capability gate re-applies at run time)."""
        with self._lock:
            p = self._proposals.get(proposal_id)
            if p is None or p.status != PROPOSED:
                return False
            self._touch()
        self._jobs.put(_ApproveProposalJob(proposal_id))
        return True

    # --- the worker (all execution happens here, serially) -------------------

    def _run_worker(self) -> None:
        while True:
            job = self._jobs.get()
            if job is _STOP:
                return
            with self._lock:
                self._worker_busy = True
            try:
                self._dispatch(job)
            except Exception as e:  # noqa: BLE001 — a worker crash must not kill the thread
                self._fail_active(job, e)
            finally:
                with self._lock:
                    self._worker_busy = False

    def _dispatch(self, job) -> None:
        if isinstance(job, _TurnJob):
            self._handle_turn(job)
        elif isinstance(job, _ResumeJob):
            self._handle_resume(job)
        elif isinstance(job, _ApproveProposalJob):
            self._handle_approve_proposal(job)
        elif isinstance(job, _ProposeJob):
            self._handle_propose()

    def _handle_turn(self, job: "_TurnJob") -> None:
        with self._lock:
            task = self._tasks.get(job.task_id)
            if task is None:
                return
            task.state = RUNNING
            self._touch()
        # Continue the CONVERSATION (thread the running history) rather than start blank — this is
        # what makes Sal remember across your messages.
        result = run_turn(self.session, self.doer_client, job.user_message,
                          history=self._history)  # UNLOCKED
        self._absorb_result(job.task_id, result)

    def _handle_resume(self, job: "_ResumeJob") -> None:
        with self._lock:
            task = self._tasks.get(job.task_id)
            # Defensive invariant (panel gpt-5.1/qwen): a ResumeJob is only ever enqueued for a
            # task left in a valid pre-resume state (RUNNING by approve(), QUEUED by resume()).
            # Re-validate under the lock so a stale/duplicate job can never re-enter run_turn on a
            # task that has since been declined/finished.
            if task is None or task.state not in (RUNNING, QUEUED):
                return
            held = list(task.held)
            history = task.history
            task.state = RUNNING
            self._touch()
        if held:
            # A task holding propose_first action(s) for approval. Run each NOW (re-gates
            # authority; single-use). UNLOCKED.
            ran = [approve_held_decision(self.session, d) for d in held]
            # Keep any that could NOT run (DENIED — e.g. capability revoked; loop.approve does not
            # consume those, so they stay retryable) as STILL-HELD, and remember the ones that DID
            # run so a later round's resume note covers ALL approved actions. Never silently drop a
            # held decision on a PARTIAL deny (panel grok F1).
            with self._lock:
                self.ledger.record_decisions(ran)
                task.decisions.extend(ran)
                task.approved_ran.extend(d.summary() for d in ran if d.status == RAN)
                remaining = [held[i] for i, d in enumerate(ran) if d.status != RAN]
                task.held = remaining
            if remaining:
                # Some held actions still can't run — keep the task AWAITING (re-approvable); do
                # NOT resume the turn until every held action has cleared. The ones that DID run
                # are recorded and their results are held on the task for the eventual resume.
                with self._lock:
                    task.state = AWAITING_APPROVAL
                    self._touch()
                return
            # All held actions have now cleared (across one or more rounds). Resume with a
            # HOST-AUTHORED authoritative note (never the human's free text) covering ALL of them.
            with self._lock:
                approved = list(task.approved_ran)
                task.approved_ran = []
            note = ("TOOL RESULTS (approved by the human, now executed — authoritative, treat as "
                    "ground truth):\n" + "\n".join(approved))
        else:
            # A task the HOST paused mid-turn (no held decisions to approve — the paused action
            # never ran). Now unpaused, just continue the turn; the model re-issues its next step.
            note = "(resumed — the session is active again; continue the task.)"
        result = run_turn(self.session, self.doer_client, note, history=history)  # UNLOCKED
        self._absorb_result(job.task_id, result)

    def _handle_approve_proposal(self, job: "_ApproveProposalJob") -> None:
        with self._lock:
            p = self._proposals.get(job.proposal_id)
            if p is None or p.status != PROPOSED:
                return
        d = approve_proposal(self.session, p)  # UNLOCKED (executes; re-gates capability)
        with self._lock:
            self.ledger.record_decision(d)  # approve_proposal does NOT record — the Host must
            self._touch()

    def _handle_propose(self) -> None:
        with self._lock:
            self._propose_pending = False
            recent = [self._action_str(d) for d in self.ledger.decisions[-8:]]
        ctx = build_proposer_context(self.session, recent_actions=recent)  # read-only session views
        props = propose(self.session, self.proposer_client, ctx)  # UNLOCKED; fail-closed ([] on any issue)
        with self._lock:
            self.ledger.record_proposals(props)
            for p in props:
                self._proposals[p.proposal_id] = p
            self._last_propose = self._clock()

    def _absorb_result(self, task_id: str, result) -> None:
        """Record a turn's outcome + map ``stopped`` -> task state, atomically."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            self.ledger.record_decisions(result.decisions)
            task.decisions.extend(result.decisions)
            task.history = result.history
            # Advance THE CONVERSATION so the next turn (a new message, or a resume) continues from
            # here — this is the memory that was missing. Worker-confined; the serial worker means
            # exactly one turn is ever in flight, so this stays consistent.
            self._history = result.history
            task.reply = result.reply
            if result.stopped in STOPPED_SUCCESS:
                task.state = DONE
            elif result.stopped in STOPPED_AWAITING:
                if result.stopped == STOPPED_HELD:
                    task.held = [d for d in result.decisions if d.status == HELD]
                    task.state = AWAITING_APPROVAL
                else:  # STOPPED_PAUSED
                    task.state = PAUSED
            else:  # STOPPED_FAILED (EMPTY / MAX_ITERATIONS) or anything unexpected -> HONEST failure
                task.state = FAILED
            self._touch()

    def _fail_active(self, job, exc: Exception) -> None:
        tid = getattr(job, "task_id", None)
        if tid is None:
            return
        with self._lock:
            t = self._tasks.get(tid)
            if t is not None:
                t.state = FAILED
                t.error = f"{type(exc).__name__}: {exc}"
                self._touch()

    # --- propose trigger (idle) ---------------------------------------------

    def _run_ticker(self) -> None:
        while not self._stopping.wait(self._tick_seconds):
            enqueue = False
            with self._lock:
                if self._should_propose():
                    self._propose_pending = True
                    enqueue = True
            if enqueue:
                self._jobs.put(_ProposeJob())

    def _should_propose(self) -> bool:
        """Fire an idle-time proposal only when ALL hold (checked under the lock)."""
        if getattr(self.session, "proactivity", "conservative") == "off":
            return False
        if self._worker_busy or not self._jobs.empty():
            return False
        if any(t.state in (RUNNING, QUEUED) for t in self._tasks.values()):
            return False
        if self._propose_pending:
            return False
        if any(p.status == PROPOSED for p in self.ledger.proposals):
            return False  # don't stack proposals
        now = self._clock()
        if now - self._last_activity < self._idle_seconds:
            return False
        if now - self._last_propose < self._propose_cooldown:
            return False
        return True

    # --- helpers -------------------------------------------------------------

    def _touch(self) -> None:
        """Mark activity (resets the idle clock). Caller holds the lock."""
        self._last_activity = self._clock()

    @staticmethod
    def _action_str(d) -> str:
        return f"{d.tool} -> {d.status}"
