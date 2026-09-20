# Workspace Task Visibility — Implementation Plan

Date: 2026-09-20 (Asia/Taipei)
Status: approved for implementation by the user's “請執行” instruction

## Goal

Make unfinished tasks for the canonical workspace visible across Codex CLI and
Codex in the ChatGPT desktop app. The task store remains workspace-scoped;
sessions only trigger a fresh read. Completed and cancelled tasks stay hidden by
default.

Deliverables:

- exact filtered counts in `task_list`, independent of pagination;
- a bounded, safe, side-effect-free read-only workspace overview;
- visible reminders at Codex `startup`, `resume`, `clear`, and `compact`;
- an optional reminder after every assistant turn;
- installed-plugin validation in Codex CLI and the desktop Codex host where the
  current host exposes an automatable path.

## Requirements Traceability

| Requirement | Planned behavior |
|---|---|
| Tasks survive `/clear`, resume, handoff, and sign-in changes | SQLite remains keyed by canonical workspace; lifecycle hooks reload current state. |
| See how many tasks remain at first glance | `SessionStart` emits a visible compact summary. |
| Hide completed work | Default `status=open` excludes `completed` and `cancelled`. |
| Approximate Claude Code's wait-state list | Optional `Stop` reminder emits the same compact summary without another model turn. |
| Work in CLI and desktop Codex | Bundle trusted plugin hooks and validate both available local hosts. |
| Do not turn stored task text into instructions | Developer context contains no stored title/description; visible titles are sanitized and bounded. |

## Architecture

State and presentation remain separate:

```text
SQLite task store (workspace-scoped)
        |
        +-- MCP task_list --> summaries + exact filtered counts
        |
        +-- read-only overview reader (mode=ro, short timeout)
                    |
                    +-- SessionStart hook --> visible lifecycle reminder
                    +-- Stop hook ---------> optional every-turn reminder
```

The hook reader must never construct `TaskStore`: its constructor may create,
migrate, or configure a database. The hook path opens only an existing schema-2
database with SQLite URI `mode=ro` and fails closed without modifying state.

## Data Flow

1. Codex invokes a lifecycle hook with JSON on stdin, including `cwd` and event.
2. The hook resolves `cwd` to its Git worktree root, falling back to canonical
   `cwd` outside Git.
3. The hook resolves the same database path as the MCP server.
4. The overview reader validates schema 2 and queries open task counts and at
   most five rows in attention order: in progress, blocked, ready pending,
   waiting pending.
5. The renderer removes control/terminal/bidi formatting, normalizes whitespace,
   and caps both individual titles and total output.
6. `SessionStart` returns `systemMessage` plus trusted generated
   `additionalContext`; `Stop` returns a `systemMessage` only in `always` mode.

## Project Structure

Before:

```text
task_management/
  server.py
  store.py
scripts/
  install_runtime.py
skills/task-management/SKILL.md
tests/
```

After:

```text
task_management/
  config.py             # pure shared application-data path resolution
  overview.py           # read-only query and safe renderer
  server.py
  store.py
hooks/
  hooks.json
  task_reminder.py      # SessionStart/Stop JSON adapter
scripts/
  install_runtime.py    # --reminder-mode lifecycle|always
skills/task-management/SKILL.md
tests/
  test_config.py
  test_overview.py
  test_hooks.py
  ...existing tests
```

## Files to Change

| File | Action | Description |
|---|---|---|
| `task_management/config.py` | Add | Resolve default/override DB and settings paths without I/O. |
| `task_management/server.py` | Modify | Use the shared database-path resolver. |
| `task_management/store.py` | Modify | Return exact `matching_total` and `by_status` from `list`. |
| `task_management/overview.py` | Add | Read schema 2 without writes and render safe compact output. |
| `hooks/hooks.json` | Add | Register SessionStart and Stop handlers with bounded timeouts. |
| `hooks/task_reminder.py` | Add | Parse hook input and produce supported hook JSON. |
| `scripts/install_runtime.py` | Modify | Atomically configure lifecycle/always reminder mode. |
| `skills/task-management/SKILL.md` | Modify | Require concise open-count reporting after task mutations. |
| `README.md` | Modify | Document behavior, modes, trust, surfaces, and limitations. |
| `tests/*` | Modify/Add | Executable specifications for every behavior and failure mode. |

## Implementation Steps (with verification)

### 1. Structural configuration extraction

- **What:** Extract the current DB default and `TASK_MANAGEMENT_DB` validation
  into pure helpers; make `server.py` call them without behavior changes.
- **Why:** Skipping this would let the MCP server and hook silently read different
  databases and would mix structural movement into later behavior commits.
- **Dependencies:** None.
- **Done When:** All 34 baseline tests pass before and after; the structural-only
  commit contains no new reminder behavior.
- **How to Verify:** Run the full unittest suite and inspect the focused diff.

### 2. Exact summaries and safe read-only overview

- **What:** Add failing tests for filtered totals, cursor independence, status
  changes, read-only/no-create/no-migrate behavior, attention ordering,
  truncation, and hostile title sanitization; then implement the minimum code.
- **Why:** Without exact counts and a side-effect-free reader, reminders can be
  misleading or mutate storage merely by displaying status.
- **Dependencies:** Step 1 committed.
- **Done When:** New tests pass, all existing tests stay green, and the behavior
  is committed separately from hook integration.
- **How to Verify:** Observe RED failures first, run focused tests to GREEN, then
  the complete suite after refactoring.

### 3. Lifecycle integration and installed delivery

- **What:** Add contract tests for SessionStart/Stop JSON and reminder settings;
  implement hooks, installer flag, skill/README guidance, plugin validation,
  cachebuster reinstall, and installed-host acceptance.
- **Why:** Without lifecycle integration the new counts remain invisible when a
  user resumes or clears a session; without installed-host checks source tests
  cannot prove Codex actually loads the hooks.
- **Dependencies:** Step 2 committed.
- **Done When:** Full tests and validators pass; updated plugin is reinstalled;
  installed cache exposes the hooks; CLI and desktop Codex evidence is recorded;
  final code is committed before task completion.
- **How to Verify:** Validate plugin/skill, reinstall through the personal
  marketplace, start fresh host sessions, exercise documented lifecycle sources,
  and confirm the same workspace count is visible.

## TDD Strategy

RED-GREEN-REFACTOR repeats for each behavior:

1. `test_config.py` defines pure path semantics before extraction.
2. Existing tests prove the structural move preserves behavior.
3. `test_store.py` defines `summary` semantics before changing `TaskStore.list`.
4. `test_overview.py` defines read-only and rendering behavior before adding the
   overview module.
5. `test_hooks.py` defines exact SessionStart/Stop output before adding scripts.
6. Installer tests define preference preservation/defaulting before adding the
   command-line option.
7. Full suite and real stdio tests remain the regression gate after each cycle.

No mocks replace SQLite behavior. Temporary databases and workspaces keep tests
isolated and repeatable. Host acceptance supplements unit tests rather than
being simulated as proof of installation.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| `systemMessage` is warning-styled, not a pinned task panel | Document the exact UX and make real desktop/CLI display an acceptance gate. |
| Stored titles contain prompt injection or terminal controls | Keep them out of developer context; sanitize, quote, and bound visible text. |
| Reminder delays a turn while SQLite is locked | Read-only connection, very short busy timeout, 1–2 second hook timeout, quiet failure. |
| Plugin hook is not trusted | Document trust requirement and verify trusted installed hook behavior. |
| Login/logout emits no documented dedicated event | Claim only the lifecycle source observed in real-host acceptance. Persistence remains intact. |
| Every-turn warnings become noisy | Default to lifecycle reminders; `always` is explicit per-machine configuration. |

## Definition of Done

- Structural and behavioral changes are committed separately.
- Each task is marked complete only after its corresponding commit exists.
- All source and installed-cache tests pass.
- Plugin and skill validators pass.
- Personal marketplace is updated only through the documented cachebuster and
  reinstall helpers.
- Acceptance evidence states clearly which Codex surfaces and lifecycle events
  were actually observed; unverified UI behavior is not claimed.
