"""Model client for the loop, and the tiny interface the loop depends on.

The loop needs only ``complete(messages, tools=None, temperature=None, max_tokens=None)
-> assistant message dict`` (shape: ``{"content": str|None, "tool_calls": [...],
"finish_reason": str|None}``). ``temperature`` and ``max_tokens`` are per-call overrides
(None → the client's configured default); the loop raises the temperature on a retry to
escape a deterministic empty-completion streak, and raises max_tokens on a retry when a
turn was TRUNCATED (finish_reason == "length"), so a large tool call clipped mid-JSON can
complete rather than be lost (see loop._complete_actionable). Tests inject a scripted fake
so the whole governed loop runs without a live model; real runs use ``OllamaClient``
against an OpenAI-compatible ``/v1`` endpoint. urllib only.
"""

from __future__ import annotations

import json
import urllib.request

COLLABORATOR_MODEL_CLIENT_VERSION = "0.1.0"


class OllamaClient:
    # max_tokens caps the model's OUTPUT per reply — NOT the context window (gpt-oss:120b's is
    # 131072, prompt + output shared). A directive turn typically emits only a few hundred tokens
    # (short reasoning + a compact tool-call JSON), so this is generous for the common case; the
    # reason to keep real headroom is a single turn that legitimately generates a LOT — chiefly a
    # large write_file, whose file content is produced as tool-call-argument tokens (a ~400-line
    # file exceeds 4096 and would clip the JSON mid-call). 16384 fits ~1000-line writes and long
    # reasoning while staying ~8x below the context window and bounding a runaway generation.
    def __init__(self, base_url: str, model: str, api_key: str = "ollama",
                 timeout: int = 120, max_tokens: int = 16384, temperature: float = 0.2) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature

    def complete(self, messages, tools=None, temperature=None, max_tokens=None) -> dict:
        temp = self.temperature if temperature is None else temperature
        mt = self.max_tokens if max_tokens is None else max_tokens
        body = {"model": self.model, "messages": messages,
                "max_tokens": mt, "temperature": temp}
        if tools:
            body["tools"] = tools
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            resp = json.load(r)
        choice = (resp.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        # Expose why generation stopped so the loop can retry a TRUNCATED turn (finish_reason
        # == "length" — output hit the cap, e.g. a large tool call clipped mid-JSON) with a
        # bigger budget. Attached to the returned dict (never sent back on the wire — the loop
        # builds its own assistant turns); leave any server-provided value untouched.
        if isinstance(msg, dict) and "finish_reason" not in msg:
            msg["finish_reason"] = choice.get("finish_reason")
        return msg


class ScriptedClient:
    """Deterministic fake: returns the queued assistant messages in order, so the
    governed loop is testable without a model. When the queue empties it returns a
    plain final answer."""

    def __init__(self, messages) -> None:
        self._queue = list(messages)
        self.seen: list = []
        self.temps: list = []            # per-call temperature the loop requested (None = default)
        self.max_tokens_seen: list = []  # per-call max_tokens the loop requested (None = default)

    def complete(self, messages, tools=None, temperature=None, max_tokens=None) -> dict:
        self.seen.append(list(messages))
        self.temps.append(temperature)
        self.max_tokens_seen.append(max_tokens)
        if self._queue:
            return self._queue.pop(0)
        return {"content": "done.", "tool_calls": None}
