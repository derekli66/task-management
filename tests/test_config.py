"""Pure application-data path contracts shared by the server and hooks."""
from pathlib import Path
import unittest

from task_management.config import data_directory, database_path, settings_path


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


if __name__ == "__main__":
    unittest.main()
