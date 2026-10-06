"""Shared task identity, validation and lifecycle constants."""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path


STATUSES = {"pending", "in_progress", "blocked", "completed", "cancelled"}
PRIORITIES = {"low", "normal", "high"}
TERMINAL = {"completed", "cancelled"}
TRANSITIONS = {
    "start": ({"pending"}, "in_progress"),
    "block": ({"in_progress"}, "blocked"),
    "release": ({"in_progress", "blocked"}, "pending"),
    "complete": ({"in_progress"}, "completed"),
    "cancel": ({"pending", "in_progress", "blocked"}, "cancelled"),
    "reopen": (TERMINAL, "pending"),
}
class TaskError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def nonblank(value, name):
    if not isinstance(value, str) or not value.strip():
        raise TaskError("invalid_input", f"{name} must be nonblank text")
    return value


def workspace_key(value):
    try:
        path = Path(value)
        if not path.is_absolute() or not path.is_dir():
            raise ValueError()
        return str(path.resolve(strict=True))
    except (OSError, ValueError, TypeError, RuntimeError):
        raise TaskError("invalid_workspace", "workspace must be an existing absolute directory") from None


def now():
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def encode_cursor(scope, position):
    return base64.urlsafe_b64encode(json.dumps([scope, position]).encode()).decode()


def decode_cursor(cursor, scope):
    try:
        actual_scope, position = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if actual_scope != scope:
            raise ValueError()
        return position
    except (ValueError, TypeError, UnicodeError):
        raise TaskError("invalid_cursor", "Cursor does not match this query") from None


def page_limit(limit):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise TaskError("invalid_input", "page size must be an integer from 1 to 100")


def metadata_changes(task, metadata, validate_dependencies):
    editable = {"title", "description", "priority", "done_when", "dependencies"}
    if not isinstance(metadata, dict) or not metadata or set(metadata) - editable:
        raise TaskError("invalid_input", "metadata must contain supported editable fields")
    if task["status"] in TERMINAL or (task["status"] != "pending" and
            {"done_when", "dependencies"} & metadata.keys()):
        raise TaskError("invalid_transition", "Reopen or release this task before changing these fields")
    for key, value in metadata.items():
        if key in {"title", "done_when"}:
            nonblank(value, key)
        elif key == "description" and not isinstance(value, str):
            raise TaskError("invalid_input", "description must be text")
        elif key == "priority" and value not in PRIORITIES:
            raise TaskError("invalid_input", "Unknown priority")
        elif key == "dependencies":
            validate_dependencies(value)
    return dict(metadata)

def action_changes(task, action, actor, reason, evidence, active_dependents):
    if action not in TRANSITIONS:
        raise TaskError("invalid_input", "Unknown action")
    allowed, target = TRANSITIONS[action]
    if task["status"] not in allowed:
        raise TaskError("invalid_transition", f"Cannot {action} a {task['status']} task")
    needs_reason = action in {"block", "release", "cancel", "reopen"}
    if needs_reason:
        nonblank(reason, "reason")
    elif reason is not None:
        raise TaskError("invalid_input", "This action does not accept a reason")
    if action == "complete":
        nonblank(evidence, "evidence")
    elif evidence is not None:
        raise TaskError("invalid_input", "Only complete accepts evidence")
    if action in {"start", "complete"} and not task["ready"]:
        raise TaskError("dependencies_unfinished", "Complete the prerequisites first",
                        task_ids=task["unfinished_dependencies"])
    if action == "reopen" and task["status"] == "completed":
        dependent_ids = active_dependents()
        if dependent_ids:
            raise TaskError("active_dependents", "Resolve these dependent tasks before reopening",
                            task_ids=dependent_ids)
    changes = {"status": target}
    if action == "start":
        changes["owner"] = actor
    if action == "block":
        changes["block_reason"] = reason
    else:
        changes["block_reason"] = None
    if action in {"release", "cancel", "reopen"}:
        changes["owner"] = None
    if action == "complete":
        changes["completion_evidence"] = evidence
    if action == "reopen":
        changes["completion_evidence"] = None
    return changes
