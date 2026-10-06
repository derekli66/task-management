"""Explicit schema-2 export/import. Ordinary task tools never import this module."""
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import uuid

from task_management.config import canonical_workspace, prepare_session, session_key
from task_management.locking import atomic_write, file_lock, read_text, reject_symlinks
from task_management.markdown import digest, text_digest, seal_task, encode_document, decode_document
from task_management.numbering import Numbering
from task_management.rules import TaskError
from task_management.store import TaskStore


def read_snapshot(db_path):
    """Capture all source rows and high-water mark in one read-only transaction."""
    path = Path(db_path)
    if not path.is_absolute() or not path.is_file():
        raise TaskError("invalid_configuration", "Legacy DB must be an existing absolute file")
    reject_symlinks(path)
    try:
        # A fenced, checkpointed file cannot create WAL sidecars on every runtime.
        # immutable is safe ONLY for a read-only source with no uncheckpointed WAL.
        immutable = path.stat().st_mode & 0o222 == 0 and not Path(str(path)+"-wal").exists()
        uri = path.as_uri()+"?mode=ro"+("&immutable=1" if immutable else "")
        with closing(sqlite3.connect(uri, uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            if db.execute("PRAGMA user_version").fetchone()[0] != 2:
                raise TaskError("unsupported_schema", "Export requires schema 2; source is never auto-upgraded")
            snapshot = {name:[dict(row) for row in db.execute(f"SELECT * FROM {name} ORDER BY {order}")]
                for name, order in (("tasks","id"),("task_numbers","number"),
                                    ("dependencies","workspace,task_id,depends_on"),("task_events","seq"))}
            sequence = db.execute("SELECT seq FROM sqlite_sequence WHERE name='task_numbers'").fetchone()
            snapshot["high_water"] = max([0, sequence[0] if sequence else 0] +
                                         [r["number"] for r in snapshot["task_numbers"]])
            snapshot["digest"] = digest(snapshot)
            return snapshot
    except (sqlite3.Error, ValueError, TypeError) as exc:
        raise TaskError("storage_error", "Cannot read consistent legacy snapshot") from exc


def preview(db_path):
    snapshot = read_snapshot(db_path)
    workspaces = sorted({r["workspace"] for r in snapshot["tasks"]})
    return dict(snapshot_digest=snapshot["digest"], high_water=snapshot["high_water"],
        workspaces=[dict(workspace=w, task_count=sum(t["workspace"]==w for t in snapshot["tasks"]),
                        event_count=sum(e["workspace"]==w for e in snapshot["task_events"])) for w in workspaces])


def _candidate(snapshot, source_workspace, metadata):
    numbers = {r["task_id"]:r["number"] for r in snapshot["task_numbers"]}
    tasks, events = [], []
    for row in snapshot["tasks"]:
        if row["workspace"] != source_workspace:
            continue
        task = copy.deepcopy(row)
        task.pop("workspace")
        task["task_number"] = f"#{numbers[task['id']]}"
        task["dependencies"] = sorted(d["depends_on"] for d in snapshot["dependencies"]
                                      if d["workspace"]==source_workspace and d["task_id"]==task["id"])
        seal_task(task)
        tasks.append(task)
    for row in snapshot["task_events"]:
        if row["workspace"] == source_workspace:
            event = copy.deepcopy(row)
            event["before"] = json.loads(event.pop("before_json")) if event["before_json"] is not None else None
            # pop also for null, preserving the original API shape and sparse snapshots.
            event.pop("before_json", None)
            event["after"] = json.loads(event.pop("after_json"))
            events.append(event)
    document = dict(metadata=copy.deepcopy(metadata), tasks=tasks, events=events)
    text = encode_document(document)
    decode_document(text)  # Validate source integrity before reserving an import.
    return document, text


def import_workspace(data_path, db_path, workspace, session_id, *, fault=None):
    """Reserve a source exactly once, then durably commit/recover its destination."""
    workspace, session_id = str(canonical_workspace(workspace)), session_key(session_id)
    numbering = Numbering(data_path)
    source_state = numbering.state().get("source")
    if not isinstance(source_state,dict) or source_state.get("path") != str(Path(db_path)):
        raise TaskError("migration_conflict", "Source does not match initialized cutover identity")
    snapshot = read_snapshot(source_state.get("snapshot_path",db_path))
    path = prepare_session(workspace, session_id)
    target = path / "tasks.md"
    def stage(point):
        if fault:
            fault(point)
    with file_lock(path / ".lock"), numbering.locked():
        ledger = numbering.load_locked()
        source = ledger.get("source")
        if (not isinstance(source, dict) or source.get("path") != str(Path(db_path))
                or source.get("snapshot_digest") != snapshot["digest"]
                or source.get("high_water") != snapshot["high_water"]):
            raise TaskError("migration_conflict", "Source is not the initialized cutover snapshot")
        if not any(t["workspace"] == workspace for t in snapshot["tasks"]):
            raise TaskError("not_found", "Legacy workspace has no tasks; select a workspace from preview")
        key = digest([source["source_id"], workspace])
        receipt = ledger["imports"].get(key)
        if receipt is not None:
            if receipt["workspace"] != workspace or receipt["session_id"] != session_id:
                raise TaskError("migration_conflict", "This source workspace is reserved for another session")
            if receipt["state"] == "committed":
                if not target.exists():
                    raise TaskError("storage_error", "Committed import document is missing")
                document, _ = TaskStore(data_path)._load(workspace,session_id,ledger["namespace"],ledger=ledger)
                if document["metadata"].get("import_id") != receipt["import_id"]:
                    raise TaskError("migration_conflict", "Target does not match committed import")
                return dict(reused=True, task_count=len(document["tasks"]), event_count=len(document["events"]),
                            path=str(target))
        else:
            if target.exists():
                raise TaskError("migration_conflict", "Import requires an empty destination session")
            metadata = dict(format_version=1, workspace=workspace, session_id=session_id, numbering_namespace=ledger["namespace"],
                document_id=str(uuid.uuid4()), generation=1, source_id=source["source_id"],
                import_id=str(uuid.uuid4()), source_snapshot_digest=snapshot["digest"])
            document, text = _candidate(snapshot,workspace,metadata)
            receipt = dict(state="pending", workspace=workspace, session_id=session_id,
                import_id=metadata["import_id"], source_snapshot_digest=snapshot["digest"],
                candidate_digest=text_digest(text), serializer_version=1, candidate_metadata=metadata)
            ledger = numbering.change_locked(ledger,lambda value:value["imports"].update({key:receipt}))
            stage("reserved")
        if receipt["serializer_version"] != 1:
            raise TaskError("unsupported_schema", "Pending import needs its original serializer")
        document, text = _candidate(snapshot,workspace,receipt["candidate_metadata"])
        if text_digest(text) != receipt["candidate_digest"]:
            raise TaskError("migration_conflict", "Source cannot reproduce the reserved candidate")
        if target.exists():
            if text_digest(read_text(target)) != receipt["candidate_digest"]:
                raise TaskError("migration_conflict", "Pending target differs; original files preserved")
        else:
            atomic_write(target,text)
        stage("canonical")
        numbering.change_locked(ledger,lambda value:value["imports"][key].update(state="committed"))
        stage("finalized")
    warnings = {}
    try:
        # Reacquire the session lock and read latest canonical data; another writer
        # may have committed after receipt finalization. Never publish stale views.
        TaskStore(data_path).refresh_views(workspace,session_id)
    except (OSError, TaskError):
        warnings = {"warnings":["Import committed; generated views could not be refreshed"]}
    return dict(reused=False, task_count=len(document["tasks"]), event_count=len(document["events"]),
                path=str(target), **warnings)
