# Local Task Management

A personal Codex plugin with four MCP tools, a usage skill, and SQLite storage. Task lists survive server restarts and new conversations. Each project/worktree has its own namespace.

## Install

Requires Python 3.13+ and Codex with local plugin support. Select a 3.13+ interpreter explicitly if your shell's `python3` changes between projects.

```sh
python3 scripts/install_runtime.py
```

This installs the pinned dependencies into `~/.local/share/task-management/runtime/<lock-id>-py<version>/` and generates the machine-local `.mcp.json`. It never installs this package globally. The generated interpreter path is absolute. The server starts from the installed plugin cache using `cwd: "."`; legacy Codex does not expand `${PLUGIN_ROOT}` inside MCP arguments.

For the personal marketplace created with this plugin:

```sh
codex plugin add task-management@personal
```

Start a new Codex thread and use `/mcp` to confirm that the plugin's `tasks` server exposes `task_create`, `task_list`, `task_get`, and `task_update`. Try:

> 使用 task-management，列出這個專案尚未完成的任務。

> 把已核准的實作計畫建立成 TODO，列出完成條件與相依關係。

New source checkouts also need a personal marketplace entry; use the Codex plugin-creator scaffold/marketplace helper for the selected source location. The machine-local marketplace and generated `.mcp.json` are not Git artifacts.

## Tool contract

Every call requires an absolute `workspace` directory. Use the Git worktree root, not a varying subdirectory. Symlink aliases resolve to the same workspace; distinct worktrees and clones are separate. Renaming/moving a workspace creates a new namespace; automatic relocation is not implemented.

| Tool | Main fields |
|---|---|
| task_create | workspace, request_id, title, done_when; optional description, priority, dependencies, actor |
| task_list | workspace; optional status (`open` default / `all` / specific state), priority, limit, cursor |
| task_get | workspace, task_id; optional event_limit, event_cursor |
| task_update | workspace, task_id, expected_revision, actor; metadata OR action, with action-specific reason/evidence |

Task and event pages default to 20 records, maximum 100. Follow `next_cursor` / `next_event_cursor`; a cursor is bound to its workspace, task/filter query. Pagination is a live view, not a snapshot across multiple calls. Events are newest first. Timestamps are UTC in `YYYYMMDD_hhmmss` format.

An example creation request (replace the workspace with your real project root):

```json
{
  "workspace": "/absolute/project/root",
  "request_id": "unique-creation-request",
  "title": "Implement the approved feature",
  "done_when": "Acceptance checks pass and required review is recorded"
}
```

Keep `request_id` and the creation payload for retries. Repeating them returns the same task, at its current revision. Reusing a key for different content fails. Different keys may intentionally create tasks with the same title.

States: `pending`, `in_progress`, `blocked`, `completed`, `cancelled`. Actions: `start`, `block`, `release`, `complete`, `cancel`, `reopen`. Start requires completed prerequisites; complete additionally requires nonblank evidence. Block/release/cancel/reopen require a reason. Blocked work continues via release then start. Dependencies and `done_when` are editable only while pending. Terminal tasks must reopen before any edits. Reopening a completed prerequisite is refused while a dependent is in progress, blocked or completed.

Owner is attribution, not access control. Starting a task atomically prevents duplicate starts; it cannot prevent a caller from working outside the tool. A stopped session's task stays in progress until someone checks it and explicitly releases it with a reason. A revision conflict means re-read before deciding, not silently retry with a newer revision.

Errors set MCP `isError` and domain errors include structured `error.code`, `message`, and `details`. Examples: `revision_conflict`, `dependencies_unfinished`, `active_dependents`, `dependency_cycle`, `not_found`, `idempotency_conflict`, `unsupported_schema`, `database_busy`. SDK argument validation errors are returned as MCP error text. Rejected operations do not increment revision or add events.

## Storage and updates

Default database: `~/.local/share/task-management/tasks.sqlite3`. `TASK_MANAGEMENT_DB` may select a different absolute path when launching the server (used by tests). No network service or background scheduler is started. Database storage is local; returned task text enters the Codex conversation.

The database is outside both the source checkout and plugin cache. Uninstall/reinstall does not remove it. Schema version 1 is accepted; unknown versions and nonempty unversioned databases are rejected without reset. Every operation checks the schema version. No schema migration is included. Use a local disk, not a network-share SQLite WAL database. Back up using SQLite's backup API, or shut down all writers and checkpoint before a filesystem backup; copying just the DB while WAL writers are running is insufficient.

To update: run `install_runtime.py` if dependencies changed, use plugin-creator's cachebuster helper, reinstall `task-management@personal`, then open a new thread. Runtime environments are keyed by lock contents and Python major/minor version, so updating dependencies does not replace the environment used by an existing server. MCP startup never downloads packages.

No permanent-delete tool, parent/child hierarchy, native TODO panel, automatic scheduling, or cross-machine sync is provided. Completion evidence is a caller's record; the tool does not certify tests, commits, or human acceptance.

## Verify

Run with the interpreter printed by `install_runtime.py`, from this source directory:

```sh
<runtime-python> -m unittest discover -s tests -v
```

Tests use temporary databases/workspaces, real subprocesses, competing SQLite writers, a killed uncommitted writer, actual Git worktrees, and real MCP stdio clients in both legacy and automatic negotiation modes. The acceptance record additionally documents new-thread discovery and calls through the actual Codex app-server, without submitting a model turn.
