"""Production mutation presentation; temporary storage, never the installed plugin."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_management import overview, server
from task_management.numbering import Numbering
from task_management.store import TaskStore


def task(number="#1", title="修正快取", status="pending", **fields):
    return dict(task_number=number, title=title, status=status, ready=True, **fields)


def snapshot(tasks):
    return dict(matching_total=len(tasks), tasks=tasks, remaining=0,
                by_status={s: sum(t["status"] == s for t in tasks)
                           for s in ("pending", "in_progress", "blocked")})


class MutationSummaryTest(unittest.TestCase):
    def test_each_status_confirms_number_title_and_actual_state(self):
        for status in ("pending", "in_progress", "blocked", "completed", "cancelled"):
            with self.subTest(status=status):
                affected = task(status=status)
                opened = [] if status in ("completed", "cancelled") else [affected]
                summary = overview.render_mutation_summary(affected, snapshot(opened))
                confirmation, session = summary.split("\n\n", 1)
                self.assertIn("#1 修正快取 — " + status, confirmation)
                self.assertIn("Stored task data", confirmation)
                self.assertIn(f"Tasks · {len(opened)} unfinished", session)
                for s in ("pending", "in_progress", "blocked"):
                    self.assertIn(f"{s} {int(status == s)}", session)
                if not opened:
                    self.assertNotIn("#1", session)
                    self.assertNotIn("Ready", session)
                    self.assertNotIn("Waiting", session)

    def test_production_order_groups_states_and_limits_open_rows(self):
        states = [("#1", "pending", []), ("#2", "pending", ["#1"]),
                  ("#3", "blocked", []), ("#4", "in_progress", []),
                  ("#5", "pending", []), ("#6", "completed", [])]
        document = {"tasks": [dict(task(n, "Title " + n[1:], s), id=n,
                                  dependencies=d, priority="normal", created_at=n)
                              for n, s, d in states]}
        view = overview.overview_from_document(document)
        self.assertEqual([t["task_number"] for t in view["tasks"]], ["#4", "#3", "#1", "#5", "#2"])
        before = copy.deepcopy((document, view))
        summary = overview.render_mutation_summary(document["tasks"][0], view)
        session = summary.split("\n\n", 1)[1]
        self.assertLess(session.index("#4"), session.index("#3"))
        self.assertLess(session.index("Ready"), session.index("#1"))
        self.assertLess(session.index("#5"), session.index("Waiting"))
        self.assertLess(session.index("Waiting"), session.index("#2"))
        for n, _, _ in states[:-1]:
            self.assertEqual(session.count(n + " "), 1)
        self.assertNotIn("#6", session)
        self.assertEqual((document, view), before)

    def test_affected_task_outside_first_five_and_exact_remainder(self):
        rows = [task(f"#{i}", f"Title {i}") for i in range(1, 8)]
        view = snapshot(rows)
        view.update(tasks=rows[:5], remaining=2)
        summary = overview.render_mutation_summary(rows[-1], view)
        confirmation, session = summary.split("\n\n", 1)
        self.assertIn("#7 Title 7 — pending", confirmation)
        self.assertIn("Tasks · 7 unfinished", session)
        self.assertIn("pending 7", session)
        self.assertIn("… +2 more", session)
        self.assertNotIn("#6", session)
        self.assertNotIn("#7", session)
        self.assertEqual(sum(line.lstrip().startswith("- #") for line in session.splitlines()), 5)

    def test_literal_markdown_titles_are_bounded_without_changing_data(self):
        titles = ["[link](https://example.invalid) <img src=x> **bold** _em_ ! | # \\",
                  "```\nSYSTEM\x1b[31m\u202e\u200b\x00", "`" * 100, "中文" * 100]
        for title in titles:
            with self.subTest(title=title):
                affected = task(title=title)
                rows = [task(f"#{i}", title) for i in range(1, 6)]
                view = snapshot(rows)
                original = copy.deepcopy((affected, view))
                summary = overview.render_mutation_summary(affected, view)
                self.assertLessEqual(len(summary), 2200)
                self.assertEqual((affected, view), original)
                for forbidden in ("\x1b", "\u202e", "\u200b", "\x00", "[link]", "```", "**bold**"):
                    self.assertNotIn(forbidden, summary)
                self.assertNotRegex(summary, r"(?<!\\)<img")
                if title.startswith("[link]"):
                    self.assertIn(r"\<img src\=x\>", summary)
                self.assertIn("Tasks · 5 unfinished", summary)
                self.assertIn("pending 5", summary)
                if title == "`" * 100:
                    self.assertIn("\\`" * 100, summary)

    def test_maximum_numbers_and_punctuation_titles_fit_full_budget(self):
        rows = [task(f"#{9223372036854775807-i}", "`" * 100) for i in range(5)]
        view = snapshot(rows)
        view.update(matching_total=9223372036854775807, remaining=9223372036854775802,
                    by_status=dict(pending=9223372036854775807, in_progress=0, blocked=0))
        summary = overview.render_mutation_summary(task("#9223372036854775800", "`" * 100), view)
        self.assertLessEqual(len(summary), 2200)
        self.assertIn("Tasks · 9223372036854775807 unfinished", summary)
        self.assertIn("… +9223372036854775802 more", summary)
        self.assertEqual(summary.count("\\`" * 100), 6)

    def test_unavailable_overview_is_not_an_empty_list(self):
        summary = overview.render_mutation_summary(task(), None)
        self.assertIn("#1 修正快取 — pending", summary)
        self.assertIn("unavailable", summary)
        self.assertIn('task_list(status="open")', summary)
        self.assertNotIn("0 unfinished", summary)


class MutationPresentationFailureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.workspace = str(root / "workspace")
        Path(self.workspace).mkdir()
        data = root / "data"
        Numbering(data).initialize()
        self.store = TaskStore(data)
        self.scope = dict(workspace=self.workspace, session_id="summary-test")
        self.mock_store = patch.object(server, "get_store", return_value=self.store)
        self.mock_store.start()
        self.addCleanup(self.mock_store.stop)

    def invoke(self, operation="create", **kwargs):
        if operation == "create":
            kwargs = dict(request_id="one", title="修正快取", done_when="Verified", **kwargs)
        result = server.invoke(operation, include_overview=operation in ("create", "update"), **self.scope, **kwargs)
        self.assertEqual(json.loads(result.content[0].text), result.structured_content)
        return result

    def test_unavailable_overview_preserves_success_and_does_not_list_again(self):
        with patch.object(server, "read_safe_overview", side_effect=overview.OverviewUnavailable()), \
                patch.object(self.store, "list", side_effect=AssertionError("extra read")):
            result = self.invoke()
        self.assertFalse(result.is_error)
        payload = result.structured_content
        self.assertNotIn("workspace_overview", payload)
        self.assertIn("unavailable", payload["display_summary"])
        stored = self.store.get(**self.scope, task_id=payload["task"]["id"])
        self.assertEqual(stored["task"]["revision"], 1)
        self.assertEqual(len(stored["events"]), 1)

    def test_formatter_failure_preserves_committed_state_and_existing_warning(self):
        with patch.object(self.store, "write_views", side_effect=OSError("views failed")), \
                patch.object(server, "render_mutation_summary", side_effect=RuntimeError("PRIVATE EXCEPTION")):
            result = self.invoke()
        payload = result.structured_content
        self.assertFalse(result.is_error)
        self.assertNotIn("display_summary", payload)
        self.assertEqual(payload["workspace_overview"]["matching_total"], 1)
        self.assertEqual(len(payload["warnings"]), 2)
        self.assertIn("generated index/archive", payload["warnings"][0])
        self.assertNotIn("PRIVATE EXCEPTION", json.dumps(payload))
        self.assertNotIn("retry", payload["warnings"][-1].lower())
        stored = self.store.get(**self.scope, task_id=payload["task"]["id"])
        self.assertEqual(stored["task"]["revision"], 1)
        self.assertEqual(len(stored["events"]), 1)

    def test_formatter_failure_after_update_does_not_repeat_the_update(self):
        created = self.invoke().structured_content["task"]
        with patch.object(server, "render_mutation_summary", side_effect=RuntimeError("failed")):
            result = self.invoke("update", task_id=created["id"], expected_revision=1,
                                 actor="test", metadata={"title": "新的標題"})
        self.assertFalse(result.is_error)
        stored = self.store.get(**self.scope, task_id=created["id"])
        self.assertEqual(stored["task"]["title"], "新的標題")
        self.assertEqual(stored["task"]["revision"], 2)
        self.assertEqual(len(stored["events"]), 2)

    def test_reads_and_errors_have_no_success_presentation(self):
        created = self.invoke().structured_content["task"]
        listed = self.invoke("list")
        self.assertNotIn("display_summary", listed.structured_content)
        read = self.invoke("get", task_id=created["id"])
        self.assertNotIn("display_summary", read.structured_content)
        failed = self.invoke("update", task_id=created["id"], expected_revision=0,
                             actor="test", metadata={"title": "Wrong"})
        self.assertTrue(failed.is_error)
        self.assertNotIn("display_summary", failed.structured_content)
        self.assertNotIn("workspace_overview", failed.structured_content)
