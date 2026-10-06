"""Migration is explicit, read-only and recoverable across two-file commits."""
from pathlib import Path
import sqlite3
import multiprocessing
import time
import tempfile
import unittest

from task_management.legacy_store import TaskStore as LegacyStore
from task_management.rules import TaskError
from task_management.store import TaskStore
from task_management.numbering import Numbering
from task_management.legacy import read_snapshot, import_workspace, preview
from tests.test_numbering import create_v1


def interrupted_import(data,db,workspace,session,ready):
    def hold(stage):
        if stage == "reserved":
            ready.send("reserved")
            time.sleep(30)
    import_workspace(data,db,workspace,session,fault=hold)


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = str(self.root)
        self.db = self.root / "old.sqlite3"
        create_v1(self.db, self.workspace)
        LegacyStore(self.db)  # Upgrade fixture once, before the read-only exporter.
        self.reader = sqlite3.connect(self.db)
        self.reader.execute("PRAGMA user_version")
        self.addCleanup(self.reader.close)
        self.data = self.root / "data"
        snapshot = read_snapshot(self.db)
        self.source = dict(source_id="fixture-source", path=str(self.db),
            snapshot_digest=snapshot["digest"], high_water=snapshot["high_water"])
        Numbering(self.data).initialize(baseline=snapshot["high_water"], source=self.source)

    def test_sparse_events_numbers_notes_and_retry_after_legal_updates(self):
        before = self.db.read_bytes()
        result = import_workspace(self.data, self.db, self.workspace, "real-session")
        self.assertEqual(result["task_count"], 2)
        self.assertEqual(self.db.read_bytes(), before)
        store = TaskStore(self.data)
        task = store.get(self.workspace, "#1", session_id="real-session")
        self.assertNotIn("task_number", task["events"][0]["after"])
        self.assertEqual(task["events"][0]["after"], {"id":task["task"]["id"]})
        store.update(self.workspace, "#1", 1, session_id="real-session", actor="test",
                     metadata={"description":"## 解法\n保留研究與下一步"})
        self.assertTrue(import_workspace(self.data, self.db, self.workspace, "real-session")["reused"])
        self.assertEqual(store.get(self.workspace, "#1", session_id="real-session")["task"]["revision"], 2)
        with self.assertRaises(TaskError):
            import_workspace(self.data, self.db, self.workspace, "another-session")

    def test_pending_import_can_only_resume_in_reserved_destination(self):
        for stage in ("reserved", "canonical", "finalized"):
            with self.subTest(stage=stage):
                # Each stage uses a separate numbering namespace and target session.
                data = self.root / stage
                Numbering(data).initialize(baseline=2, source=self.source)
                def fault(point):
                    if point == stage:
                        raise RuntimeError("simulated interruption")
                with self.assertRaises(RuntimeError):
                    import_workspace(data, self.db, self.workspace, stage, fault=fault)
                if stage != "finalized":
                    with self.assertRaises(TaskError) as caught:
                        TaskStore(data).create(self.workspace, "new", "New", "Done", session_id=stage)
                    self.assertEqual(caught.exception.code, "migration_in_progress")
                with self.assertRaises(TaskError):
                    import_workspace(data, self.db, self.workspace, stage+"-other")
                result = import_workspace(data, self.db, self.workspace, stage)
                self.assertEqual(result["task_count"], 2)

    def test_schema_one_is_rejected_without_upgrading(self):
        path = self.root / "v1.sqlite3"
        create_v1(path, self.workspace)
        before = path.read_bytes()
        with self.assertRaises(TaskError) as caught:
            read_snapshot(path)
        self.assertEqual(caught.exception.code, "unsupported_schema")
        self.assertEqual(path.read_bytes(), before)

    def test_preview_counts_and_missing_committed_document_never_reset(self):
        self.assertEqual(preview(self.db)["workspaces"][0]["task_count"],2)
        result = import_workspace(self.data,self.db,self.workspace,"real-session")
        Path(result["path"]).unlink()
        with self.assertRaises(TaskError) as caught:
            TaskStore(self.data).create(self.workspace,"new","New","Done",session_id="real-session")
        self.assertEqual(caught.exception.code,"storage_error")
        self.assertFalse(Path(result["path"]).exists())

    def test_competing_destinations_and_killed_import_recover_one_document(self):
        # Use a stable DELETE-mode backup so a read-only child needs no WAL sidecar creation.
        snapshot_path = self.root / "snapshot.sqlite3"
        with sqlite3.connect(snapshot_path) as backup:
            self.reader.backup(backup)
            backup.execute("PRAGMA journal_mode=DELETE")
        snapshot_path.chmod(0o400)
        source = {**self.source,"snapshot_path":str(snapshot_path)}
        data = self.root / "race-data"
        Numbering(data).initialize(baseline=2,source=source)
        context = multiprocessing.get_context("spawn")
        ready, signal = context.Pipe(False)
        process = context.Process(target=interrupted_import,args=(data,self.db,self.workspace,"reserved-session",signal))
        process.start()
        try:
            self.assertTrue(ready.poll(10))
            ready.recv()
            # Another destination has its own session lock, but cannot bypass the global reservation.
            with self.assertRaises(TaskError) as caught:
                import_workspace(data,self.db,self.workspace,"competing-session")
            self.assertEqual(caught.exception.code,"database_busy")
            process.terminate();process.join(5)
            with self.assertRaises(TaskError) as caught:
                import_workspace(data,self.db,self.workspace,"competing-session")
            self.assertEqual(caught.exception.code,"migration_conflict")
            result = import_workspace(data,self.db,self.workspace,"reserved-session")
            self.assertEqual(result["task_count"],2)
            self.assertEqual(len(list((self.root / ".codex/task-management/sessions").glob("*/tasks.md"))),1)
        finally:
            if process.is_alive():
                process.terminate();process.join()
            ready.close();signal.close()

    def test_changed_source_and_nonempty_destination_are_rejected(self):
        TaskStore(self.data).create(self.workspace, "new", "New", "Done", session_id="occupied")
        with self.assertRaises(TaskError):
            import_workspace(self.data, self.db, self.workspace, "occupied")
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE tasks SET description='Changed'")
        with self.assertRaises(TaskError):
            import_workspace(self.data, self.db, self.workspace, "empty")
