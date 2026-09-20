"""Installer reminder preference contracts without creating a runtime."""
from pathlib import Path
import tempfile
import unittest

from scripts.install_runtime import configure_reminder, parse_arguments
from task_management.config import read_reminder_mode, write_reminder_mode


class InstallRuntimeTest(unittest.TestCase):
    def test_parses_explicit_reminder_mode(self):
        self.assertEqual(parse_arguments(["--reminder-mode", "always"]).reminder_mode,
                         "always")
        self.assertIsNone(parse_arguments([]).reminder_mode)

    def test_configure_preserves_valid_mode_and_repairs_invalid_mode(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "settings.json"
            write_reminder_mode("always", path=path)
            self.assertEqual(configure_reminder(None, path=path), "always")
            self.assertEqual(read_reminder_mode(path=path), "always")

            path.write_text("invalid")
            self.assertEqual(configure_reminder(None, path=path), "lifecycle")
            self.assertEqual(read_reminder_mode(path=path), "lifecycle")

            self.assertEqual(configure_reminder("always", path=path), "always")
            self.assertEqual(read_reminder_mode(path=path), "always")


if __name__ == "__main__":
    unittest.main()
