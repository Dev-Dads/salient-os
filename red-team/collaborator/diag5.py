"""Empty-rate per directive, and how much retry budget clears it. Evidence for the
run_turn empty_retries default. Raw single-call empty rate + retry-to-first-actionable.
"""
from __future__ import annotations
import json, os, sys, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from collaborator.loop import sal_system_prompt
from collaborator.tools import openai_tools

BASE = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11500/v1").rstrip("/")
MODEL = os.environ.get("OLLAMA_MODEL", "gpt-oss:120b")
N = int(os.environ.get("N", "8"))
MAXTOK = int(os.environ.get("MAXTOK", "4096"))

DIRECTIVES = {
    "notes": ("Create a file called notes.txt containing exactly three short bullet lines about "
              "the number seven. Then read it back and tell me what it says."),
    "script_run": ("Write a Python file hello.py that prints the word hi, then run it with python3 "
                   "to show me the output."),
    "one_file": "Create a file paused_probe.txt with the word blocked.",
}

def call(directive):
    body = {"model": MODEL,
            "messages": [{"role": "system", "content": sal_system_prompt()},
                         {"role": "user", "content": directive}],
            "max_tokens": MAXTOK, "temperature": 0.0, "tools": openai_tools()}
    req = urllib.request.Request(BASE + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer ollama",
                                          "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        resp = json.load(r)
    m = ((resp.get("choices") or [{}])[0].get("message")) or {}
    tc = m.get("tool_calls")
    empty = not (m.get("content") or "").strip() and not tc
    return empty

for name, d in DIRECTIVES.items():
    empties = 0
    max_streak = streak = 0
    for _ in range(N):
        if call(d):
            empties += 1; streak += 1; max_streak = max(max_streak, streak)
        else:
            streak = 0
    print(f"{name:12s}: empty {empties}/{N} single-call  (max consecutive empties={max_streak})")
