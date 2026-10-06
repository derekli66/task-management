"""Explicit activation, durable reservations, recovery and rollback detection."""
from pathlib import Path
import tempfile
import unittest

from task_management.numbering import Numbering
from task_management.rules import TaskError


class NumberingMarkdownTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "data"
        self.numbering = Numbering(self.root)

    def test_crud_cannot_initialize_missing_ledger(self):
        with self.assertRaises(TaskError) as error:
            self.numbering.reserve()
        self.assertEqual(error.exception.code, "storage_uninitialized")
        self.numbering.initialize(baseline=100)
        self.assertEqual(self.numbering.reserve(), ("#101", self.numbering.state()["namespace"]))
        (self.root / "numbering.md").unlink()
        with self.assertRaises(TaskError):
            self.numbering.reserve()

    def test_pending_commit_recovers_before_and_after_replace(self):
        for boundary in ("pending", "ledger"):
            with self.subTest(boundary=boundary):
                root = self.root / boundary
                numbering = Numbering(root)
                numbering.initialize()
                def fail(stage):
                    if stage == boundary:
                        raise RuntimeError("interrupted")
                numbering.fault = fail
                with self.assertRaises(RuntimeError):
                    numbering.reserve()
                numbering.fault = None
                self.assertEqual(numbering.reserve()[0], "#2")

    def test_restore_only_one_ledger_is_rejected(self):
        self.numbering.initialize()
        old = (self.root / "numbering.md").read_bytes()
        self.numbering.reserve()
        (self.root / "numbering.md").write_bytes(old)
        with self.assertRaises(TaskError):
            self.numbering.reserve()
