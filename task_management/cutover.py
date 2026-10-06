"""Explicit migration barrier: drain writers, backup, fence, verify, then activate."""
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import uuid

from task_management.legacy import read_snapshot
from task_management.locking import atomic_write, file_lock, read_text, reject_symlinks
from task_management.markdown import metadata_text, decode_metadata
from task_management.numbering import Numbering
from task_management.rules import TaskError


def _assert_drained(path, *, exclude_self=False):
    lsof = shutil.which("lsof")
    if lsof is None:
        raise TaskError("migration_blocked", "lsof is required to verify that old file descriptors are closed")
    targets = [str(p) for p in (path,Path(str(path)+"-wal"),Path(str(path)+"-shm")) if p.exists()]
    result = subprocess.run([lsof,"-t","--",*targets],capture_output=True,text=True,timeout=10)
    pids = set(result.stdout.split())
    if exclude_self:
        pids.discard(str(os.getpid()))
    if pids or result.returncode not in (0,1):
        raise TaskError("migration_blocked", "Close all legacy connections before cutover", process_ids=sorted(pids))


def _verify_readonly(path):
    # New process AFTER the guard closes: BUSY is not proof of a durable fence.
    code = '''import sqlite3,sys
db=sqlite3.connect(sys.argv[1],timeout=0.2,isolation_level=None)
try:
    db.execute("BEGIN IMMEDIATE")
    db.execute("UPDATE tasks SET title=title WHERE 0")
except sqlite3.Error as exc:
    sys.exit(0 if (getattr(exc,"sqlite_errorcode",0)&255)==sqlite3.SQLITE_READONLY else 2)
finally:
    db.rollback();db.close()
sys.exit(3)
'''
    result = subprocess.run([sys.executable,"-c",code,str(path)],capture_output=True,text=True,timeout=5)
    if result.returncode != 0:
        raise TaskError("migration_blocked", "New legacy writer was not rejected as SQLITE_READONLY; activation refused")


def _durable_readonly(path):
    reject_symlinks(path)
    os.chmod(path,0o400)
    descriptor = os.open(path,os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    descriptor = os.open(path.parent,os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def initialize_fresh(data_path, legacy_db):
    """No implicit bootstrap: an existing old database always requires cutover."""
    if Path(legacy_db).exists():
        raise TaskError("migration_required", "Legacy database exists; preview and cut over instead of starting at #1")
    return Numbering(data_path).initialize()


def cutover_legacy(data_path, db_path, *, writers_stopped=False):
    """Operator must disable old launchers; never kill unrelated user sessions."""
    if not writers_stopped:
        raise TaskError("migration_blocked", "Confirm old launchers/connections are stopped before fencing")
    path, numbering = Path(db_path), Numbering(data_path)
    if not path.is_absolute() or not path.is_file():
        raise TaskError("invalid_configuration", "Legacy DB must be an existing absolute file")
    reject_symlinks(path)
    _assert_drained(path)
    numbering.directory.mkdir(parents=True,exist_ok=True)
    journal_path = numbering.directory / "cutover.md"
    with file_lock(numbering.directory / "cutover.lock"):
        if journal_path.exists():
            journal = decode_metadata(read_text(journal_path))
            source = journal["source"]
            if source["path"] != str(path):
                raise TaskError("migration_conflict", "Cutover journal belongs to another source")
            snapshot = read_snapshot(source["snapshot_path"])
            if snapshot["digest"] != source["snapshot_digest"]:
                raise TaskError("storage_error", "Cutover backup differs from recorded source")
        else:
            if (numbering.directory / "activation.md").exists() or (numbering.directory / "numbering.md").exists():
                raise TaskError("migration_conflict", "Numbering is already initialized; cannot bind another source")
            source_id = str(uuid.uuid4())
            backup_path = numbering.directory / f"legacy-{source_id}.sqlite3"
            with closing(sqlite3.connect(path,timeout=0.2,isolation_level=None)) as guard:
                guard.execute("BEGIN IMMEDIATE")
                _assert_drained(path,exclude_self=True)
                snapshot = read_snapshot(path)
                with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)) as reader, closing(sqlite3.connect(backup_path)) as backup:
                    reader.backup(backup)
                    backup.execute("PRAGMA journal_mode=DELETE")
                _durable_readonly(backup_path)
                if read_snapshot(backup_path)["digest"] != snapshot["digest"]:
                    raise TaskError("storage_error", "Backup verification failed")
                source = dict(source_id=source_id,path=str(path),snapshot_path=str(backup_path),
                              schema_version=2,snapshot_digest=snapshot["digest"],high_water=snapshot["high_water"])
                modes = {str(p):stat.S_IMODE(p.stat().st_mode) for p in
                         (path,Path(str(path)+"-wal"),Path(str(path)+"-shm")) if p.exists()}
                journal = dict(format_version=1,state="prepared",source=source,original_modes=modes)
                atomic_write(journal_path,metadata_text("Legacy cutover",journal))
                for name in modes:
                    _durable_readonly(Path(name))
                guard.rollback()
            # Guard is now closed, before testing that a restarted process cannot write.
        for name in journal["original_modes"]:
            target = Path(name)
            if target.exists():
                reject_symlinks(target)
                _durable_readonly(target)
        _assert_drained(path)
        _verify_readonly(path)
        if read_snapshot(path)["digest"] != source["snapshot_digest"]:
            raise TaskError("migration_conflict", "Source changed since backup; activation refused")
        journal["state"] = "fenced"
        atomic_write(journal_path,metadata_text("Legacy cutover",journal))
        numbering.initialize(baseline=source["high_water"],source=source)
        journal["state"] = "active"
        atomic_write(journal_path,metadata_text("Legacy cutover",journal))
        return source
