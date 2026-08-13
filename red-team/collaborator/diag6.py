"""Does PERTURBING the retry escape a deterministic empty streak? Plain retry (temp 0,
identical input) is the control; nudge-message retry and raised-temperature retry are
the candidates. Run against a directive that is currently empty-streaking.
"""
from __future__ import annotations
import json, os, sys, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from collaborator.loop import sal_system_prompt
from collaborator.tools import openai_tools

BASE = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11500/v1").rstrip("/")
MODEL = os.environ.get("OLLAMA_MODEL", "gpt-oss:120b")
N = int(os.environ.get("N", "6"))
MAXTOK = int(os.environ.get("MAXTOK", "4096"))
directive = os.environ.get("DIRECTIVE",
    "Write a Python file hello.py that prints the word hi, then run it with python3 to show me the output.")

NUDGE = ("Your previous response was empty — no tool call and no answer. Do not think silently. "
         "Emit the next concrete tool call now (or give your final answer if the task is done).")

def call(messages, temperature):
    body = {"model": MODEL, "messages": messages, "max_tokens": MAXTOK,
            "temperature": temperature, "tools": openai_tools()}
    req = urllib.request.Request(BASE + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer ollama",
                                          "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.load(r)
    m = ((resp.get("choices") or [{}])[0].get("message")) or {}
    tc = m.get("tool_calls")
    return (not (m.get("content") or "").strip() and not tc)  # empty?

base = [{"role": "system", "content": sal_system_prompt()},
        {"role": "user", "content": directive}]

def trial_plain():          # control: identical input, temp 0, twice
    if not call(base, 0.0):
        return "act@1"
    return "act@2" if not call(base, 0.0) else "EMPTY"

def trial_nudge():          # empty -> append nudge -> retry (temp 0)
    if not call(base, 0.0):
        return "act@1"
    msgs = base + [{"role": "assistant", "content": ""}, {"role": "user", "content": NUDGE}]
    return "act@2" if not call(msgs, 0.0) else "EMPTY"

def trial_temp():           # empty -> retry same input at temp 0.7
    if not call(base, 0.0):
        return "act@1"
    return "act@2" if not call(base, 0.7) else "EMPTY"

for label, fn in (("plain(control)", trial_plain), ("nudge", trial_nudge), ("temp0.7", trial_temp)):
    outs = [fn() for _ in range(N)]
    acted = sum(1 for o in outs if o.startswith("act"))
    escaped = sum(1 for o in outs if o == "act@2")   # was empty@1, recovered on retry
    print(f"{label:14s}: actionable {acted}/{N}  (recovered-from-empty {escaped})  {outs}")
