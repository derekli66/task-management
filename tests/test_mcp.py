"""Real stdio transport tests: run from a directory unrelated to the plugin."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from task_management.numbering import Numbering

ROOT = Path(__file__).resolve().parents[1]


class MCPTest(unittest.TestCase):
    def test_stdio_lifecycle_restart_and_machine_readable_errors(self):
        for mode in ("legacy", "auto"):
            with self.subTest(mode=mode):
                asyncio.run(asyncio.wait_for(self.lifecycle(mode), timeout=25))

    async def lifecycle(self, mode):
        with tempfile.TemporaryDirectory() as cwd:
            workspace = str(Path(cwd).resolve())
            other = Path(cwd) / "other"
            other.mkdir()
            Numbering(Path(cwd).resolve() / "data").initialize()
            params = StdioServerParameters(command=sys.executable,
                args=[str(ROOT / "task_management/server.py")], cwd=cwd,
                env={**os.environ, "TASK_MANAGEMENT_DATA": str(Path(cwd).resolve() / "data")})
            async with Client(params, mode=mode) as client:
                listing = await client.list_tools()
                self.assertEqual({t.name for t in listing.tools},
                    {"task_create", "task_list", "task_get", "task_update"})
                for tool in listing.tools:
                    if tool.name in ("task_create", "task_update"):
                        for required in ("task_number", "title", "status", "summary", "visible"):
                            self.assertIn(required, tool.description)
                async def call(name, **args):
                    result = await client.call_tool(name, {"workspace": workspace, "session_id":"thread-test", **args})
                    self.assertFalse(result.is_error, str(result))
                    self.assertEqual(json.loads(result.content[0].text), result.structured_content)
                    if name in ("task_create", "task_update"):
                        payload = result.structured_content
                        summary = payload["display_summary"]
                        confirmation = summary.split("\n\n", 1)[0]
                        task = payload["task"]
                        self.assertIn(f"{task['task_number']} {task['title']} — {task['status']}", confirmation)
                        self.assertIn(f"Tasks · {payload['workspace_overview']['matching_total']} unfinished", summary)
                        self.assertLessEqual(len(summary), 2200)
                    else:
                        self.assertNotIn("display_summary", result.structured_content)
                    return result.structured_content
                created = await call("task_create", request_id="retry-key", title="測試", done_when="All checks pass")
                task = created["task"]
                self.assertEqual(task["task_number"], "#1")
                self.assertEqual(created["workspace_overview"]["matching_total"], 1)
                self.assertEqual(created["workspace_overview"]["tasks"][0]["task_number"], "#1")
                task_page = await call("task_list")
                self.assertEqual(task_page["tasks"][0]["task_number"], "#1")
                self.assertEqual(task_page["summary"]["matching_total"], 1)
                self.assertEqual(task_page["summary"]["by_status"]["pending"], 1)
                self.assertEqual((await call("task_get", task_id="#1"))["task"], task)
                retry = await call("task_create", request_id="retry-key", title="測試", done_when="All checks pass")
                self.assertTrue(retry["reused"])
                self.assertEqual(retry["workspace_overview"]["matching_total"], 1)
                edited = await call("task_update", task_id=task["id"], expected_revision=1,
                                    actor="test", metadata={"title": "更新測試"})
                task = edited["task"]
                retry = await call("task_create", request_id="retry-key", title="測試", done_when="All checks pass")
                self.assertTrue(retry["reused"])
                self.assertEqual(retry["task"], task)
                self.assertNotIn("created", retry["display_summary"].lower())
                result = await client.call_tool("task_get", {"workspace":str(other), "session_id":"thread-test", "task_id":task["id"]})
                self.assertTrue(result.is_error)
                self.assertEqual(result.structured_content["error"]["code"], "not_found")
                for action, kwargs in (("start", {}), ("block", {"reason":"Wait"}),
                        ("release", {"reason":"Resume"}), ("start", {}),
                        ("complete", {"evidence":"Tests pass"}), ("reopen", {"reason":"Revise"}),
                        ("cancel", {"reason":"Unneeded"})):
                    mutation = await call("task_update", task_id=task["task_number"],
                                           expected_revision=task["revision"], actor="test",
                                           action=action, **kwargs)
                    task = mutation["task"]
                self.assertEqual((await call("task_list"))["tasks"], [])
                self.assertEqual(mutation["workspace_overview"], {
                    "matching_total": 0,
                    "by_status": {"pending": 0, "in_progress": 0, "blocked": 0},
                    "tasks": [],
                    "remaining": 0,
                })
                self.assertEqual(len((await call("task_list", status="all"))["tasks"]), 1)
                result = await client.call_tool("task_update", {"workspace":workspace, "session_id":"thread-test",
                    "task_id":task["id"], "expected_revision":1, "actor":"test", "action":"reopen", "reason":"Old"})
                self.assertTrue(result.is_error)
                self.assertEqual(result.structured_content["error"]["code"], "revision_conflict")
                self.assertNotIn("display_summary", result.structured_content)
            async with Client(params, mode=mode) as fresh:
                result = await fresh.call_tool("task_get", {"workspace":workspace, "session_id":"thread-test", "task_id":task["task_number"]})
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content["task"], task)
                self.assertEqual(len(result.structured_content["events"]), 9)

    def test_stdio_summary_retains_affected_task_outside_top_five(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as cwd:
                root = Path(cwd).resolve()
                Numbering(root / "data").initialize()
                params = StdioServerParameters(command=sys.executable,
                    args=[str(ROOT / "task_management/server.py")], cwd=cwd,
                    env={**os.environ, "TASK_MANAGEMENT_DATA": str(root / "data")})
                async with Client(params) as client:
                    scope = dict(workspace=str(root), session_id="summary-test")
                    for i in range(7):
                        result = await client.call_tool("task_create", dict(scope,
                            request_id=str(i), title=f"Task {i}", done_when="Verified",
                            priority="low" if i == 6 else "normal"))
                        self.assertFalse(result.is_error)
                    task = result.structured_content["task"]
                    result = await client.call_tool("task_update", dict(scope, task_id=task["id"],
                        expected_revision=1, actor="test", metadata={"title":"Deferred fix"}))
                    payload = result.structured_content
                    self.assertFalse(result.is_error)
                    self.assertEqual(json.loads(result.content[0].text), payload)
                    self.assertEqual(payload["workspace_overview"]["matching_total"], 7)
                    self.assertEqual(payload["workspace_overview"]["remaining"], 2)
                    self.assertNotIn(task["task_number"],
                        [t["task_number"] for t in payload["workspace_overview"]["tasks"]])
                    confirmation, session = payload["display_summary"].split("\n\n", 1)
                    self.assertIn("#7 Deferred fix — pending", confirmation)
                    self.assertIn("pending 7", session)
                    self.assertIn("… +2 more", session)
                    self.assertNotIn("#7 ", session)
        asyncio.run(asyncio.wait_for(scenario(), timeout=20))

    def test_invalid_tool_arguments_cannot_change_state(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as cwd:
                workspace = str(Path(cwd).resolve())
                Numbering(Path(cwd).resolve() / "data").initialize()
                params = StdioServerParameters(command=sys.executable,
                    args=[str(ROOT / "task_management/server.py")], cwd=cwd,
                    env={**os.environ, "TASK_MANAGEMENT_DATA": str(Path(cwd).resolve() / "data")})
                async with Client(params) as client:
                    result = await client.call_tool("task_create", dict(workspace=workspace, session_id="thread-test",
                        request_id="one", title="Task", done_when="Verified"))
                    task = result.structured_content["task"]
                    base = dict(workspace=workspace, session_id="thread-test", task_id=task["id"], expected_revision=1, actor="test")
                    for extra in ({"metadata":{"status":"completed"}},
                                  {"action":"start", "expected_revision":True},
                                  {"action":"start", "metadata":{"title":"Smuggled"}},
                                  {"metadata":{"title":None}},
                                  {"action":"start", "evidence":"Extraneous"}):
                        result = await client.call_tool("task_update", {**base, **extra})
                        self.assertTrue(result.is_error, str(result))
                    result = await client.call_tool("task_get", dict(workspace=workspace, session_id="thread-test", task_id=task["id"]))
                    self.assertEqual(result.structured_content["task"]["revision"], 1)
        asyncio.run(asyncio.wait_for(scenario(), timeout=20))
