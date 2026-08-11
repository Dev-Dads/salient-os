"""PR 1a "Harm B" — the OPERATOR's sensitive host paths as a porous, best-effort recognizer.

Pins the cheap, cross-platform defence-in-depth layer over run_command's unfenced filesystem reach,
WITHOUT hard-fencing run_command (which would kill system maintenance):

* the POROUS recognizer (names_sensitive_path) — explicitly NOT a boundary, and unlike codefence NO
  structural boundary is planned (negative cases + the precision exclusions encode that),
* the proposer hard-deny (DENY outright — operator-confirmed) + the approve-time re-deny,
* the human-preview ⚠ + the audit-only execution tag (never a deny),
* maintenance preserved: a USER/HOST-directed secret-touching command is untouched (held then runs).

The highest-value test is test_env_example_is_NOT_matched: `.env` is deliberately EXCLUDED so the
recognizer does not noise-blind the human on routine dev commands nor over-deny legit proposer work.
"""

import sys
import tempfile
import unittest

from collaborator import sensitivepaths
from collaborator.governance import DENIED, HELD, RAN, govern_action
from collaborator.loop import approve
from collaborator.session import Session
from collaborator.toolcall import ToolIntent


def _shell_session(tmp, **kw):
    return Session(workspace=tmp, capabilities=("fs.read:project", "fs.write:project", "shell.exec"), **kw)


# A hermetic secret-shaped literal — a fixed string, never the real $HOME (keeps the test deterministic
# across machines and CI, and never actually reads anyone's key).
_SECRET = "/home/op/.ssh/id_rsa"


class NamesSensitivePathRecognizer(unittest.TestCase):
    """POROUS, HIGH-PRECISION defence-in-depth — negative cases ENCODE that it is not a boundary."""

    def test_matches_ssh_key_in_list(self):
        got = sensitivepaths.names_sensitive_path(["cat", "/home/u/.ssh/id_rsa"])
        self.assertIn("id_rsa", got)
        self.assertIn(".ssh/", got)

    def test_matches_aws_credentials_in_string(self):
        self.assertIn(".aws/credentials", sensitivepaths.names_sensitive_path("cat ~/.aws/credentials"))

    def test_matches_etc_shadow(self):
        self.assertIn("/etc/shadow", sensitivepaths.names_sensitive_path(["cp", "/etc/shadow", "/tmp/x"]))

    def test_windows_backslash_and_case_normalized(self):
        # pins BOTH the "\\" -> "/" normalization AND the lowercasing divergence from names_code_root
        got = sensitivepaths.names_sensitive_path(r"type C:\Users\me\.SSH\id_rsa")
        self.assertIn(".ssh/", got)
        self.assertIn("id_rsa", got)

    def test_env_example_is_NOT_matched(self):
        # THE landmine test: a substring match on ".env" would fire on routine dev commands and
        # over-deny the proposer. `.env` is deliberately excluded (wrong axis + noise).
        self.assertEqual(sensitivepaths.names_sensitive_path("cp .env.example .env"), "")
        self.assertEqual(sensitivepaths.names_sensitive_path(["cat", ".env.local"]), "")
        self.assertEqual(sensitivepaths.names_sensitive_path(["cat", ".envrc"]), "")

    def test_npmrc_is_NOT_matched(self):
        # npm/CI/Dockerfiles touch .npmrc constantly — excluded to avoid alarm fatigue.
        cmd = ["npm", "config", "set", "//r/:_authToken=x", "--userconfig", ".npmrc"]
        self.assertEqual(sensitivepaths.names_sensitive_path(cmd), "")

    def test_aws_config_not_matched_but_credentials_is(self):
        # two-segment anchoring: ~/.aws/config (routine region settings) must NOT fire; credentials must.
        self.assertEqual(sensitivepaths.names_sensitive_path("cat ~/.aws/config"), "")
        self.assertTrue(sensitivepaths.names_sensitive_path("cat ~/.aws/credentials"))

    def test_benign_and_empty(self):
        self.assertEqual(sensitivepaths.names_sensitive_path(["echo", "hello"]), "")
        self.assertEqual(sensitivepaths.names_sensitive_path(""), "")
        self.assertEqual(sensitivepaths.names_sensitive_path(None), "")

    def test_porous_dynamic_construction_is_NOT_caught(self):
        # A shell that builds the path at runtime (a var, no literal marker) sails past — exactly why
        # this is DiD and NOT a boundary. Pins the porousness so nobody mistakes it for a fence.
        self.assertEqual(
            sensitivepaths.names_sensitive_path(["sh", "-c", 'k=$(cat cfg); cat "$k"']), "")

    def test_multiple_markers_joined(self):
        got = sensitivepaths.names_sensitive_path(["sh", "-c", "cat ~/.ssh/id_rsa ~/.aws/credentials"])
        self.assertIn("id_rsa", got)
        self.assertIn(".aws/credentials", got)
        self.assertIn(",", got)  # multiple markers, comma-joined


class GovernSensitivePathSlot(unittest.TestCase):
    def test_proposer_command_naming_secret_is_hard_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _shell_session(tmp)
            d = govern_action(s, ToolIntent("run_command", {"command": ["cat", _SECRET]}, "proposed"))
            self.assertEqual(d.status, DENIED)
            self.assertIn("sensitive host paths", d.reason)

    def test_proposer_env_example_is_NOT_denied(self):
        # the landmine exclusion holds END TO END: a proposed `cp .env.example .env` is not denied by
        # this rule — it is merely HELD (proposer run_command floors to propose_first).
        with tempfile.TemporaryDirectory() as tmp:
            s = _shell_session(tmp)
            d = govern_action(s, ToolIntent(
                "run_command", {"command": ["cp", ".env.example", ".env"]}, "proposed"))
            self.assertNotEqual(d.status, DENIED)
            self.assertEqual(d.status, HELD)

    def test_user_directed_secret_command_not_denied_and_runs(self):
        # maintenance preserved: a user/host-directed command naming a secret is NOT hard-denied (only
        # the PROPOSER path is); it is HELD by the leash and runs under a human hand. `echo` is harmless.
        with tempfile.TemporaryDirectory() as tmp:
            s = _shell_session(tmp)
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", _SECRET]}, "structured"))
            self.assertEqual(held.status, HELD)
            self.assertNotEqual(held.status, DENIED)
            d = approve(s, held)              # the human hand: echo runs (not re-denied for user origin)
            self.assertEqual(d.status, RAN)

    def test_held_preview_surfaces_named_sensitive_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _shell_session(tmp)
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", _SECRET]}, "structured"))
            self.assertEqual(held.status, HELD)
            self.assertIn("id_rsa", held.preview.get("names_sensitive_path", ""))

    def test_executed_run_command_secret_tag_is_audit_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = _shell_session(tmp)
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", _SECRET]}, "structured"))
            d = approve(s, held)
            self.assertEqual(d.status, RAN)                 # the tag NEVER turns a RAN into a DENIED
            self.assertTrue(d.secret_touch)                 # honest audit tag present
            self.assertIn("secret-touch audit", d.summary())


class ApproveReDeniesMutatedSensitivePath(unittest.TestCase):
    def test_approve_re_denies_a_mutated_collaborator_command(self):
        # a held COLLABORATOR-origin run_command mutated after origination to name a secret is refused
        # at the moment of use (symmetric with the code-root re-deny), not consumed. NOTE: freeze_args
        # makes held.args a dict whose "command" is an immutable tuple — so we REASSIGN the dict key
        # (mirrors the codefence test); mutating the tuple in place would raise TypeError.
        with tempfile.TemporaryDirectory() as tmp:
            s = _shell_session(tmp)
            held = govern_action(s, ToolIntent("run_command", {"command": ["echo", "hi"]}, "structured"))
            self.assertEqual(held.status, HELD)
            held.origin = "collaborator"                    # as if a proposer raised the hold
            held.args["command"] = ["cat", _SECRET]         # mutated post-origination (key reassign)
            d = approve(s, held)
            self.assertEqual(d.status, DENIED)
            self.assertIn("sensitive path", d.reason)
            self.assertFalse(held.consumed)                 # retryable, not burned


if __name__ == "__main__":
    sys.exit(unittest.main())
