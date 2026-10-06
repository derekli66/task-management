"""Versioned Markdown with ordinary, losslessly framed research notes."""
import hashlib
import json
import re
import uuid
from pathlib import Path

from task_management.rules import PRIORITIES, STATUSES, TaskError


FIELDS = ("id", "task_number", "request_id", "initial_payload", "title", "description",
          "done_when", "priority", "status", "owner", "block_reason", "completion_evidence",
          "revision", "created_at", "updated_at", "dependencies")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def text_digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def projection(task):
    return {key: sorted(task[key]) if key == "dependencies" else task[key] for key in FIELDS}


def seal_task(task):
    task["integrity_digest"] = digest(projection(task))


def fence(kind, value):
    return f"```json task-management-{kind}\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```\n"


def metadata_text(title, value):
    return f"# {title}\n\n" + fence("metadata", value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class Reader:
    def __init__(self, text):
        self.text, self.position = text, 0

    def take(self, expected):
        if not self.text.startswith(expected, self.position):
            raise ValueError("invalid Markdown framing")
        self.position += len(expected)

    def json(self, kind):
        self.take(f"```json task-management-{kind}\n")
        end = self.text.index("\n```\n", self.position)
        value = json.loads(self.text[self.position:end], object_pairs_hook=_unique_object)
        self.position = end + len("\n```\n")
        return value


def decode_metadata(text):
    try:
        reader = Reader(text)
        end = text.index("\n\n")
        if not text.startswith("# "):
            raise ValueError()
        reader.position = end + 2
        value = reader.json("metadata")
        if reader.position != len(text) or not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError, KeyError) as exc:
        raise TaskError("storage_error", "Invalid Markdown metadata; original file preserved") from exc


def heading(task):
    title = re.sub(r"[\x00-\x1f\x7f]", " ", task["title"])
    return f"\n## {task['task_number']}：{title}\n\n"


def encode_document(document):
    metadata = {**document["metadata"], "events_digest":digest(document["events"])}
    chunks = ["# Session TODO\n\n", fence("session", metadata)]
    for task in document["tasks"]:
        record = {key:value for key, value in task.items() if key != "description"}
        record["description_length"] = len(task["description"])
        events = [event for event in document["events"] if event["task_id"] == task["id"]]
        chunks.extend([heading(task), fence("task", record), "\n### 工作筆記\n\n",
                       task["description"], "\n\n<details>\n<summary>事件歷史</summary>\n\n",
                       fence("events", events), "\n</details>\n"])
    return "".join(chunks)


def validate_document(document):
    """Validate persisted data, never compare derived readiness to old events."""
    metadata = document["metadata"]
    if not isinstance(metadata["workspace"],str) or not Path(metadata["workspace"]).is_absolute():
        raise ValueError("invalid workspace scope")
    from task_management.config import session_key
    session_key(metadata["session_id"])
    uuid.UUID(metadata["numbering_namespace"])
    uuid.UUID(metadata["document_id"])
    if (metadata.get("format_version") != 1 or type(metadata.get("generation")) is not int
            or metadata["generation"] < 0 or not isinstance(document["tasks"],list)
            or not isinstance(document["events"],list)):
        raise ValueError("invalid session metadata")
    ids, numbers, requests, sequences = set(), set(), set(), set()
    for task in document["tasks"]:
        uuid.UUID(task["id"])
        number = task["task_number"]
        if not re.fullmatch(r"#[1-9][0-9]{0,18}", number) or int(number[1:]) > 9223372036854775807:
            raise ValueError("invalid task number")
        if (task["id"] in ids or number in numbers or task["request_id"] in requests
                or type(task["revision"]) is not int or task["revision"] < 1
                or task["status"] not in STATUSES or task["priority"] not in PRIORITIES):
            raise ValueError("invalid or duplicate task")
        for key in ("title", "done_when", "request_id", "initial_payload", "created_at", "updated_at"):
            if not isinstance(task[key], str) or not task[key].strip():
                raise ValueError("invalid task field")
        if not isinstance(task["description"], str):
            raise ValueError("invalid notes")
        for key in ("owner", "block_reason", "completion_evidence"):
            if task[key] is not None and not isinstance(task[key], str):
                raise ValueError("invalid nullable field")
        json.loads(task["initial_payload"])
        dependencies = task["dependencies"]
        if (not isinstance(dependencies, list) or any(not isinstance(x, str) for x in dependencies)
                or len(set(dependencies)) != len(dependencies)):
            raise ValueError("invalid dependencies")
        if digest(projection(task)) != task["integrity_digest"]:
            raise ValueError("record changed outside a revision-controlled update")
        ids.add(task["id"]); numbers.add(number); requests.add(task["request_id"])
    tasks = {task["id"]:task for task in document["tasks"]}
    counts = {identity:len(task["dependencies"]) for identity,task in tasks.items()}
    dependents = {identity:[] for identity in tasks}
    for task in tasks.values():
        for dependency in task["dependencies"]:
            if dependency not in tasks:
                raise ValueError("missing dependency")
            dependents[dependency].append(task["id"])
    # Iterative graph validation also handles long legitimate dependency chains.
    ready = [identity for identity,count in counts.items() if count == 0]
    visited = 0
    while ready:
        visited += 1
        for identity in dependents[ready.pop()]:
            counts[identity] -= 1
            if counts[identity] == 0:
                ready.append(identity)
    if visited != len(tasks):
        raise ValueError("cyclic dependency")
    revisions = {identity:[] for identity in ids}
    for event in document["events"]:
        if (type(event["seq"]) is not int or event["seq"] < 1 or event["seq"] in sequences
                or event["task_id"] not in tasks or type(event["revision"]) is not int
                or event["action"] not in {"create","edit","start","block","release","complete","cancel","reopen"}
                or not isinstance(event["after"], dict)
                or event["before"] is not None and not isinstance(event["before"], dict)):
            raise ValueError("invalid event")
        if (not isinstance(event.get("actor"),str) or not event["actor"].strip()
                or not isinstance(event.get("created_at"),str)
                or not isinstance(event.get("workspace"),str)
                or any(event.get(key) is not None and not isinstance(event[key],str) for key in ("reason","evidence"))):
            raise ValueError("invalid event attribution")
        sequences.add(event["seq"])
        revisions[event["task_id"]].append(event["revision"])
    for identity, task in tasks.items():
        history = sorted(revisions[identity])
        if len(history) != task["revision"] or any(value != index for index,value in enumerate(history,1)):
            raise ValueError("incomplete event history")


def decode_document(text):
    try:
        reader = Reader(text)
        reader.take("# Session TODO\n\n")
        metadata = reader.json("session")
        if metadata["format_version"] != 1:
            raise TaskError("unsupported_schema", "Unknown Markdown format; reload compatible tools")
        expected_events = metadata.pop("events_digest")
        if type(metadata["generation"]) is not int or metadata["generation"] < 0:
            raise ValueError("invalid generation")
        tasks, events = [], []
        while reader.position < len(text):
            reader.take("\n## ")
            end = text.index("\n\n", reader.position)
            reader.position = end + 2
            record = reader.json("task")
            reader.take("\n### 工作筆記\n\n")
            length = record.pop("description_length")
            if type(length) is not int or length < 0:
                raise ValueError("invalid note length")
            record["description"] = text[reader.position:reader.position + length]
            reader.position += length
            reader.take("\n\n<details>\n<summary>事件歷史</summary>\n\n")
            events.extend(reader.json("events"))
            reader.take("\n</details>\n")
            tasks.append(record)
        events.sort(key=lambda event:event["seq"])
        if digest(events) != expected_events:
            raise ValueError("event history changed")
        document = dict(metadata=metadata, tasks=tasks, events=events)
        validate_document(document)
        return document
    except TaskError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, RecursionError) as exc:
        raise TaskError("storage_error", "Invalid task Markdown; original file preserved", reason=str(exc)) from exc
