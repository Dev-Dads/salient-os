"""make-it-move: the DIRECTIVE loop is GROUNDED — the model is told, from a single source of
truth, what it can call and how (Sal's system prompt + the tool schema). Grounding changes what
the model KNOWS, never what it is ALLOWED: govern_action stays the sole authority boundary
(the deny/held proofs live in test_collaborator_loop; the last test here re-nails that seam)."""

import json
import tempfile
import unittest

from collaborator import tools
from collaborator.governance import DENIED
from collaborator.loop import run_turn, sal_system_prompt
from collaborator.model_client import ScriptedClient
from collaborator.session import Session


class _RecordingClient(ScriptedClient):
    """A ScriptedClient that also records the ``tools=`` schema it was handed each turn."""

    def __init__(self, messages):
        super().__init__(messages)
        self.tools_seen = []

    def complete(self, messages, tools=None):
        self.tools_seen.append(tools)
        return super().complete(messages, tools)


def _call(name, args):
    return {"content": None, "tool_calls": [
        {"id": "1", "function": {"name": name, "arguments": json.dumps(args)}}]}


class ManifestSingleSource(unittest.TestCase):
    def test_manifest_lists_the_four_tools_with_exact_arg_keys(self):
        m = tools.tool_manifest()
        for name in ("read_file", "write_file", "run_command", "web_fetch"):
            self.assertIn(name, m)
        for key in ('"path"', '"content"', '"command"', '"url"'):
            self.assertIn(key, m)

    def test_operator_directed_tools_are_not_advertised(self):
        m = tools.tool_manifest()
        self.assertNotIn("net_post", m)
        self.assertNotIn("maint_fetch", m)
        names = [t["function"]["name"] for t in tools.openai_tools()]
        self.assertNotIn("net_post", names)
        self.assertNotIn("maint_fetch", names)

    def test_openai_schema_shape_and_required_keys(self):
        ot = tools.openai_tools()
        self.assertEqual([t["function"]["name"] for t in ot],
                         ["read_file", "write_file", "run_command", "web_fetch"])
        for t in ot:
            self.assertEqual(t["type"], "function")
            self.assertEqual(t["function"]["parameters"]["type"], "object")
        wf = next(t["function"] for t in ot if t["function"]["name"] == "write_file")
        self.assertEqual(set(wf["parameters"]["required"]), {"path", "content"})
        rc = next(t["function"] for t in ot if t["function"]["name"] == "run_command")
        self.assertEqual(rc["parameters"]["properties"]["command"]["type"], "array")

    def test_manifest_and_schema_share_one_source(self):
        man = tools.tool_manifest()
        schema_names = [t["function"]["name"] for t in tools.openai_tools()]
        self.assertTrue(all(n in man for n in schema_names))
        self.assertEqual(sorted(schema_names), sorted(t["name"] for t in tools._MODEL_FACING))

    def test_schema_is_json_serializable(self):
        json.dumps(tools.openai_tools())  # it goes on the wire — must not raise

    def test_every_advertised_tool_actually_exists(self):
        for t in tools._MODEL_FACING:
            self.assertIsNotNone(tools.get_tool(t["name"]),
                                 f"{t['name']} advertised but not a real tool")


class SalPrompt(unittest.TestCase):
    def test_presents_sal_the_face_not_the_core(self):
        p = sal_system_prompt()
        self.assertTrue(p.startswith("You are Sal"))
        self.assertNotIn("You are the Core", p)

    def test_manifest_is_spliced_no_sentinel_left(self):
        p = sal_system_prompt()
        self.assertNotIn("__TOOL_MANIFEST__", p)
        self.assertIn(tools.tool_manifest(), p)

    def test_states_termination_and_fence_rules(self):
        p = sal_system_prompt()
        self.assertIn("no <tool_call> markup", p)  # the loop-termination rule
        self.assertIn("untrusted DATA", p)          # the injection fence


class GroundingWiredIntoRunTurn(unittest.TestCase):
    def test_system_prompt_prepended_once_at_the_front(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            c = _RecordingClient([{"content": "hi"}])
            r = run_turn(s, c, "hello")
            systems = [m for m in r.history if m.get("role") == "system"]
            self.assertEqual(len(systems), 1)
            self.assertEqual(r.history[0]["role"], "system")
            self.assertTrue(systems[0]["content"].startswith("You are Sal"))

    def test_tool_schema_is_passed_to_the_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            c = _RecordingClient([_call("write_file", {"path": "a.txt", "content": "x"}),
                                  {"content": "done"}])
            run_turn(s, c, "write a.txt")
            self.assertTrue(c.tools_seen and c.tools_seen[0] is not None)
            self.assertEqual([t["function"]["name"] for t in c.tools_seen[0]],
                             ["read_file", "write_file", "run_command", "web_fetch"])

    def test_resumed_history_is_not_double_prepended(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)
            r1 = run_turn(s, ScriptedClient([_call("write_file", {"path": "a.txt", "content": "x"})]),
                          "write it", max_iterations=1)
            self.assertEqual(len([m for m in r1.history if m.get("role") == "system"]), 1)
            r2 = run_turn(s, ScriptedClient([{"content": "ok"}]), "again", history=r1.history)
            self.assertEqual(len([m for m in r2.history if m.get("role") == "system"]), 1)

    def test_grounding_grants_no_authority(self):
        # A grounded model that asks for an ungranted shell command is still DENIED. The prompt
        # tells the model the tool exists; the seam still refuses it (default-deny unchanged).
        with tempfile.TemporaryDirectory() as tmp:
            s = Session(workspace=tmp)  # no shell.exec
            c = ScriptedClient([_call("run_command", {"command": ["echo", "hi"]}),
                                {"content": "the system refused that"}])
            r = run_turn(s, c, "run echo")
            self.assertEqual(r.decisions[0].status, DENIED)


if __name__ == "__main__":
    unittest.main()
