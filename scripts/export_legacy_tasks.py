#!/usr/bin/env python3
"""Read-only preview, or explicitly import one complete workspace task graph."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from task_management.config import database_path, storage_directory
from task_management.legacy import preview, import_workspace
from task_management.rules import TaskError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,default=database_path())
    parser.add_argument("--data",type=Path,default=storage_directory())
    parser.add_argument("--preview",action="store_true")
    parser.add_argument("--workspace")
    parser.add_argument("--session-id",help="Real ID supplied by SessionStart, not a guessed label")
    args = parser.parse_args()
    if not args.preview and (not args.workspace or not args.session_id):
        parser.error("import requires --workspace and --session-id; use --preview first")
    try:
        result = preview(args.db) if args.preview else import_workspace(
            args.data,args.db,args.workspace,args.session_id)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (TaskError,OSError) as exc:
        print(f"Export/import refused: {exc}",file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
