# Session Markdown implementation — acceptance and summary

Date: 2026-10-07 (Asia/Taipei)

Status: source implementation and isolated validation complete; production installation/cutover NOT performed. The user approved committing and pushing the implementation, tests and documentation on 2026-10-07. This record accompanies the behavioral implementation commit. The structural rule extraction is already committed as `5b87772`.

## Summary

Normal MCP CRUD now uses one canonical `tasks.md` per canonical workspace and real Codex session, with generated `index.md` / `archive.md`. SQLite is accessed only by explicit legacy export/cutover and regression fixtures. Retained rules include global human numbers, UUIDs, dependencies, expected revisions, request-id reuse, lifecycle transitions, evidence and complete history.

Full research/solution notes are ordinary Markdown bodies, including code fences and multiline Chinese. Updates are revision-controlled whole-note replacements. The skill now requires preserving useful deferred-work findings and reading them on resume; it does not authorize executing stored suggestions.

Default reminders remain lifecycle/mutation-driven, not an entire task list after every turn. SessionStart supplies scope even when empty/unavailable; PreToolUse checks mutation scope, and PostToolUse compares input/result/host scope. Ready precedes Waiting.

## Verification

Final combined suite: **105 tests passed in 35.590 seconds**, including the opt-in real Codex host test (no skips). `git diff --check` passed.

Pre-commit repeat on 2026-10-07: the same **105 tests passed in 26.314 seconds**, including the real host test. No production installation or data cutover was performed during verification.

| Gate | Evidence |
|---|---|
| Baseline and extraction | Original 57 tests passed before and after shared rules extraction; structural commit `5b87772` |
| Task invariants | Legacy behavior contracts also run against Markdown, including lifecycle, revision, retries, cyclic/unfinished dependencies, active dependents, numbers and pagination |
| Research persistence | Exact note roundtrips include embedded fences, delimiter-looking text, CRLF, NUL and trailing whitespace; restart and startup/resume/clear/compact hook fixtures preserve the same scope |
| Canonical integrity | Invalid format/history/digests refused; copied workspace scope refused; missing known canonical files never regenerated as empty |
| Concurrency | Real competing processes: one start wins, duplicate create has one UUID/event, numbers unique across sessions; killed writers before/after replace expose only full old/new state |
| Generated views | View failures are committed success with warning; explicit view repair preserves canonical bytes/revisions; index is not truncated to the hook message bound |
| Git privacy | Normal repo, subdirectories, spaces, linked worktree, nested repo, non-Git then git init; tracked data, negation, failed exclude and symlink escape refuse writes |
| Global recovery | Pending ledger replay before/after replacement; missing activation/ledger and single-file stale backups refused; CRUD never initializes missing storage |
| Legacy import | Schema 1 refused read-only; schema-2 sparse events retained; fixed candidate pending receipts recover after reserved/canonical/finalized boundaries; two destinations and terminated importer cannot duplicate a source |
| Cutover fence | Real unprivileged subprocess receives SQLITE_READONLY after guard closes, not busy; highest sqlite_sequence seeds numbering even beyond surviving numbers; import works from bound backup after original DB is moved away |
| MCP transport | Real stdio in legacy and auto negotiation, four session-required tools, lifecycle/restart, structured errors and validation |
| Real Codex host | CLI 0.160.1 app-server, two ephemeral host-generated session IDs; four tools discovered and create/list/get/update passed with independent lists and research notes; NO model turn submitted |
| Skill | Official skill-creator quick_validate passed; pinned runtime lacks PyYAML, so validation used the existing Python 3.13 interpreter with that dependency |
| Code review | Independent Critic's final verdict: no remaining BLOCKER, High confidence; reviewer did not modify files or production data |

The opt-in real-host test loads source MCP via per-thread configuration, NOT the installed old plugin cache. It verifies host integration without replacing the user's active installation. Desktop UI rendering, installed-cache schema reload, and an actual paid/model compact flow are not claimed as tested. Real-host test uses ephemeral threads and temporary task data. Default discovery skips it unless the CLI is explicitly supplied.

Test commands:

```sh
<runtime-python> -m unittest discover -s tests
TASK_MANAGEMENT_TEST_CODEX=/absolute/path/to/codex \
  <runtime-python> -W ignore::ResourceWarning -m unittest discover -s tests
python3 /Users/derek-macmini/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/task-management
```

Existing SQLite regression fixtures emit ResourceWarning for connections that their context managers do not close immediately; the final combined run suppresses that warning category, not test failures. New production Markdown CRUD does not open SQLite.

## Independent review results

All six findings were ACCEPTED and covered by corrections/regression checks:

| Finding | Correction |
|---|---|
| Preview referenced undefined numbering | Preview reads its supplied source; CLI/help and preview counts verified |
| Import ignored bound backup | Read ledger source identity first; import/recovery read saved snapshot_path |
| Missing committed canonical file could reset | Receipt/view evidence causes storage_error; original views retained |
| Copied file could cross workspace | Document metadata includes canonical workspace and checks it on load |
| Non-Git ignore negation could expose notes after git init | Only verified full-exclusion content accepted; no overwrite of existing rules |
| Import could publish stale views after a concurrent writer | Reacquire session lock and reload latest canonical before refreshing |

## Measured performance (not a speed claim)

Run `python -m tests.benchmark_storage` with the runtime. Temporary local non-Git workspace; 50 tasks; 20 warm list samples; medians; no model/UI time. Legacy SQLite and new Markdown are measured on the same machine/run.

| Scope | No research note | 20,000-character note per task |
|---|---:|---:|
| Canonical Markdown file | 91,237 bytes | 13,091,437 bytes |
| SQLite direct list | 0.52 ms | 7.47 ms |
| Markdown direct list | 36.03 ms | 118.25 ms |
| SQLite create | 3.00 ms | 4.21 ms |
| Markdown create | 57.16 ms | 151.25 ms |
| Markdown warm MCP list | 23.93 ms | 118.86 ms |
| Markdown startup → first list | 432.64 ms | 1,268.74 ms |

List/create timings differ from earlier informal measurements and vary with subprocess/filesystem load. Direct creation does not include server post-commit overview. Long notes are intentionally repeated in initial payload and historical snapshots to preserve requested rules, causing file growth. This implementation is slower than SQLite in these scopes; its benefits are readability and per-session persistence. No unrequested cache/storage redesign was added to hide that result.

## Remaining deployment step

The source version is `0.2.0+codex.20261007010000`; four tools now require session_id. The installed plugin and active MCP still use 0.1. No real DB permissions, ledger or session tasks were migrated in this implementation turn. Only the authorized implementation tracker was created/updated through the old installed MCP.

Before installation/data activation: save active handoffs, disable/close all old launchers and MCP connections, run verified cutover (source retained, permissions intentionally fenced), reload plugin/client, obtain a real destination session ID and explicitly import selected old workspaces. See [README](../README.md#upgrade-an-existing-sqlite-installation). The script will not kill other sessions or infer historical session ownership.

Rollback is not an automatic Markdown-to-SQLite conversion: preserve post-cutover files and reconcile new work before restoring original modes from cutover.md. Keep machine ledger/activation and session canonical files together when backing up.

## Conclusion

Markdown task storage and safe explicit migration are implemented and independently reviewed. Deployment is intentionally separate while old sessions remain active. Do not report that the user's existing tasks are already Markdown or that this change improves speed.
