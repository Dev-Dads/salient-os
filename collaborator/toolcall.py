"""The tool-call parser we own.

The rig's default model path executes ONLY structured ``tool_calls`` and silently
drops a tool call a model emits as plain text — the "box tool-exec gap" (some
GGUF templates, e.g. hermes3/mistral, return the call as content). We own this
step so every real tool intent is caught, from BOTH:

* structured ``tool_calls`` (the OpenAI/ollama shape), and
* content-embedded calls: an explicit ``<tool_call>{...}</tool_call>`` block, or a
  message whose WHOLE content is a single tool-call JSON object/array.

Strictness (panel gap #5): only an unambiguous call is an intent. A tool-shaped
JSON sitting mid-sentence, or a malformed call, is NOT executed — it is returned
as ``ambiguous`` for the loop to surface, never guessed into running. This module
is pure (stdlib only) and unit-tested in isolation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

COLLABORATOR_TOOLCALL_VERSION = "0.1.0"

# An explicit, delimited tool call inside content — the strongest content signal.
_TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call>\s*(\{.*?\}|\[.*?\])\s*</tool_call>", re.DOTALL)
# A single wrapping code fence we tolerate before the whole-content-JSON check.
_FENCE_RE = re.compile(r"^```(?:json|tool_call)?\s*(.*?)\s*```$", re.DOTALL)


@dataclass(frozen=True)
class ToolIntent:
    """One caught, well-formed request to run a tool."""

    name: str
    args: dict
    source: str  # "structured" | "content_block" | "content_json"
    raw: str = ""


@dataclass(frozen=True)
class ParseResult:
    intents: tuple[ToolIntent, ...] = ()
    ambiguous: tuple[str, ...] = ()  # tool-shaped but not strict enough to run
    text: str = ""  # the model's plain prose, tool markup removed


def _coerce_call(obj: object, source: str, raw: str) -> "ToolIntent | None":
    """A dict is a call only if it names a tool AND its args are a JSON object."""
    if not isinstance(obj, dict):
        return None
    # OpenAI function shape: {"function": {"name": ..., "arguments": "<json>"}}
    fn = obj.get("function")
    if isinstance(fn, dict):
        name = fn.get("name")
        args = fn.get("arguments")
    else:
        name = obj.get("name") or obj.get("tool") or obj.get("tool_name")
        args = obj.get("arguments")
        if args is None:
            args = obj.get("args")
    if not isinstance(name, str) or not name:
        return None
    if isinstance(args, str):  # arguments as a JSON string (structured shape)
        try:
            args = json.loads(args)
        except (ValueError, TypeError):
            return None
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None
    return ToolIntent(name=name, args=args, source=source, raw=raw)


def _from_structured(tool_calls: object) -> "tuple[list[ToolIntent], list[str]]":
    intents: list[ToolIntent] = []
    ambiguous: list[str] = []
    if not isinstance(tool_calls, (list, tuple)):
        return intents, ambiguous
    for tc in tool_calls:
        intent = _coerce_call(tc, "structured", raw=json.dumps(tc, default=str))
        if intent is not None:
            intents.append(intent)
        else:
            ambiguous.append(str(tc)[:200])
    return intents, ambiguous


def _try_json(text: str) -> object:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def parse_message(message: object) -> ParseResult:
    """Parse one assistant message into tool intents + leftover prose.

    ``message`` may be a dict ({"content": str|None, "tool_calls": [...]}) or a
    bare content string. Precedence: structured tool_calls win; then explicit
    ``<tool_call>`` blocks in content; then a whole-content tool-call JSON.
    """
    content = ""
    tool_calls: object = None
    if isinstance(message, dict):
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls")
    elif isinstance(message, str):
        content = message
    if not isinstance(content, str):
        content = ""

    intents: list[ToolIntent] = []
    ambiguous: list[str] = []

    # 1) Structured tool_calls (may coexist with prose content).
    s_intents, s_amb = _from_structured(tool_calls)
    intents.extend(s_intents)
    ambiguous.extend(s_amb)

    # 2) Explicit <tool_call>...</tool_call> blocks in content.
    remaining = content
    blocks = list(_TOOL_CALL_BLOCK_RE.finditer(content))
    if blocks:
        for m in blocks:
            payload = _try_json(m.group(1))
            if isinstance(payload, list):
                for obj in payload:
                    it = _coerce_call(obj, "content_block", raw=m.group(0))
                    (intents if it else ambiguous).append(it if it else m.group(0)[:200])
            else:
                it = _coerce_call(payload, "content_block", raw=m.group(0))
                (intents.append(it) if it else ambiguous.append(m.group(0)[:200]))
        remaining = _TOOL_CALL_BLOCK_RE.sub("", content).strip()

    # 3) Whole-content tool-call JSON (hermes3/mistral emit the call as the entire
    #    message). Only when the ENTIRE de-fenced content is that JSON — a
    #    tool-shaped object mid-prose is deliberately NOT run.
    if not intents:
        candidate = content.strip()
        fence = _FENCE_RE.match(candidate)
        if fence:
            candidate = fence.group(1).strip()
        payload = _try_json(candidate)
        if isinstance(payload, dict):
            it = _coerce_call(payload, "content_json", raw=candidate)
            if it is not None:
                intents.append(it)
                remaining = ""
            elif _looks_toolish(payload):
                ambiguous.append(candidate[:200])
        elif isinstance(payload, list) and payload:
            got = [_coerce_call(o, "content_json", raw=candidate) for o in payload]
            if all(g is not None for g in got):
                intents.extend(g for g in got if g is not None)
                remaining = ""

    return ParseResult(intents=tuple(intents), ambiguous=tuple(ambiguous), text=remaining.strip())


def _looks_toolish(obj: dict) -> bool:
    return any(k in obj for k in ("name", "tool", "tool_name", "function", "arguments", "args"))
