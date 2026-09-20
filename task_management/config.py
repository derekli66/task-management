"""Pure path resolution shared by MCP and lifecycle entry points."""
import json
import os
from collections.abc import Mapping
from pathlib import Path
import tempfile


REMINDER_MODES = {"lifecycle", "always"}


def data_directory(*, home: Path | None = None) -> Path:
    """Return the stable per-machine application-data directory without I/O."""
    return (home if home is not None else Path.home()) / ".local/share/task-management"


def database_path(*, environ: Mapping[str, str] | None = None,
                  home: Path | None = None) -> Path:
    """Resolve the configured database path without validating or creating it."""
    environment = os.environ if environ is None else environ
    configured = environment.get("TASK_MANAGEMENT_DB")
    return Path(configured) if configured else data_directory(home=home) / "tasks.sqlite3"


def settings_path(*, home: Path | None = None) -> Path:
    """Return the reminder preference path without reading or creating it."""
    return data_directory(home=home) / "settings.json"


def read_reminder_mode(*, path: Path | None = None) -> str:
    """Read the per-machine preference, failing closed to lifecycle reminders."""
    target = settings_path() if path is None else Path(path)
    try:
        payload = json.loads(target.read_text())
        mode = payload.get("reminder_mode") if isinstance(payload, dict) else None
        return mode if mode in REMINDER_MODES else "lifecycle"
    except (OSError, json.JSONDecodeError, UnicodeError):
        return "lifecycle"


def write_reminder_mode(mode: str, *, path: Path | None = None) -> None:
    """Atomically persist a validated per-machine reminder preference."""
    if mode not in REMINDER_MODES:
        raise ValueError("reminder mode must be lifecycle or always")
    target = settings_path() if path is None else Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump({"reminder_mode": mode}, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            Path(temporary).unlink()
        except OSError:
            pass
        raise
