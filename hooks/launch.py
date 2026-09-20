#!/usr/bin/env python3
"""Python 3.9-compatible bridge to the plugin's pinned runtime interpreter."""
import json
import os
from pathlib import Path
import sys


def debug(message):
    if os.environ.get("TASK_MANAGEMENT_HOOK_DEBUG") == "1":
        print("task-management hook: " + message, file=sys.stderr)


def main():
    try:
        plugin_root = Path(os.environ["PLUGIN_ROOT"]).resolve(strict=True)
        config = json.loads((plugin_root / ".mcp.json").read_text())
        interpreter = Path(config["mcpServers"]["tasks"]["command"])
        script = plugin_root / "hooks/task_reminder.py"
        if not interpreter.is_absolute() or not interpreter.is_file() or not script.is_file():
            raise ValueError("pinned runtime or reminder script is unavailable")
        os.execv(str(interpreter), [str(interpreter), str(script)])
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        debug(str(exc))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
