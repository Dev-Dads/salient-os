"""③ SIGNED-POLICYCAPS proof — authority as a verified grant the config can't widen.

The Collaborator's authority (capabilities + per-tool leash caps) is bound into a signed
grant verified on every governed action. The mutable session config and the Step-2 control
surface can only TIGHTEN, never widen past the grant; tamper / strip / wrong-subject /
absent-key / an unlisted tool all fail closed. This proof drives those properties directly.

Honest scope: symmetric HMAC, a single trust domain — tamper-evidence + provenance +
fail-closed integrity against non-crypto mutation, NOT a hard boundary against a fully
in-process re-signer. Asymmetric / separate authority is the deferred next step (ADR 0002).

Run:  python red-team/collaborator/policycaps_proof.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from collaborator.governance import DENIED, HELD, NOTIFIED, RAN, govern_action  # noqa: E402
from collaborator.loop import approve  # noqa: E402
from collaborator.policycaps import (  # noqa: E402
    PolicyCaps,
    SignedPolicyCaps,
    mint,
    workspace_subject,
)
from collaborator.session import Session  # noqa: E402
from collaborator.toolcall import ToolIntent  # noqa: E402
from collaborator.view import set_leash  # noqa: E402

KEY = b"authority-caps-key"


def _wi(path, content="x"):
    return ToolIntent("write_file", {"path": path, "content": content}, "structured")


def _rc():
    return ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured")


def _granted(tmp, caps, leash_caps, subject=None):
    subj = subject if subject is not None else workspace_subject(tmp)
    return Session(workspace=tmp, capabilities=(),
                   policy_caps=mint(caps, leash_caps, "admin", subj, KEY), caps_key=KEY)


def main() -> None:
    print("③ SIGNED-POLICYCAPS proof — the config/controls can tighten, never widen\n")
    checks: list[tuple[str, bool]] = []

    with tempfile.TemporaryDirectory() as tmp:
        s = _granted(tmp, ("fs.read:project", "fs.write:project"),
                     {"write_file": "act_then_report"})
        d = govern_action(s, _wi("ok.txt"))
        print(f"  grant confers fs.write @ act_then_report -> write {d.status}")
        checks.append(("a valid grant confers its capability + runnable leash", d.status == RAN))

    with tempfile.TemporaryDirectory() as tmp:
        s = _granted(tmp, ("fs.read:project", "fs.write:project"), {"write_file": "act_then_report"})
        s.capabilities = ("fs.read:project", "fs.write:project", "shell.exec")  # widen the tuple
        d = govern_action(s, _rc())
        print(f"  mutate session.capabilities += shell.exec -> run_command {d.status}")
        checks.append(("mutable capabilities cannot widen past the grant", d.status == DENIED))

    with tempfile.TemporaryDirectory() as tmp:
        s = _granted(tmp, ("fs.read:project", "fs.write:project", "shell.exec"),
                     {"run_command": "propose_first", "write_file": "act_then_report"})
        set_leash(s, "run_command", "act_then_report")  # view tries to loosen
        d = govern_action(s, _rc())
        print(f"  view set_leash(run_command, act_then_report), cap=propose_first -> {d.status}")
        checks.append(("the control surface cannot loosen past the leash cap", d.status == HELD))

    with tempfile.TemporaryDirectory() as tmp:
        s = _granted(tmp, ("fs.read:project", "fs.write:project", "shell.exec"),
                     {"write_file": "act_then_report"})  # shell.exec granted, run_command UNLISTED
        set_leash(s, "run_command", "act_then_report")
        d = govern_action(s, _rc())
        print(f"  unlisted tool under a grant (no leash cap) -> run_command {d.status}")
        checks.append(("an unlisted tool defaults to strictest (never runs)", d.status == NOTIFIED))

    with tempfile.TemporaryDirectory() as tmp:
        s = _granted(tmp, ("fs.read:project", "fs.write:project"), {"write_file": "act_then_report"})
        s.policy_caps = None  # strip the grant at runtime
        d = govern_action(s, _wi("no.txt"))
        print(f"  STRIP the grant (policy_caps=None), enforce sticky -> write {d.status}")
        checks.append(("stripping the grant fails closed (not legacy)",
                       d.status == DENIED and not (Path(tmp) / "no.txt").exists()))

    with tempfile.TemporaryDirectory() as tmp:
        s = _granted(tmp, ("fs.read:project", "fs.write:project"), {"write_file": "act_then_report"})
        s.policy_caps = SignedPolicyCaps(
            PolicyCaps(("fs.write:project", "shell.exec"), (("write_file", "act_then_report"),),
                       "admin", workspace_subject(tmp)), s.policy_caps.signature)  # widen + stale sig
        d = govern_action(s, _wi("no.txt"))
        print(f"  TAMPER caps (add shell.exec, keep old sig) -> write {d.status}")
        checks.append(("tampered caps fail closed", d.status == DENIED))

    with tempfile.TemporaryDirectory() as tmp:
        s = _granted(tmp, ("fs.write:project",), {"write_file": "act_then_report"},
                     subject="/some/other/workspace")  # replay onto a foreign subject
        d = govern_action(s, _wi("no.txt"))
        print(f"  REPLAY grant minted for another workspace -> write {d.status}")
        checks.append(("a grant for another workspace is rejected (subject binding)", d.status == DENIED))

    with tempfile.TemporaryDirectory() as tmp:
        s = _granted(tmp, ("fs.read:project", "fs.write:project"), {"write_file": "propose_first"})
        held = govern_action(s, _wi("held.txt"))
        s.policy_caps = None  # revoke while the action is held
        after = approve(s, held)
        print(f"  approve a held action after revoke -> {after.status}  (TOCTOU re-gate)")
        checks.append(("approval re-gates against the current grant", after.status == DENIED))

    with tempfile.TemporaryDirectory() as tmp:
        s = Session(workspace=tmp)  # legacy: no grant at construction
        d = govern_action(s, _wi("legacy.txt"))
        print(f"  legacy session (no grant) -> write {d.status}  (unchanged)")
        checks.append(("a session with no grant behaves as today", d.status == RAN))

    print("\n=== CHECKS ===")
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    allok = all(ok for _, ok in checks)
    print(f"\n③ SIGNED-POLICYCAPS: {'OK' if allok else 'INCOMPLETE'}  "
          f"({sum(ok for _, ok in checks)}/{len(checks)} properties held)")


if __name__ == "__main__":
    main()
