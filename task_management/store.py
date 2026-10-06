"""Session-scoped Markdown tasks; legacy SQLite is never opened by CRUD."""
from contextlib import contextmanager
import copy
import json
import re
import uuid

from task_management.config import canonical_workspace, prepare_session, session_directory, session_key
from task_management.locking import atomic_write, file_lock, read_text
from task_management.markdown import decode_document, encode_document, seal_task, text_digest, validate_document
from task_management.numbering import Numbering
from task_management.rules import (STATUSES, PRIORITIES, TERMINAL, TaskError, nonblank, now,
    encode_cursor, decode_cursor, page_limit, metadata_changes, action_changes)


class TaskStore:
    def __init__(self, data_path, timeout=3.0):
        self.numbering = Numbering(data_path, timeout)
        self.timeout, self.fault = timeout, None

    def _load(self, workspace, session_id, namespace, *, ledger=None):
        path = session_directory(workspace, session_id) / "tasks.md"
        if not path.exists():
            receipts = (ledger or self.numbering.state())["imports"].values()
            known_import = any(r["workspace"] == workspace and r["session_id"] == session_id
                               for r in receipts)
            if known_import or any((path.parent / name).exists() for name in ("index.md","archive.md")):
                raise TaskError("storage_error", "Known session's canonical tasks.md is missing; automatic reset forbidden")
            return dict(metadata=dict(format_version=1, workspace=workspace, session_id=session_id,
                numbering_namespace=namespace, document_id=str(uuid.uuid4()), generation=0), tasks=[], events=[]), None
        text = read_text(path)
        document = decode_document(text)
        if (document["metadata"]["workspace"] != workspace or document["metadata"]["session_id"] != session_id
                or document["metadata"]["numbering_namespace"] != namespace):
            raise TaskError("storage_error", "Document belongs to another scope or numbering history")
        return document, text_digest(text)

    @contextmanager
    def _mutation(self, workspace, session_id):
        workspace, session_id = str(canonical_workspace(workspace)), session_key(session_id)
        self.numbering.state()  # Missing activation never creates workspace data.
        path = prepare_session(workspace, session_id)
        with file_lock(path / ".lock", self.timeout):
            with self.numbering.locked():
                ledger = self.numbering.load_locked()
                self.numbering.check_session_locked(ledger, workspace, session_id)
            document, baseline = self._load(workspace, session_id, ledger["namespace"],ledger=ledger)
            yield workspace, session_id, path, document, baseline

    def _commit(self, path, document, baseline):
        document["metadata"]["generation"] += 1
        validate_document(document)
        def check():
            target = path / "tasks.md"
            current = text_digest(read_text(target)) if target.exists() else None
            if current != baseline:
                raise TaskError("revision_conflict", "Session file changed outside the writer lock")
            if self.fault:
                self.fault("before_replace")
        atomic_write(path / "tasks.md", encode_document(document), before_replace=check)
        if self.fault:
            self.fault("committed")
        try:
            self.write_views(path, document)
            return {}
        except (OSError, TaskError):
            return {"warnings":["Task committed; generated index/archive could not be refreshed"]}

    def write_views(self, path, document):
        from task_management.overview import overview_from_document, render_overview
        generation = document["metadata"]["generation"]
        banner = f"Generated view. Source generation: {generation}. Edit through task tools.\n\n"
        atomic_write(path / "index.md", "# Unfinished tasks\n\n" + banner +
                     render_overview(overview_from_document(document, limit=None), include_empty=True,
                                     message_limit=1000000000) + "\n")
        archived = [t for t in document["tasks"] if t["status"] in TERMINAL]
        body = "".join(f"\n## {t['task_number']} {t['title']}\n\nStatus: {t['status']}\n\n"
            + t["description"] + "\n\nEvidence: " + (t["completion_evidence"] or "—") + "\n" for t in archived)
        atomic_write(path / "archive.md", "# Archived tasks\n\n" + banner + body)

    def refresh_views(self, workspace, session_id):
        """Repair generated files without changing canonical tasks or their revisions."""
        with self._mutation(workspace,session_id) as (_,_,path,document,baseline):
            if baseline is None:
                raise TaskError("not_found", "No canonical session file to refresh")
            self.write_views(path,document)
            return {"generation":document["metadata"]["generation"],"path":str(path)}

    @staticmethod
    def _snapshots(document, workspace, session_id):
        tasks = {task["id"]:task for task in document["tasks"]}
        result = []
        for original in document["tasks"]:
            task = {key:copy.deepcopy(value) for key,value in original.items()
                    if key not in {"initial_payload", "integrity_digest"}}
            task.update(workspace=workspace, session_id=session_id)
            task["unfinished_dependencies"] = sorted(identity for identity in task["dependencies"]
                                                     if tasks[identity]["status"] != "completed")
            task["ready"] = not task["unfinished_dependencies"]
            result.append(task)
        return result

    @staticmethod
    def _resolve(document, reference):
        nonblank(reference, "task_id")
        if reference.startswith("#") and (not re.fullmatch(r"#[1-9][0-9]{0,18}", reference)
                                          or int(reference[1:]) > 9223372036854775807):
            raise TaskError("invalid_input", "Task number must be # followed by a positive 64-bit integer")
        for task in document["tasks"]:
            if reference in {task["id"], task["task_number"]}:
                return task
        raise TaskError("not_found", "Task not found in this workspace/session")

    def _dependencies(self, document, identity, dependencies):
        if not isinstance(dependencies, list) or any(not isinstance(x, str) or not x.strip() for x in dependencies):
            raise TaskError("invalid_input", "dependencies must be a list of task UUIDs")
        if len(set(dependencies)) != len(dependencies):
            raise TaskError("invalid_input", "dependencies must not contain duplicates")
        tasks = {task["id"]:task for task in document["tasks"]}
        for dependency in dependencies:
            pending, visited = [dependency], set()
            while pending:
                current = pending.pop()
                if current == identity:
                    raise TaskError("dependency_cycle", "This dependency would introduce a cycle")
                if current not in tasks:
                    raise TaskError("not_found", "Dependency not found in this session")
                if current not in visited:
                    visited.add(current)
                    pending.extend(tasks[current]["dependencies"])

    def _event(self, document, workspace, session_id, before, task, action, actor, reason=None, evidence=None):
        snapshot = next(t for t in self._snapshots(document, workspace, session_id) if t["id"] == task["id"])
        document["events"].append(dict(seq=max((e["seq"] for e in document["events"]), default=0)+1,
            workspace=workspace, task_id=task["id"], revision=task["revision"], action=action,
            actor=actor, reason=reason, evidence=evidence, before=before, after=snapshot, created_at=task["updated_at"]))
        return snapshot

    def create(self, workspace, request_id, title, done_when, description="", priority="normal",
               dependencies=None, actor="unspecified", *, session_id):
        for name,value in (("request_id",request_id),("title",title),("done_when",done_when),("actor",actor)):
            nonblank(value, name)
        if not isinstance(description, str) or priority not in PRIORITIES:
            raise TaskError("invalid_input", "Invalid description or priority")
        dependencies = [] if dependencies is None else dependencies
        if not isinstance(dependencies, list) or any(not isinstance(x,str) for x in dependencies):
            raise TaskError("invalid_input", "Invalid dependencies")
        payload = json.dumps(dict(title=title, done_when=done_when, description=description,
                                  priority=priority, dependencies=sorted(dependencies)), sort_keys=True)
        with self._mutation(workspace, session_id) as (workspace, session_id, path, document, baseline):
            for task in document["tasks"]:
                if task["request_id"] == request_id:
                    if task["initial_payload"] != payload:
                        raise TaskError("idempotency_conflict", "request_id was already used for different content")
                    return {"task":next(t for t in self._snapshots(document,workspace,session_id) if t["id"]==task["id"]), "reused":True}
            identity, timestamp = str(uuid.uuid4()), now()
            self._dependencies(document, identity, dependencies)
            number, namespace = self.numbering.reserve()
            if namespace != document["metadata"]["numbering_namespace"]:
                raise TaskError("storage_error", "Numbering namespace changed")
            task = dict(id=identity, task_number=number, request_id=request_id, initial_payload=payload,
                title=title, description=description, done_when=done_when, priority=priority,
                dependencies=sorted(dependencies), status="pending", revision=1, owner=None,
                block_reason=None, completion_evidence=None, created_at=timestamp, updated_at=timestamp)
            seal_task(task)
            document["tasks"].append(task)
            snapshot = self._event(document,workspace,session_id,None,task,"create",actor)
            return {"task":snapshot, "reused":False, **self._commit(path,document,baseline)}

    def update(self, workspace, task_id, expected_revision, *, session_id, actor, metadata=None,
               action=None, reason=None, evidence=None):
        nonblank(actor, "actor")
        if type(expected_revision) is not int or expected_revision < 1:
            raise TaskError("invalid_input", "expected_revision must be positive")
        if (metadata is None) == (action is None):
            raise TaskError("invalid_input", "Provide metadata OR action")
        with self._mutation(workspace,session_id) as (workspace,session_id,path,document,baseline):
            task = self._resolve(document,task_id)
            before = next(t for t in self._snapshots(document,workspace,session_id) if t["id"]==task["id"])
            if task["revision"] != expected_revision:
                raise TaskError("revision_conflict", "Task changed; read it again", current_revision=task["revision"])
            if metadata is not None:
                if reason is not None or evidence is not None:
                    raise TaskError("invalid_input", "Metadata edits cannot contain action arguments")
                changes = metadata_changes(before,metadata,lambda deps:self._dependencies(document,task["id"],deps))
            else:
                active = lambda:sorted(t["id"] for t in document["tasks"] if task["id"] in t["dependencies"]
                                       and t["status"] in {"in_progress","blocked","completed"})
                changes = action_changes(before,action,actor,reason,evidence,active)
            task.update(changes, revision=expected_revision+1, updated_at=now())
            task["dependencies"].sort()
            seal_task(task)
            snapshot = self._event(document,workspace,session_id,before,task,action or "edit",actor,reason,evidence)
            return {"task":snapshot, **self._commit(path,document,baseline)}

    def _read(self, workspace, session_id):
        workspace, session_id = str(canonical_workspace(workspace)), session_key(session_id)
        ledger = self.numbering.state()
        self.numbering.check_session_locked(ledger,workspace,session_id)
        document, _ = self._load(workspace,session_id,ledger["namespace"],ledger=ledger)
        return workspace,session_id,document

    def list(self, workspace, status="open", priority=None, limit=20, cursor=None, *, session_id):
        page_limit(limit)
        if status not in STATUSES | {"open","all"} or priority is not None and priority not in PRIORITIES:
            raise TaskError("invalid_input", "Unknown filter")
        workspace,session_id,document = self._read(workspace,session_id)
        scope = ["tasks",workspace,session_id,status,priority]
        tasks = sorted((t for t in self._snapshots(document,workspace,session_id)
            if (status=="all" or status=="open" and t["status"] not in TERMINAL or t["status"]==status)
            and (priority is None or t["priority"]==priority)), key=lambda t:(t["created_at"],t["id"]))
        summary = {"matching_total":len(tasks), "by_status":{s:sum(t["status"]==s for t in tasks) for s in sorted(STATUSES)}}
        if cursor is not None:
            position = decode_cursor(cursor,scope)
            if not isinstance(position,list) or len(position)!=2 or any(not isinstance(v,str) for v in position):
                raise TaskError("invalid_cursor", "Invalid task position")
            tasks = [t for t in tasks if (t["created_at"],t["id"])>tuple(position)]
        page = tasks[:limit]
        hidden = {"description","request_id","done_when","completion_evidence","block_reason"}
        return {"tasks":[{k:v for k,v in t.items() if k not in hidden} for t in page], "summary":summary,
            "next_cursor":encode_cursor(scope,[page[-1]["created_at"],page[-1]["id"]]) if len(tasks)>limit else None}

    def get(self, workspace, task_id, event_limit=20, event_cursor=None, *, session_id):
        page_limit(event_limit)
        workspace,session_id,document = self._read(workspace,session_id)
        task = self._resolve(document,task_id)
        scope = ["events",workspace,session_id,task["id"]]
        upper = 9223372036854775807 if event_cursor is None else decode_cursor(event_cursor,scope)
        if type(upper) is not int or upper<1:
            raise TaskError("invalid_cursor", "Invalid event position")
        events = sorted((e for e in document["events"] if e["task_id"]==task["id"] and e["seq"]<upper),key=lambda e:e["seq"],reverse=True)
        page = events[:event_limit]
        return {"task":next(t for t in self._snapshots(document,workspace,session_id) if t["id"]==task["id"]),
                "events":page,"next_event_cursor":encode_cursor(scope,page[-1]["seq"]) if len(events)>event_limit else None}


if __name__ == "__main__":
    import argparse
    from task_management.config import storage_directory
    parser = argparse.ArgumentParser(description="Rebuild generated views from canonical session Markdown")
    parser.add_argument("--workspace",required=True)
    parser.add_argument("--session-id",required=True)
    options = parser.parse_args()
    print(json.dumps(TaskStore(storage_directory()).refresh_views(options.workspace,options.session_id)))
