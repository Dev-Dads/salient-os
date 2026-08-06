"""The salience bus: append-only audit surface, structurally body-free (Finding G)."""

import unittest

from salienceos.interpreter import (
    Facet,
    SalienceBus,
    SalienceSignal,
    interpret,
    issue_policy,
)

KEY = b"policy-test-key"


def sig(subject, facet, influence, subsystem="m", prov=()):
    return SalienceSignal(subsystem, subject, facet, influence, 1.0, tuple(prov))


class BusAudit(unittest.TestCase):
    def test_publish_grows_hash_chain(self):
        bus = SalienceBus()
        h0 = bus.head()
        bus.publish(sig("req-1", Facet.ATTENTION, 0.5, prov=("evt:1",)))
        h1 = bus.head()
        bus.publish(sig("req-1", Facet.MEMORY, 0.3))
        h2 = bus.head()
        self.assertNotEqual(h0, h1)
        self.assertNotEqual(h1, h2)
        self.assertTrue(all(isinstance(h, str) and h for h in (h1, h2)))

    def test_publish_rejects_non_signal(self):
        bus = SalienceBus()
        for bad in ("string", 42, object(), SalienceSignal("m", "", "f", 0.5, 1.0, ())):
            with self.assertRaises(TypeError):
                bus.publish(bad)

    def test_signals_for_filters_by_subject(self):
        bus = SalienceBus()
        bus.publish(sig("req-1", Facet.ATTENTION, 0.5))
        bus.publish(sig("req-2", Facet.ATTENTION, 0.9))
        bus.publish(sig("req-1", Facet.MEMORY, 0.4))
        got = bus.signals_for("req-1")
        self.assertEqual(len(got), 2)
        self.assertTrue(all(s.subject == "req-1" for s in got))

    def test_signal_carries_no_body_fields(self):
        # The durable record cannot hold prompts/bodies/args/CoT — there are no
        # such fields; only provenance refs (which must be strings).
        fields = set(SalienceSignal.__dataclass_fields__)
        for forbidden in ("prompt", "body", "args", "content", "cot", "reasoning", "text"):
            self.assertNotIn(forbidden, fields)

    def test_emit_records_directive_decision(self):
        bus = SalienceBus()
        pol = issue_policy("pol-1", "req-1", ("fs.read:project",), 10, 1000, 0, 3,
                           "semantic", False, 2, 0.4, False, KEY)
        d = interpret(pol, [sig("req-1", Facet.ATTENTION, 0.7)], KEY)
        h = bus.emit(d)
        self.assertTrue(isinstance(h, str) and h)
        self.assertEqual(bus.head(), h)

    def test_emit_payload_carries_adaptation_rationale(self):
        # The durable record must carry the decider's rationale code so a
        # downstream consumer can act on the recorded reason (Finding D).
        import json as _json
        import tempfile
        pol = issue_policy("pol-1", "req-1", (), 10, 1000, 0, 3,
                           "semantic", False, 2, 0.4, False, KEY)
        d = interpret(pol, [], KEY)
        with tempfile.TemporaryDirectory() as td:
            path = td + "/bus.jsonl"
            bus = SalienceBus(path=path)
            bus.emit(d)
            with open(path, encoding="utf-8") as fh:
                entry = _json.loads(fh.readlines()[-1])
        self.assertEqual(entry["payload"]["adaptation_rationale"],
                         d.adaptation_rationale.value)
        self.assertEqual(entry["payload"]["adaptation_rationale"], "policy_disallowed")


class DirectiveReader(unittest.TestCase):
    """directives_for closes the write-only gap: emitted directives are
    readable, filtered by subject, oldest first, as copies that cannot reach
    back into the audit record."""

    def _bus_with_two_subjects(self):
        bus = SalienceBus()
        for subject in ("req-1", "req-2", "req-1"):
            pol = issue_policy("pol-1", subject, (), 10, 1000, 0, 3,
                               "semantic", False, 2, 0.4, False, KEY)
            bus.emit(interpret(pol, [sig(subject, Facet.ATTENTION, 0.7)], KEY))
        return bus

    def test_reads_filter_by_subject_oldest_first(self):
        bus = self._bus_with_two_subjects()
        got = bus.directives_for("req-1")
        self.assertEqual(len(got), 2)
        self.assertTrue(all(p["subject"] == "req-1" for p in got))
        self.assertEqual(len(bus.directives_for("req-2")), 1)
        self.assertEqual(bus.directives_for("req-none"), ())

    def test_round_trip_carries_the_rationale(self):
        # The factory policy has allow_adaptation=False, and the chain records
        # the FIRST failing clause — so the durable code is policy_disallowed.
        bus = self._bus_with_two_subjects()
        p = bus.directives_for("req-1")[0]
        self.assertEqual(p["adaptation_rationale"], "policy_disallowed")

    def test_returned_copies_cannot_mutate_the_record(self):
        bus = self._bus_with_two_subjects()
        p = bus.directives_for("req-1")[0]
        p["compute_budget"] = 999999
        p["allowed_capabilities"].append("fs.write:/")
        fresh = bus.directives_for("req-1")[0]
        self.assertNotEqual(fresh["compute_budget"], 999999)
        self.assertNotIn("fs.write:/", fresh["allowed_capabilities"])
        self.assertTrue(bus.verify_chain())


class ReplayOnOpen(unittest.TestCase):
    """A reopened bus continues its own chain (the session-resume case,
    ADR 0002) — and refuses to extend a record it cannot verify."""

    def _emit_on(self, bus, subject="req-1"):
        pol = issue_policy("pol-1", subject, (), 10, 1000, 0, 3,
                           "semantic", False, 2, 0.4, False, KEY)
        bus.emit(interpret(pol, bus.signals_for(subject), KEY))

    def test_reopened_bus_continues_the_chain(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = td + "/bus.jsonl"
            first = SalienceBus(path=path)
            first.publish(sig("req-1", Facet.ATTENTION, 0.5))
            self._emit_on(first)
            head_before = first.head()

            second = SalienceBus(path=path)  # a NEW process reopening the file
            self.assertEqual(second.head(), head_before)  # continues, not restarts
            self.assertEqual(len(second.signals_for("req-1")), 1)
            self.assertEqual(len(second.directives_for("req-1")), 1)
            second.publish(sig("req-1", Facet.MEMORY, 0.4))
            self._emit_on(second)
            self.assertTrue(second.verify_chain())

            third = SalienceBus(path=path)   # and the whole file still verifies
            self.assertTrue(third.verify_chain())
            self.assertEqual(len(third.directives_for("req-1")), 2)

    def test_corrupt_tail_refuses_to_open(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = td + "/bus.jsonl"
            bus = SalienceBus(path=path)
            bus.publish(sig("req-1", Facet.ATTENTION, 0.5))
            self._emit_on(bus)
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
            lines[-1] = lines[-1].replace('"prev"', '"perv"', 1)  # mangle tail
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
            with self.assertRaises(ValueError):
                SalienceBus(path=path)

    def test_tampered_middle_refuses_to_open(self):
        import json as _json
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = td + "/bus.jsonl"
            bus = SalienceBus(path=path)
            bus.publish(sig("req-1", Facet.ATTENTION, 0.5))
            bus.publish(sig("req-1", Facet.MEMORY, 0.9))
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
            e = _json.loads(lines[0])
            e["payload"]["influence"] = 1.0  # rewrite history, keep the old hash
            lines[0] = _json.dumps(e, sort_keys=True) + "\n"
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
            with self.assertRaises(ValueError):
                SalienceBus(path=path)


if __name__ == "__main__":
    unittest.main()
