"""Installed lifecycle hook wire-contract tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from task_management.config import settings_path, write_reminder_mode
from task_management.store import TaskStore


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
        self.db = self.root / "tasks.sqlite3"
        self.store = TaskStore(self.db)

    def run_hook(self, event, *, launcher=False):
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
        command = [sys.executable, str(HOOK)]
        if launcher:
            command = ["/usr/bin/python3", str(ROOT / "hooks/launch.py")]
        result = subprocess.run(command, input=json.dumps(payload), text=True,
            capture_output=True, timeout=5, env={**os.environ,
                "HOME": str(self.home), "PLUGIN_ROOT": str(ROOT),
                "TASK_MANAGEMENT_DB": str(self.db)})
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout) if result.stdout else None

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

    def test_session_start_is_quiet_for_empty_or_unavailable_storage(self):
        self.assertIsNone(self.run_hook("SessionStart"))
        self.db.unlink()
        self.assertIsNone(self.run_hook("SessionStart"))

    def test_stop_is_opt_in_and_never_forces_an_extra_turn(self):
        self.store.create(str(self.workspace), "one", "Pending", "Done")
        self.assertIsNone(self.run_hook("Stop"))

        write_reminder_mode("always", path=settings_path(home=self.home))
        output = self.run_hook("Stop")

        self.assertIn("1 unfinished", output["systemMessage"])
        self.assertNotIn("decision", output)
        self.assertNotIn("continue", output)

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
        for event in ("SessionStart", "Stop"):
            handler = hooks[event][0]["hooks"][0]
            self.assertEqual(handler["type"], "command")
            self.assertLessEqual(handler["timeout"], 2)
            self.assertIn("${PLUGIN_ROOT}/hooks/launch.py", handler["command"])


if __name__ == "__main__":
    unittest.main()
