"""The thin bus contract — the normalized edge where subsystems publish salience.

Per the design review (Part 1, Finding A): each subsystem computes its own
salience however suits it — routing, memory, and expert-promotion salience can be
entirely different functions over different inputs. What they share is the *bus*,
and the bus contract is deliberately **thin but real**: enough for the interpreter
to compare and combine, not enough to constrain how anyone scores. A publisher
emits a comparable influence magnitude + confidence + provenance + subsystem-id,
keeping its rich internal scoring private (the "thin normalization at the bus
edge" fork).

The P-01 fence is structural: a `SalienceSignal` carries influence, NEVER
authority. There is no capability, grant, or scope field — a signal can say
"X matters this much", never "allow X". Salience influences; policy authorizes.

The audit fence (Finding G) is also structural: every string field is a bounded,
ref-shaped token (`MAX_TOKEN_LEN`), and provenance is a short list of such refs
(`MAX_PROVENANCE_REFS`). A signal therefore cannot carry a prompt, body, tool
args, or chain-of-thought into the durable bus record — not because no field is
*named* "body", but because nothing large enough to *be* one validates.
"""

from dataclasses import dataclass, field

# Ref-shaped means: an id, a content hash, or a rationale code — never prose.
MAX_TOKEN_LEN = 128
MAX_PROVENANCE_REFS = 16


class Facet:
    """The concerns the interpreter knows how to map to directive knobs.

    Facets are open strings — a publisher may emit any facet — but only the
    known ones move a knob. An unknown facet is recorded on the bus and ignored
    by arbitration (fail-safe: an unrecognized signal grants nothing).
    """

    ATTENTION = "attention"        # -> compute budget
    VERIFICATION = "verification"  # -> verification depth (safety; scales up)
    RISK = "risk"                  # -> raises verification, gates adaptation
    MEMORY = "memory"              # -> retention class
    ROUTING = "routing"            # -> routing hint (advisory only)
    ADAPTATION = "adaptation"      # -> adaptation request (gated by policy)


@dataclass(frozen=True)
class SalienceSignal:
    subsystem_id: str
    subject: str            # arbitration key — what this signal is about (e.g. request id)
    facet: str              # which concern it bears on
    influence: float        # [0,1] comparable magnitude
    confidence: float       # [0,1]
    provenance: tuple = field(default=())  # source refs / rationale codes; NO prompts/bodies/CoT


def _unit(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and 0.0 <= float(x) <= 1.0


def _ref_token(x) -> bool:
    return isinstance(x, str) and 0 < len(x) <= MAX_TOKEN_LEN


def valid_signal(s) -> bool:
    """A signal is usable only if it is exactly a SalienceSignal with non-empty,
    bounded ref-shaped string identity and unit-interval influence/confidence.
    Anything else is dropped at the interpreter boundary (fail-closed). The
    length bounds are what make the audit fence structural (Finding G)."""
    return (
        type(s) is SalienceSignal
        and _ref_token(s.subsystem_id)
        and _ref_token(s.subject)
        and _ref_token(s.facet)
        and _unit(s.influence)
        and _unit(s.confidence)
        and isinstance(s.provenance, tuple)
        and len(s.provenance) <= MAX_PROVENANCE_REFS
        and all(_ref_token(p) for p in s.provenance)
    )
