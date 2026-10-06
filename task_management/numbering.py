"""One-machine number allocation and import receipts with a recoverable journal."""
from contextlib import contextmanager
import copy
from pathlib import Path
import uuid

from task_management.locking import atomic_write, file_lock, read_text, reject_symlinks
from task_management.markdown import decode_metadata, digest, metadata_text
from task_management.rules import TaskError


class Numbering:
    def __init__(self, directory, timeout=3.0):
        self.directory = Path(directory)
        if not self.directory.is_absolute():
            raise TaskError("invalid_configuration", "Numbering directory must be absolute")
        reject_symlinks(self.directory)
        self.timeout, self.fault = timeout, None

    def _stage(self, stage):
        if self.fault:
            self.fault(stage)

    @contextmanager
    def locked(self, *, initialize=False):
        if not self.directory.exists():
            if not initialize:
                raise TaskError("storage_uninitialized", "Run explicit task storage initialization/cutover first")
            self.directory.mkdir(parents=True, exist_ok=True)
        with file_lock(self.directory / "numbering.lock", self.timeout):
            yield

    def _read(self, name):
        path = self.directory / name
        return decode_metadata(read_text(path)) if path.exists() else None

    def _write(self, name, value):
        atomic_write(self.directory / name, metadata_text(name.removesuffix(".md"), value))

    def _active(self, ledger):
        return dict(format_version=1, state="active", namespace=ledger["namespace"],
                    generation=ledger["generation"], ledger_digest=digest(ledger),
                    high_water=ledger["max_reserved_number"])

    @staticmethod
    def _validate(ledger):
        if (not isinstance(ledger,dict) or ledger.get("format_version") != 1
                or not isinstance(ledger.get("namespace"),str)
                or type(ledger.get("generation")) is not int or ledger["generation"] < 1
                or type(ledger.get("max_reserved_number")) is not int
                or not 0 <= ledger["max_reserved_number"] <= 9223372036854775807
                or not isinstance(ledger.get("imports"),dict)):
            raise TaskError("storage_error", "Invalid numbering ledger")
        try:
            uuid.UUID(ledger["namespace"])
            for receipt in ledger["imports"].values():
                if (not isinstance(receipt,dict) or receipt.get("state") not in {"pending","committed"}
                        or not all(isinstance(receipt.get(key),str) for key in
                                   ("workspace","session_id","import_id","candidate_digest","source_snapshot_digest"))
                        or not isinstance(receipt.get("candidate_metadata"),dict)
                        or receipt.get("serializer_version") != 1):
                    raise ValueError("Invalid receipt")
        except (ValueError,TypeError,AttributeError) as exc:
            raise TaskError("storage_error", "Invalid numbering identity/import receipt") from exc

    def _commit(self, previous, proposed):
        pending = dict(format_version=1, state="pending", namespace=proposed["namespace"],
                       previous_digest=digest(previous) if previous is not None else None,
                       proposed_digest=digest(proposed), proposed=proposed)
        self._write("activation.md", pending)
        self._stage("pending")
        self._write("numbering.md", proposed)
        self._stage("ledger")
        self._write("activation.md", self._active(proposed))
        self._stage("active")

    def load_locked(self):
        ledger, marker = self._read("numbering.md"), self._read("activation.md")
        if marker is None:
            raise TaskError("storage_uninitialized" if ledger is None else "storage_error",
                            "Missing activation marker; automatic bootstrap is forbidden")
        if marker.get("format_version") != 1:
            raise TaskError("unsupported_schema", "Unknown numbering activation format")
        if marker.get("state") == "pending":
            proposed = marker.get("proposed")
            if not isinstance(proposed, dict) or digest(proposed) != marker.get("proposed_digest"):
                raise TaskError("storage_error", "Invalid pending numbering journal")
            self._validate(proposed)
            actual = digest(ledger) if ledger is not None else None
            if actual == marker.get("previous_digest"):
                self._write("numbering.md", proposed)
            elif actual != marker.get("proposed_digest"):
                raise TaskError("storage_error", "Ledger diverges from pending journal; original files preserved")
            self._write("activation.md", self._active(proposed))
            ledger, marker = proposed, self._active(proposed)
        self._validate(ledger)
        if marker.get("state") != "active" or marker != self._active(ledger):
            raise TaskError("storage_error", "Missing, damaged or rolled-back numbering ledger")
        return ledger

    def initialize(self, *, baseline=0, source=None):
        if type(baseline) is not int or not 0 <= baseline <= 9223372036854775807:
            raise TaskError("invalid_input", "Invalid numbering baseline")
        with self.locked(initialize=True):
            if (self.directory / "activation.md").exists() or (self.directory / "numbering.md").exists():
                ledger = self.load_locked()
                if ledger.get("source") != source:
                    raise TaskError("storage_error", "Storage already initialized with a different source")
                return ledger
            ledger = dict(format_version=1, namespace=str(uuid.uuid4()), generation=1,
                          max_reserved_number=baseline, source=source, imports={})
            self._commit(None, ledger)
            return ledger

    def state(self):
        with self.locked():
            return copy.deepcopy(self.load_locked())

    def change_locked(self, ledger, change):
        proposed = copy.deepcopy(ledger)
        change(proposed)
        proposed["generation"] += 1
        self._validate(proposed)
        self._commit(ledger, proposed)
        return proposed

    def reserve(self):
        with self.locked():
            ledger = self.load_locked()
            if ledger["max_reserved_number"] == 9223372036854775807:
                raise TaskError("storage_error", "Task number space exhausted")
            ledger = self.change_locked(ledger, lambda value:value.update(
                max_reserved_number=value["max_reserved_number"] + 1))
            return f"#{ledger['max_reserved_number']}", ledger["namespace"]

    def check_session_locked(self, ledger, workspace, session_id):
        for receipt in ledger["imports"].values():
            if receipt["workspace"] == workspace and receipt["session_id"] == session_id and receipt["state"] != "committed":
                raise TaskError("migration_in_progress", "Finish the pending import in this session before mutation")
