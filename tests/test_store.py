"""Behavioral contracts for persistence, state changes and workspace boundaries."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

from task_management.store import TaskError, TaskStore


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = str(self.root / "project")
        Path(self.workspace).mkdir()
        self.other = str(self.root / "other")
        Path(self.other).mkdir()
        self.db = self.root / "tasks.sqlite3"
        self.store = TaskStore(self.db)

    def create(self, **kwargs):
        args = dict(workspace=self.workspace, request_id=str(uuid.uuid4()),
                    title="Build feature", done_when="Tests pass", actor="session-a")
        args.update(kwargs)
        return self.store.create(**args)["task"]

    def action(self, task, action, **kwargs):
        return self.store.update(self.workspace, task["id"], task["revision"],
            actor="session-a", action=action, **kwargs)["task"]

    def error(self, code, fn, *args, **kwargs):
        with self.assertRaises(TaskError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_restart_reads_task_and_atomic_creation_history(self):
        task = self.create(title="中文任務")
        result = TaskStore(self.db).get(self.workspace, task["id"])
        self.assertEqual(result["task"], task)
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["revision"], 1)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["after"]["title"], "中文任務")
        self.assertRegex(task["created_at"], r"^\d{8}_\d{6}$")

    def test_create_retry_is_idempotent_but_reusing_key_for_new_content_fails(self):
        task = self.create(request_id="stable-request")
        started = self.action(task, "start")
        retry = self.store.create(self.workspace, "stable-request", "Build feature", "Tests pass")
        self.assertEqual(retry["task"], started)
        self.assertTrue(retry["reused"])
        self.error("idempotency_conflict", self.create, request_id="stable-request", title="Changed")
        self.assertEqual(len(self.store.list(self.workspace)["tasks"]), 1)
        self.assertNotEqual(self.create(workspace=self.other, request_id="stable-request")["id"], task["id"])

    def test_workspace_isolation_on_every_operation_and_symlink_alias(self):
        task = self.create()
        alias = self.root / "alias"
        alias.symlink_to(self.workspace, target_is_directory=True)
        self.assertEqual(self.store.get(str(alias), task["id"])["task"], task)
        self.assertEqual(self.store.list(self.other)["tasks"], [])
        self.error("not_found", self.store.get, self.other, task["id"])
        self.error("not_found", self.store.update, self.other, task["id"], 1,
                   actor="other", action="start")
        self.error("invalid_workspace", self.store.list, "relative/path")
        self.error("invalid_workspace", self.store.list, str(self.root / "missing"))
        self.error("invalid_workspace", self.store.list, str(self.db))

    def test_full_lifecycle_and_recovery_preserve_evidence_history(self):
        task = self.action(self.create(), "start")
        self.assertEqual(task["owner"], "session-a")
        task = self.action(task, "block", reason="Waiting for input")
        self.assertEqual(task["owner"], "session-a")
        task = self.store.update(self.workspace, task["id"], task["revision"],
            actor="session-b", action="release", reason="Previous session stopped")["task"]
        self.assertIsNone(task["owner"])
        self.assertIsNone(task["block_reason"])
        task = self.action(task, "start")
        task = self.action(task, "complete", evidence="tests passed; commit abc123")
        self.assertEqual(task["status"], "completed")
        task = self.action(task, "reopen", reason="New acceptance condition")
        self.assertIsNone(task["completion_evidence"])
        task = self.action(task, "cancel", reason="No longer needed")
        self.assertEqual(task["status"], "cancelled")
        task = self.action(task, "reopen", reason="Required again")
        history = self.store.get(self.workspace, task["id"])["events"]
        complete = next(event for event in history if event["action"] == "complete")
        self.assertEqual(complete["evidence"], "tests passed; commit abc123")
        self.assertEqual(task["revision"], 9)

    def test_rejected_transition_and_stale_revision_have_no_side_effects(self):
        original = self.create()
        self.error("invalid_transition", self.action, original, "complete", evidence="claim")
        started = self.action(original, "start")
        self.error("revision_conflict", self.action, original, "cancel", reason="stale")
        self.error("invalid_transition", self.action, started, "start")
        for action, data in (("complete", {"evidence":" "}), ("block", {"reason":""}),
                             ("release", {}), ("cancel", {})):
            self.error("invalid_input", self.action, started, action, **data)
        result = self.store.get(self.workspace, original["id"])
        self.assertEqual(result["task"], started)
        self.assertEqual(len(result["events"]), 2)

    def test_prerequisites_and_cancelled_dependencies_gate_start(self):
        before = self.create()
        after = self.create(dependencies=[before["id"]])
        self.assertFalse(after["ready"])
        self.error("dependencies_unfinished", self.action, after, "start")
        before = self.action(before, "cancel", reason="Dropped")
        self.error("dependencies_unfinished", self.action, after, "start")
        before = self.action(before, "reopen", reason="Restored")
        before = self.action(before, "start")
        self.action(before, "complete", evidence="Done")
        after = self.action(after, "start")
        self.assertTrue(after["ready"])
        self.assertEqual(self.action(after, "complete", evidence="Done")["status"], "completed")

    def test_completed_prerequisite_cannot_reopen_under_active_blocked_or_done_task(self):
        before = self.action(self.create(), "start")
        before = self.action(before, "complete", evidence="Done")
        after = self.action(self.create(dependencies=[before["id"]]), "start")
        for state in ("in_progress", "blocked", "completed"):
            with self.subTest(state=state):
                if state == "blocked":
                    after = self.action(after, "block", reason="External blocker")
                if state == "completed":
                    after = self.action(self.action(after, "release", reason="Continue"), "start")
                    after = self.action(after, "complete", evidence="Done")
                err = self.error("active_dependents", self.action, before, "reopen", reason="Revise")
                self.assertEqual(err.details["task_ids"], [after["id"]])
        self.action(after, "reopen", reason="Resolve downstream first")
        self.assertEqual(self.action(before, "reopen", reason="Now safe")["status"], "pending")

    def test_dependency_edits_and_done_when_are_frozen_after_start(self):
        task = self.action(self.create(), "start")
        for metadata in ({"dependencies": []}, {"done_when": "Easier"}):
            self.error("invalid_transition", self.store.update, self.workspace, task["id"],
                       task["revision"], actor="a", metadata=metadata)
        task = self.store.update(self.workspace, task["id"], task["revision"], actor="a",
            metadata={"description":"Progress note", "priority":"high"})["task"]
        self.assertEqual(task["description"], "Progress note")
        task = self.action(task, "complete", evidence="Tests pass")
        self.error("invalid_transition", self.store.update, self.workspace, task["id"],
            task["revision"], actor="a", metadata={"title":"New"})

    def test_cycles_missing_self_duplicate_and_cross_workspace_dependencies_rejected(self):
        a = self.create()
        b = self.create(dependencies=[a["id"]])
        c = self.create(dependencies=[b["id"]])
        other = self.create(workspace=self.other)
        for dependencies, code in (([a["id"]], "dependency_cycle"),
                                   ([c["id"]], "dependency_cycle"),
                                   (["missing"], "not_found"),
                                   ([other["id"]], "not_found"),
                                   ([b["id"], b["id"]], "invalid_input")):
            self.error(code, self.store.update, self.workspace, a["id"], 1,
                       actor="a", metadata={"dependencies": dependencies})
        self.assertEqual(self.store.get(self.workspace, a["id"])["task"], a)

    def test_actions_cannot_smuggle_metadata_or_raw_status(self):
        task = self.create()
        for metadata in ({"status":"completed"}, {"owner":"someone"}, {"revision":0}):
            self.error("invalid_input", self.store.update, self.workspace, task["id"], 1,
                       actor="a", metadata=metadata)
        self.error("invalid_input", self.store.update, self.workspace, task["id"], 1,
                   actor="a", metadata={"title":"Edit"}, action="start")
        self.error("invalid_input", self.action, task, "start", evidence="Unrelated")
        self.error("invalid_input", self.store.update, self.workspace, task["id"], True,
                   actor="a", action="start")

    def test_pagination_and_status_filters_do_not_drop_tasks_with_same_timestamp(self):
        tasks = [self.create() for _ in range(5)]
        page = self.store.list(self.workspace, limit=2)
        expected_open_summary = {
            "matching_total": 5,
            "by_status": {"pending": 5, "in_progress": 0, "blocked": 0,
                          "completed": 0, "cancelled": 0},
        }
        self.assertEqual(page["summary"], expected_open_summary)
        ids = [t["id"] for t in page["tasks"]]
        while page["next_cursor"]:
            page = self.store.list(self.workspace, limit=2, cursor=page["next_cursor"])
            self.assertEqual(page["summary"], expected_open_summary)
            ids.extend(t["id"] for t in page["tasks"])
        self.assertCountEqual(ids, [t["id"] for t in tasks])
        done = self.action(self.action(tasks[0], "start"), "complete", evidence="Done")
        open_page = self.store.list(self.workspace)
        self.assertEqual(len(open_page["tasks"]), 4)
        self.assertEqual(open_page["summary"], {
            "matching_total": 4,
            "by_status": {"pending": 4, "in_progress": 0, "blocked": 0,
                          "completed": 0, "cancelled": 0},
        })
        completed_page = self.store.list(self.workspace, status="completed")
        self.assertEqual([t["id"] for t in completed_page["tasks"]], [done["id"]])
        self.assertEqual(completed_page["summary"]["matching_total"], 1)
        self.assertEqual(completed_page["summary"]["by_status"]["completed"], 1)
        self.assertEqual(self.store.list(self.workspace, status="all")["summary"], {
            "matching_total": 5,
            "by_status": {"pending": 4, "in_progress": 0, "blocked": 0,
                          "completed": 1, "cancelled": 0},
        })
        high_page = self.store.list(self.workspace, priority="high")
        self.assertEqual(high_page["tasks"], [])
        self.assertEqual(high_page["summary"]["matching_total"], 0)
        self.error("invalid_input", self.store.list, self.workspace, limit=101)
        self.error("invalid_cursor", self.store.list, self.workspace, cursor="bad")
        cursor = self.store.list(self.workspace, limit=1)["next_cursor"]
        self.error("invalid_cursor", self.store.list, self.other, cursor=cursor)

    def test_history_pagination_keeps_newest_first_and_all_revisions(self):
        task = self.create()
        for index in range(5):
            task = self.store.update(self.workspace, task["id"], task["revision"], actor="a",
                metadata={"description": str(index)})["task"]
        events = []
        page = self.store.get(self.workspace, task["id"], event_limit=2)
        while True:
            events.extend(page["events"])
            if not page["next_event_cursor"]:
                break
            page = self.store.get(self.workspace, task["id"], event_limit=2,
                event_cursor=page["next_event_cursor"])
        self.assertEqual([e["revision"] for e in events], [6,5,4,3,2,1])

    def test_invalid_inputs_fail_before_creating_tasks(self):
        for kwargs in ({"title":" "}, {"done_when":""}, {"request_id":""},
                       {"priority":"urgent"}, {"dependencies":"not-a-list"}):
            self.error("invalid_input", self.create, **kwargs)
        self.assertEqual(self.store.list(self.workspace)["tasks"], [])

    def test_unknown_schema_is_rejected_without_reset(self):
        task = self.create()
        with sqlite3.connect(self.db) as db:
            db.execute("PRAGMA user_version=99")
        self.error("unsupported_schema", TaskStore, self.db)
        self.error("unsupported_schema", self.store.list, self.workspace)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 99)
            self.assertEqual(db.execute("SELECT id FROM tasks").fetchone()[0], task["id"])

    def test_unversioned_nonempty_database_is_not_adopted(self):
        foreign = self.root / "foreign.sqlite3"
        with sqlite3.connect(foreign) as db:
            db.execute("CREATE TABLE valuable_data (value TEXT)")
        self.error("unsupported_schema", TaskStore, foreign)

    def test_failed_event_write_rolls_back_task_update(self):
        task = self.create()
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TRIGGER fail_event BEFORE INSERT ON task_events "
                       "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
        self.error("storage_error", self.action, task, "start")
        self.assertEqual(self.store.get(self.workspace, task["id"])["task"], task)
