"""Model client for the loop, and the tiny interface the loop depends on.

The loop needs only ``complete(messages, tools=None, temperature=None) -> assistant
message dict`` (shape: ``{"content": str|None, "tool_calls": [...]}``). ``temperature``
is a per-call override (None → the client's configured default); the loop raises it on
a retry to escape a deterministic empty-completion streak (see loop._complete_actionable).
Tests inject a scripted fake so the whole governed loop runs without a live model; real
runs use ``OllamaClient`` against an OpenAI-compatible ``/v1`` endpoint. urllib only.
"""

from __future__ import annotations

import json
import urllib.request

COLLABORATOR_MODEL_CLIENT_VERSION = "0.1.0"


class OllamaClient:
    def __init__(self, base_url: str, model: str, api_key: str = "ollama",
                 timeout: int = 120, max_tokens: int = 4096, temperature: float = 0.2) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature

    def complete(self, messages, tools=None, temperature=None) -> dict:
        temp = self.temperature if temperature is None else temperature
        body = {"model": self.model, "messages": messages,
                "max_tokens": self.max_tokens, "temperature": temp}
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
        return choice.get("message") or {}


class ScriptedClient:
    """Deterministic fake: returns the queued assistant messages in order, so the
    governed loop is testable without a model. When the queue empties it returns a
    plain final answer."""

    def __init__(self, messages) -> None:
        self._queue = list(messages)
        self.seen: list = []
        self.temps: list = []   # per-call temperature the loop requested (None = default)

    def complete(self, messages, tools=None, temperature=None) -> dict:
        self.seen.append(list(messages))
        self.temps.append(temperature)
        if self._queue:
            return self._queue.pop(0)
        return {"content": "done.", "tool_calls": None}
