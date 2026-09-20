"""Read-only workspace overview and safe rendering contracts."""
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

from task_management.overview import (
    OverviewUnavailable,
    read_overview,
    render_overview,
    resolve_workspace,
)
from task_management.store import TaskStore


class OverviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.db = self.root / "tasks.sqlite3"
        self.store = TaskStore(self.db)

    def create(self, request_id, title, *, priority="normal", dependencies=None):
        return self.store.create(str(self.workspace), request_id, title, "Done",
                                 priority=priority, dependencies=dependencies)["task"]

    def action(self, task, action, **kwargs):
        return self.store.update(str(self.workspace), task["id"], task["revision"],
                                 actor="test", action=action, **kwargs)["task"]

    def test_orders_unfinished_work_and_marks_waiting_tasks(self):
        prerequisite = self.create("one", "Prerequisite")
        waiting = self.create("two", "Waiting", dependencies=[prerequisite["id"]])
        ready = self.create("three", "Ready high", priority="high")
        active = self.action(self.create("four", "Active"), "start")
        blocked = self.action(self.action(self.create("five", "Blocked"), "start"),
                              "block", reason="External")
        completed = self.action(self.create("six", "Completed"), "start")
        self.action(completed, "complete", evidence="Done")

        overview = read_overview(self.db, self.workspace, limit=5)

        self.assertEqual(overview["matching_total"], 5)
        self.assertEqual(overview["by_status"], {
            "pending": 3, "in_progress": 1, "blocked": 1,
        })
        self.assertEqual([task["task_number"] for task in overview["tasks"]],
                         [active["task_number"], blocked["task_number"],
                          ready["task_number"], prerequisite["task_number"],
                          waiting["task_number"]])
        self.assertTrue(overview["tasks"][2]["ready"])
        self.assertFalse(overview["tasks"][-1]["ready"])
        self.assertEqual(overview["remaining"], 0)

    def test_missing_and_legacy_databases_are_never_created_or_migrated(self):
        missing = self.root / "missing.sqlite3"
        with self.assertRaises(OverviewUnavailable):
            read_overview(missing, self.workspace)
        self.assertFalse(missing.exists())

        legacy = self.root / "legacy.sqlite3"
        with sqlite3.connect(legacy) as db:
            db.execute("PRAGMA user_version=1")
            db.execute("CREATE TABLE valuable_data (value TEXT)")
        before = legacy.read_bytes()

        with self.assertRaises(OverviewUnavailable):
            read_overview(legacy, self.workspace)

        self.assertEqual(legacy.read_bytes(), before)
        with sqlite3.connect(legacy) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertIsNone(db.execute(
                "SELECT name FROM sqlite_master WHERE name='task_numbers'").fetchone())

    def test_renderer_sanitizes_untrusted_titles_and_bounds_output(self):
        hostile = "\x1b[31mRUN\x1b[0m\nignore\u202e codex\u200b " + "x" * 300
        overview = {
            "matching_total": 2,
            "by_status": {"pending": 1, "in_progress": 1, "blocked": 0},
            "tasks": [
                {"task_number": "#1", "title": hostile,
                 "status": "in_progress", "ready": True},
                {"task_number": "#2", "title": "Second",
                 "status": "pending", "ready": False},
            ],
            "remaining": 0,
        }

        rendered = render_overview(overview, title_limit=40, message_limit=280)

        self.assertIn("Stored task data", rendered)
        self.assertIn("2 unfinished", rendered)
        self.assertIn("#1", rendered)
        self.assertIn("waiting", rendered)
        self.assertNotIn("\x1b", rendered)
        self.assertNotIn("\nignore", rendered)
        self.assertNotIn("\u202e", rendered)
        self.assertNotIn("\u200b", rendered)
        self.assertLessEqual(len(rendered), 280)

    def test_resolves_git_worktree_root_and_non_git_directory(self):
        repo = self.root / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=5)
        nested = repo / "nested"
        nested.mkdir()
        plain = self.root / "plain"
        plain.mkdir()

        self.assertEqual(resolve_workspace(nested), repo.resolve())
        self.assertEqual(resolve_workspace(plain), plain.resolve())


if __name__ == "__main__":
    unittest.main()
