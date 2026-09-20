"""Pure path resolution shared by MCP and lifecycle entry points."""
import os
from collections.abc import Mapping
from pathlib import Path


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
