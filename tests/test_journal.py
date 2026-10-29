import tempfile
import unittest
from pathlib import Path

from kahua_migration.journal import Journal, JournalConflict


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "migration.sqlite"
        self.journal = Journal(self.path)
        self.addCleanup(lambda: self.journal.close())
        self.scope = dict(environment="test", domain="d", project=1, folder=2)
        self.journal.register("operation", "original", self.scope)

    def test_crash_after_intent_does_not_allow_another_upload(self):
        self.journal.begin("operation", "uploaded")
        self.journal.close()
        self.journal = Journal(self.path)
        with self.assertRaises(JournalConflict):
            self.journal.begin("operation", "uploaded")

    def test_lost_response_requires_evidence_then_resumes_at_next_stage(self):
        self.journal.begin("operation", "uploaded")
        self.journal.uncertain("operation", "response lost")
        with self.assertRaises(JournalConflict):
            self.journal.begin("operation", "uploaded")
        self.journal.confirm("operation", "uploaded", dict(target_record_id=123,
                             observed_at="2026-10-28T00:00:00Z", evidence_path="readback.json"))
        self.journal.begin("operation", "metadata_verified")
        self.assertEqual(self.journal.status("operation")["pending_stage"], "metadata_verified")

    def test_changed_source_or_destination_cannot_reuse_state(self):
        with self.assertRaises(JournalConflict):
            self.journal.register("operation", "changed", self.scope)
        with self.assertRaises(JournalConflict):
            self.journal.register("operation", "original", {**self.scope, "folder": 3})

    def test_second_connection_cannot_claim_same_mutation(self):
        second = Journal(self.path)
        self.addCleanup(second.close)
        self.journal.begin("operation", "uploaded")
        with self.assertRaises(JournalConflict):
            second.begin("operation", "uploaded")

    def test_release_cannot_skip_metadata_and_qa(self):
        with self.assertRaises(JournalConflict):
            self.journal.begin("operation", "released")

    def cancellation_evidence(self):
        return dict(observed_at="2026-10-29T00:00:00Z", evidence_path="negative-audit.json",
                    target_snapshot_complete=True, planned_revision_absent=True,
                    existing_versions_unchanged=True, precommit_form_closed=True, commit_never_attempted=True)

    def test_cancelled_precommit_wizard_can_retry_only_after_complete_negative_audit(self):
        self.journal.begin("operation", "uploaded")
        with self.assertRaises(JournalConflict):
            self.journal.cancel_precommit_upload("operation", {**self.cancellation_evidence(), "existing_versions_unchanged": False})
        self.journal.cancel_precommit_upload("operation", self.cancellation_evidence())
        self.journal.begin("operation", "uploaded")
        events = self.journal.db.execute("SELECT event_type FROM events WHERE operation_key='operation' ORDER BY event_id").fetchall()
        self.assertEqual([r[0] for r in events], ["intent", "cancelled_before_commit", "intent"])

    def test_uncertain_final_save_cannot_be_cleared_using_absence(self):
        self.journal.begin("operation", "uploaded")
        self.journal.mark_upload_commit("operation", dict(observed_at="2026-10-29T00:00:00Z", evidence_path="before-save.json"))
        self.journal.uncertain("operation", "Save timed out")
        with self.assertRaisesRegex(JournalConflict, "final Save was attempted"):
            self.journal.cancel_precommit_upload("operation", self.cancellation_evidence())
        with self.assertRaises(JournalConflict):
            self.journal.begin("operation", "uploaded")

    def test_final_save_intent_cannot_be_replayed_after_restart(self):
        self.journal.begin("operation", "uploaded")
        evidence = dict(observed_at="2026-10-29T00:00:00Z", evidence_path="before-save.json")
        self.journal.mark_upload_commit("operation", evidence)
        self.journal.close()
        self.journal = Journal(self.path)
        with self.assertRaisesRegex(JournalConflict, "already has an intent"):
            self.journal.mark_upload_commit("operation", evidence)


if __name__ == "__main__":
    unittest.main()
