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

    def test_malformed_open_tag_variant_is_caught(self):
        # gemma emitted "<tool_call {json}>" (no closing tag, space opener) — a real
        # tool call. The balanced-brace scan catches it despite nested braces.
        content = '<tool_call {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hi there"}}>'
        r = parse_message({"content": content})
        self.assertEqual(len(r.intents), 1)
        self.assertEqual(r.intents[0].name, "write_file")
        self.assertEqual(r.intents[0].args, {"path": "notes.txt", "content": "hi there"})

    def test_nested_braces_not_truncated(self):
        content = '<tool_call>{"name":"write_file","arguments":{"path":"a","content":"{not the end}"}}</tool_call>'
        r = parse_message({"content": content})
        self.assertEqual(len(r.intents), 1)
        self.assertEqual(r.intents[0].args["content"], "{not the end}")


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


class NeverSilentlyDropped(unittest.TestCase):
    """A large or batched tool call must never VANISH: if it can't be run (truncated,
    malformed, or a mixed batch) it is SURFACED as ambiguous, never silently dropped."""

    def test_large_wellformed_call_has_no_size_cap(self):
        big = "x" * 50000
        msg = {"tool_calls": [{"function": {"name": "write_file",
               "arguments": json.dumps({"path": "big.py", "content": big})}}]}
        r = parse_message(msg)
        self.assertEqual(len(r.intents), 1)
        self.assertEqual(len(r.intents[0].args["content"]), 50000)

    def test_large_set_has_no_count_cap(self):
        calls = [{"function": {"name": "write_file",
                  "arguments": json.dumps({"path": f"f{i}.txt", "content": "z"})}} for i in range(25)]
        r = parse_message({"tool_calls": calls})
        self.assertEqual(len(r.intents), 25)

    def test_truncated_tool_call_block_is_surfaced_not_dropped(self):
        # a <tool_call> whose JSON never closes (clipped by max_tokens)
        clipped = '<tool_call>{"name":"write_file","arguments":{"path":"big.py","content":"aaaaaa'
        r = parse_message({"content": clipped})
        self.assertEqual(r.intents, ())
        self.assertEqual(len(r.ambiguous), 1)          # surfaced, not vanished
        self.assertNotIn("write_file", r.text)         # and NOT leaked into the prose reply

    def test_whole_content_batch_with_one_bad_call_is_surfaced_not_dropped(self):
        batch = '[{"name":"write_file","arguments":{"path":"a","content":"x"}}, {"not":"a call"}]'
        r = parse_message({"content": batch})
        self.assertEqual(r.intents, ())                # strict: a partial batch is not run...
        self.assertEqual(len(r.ambiguous), 1)          # ...but the whole batch is surfaced, not lost

    def test_truncated_whole_content_batch_is_surfaced_not_dropped(self):
        # a whole-content JSON array clipped mid-way (no <tool_call> marker, invalid JSON)
        clipped = ('[{"name":"write_file","arguments":{"path":"a","content":"x"}}, '
                   '{"name":"write_file","arguments":{"path":"b","content":"cli')
        r = parse_message({"content": clipped})
        self.assertEqual(r.intents, ())
        self.assertEqual(len(r.ambiguous), 1)          # surfaced, not silently lost

    def test_plain_prose_is_not_misread_as_a_tool_call(self):
        r = parse_message({"content": "Sure — I'll read the file and report back."})
        self.assertEqual(r.intents, ())
        self.assertEqual(r.ambiguous, ())              # no false positive

    def test_whole_content_all_valid_batch_still_runs(self):
        batch = ('[{"name":"write_file","arguments":{"path":"a","content":"x"}},'
                 ' {"name":"read_file","arguments":{"path":"a"}}]')
        r = parse_message({"content": batch})
        self.assertEqual(len(r.intents), 2)


if __name__ == "__main__":
    unittest.main()
