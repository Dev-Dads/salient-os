"""Cross-session DURABLE provenance store (F2 follow-up) — persistence + integrity + Session wiring.

The F2 manifest is session-lived; this persists it across sessions, HOST-controlled and
integrity-protected (HMAC under policy_key + workspace-subject binding, stored OUTSIDE the workspace
so the agent cannot tamper it). Pins: round-trip; missing=fresh; tamper/wrong-key/subject-mismatch/
corrupt => untrusted-and-honest (empty + degraded, never fake-clean, never raise); the Session opt-in
persists a drop across two sessions and clears across sessions; a store inside the workspace is refused
LOUD; no store => unchanged in-memory behaviour.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collaborator import provenancestore as ps
from collaborator.policycaps import workspace_subject
from collaborator.session import Session


def _try_symlink(target, link):
    """Create a symlink, skipping the test if the platform/privilege can't (Windows without dev mode)."""
    try:
        os.symlink(str(target), str(link))
    except (OSError, NotImplementedError, AttributeError) as exc:
        raise unittest.SkipTest(f"symlinks unavailable here: {exc}")

KEY = b"collab-policy-key"          # Session's default policy_key
SUBJ = "workspace:abc"


class StoreUnit(unittest.TestCase):
    def _tmpfile(self, d):
        return str(Path(d) / "prov.json")

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._tmpfile(d)
            self.assertTrue(ps.save(path, SUBJ, KEY, {"build.sh", "sub/x.sh"}, False))
            authored, incomplete, ok = ps.load(path, SUBJ, KEY)
            self.assertTrue(ok)
            self.assertFalse(incomplete)
            self.assertEqual(authored, {"build.sh", "sub/x.sh"})

    def test_missing_file_is_a_fresh_trusted_start(self):
        with tempfile.TemporaryDirectory() as d:
            authored, incomplete, ok = ps.load(self._tmpfile(d), SUBJ, KEY)
            self.assertEqual((authored, incomplete, ok), (set(), False, True))

    def test_incomplete_flag_persists(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._tmpfile(d)
            ps.save(path, SUBJ, KEY, {"a"}, True)
            _, incomplete, ok = ps.load(path, SUBJ, KEY)
            self.assertTrue(ok and incomplete)

    def test_tamper_breaks_the_mac_and_is_untrusted(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._tmpfile(d)
            ps.save(path, SUBJ, KEY, {"build.sh"}, False)
            doc = json.loads(Path(path).read_text())
            doc["body"]["authored"].append("evil.sh")       # add a path WITHOUT re-MACing
            Path(path).write_text(json.dumps(doc))
            authored, incomplete, ok = ps.load(path, SUBJ, KEY)
            self.assertFalse(ok)                              # untrusted...
            self.assertTrue(incomplete)                       # ...and honestly degraded
            self.assertEqual(authored, set())                 # never trust the tampered contents

    def test_wrong_key_is_untrusted(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._tmpfile(d)
            ps.save(path, SUBJ, KEY, {"build.sh"}, False)
            authored, incomplete, ok = ps.load(path, SUBJ, b"a-different-key")
            self.assertEqual((authored, incomplete, ok), (set(), True, False))

    def test_subject_mismatch_is_untrusted(self):
        # a store written for workspace A must not be trusted when loaded for workspace B (replay)
        with tempfile.TemporaryDirectory() as d:
            path = self._tmpfile(d)
            ps.save(path, "workspace:AAA", KEY, {"build.sh"}, False)
            authored, incomplete, ok = ps.load(path, "workspace:BBB", KEY)
            self.assertEqual((authored, incomplete, ok), (set(), True, False))

    def test_corrupt_json_is_untrusted_not_a_raise(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._tmpfile(d)
            Path(path).write_text("{not json")
            self.assertEqual(ps.load(path, SUBJ, KEY), (set(), True, False))

    def test_reorder_without_change_still_verifies(self):
        # the MAC is over the SORTED set, so a benign reorder of the stored list still verifies
        with tempfile.TemporaryDirectory() as d:
            path = self._tmpfile(d)
            ps.save(path, SUBJ, KEY, {"a.sh", "b.sh", "c.sh"}, False)
            doc = json.loads(Path(path).read_text())
            doc["body"]["authored"] = list(reversed(doc["body"]["authored"]))
            Path(path).write_text(json.dumps(doc))
            authored, _, ok = ps.load(path, SUBJ, KEY)
            self.assertTrue(ok)
            self.assertEqual(authored, {"a.sh", "b.sh", "c.sh"})


class SessionDurability(unittest.TestCase):
    def _ws_and_store(self, root):
        ws = Path(root) / "ws"
        ws.mkdir()
        store = Path(root) / "prov.json"     # a SIBLING of the workspace -> outside it
        return str(ws), str(store)

    def test_store_inside_workspace_is_refused_loud(self):
        with tempfile.TemporaryDirectory() as root:
            ws, _ = self._ws_and_store(root)
            inside = str(Path(ws) / ".prov.json")
            with self.assertRaises(ValueError):
                Session(workspace=ws, provenance_store=inside)

    def test_drop_persists_across_sessions(self):
        with tempfile.TemporaryDirectory() as root:
            ws, store = self._ws_and_store(root)
            (Path(ws) / "build.sh").write_text("x")              # the dropped file exists (so not pruned)
            s1 = Session(workspace=ws, provenance_store=store)
            s1.note_autonomous_authorship(["build.sh"])
            # a brand-new session over the SAME workspace + store sees the prior drop
            s2 = Session(workspace=ws, provenance_store=store)
            self.assertIn("build.sh", s2._autonomous_authored)
            self.assertFalse(s2._autonomous_tracking_incomplete)

    def test_clear_persists_across_sessions(self):
        with tempfile.TemporaryDirectory() as root:
            ws, store = self._ws_and_store(root)
            (Path(ws) / "build.sh").write_text("x")              # the dropped file exists (so not pruned)
            Session(workspace=ws, provenance_store=store).note_autonomous_authorship(["build.sh"])
            s2 = Session(workspace=ws, provenance_store=store)
            self.assertIn("build.sh", s2._autonomous_authored)
            s2.clear_autonomous_authorship(["build.sh"])         # human vets it
            s3 = Session(workspace=ws, provenance_store=store)
            self.assertNotIn("build.sh", s3._autonomous_authored)  # cleared for good

    def test_incomplete_flag_persists_across_sessions(self):
        with tempfile.TemporaryDirectory() as root:
            ws, store = self._ws_and_store(root)
            Session(workspace=ws, provenance_store=store).mark_tracking_incomplete()
            self.assertTrue(Session(workspace=ws, provenance_store=store)._autonomous_tracking_incomplete)

    def test_untrusted_store_loads_empty_and_degraded(self):
        with tempfile.TemporaryDirectory() as root:
            ws, store = self._ws_and_store(root)
            Session(workspace=ws, provenance_store=store).note_autonomous_authorship(["build.sh"])
            # a hostile/corrupt store (the agent cannot forge the MAC, but corruption must fail-honest)
            Path(store).write_text("garbage")
            s = Session(workspace=ws, provenance_store=store)
            self.assertEqual(s._autonomous_authored, set())          # nothing fake-trusted
            self.assertTrue(s._autonomous_tracking_incomplete)        # honestly degraded

    def test_a_store_from_another_workspace_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as root:
            wsA = Path(root) / "wsA"; wsA.mkdir()
            wsB = Path(root) / "wsB"; wsB.mkdir()
            store = str(Path(root) / "prov.json")
            Session(workspace=str(wsA), provenance_store=store).note_autonomous_authorship(["a.sh"])
            sB = Session(workspace=str(wsB), provenance_store=store)   # same file, different workspace
            self.assertEqual(sB._autonomous_authored, set())
            self.assertTrue(sB._autonomous_tracking_incomplete)

    def test_no_store_is_pure_in_memory_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            ws, _ = self._ws_and_store(root)
            s = Session(workspace=ws)                    # no provenance_store
            self.assertIsNone(s._provenance_store)
            s.note_autonomous_authorship(["x.sh"])       # works, just not persisted
            self.assertIn("x.sh", s._autonomous_authored)

    def test_relative_store_path_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            ws, _ = self._ws_and_store(root)
            with self.assertRaises(ValueError):
                Session(workspace=ws, provenance_store="prov.json")   # not absolute -> CWD ambiguity

    def test_symlink_in_workspace_pointing_outside_is_refused(self):
        # External panel crux: a store configured as ws/prov -> /outside RESOLVES outside but is
        # lexically IN the workspace; writing it (os.replace) would drop a real, agent-tamperable file
        # at ws/prov. The lexical-containment check must refuse it.
        with tempfile.TemporaryDirectory() as root:
            ws, _ = self._ws_and_store(root)
            outside = Path(root) / "real_store.json"
            link = Path(ws) / "prov"                      # lexically inside the workspace
            _try_symlink(outside, link)
            with self.assertRaises(ValueError):
                Session(workspace=ws, provenance_store=str(link))

    def test_persisted_store_path_is_the_resolved_target(self):
        # A symlink OUTSIDE the workspace is allowed, but the session must PERSIST/IO the resolved
        # target (not the symlink), so all reads/writes hit one fixed out-of-workspace location.
        with tempfile.TemporaryDirectory() as root:
            ws, _ = self._ws_and_store(root)
            real = Path(root) / "real_store.json"
            link = Path(root) / "link_store.json"         # sibling of ws (outside)
            _try_symlink(real, link)
            s = Session(workspace=ws, provenance_store=str(link))
            self.assertEqual(Path(s._provenance_store), real.resolve())

    def test_save_failure_marks_tracking_incomplete(self):
        # External panel: a failed durable write must not be silently trusted-as-complete later — the
        # session surfaces degraded tracking.
        with tempfile.TemporaryDirectory() as root:
            ws, store = self._ws_and_store(root)
            s = Session(workspace=ws, provenance_store=store)
            with patch("collaborator.provenancestore.save", return_value=False):
                s.note_autonomous_authorship(["build.sh"])
            self.assertTrue(s._autonomous_tracking_incomplete)


class PruneStaleProvenance(unittest.TestCase):
    """A durable manifest ACCUMULATES; a dropped-then-deleted file must be pruned at load so a human
    creating a same-named file isn't FALSELY warned (noise-blinding) and the store doesn't grow forever.
    Pruning only ever removes taints for ABSENT (un-runnable) files, so it never drops a live warning."""

    def _ws_store(self, root):
        ws = Path(root) / "ws"
        ws.mkdir()
        return ws, str(Path(root) / "prov.json")

    def test_present_file_is_kept_across_sessions(self):
        with tempfile.TemporaryDirectory() as root:
            ws, store = self._ws_store(root)
            (ws / "build.sh").write_text("#!/bin/sh\n")          # the dropped file EXISTS
            Session(workspace=str(ws), provenance_store=store).note_autonomous_authorship(["build.sh"])
            s = Session(workspace=str(ws), provenance_store=store)
            self.assertIn("build.sh", s._autonomous_authored)     # present -> kept

    def test_deleted_file_is_pruned_at_load_and_repersisted(self):
        with tempfile.TemporaryDirectory() as root:
            ws, store = self._ws_store(root)
            (ws / "build.sh").write_text("#!/bin/sh\n")
            Session(workspace=str(ws), provenance_store=store).note_autonomous_authorship(["build.sh"])
            (ws / "build.sh").unlink()                            # the file is deleted
            s = Session(workspace=str(ws), provenance_store=store)
            self.assertNotIn("build.sh", s._autonomous_authored)  # absent -> pruned
            # re-persisted, so a later session also doesn't carry the stale taint
            self.assertNotIn("build.sh",
                             Session(workspace=str(ws), provenance_store=store)._autonomous_authored)

    def test_prune_never_drops_a_live_warning(self):
        # a human about to run an autonomous-dropped file that STILL exists is still warned
        from collaborator import provenance
        with tempfile.TemporaryDirectory() as root:
            ws, store = self._ws_store(root)
            (ws / "evil.sh").write_text("payload\n")
            Session(workspace=str(ws), provenance_store=store).note_autonomous_authorship(["evil.sh"])
            s = Session(workspace=str(ws), provenance_store=store)
            self.assertEqual(
                provenance.references_autonomous_file("sh ./evil.sh", s._autonomous_authored, str(ws)),
                "evil.sh")

    def test_a_present_directory_entry_is_kept_even_if_broken_symlink(self):
        # External panel (grok): prune must key on the DIRECTORY ENTRY (lstat), not exists() — a present
        # entry (even a broken symlink) is NOT laundered away. Only a definitive absence prunes.
        with tempfile.TemporaryDirectory() as root:
            ws, store = self._ws_store(root)
            try:
                os.symlink(str(ws / "nonexistent-target"), str(ws / "link.sh"))  # a broken symlink ENTRY
            except (OSError, NotImplementedError, AttributeError) as exc:
                self.skipTest(f"symlinks unavailable here: {exc}")
            Session(workspace=str(ws), provenance_store=store).note_autonomous_authorship(["link.sh"])
            s = Session(workspace=str(ws), provenance_store=store)
            self.assertIn("link.sh", s._autonomous_authored)     # entry present -> kept, not pruned


if __name__ == "__main__":
    unittest.main()
