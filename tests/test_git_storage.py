"""Private task data is excluded before writing, including linked worktrees."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from task_management.config import prepare_session
from task_management.locking import atomic_write
from task_management.rules import TaskError


class GitStorageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo with spaces"
        self.git("init","-q",str(self.repo),cwd=self.root)

    def git(self,*args,cwd=None):
        return subprocess.run(["git","-C",str(cwd or self.repo),*args],capture_output=True,
                              text=True,check=True,timeout=5).stdout

    def assert_ignored(self,root):
        path = prepare_session(str(root),"actual-session")
        for name in ("tasks.md","index.md","archive.md"):
            atomic_write(path / name,"private notes\n")
        self.assertEqual(self.git("status","--porcelain",cwd=root),"")

    def test_subdirectory_linked_worktree_and_nested_repo(self):
        atomic_write(self.repo / "seed.md","seed\n")
        self.git("add","seed.md")
        self.git("-c","user.name=Test","-c","user.email=test@example.com","commit","-qm","test fixture")
        nested = self.repo / "nested"
        nested.mkdir()
        self.assertEqual(prepare_session(str(nested),"session").parents[3],self.repo)
        worktree = self.root / "linked tree"
        self.git("worktree","add","-qb","fixture",str(worktree))
        self.assert_ignored(worktree)
        inner = nested / "inner"
        self.git("init","-q",str(inner),cwd=self.root)
        self.assertEqual(prepare_session(str(inner),"session").parents[3],inner)

    def test_tracked_data_and_failed_exclusion_stop_before_task_write(self):
        private = self.repo / ".codex/task-management"
        private.mkdir(parents=True)
        atomic_write(private / "existing.md","valuable\n")
        self.git("add","-f",".codex/task-management/existing.md")
        with self.assertRaises(TaskError) as caught:
            prepare_session(str(self.repo),"new")
        self.assertEqual(caught.exception.code,"tracked_task_storage")
        self.assertFalse((private / "sessions").exists())

    def test_failed_exclude_and_symlink_escape_write_no_session(self):
        with patch("task_management.config.atomic_write",side_effect=OSError("read-only exclude")):
            with self.assertRaises(OSError):
                prepare_session(str(self.repo),"new")
        self.assertFalse((self.repo / ".codex/task-management").exists())
        outside = self.root / "outside"
        outside.mkdir()
        (self.repo / ".codex").symlink_to(outside)
        with self.assertRaises(TaskError):
            prepare_session(str(self.repo),"new")
        self.assertEqual(list(outside.iterdir()),[])

    def test_non_git_directory_remains_ignored_after_git_init(self):
        plain = self.root / "plain"
        plain.mkdir()
        path = prepare_session(str(plain),"session")
        atomic_write(path / "tasks.md","notes\n")
        self.git("init","-q",str(plain),cwd=self.root)
        self.assertEqual(self.git("status","--porcelain",cwd=plain),"")

    def test_ignore_negation_is_not_mistaken_for_a_private_directory(self):
        plain = self.root / "plain"
        private = plain / ".codex/task-management"
        private.mkdir(parents=True)
        atomic_write(private / ".gitignore","*\n!sessions/\n!sessions/**\n")
        with self.assertRaises(TaskError):
            prepare_session(str(plain),"session")
        self.assertFalse((private / "sessions").exists())
        atomic_write(self.repo / ".gitignore","!/.codex/task-management/\n")
        with self.assertRaises(TaskError):
            prepare_session(str(self.repo),"session")
        self.assertFalse((self.repo / ".codex/task-management/sessions").exists())
