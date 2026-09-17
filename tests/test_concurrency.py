"""Real process races and crash recovery; all processes have bounded lifetimes."""
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from task_management.store import TaskError, TaskStore


def compete(db_path, workspace, gate, results, operation, task=None):
    gate.wait(10)
    try:
        store = TaskStore(db_path)
        if operation == "create":
            result = store.create(workspace, "one-request", "Task", "Done")
        else:
            result = store.update(workspace, task["id"], task["revision"], actor="worker",
                action=operation, **({"reason":"Revise"} if operation == "reopen" else {}))
        results.put(("ok", result["task"]["id"]))
    except TaskError as exc:
        results.put((exc.code, None))


def interrupted_update(db_path, workspace, task, ready):
    store = TaskStore(db_path)
    original = store._event
    def hold_uncommitted(*args, **kwargs):
        original(*args, **kwargs)
        ready.send(True)
        time.sleep(30)
    store._event = hold_uncommitted
    store.update(workspace, task["id"], task["revision"], actor="worker", action="start")


class ConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = str(Path(self.temp.name).resolve())
        self.db = Path(self.workspace) / "tasks.sqlite3"
        self.ctx = multiprocessing.get_context("spawn")

    def race(self, operations):
        gate, results = self.ctx.Event(), self.ctx.Queue()
        processes = [self.ctx.Process(target=compete,
            args=(self.db, self.workspace, gate, results, operation, task)) for operation, task in operations]
        try:
            for process in processes:
                process.start()
            gate.set()
            responses = [results.get(timeout=10) for _ in processes]
            for process in processes:
                process.join(5)
                self.assertEqual(process.exitcode, 0)
            return responses
        finally:
            for process in processes:
                if process.is_alive():
                    process.kill()
                process.join(5)
            results.close()
            results.join_thread()

    def test_two_processes_initialize_and_retry_one_create(self):
        responses = self.race([("create", None), ("create", None)])
        self.assertEqual([r[0] for r in responses], ["ok", "ok"])
        self.assertEqual(responses[0][1], responses[1][1])
        result = TaskStore(self.db).get(self.workspace, responses[0][1])
        self.assertEqual(len(result["events"]), 1)

    def test_two_claims_have_exactly_one_winner(self):
        store = TaskStore(self.db)
        task = store.create(self.workspace, "key", "Task", "Done")["task"]
        self.assertCountEqual([r[0] for r in self.race([("start", task), ("start", task)])],
                              ["ok", "revision_conflict"])
        result = store.get(self.workspace, task["id"])
        self.assertEqual(result["task"]["revision"], 2)
        self.assertEqual(len(result["events"]), 2)

    def test_racing_reopen_and_start_cannot_break_dependency_invariant(self):
        store = TaskStore(self.db)
        before = store.create(self.workspace, "first", "Before", "Done")["task"]
        before = store.update(self.workspace, before["id"], 1, actor="a", action="start")["task"]
        before = store.update(self.workspace, before["id"], 2, actor="a", action="complete", evidence="Done")["task"]
        after = store.create(self.workspace, "next", "After", "Done", dependencies=[before["id"]])["task"]
        responses = self.race([("reopen", before), ("start", after)])
        codes = [r[0] for r in responses]
        self.assertEqual(codes.count("ok"), 1)
        self.assertTrue({"active_dependents", "dependencies_unfinished"} & set(codes))

    def test_killed_writer_rolls_back_task_and_event_together(self):
        store = TaskStore(self.db)
        task = store.create(self.workspace, "key", "Task", "Done")["task"]
        read, write = self.ctx.Pipe(duplex=False)
        process = self.ctx.Process(target=interrupted_update, args=(self.db, self.workspace, task, write))
        try:
            process.start()
            self.assertTrue(read.poll(10), "Writer did not reach its uncommitted event")
            self.assertTrue(read.recv())
            process.kill()
            process.join(5)
            result = TaskStore(self.db).get(self.workspace, task["id"])
            self.assertEqual(result["task"], task)
            self.assertEqual(len(result["events"]), 1)
            self.assertEqual(store.update(self.workspace, task["id"], 1, actor="recovery", action="start")["task"]["revision"], 2)
        finally:
            if process.is_alive():
                process.kill()
            process.join(5)
            read.close()
            write.close()

    def test_busy_writer_returns_bounded_identifiable_error(self):
        store = TaskStore(self.db, timeout=0.05)
        task = store.create(self.workspace, "key", "Task", "Done")["task"]
        lock = sqlite3.connect(self.db, isolation_level=None)
        try:
            lock.execute("BEGIN IMMEDIATE")
            began = time.monotonic()
            with self.assertRaises(TaskError) as caught:
                store.update(self.workspace, task["id"], 1, actor="a", action="start")
            self.assertEqual(caught.exception.code, "database_busy")
            self.assertLess(time.monotonic() - began, 2)
        finally:
            lock.close()

    def test_git_worktrees_are_separate_namespaces(self):
        import subprocess
        repo, worktree = Path(self.workspace) / "repo", Path(self.workspace) / "branch"
        subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True, timeout=5)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "--allow-empty", "-qm", "Initial"], check=True, capture_output=True, timeout=5)
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(worktree)],
                       check=True, capture_output=True, timeout=5)
        store = TaskStore(self.db)
        task = store.create(str(repo), "same", "Main checkout", "Done")["task"]
        self.assertEqual(store.list(str(worktree))["tasks"], [])
        self.assertNotEqual(store.create(str(worktree), "same", "Other checkout", "Done")["task"]["id"], task["id"])
