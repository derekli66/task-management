-- Frozen schema from version 0.1.0, used to verify the real upgrade boundary.
CREATE TABLE tasks (
    id TEXT PRIMARY KEY, workspace TEXT NOT NULL, request_id TEXT NOT NULL,
    initial_payload TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
    done_when TEXT NOT NULL, priority TEXT NOT NULL CHECK(priority IN ('low','normal','high')),
    status TEXT NOT NULL CHECK(status IN ('pending','in_progress','blocked','completed','cancelled')),
    owner TEXT, block_reason TEXT, completion_evidence TEXT,
    revision INTEGER NOT NULL CHECK(revision > 0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(workspace, id), UNIQUE(workspace, request_id));
CREATE TABLE dependencies (
    workspace TEXT NOT NULL, task_id TEXT NOT NULL, depends_on TEXT NOT NULL,
    PRIMARY KEY(workspace, task_id, depends_on), CHECK(task_id != depends_on),
    FOREIGN KEY(workspace, task_id) REFERENCES tasks(workspace, id),
    FOREIGN KEY(workspace, depends_on) REFERENCES tasks(workspace, id));
CREATE TABLE task_events (
    seq INTEGER PRIMARY KEY, workspace TEXT NOT NULL, task_id TEXT NOT NULL,
    revision INTEGER NOT NULL, action TEXT NOT NULL, actor TEXT NOT NULL,
    reason TEXT, evidence TEXT, before_json TEXT, after_json TEXT NOT NULL, created_at TEXT NOT NULL,
    FOREIGN KEY(workspace, task_id) REFERENCES tasks(workspace, id), UNIQUE(task_id, revision));
CREATE INDEX task_listing ON tasks(workspace, created_at, id);
CREATE INDEX reverse_dependencies ON dependencies(workspace, depends_on);
CREATE INDEX event_history ON task_events(workspace, task_id, seq);
PRAGMA user_version=1;
