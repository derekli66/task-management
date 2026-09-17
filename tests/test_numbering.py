"""Human task numbers, legacy data migration and competing process contracts."""
import json
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from task_management.store import TaskError, TaskStore, encode_cursor


def create_v1(path, workspace):
    """Frozen v1 fixture: independent of the implementation's current schema."""
    with sqlite3.connect(path) as db:
        db.executescript((Path(__file__).parent / "fixtures/schema_v1.sql").read_text())
        # UUID lexical order deliberately differs from actual creation order.
        for index, task_id in enumerate(("ffffffff-ffff-4fff-8fff-ffffffffffff",
                                         "11111111-1111-4111-8111-111111111111"), 1):
            initial = json.dumps(dict(title=f"Task {index}", done_when="Done",
                description="", priority="normal", dependencies=[]), sort_keys=True)
            db.execute("""INSERT INTO tasks VALUES
                (?, ?, ?, ?, ?, '', 'Done', 'normal', 'pending', NULL, NULL, NULL,
                 1, '20260918_000000', '20260918_000000')""",
                (task_id, workspace, f"request-{index}", initial, f"Task {index}"))
            db.execute("""INSERT INTO task_events VALUES
                (?, ?, ?, 1, 'create', 'old-client', NULL, NULL, NULL, ?, '20260918_000000')""",
                (index, workspace, task_id, json.dumps({"id": task_id})))
        db.execute("INSERT INTO dependencies VALUES (?, ?, ?)",
            (workspace, "11111111-1111-4111-8111-111111111111", "ffffffff-ffff-4fff-8fff-ffffffffffff"))
    return raw_data(path)


def raw_data(path):
    with sqlite3.connect(path) as db:
        return {table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in ("tasks", "dependencies", "task_events")}


def numbered_create(path, workspace, request_id, gate, results):
    gate.wait(10)
    try:
        result = TaskStore(path).create(workspace, request_id, "Race", "Done")
        results.put(("ok", result["task"]["id"], result["task"]["task_number"]))
    except Exception as exc:
        results.put((type(exc).__name__, str(exc)))


class NumberingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = str(self.root)
        self.other = self.root / "other"
        self.other.mkdir()
        self.db = self.root / "tasks.sqlite3"

    def assert_error(self, code, operation, *args, **kwargs):
        with self.assertRaises(TaskError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_numbers_are_global_stable_and_idempotent(self):
        store = TaskStore(self.db)
        first = store.create(self.workspace, "one", "One", "Done")["task"]
        self.assertEqual(first["task_number"], "#1")
        store.update(self.workspace, "#1", 1, actor="test", action="cancel", reason="Dropped")
        second = store.create(str(self.other), "two", "Two", "Done")["task"]
        self.assertEqual(second["task_number"], "#2")
        self.assertEqual(store.create(self.workspace, "one", "One", "Done")["task"]["task_number"], "#1")
        reopened = store.update(self.workspace, "#1", 2, actor="test", action="reopen", reason="Again")["task"]
        self.assertEqual(reopened["task_number"], "#1")
        self.assertEqual(TaskStore(self.db).get(self.workspace, "#1")["task"], reopened)
        self.assertEqual(store.create(self.workspace, "three", "Three", "Done")["task"]["task_number"], "#3")
        self.assertEqual({t["task_number"] for t in store.list(self.workspace)["tasks"]}, {"#1", "#3"})
        self.assert_error("not_found", store.get, str(self.other), "#1")
        self.assert_error("not_found", store.update, str(self.other), "#1", 3, actor="test", action="start")
        self.assert_error("revision_conflict", store.update, self.workspace, "#1", 1, actor="test", action="start")
        self.assert_error("invalid_input", store.update, self.workspace, "#1", 3,
                          actor="test", metadata={"task_number": "#8"})

    def test_aliases_share_event_cursors_and_uuid_dependencies(self):
        store = TaskStore(self.db)
        first = store.create(self.workspace, "one", "One", "Done")["task"]
        store.update(self.workspace, "#1", 1, actor="test", metadata={"title":"Updated"})
        cursor = store.get(self.workspace, "#1", event_limit=1)["next_event_cursor"]
        page = store.get(self.workspace, first["id"], event_limit=1, event_cursor=cursor)
        self.assertEqual(page["events"][0]["action"], "create")
        legacy_cursor = encode_cursor(["events", self.workspace, first["id"]], 2)
        self.assertEqual(store.get(self.workspace, "#1", event_cursor=legacy_cursor)["events"], page["events"])
        dependent = store.create(self.workspace, "two", "Two", "Done", dependencies=[first["id"]])["task"]
        self.assertEqual(dependent["dependencies"], [first["id"]])
        self.assert_error("dependencies_unfinished", store.update, self.workspace, "#2", 1,
                          actor="test", action="start")
        self.assert_error("not_found", store.create, self.workspace, "three", "Three", "Done", dependencies=["#1"])

    def test_invalid_number_and_overflow_are_identifiable_errors(self):
        store = TaskStore(self.db)
        for reference in ("#0", "#01", "#-1", "#1.0", "# 1", "#１", "#1\n",
                          "#9223372036854775808", "#" + "9" * 5000):
            with self.subTest(reference=reference[:40]):
                self.assert_error("invalid_input", store.get, self.workspace, reference)
                self.assert_error("invalid_input", store.update, self.workspace, reference, 1,
                                  actor="test", action="start")
        self.assert_error("not_found", store.get, self.workspace, "#9223372036854775807")

    def test_v1_backfill_preserves_raw_rows_history_revisions_and_cursors(self):
        before = create_v1(self.db, self.workspace)
        store = TaskStore(self.db)
        self.assertEqual(raw_data(self.db), before)
        self.assertIsNotNone(store.migration_backup)
        self.assertEqual(raw_data(store.migration_backup), before)
        with sqlite3.connect(store.migration_backup) as backup:
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 1)
        first = store.get(self.workspace, "#1")["task"]
        self.assertEqual(first["id"], "ffffffff-ffff-4fff-8fff-ffffffffffff")
        self.assertEqual(first["revision"], 1)
        retry = store.create(self.workspace, "request-1", "Task 1", "Done")
        self.assertEqual(retry["task"], first)
        old_cursor = encode_cursor(["events", self.workspace, first["id"]], 2)
        self.assertEqual(store.get(self.workspace, "#1", event_cursor=old_cursor)["events"][0]["revision"], 1)
        self.assertNotIn("task_number", store.get(self.workspace, "#1")["events"][0]["after"])
        listing_cursor = encode_cursor(["tasks", self.workspace, "open", None],
            ["20260918_000000", "11111111-1111-4111-8111-111111111111"])
        self.assertEqual(store.list(self.workspace, cursor=listing_cursor)["tasks"][0]["id"], first["id"])
        self.assertIsNone(TaskStore(self.db).migration_backup)
        self.assertEqual(store.create(self.workspace, "new", "New", "Done")["task"]["task_number"], "#3")

    def test_failed_migration_rolls_back_without_rewriting_v1(self):
        create_v1(self.db, self.workspace)
        with sqlite3.connect(self.db) as db:
            db.execute("DELETE FROM task_events WHERE seq=2")
        before = raw_data(self.db)
        self.assert_error("storage_error", TaskStore, self.db)
        self.assertEqual(raw_data(self.db), before)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='task_numbers'").fetchone())

    def test_backup_and_upgrade_hold_writer_lock_in_wal_mode(self):
        before = create_v1(self.db, self.workspace)
        with sqlite3.connect(self.db) as db:
            db.execute("PRAGMA journal_mode=WAL")
        original = TaskStore._backup_v1
        def checked_backup(store):
            with sqlite3.connect(self.db, timeout=0.01) as competitor:
                with self.assertRaises(sqlite3.OperationalError) as caught:
                    competitor.execute("BEGIN IMMEDIATE")
                self.assertEqual(caught.exception.sqlite_errorcode, sqlite3.SQLITE_BUSY)
            original(store)
        with patch.object(TaskStore, "_backup_v1", checked_backup):
            upgraded = TaskStore(self.db)
        self.assertEqual(raw_data(upgraded.migration_backup), before)

    def test_failed_backup_prevents_upgrade(self):
        before = create_v1(self.db, self.workspace)
        with patch.object(TaskStore, "_backup_v1", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                TaskStore(self.db)
        self.assertEqual(raw_data(self.db), before)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_event_failure_rolls_back_allocation_with_task(self):
        store = TaskStore(self.db)
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TRIGGER fail_event BEFORE INSERT ON task_events "
                       "BEGIN SELECT RAISE(ABORT, 'failure'); END")
        self.assert_error("storage_error", store.create, self.workspace, "one", "One", "Done")
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM task_numbers").fetchone()[0], 0)
            db.execute("DROP TRIGGER fail_event")
        self.assertEqual(store.create(self.workspace, "one", "One", "Done")["task"]["task_number"], "#1")

    def race(self, requests):
        ctx = multiprocessing.get_context("spawn")
        gate, results = ctx.Event(), ctx.Queue()
        processes = [ctx.Process(target=numbered_create, args=(self.db, self.workspace, key, gate, results))
                     for key in requests]
        try:
            for process in processes:
                process.start()
            gate.set()
            responses = [results.get(timeout=15) for _ in processes]
            for process in processes:
                process.join(5)
                self.assertEqual(process.exitcode, 0)
            self.assertTrue(all(result[0] == "ok" for result in responses), responses)
            return responses
        finally:
            for process in processes:
                if process.is_alive():
                    process.kill()
                process.join(5)
            results.close()
            results.join_thread()

    def test_concurrent_cold_creates_assign_distinct_numbers(self):
        responses = self.race([f"request-{index}" for index in range(6)])
        self.assertEqual({r[2] for r in responses}, {f"#{index}" for index in range(1, 7)})

    def test_concurrent_migration_and_retry_keep_one_number(self):
        create_v1(self.db, self.workspace)
        responses = self.race(["new", "new", "new"])
        self.assertEqual(len({(r[1], r[2]) for r in responses}), 1)
        self.assertEqual(responses[0][2], "#3")
        self.assertEqual(len(list(self.root.glob("*.schema1.*.sqlite3"))), 1)
