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


if __name__ == "__main__":
    unittest.main()
