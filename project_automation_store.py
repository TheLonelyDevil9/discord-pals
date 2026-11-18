"""Durable cases, human decisions, and an outbox for project automation.

Each operation owns a short SQLite transaction, so Flask threads and the Discord
event loop can use the same store. No transaction remains open across network
work. A worker must mark a job inflight before a remote write: abandoned writes
then require reconciliation instead of being sent again after a restart.
"""

from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid


MAX_DOCUMENT_BYTES = 256 * 1024
MAX_LINK_BYTES = 64 * 1024
MAX_TRANSCRIPT_ENTRIES = 100
MAX_DECISIONS = 100
DAY_SECONDS = 86400
FAILED_RETENTION_DAYS = 7
# GitHub offers a delivery for redelivery for 3 days, so its marker must outlive that window:
# https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/redelivering-webhooks
DELIVERY_MARKER_DAYS = 4
PRUNE_BATCH_ROWS = 500
BULK_DELETE_ROWS = 20000
COMPACT_MIN_FREE_SHARE = 0.25
COMPACT_MIN_FREE_BYTES = 16 * 1024 * 1024
JOURNAL_SIZE_LIMIT = 64 * 1024 * 1024
# PRAGMA user_version reaches this value once the one-time queue cleanup has run.
QUEUE_CLEANUP_VERSION = 1
_MANAGED_CASE_FIELDS = {"id", "source_key", "revision", "created_at", "updated_at", "decisions"}
_IDENTITY_FIELDS = {"channel_id", "reporter_id", "guild_id", "bot_name", "repository"}


class Conflict(Exception):
    """A decision, job lease, or document revision is no longer current."""


def _text(value, name, maximum=512):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be nonempty text of at most {maximum} characters")
    return value


def _json_object(value, maximum=MAX_DOCUMENT_BYTES):
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Expected JSON-compatible data") from exc
    if len(encoded.encode("utf-8")) > maximum:
        raise ValueError("Stored document is too large")
    return encoded


def _duration(value, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Duration must be a number")
    if not math.isfinite(value) or not minimum <= value <= 365 * 86400:
        raise ValueError("Duration is outside the supported range")
    return float(value)


def _limit(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("Limit must be a positive integer")
    return min(value, 500)


def _days(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 365:
        raise ValueError("History days must be a whole number from 0 to 365")
    return value


def _job_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 2**63 - 1:
        raise ValueError("Job ID must be a positive integer")
    return value


def _job_snapshot(value):
    if not isinstance(value, dict) or set(value) != {"id", "updated_at"}:
        raise ValueError("Expected a job ID and updated_at snapshot")
    _job_id(value["id"])
    timestamp = value["updated_at"]
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        raise ValueError("updated_at must be a finite number")
    try:
        finite = math.isfinite(timestamp)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("updated_at must be a finite number")
    return value


class AutomationStore:
    """SQLite store with compare-and-swap decisions and leased durable jobs."""

    def __init__(self, path):
        if str(path) == ":memory:":
            raise ValueError("AutomationStore requires a persistent database path")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY,
                    source_key TEXT NOT NULL UNIQUE,
                    channel_id TEXT NOT NULL,
                    reporter_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    document TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS cases_channel
                    ON cases(channel_id, reporter_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    key TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    available_at REAL NOT NULL,
                    lease_until REAL,
                    lease_token TEXT,
                    last_error TEXT NOT NULL DEFAULT '',
                    recovery_notes TEXT NOT NULL DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS jobs_ready
                    ON jobs(state, available_at, id);
                CREATE INDEX IF NOT EXISTS jobs_sync_cycle
                    ON jobs(json_extract(payload, '$._sync_cycle'), state);
                CREATE TABLE IF NOT EXISTS links (
                    kind TEXT NOT NULL,
                    key TEXT NOT NULL,
                    document TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(kind, key)
                );
                CREATE INDEX IF NOT EXISTS pr_sources_scope
                    ON links(substr(key, 1, 64), json_extract(document, '$.event.number'),
                             json_extract(document, '$.event.activity_type'), key)
                    WHERE kind = 'pr_source';
            """)

    @contextmanager
    def _connection(self, write=False, timeout=10):
        connection = sqlite3.connect(str(self.path), timeout=timeout, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(f"PRAGMA journal_size_limit={JOURNAL_SIZE_LIMIT}")
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except BaseException:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _case(connection, case_id):
        row = connection.execute("SELECT document FROM cases WHERE id = ?", (case_id,)).fetchone()
        return json.loads(row["document"]) if row else None

    @staticmethod
    def _encode_case(case):
        transcript = case.get("transcript", [])
        if not isinstance(transcript, list):
            raise ValueError("Case transcript must be a list")
        if len(transcript) > MAX_TRANSCRIPT_ENTRIES:
            # Keep the original report as well as the most recent conversation.
            case["transcript"] = transcript[:1] + transcript[-(MAX_TRANSCRIPT_ENTRIES - 1):]
        return _json_object(case)

    @staticmethod
    def _save_case(connection, case):
        document = AutomationStore._encode_case(case)
        connection.execute(
            "UPDATE cases SET state = ?, updated_at = ?, document = ? WHERE id = ?",
            (str(case.get("state", "assessing")), case["updated_at"], document, case["id"]),
        )
        return json.loads(document)

    def create_case(self, case, source_key):
        """Create one case per Discord source, returning the original on replay."""
        _text(source_key, "Source key")
        _json_object(case)
        with self._connection(write=True) as connection:
            existing = connection.execute("SELECT document FROM cases WHERE source_key = ?", (source_key,)).fetchone()
            if existing:
                return json.loads(existing["document"])
            now = time.time()
            record = {"state": "assessing", "transcript": [], "gate": None, **case}
            record.update(id=uuid.uuid4().hex, source_key=source_key, revision=1, created_at=now, updated_at=now, decisions=[])
            for field in ("channel_id", "reporter_id", "guild_id"):
                if field in record:
                    record[field] = str(record[field])
            document = self._encode_case(record)
            connection.execute(
                "INSERT INTO cases(id, source_key, channel_id, reporter_id, state, updated_at, document) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (record["id"], source_key, str(record.get("channel_id", "")), str(record.get("reporter_id", "")),
                 str(record["state"]), now, document),
            )
            return json.loads(document)

    def get_case(self, case_id):
        with self._connection() as connection:
            return self._case(connection, case_id)

    def get_case_by_source(self, source_key):
        with self._connection() as connection:
            row = connection.execute("SELECT document FROM cases WHERE source_key = ?", (source_key,)).fetchone()
            return json.loads(row["document"]) if row else None

    def find_case(self, channel_id, reporter_id=None):
        query = "SELECT document FROM cases WHERE channel_id = ?"
        parameters = [str(channel_id)]
        if reporter_id is not None:
            query += " AND reporter_id = ?"
            parameters.append(str(reporter_id))
        with self._connection() as connection:
            row = connection.execute(query + " ORDER BY updated_at DESC, rowid DESC LIMIT 1", parameters).fetchone()
            return json.loads(row["document"]) if row else None

    def list_cases(self, limit=100):
        with self._connection() as connection:
            rows = connection.execute("SELECT document FROM cases ORDER BY updated_at DESC, rowid DESC LIMIT ?", (_limit(limit),)).fetchall()
            return [json.loads(row["document"]) for row in rows]

    def list_pending_cases(self):
        """Restore every unfinished case after reconnecting, including old gates."""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT document FROM cases WHERE state NOT IN ('closed', 'filed', 'cancelled') ORDER BY updated_at, rowid"
            ).fetchall()
            return [json.loads(row["document"]) for row in rows]

    def cases_for_issue(self, repository, number):
        """Find every linked reporter, without a dashboard pagination cutoff."""
        _text(repository, "Repository", 256)
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ValueError("Issue number must be a positive integer")
        with self._connection() as connection:
            rows = connection.execute("SELECT document FROM cases ORDER BY updated_at, rowid")
            matches = []
            for row in rows:
                case = json.loads(row["document"])
                if str(case.get("repository", "")).casefold() == repository.casefold() and case.get("linked_issue_number") == number:
                    matches.append(case)
            return matches

    def count_active_cases(self, reporter_id, guild_id):
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT document FROM cases WHERE reporter_id = ? AND state NOT IN ('closed', 'filed', 'cancelled')",
                (str(reporter_id),),
            )
            return sum(str(json.loads(row["document"]).get("guild_id", "")) == str(guild_id) for row in rows)

    @staticmethod
    def _current_case(connection, case_id, expected_revision):
        if expected_revision is not None and (
            isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1
        ):
            raise ValueError("Revision must be a positive integer")
        case = AutomationStore._case(connection, case_id)
        if case is None:
            raise KeyError(case_id)
        if expected_revision is not None and case["revision"] != expected_revision:
            raise Conflict("This case changed; use its current decision controls")
        return case

    def update_case(self, case_id, patch, expected_revision=None, job=None):
        """Apply a patch and optional outbox job in the same transaction."""
        _json_object(patch)
        if (_MANAGED_CASE_FIELDS | _IDENTITY_FIELDS).intersection(patch):
            raise ValueError("Case identity, revisions, and decision audit are managed by the store")
        with self._connection(write=True) as connection:
            case = self._current_case(connection, case_id, expected_revision)
            case.update(patch)
            case["revision"] += 1
            case["updated_at"] = time.time()
            updated = self._save_case(connection, case)
            if job is not None:
                self._enqueue_spec(connection, job)
            return updated

    def consume_gate(self, case_id, expected_revision, action, actor, job=None):
        """Consume a stored human choice once; authorization belongs to the caller."""
        _text(action, "Action", 80)
        _text(str(actor), "Actor", 128)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            raise ValueError("A gate requires its exact revision")
        with self._connection(write=True) as connection:
            case = self._current_case(connection, case_id, expected_revision)
            gate = case.get("gate")
            options = gate.get("options", []) if isinstance(gate, dict) else []
            if action not in {option.get("key") for option in options if isinstance(option, dict)}:
                raise Conflict("That choice is no longer available")
            decisions = case["decisions"] + [{"actor": str(actor), "action": action, "revision": expected_revision, "time": time.time()}]
            case.update(gate=None, state="processing", decisions=decisions[-MAX_DECISIONS:], revision=case["revision"] + 1, updated_at=time.time())
            updated = self._save_case(connection, case)
            if job is not None:
                self._enqueue_spec(connection, job)
            return updated

    @staticmethod
    def _enqueue_spec(connection, job):
        if not isinstance(job, dict):
            raise ValueError("Job must be an object")
        return AutomationStore._enqueue(connection, job["kind"], job["payload"], job["key"], job.get("delay", 0))

    @staticmethod
    def _enqueue(connection, kind, payload, key, delay):
        _text(kind, "Job kind", 80)
        _text(key, "Job key")
        document = _json_object(payload)
        delay = _duration(delay)
        existing = connection.execute("SELECT id, kind, payload FROM jobs WHERE key = ?", (key,)).fetchone()
        if existing:
            if existing["kind"] != kind or existing["payload"] != document:
                raise Conflict("A different job already uses this deduplication key")
            return existing["id"]
        now = time.time()
        cursor = connection.execute(
            "INSERT INTO jobs(kind, key, payload, created_at, updated_at, available_at) VALUES (?, ?, ?, ?, ?, ?)",
            (kind, key, document, now, now, now + delay),
        )
        return cursor.lastrowid

    def enqueue(self, kind, payload, key, delay=0):
        with self._connection(write=True) as connection:
            return self._enqueue(connection, kind, payload, key, delay)

    def enqueue_delivery(self, delivery, kind, payload, key):
        """Queue a webhook delivery once per delivery ID; return None when it was seen before.

        The marker keeps only a SHA-256 of the ID, so a redelivery stays a no-op after
        retention deletes the job row.
        """
        marker = hashlib.sha256(_text(delivery, "Delivery ID", 128).encode("utf-8")).hexdigest()
        with self._connection(write=True) as connection:
            if connection.execute("SELECT 1 FROM links WHERE kind = 'github_delivery' AND key = ?",
                                  (marker,)).fetchone():
                return None
            connection.execute("INSERT INTO links(kind, key, document, updated_at) VALUES ('github_delivery', ?, '{}', ?)",
                               (marker, time.time()))
            return self._enqueue(connection, kind, payload, key, 0)

    @staticmethod
    def _expire_leases(connection):
        now = time.time()
        connection.execute(
            "UPDATE jobs SET state = 'recovery', lease_until = NULL, lease_token = NULL, updated_at = ?, "
            "last_error = 'A remote write may have completed. Verify its result before retrying.' "
            "WHERE state = 'inflight' AND lease_until <= ?", (now, now),
        )
        connection.execute(
            "UPDATE jobs SET state = 'pending', lease_until = NULL, lease_token = NULL, updated_at = ? "
            "WHERE state = 'running' AND lease_until <= ?", (now, now),
        )

    def _expire_due_leases(self):
        """Release lapsed leases in their own short write, taken only when one has lapsed."""
        with self._connection() as connection:
            due = connection.execute(
                "SELECT 1 FROM jobs WHERE state IN ('running', 'inflight') AND lease_until <= ? LIMIT 1",
                (time.time(),),
            ).fetchone()
        if due:
            with self._connection(write=True) as connection:
                self._expire_leases(connection)

    @staticmethod
    def _job(row):
        if row is None:
            return None
        job = dict(row)
        job["payload"] = json.loads(job["payload"])
        job["recovery_notes"] = json.loads(job["recovery_notes"])
        return job

    def get_job(self, job_id):
        self._expire_due_leases()
        with self._connection() as connection:
            return self._job(connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())

    def list_jobs(self, limit=100):
        with self._connection(write=True) as connection:
            self._expire_leases(connection)
            rows = connection.execute("SELECT * FROM jobs ORDER BY updated_at DESC, id DESC LIMIT ?", (_limit(limit),)).fetchall()
            return [self._job(row) for row in rows]

    def list_job_activity(self, *, view="recent", before_id=None, limit=100):
        """Page presentation state without changing the worker's job listing."""
        filters = {
            "recent": "(j.state != 'failed' OR d.key IS NULL)",
            "attention": "(j.state = 'recovery' OR (j.state = 'failed' AND d.key IS NULL))",
            "dismissed": "(j.state = 'failed' AND d.key IS NOT NULL)",
        }
        if not isinstance(view, str) or view not in filters:
            raise ValueError("Unknown activity view")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Activity limit must be between 1 and 100")
        parameters = []
        where = filters[view]
        if before_id is not None:
            parameters.append(_job_id(before_id))
            where += " AND j.id < ?"
        parameters.append(limit + 1)
        self._expire_due_leases()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT j.*, CASE WHEN j.state = 'failed' THEN d.updated_at END AS dismissed_at "
                "FROM jobs j LEFT JOIN links d ON d.kind = 'job_dismissal' AND d.key = CAST(j.id AS TEXT) "
                "WHERE " + where + " ORDER BY j.id DESC LIMIT ?", parameters,
            ).fetchall()
        return {"jobs": [self._job(row) for row in rows[:limit]],
                "next_before_id": rows[limit - 1]["id"] if len(rows) > limit else None}

    def dismiss_jobs(self, jobs):
        """Atomically hide exactly the failed snapshots the operator inspected."""
        if not isinstance(jobs, list) or not 1 <= len(jobs) <= 100:
            raise ValueError("Choose between 1 and 100 jobs")
        snapshots = [_job_snapshot(job) for job in jobs]
        if len({job["id"] for job in snapshots}) != len(snapshots):
            raise ValueError("Job IDs must be unique")
        with self._connection(write=True) as connection:
            for snapshot in snapshots:
                self._set_job_dismissal(connection, snapshot, dismissed=True)
        return len(snapshots)

    def restore_job(self, job_id, updated_at):
        snapshot = _job_snapshot({"id": job_id, "updated_at": updated_at})
        with self._connection(write=True) as connection:
            self._set_job_dismissal(connection, snapshot, dismissed=False)

    @staticmethod
    def _set_job_dismissal(connection, snapshot, *, dismissed):
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (snapshot["id"],)).fetchone()
        if row is None:
            raise KeyError(snapshot["id"])
        if row["state"] != "failed" or row["updated_at"] != snapshot["updated_at"]:
            raise Conflict("The failure changed. Refresh activity before trying again.")
        key = str(row["id"])
        existing = connection.execute(
            "SELECT 1 FROM links WHERE kind = 'job_dismissal' AND key = ?", (key,),
        ).fetchone()
        if bool(existing) == dismissed:
            return
        now = time.time()
        if dismissed:
            connection.execute(
                "INSERT INTO links(kind, key, document, updated_at) VALUES ('job_dismissal', ?, ?, ?)",
                (key, _json_object({"actor": "dashboard", "dismissed_at": now,
                                    "updated_at": row["updated_at"]}), now),
            )
        else:
            connection.execute("DELETE FROM links WHERE kind = 'job_dismissal' AND key = ?", (key,))
        notes = json.loads(row["recovery_notes"]) + [{
            "actor": "dashboard", "outcome": "dismiss" if dismissed else "restore", "time": now,
            "note": "Hidden from activity; no work changed." if dismissed else "Restored to activity; no work changed.",
        }]
        connection.execute("UPDATE jobs SET recovery_notes = ? WHERE id = ?",
                           (json.dumps(notes[-MAX_DECISIONS:], ensure_ascii=False), row["id"]))

    def claim_job(self, kinds=None, lease_seconds=120):
        lease_seconds = _duration(lease_seconds, minimum=1)
        query = "SELECT id FROM jobs WHERE state = 'pending' AND available_at <= ?"
        parameters = [time.time()]
        if kinds is not None:
            if isinstance(kinds, str):
                raise ValueError("Job kinds must be a collection")
            kinds = list(kinds)
            if not kinds:
                return None
            for kind in kinds:
                _text(kind, "Job kind", 80)
            query += " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
            parameters.extend(kinds)
        with self._connection(write=True) as connection:
            self._expire_leases(connection)
            row = connection.execute(query + " ORDER BY available_at, id LIMIT 1", parameters).fetchone()
            if row is None:
                return None
            now = time.time()
            connection.execute(
                "UPDATE jobs SET state = 'running', attempts = attempts + 1, updated_at = ?, lease_until = ?, lease_token = ? WHERE id = ?",
                (now, now + lease_seconds, uuid.uuid4().hex, row["id"]),
            )
            return self._job(connection.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone())

    @staticmethod
    def _claimed_job(connection, job_id, lease_token):
        AutomationStore._expire_leases(connection)
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        if row["state"] not in ("running", "inflight"):
            raise Conflict("This job no longer has an active worker lease")
        if lease_token is not None and row["lease_token"] != lease_token:
            raise Conflict("This job belongs to a newer worker lease")
        return row

    def mark_job_inflight(self, job_id, lease_token=None, lease_seconds=120):
        """Call immediately before a remote write; a crash now requires recovery."""
        lease_seconds = _duration(lease_seconds, minimum=1)
        with self._connection(write=True) as connection:
            self._claimed_job(connection, job_id, lease_token)
            now = time.time()
            connection.execute("UPDATE jobs SET state = 'inflight', updated_at = ?, lease_until = ? WHERE id = ?", (now, now + lease_seconds, job_id))

    def complete_job(self, job_id, lease_token=None):
        with self._connection(write=True) as connection:
            self._claimed_job(connection, job_id, lease_token)
            connection.execute(
                "UPDATE jobs SET state = 'done', updated_at = ?, lease_until = NULL, lease_token = NULL, last_error = '' WHERE id = ?",
                (time.time(), job_id),
            )

    def fail_job(self, job_id, error, delay=30, permanent=False, lease_token=None):
        """Retry reads; park every uncertain remote write for explicit recovery."""
        delay = _duration(delay)
        with self._connection(write=True) as connection:
            row = self._claimed_job(connection, job_id, lease_token)
            state = "recovery" if row["state"] == "inflight" else ("failed" if permanent else "pending")
            now = time.time()
            connection.execute(
                "UPDATE jobs SET state = ?, updated_at = ?, available_at = ?, lease_until = NULL, lease_token = NULL, last_error = ? WHERE id = ?",
                (state, now, now + delay, str(error)[:1500], job_id),
            )

    def retry_job(self, job_id):
        """Retry a known failure, never an unresolved or completed remote write."""
        with self._connection(write=True) as connection:
            self._expire_leases(connection)
            row = connection.execute("SELECT state FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["state"] != "failed":
                raise Conflict("Only failed jobs can be retried; uncertain writes need recovery")
            now = time.time()
            connection.execute("UPDATE jobs SET state = 'pending', available_at = ?, updated_at = ?, last_error = '' WHERE id = ?", (now, now, job_id))
            connection.execute("DELETE FROM links WHERE kind = 'job_dismissal' AND key = ?", (str(job_id),))

    def resolve_recovery(self, job_id, outcome, actor, note, case_update=None):
        """Record a verified remote result before completing or retrying a write."""
        if outcome not in ("complete", "retry", "cancel"):
            raise ValueError("Recovery outcome must be complete, retry, or cancel")
        _text(str(actor), "Actor", 128)
        _text(note, "Recovery note", 1000)
        with self._connection(write=True) as connection:
            self._expire_leases(connection)
            row = connection.execute("SELECT state, recovery_notes FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["state"] != "recovery":
                raise Conflict("This job does not need recovery")
            if case_update is not None:
                patch = case_update["patch"]
                _json_object(patch)
                if (_MANAGED_CASE_FIELDS | _IDENTITY_FIELDS).intersection(patch):
                    raise ValueError("Case identity and audit fields are managed by the store")
                case = self._current_case(connection, case_update["case_id"], case_update["expected_revision"])
                case.update(patch)
                case["revision"] += 1
                case["updated_at"] = time.time()
                self._save_case(connection, case)
                if case_update.get("job"):
                    self._enqueue_spec(connection, case_update["job"])
            now = time.time()
            notes = json.loads(row["recovery_notes"]) + [{"actor": str(actor), "outcome": outcome, "note": note, "time": now}]
            state = {"complete": "done", "retry": "pending", "cancel": "cancelled"}[outcome]
            connection.execute(
                "UPDATE jobs SET state = ?, available_at = ?, updated_at = ?, last_error = '', recovery_notes = ? WHERE id = ?",
                (state, now, now, json.dumps(notes[-MAX_DECISIONS:], ensure_ascii=False), job_id),
            )

    def get_link(self, kind, key):
        with self._connection() as connection:
            row = connection.execute("SELECT document FROM links WHERE kind = ? AND key = ?", (kind, str(key))).fetchone()
            return json.loads(row["document"]) if row else None

    def list_links(self, kind):
        with self._connection() as connection:
            rows = connection.execute("SELECT key, document FROM links WHERE kind = ? ORDER BY key", (kind,)).fetchall()
            return [{"key": row["key"], "data": json.loads(row["document"])} for row in rows]

    def list_pr_sources(self, mapping, number, source, *, after="", limit=100):
        """Read a bounded, indexed page from one PR's observed source history."""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT key, document FROM links WHERE kind = 'pr_source' "
                "AND substr(key, 1, 64) = ? AND json_extract(document, '$.event.number') = ? "
                "AND json_extract(document, '$.event.activity_type') = ? AND key > ? ORDER BY key LIMIT ?",
                (mapping, number, source, after, _limit(limit)),
            ).fetchall()
            return [{"key": row["key"], "data": json.loads(row["document"])} for row in rows]

    def put_link(self, kind, key, data):
        _text(kind, "Link kind", 80)
        _text(str(key), "Link key")
        document = _json_object(data, MAX_LINK_BYTES)
        with self._connection(write=True) as connection:
            connection.execute(
                "INSERT INTO links(kind, key, document, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(kind, key) DO UPDATE SET document = excluded.document, updated_at = excluded.updated_at",
                (kind, str(key), document, time.time()),
            )
        return json.loads(document)

    def delete_links(self, kind, keys):
        keys = [str(key) for key in keys]
        if keys:
            with self._connection(write=True) as connection:
                connection.executemany("DELETE FROM links WHERE kind = ? AND key = ?", [(kind, key) for key in keys])

    def ensure_link(self, kind, key, data):
        """Create a durable baseline once, including concurrent webhook arrivals."""
        _text(kind, "Link kind", 80)
        _text(str(key), "Link key")
        document = _json_object(data, MAX_LINK_BYTES)
        with self._connection(write=True) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO links(kind, key, document, updated_at) VALUES (?, ?, ?, ?)",
                (kind, str(key), document, time.time()),
            )
            row = connection.execute("SELECT document FROM links WHERE kind = ? AND key = ?",
                                     (kind, str(key))).fetchone()
            return json.loads(row["document"])

    def save_links_with_jobs(self, links, jobs=()):
        """Commit source checkpoints together with their outbound work."""
        with self._connection(write=True) as connection:
            for kind, key, data in links:
                _text(kind, "Link kind", 80)
                _text(str(key), "Link key")
                document = _json_object(data, MAX_LINK_BYTES)
                connection.execute(
                    "INSERT INTO links(kind, key, document, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(kind, key) DO UPDATE SET document = excluded.document, updated_at = excluded.updated_at",
                    (kind, str(key), document, time.time()),
                )
            return [self._enqueue_spec(connection, job) for job in jobs]

    def record_status(self, key, field, timestamp=None):
        if field not in {"last_webhook_at", "last_sync_at", "last_activity_at"}:
            raise ValueError("Unknown activity status field")
        timestamp = time.time() if timestamp is None else timestamp
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
            raise ValueError("Invalid status timestamp")
        with self._connection(write=True) as connection:
            row = connection.execute("SELECT document FROM links WHERE kind = 'project_status' AND key = ?",
                                     (key,)).fetchone()
            data = json.loads(row["document"]) if row else {}
            data[field] = max(timestamp, data.get(field, 0))
            connection.execute(
                "INSERT INTO links(kind, key, document, updated_at) VALUES ('project_status', ?, ?, ?) "
                "ON CONFLICT(kind, key) DO UPDATE SET document = excluded.document, updated_at = excluded.updated_at",
                (key, _json_object(data, MAX_LINK_BYTES), time.time()),
            )

    def job_counts(self):
        self._expire_due_leases()
        with self._connection() as connection:
            # Listing the unfinished states lets jobs_ready skip every done row,
            # and only failed rows probe links for a dismissal.
            rows = connection.execute(
                "SELECT j.state, COUNT(*), COUNT(d.key) FROM jobs j "
                "LEFT JOIN links d ON j.state = 'failed' AND d.kind = 'job_dismissal' AND d.key = CAST(j.id AS TEXT) "
                "WHERE j.state IN ('pending', 'running', 'inflight', 'failed', 'recovery') GROUP BY j.state"
            ).fetchall()
        counts = {row[0]: row[1] for row in rows}
        dismissed = sum(row[2] for row in rows)
        return {"pending": sum(counts.get(state, 0) for state in ("pending", "running", "inflight")),
                "failed": counts.get("failed", 0) - dismissed, "recovery": counts.get("recovery", 0),
                "dismissed": dismissed}

    def reconciliation_live(self):
        """Report whether a sync root or any job of a sync cycle is pending, running, or inflight."""
        with self._connection() as connection:
            return connection.execute(
                "SELECT 1 FROM jobs WHERE state IN ('pending', 'running', 'inflight') "
                "AND (kind = 'sync' OR json_extract(payload, '$._sync_cycle') IS NOT NULL) LIMIT 1"
            ).fetchone() is not None

    def unfinished_sync_jobs(self, cycle_id):
        """A sync is successful only after its child deliveries complete."""
        with self._connection() as connection:
            return connection.execute(
                "SELECT 1 FROM jobs WHERE json_extract(payload, '$._sync_cycle') = ? AND state != 'done' LIMIT 1",
                (cycle_id,),
            ).fetchone() is not None

    @staticmethod
    def _collect_expired_jobs(connection, history_days, now):
        """Add the jobs retention may delete to temp.doomed, counting rows already there as gone."""
        connection.execute("CREATE TEMP TABLE IF NOT EXISTS doomed(id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TEMP TABLE IF NOT EXISTS open_cycle_jobs(id INTEGER PRIMARY KEY)")
        cycles = [row[0] for row in connection.execute(
            "SELECT json_extract(payload, '$._sync_cycle') FROM jobs WHERE state IN ('pending', 'running', 'inflight') "
            "AND id NOT IN (SELECT id FROM temp.doomed) AND json_extract(payload, '$._sync_cycle') IS NOT NULL "
            "UNION SELECT id FROM jobs WHERE state IN ('pending', 'running', 'inflight') AND kind = 'sync' "
            "AND id NOT IN (SELECT id FROM temp.doomed)"
        )]
        # complete_cycle reads the root when the last job of its cycle finishes, so a cycle
        # keeps all its finished rows, root included, while any of its jobs is live.
        for cycle in cycles:
            connection.execute("INSERT OR IGNORE INTO temp.open_cycle_jobs SELECT id FROM jobs "
                               "WHERE json_extract(payload, '$._sync_cycle') = ? AND state = 'done'", (cycle,))
            connection.execute("INSERT OR IGNORE INTO temp.open_cycle_jobs SELECT id FROM jobs "
                               "WHERE id = ? AND state = 'done'", (cycle,))
        finished, parameters = "state = 'done'", []
        if history_days:
            cutoff = now - history_days * DAY_SECONDS
            # A done row never has available_at above updated_at, so this bound only
            # lets jobs_ready skip rows that finished inside the window.
            finished += " AND available_at < ? AND updated_at < ?"
            parameters += [cutoff, cutoff]
        parameters.append(now - max(FAILED_RETENTION_DAYS, history_days) * DAY_SECONDS)
        connection.execute(
            "INSERT OR IGNORE INTO temp.doomed SELECT id FROM jobs WHERE " + finished
            + " AND id NOT IN (SELECT id FROM temp.open_cycle_jobs) "
            "UNION ALL SELECT id FROM jobs WHERE state IN ('failed', 'cancelled') AND updated_at < ?",
            parameters,
        )

    @staticmethod
    def _skip_zeroing_freed_pages(connection):
        # SQLite builds with secure_delete on, the Debian and Ubuntu default, overwrite
        # every freed page and write it to the WAL, so a bulk delete would write as many
        # bytes as it frees. FAST frees the pages without that extra write.
        connection.execute("PRAGMA secure_delete=FAST")

    @staticmethod
    def _replace_jobs(connection, kept_ids):
        """Keep only the jobs whose IDs the kept_ids query selects, inside the caller's write transaction.

        DELETE without WHERE frees whole table and index pages instead of removing each
        row and index entry, and it leaves sqlite_sequence alone, so IDs are never reused.
        """
        AutomationStore._skip_zeroing_freed_pages(connection)
        connection.execute("CREATE TEMP TABLE kept AS SELECT * FROM jobs WHERE id IN (" + kept_ids + ")")
        connection.execute("DELETE FROM jobs")
        connection.execute("INSERT INTO jobs SELECT * FROM temp.kept")
        connection.execute("DROP TABLE temp.kept")

    @staticmethod
    def _delete_doomed(connection, limit=None):
        """Delete the jobs in temp.doomed, with links keyed by their ID, and return the count.

        Past BULK_DELETE_ROWS every listed job goes in one step; otherwise only the
        lowest `limit` IDs go when a limit is given.
        """
        count = connection.execute("SELECT COUNT(*) FROM temp.doomed").fetchone()[0]
        if count > BULK_DELETE_ROWS:
            # INDEXED BY keeps the scan of surviving IDs on the small covering index
            # instead of reading every row of the table.
            AutomationStore._replace_jobs(
                connection, "SELECT id FROM jobs INDEXED BY jobs_ready WHERE id NOT IN (SELECT id FROM temp.doomed)")
        else:
            if limit is not None:
                connection.execute("DELETE FROM temp.doomed WHERE id NOT IN "
                                   "(SELECT id FROM temp.doomed ORDER BY id LIMIT ?)", (limit,))
            count = connection.execute("DELETE FROM jobs WHERE id IN (SELECT id FROM temp.doomed)").rowcount
        connection.execute("DELETE FROM links WHERE kind IN ('job_dismissal', 'pr_prepared') "
                           "AND key IN (SELECT CAST(id AS TEXT) FROM temp.doomed)")
        return count

    def prune_jobs(self, history_days=0, *, limit=PRUNE_BATCH_ROWS):
        """Delete one batch of jobs that retention allows and return how many were deleted.

        Finished jobs go once they are history_days old (at once for 0), but a reconciliation
        cycle keeps its rows while any of its jobs is live. Failed and cancelled jobs go after
        max(7, history_days) days. Pending, running, inflight and held jobs always stay. A
        backlog above BULK_DELETE_ROWS goes in one transaction instead of one batch.
        """
        history_days, limit = _days(history_days), _limit(limit)
        with self._connection(write=True) as connection:
            self._collect_expired_jobs(connection, history_days, time.time())
            return self._delete_doomed(connection, limit)

    @staticmethod
    def _keep_duplicate_guards(connection, now):
        """Write delivery markers and question notices for job rows that are about to be deleted.

        Rows written before these links existed are their own duplicate guard, so deleting
        them without the links would let a redelivery or a rerun act twice.
        """
        rows = connection.execute(
            "SELECT key, created_at FROM jobs WHERE key >= 'github:' AND key < 'github;' AND created_at >= ?",
            (now - DELIVERY_MARKER_DAYS * DAY_SECONDS,),
        ).fetchall()
        connection.executemany(
            "INSERT OR IGNORE INTO links(kind, key, document, updated_at) VALUES ('github_delivery', ?, '{}', ?)",
            [(hashlib.sha256(row["key"][len("github:"):].encode("utf-8")).hexdigest(), row["created_at"])
             for row in rows],
        )
        connection.execute(
            "INSERT OR IGNORE INTO links(kind, key, document, updated_at) SELECT 'question_notice', "
            "substr(key, 10), '{}', created_at FROM jobs WHERE key >= 'question:' AND key < 'question;'")

    @staticmethod
    def _delete_orphaned_job_links(connection):
        connection.execute(
            "DELETE FROM links WHERE kind IN ('job_dismissal', 'pr_prepared', 'pr_discovery_cycle') "
            "AND NOT EXISTS (SELECT 1 FROM jobs WHERE jobs.id = CAST(links.key AS INTEGER))")
        connection.execute(
            "DELETE FROM links WHERE kind = 'pr_discovery_page' AND NOT EXISTS (SELECT 1 FROM jobs "
            "WHERE jobs.id = CAST(substr(links.key, instr(links.key, ':') + 1) AS INTEGER))")

    def clear_history(self):
        """Delete every job except running and inflight ones, with their job links; return the count.

        Post records, receipts, feedback cases and delivery markers stay.
        """
        with self._connection(write=True) as connection:
            before = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            self._keep_duplicate_guards(connection, time.time())
            self._replace_jobs(connection, "SELECT id FROM jobs WHERE state IN ('running', 'inflight')")
            self._delete_orphaned_job_links(connection)
            return before - connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    def reset_project_data(self):
        """Delete every job, link and case and return the counts; raise Conflict while a job is in progress."""
        with self._connection(write=True) as connection:
            if connection.execute("SELECT 1 FROM jobs WHERE state IN ('running', 'inflight') LIMIT 1").fetchone():
                raise Conflict("A job is running or sending a delivery")
            self._skip_zeroing_freed_pages(connection)
            counts = {}
            for table in ("jobs", "links", "cases"):
                counts[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                connection.execute(f"DELETE FROM {table}")
            return counts

    def discard_job(self, job_id, updated_at):
        """Delete one held job and its job links if it still matches the snapshot the operator saw."""
        snapshot = _job_snapshot({"id": job_id, "updated_at": updated_at})
        with self._connection(write=True) as connection:
            row = connection.execute("SELECT state, updated_at FROM jobs WHERE id = ?", (snapshot["id"],)).fetchone()
            if row is None:
                raise KeyError(snapshot["id"])
            if row["state"] != "recovery" or row["updated_at"] != snapshot["updated_at"]:
                raise Conflict("The held job changed. Refresh activity before trying again.")
            connection.execute("DELETE FROM links WHERE kind IN ('job_dismissal', 'pr_prepared') AND key = ?",
                               (str(snapshot["id"]),))
            connection.execute("DELETE FROM jobs WHERE id = ?", (snapshot["id"],))

    def clean_up_old_queue(self, history_days=0):
        """Clear work left by overlapping sync cycles once, and return counts or None after the first run.

        Pending sync roots, PR scan jobs and sync-item refreshes go first; pending
        webhook events, pr-event deliveries and feedback jobs stay. Retention then
        applies to every remaining row. Both steps share one transaction.
        """
        history_days = _days(history_days)
        with self._connection(write=True) as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] >= QUEUE_CLEANUP_VERSION:
                return None
            self._keep_duplicate_guards(connection, time.time())
            connection.execute("CREATE TEMP TABLE IF NOT EXISTS doomed(id INTEGER PRIMARY KEY)")
            stale = connection.execute(
                "INSERT INTO temp.doomed SELECT id FROM jobs WHERE state = 'pending' AND (kind IN "
                "('sync', 'pr_sync_page', 'pr_sync_missing', 'pr_sync_check', 'pr_sync_discover') "
                "OR (kind = 'event' AND substr(key, 1, 10) = 'sync-item:'))"
            ).rowcount
            self._collect_expired_jobs(connection, history_days, time.time())
            deleted = self._delete_doomed(connection)
            connection.execute(f"PRAGMA user_version = {QUEUE_CLEANUP_VERSION}")
        return {"stale": stale, "expired": deleted - stale}

    def prune_links(self, history_days=0, *, limit=PRUNE_BATCH_ROWS):
        """Delete one batch of expired bookkeeping links and return how many were deleted.

        Delivery markers go after DELIVERY_MARKER_DAYS, discovery links once their cycle's root
        job is gone, and release, workflow and commit post records, with their attempt records,
        after max(7, history_days) days.
        """
        history_days, limit = _days(history_days), _limit(limit)
        now = time.time()
        with self._connection(write=True) as connection:
            deleted = connection.execute(
                "DELETE FROM links WHERE rowid IN (SELECT rowid FROM links WHERE kind = 'github_delivery' "
                "AND updated_at < ? LIMIT ?)", (now - DELIVERY_MARKER_DAYS * DAY_SECONDS, limit),
            ).rowcount
            for kind, cycle in (("pr_discovery_cycle", "links.key"),
                                ("pr_discovery_page", "substr(links.key, instr(links.key, ':') + 1)")):
                deleted += connection.execute(
                    "DELETE FROM links WHERE rowid IN (SELECT rowid FROM links WHERE kind = ? AND NOT EXISTS "
                    f"(SELECT 1 FROM jobs WHERE jobs.id = CAST({cycle} AS INTEGER)) LIMIT ?)",
                    (kind, limit - deleted),
                ).rowcount
            keys = [row[0] for row in connection.execute(
                "SELECT key FROM links WHERE kind = 'mirror' AND updated_at < ? "
                "AND json_extract(document, '$.kind') IN ('general', 'commit') LIMIT ?",
                (now - max(FAILED_RETENTION_DAYS, history_days) * DAY_SECONDS, (limit - deleted) // 2),
            )]
            if keys:
                deleted += connection.execute(
                    "DELETE FROM links WHERE kind IN ('mirror', 'mirror_attempt') AND key IN ("
                    + ",".join("?" * len(keys)) + ")", keys,
                ).rowcount
        return deleted

    def database_usage(self):
        """Return the database size and the bytes held by free pages, both from SQLite's page counts."""
        with self._connection() as connection:
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            pages = connection.execute("PRAGMA page_count").fetchone()[0]
            free = connection.execute("PRAGMA freelist_count").fetchone()[0]
        return {"bytes": pages * page_size, "free_bytes": free * page_size}

    def compact(self):
        """VACUUM when free pages are at least 25% of the file and 16 MB; return whether it ran.

        VACUUM rewrites the whole file, so never call this on the event loop. While
        another connection holds the write lock it returns False, and a later call
        tries again.
        """
        usage = self.database_usage()
        if (usage["free_bytes"] < usage["bytes"] * COMPACT_MIN_FREE_SHARE
                or usage["free_bytes"] < COMPACT_MIN_FREE_BYTES):
            return False
        try:
            with self._connection(timeout=1) as connection:
                # VACUUM is the only point where auto_vacuum can change on an existing
                # file. Incremental mode lets release_free_pages return later space.
                connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
                connection.execute("VACUUM")
                return True
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc):
                return False
            raise

    def release_free_pages(self):
        """Return free pages to the file system after a sweep and report how many were released.

        This does nothing until compact has switched the file to incremental auto_vacuum,
        or while another connection holds the write lock.
        """
        try:
            with self._connection(timeout=1) as connection:
                if connection.execute("PRAGMA auto_vacuum").fetchone()[0] != 2:
                    return 0
                free = connection.execute("PRAGMA freelist_count").fetchone()[0]
                # Each step of this pragma frees one page, so read every row.
                connection.execute("PRAGMA incremental_vacuum").fetchall()
                return free
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc):
                return 0
            raise
