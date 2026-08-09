"""Verifier: a nested file.write verifies (the auto-created parent dirs are ENTAILED by the
declared write, not undeclared mutations), while an UNRELATED undeclared write is still
caught — the entailed-ancestor exemption is bounded to the declared path's own parent chain.

Regression: before this, any nested write false-failed the write-set boundary because the
created parents (`a/`, `a/b/`) showed up as "extra" changed paths the contract never declared,
so the Collaborator could not verify-clear any nested file (a/b/c.txt, .github/workflows/…).
"""

import sys
import tempfile
import unittest
from pathlib import Path

from salienceos.verifier import Status, issue_receipt
from salienceos.verifier.observers import (
    entailed_ancestors,
    observe_action,
    observed_write_set,
    run_supervised,
    snapshot_tree,
)
from salienceos.verifier.signing import sha256_bytes
from tests.helpers import EXECUTOR_ID, EXECUTOR_KEY, make_verifier, write_envelope

_CONTENT = "line1\nline2\n"


def _write_child(workspace, *pairs):
    """A real executor child that writes each (relpath, text) pair, creating parents."""
    script = ("import sys, pathlib\n"
              "args = sys.argv[1:]\n"
              "for i in range(0, len(args), 2):\n"
              "    p = pathlib.Path(args[i]); p.parent.mkdir(parents=True, exist_ok=True)\n"
              "    p.write_bytes(args[i+1].encode('utf-8'))\n"
              "sys.exit(0)\n")
    flat = [x for pair in pairs for x in pair]
    return run_supervised([sys.executable, "-c", script, *flat], cwd=workspace)


class EntailedAncestors(unittest.TestCase):
    def test_ancestors_helper(self):
        self.assertEqual(entailed_ancestors("a/b/c.txt"), ["a", "a/b"])
        self.assertEqual(entailed_ancestors("flat.txt"), [])
        self.assertEqual(entailed_ancestors(".github/workflows/ci.yml"), [".github", ".github/workflows"])
        self.assertEqual(entailed_ancestors("a\\b\\c.txt"), ["a", "a/b"])  # windows sep tolerated

    def test_observed_write_set_exempts_listed_paths(self):
        pre = {}
        post = {"a": "dir", "a/b": "dir", "a/b/c.txt": "hash"}
        # Without exemption the two dirs are "changes"; exempting the entailed ancestors leaves
        # only the declared file.
        self.assertEqual(observed_write_set(pre, post), ["a", "a/b", "a/b/c.txt"])
        self.assertEqual(observed_write_set(pre, post, exempt=("a", "a/b")), ["a/b/c.txt"])


class NestedWriteVerifies(unittest.TestCase):
    def _receipt(self, env, path, content):
        return issue_receipt("r-" + env.envelope_id, env.envelope_id, 0,
                             {path: sha256_bytes(content.encode("utf-8"))}, (path,),
                             True, EXECUTOR_ID, EXECUTOR_KEY)

    def test_nested_write_clears(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            env = write_envelope("env-nest", "a/b/c.txt", _CONTENT)
            pre = snapshot_tree(ws)
            res = _write_child(ws, ("a/b/c.txt", _CONTENT))
            world = observe_action(env, ws, pre, res)
            v = make_verifier().verify(env, self._receipt(env, "a/b/c.txt", _CONTENT), world)
            self.assertIs(v.status, Status.VERIFIED)

    def test_flat_write_still_clears(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            env = write_envelope("env-flat", "top.txt", _CONTENT)
            pre = snapshot_tree(ws)
            res = _write_child(ws, ("top.txt", _CONTENT))
            world = observe_action(env, ws, pre, res)
            v = make_verifier().verify(env, self._receipt(env, "top.txt", _CONTENT), world)
            self.assertIs(v.status, Status.VERIFIED)

    def test_unrelated_undeclared_write_is_STILL_caught(self):
        # The exemption is bounded: it covers only the declared path's parent chain. A sibling
        # file the write also created (not an ancestor of the declared path) must still trip the
        # write-set boundary — proving we did not open a hole.
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d)
            env = write_envelope("env-evil", "a/b/c.txt", _CONTENT)
            pre = snapshot_tree(ws)
            res = _write_child(ws, ("a/b/c.txt", _CONTENT), ("evil.txt", "gotcha"))
            world = observe_action(env, ws, pre, res)
            v = make_verifier().verify(env, self._receipt(env, "a/b/c.txt", _CONTENT), world)
            self.assertIsNot(v.status, Status.VERIFIED)  # undeclared sibling still fails closed


if __name__ == "__main__":
    unittest.main()
