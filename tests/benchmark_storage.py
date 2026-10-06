"""Temporary-data comparison; run explicitly, never touches production tasks."""
import asyncio
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

from mcp import Client
from mcp.client.stdio import StdioServerParameters
from task_management.legacy_store import TaskStore as LegacyStore
from task_management.numbering import Numbering
from task_management.store import TaskStore


def timed(operation,count=20):
    samples = []
    for _ in range(count):
        started = time.perf_counter()
        operation()
        samples.append((time.perf_counter()-started)*1000)
    return round(statistics.median(samples),2)


async def measure(note_size):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        workspace = root / "workspace"
        workspace.mkdir()
        old = LegacyStore(root / "legacy.sqlite3")
        data = root / "data"
        Numbering(data).initialize()
        new = TaskStore(data)
        old_create, new_create = [], []
        for index in range(50):
            for store, samples, extra in ((old,old_create,{}),(new,new_create,{"session_id":"benchmark"})):
                started = time.perf_counter()
                store.create(str(workspace),str(index),f"Task {index}","Verified",
                             description="研究"*note_size,**extra)
                samples.append((time.perf_counter()-started)*1000)
        direct_old = timed(lambda:old.list(str(workspace)))
        direct_new = timed(lambda:new.list(str(workspace),session_id="benchmark"))
        params = StdioServerParameters(command=sys.executable,args=[str(Path(__file__).resolve().parents[1] / "task_management/server.py")],
            env={**os.environ,"TASK_MANAGEMENT_DATA":str(data)})
        started = time.perf_counter()
        async with Client(params,mode="legacy") as client:
            args = dict(workspace=str(workspace),session_id="benchmark")
            await client.call_tool("task_list",args)
            cold = (time.perf_counter()-started)*1000
            samples = []
            for _ in range(20):
                started = time.perf_counter()
                await client.call_tool("task_list",args)
                samples.append((time.perf_counter()-started)*1000)
        file = workspace / ".codex/task-management/sessions/benchmark/tasks.md"
        print(dict(tasks=50,note_characters=2*note_size,canonical_bytes=file.stat().st_size,
            sqlite_list_ms=direct_old,markdown_list_ms=direct_new,
            sqlite_create_ms=round(statistics.median(old_create),2),
            markdown_create_ms=round(statistics.median(new_create),2),
            markdown_warm_mcp_list_ms=round(statistics.median(samples),2),
            markdown_startup_first_list_ms=round(cold,2)),flush=True)


if __name__ == "__main__":
    for size in (0,10000):
        asyncio.run(measure(size))
