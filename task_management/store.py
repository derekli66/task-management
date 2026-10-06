"""Transactional task rules for one local user and multiple cooperating callers."""
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import uuid

from task_management.rules import (
    STATUSES, PRIORITIES, TERMINAL, TRANSITIONS, TaskError, nonblank,
    workspace_key, now, encode_cursor, decode_cursor, page_limit,
    metadata_changes, action_changes,
)


SCHEMA = (
    """CREATE TABLE tasks (
        id TEXT PRIMARY KEY, workspace TEXT NOT NULL, request_id TEXT NOT NULL,
        initial_payload TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
        done_when TEXT NOT NULL, priority TEXT NOT NULL CHECK(priority IN ('low','normal','high')),
        status TEXT NOT NULL CHECK(status IN ('pending','in_progress','blocked','completed','cancelled')),
        owner TEXT, block_reason TEXT, completion_evidence TEXT,
        revision INTEGER NOT NULL CHECK(revision > 0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(workspace, id), UNIQUE(workspace, request_id))""",
    """CREATE TABLE dependencies (
        workspace TEXT NOT NULL, task_id TEXT NOT NULL, depends_on TEXT NOT NULL,
        PRIMARY KEY(workspace, task_id, depends_on), CHECK(task_id != depends_on),
        FOREIGN KEY(workspace, task_id) REFERENCES tasks(workspace, id),
        FOREIGN KEY(workspace, depends_on) REFERENCES tasks(workspace, id))""",
    """CREATE TABLE task_events (
        seq INTEGER PRIMARY KEY, workspace TEXT NOT NULL, task_id TEXT NOT NULL,
        revision INTEGER NOT NULL, action TEXT NOT NULL, actor TEXT NOT NULL,
        reason TEXT, evidence TEXT, before_json TEXT, after_json TEXT NOT NULL, created_at TEXT NOT NULL,
        FOREIGN KEY(workspace, task_id) REFERENCES tasks(workspace, id), UNIQUE(task_id, revision))""",
    "CREATE INDEX task_listing ON tasks(workspace, created_at, id)",
    "CREATE INDEX reverse_dependencies ON dependencies(workspace, depends_on)",
    "CREATE INDEX event_history ON task_events(workspace, task_id, seq)",
)


class TaskStore:
    def __init__(self, db_path, timeout=3.0):
        self.db_path = Path(db_path)
        self.timeout = timeout
        self.migration_backup = None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self):
        db = None
        try:
            db = sqlite3.connect(self.db_path, timeout=self.timeout, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            yield db
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            if primary in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise TaskError("database_busy", "Database is busy; read current state before retrying") from exc
            raise TaskError("storage_error", str(exc)) from exc
        finally:
            if db is not None:
                db.close()  # An uncommitted transaction is rolled back, including its events.

    @staticmethod
    def _check_schema(db):
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version != 2:
            raise TaskError("unsupported_schema", f"Expected schema 2; found {version}; reload the task server")

    def _backup_v1(self):
        # The caller holds BEGIN IMMEDIATE, preventing an acknowledged write
        # from landing between this consistent snapshot and the migration.
        descriptor, path = tempfile.mkstemp(prefix=f"{self.db_path.name}.schema1.{now()}.",
                                           suffix=".sqlite3", dir=self.db_path.parent)
        os.close(descriptor)
        with self._connection() as source, closing(sqlite3.connect(path)) as backup:
            source.backup(backup)
        self.migration_backup = Path(path)

    @staticmethod
    def _add_numbers(db):
        # A separate mapping leaves UUID relationships and historical JSON intact.
        # AUTOINCREMENT does not reuse a previously committed number.
        db.execute("""CREATE TABLE task_numbers (
            number INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id))""")
        db.execute("""INSERT INTO task_numbers (task_id)
            SELECT t.id FROM tasks t JOIN task_events e ON e.task_id=t.id
            WHERE e.revision=1 AND e.action='create' ORDER BY e.seq""")
        counts = db.execute("""SELECT (SELECT count(*) FROM tasks),
                                      (SELECT count(*) FROM task_numbers)""").fetchone()
        if counts[0] != counts[1]:
            raise TaskError("storage_error", "Cannot number tasks with missing creation events")
        db.execute("PRAGMA user_version=2")

    def _initialize(self):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if tables:
                    raise TaskError("unsupported_schema", "Unversioned nonempty database will not be adopted")
                # executescript commits implicitly; individual statements keep initialization atomic.
                for statement in SCHEMA:
                    db.execute(statement)
                self._add_numbers(db)
            elif version == 1:
                self._backup_v1()
                self._add_numbers(db)
            else:
                self._check_schema(db)
            db.commit()
            # Changing journal mode can race a second first-time opener. Retry only this
            # idempotent setup operation, within the same finite connection timeout.
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    db.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if (getattr(exc, "sqlite_errorcode", 0) & 0xFF) != sqlite3.SQLITE_BUSY or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.01)

    @contextmanager
    def _transaction(self, write=False):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            self._check_schema(db)
            yield db
            db.commit()

    def _snapshot(self, db, workspace, task_id):
        row = db.execute("""SELECT t.*, n.number FROM tasks t
            JOIN task_numbers n ON n.task_id=t.id WHERE t.workspace=? AND t.id=?""",
            (workspace, task_id)).fetchone()
        if row is None:
            raise TaskError("not_found", "Task not found in this workspace")
        task = dict(row)
        task["task_number"] = f"#{task.pop('number')}"
        task.pop("initial_payload")
        deps = db.execute("""SELECT t.id, t.status FROM dependencies d JOIN tasks t
            ON t.workspace=d.workspace AND t.id=d.depends_on
            WHERE d.workspace=? AND d.task_id=? ORDER BY t.id""", (workspace, task_id)).fetchall()
        task["dependencies"] = [dep["id"] for dep in deps]
        task["unfinished_dependencies"] = [dep["id"] for dep in deps if dep["status"] != "completed"]
        task["ready"] = not task["unfinished_dependencies"]
        return task

    @staticmethod
    def _resolve_task_id(db, workspace, reference):
        nonblank(reference, "task_id")
        if not reference.startswith("#"):
            return reference  # Preserve the original UUID API and not_found behavior.
        digits = reference[1:]
        if (len(digits) > 19 or not re.fullmatch(r"[1-9][0-9]*", digits)
                or int(digits) > 9223372036854775807):
            raise TaskError("invalid_input", "Task number must be # followed by a positive 64-bit integer")
        row = db.execute("""SELECT t.id FROM task_numbers n JOIN tasks t ON t.id=n.task_id
            WHERE n.number=? AND t.workspace=?""", (int(digits), workspace)).fetchone()
        if row is None:
            raise TaskError("not_found", "Task not found in this workspace")
        return row["id"]

    def _validate_dependencies(self, db, workspace, task_id, dependencies):
        if not isinstance(dependencies, list) or any(not isinstance(x, str) or not x.strip() for x in dependencies):
            raise TaskError("invalid_input", "dependencies must be a list of task IDs")
        if len(set(dependencies)) != len(dependencies):
            raise TaskError("invalid_input", "dependencies must not contain duplicates")
        for dep in dependencies:
            if dep == task_id:
                raise TaskError("dependency_cycle", "A task cannot depend on itself")
            self._snapshot(db, workspace, dep)
            reaches = db.execute("""WITH RECURSIVE ancestors(id) AS (
                SELECT ? UNION SELECT d.depends_on FROM dependencies d JOIN ancestors a ON d.task_id=a.id
                WHERE d.workspace=?) SELECT 1 FROM ancestors WHERE id=? LIMIT 1""",
                (dep, workspace, task_id)).fetchone()
            if reaches:
                raise TaskError("dependency_cycle", "This dependency would introduce a cycle")

    @staticmethod
    def _set_dependencies(db, workspace, task_id, dependencies):
        db.execute("DELETE FROM dependencies WHERE workspace=? AND task_id=?", (workspace, task_id))
        db.executemany("INSERT INTO dependencies VALUES (?, ?, ?)",
                       [(workspace, task_id, dep) for dep in dependencies])

    @staticmethod
    def _event(db, before, after, action, actor, reason=None, evidence=None):
        db.execute("""INSERT INTO task_events
            (workspace, task_id, revision, action, actor, reason, evidence, before_json, after_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (after["workspace"], after["id"], after["revision"], action, actor, reason, evidence,
             json.dumps(before, ensure_ascii=False) if before else None,
             json.dumps(after, ensure_ascii=False), after["updated_at"]))

    def create(self, workspace, request_id, title, done_when, description="", priority="normal",
               dependencies=None, actor="unspecified"):
        workspace = workspace_key(workspace)
        for name, value in (("request_id", request_id), ("title", title), ("done_when", done_when), ("actor", actor)):
            nonblank(value, name)
        if not isinstance(description, str) or priority not in PRIORITIES:
            raise TaskError("invalid_input", "Invalid description or priority")
        dependencies = [] if dependencies is None else dependencies
        if not isinstance(dependencies, list) or any(not isinstance(dep, str) for dep in dependencies):
            raise TaskError("invalid_input", "dependencies must be a list of task IDs")
        payload = json.dumps(dict(title=title, done_when=done_when, description=description,
                                  priority=priority, dependencies=sorted(dependencies)), sort_keys=True)
        with self._transaction(write=True) as db:
            previous = db.execute("SELECT id, initial_payload FROM tasks WHERE workspace=? AND request_id=?",
                                  (workspace, request_id)).fetchone()
            if previous:
                if previous["initial_payload"] != payload:
                    raise TaskError("idempotency_conflict", "request_id was already used for different content")
                return {"task": self._snapshot(db, workspace, previous["id"]), "reused": True}
            task_id, timestamp = str(uuid.uuid4()), now()
            self._validate_dependencies(db, workspace, task_id, dependencies)
            db.execute("""INSERT INTO tasks (id, workspace, request_id, initial_payload, title,
                description, done_when, priority, status, revision, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 1, ?, ?)""",
                (task_id, workspace, request_id, payload, title, description, done_when, priority, timestamp, timestamp))
            db.execute("INSERT INTO task_numbers (task_id) VALUES (?)", (task_id,))
            self._set_dependencies(db, workspace, task_id, dependencies)
            task = self._snapshot(db, workspace, task_id)
            self._event(db, None, task, "create", actor)
            return {"task": task, "reused": False}

    def update(self, workspace, task_id, expected_revision, *, actor, metadata=None,
               action=None, reason=None, evidence=None):
        workspace = workspace_key(workspace)
        nonblank(actor, "actor")
        if type(expected_revision) is not int or expected_revision < 1:
            raise TaskError("invalid_input", "expected_revision must be a positive integer")
        if (metadata is None) == (action is None):
            raise TaskError("invalid_input", "Provide either metadata or action, never both")
        with self._transaction(write=True) as db:
            task_id = self._resolve_task_id(db, workspace, task_id)
            before = self._snapshot(db, workspace, task_id)
            if before["revision"] != expected_revision:
                raise TaskError("revision_conflict", "Task changed; read it again before deciding",
                                current_revision=before["revision"])
            if metadata is not None:
                if reason is not None or evidence is not None:
                    raise TaskError("invalid_input", "Metadata edits cannot contain action arguments")
                changes = self._metadata_changes(db, before, metadata)
            else:
                changes = self._action_changes(db, before, action, actor, reason, evidence)
            dependencies = changes.pop("dependencies", None)
            changes.update(revision=expected_revision + 1, updated_at=now())
            # Keys are validated internally, never taken as SQL identifiers from the caller.
            assignment = ", ".join(f"{key}=?" for key in changes)
            db.execute(f"UPDATE tasks SET {assignment} WHERE workspace=? AND id=?",
                       (*changes.values(), workspace, task_id))
            if dependencies is not None:
                self._set_dependencies(db, workspace, task_id, dependencies)
            after = self._snapshot(db, workspace, task_id)
            self._event(db, before, after, action or "edit", actor, reason, evidence)
            return {"task": after}

    def _metadata_changes(self, db, task, metadata):
        return metadata_changes(task, metadata, lambda value:
            self._validate_dependencies(db, task["workspace"], task["id"], value))

    @staticmethod
    def _action_changes(db, task, action, actor, reason, evidence):
        def active_dependents():
            rows = db.execute("""SELECT t.id FROM dependencies d JOIN tasks t
                ON t.workspace=d.workspace AND t.id=d.task_id WHERE d.workspace=? AND d.depends_on=?
                AND t.status IN ('in_progress','blocked','completed') ORDER BY t.id""",
                (task["workspace"], task["id"])).fetchall()
            return [row[0] for row in rows]
        return action_changes(task, action, actor, reason, evidence, active_dependents)

    def list(self, workspace, status="open", priority=None, limit=20, cursor=None):
        workspace = workspace_key(workspace)
        page_limit(limit)
        if status not in STATUSES | {"open", "all"} or (priority is not None and priority not in PRIORITIES):
            raise TaskError("invalid_input", "Unknown status or priority filter")
        scope = ["tasks", workspace, status, priority]
        filter_where, filter_params = ["workspace=?"], [workspace]
        if status == "open":
            filter_where.append("status NOT IN ('completed','cancelled')")
        elif status != "all":
            filter_where.append("status=?")
            filter_params.append(status)
        if priority is not None:
            filter_where.append("priority=?")
            filter_params.append(priority)
        where, params = list(filter_where), list(filter_params)
        if cursor is not None:
            position = decode_cursor(cursor, scope)
            if not isinstance(position, list) or len(position) != 2 or not all(isinstance(x, str) for x in position):
                raise TaskError("invalid_cursor", "Invalid task position")
            where.append("(created_at, id) > (?, ?)")
            params.extend(position)
        with self._transaction() as db:
            count_rows = db.execute(
                f"SELECT status, count(*) AS count FROM tasks "
                f"WHERE {' AND '.join(filter_where)} GROUP BY status",
                filter_params).fetchall()
            by_status = {name: 0 for name in
                         ("pending", "in_progress", "blocked", "completed", "cancelled")}
            for row in count_rows:
                by_status[row["status"]] = row["count"]
            summary = {"matching_total": sum(by_status.values()), "by_status": by_status}
            rows = db.execute(f"SELECT id, created_at FROM tasks WHERE {' AND '.join(where)} "
                              "ORDER BY created_at, id LIMIT ?", (*params, limit + 1)).fetchall()
            page = rows[:limit]
            tasks = [self._snapshot(db, workspace, row["id"]) for row in page]
            summary_keys = {"id", "task_number", "title", "status", "priority", "owner", "revision", "dependencies",
                            "ready", "unfinished_dependencies", "created_at", "updated_at", "workspace"}
            return {"tasks": [{key: value for key, value in task.items() if key in summary_keys} for task in tasks],
                    "summary": summary,
                    "next_cursor": encode_cursor(scope, [page[-1]["created_at"], page[-1]["id"]])
                    if len(rows) > limit else None}

    def get(self, workspace, task_id, event_limit=20, event_cursor=None):
        workspace = workspace_key(workspace)
        page_limit(event_limit)
        with self._transaction() as db:
            task_id = self._resolve_task_id(db, workspace, task_id)
            scope = ["events", workspace, task_id]
            upper = 9223372036854775807
            if event_cursor is not None:
                upper = decode_cursor(event_cursor, scope)
                if type(upper) is not int or not 0 < upper <= 9223372036854775807:
                    raise TaskError("invalid_cursor", "Invalid event position")
            task = self._snapshot(db, workspace, task_id)
            rows = db.execute("""SELECT * FROM task_events WHERE workspace=? AND task_id=? AND seq<?
                ORDER BY seq DESC LIMIT ?""", (workspace, task_id, upper, event_limit + 1)).fetchall()
            events = []
            for row in rows[:event_limit]:
                event = dict(row)
                before = event.pop("before_json")
                event["before"] = json.loads(before) if before else None
                event["after"] = json.loads(event.pop("after_json"))
                events.append(event)
            return {"task": task, "events": events,
                    "next_event_cursor": encode_cursor(scope, events[-1]["seq"]) if len(rows) > event_limit else None}
