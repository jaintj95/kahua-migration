"""Durable intent/receipt journal. This module does not upload anything.

Every external mutation must have a committed intent first. Pending or uncertain
intents cannot be repeated until target evidence resolves the outcome. A local
receipt alone does not establish that Kahua currently matches the manifest.
"""

import json
import sqlite3
from datetime import datetime, timezone


STAGES = ("uploaded", "metadata_verified", "submitted", "accepted", "released", "verified")


class JournalConflict(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


class Journal:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS operations (
                operation_key TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                target_scope TEXT NOT NULL,
                stage_index INTEGER NOT NULL DEFAULT -1,
                pending_stage TEXT,
                uncertain INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY,
                operation_key TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                stage TEXT NOT NULL,
                evidence TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS batch_checks (
                batch_key TEXT PRIMARY KEY,
                observed_at TEXT NOT NULL,
                evidence_path TEXT NOT NULL
            );
        """)

    def close(self):
        self.db.close()

    def _event(self, key, event, stage, evidence):
        self.db.execute("INSERT INTO events(operation_key,timestamp,event_type,stage,evidence) VALUES(?,?,?,?,?)",
                        (key, now(), event, stage, json.dumps(evidence, sort_keys=True)))

    def _get(self, key):
        row = self.db.execute("SELECT * FROM operations WHERE operation_key=?", (key,)).fetchone()
        if row is None:
            raise JournalConflict("Operation is not registered")
        return row

    def register(self, key, fingerprint, target_scope):
        scope = json.dumps(target_scope, sort_keys=True)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("INSERT OR IGNORE INTO operations(operation_key,fingerprint,target_scope) VALUES(?,?,?)",
                            (key, fingerprint, scope))
            row = self._get(key)
            if row["fingerprint"] != fingerprint or row["target_scope"] != scope:
                raise JournalConflict("Source metadata/content or destination changed; reconcile before making a new plan")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def begin(self, key, stage):
        if stage not in STAGES:
            raise JournalConflict(f"Unknown stage: {stage}")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self._get(key)
            if row["pending_stage"] is not None:
                raise JournalConflict("An earlier attempt has no verified outcome; reconcile it before any retry")
            if STAGES.index(stage) != row["stage_index"] + 1:
                raise JournalConflict("Stages must be verified in order and cannot be replayed")
            self.db.execute("UPDATE operations SET pending_stage=?,uncertain=0 WHERE operation_key=?", (stage, key))
            self._event(key, "intent", stage, {})
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def uncertain(self, key, reason):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self._get(key)
            if row["pending_stage"] is None:
                raise JournalConflict("No attempt is pending")
            self.db.execute("UPDATE operations SET uncertain=1 WHERE operation_key=?", (key,))
            self._event(key, "uncertain", row["pending_stage"], {"reason": reason})
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def mark_upload_commit(self, key, evidence):
        """Persist before clicking the final Save in a browser upload wizard."""
        if not evidence.get("observed_at") or not evidence.get("evidence_path"):
            raise JournalConflict("Saved precommit review evidence is required")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self._get(key)
            if row["pending_stage"] != "uploaded" or row["stage_index"] != -1:
                raise JournalConflict("No initial upload is pending")
            intent = self.db.execute("SELECT MAX(event_id) FROM events WHERE operation_key=? AND event_type='intent' AND stage='uploaded'",
                                     (key,)).fetchone()[0]
            committed = self.db.execute("SELECT 1 FROM events WHERE operation_key=? AND event_type='commit_intent' AND event_id>?",
                                       (key, intent or 0)).fetchone()
            if committed:
                raise JournalConflict("The final Save already has an intent; read back its outcome instead of repeating it")
            self._event(key, "commit_intent", "uploaded", evidence)
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def cancel_precommit_upload(self, key, evidence):
        """Resolve a cancelled wizard only when no final Save was attempted.

        The caller must supply a fresh complete target audit proving absence and
        unchanged existing file versions. A committed/uncertain Save can never
        use this path merely because a record has not appeared yet.
        """
        required = ("target_snapshot_complete", "planned_revision_absent",
                    "existing_versions_unchanged", "precommit_form_closed", "commit_never_attempted")
        if (not evidence.get("observed_at") or not evidence.get("evidence_path")
                or not all(evidence.get(field) is True for field in required)):
            raise JournalConflict("A complete negative target audit and precommit cancellation evidence are required")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self._get(key)
            if row["pending_stage"] != "uploaded" or row["stage_index"] != -1:
                raise JournalConflict("Only a pending initial upload can be cancelled")
            intent = self.db.execute("SELECT MAX(event_id) FROM events WHERE operation_key=? AND event_type='intent' AND stage='uploaded'",
                                     (key,)).fetchone()[0]
            committed = self.db.execute("SELECT 1 FROM events WHERE operation_key=? AND event_type='commit_intent' AND event_id>?",
                                        (key, intent or 0)).fetchone()
            if committed:
                raise JournalConflict("A final Save was attempted; reconcile its outcome without replaying the upload")
            self._event(key, "cancelled_before_commit", "uploaded", evidence)
            self.db.execute("UPDATE operations SET pending_stage=NULL,uncertain=0 WHERE operation_key=?", (key,))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def confirm(self, key, stage, evidence):
        if not evidence.get("target_record_id") or not evidence.get("observed_at") or not evidence.get("evidence_path"):
            raise JournalConflict("A target record ID, observation time, and saved verification evidence are required")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self._get(key)
            if row["pending_stage"] != stage:
                raise JournalConflict("Evidence must correspond to the pending stage")
            self.db.execute("UPDATE operations SET stage_index=?,pending_stage=NULL,uncertain=0 WHERE operation_key=?",
                            (STAGES.index(stage), key))
            self._event(key, "confirmed", stage, evidence)
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def status(self, key):
        row = dict(self._get(key))
        row["verified_stage"] = STAGES[row["stage_index"]] if row["stage_index"] >= 0 else None
        return row

    def batch_verified(self, key):
        return self.db.execute("SELECT 1 FROM batch_checks WHERE batch_key=?", (key,)).fetchone() is not None

    def confirm_batch(self, key, evidence_path):
        if not evidence_path:
            raise JournalConflict("Saved batch verification evidence is required")
        self.db.execute("INSERT INTO batch_checks(batch_key,observed_at,evidence_path) VALUES(?,?,?)",
                        (key, now(), evidence_path))
