"""Pure application-data path contracts shared by the server and hooks."""
from pathlib import Path
import json
import tempfile
import unittest

from task_management.config import (
    data_directory,
    database_path,
    read_reminder_mode,
    settings_path,
    write_reminder_mode,
)


class ConfigTest(unittest.TestCase):
    def test_default_paths_share_one_application_data_directory(self):
        home = Path("/Users/example")

        self.assertEqual(data_directory(home=home),
                         home / ".local/share/task-management")
        self.assertEqual(database_path(environ={}, home=home),
                         home / ".local/share/task-management/tasks.sqlite3")
        self.assertEqual(settings_path(home=home),
                         home / ".local/share/task-management/settings.json")

    def test_database_environment_override_is_preserved_verbatim(self):
        configured = "/var/tmp/private-tasks.sqlite3"

        self.assertEqual(database_path(
            environ={"TASK_MANAGEMENT_DB": configured}, home=Path("/unused")),
            Path(configured))

    def test_reminder_mode_defaults_and_writes_atomically(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "nested/settings.json"
            self.assertEqual(read_reminder_mode(path=path), "lifecycle")

            write_reminder_mode("always", path=path)

            self.assertEqual(read_reminder_mode(path=path), "always")
            self.assertEqual(json.loads(path.read_text()), {"reminder_mode": "always"})
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_invalid_reminder_settings_fail_closed_to_lifecycle(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "settings.json"
            for content in ("not json", '{"reminder_mode":"unknown"}', "[]"):
                path.write_text(content)
                self.assertEqual(read_reminder_mode(path=path), "lifecycle")
            with self.assertRaises(ValueError):
                write_reminder_mode("sometimes", path=path)


if __name__ == "__main__":
    unittest.main()
