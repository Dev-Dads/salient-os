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
        # The two req-1 directives carry DIFFERENT budgets, so the order is
        # actually observable (identical payloads could not pin "oldest
        # first" — a reversed reader would pass).
        bus = SalienceBus()
        budgets = []
        for subject, infl in (("req-1", 0.2), ("req-2", 0.5), ("req-1", 0.9)):
            pol = issue_policy("pol-1", subject, (), 10, 1000, 0, 3,
                               "semantic", False, 2, 0.4, False, KEY)
            d = interpret(pol, [sig(subject, Facet.ATTENTION, infl)], KEY)
            bus.emit(d)
            if subject == "req-1":
                budgets.append(d.compute_budget)
        self.assertNotEqual(budgets[0], budgets[1])
        got = bus.directives_for("req-1")
        self.assertEqual([p["compute_budget"] for p in got], budgets)
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

    def test_spliced_intact_lines_refuse_to_open(self):
        # Both lines individually hash-correct, but reordered: only the prev
        # continuity clause can catch this — pinned here so it stays.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = td + "/bus.jsonl"
            bus = SalienceBus(path=path)
            bus.publish(sig("req-1", Facet.ATTENTION, 0.5))
            bus.publish(sig("req-1", Facet.MEMORY, 0.9))
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines([lines[1], lines[0]])
            with self.assertRaises(ValueError):
                SalienceBus(path=path)

    def _crafted_line(self, kind, payload, prev):
        # Mirror _append's encoding exactly, with a CORRECT hash — these tests
        # attack the semantic fences, not the digest.
        import json as _json
        from salienceos.verifier.signing import digest
        base = {"kind": kind, "payload": payload, "prev": prev}
        return _json.dumps({**base, "hash": digest(base)}, sort_keys=True) + "\n", digest(base)

    def test_persisted_invalid_signal_refuses_to_open(self):
        # A hash-correct line whose signal fails valid_signal (influence 5.0)
        # must not be served by signals_for on a reopened bus — pin the
        # re-validation fence in _replay.
        import tempfile
        payload = {"subsystem_id": "s", "subject": "req-1", "facet": "attention",
                   "influence": 5.0, "confidence": 1.0, "provenance": []}
        line, _ = self._crafted_line("signal", payload, "")
        with tempfile.TemporaryDirectory() as td:
            path = td + "/bus.jsonl"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(line)
            with self.assertRaises(ValueError):
                SalienceBus(path=path)

    def test_smuggled_key_refuses_to_open(self):
        # Unknown top-level keys sit outside the digest base; without the
        # exact-key-set fence the JSONL would be a smuggling channel through
        # the audit fence (Finding G).
        import json as _json
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = td + "/bus.jsonl"
            bus = SalienceBus(path=path)
            bus.publish(sig("req-1", Facet.ATTENTION, 0.5))
            with open(path, encoding="utf-8") as fh:
                e = _json.loads(fh.readline())
            e["smuggled"] = "x" * 10000  # rides outside the hash
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_json.dumps(e, sort_keys=True) + "\n")
            with self.assertRaises(ValueError):
                SalienceBus(path=path)

    def test_non_dict_payload_refuses_to_open(self):
        # A hash-correct directive line with a non-dict payload must fail at
        # the door, not later inside directives_for.
        import tempfile
        line, _ = self._crafted_line("directive", "not-a-dict", "")
        with tempfile.TemporaryDirectory() as td:
            path = td + "/bus.jsonl"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(line)
            with self.assertRaises(ValueError):
                SalienceBus(path=path)

    def _directive_payload(self, **overrides):
        p = {"subject": "req-1", "policy_id": "p", "compute_budget": 10,
             "verification_depth": 3, "retention_class": "ephemeral",
             "routing_hint": "", "adaptation_eligibility": "none",
             "adaptation_rationale": "policy_disallowed",
             "allowed_capabilities": [], "reconfigure": "between_turn",
             "interpreter_version": "x", "reasons": []}
        p.update(overrides)
        return p

    def _open_with_directive_payload(self, payload):
        import tempfile
        line, _ = self._crafted_line("directive", payload, "")
        with tempfile.TemporaryDirectory() as td:
            path = td + "/bus.jsonl"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(line)
            return SalienceBus(path=path)

    def test_directive_payload_fence_on_replay(self):
        # The DIRECTIVE half of the audit fence: hash-correct lines whose
        # payload smuggles content (prompt-sized values, or keys INSIDE the
        # payload where the top-level key-set fence cannot see them) refuse
        # to open. Nothing prompt-sized can become durable through either
        # entry kind.
        good = self._directive_payload()
        self.assertTrue(self._open_with_directive_payload(good).verify_chain())
        bad_payloads = [
            self._directive_payload(prompt="H" * 50_000),          # smuggled key
            self._directive_payload(subject="H" * 50_000),         # oversized field
            self._directive_payload(reasons=["H" * 50_000]),       # oversized item
            self._directive_payload(allowed_capabilities=["c"] * 65),  # unbounded list
            {k: v for k, v in good.items() if k != "subject"},     # missing key
        ]
        for payload in bad_payloads:
            with self.assertRaises(ValueError):
                self._open_with_directive_payload(payload)

    def test_unknown_entry_kind_refuses_to_open(self):
        import tempfile
        line, _ = self._crafted_line("blob", {"x": "y"}, "")
        with tempfile.TemporaryDirectory() as td:
            path = td + "/bus.jsonl"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(line)
            with self.assertRaises(ValueError):
                SalienceBus(path=path)

    def test_emit_refuses_prompt_sized_directives(self):
        # The emit-side fence: a hand-built Directive with prompt-sized
        # strings must be rejected exactly like an invalid signal.
        from salienceos.interpreter import (
            AdaptationEligibility, AdaptationRationale, Directive, Reconfigure,
        )
        base = dict(
            subject="req-1", policy_id="p", compute_budget=10,
            verification_depth=3, retention_class="ephemeral", routing_hint="",
            adaptation_eligibility=AdaptationEligibility.NONE,
            adaptation_rationale=AdaptationRationale.POLICY_DISALLOWED,
            allowed_capabilities=(), reconfigure=Reconfigure.BETWEEN_TURN,
            interpreter_version="x", reasons=(),
        )
        bus = SalienceBus()
        bus.emit(Directive(**base))  # the bounded shape is accepted
        for field, value in (("subject", "H" * 50_000),
                             ("routing_hint", "H" * 50_000),
                             ("reasons", ("H" * 50_000,)),
                             ("allowed_capabilities", ("c",) * 65)):
            with self.assertRaises(TypeError):
                bus.emit(Directive(**{**base, field: value}))


if __name__ == "__main__":
    unittest.main()
