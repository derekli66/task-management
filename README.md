# Local Task Management

A personal Codex plugin with four local MCP tools. Version 0.2 replaces normal SQLite CRUD with session-scoped Markdown, retaining task numbers, UUIDs, dependencies, revisions, idempotent creation, evidence and full event history.

Each real Codex session owns a separate TODO within its workspace. Notes can contain the investigated cause, suggested fix, evidence and next step—not just a title. Resuming the same session restores its notes; a new session does not silently inherit another list. This is a breaking tool-contract change: every call now requires `session_id`.

## Storage

```text
<workspace>/.codex/task-management/sessions/<real-session-id>/
  tasks.md       canonical: ALL tasks, ordinary Markdown notes, metadata and history
  index.md       generated unfinished view, Ready before Waiting
  archive.md     generated completed/cancelled view
  .lock          stable cooperative writer lock

~/.local/share/task-management/
  numbering.md   machine-wide number reservations and import receipts
  activation.md  activation witness and recoverable pending ledger commit
  numbering.lock
  cutover.md     legacy cutover journal, source identity and original file modes
  legacy-<uuid>.sqlite3  read-only consistent backup (legacy cutover only)
  tasks.sqlite3  retained original legacy database
```

`tasks.md` is the only source for task state. Moving a task to completed updates it and its event in that ONE file; archive/index are rebuilt afterward. A failed view refresh returns committed success with a warning. Neither view is independently editable.

Before writing private task data, the tool installs and verifies `/.codex/task-management/` in the local Git `info/exclude`. Tracked task files cause a refusal, not automatic removal from Git. Worktrees use Git's resolved exclude path. Non-Git directories get a self-ignoring nested `.gitignore`, so later `git init` still excludes the data. Deliberate `git add -f` can bypass exclusions.

`TASK_MANAGEMENT_DATA` selects an absolute, canonical machine-data directory. `TASK_MANAGEMENT_DB` is only used by explicit migration scripts; normal MCP tools never open SQLite. No daemon, network service or scheduler is introduced. Returned task text still enters the model conversation.

## Install and activate

Requires Python 3.13+, Git for Git workspaces, local disk, and a supported local Codex client. Locking uses Unix `fcntl.flock`; Windows and network filesystems are not supported in this version. Legacy cutover additionally requires `lsof` and an unprivileged account capable of enforcing the read-only fence.

```sh
python3 scripts/install_runtime.py
```

Use the 3.13+ interpreter explicitly. Setup installs the locked dependencies into the machine-local runtime and generates `.mcp.json`; it NEVER initializes numbering, imports tasks or changes the old DB. Use the printed runtime interpreter for the following commands (shown as `<runtime-python>`).

For a genuinely fresh install, with no old DB at the configured/default path:

```sh
<runtime-python> scripts/cutover_tasks.py --fresh
```

Missing ledger/activation in ordinary CRUD is an error, never a reason to restart numbering at #1. If either file exists but is corrupt/missing, preserve it and recover a verified backup; `--fresh` does not reset it. Do not point a fresh initializer at an alternate absent DB to sidestep an existing legacy source.

For an existing personal marketplace, install the new source version only after following the legacy cutover procedure below:

```sh
codex plugin remove task-management@personal
codex plugin add task-management@personal
```

Then restart the ChatGPT desktop app or start/reconnect the CLI, and use a new thread to load the new tool schemas and skill. The running old process is not updated by changing the source checkout. Marketplace paths are relative to the marketplace root; local plugin installation/cache behavior is described in the [official local-plugin guidance](https://developers.openai.com/plugins/build/plugins#install-a-local-plugin-manually). CLI commands are documented in [developer commands](https://learn.chatgpt.com/docs/developer-commands#codex-plugin).

## Upgrade an existing SQLite installation

The old DB knows workspaces, NOT historical session IDs. Choose one real destination session for the complete task graph of each old workspace. Do not guess how tasks belonged to prior chats or split a dependency graph automatically.

1. Preview the old source while it is readable; the exporter uses a read-only schema-2 transaction and never auto-upgrades schema 1:

   ```sh
   <runtime-python> scripts/export_legacy_tasks.py --preview
   ```

   An idle WAL source without readable sidecars can be unavailable to read-only SQLite on some runtimes. Do not reopen it writable just to preview; after fencing, preview the verified `snapshot_path` reported by cutover with `--db /absolute/backup.sqlite3`.

2. Save handoffs, disable/close ALL old launchers and MCP connections—including other CLI/App sessions. This repo's script does not kill them. The operator's `--writers-stopped` confirmation is necessary but not sufficient: `lsof` must also show that all source DB/WAL/SHM descriptors are closed. Do not let an old launcher reconnect during cutover.

3. Run the explicit cutover:

   ```sh
   <runtime-python> scripts/cutover_tasks.py --writers-stopped
   ```

   It obtains a writer guard without changing task rows, creates/verifies a consistent read-only backup, durably records original modes, fences the old DB and existing sidecars, CLOSES the guard, and verifies a new old writer fails with `SQLITE_READONLY` (not merely busy). Only then does it initialize numbering from both task numbers and SQLite's high-water sequence. Interrupted cutover uses `cutover.md`; retry the same source, never delete the journal. Failure before activation leaves preserved files and an explicit error; a partially applied fence may remain.

4. Install/reload the new plugin as above. The SessionStart hook supplies the real session ID even if the list is empty or storage unavailable. Import a selected workspace into that real session:

   ```sh
   <runtime-python> scripts/export_legacy_tasks.py \
     --workspace /absolute/worktree/root --session-id REAL_HOST_SESSION_ID
   ```

   Add `--db /absolute/original/tasks.sqlite3` if the legacy source is not the default. The importer verifies the bound source identity and reads its consistent cutover backup. It imports all statuses and preserves UUIDs, original numbers, revisions, initial payloads, dependencies, research notes, evidence and historical snapshots, including sparse old JSON.

5. Verify task/event counts, full notes, dependencies and `git check-ignore` before doing new work. Import receipts reserve one destination BEFORE writing its task file. A crash can be recovered only in that destination; normal mutations there remain blocked while the receipt is pending. A committed retry does not overwrite subsequent legitimate updates. A different destination/source or nonempty target is refused.

No real DB is automatically cut over by an install, tool call or test. The original DB's task rows are retained; cutover intentionally changes its file permissions. The backup and journals stay outside the plugin cache.

### Recovery and rollback boundaries

The numbering ledger uses a pending activation journal before every replacement, so interrupted reservations replay without reusing numbers. Missing/mismatched active files and single-file stale backups are refused. Simultaneously restoring two self-consistent OLD global files cannot reveal every later reservation; reconcile all session files and receipts before any backup rollback. Never merge divergent namespaces as one numbering history.

Reinstalling 0.1 does NOT import post-cutover Markdown changes back into SQLite. Before rollback, stop all Markdown writers, preserve their files, and explicitly reconcile any new work. Original permission modes are recorded in `cutover.md`; restoring them re-enables old writers and is a deliberate rollback operation, not part of uninstall. Never automatically chmod the original source writable after a failed test or cache update.

## Tool contract

Every call requires absolute canonical `workspace` and the real host `session_id`. Git subdirectories resolve to the worktree root; workspace aliases canonicalize, but task storage symlinks are rejected. Document scope is checked against the call: copying/moving a document to another canonical root is not an automatic transfer; preserve it and request explicit relocation support. Different clones/worktrees/sessions are independent. No shared current-session pointer, implicit inheritance or cross-session transfer exists.

| Tool | Main fields |
|---|---|
| task_create | workspace, session_id, request_id, title, done_when; optional description, priority, dependencies, actor |
| task_list | workspace, session_id; optional status (`open` / `all` / specific), priority, limit, cursor |
| task_get | workspace, session_id, task_id; optional event_limit, event_cursor |
| task_update | workspace, session_id, task_id, expected_revision, actor; metadata OR action, reason/evidence |

Tasks have immutable `task_number` (`#N`) plus UUID `id`. Numbers increase globally across this machine's sessions/workspaces, up to signed 64-bit maximum; harmless gaps after failed creation are allowed. Separate machines/namespaces can have the same numbers. `task_get/update` accept #N or UUID; dependencies require UUIDs in this same scope. No deletion tool exists.

`request_id` is unique within workspace/session. Retry the SAME key and original content after a lost creation response; it returns the existing task at its current revision. Reusing it for different content is an error. Every update requires `expected_revision`; on conflict read again and decide, never silently overwrite. Actor/owner is attribution, not authorization.

States are `pending`, `in_progress`, `blocked`, `completed`, `cancelled`. Actions are `start`, `block`, `release`, `complete`, `cancel`, `reopen`. Start/complete need completed prerequisites; complete needs evidence. Block/release/cancel/reopen need a reason. Blocked work resumes by release then start. Dependencies and `done_when` can change only while pending; terminal tasks must reopen for edits. A completed prerequisite cannot reopen under an active, blocked or completed dependent.

Pages default to 20, maximum 100. Counts in `summary` describe the complete filtered result, not page length. Cursors bind workspace AND session and query/task. Events are newest first. Listing uses creation timestamp then UUID; overview instead prioritizes active/blocked work and Ready before Waiting, then priority. Timestamps use UTC `YYYYMMDD_hhmmss`.

Successful mutations include the current session's bounded `workspace_overview` (compatibility field name retained). A view failure includes a warning but does not turn a committed task into a failed mutation. Rejected operations do not change task revisions or events. Errors include `revision_conflict`, `idempotency_conflict`, `dependencies_unfinished`, `active_dependents`, `storage_uninitialized`, `migration_in_progress`, `migration_conflict`, `unsupported_schema`, `storage_error`, and `database_busy` (a cooperative lock timeout).

Successful create/update responses also include `display_summary`, a ready-to-render Markdown string of at most 2,200 characters. It confirms the affected task's number, literal title and actual status, then exact unfinished/pending/in_progress/blocked counts and up to five open tasks in the overview's Ready/Waiting order. Each row has an explicit status; `… +N more` accounts for undisplayed open tasks. Only presentation titles are sanitized, bounded to 100 characters and Markdown-escaped; stored research notes and titles are unchanged.

The existing JSON text block and structured result remain identical objects; the new field is additive, not a replacement for either. Read/error results do not receive success summaries. Idempotent create reuse describes the existing task. A post-commit overview failure yields an explicit unavailable summary, not zero. A formatter failure preserves committed success and existing warnings, omits `display_summary` and adds a fixed presentation warning; it is not a reason to repeat the mutation.

## Research notes and editing

`description` holds complete Markdown notes: problem, investigated cause and evidence, suggested fix/steps, unknowns, next action and references. Read `task_get` before replacing it via metadata; preserve earlier material. Resume requires checking whether the saved evidence still matches reality.

The canonical file uses versioned JSON metadata fences, length-framed raw Markdown notes and collapsible full event history. See [format specification](docs/20261007_001507_markdown-format.md). Metadata is authoritative; display headings are generated. Arbitrary editor changes to notes/metadata are detected and rejected rather than silently accepted without revision/history. Use MCP for writes. This integrity check is cooperative error detection, NOT protection against someone rewriting content and digests deliberately.

Writers coordinate via stable locks and atomic same-directory replacements with fsync. Uncooperative editors writing concurrently can still overwrite a tool after its final check; do not promise losslessness outside the shared locking protocol. Back up the canonical session files AND global ledger/activation/cutover records with writers stopped. History makes files grow, and Markdown is not assumed faster than SQLite.

To repair generated views without changing tasks or revisions:

```sh
<runtime-python> -m task_management.store \
  --workspace /absolute/worktree/root --session-id REAL_HOST_SESSION_ID
```

## Reminders and host UI

Default `lifecycle` mode redraws on SessionStart and successful create/update, not every assistant turn. Completed/cancelled tasks are hidden from the unfinished list, but the assistant still confirms a just-completed/cancelled task's number, title and status. Ready/Waiting grouping is retained. Idempotent retries, errors and unrelated tools do not redraw the hook. Closing the final task emits `Tasks · 0 unfinished`. Opt-in `install_runtime.py --reminder-mode always` adds a Stop reminder, but is noisier.

SessionStart always provides bounded scope/path/query context even for empty or unavailable storage. PreToolUse checks mutation input against the host's workspace/session BEFORE writing; PostToolUse requires matching hook/input/result scope. These hooks are cooperative error prevention, not identity/authentication, and apply only where the host runs trusted hooks. See [official hook fields and behavior](https://learn.chatgpt.com/docs/hooks).

CLI can display warning-style hook output. Historical desktop acceptance found that `systemMessage` was not reliably visible; this is not a fresh test of the current App version. The tool/skill contract therefore requires a visible assistant reply containing number + literal title + actual status and an unfinished summary regardless of hook visibility, not a task number alone.

For a single mutation, render `display_summary`. For several in one turn, confirm each distinct affected task once at its final observed status and append only the final successful mutation's session-summary section. This avoids repeated summaries and extra list calls. If the field is absent, use `task` plus `workspace_overview`; only if the overview is unavailable, use one `task_list(status="open", limit=5)` call. That fallback reports exact counts and a labeled page, not a globally priority-ordered selection. A failed fallback read reports unavailable, never zero. Unrelated turns do not require summaries.

Neither approach creates a pinned native TODO panel or guarantees that the App/model follows the presentation contract. Source tests validate formatting and tool delivery; opt-in real-host tests validate transport without a model turn. Visible App acceptance is separate and remains pending until authorized installation/reload and observed create/update/terminal/batch replies. Filesystem-local tools require a local runtime/workspace; do not assume ordinary cloud/web chats can read these files.

## Verify

```sh
<runtime-python> -m unittest discover -s tests -v
```

Tests use temporary workspaces/data, actual multiprocessing/terminated writers, Git worktrees, real MCP stdio in legacy/auto negotiation and real hook subprocesses. SQLite is retained only for regression fixtures and explicit export/cutover. Acceptance records distinguish source tests, real host checks and any installation/data switch not yet performed.
