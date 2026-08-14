"""LIVE proof that Sal REMEMBERS across messages (the ② conversation-memory fix). Drives the Host
directly against Sparky's gpt-oss:120b: message 1 tells Sal a fact, message 2 (a SEPARATE submit)
asks about it. Before the fix each submit ran blank; after it, the second turn carries the first.

Usage (on Sparky):  python3 red-team/collaborator/e2e_sparky_memory.py
"""
from __future__ import annotations
import os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from collaborator.host import Collaborator, DONE, FAILED  # noqa: E402
from collaborator.model_client import OllamaClient  # noqa: E402
from collaborator.session import Session  # noqa: E402

BASE = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11500/v1")
MODEL = os.environ.get("OLLAMA_MODEL", "gpt-oss:120b")


def wait(host, tid, timeout=180):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        t = host.get_task(tid)
        if t and t["state"] in (DONE, FAILED):
            return t
        time.sleep(0.5)
    return host.get_task(tid)


def main() -> int:
    ws = Path(tempfile.mkdtemp(prefix="sal_mem_"))
    session = Session(workspace=ws, capabilities=("fs.read:project", "fs.write:project"),
                      proactivity="off")
    host = Collaborator(session, OllamaClient(BASE, MODEL, timeout=180, temperature=0.0)).start()
    try:
        t1 = wait(host, host.submit("Please remember two facts about me: my name is Bjorn "
                                    "and my favorite number is forty-two. Just acknowledge."))
        print(f"turn1: state={t1['state']}\n  Sal: {t1['reply'][:200]}\n")
        t2 = wait(host, host.submit("Without me repeating them, what is my name and my "
                                    "favorite number?"))
        reply = (t2["reply"] or "")
        print(f"turn2: state={t2['state']}\n  Sal: {reply[:300]}\n")
        remembered = ("bjorn" in reply.lower()) and ("forty-two" in reply.lower()
                                                     or "42" in reply)
        print(f"{'PASS' if remembered else 'FAIL'}  Sal remembered across two separate messages: "
              f"{remembered}")
        return 0 if remembered else 1
    finally:
        host.stop()


if __name__ == "__main__":
    raise SystemExit(main())
