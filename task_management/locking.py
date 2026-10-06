"""Bounded cooperative locks and durable, same-directory file replacement."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import tempfile
import time

from task_management.rules import TaskError


def reject_symlinks(path):
    """Check all existing components, including broken final symlinks."""
    path = Path(path)
    for component in (path, *path.parents):
        if component.is_symlink():
            raise TaskError("storage_error", "Task storage must not traverse symlinks")


@contextmanager
def file_lock(path, timeout=3.0):
    path = Path(path)
    reject_symlinks(path)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TaskError("database_busy", "Task storage is busy; read state before retrying")
                time.sleep(0.01)
        yield
    finally:
        os.close(descriptor)


def atomic_write(path, text, *, before_replace=None):
    path = Path(path)
    reject_symlinks(path)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if before_replace:
            before_replace()
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        # Only the exact temporary file created by this operation is removed.
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_text(path):
    reject_symlinks(path)
    try:
        with Path(path).open(encoding="utf-8", newline="") as stream:
            return stream.read()
    except UnicodeError as exc:
        raise TaskError("storage_error", "Task file is not valid UTF-8; original file preserved") from exc
