# Local Task Management

A personal Codex plugin with four MCP tools, a usage skill, and SQLite storage. Task lists survive server restarts and new conversations. Each project/worktree has its own namespace.

## Install

Requires Python 3.13+ and Codex with local plugin support. Select a 3.13+ interpreter explicitly if your shell's `python3` changes between projects.

```sh
python3 scripts/install_runtime.py
```

This installs the pinned dependencies into `~/.local/share/task-management/runtime/<lock-id>-py<version>/`, generates the machine-local `.mcp.json`, and initializes event-driven task reminders. It never installs this package globally. The generated interpreter path is absolute. The server starts from the installed plugin cache using `cwd: "."`; legacy Codex does not expand `${PLUGIN_ROOT}` inside MCP arguments.

To additionally show a compact unfinished-task reminder after every assistant turn:

```sh
python3 scripts/install_runtime.py --reminder-mode always
```

Switch back with `--reminder-mode lifecycle`. The per-machine preference is stored at `~/.local/share/task-management/settings.json`; it is not workspace task data.

For the personal marketplace created with this plugin:

```sh
codex plugin add task-management@personal
```

Start a new Codex thread and use `/mcp` to confirm that the plugin's `tasks` server exposes `task_create`, `task_list`, `task_get`, and `task_update`. Try:

> 使用 task-management，列出這個專案尚未完成的任務。

> 把已核准的實作計畫建立成 TODO，列出完成條件與相依關係。

## Workspace reminders

Trusted bundled hooks read the same local database without creating or migrating it. At Codex `startup`, `resume`, `/clear`, and compaction, a warning-styled summary shows the exact unfinished count and up to five tasks. Pending work is visibly grouped as `Ready` then `Waiting`, preserving the actionable-first order instead of sorting by task number. The default `lifecycle` mode also redraws that summary after a proven successful `task_create` or `task_update` in the active workspace. Unrelated turns, failed mutations, and idempotent create retries do not redraw it. Closing the final task emits `Tasks · 0 unfinished`; this supersedes an earlier warning semantically, although the host may retain the earlier message in scrollback.

In `always` mode, the `Stop` hook additionally emits the summary after every assistant turn. A mutation turn can therefore show both its mutation redraw and its end-of-turn redraw. This opt-in mode is intentionally noisier; the default does not redraw on every turn. Parallel mutations may each show the same final workspace snapshot.

The hooks must be reviewed and trusted before Codex runs them. They use a two-second timeout and fail quietly when the database is absent, busy, unsupported, or when a mutation result cannot be proven successful. Stored titles are sanitized and bounded for display. Session-start context contains only the unfinished count and a direction to query the tools; mutation redraws return only `systemMessage` and do not start another model turn or add per-turn developer context. The bounded `workspace_overview` in successful mutation tool output is model-visible, but it replaces the extra `task_list` call that older versions required.

Codex CLI renders the hook `systemMessage` as a visible warning. Codex local projects in the ChatGPT desktop app can run the installed MCP server and hooks, but the current desktop UI may not render that warning; the skill therefore also puts the bounded mutation summary at the end of the assistant response. Neither surface gets a pinned panel. The local SQLite database is unavailable to ChatGPT web/mobile, Codex cloud, regular chats without a local repo workspace, and the IDE extension. See the official [Codex hooks](https://learn.chatgpt.com/docs/hooks) and [plugin surfaces](https://learn.chatgpt.com/docs/plugins) documentation.

New source checkouts also need a personal marketplace entry; use the Codex plugin-creator scaffold/marketplace helper for the selected source location. The machine-local marketplace and generated `.mcp.json` are not Git artifacts.

## Tool contract

Every call requires an absolute `workspace` directory. Use the Git worktree root, not a varying subdirectory. Symlink aliases resolve to the same workspace; distinct worktrees and clones are separate. Renaming/moving a workspace creates a new namespace; automatic relocation is not implemented.

Every task has a human-readable `task_number` such as `"#1"`, `"#2"`, `"#3"` and the original UUID `id`. Numbers are immutable and increase across all workspaces in **one database**. Completed, cancelled or reopened tasks retain their numbers. A retry of the same creation request returns the original number. Separate databases/machines can have the same numbers; the UUID remains the durable internal identity. Restoring an older backup also restores its numbering state, so never merge references from divergent database histories as though they were one sequence.

Pass either the UUID or the exact `"#N"` string as `task_id` to `task_get` and `task_update`. Workspace checks and `expected_revision` still apply. Numbers are positive ASCII integers without leading zeros, up to SQLite's signed 64-bit limit. They are not editable metadata. Dependency arrays and error-detail task IDs remain UUIDs: look up `#N` first and use its returned `id` for dependencies. Listing/pagination order is unchanged (creation timestamp, then UUID), not numeric order.

| Tool | Main fields |
|---|---|
| task_create | workspace, request_id, title, done_when; optional description, priority, dependencies, actor |
| task_list | workspace; optional status (`open` default / `all` / specific state), priority, limit, cursor; returns exact filtered summary |
| task_get | workspace, task_id; optional event_limit, event_cursor |
| task_update | workspace, task_id, expected_revision, actor; metadata OR action, with action-specific reason/evidence |

Successful `task_create` and `task_update` responses include a post-commit `workspace_overview`: exact unfinished counts plus at most five sanitized unfinished task titles. It may reflect a newer concurrent commit, which is safe for presentation. Older servers omit this field; callers then use `task_list(status="open")`. A failed mutation never includes the overview.

Task and event pages default to 20 records, maximum 100. Follow `next_cursor` / `next_event_cursor`; a cursor is bound to its workspace, task/filter query. `task_list.summary.matching_total` and `summary.by_status` apply the workspace/status/priority filters before pagination, so they do not shrink on later pages. Pagination remains a live view, not a snapshot across multiple calls. Events are newest first. Timestamps are UTC in `YYYYMMDD_hhmmss` format.

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

The database is outside both the source checkout and plugin cache. Uninstall/reinstall does not remove it. New databases use schema 2. On first open, schema 1 upgrades atomically after creating a private SQLite backup beside the database named `<database>.schema1.<UTC timestamp>.<unique suffix>.sqlite3`. The writer lock covers both backup and migration, so no acknowledged write can slip between them. Backup failure aborts the upgrade. Unknown versions and nonempty unversioned databases are rejected without reset; every operation checks the schema version.

Existing tasks receive numbers in creation-event order, including completed/cancelled tasks. The migration does not change UUIDs, revisions, task fields, dependencies or historical event JSON. Old historical snapshots intentionally have no `task_number`; current snapshots and new events do. Missing creation events abort migration instead of inventing an order. No automatic downgrade is provided. Old server processes reject schema 2 with `unsupported_schema`; reconnect using the updated plugin/new thread, rather than resetting the database. Keep the backup, and do not restore over newer work without accounting for every post-upgrade change.

Use a local disk, not a network-share SQLite WAL database. For routine backups, use SQLite's backup API, or shut down all writers and checkpoint before a filesystem backup; copying just the DB while WAL writers are running is insufficient.

To update: run `install_runtime.py` if dependencies changed, use plugin-creator's cachebuster helper, reinstall `task-management@personal`, then open a new thread. Runtime environments are keyed by lock contents and Python major/minor version, so updating dependencies does not replace the environment used by an existing server. MCP startup never downloads packages.

No permanent-delete tool, parent/child hierarchy, native pinned TODO panel, automatic scheduling, or cross-machine sync is provided. Task reminders are Codex warning messages plus a bounded assistant-response fallback, not custom host chrome. Completion evidence is a caller's record; the tool does not certify tests, commits, or human acceptance.

## Verify

Run with the interpreter printed by `install_runtime.py`, from this source directory:

```sh
<runtime-python> -m unittest discover -s tests -v
```

Tests use temporary databases/workspaces, real subprocesses, competing SQLite writers, a killed uncommitted writer, actual Git worktrees, and real MCP stdio clients in both legacy and automatic negotiation modes. The acceptance record additionally documents new-thread discovery and calls through the actual Codex app-server, without submitting a model turn.
