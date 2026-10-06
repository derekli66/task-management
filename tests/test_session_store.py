"""Session isolation, deferred research and unchanged task invariants."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from task_management.numbering import Numbering
from task_management.rules import TaskError
from task_management.store import TaskStore
from task_management.config import prepare_session
from task_management.locking import atomic_write


class SessionStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = str(self.root / "repo")
        Path(self.workspace).mkdir()
        subprocess.run(["git", "init", "-q", self.workspace], check=True)
        self.data = self.root / "data"
        Numbering(self.data).initialize()
        self.store = TaskStore(self.data)

    def create(self, session="one", **extra):
        return self.store.create(self.workspace, "request", "Investigate", "Regression passes",
                                 session_id=session, **extra)["task"]

    def test_two_sessions_global_numbers_and_retry(self):
        first, second = self.create(), self.create("two")
        self.assertEqual((first["task_number"], second["task_number"]), ("#1", "#2"))
        self.assertEqual(self.create()["id"], first["id"])
        self.assertEqual(len(self.store.list(self.workspace, session_id="one")["tasks"]), 1)
        with self.assertRaises(TaskError) as error:
            self.store.get(self.workspace, first["id"], session_id="two")
        self.assertEqual(error.exception.code, "not_found")
        with self.assertRaises(TaskError):
            self.create("../../escape")

    def test_copying_a_canonical_file_to_another_workspace_is_rejected(self):
        task = self.create()
        original = Path(self.workspace) / ".codex/task-management/sessions/one/tasks.md"
        other = self.root / "other"
        other.mkdir()
        path = prepare_session(str(other),"one") / "tasks.md"
        atomic_write(path,original.read_text())
        with self.assertRaises(TaskError) as caught:
            self.store.update(str(other),task["id"],1,session_id="one",actor="test",action="start")
        self.assertEqual(caught.exception.code,"storage_error")

    def test_research_and_dependency_readiness_survive_restart(self):
        notes = "#### 根因\ncache key 缺少 user ID\n\n#### 下一步\n```python\nassert fixed\n```\n"
        first = self.create(description=notes)
        after = self.store.create(self.workspace, "after", "Next", "Verified",
            session_id="one", dependencies=[first["id"]])["task"]
        self.assertFalse(after["ready"])
        for action, kwargs in (("start", {}), ("complete", {"evidence":"tests and commit verified"})):
            first = self.store.update(self.workspace, first["id"], first["revision"],
                session_id="one", actor="test", action=action, **kwargs)["task"]
        restarted = TaskStore(self.data)
        self.assertEqual(restarted.get(self.workspace, first["id"], session_id="one")["task"]["description"], notes)
        started = restarted.update(self.workspace, after["id"], 1, session_id="one", actor="test", action="start")["task"]
        self.assertTrue(started["ready"])
        with self.assertRaises(TaskError) as error:
            restarted.update(self.workspace, first["id"], first["revision"], session_id="one",
                             actor="test", action="reopen", reason="Changed")
        self.assertEqual(error.exception.code, "active_dependents")

    def test_ignore_before_writes_and_view_failure_is_committed_success(self):
        with patch.object(self.store, "write_views", side_effect=OSError("view failed")):
            result = self.store.create(self.workspace, "request", "Title", "Done", session_id="one")
        self.assertIn("warnings", result)
        task = result["task"]
        self.assertEqual(self.store.get(self.workspace, task["id"], session_id="one")["task"], task)
        status = subprocess.run(["git", "-C", self.workspace, "status", "--porcelain"],
                                check=True, capture_output=True, text=True)
        self.assertEqual(status.stdout, "")
        retry = self.store.create(self.workspace, "request", "Title", "Done", session_id="one")
        self.assertEqual(retry["task"]["id"], task["id"])

    def test_stale_revision_and_external_edits_preserve_file(self):
        task = self.create(description="original")
        changed = self.store.update(self.workspace, task["id"], 1, session_id="one",
                                   actor="test", metadata={"description":"new research"})["task"]
        with self.assertRaises(TaskError) as error:
            self.store.update(self.workspace, task["id"], 1, session_id="one", actor="test", action="start")
        self.assertEqual(error.exception.code, "revision_conflict")
        path = Path(self.workspace) / ".codex/task-management/sessions/one/tasks.md"
        path.write_text(path.read_text().replace("new research", "bad research"))
        before = path.read_bytes()
        with self.assertRaises(TaskError):
            self.store.get(self.workspace, changed["id"], session_id="one")
        self.assertEqual(path.read_bytes(), before)

    def test_view_repair_does_not_change_revisions_and_is_not_truncated(self):
        for n in range(15):
            self.store.create(self.workspace,str(n),"Title "+str(n)+"x"*95,"Done",session_id="one")
        path = Path(self.workspace) / ".codex/task-management/sessions/one"
        before = (path / "tasks.md").read_bytes()
        (path / "index.md").unlink()
        self.store.refresh_views(self.workspace,"one")
        self.assertIn("#15",(path / "index.md").read_text())
        self.assertEqual((path / "tasks.md").read_bytes(),before)
