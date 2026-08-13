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

# The `<tool_call` marker — the strongest content signal. We tolerate every tag
# variant real models emit (<tool_call>{..}</tool_call>, <tool_call {..}>, bare
# <tool_call>{..}) by scanning for the marker then extracting the BALANCED JSON
# object/array that follows, so nested braces don't truncate the call.
_TOOL_CALL_MARKER_RE = re.compile(r"<tool_call")
# A single wrapping code fence we tolerate before the whole-content-JSON check.
_FENCE_RE = re.compile(r"^```(?:json|tool_call)?\s*(.*?)\s*```$", re.DOTALL)


def _balanced_span(text: str, i: int) -> int:
    """text[i] is '{' or '['. Return the index just past the balanced span, or -1.
    String-aware so braces inside quoted values don't count."""
    open_c = text[i]
    close_c = "}" if open_c == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(text)):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return j + 1
    return -1


def _tool_call_tag_objects(content: str):
    """For each ``<tool_call`` marker, extract the balanced JSON that follows it
    (skipping tag chars/whitespace). Returns list of (start, end, json_str, balanced).
    An UNBALANCED span — a call whose JSON never closes, e.g. a large call clipped by
    max_tokens — is still returned (balanced=False, spanning to end-of-content) so the
    caller can SURFACE it as ambiguous and strip it from the prose. It is never silently
    dropped: a truncated tool call is the exact 'large call rejected' failure to avoid."""
    hits = []
    for m in _TOOL_CALL_MARKER_RE.finditer(content):
        start = None
        for j in range(m.end(), min(len(content), m.end() + 40)):
            if content[j] in "{[":
                start = j
                break
            if content[j] not in "> \t\r\n/=\"'":  # unexpected char -> not a call
                break
        if start is None:
            continue
        end = _balanced_span(content, start)
        if end != -1:
            hits.append((m.start(), end, content[start:end], True))
        else:  # unbalanced (truncated / malformed) — surface it, do not lose it
            hits.append((m.start(), len(content), content[start:], False))
    return hits


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

    # 2) <tool_call> markers in content (any tag variant), balanced-JSON extracted.
    remaining = content
    hits = _tool_call_tag_objects(content)
    if hits:
        for _start, _end, js, balanced in hits:
            if not balanced:  # truncated / never-closed call — surface, never run, never drop
                ambiguous.append(js[:200])
                continue
            payload = _try_json(js)
            objs = payload if isinstance(payload, list) else [payload]
            for obj in objs:
                it = _coerce_call(obj, "content_block", raw=js)
                if it is not None:
                    intents.append(it)
                else:
                    ambiguous.append(js[:200])
        keep, last = [], 0
        for start, end, _js, _balanced in hits:
            keep.append(content[last:start])
            last = max(last, end)
        keep.append(content[last:])
        remaining = ("".join(keep).replace("<tool_call", "").replace("</tool_call>", "")
                     .strip(" >/\t\r\n"))

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
            elif any(_looks_toolish(o) for o in payload if isinstance(o, dict)):
                # a batch the model MEANT as calls but one element is malformed — surface the
                # whole batch as ambiguous rather than silently drop every call in it.
                ambiguous.append(candidate[:200])
        elif payload is None and candidate[:1] in "[{" and _text_looks_toolish(candidate):
            # a whole-content tool-call attempt that did NOT parse (e.g. a batch clipped by
            # max_tokens, no <tool_call> marker) — surface it, never silently lose it (panel
            # grok/qwen F1). It is tool-shaped but unrunnable, so it is ambiguous, never run.
            ambiguous.append(candidate[:200])

    return ParseResult(intents=tuple(intents), ambiguous=tuple(ambiguous), text=remaining.strip())


def _looks_toolish(obj: dict) -> bool:
    return any(k in obj for k in ("name", "tool", "tool_name", "function", "arguments", "args"))


_TOOLISH_TOKENS = ('"name"', '"tool"', '"tool_name"', '"function"', '"arguments"', '"args"')


def _text_looks_toolish(s: str) -> bool:
    """A raw (possibly unparseable/clipped) string that appears to be an ATTEMPTED tool call —
    used to surface a whole-content call that did not parse (e.g. a batch clipped by max_tokens)
    so it is never silently lost."""
    return any(tok in s for tok in _TOOLISH_TOKENS)
