#!/usr/bin/env python3
"""Explicit initialization/cutover. This script never stops user processes."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from task_management.config import database_path, storage_directory
from task_management.cutover import initialize_fresh, cutover_legacy
from task_management.rules import TaskError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data",type=Path,default=storage_directory())
    parser.add_argument("--db",type=Path,default=database_path())
    parser.add_argument("--fresh",action="store_true",help="Initialize only when no old DB exists")
    parser.add_argument("--writers-stopped",action="store_true",help="Old launchers disabled and connections closed")
    args = parser.parse_args()
    try:
        result = initialize_fresh(args.data,args.db) if args.fresh else cutover_legacy(
            args.data,args.db,writers_stopped=args.writers_stopped)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (TaskError,OSError) as exc:
        print(f"Cutover refused: {exc}",file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
