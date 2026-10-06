"""Pure path resolution shared by MCP and lifecycle entry points."""
import json
import os
from collections.abc import Mapping
from pathlib import Path
import tempfile
import re
import subprocess

from task_management.locking import atomic_write, file_lock, read_text, reject_symlinks
from task_management.rules import TaskError, workspace_key


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


def storage_directory(*, environ=None):
    environment = os.environ if environ is None else environ
    path = Path(environment.get("TASK_MANAGEMENT_DATA", data_directory()))
    if not path.is_absolute():
        raise TaskError("invalid_configuration", "TASK_MANAGEMENT_DATA must be absolute")
    reject_symlinks(path)
    return path


def session_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise TaskError("invalid_input", "session_id must be a nonblank safe Codex session identifier")
    return value


def canonical_workspace(value):
    directory = Path(workspace_key(value))
    result = subprocess.run(["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
                            capture_output=True, text=True, timeout=2)
    return Path(result.stdout.strip()).resolve(strict=True) if result.returncode == 0 else directory


def session_directory(workspace, session_id):
    root = canonical_workspace(workspace)
    path = root / ".codex/task-management/sessions" / session_key(session_id)
    reject_symlinks(path)
    return path


def prepare_session(workspace, session_id):
    """Install and verify exclusions before any lock, task or temporary data file."""
    root = canonical_workspace(workspace)
    path = session_directory(root, session_id)
    private = root / ".codex/task-management"
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-path", "info/exclude"],
                            capture_output=True, text=True, timeout=2)
    if result.returncode == 0:
        tracked = subprocess.run(["git", "-C", str(root), "ls-files", "--", ".codex/task-management"],
                                 capture_output=True, text=True, timeout=2, check=True)
        if tracked.stdout:
            raise TaskError("tracked_task_storage", "Task storage is already tracked; no private data was written")
        exclude = Path(result.stdout.strip())
        if not exclude.is_absolute():
            exclude = root / exclude
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(exclude.with_name("task-management-exclude.lock")):
            content = read_text(exclude) if exclude.exists() else ""
            rule = "/.codex/task-management/"
            if rule not in content.splitlines():
                atomic_write(exclude, content.rstrip("\n") + "\n" + rule + "\n")
        targets = [private / "sessions", path / "tasks.md", path / "index.md", path / "archive.md", path / ".lock"]
        for target in targets:
            ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", "--", str(target)],
                                     capture_output=True, timeout=2)
            if ignored.returncode != 0:
                raise TaskError("storage_error", "Git exclusion could not be verified; no task data written")
    else:
        # A nested self-ignoring file also protects this directory after future git init.
        reject_symlinks(private)
        private.mkdir(parents=True, exist_ok=True)
        ignore = private / ".gitignore"
        if not ignore.exists():
            atomic_write(ignore, "*\n")
        elif read_text(ignore) not in {"*", "*\n"}:
            raise TaskError("storage_error", "Existing task ignore file is not a verified full exclusion; no rules overwritten")
    reject_symlinks(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


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
