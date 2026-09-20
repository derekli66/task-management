#!/usr/bin/env python3
"""Explicit dependency setup; MCP startup never installs or upgrades packages."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from task_management.config import read_reminder_mode, settings_path, write_reminder_mode


def parse_arguments(arguments=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--reminder-mode", choices=("lifecycle", "always"),
                        help="Show task reminders at lifecycle events or after every turn")
    return parser.parse_args(arguments)


def configure_reminder(requested_mode, *, path=None):
    """Preserve a valid preference or repair it to the safe lifecycle default."""
    target = settings_path() if path is None else Path(path)
    mode = requested_mode or read_reminder_mode(path=target)
    write_reminder_mode(mode, path=target)
    return mode


def main(arguments=None):
    options = parse_arguments(arguments)
    if sys.version_info < (3, 13):
        raise SystemExit("Python 3.13+ is required; select that interpreter explicitly.")
    lock = ROOT / "requirements.lock"
    identity = hashlib.sha256(lock.read_bytes()).hexdigest()[:16]
    runtime = (Path.home() / ".local/share/task-management/runtime" /
               f"{identity}-py{sys.version_info.major}.{sys.version_info.minor}")
    python = runtime / "bin/python"
    ready = runtime / ".ready"
    if not ready.exists():
        venv.EnvBuilder(with_pip=True).create(runtime)
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                        "-r", str(lock)], check=True)
        subprocess.run([str(python), "-m", "pip", "check"], check=True)
        ready.touch()
    # Legacy Codex MCP resolves relative cwd against the cache, but does not
    # substitute PLUGIN_ROOT in args. Anchor cwd explicitly, then use a local path.
    config = {"mcpServers": {"tasks": {
        "command": str(python),
        "args": ["./task_management/server.py"],
        "cwd": ".",
        "env_vars": ["TASK_MANAGEMENT_DB"],
    }}}
    (ROOT / ".mcp.json").write_text(json.dumps(config, indent=2) + "\n")
    configure_reminder(options.reminder_mode)
    print(python)


if __name__ == "__main__":
    main()
