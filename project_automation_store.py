"""Durable cases, human decisions, and an outbox for project automation.

Each operation owns a short SQLite transaction, so Flask threads and the Discord
event loop can use the same store. No transaction remains open across network
work. A worker must mark a job inflight before a remote write: abandoned writes
then require reconciliation instead of being sent again after a restart.
"""

from contextlib import contextmanager
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
                CREATE TABLE IF NOT EXISTS links (
                    kind TEXT NOT NULL,
                    key TEXT NOT NULL,
                    document TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(kind, key)
                );
            """)

    @contextmanager
    def _connection(self, write=False):
        connection = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
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

    @staticmethod
    def _job(row):
        if row is None:
            return None
        job = dict(row)
        job["payload"] = json.loads(job["payload"])
        job["recovery_notes"] = json.loads(job["recovery_notes"])
        return job

    def get_job(self, job_id):
        with self._connection(write=True) as connection:
            self._expire_leases(connection)
            return self._job(connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())

    def list_jobs(self, limit=100):
        with self._connection(write=True) as connection:
            self._expire_leases(connection)
            rows = connection.execute("SELECT * FROM jobs ORDER BY updated_at DESC, id DESC LIMIT ?", (_limit(limit),)).fetchall()
            return [self._job(row) for row in rows]

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
