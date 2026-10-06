"""A stopped legacy process cannot resume writing after the guard closes."""
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing

from task_management.legacy_store import TaskStore as LegacyStore
from task_management.numbering import Numbering
from task_management.rules import TaskError
from task_management.cutover import initialize_fresh, cutover_legacy
from task_management.legacy import import_workspace
from task_management.store import TaskStore


class CutoverTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.db, self.data = self.root / "tasks.sqlite3", self.root / "data"
        LegacyStore(self.db).create(str(self.root), "old", "Old", "Done")

    def test_refuses_without_stopped_writers_or_with_open_descriptor(self):
        with self.assertRaises(TaskError):
            cutover_legacy(self.data,self.db,writers_stopped=False)
        with open(self.db,"rb"):
            with self.assertRaises(TaskError):
                cutover_legacy(self.data,self.db,writers_stopped=True)
        self.assertFalse((self.data / "activation.md").exists())

    @unittest.skipIf(os.geteuid()==0, "permission fence must be verified as an unprivileged user")
    def test_fence_blocks_new_legacy_process_and_imports_from_backup(self):
        source = cutover_legacy(self.data,self.db,writers_stopped=True)
        self.assertEqual(Numbering(self.data).state()["max_reserved_number"],1)
        self.assertTrue(Path(source["snapshot_path"]).exists())
        self.assertEqual(self.db.stat().st_mode & 0o222,0)
        code = "from task_management.legacy_store import TaskStore; TaskStore(__import__('sys').argv[1]).create(__import__('sys').argv[2], 'new', 'Forbidden', 'Done')"
        result = subprocess.run([sys.executable,"-c",code,str(self.db),str(self.root)],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("readonly",result.stderr.lower())
        self.assertNotIn("database_busy",result.stderr)
        self.db.rename(self.root / "preserved-original.sqlite3")
        import_workspace(self.data,self.db,str(self.root),"live-session")
        task = TaskStore(self.data).create(str(self.root),"new","New","Done",session_id="live-session")["task"]
        self.assertEqual(task["task_number"],"#2")

    def test_fresh_install_is_explicit_and_refuses_an_existing_legacy_db(self):
        with self.assertRaises(TaskError):
            initialize_fresh(self.data,self.db)
        self.assertFalse(self.data.exists())
        initialize_fresh(self.data,self.root / "absent.sqlite3")
        self.assertEqual(Numbering(self.data).reserve()[0],"#1")

    def test_cutover_seeds_sqlite_high_water_not_just_existing_numbers(self):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("UPDATE sqlite_sequence SET seq=99 WHERE name='task_numbers'")
        source = cutover_legacy(self.data,self.db,writers_stopped=True)
        self.assertEqual(source["high_water"],99)
        self.assertEqual(Numbering(self.data).reserve()[0],"#100")
