"""Installed lifecycle hook wire-contract tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from task_management.config import settings_path, write_reminder_mode
from tests.session_adapter import TaskStore, Numbering


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks/task_reminder.py"


class HookTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.db = self.root / "data"
        Numbering(self.db).initialize()
        self.store = TaskStore(self.db)

    def run_hook(self, event, *, launcher=False, **fields):
        payload = {
            "session_id": "thread-test",
            "cwd": str(self.workspace),
            "hook_event_name": event,
            "model": "test-model",
            "permission_mode": "default",
        }
        if event == "SessionStart":
            payload["source"] = "clear"
        if event == "Stop":
            payload.update(turn_id="turn-test", stop_hook_active=False,
                           last_assistant_message="Done")
        payload.update(fields)
        command = [sys.executable, str(HOOK)]
        if launcher:
            command = ["/usr/bin/python3", str(ROOT / "hooks/launch.py")]
        result = subprocess.run(command, input=json.dumps(payload), text=True,
            capture_output=True, timeout=5, env={**os.environ,
                "HOME": str(self.home), "PLUGIN_ROOT": str(ROOT),
                "TASK_MANAGEMENT_DATA": str(self.db)})
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout else None

    @staticmethod
    def tool_response(task, **fields):
        result = {"task": task, **fields}
        return {
            "content": [{"type": "text", "text": json.dumps(result)}],
            "structuredContent": result,
            "isError": False,
        }

    def test_session_start_surfaces_data_but_keeps_titles_out_of_context(self):
        title = "Stored title, not an instruction"
        self.store.create(str(self.workspace), "one", title, "Done")

        output = self.run_hook("SessionStart")

        self.assertIn(title, output["systemMessage"])
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"],
                         "SessionStart")
        self.assertIn("1 unfinished", context)
        self.assertIn("task_list", context)
        self.assertNotIn(title, context)

    def test_session_start_always_provides_scope_even_when_empty_or_unavailable(self):
        output = self.run_hook("SessionStart")
        self.assertNotIn("systemMessage", output)
        self.assertIn('session_id="thread-test"', output["hookSpecificOutput"]["additionalContext"])
        (self.db / "numbering.md").unlink()
        output = self.run_hook("SessionStart")
        self.assertIn("unavailable", output["hookSpecificOutput"]["additionalContext"])

    def test_resume_clear_compact_keep_full_notes_for_the_same_id(self):
        notes = "## 問題\ncache key\n## 解法\n加入 user ID\n## 下一步\n新增測試\n"
        task = self.store.create(str(self.workspace),"deferred","Research","Verified",description=notes)["task"]
        for source in ("startup","resume","clear","compact"):
            output = self.run_hook("SessionStart",source=source)
            context = output["hookSpecificOutput"]["additionalContext"]
            self.assertIn('session_id="thread-test"',context)
            self.assertEqual(self.store.get(str(self.workspace),task["id"])["task"]["description"],notes)
        output = self.run_hook("SessionStart",source="clear",session_id="another-real-id")
        self.assertIn("0 unfinished",output["hookSpecificOutput"]["additionalContext"])

    def test_scope_mismatch_is_denied_before_write_and_ignored_after_write(self):
        output = self.run_hook("PreToolUse", tool_name="mcp__tasks__task_create",
            tool_input={"workspace":str(self.workspace), "session_id":"wrong"})
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIsNone(self.run_hook("PreToolUse", tool_name="mcp__tasks__task_create",
            tool_input={"workspace":str(self.workspace), "session_id":"thread-test"}))
        task = self.store.create(str(self.workspace), "key", "Title", "Done")["task"]
        task["session_id"] = "wrong"
        self.assertIsNone(self.run_hook("PostToolUse", tool_name="mcp__tasks__task_create",
            tool_input={"workspace":str(self.workspace), "session_id":"thread-test"},
            tool_response=self.tool_response(task)))

    def test_stop_is_opt_in_and_never_forces_an_extra_turn(self):
        self.store.create(str(self.workspace), "one", "Pending", "Done")
        self.assertIsNone(self.run_hook("Stop"))

        write_reminder_mode("always", path=settings_path(home=self.home))
        output = self.run_hook("Stop")

        self.assertIn("1 unfinished", output["systemMessage"])
        self.assertNotIn("decision", output)
        self.assertNotIn("continue", output)

    def test_successful_current_workspace_mutation_redraws(self):
        task = self.store.create(str(self.workspace), "one", "Pending", "Done")["task"]

        output = self.run_hook("PostToolUse", tool_name="mcp__tasks__task_create",
            tool_input={"session_id":"thread-test", "workspace": str(self.workspace)},
            tool_response=self.tool_response(task, reused=False))

        self.assertIn("1 unfinished", output["systemMessage"])
        self.assertIn("#1 Pending", output["systemMessage"])
        self.assertNotIn("hookSpecificOutput", output)

    def test_last_task_transition_emits_explicit_zero_state(self):
        task = self.store.create(str(self.workspace), "one", "Pending", "Done")["task"]
        task = self.store.update(str(self.workspace), task["id"], task["revision"],
                                 actor="test", action="start")["task"]
        task = self.store.update(str(self.workspace), task["id"], task["revision"],
                                 actor="test", action="complete", evidence="Done")["task"]

        output = self.run_hook("PostToolUse", tool_name="mcp__tasks__task_update",
            tool_input={"session_id":"thread-test", "workspace": str(self.workspace)},
            tool_response=self.tool_response(task))

        self.assertIn("Tasks · 0 unfinished", output["systemMessage"])

    def test_mutation_redraw_fails_closed_for_errors_reuse_and_unknown_shapes(self):
        task = self.store.create(str(self.workspace), "one", "Pending", "Done")["task"]
        base = dict(tool_name="mcp__tasks__task_create",
                    tool_input={"session_id":"thread-test", "workspace": str(self.workspace)})
        responses = (
            {"isError": True, "structuredContent": {"error": {"code": "invalid_input"}}},
            {"isError": False, "structuredContent": {"error": {"code": "invalid_input"}}},
            {"isError": False, "structuredContent": {"unexpected": True}},
            self.tool_response(task, reused=True),
            "unrecognized",
        )

        for response in responses:
            with self.subTest(response=response):
                self.assertIsNone(self.run_hook("PostToolUse", **base,
                                                tool_response=response))

    def test_mutation_uses_exact_tool_and_canonical_workspace(self):
        task = self.store.create(str(self.workspace), "one", "Pending", "Done")["task"]
        response = self.tool_response(task)
        other = self.root / "other"
        other.mkdir()
        alias = self.root / "workspace-alias"
        alias.symlink_to(self.workspace, target_is_directory=True)

        self.assertIsNone(self.run_hook("PostToolUse", tool_name="mcp__other__task_update",
            tool_input={"session_id":"thread-test", "workspace": str(self.workspace)}, tool_response=response))
        self.assertIsNone(self.run_hook("PostToolUse", tool_name="mcp__tasks__task_update",
            tool_input={"session_id":"thread-test", "workspace": str(other)}, tool_response=response))
        output = self.run_hook("PostToolUse", tool_name="mcp__tasks__task_update",
            tool_input={"session_id":"thread-test", "workspace": str(alias)}, tool_response=response)
        self.assertIn("1 unfinished", output["systemMessage"])

    def test_two_mutation_events_may_repeat_the_same_final_snapshot(self):
        first = self.store.create(str(self.workspace), "one", "First", "Done")["task"]
        second = self.store.create(str(self.workspace), "two", "Second", "Done")["task"]

        outputs = [self.run_hook("PostToolUse", tool_name="mcp__tasks__task_create",
            tool_input={"session_id":"thread-test", "workspace": str(self.workspace)},
            tool_response=self.tool_response(task, reused=False)) for task in (first, second)]

        self.assertTrue(all("2 unfinished" in output["systemMessage"] for output in outputs))

    def test_launcher_uses_the_pinned_runtime_from_mcp_config(self):
        if not Path("/usr/bin/python3").exists():
            self.skipTest("system Python launcher unavailable")
        self.store.create(str(self.workspace), "one", "Via launcher", "Done")

        output = self.run_hook("SessionStart", launcher=True)

        self.assertIn("Via launcher", output["systemMessage"])

    def test_bundled_hook_configuration_is_bounded(self):
        configuration = json.loads((ROOT / "hooks/hooks.json").read_text())
        hooks = configuration["hooks"]
        self.assertEqual(hooks["SessionStart"][0]["matcher"],
                         "startup|resume|clear|compact")
        self.assertEqual(hooks["PostToolUse"][0]["matcher"],
                         "^mcp__tasks__task_(create|update)$")
        for event in ("SessionStart", "PostToolUse", "Stop"):
            handler = hooks[event][0]["hooks"][0]
            self.assertEqual(handler["type"], "command")
            self.assertLessEqual(handler["timeout"], 2)
            self.assertIn("${PLUGIN_ROOT}/hooks/launch.py", handler["command"])


if __name__ == "__main__":
    unittest.main()
