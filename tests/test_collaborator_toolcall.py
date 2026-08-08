"""The tool-call parser we own: catches structured AND content-embedded calls
(the box gap), strictly — ambiguous text is surfaced, never run."""

import json
import unittest

from collaborator.toolcall import parse_message


class Structured(unittest.TestCase):
    def test_structured_tool_calls(self):
        msg = {"content": None, "tool_calls": [
            {"id": "1", "function": {"name": "write_file",
                                     "arguments": json.dumps({"path": "a.txt", "content": "hi"})}}]}
        r = parse_message(msg)
        self.assertEqual(len(r.intents), 1)
        self.assertEqual(r.intents[0].name, "write_file")
        self.assertEqual(r.intents[0].args, {"path": "a.txt", "content": "hi"})
        self.assertEqual(r.intents[0].source, "structured")

    def test_malformed_structured_is_ambiguous_not_run(self):
        msg = {"tool_calls": [{"function": {"name": "write_file", "arguments": "{not json"}}]}
        r = parse_message(msg)
        self.assertEqual(r.intents, ())
        self.assertEqual(len(r.ambiguous), 1)


class ContentEmbedded(unittest.TestCase):
    """The box gap: models that put the call in content must still be executed."""

    def test_tool_call_block_in_content(self):
        content = 'Sure.\n<tool_call>{"name": "read_file", "arguments": {"path": "x"}}</tool_call>'
        r = parse_message({"content": content})
        self.assertEqual(len(r.intents), 1)
        self.assertEqual(r.intents[0].name, "read_file")
        self.assertEqual(r.intents[0].source, "content_block")

    def test_multiple_blocks_each_an_action(self):
        content = ('<tool_call>{"name":"read_file","arguments":{"path":"a"}}</tool_call>'
                   '<tool_call>{"name":"read_file","arguments":{"path":"b"}}</tool_call>')
        r = parse_message({"content": content})
        self.assertEqual(len(r.intents), 2)

    def test_whole_content_json_hermes_style(self):
        content = '{"name": "write_file", "arguments": {"path": "a.txt", "content": "hi"}}'
        r = parse_message({"content": content})
        self.assertEqual(len(r.intents), 1)
        self.assertEqual(r.intents[0].source, "content_json")

    def test_fenced_whole_content_json(self):
        content = '```json\n{"name": "read_file", "arguments": {"path": "a"}}\n```'
        r = parse_message({"content": content})
        self.assertEqual(len(r.intents), 1)
        self.assertEqual(r.intents[0].name, "read_file")


class Strictness(unittest.TestCase):
    def test_json_mid_prose_is_not_run(self):
        # A tool-shaped object embedded in a sentence is a MENTION, not an intent.
        content = ('Here is an example you could use: '
                   '{"name": "run_command", "arguments": {"command": "rm -rf /tmp/x"}} '
                   'but I will not run it.')
        r = parse_message({"content": content})
        self.assertEqual(r.intents, ())  # never auto-run
        self.assertIn("example", r.text)

    def test_plain_prose_has_no_intents(self):
        r = parse_message({"content": "I think the file looks fine. No action needed."})
        self.assertEqual(r.intents, ())
        self.assertEqual(r.ambiguous, ())
        self.assertIn("fine", r.text)

    def test_bare_string_message(self):
        r = parse_message('{"name": "read_file", "arguments": {"path": "a"}}')
        self.assertEqual(len(r.intents), 1)


if __name__ == "__main__":
    unittest.main()
