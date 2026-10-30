# Task Management

A Codex plugin for persistent, session-scoped TODOs and the research behind them. Use it in Codex CLI or Codex in the ChatGPT desktop app with a local workspace.

The plugin bundles an MCP server named `tasks`, a skill that guides the AI agent, and hooks for task reminders. It is not a built-in Codex task tool.

## What it does

- **Keep context with the task.** Save the problem, findings, proposed solution, references and next action—not just a title.
- **Resume your work.** Tasks survive restarting the same session. Each new session has its own list; it does not automatically inherit another session's TODOs.
- **Track progress.** Stable task numbers, completion criteria, dependencies, five statuses and event history make deferred work easier to pick up.
- **See what remains.** After create/update, the AI agent reports the affected number, title and status, then exact unfinished counts and up to five open tasks, with Ready before Waiting.
- **Avoid conflicting updates.** Revision checks and duplicate-request handling help prevent accidental overwrites and repeated creation.
- **Keep task files out of Git.** Tasks are stored locally as Markdown and excluded from normal Git staging.

## Requirements

- Python 3.13+ and Git.
- The `codex` CLI for the installation commands below, even if you plan to use the desktop app.
- A local Codex client with plugin, MCP and hook support.
- A local Unix filesystem. Windows and network filesystems are not supported.

## Install

These steps are for a first-time installation. If you already use the plugin, keep your existing task storage; do not reinitialize it as an update or repair.

1. Clone and prepare the runtime:

   ```sh
   git clone https://github.com/derekli66/task-management.git "$HOME/plugins/task-management"
   cd "$HOME/plugins/task-management"
   python3.13 scripts/install_runtime.py
   ```

   Use your Python 3.13+ executable if it has a different name. Setup generates `.mcp.json` and prints the runtime Python path; use that path in place of `<runtime-python>` below.

2. Initialize local task storage **once**, before first use:

   ```sh
   <runtime-python> scripts/cutover_tasks.py --fresh
   ```

   If initialization is refused or existing storage is damaged, stop and preserve the files. Do not delete storage or choose another data directory to bypass the check.

3. Register the local plugin in `~/.agents/plugins/marketplace.json`. Create the parent directories if needed. For a new personal marketplace, use:

   ```json
   {
     "name": "personal",
     "plugins": [
       {
         "name": "task-management",
         "source": {
           "source": "local",
           "path": "./plugins/task-management"
         },
         "policy": {
           "installation": "AVAILABLE",
           "authentication": "ON_INSTALL"
         },
         "category": "Productivity"
       }
     ]
   }
   ```

   If the file already exists, merge only the plugin entry; preserve its name and other plugins. The path is relative to the marketplace root (your home directory for this personal marketplace), not the `.agents/plugins/` directory. See the [official local-plugin guide](https://developers.openai.com/plugins/build/plugins#install-a-local-plugin-manually).

4. Install the plugin:

   ```sh
   codex plugin add task-management@personal
   ```

   Replace `personal` with your existing marketplace name if different. Restart the desktop app or CLI and open a new chat to load the plugin. Review and trust its hooks when prompted.

   In Codex CLI, `/mcp` should list the `tasks` server with `task_create`, `task_list`, `task_get` and `task_update`. `/plugins` shows installed plugins; `/hooks` manages hook trust.

## Use it

Ask the AI agent in ordinary language:

> Add a task to fix the settings cache. Done when the regression test passes. Save the current findings and proposed fix with it.

> List this session's unfinished tasks, grouped by Ready and Waiting.

> Before we stop, save the investigated cause, suggested solution and next step in that task.

> After the regression test passes, complete that task and record the test result as evidence.

Tasks can be `pending`, `in_progress`, `blocked`, `completed` or `cancelled`. Unfinished prerequisites leave a task Waiting; completing a task requires evidence. Cancel unwanted tasks with a reason instead of deleting them.

Example reply:

```text
Task · #123 Fix settings cache — pending

Tasks · 1 unfinished · pending 1 · in_progress 0 · blocked 0

Ready
- #123 Fix settings cache — pending
```

Several changes in one turn produce one final AI agent summary. A just-completed or cancelled task is confirmed, but excluded from the unfinished list. Unrelated turns do not require the list to be repeated.

CLI may also show a hook summary. The AI agent's visible reply remains necessary in both clients; this plugin does not create a pinned or live task panel. Rendering depends on the client and AI agent, not only the plugin.

## Storage and boundaries

Each canonical workspace and real session ID has its own folder:

```text
<workspace>/.codex/task-management/sessions/<session-id>/
  tasks.md     authoritative tasks, research notes and history
  index.md     generated unfinished view
  archive.md   generated completed/cancelled view
```

- Read the Markdown freely, but **use the MCP tools to update it**. Direct edits can break revision/history integrity; the generated views are not separate task stores.
- The same session ID restores its list; a different ID does not. Separate clones and worktrees are independent. Cross-session transfer is not automatic.
- Task numbers are allocated across this machine, not restarted at #1 for each session. Machine-wide storage metadata and the runtime live in `~/.local/share/task-management/`.
- Git exclusion is installed locally before task writes. Already-tracked task files are refused; deliberate `git add -f` can bypass exclusion.
- Local files do not mean an offline/private-to-the-device conversation: returned task content still enters the AI agent's conversation.
- Task notes are data, not permission to execute their suggested work. Local tools require local filesystem access; ordinary cloud/web chats cannot be assumed to have it.

For tool workflow and state transitions, see the [skill](skills/task-management/SKILL.md). For the canonical file structure, see the [Markdown format](docs/20261007_001507_markdown-format.md).

## Development

After runtime setup, run tests with the printed runtime Python:

```sh
<runtime-python> -m unittest discover -s tests -v
```

Tests use temporary task storage. Automated transport checks do not substitute for observing the AI agent's visible reply in the App or interactive CLI.
