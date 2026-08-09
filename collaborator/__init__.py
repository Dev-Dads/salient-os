"""The Collaborator — a governed agent loop we own.

P-01 made physical at the hands: a loop, a tool-call parser, and a small
workspace-fenced toolset that we control, where every tool action is mediated by
the salienceos judgment core before it runs. Importance buys scrutiny and compute;
only policy grants capability. Impure by design (talks to a model, runs tools) — it
lives BESIDE the pure `salienceos` core and consumes its decisions, never reaching
inside them.

See docs/collaborator-plain-language.md (the approved plan).
"""

from collaborator.egress import (
    EgressRecord,
    EgressResult,
    canonical_host,
    fetch,
    post,
    required_capability,
)
from collaborator.governance import (
    DENIED,
    FAILED,
    HELD,
    NOTIFIED,
    RAN,
    UNKNOWN_TOOL,
    Decision,
    execute_and_verify,
    govern_action,
)
from collaborator.loop import TurnResult, approve, run_turn
from collaborator.model_client import OllamaClient, ScriptedClient
from collaborator.propose import (
    Proposal,
    approve_proposal,
    propose,
    veto_proposal,
)
from collaborator.proposalpool import ProposalPool
from collaborator.policycaps import (
    PolicyCaps,
    SignedPolicyCaps,
    mint,
    verify,
    workspace_subject,
)
from collaborator.view import (
    JudgmentLedger,
    JudgmentView,
    pause,
    resume,
    set_leash,
    set_proactivity,
)
from collaborator.session import DEFAULT_CAPABILITIES, Session
from collaborator.toolcall import ParseResult, ToolIntent, parse_message
from collaborator.tools import (
    ACT_THEN_REPORT,
    NOTIFY_ONLY,
    PROPOSE_FIRST,
    Tool,
    ToolResult,
    WorkspaceError,
    get_tool,
    toolset,
)

COLLABORATOR_VERSION = "0.1.0"

__all__ = [
    "COLLABORATOR_VERSION",
    "Session",
    "DEFAULT_CAPABILITIES",
    "run_turn",
    "approve",
    "TurnResult",
    "govern_action",
    "execute_and_verify",
    "Decision",
    "RAN",
    "FAILED",
    "HELD",
    "DENIED",
    "NOTIFIED",
    "UNKNOWN_TOOL",
    "parse_message",
    "ToolIntent",
    "ParseResult",
    "toolset",
    "get_tool",
    "Tool",
    "ToolResult",
    "WorkspaceError",
    "ACT_THEN_REPORT",
    "PROPOSE_FIRST",
    "NOTIFY_ONLY",
    "OllamaClient",
    "ScriptedClient",
    "propose",
    "approve_proposal",
    "veto_proposal",
    "Proposal",
    "ProposalPool",
    "JudgmentView",
    "JudgmentLedger",
    "set_leash",
    "set_proactivity",
    "pause",
    "resume",
    "PolicyCaps",
    "SignedPolicyCaps",
    "mint",
    "verify",
    "workspace_subject",
    "fetch",
    "post",
    "canonical_host",
    "required_capability",
    "EgressRecord",
    "EgressResult",
]
