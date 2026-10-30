# App mutation summaries — Final Plan

## Version and status

- Version: **v1.1**
- Date: 2026-10-30 (Asia/Taipei)
- Independent review: cleared for developer review; High confidence; no unresolved BLOCKER.
- Status: **Awaiting developer approval**
- Tracking: task #186 in this session. Approval of this version must be recorded separately before implementation.
- Baseline: branch `main`, HEAD `2a71f2eb68623090ed9bf0cb7a78986a16c112af`; clean staged/unstaged/untracked state before planning.
- Baseline checks: 105 tests passed, one opt-in real-host test skipped (39.007 seconds). No current App visual test was performed.

## Goal and requirements

After successful task creation or update, the visible assistant reply must include the affected task's number, title and actual status, plus an unfinished-session summary. A reply containing only a task number is insufficient.

The summary remains event-driven, not required on unrelated turns. In one assistant turn with multiple mutations, show each distinct affected task once at its final observed status and only one final session summary. Keep the existing five-open-task bound, exact counts, Ready/Waiting grouping and token-conscious behavior.

A task just completed or cancelled must still be confirmed with its title and terminal status; it must not be reinserted into the unfinished list.

## Evidence and current behavior

- `task_management/server.py` advertises "Show task_number" in MCP instructions. Mutation docstrings emphasize IDs rather than the required visible confirmation.
- Successful mutations already return the complete task and, when readable, a sanitized bounded `workspace_overview`. The assistant currently constructs its visible summary itself.
- `skills/task-management/SKILL.md` requests number/title/status and a closing overview, but its general terminal-history exclusion does not explicitly distinguish the affected terminal task's confirmation.
- `hooks/task_reminder.py` validates mutation scope and returns `systemMessage`; mutation hooks do not put task content into developer context.
- `docs/20260920_235220_acceptance.md` records historical desktop hook-display limitations. These are not fresh observations of the installed App version.
- `tests/test_mcp.py` requires `content[0]` to remain parseable JSON equal to `structured_content`.
- Existing `tests/test_overview.py` targets the legacy formatter, so it is not sufficient production-renderer coverage.

Official hook documentation was fetched on 2026-10-30:
[PostToolUse](https://learn.chatgpt.com/docs/hooks#posttooluse).
It documents additional developer context and also notes unsupported output-replacement fields. This plan does not use those mechanisms.

## Scope and exclusions

In scope: a deterministic, bounded presentation field on successful mutation results; clearer MCP/tool/skill guidance; isolated regression tests; README and plugin build identifier; separate execution/acceptance record.

Excluded:

- Storage formats, task state transitions, session identity, dependencies, numbering, idempotency, revisions and Git privacy changes.
- New tools, dependencies, scheduler, daemon, native App UI or a pinned panel.
- Hook configuration, hook trust or permission boundaries, mutation cadence, and mandatory every-turn Stop summaries.
- Installed cache/runtime changes, reinstalling, restarting clients, changing old DBs or task archives, pushing commits, or creating real task fixtures in other sessions.
- Claiming App/model rendering compliance from a JSON payload or transport-only test.

A separately authorized installed-App test is necessary before claiming the user-visible bug fully resolved.

## Design and data flow

Existing flow stays unchanged through persistence:

`task_create/task_update → TaskStore commit → optional workspace_overview → presentation formatter → existing JSON + structured result → visible assistant reply`

### 1. Pure mutation formatter

Add `render_mutation_summary(task, overview)` to production `task_management/overview.py`. Do not change `render_overview`, its generated-view callers or `legacy_overview.py`.

Output contract:

1. A visible label identifies the content as stored task data, not instructions.
2. Affected row: `#N + sanitized literal title + actual status`, covering all five statuses.
3. Session header: exact unfinished total and pending/in_progress/blocked counts, including zeros.
4. At most five open rows from the existing overview, each with number/title/explicit status. Preserve its active/blocked/Ready/Waiting order and Ready-before-Waiting grouping, with `… +N more` as supplied.
5. A terminal affected task is only in its confirmation row, never in open rows. An affected task outside the top five must still be confirmed.
6. If overview is unavailable, retain the affected row and an explicit unavailable notice recommending `task_list(status="open")`; never substitute zero.

Titles use existing `sanitize_title(..., limit=100)`, then backslash-escape ASCII Markdown punctuation, including backslash, backticks, brackets, parentheses, angle brackets, exclamation mark, pipe, hash, asterisks, underscores and tilde. Each title is one literal Markdown line. Preserve stored raw title/description/history unchanged; do not place them in developer instructions.

Bound the entire field to **2,200 characters**, budgeting fixed overhead and at most six 100-character titles expanding to at most 200 characters after escaping. Preserve mandatory task identity/status, exact counts and honest remainder; do not blindly chop the tail of a fully composed message.

This is cooperative display safety, not an authorization or security guarantee against arbitrary task instructions.

### 2. Additive MCP response field

On successful create/update, add `display_summary: string` to the existing result payload. Keep `task`, `workspace_overview`, warnings and error shapes otherwise unchanged. Both the existing JSON text block and structured result contain the identical augmented object. Do not replace JSON with prose or add a duplicate text block.

- No display_summary on read results or failed mutations.
- Idempotent create reuse describes the existing task/current state, not a new creation; no new hook redraw.
- OverviewUnavailable yields the explicit unavailable presentation, retaining committed success.
- Protect only the post-commit presentation construction with a local ordinary-Exception boundary. A formatter failure retains task, overview and committed success, omits display_summary and appends a fixed safe warning to the existing warnings list. Do not include exception/task text in that warning or suggest retrying a committed mutation.
- No additional list call when a usable overview is present.

Update MCPServer instructions and create/update tool descriptions so discovery itself requests visible number + title + status and a final session summary, rather than number alone. Guidance is trusted static text; returned task fields remain data.

### 3. Skill and backward-compatible fallback

Use a short explicit response template in the existing skill, not a new skill or policy layer.

- Single mutation: render the returned display_summary in the user-visible reply.
- Multiple mutations: confirm each distinct affected task once at its final observed state; include only the final successful mutation's session-summary section once.
- Completed/cancelled affected tasks are confirmed once, while general terminal history stays hidden.
- If display_summary is absent for any reason (older server OR formatter failure), construct the confirmation from the returned task and overview.
- If overview is absent, make one `task_list(status="open", limit=5)` call. Use exact returned counts and at most five page rows, clearly labeled a fallback page. This page is NOT promised to be the globally actionable-first selection. Do not silently paginate or invent totals/order.
- Errors do not claim a successful change; do not add read calls solely to redraw failed operations.
- No successful mutation means no new requirement to append a full list.

README distinguishes source checks, transport delivery and actual visible App acceptance. A deterministic field and instructions reduce omission but cannot force the host/model's rendering.

## Files to change after approval

| File | Action | Purpose |
|---|---|---|
| task_management/overview.py | Modify | Add pure mutation-only formatter without changing existing renderer behavior |
| task_management/server.py | Modify | Add display_summary and post-commit fallback; align discovery guidance |
| skills/task-management/SKILL.md | Modify | Explicit visible confirmations, terminal handling and one-summary batch rule |
| README.md | Modify | Document field, fallback, cadence, limitations and acceptance |
| .codex-plugin/plugin.json | Modify | New dated build identifier, retaining base version 0.2.0 |
| tests/test_mutation_summary.py | Add | Production formatter and isolated presentation-failure tests |
| tests/test_mcp.py | Modify | Real stdio payload/compatibility/discovery regression checks |
| tests/test_codex_host.py | Modify | Opt-in real host payload checks; no model turns |
| docs/20261030_102700_app-mutation-summary-plan.md | This plan | Preserve approved baseline and review dispositions |
| docs/20261030_102700_app-mutation-summary-execution.md | Add after approval | Record approval message/version, RED/GREEN, checks, adjustments, commit and acceptance |

No dependency lock, package semantic version or storage schema change is planned. These are behavioral changes; no unrelated structural refactor is included.

## Ordered implementation steps

These steps begin only after explicit approval of v1.1.

| What | Why (consequence of skipping) | Dependencies | Done When |
|---|---|---|---|
| 1. Write failing production formatter tests, then implement the pure formatter | A number-only, malformed or incorrectly counted summary could remain undetected | Approved v1.1 | RED observed; formatter tests pass for all states, limits, literal titles and unavailable overview |
| 2. Integrate additive MCP payload and align tool/skill/README/build guidance | A formatter unused by the model would not address the omitted visible reply | Step 1 | Stdio tests prove JSON equality/field delivery, failures preserve committed state, instructions cover terminal and batch replies |
| 3. Validate an isolated candidate and Safe-Commit task-owned changes | Live-only checks could hide compatibility/privacy regressions or unrelated dependencies | Step 2 | Full suite and skill validation pass, reviewed candidate committed, hash and results recorded; no push |
| 4. Perform visible App acceptance after separate installation/reload authorization | Payload delivery alone cannot prove the user actually sees the required reply | Step 3 plus authorized installation/test scope | Visible create/update/terminal/batch examples pass, or UI acceptance explicitly remains pending and task is not marked complete |

## Verification and acceptance criteria

Automated, temporary data only:

- Create, metadata/title update, start/block/release/complete/reopen/cancel; actual affected status and title always present.
- Last open task completed/cancelled: its terminal confirmation plus one exact zero summary.
- More than five open tasks: exact total/all three status counts and truthful remaining, no more than five open rows, affected row retained even outside that selection.
- Production ordering and Ready/Waiting grouping; terminal history omitted from open list.
- Chinese, long/multiline/ANSI/bidi/control titles and Markdown links/HTML/triple backticks/100 consecutive backticks rendered as bounded literal data.
- Overview unavailable: no false zero; formatter exception: committed success, unchanged warnings preserved and fixed added warning, no duplicate revision/event or retry instruction.
- Idempotent retry, stale revisions, rejected/error mutations and reads do not acquire false success semantics.
- Four tools and input signatures unchanged; JSON text equals structured result; discovery descriptions include full presentation guidance.
- Existing hooks and storage/privacy rules remain unchanged and regression tests pass.
- Run full unittest suite, skill quick_validate and git diff --check in the isolated candidate. Opt-in real-host test, when executable available, uses ephemeral threads/temp data and no model turn.

Manual/installed App acceptance, NOT established by source tests:

- Authorize installation/reload into a test scope separately.
- Observe visible assistant replies for create, metadata/state update, final-zero terminal change and multiple mutations in one turn.
- Confirm number + literal title + actual status and only one final unfinished summary, even without a visible hook notification.
- If host, installation or UI test is unavailable, record pending status; do not label #186 fully complete.

## Independent review dispositions

Reviewer: fresh-context read-only Critic `/root/critic_app_summary`; source files inspected. v1 reviewed, revisions returned for independent verification, final v1.1 cleared. No files, index, tasks or learning stores modified by reviewer.

| Finding | Verdict | Resolution / reason |
|---|---|---|
| Existing sanitizer does not neutralize Markdown links, HTML or fences | ACCEPT | Explicit punctuation-escaping strategy and hostile-title tests; bound raised from proposed 1,600 to 2,200 |
| One task_list page cannot reproduce globally actionable-first overview | ACCEPT | Fallback page explicitly labeled; exact counts retained; global selection guarantee only for workspace_overview |
| Post-commit formatter failures must not lose committed success | ACCEPT | Local presentation exception boundary, existing warnings preserved, fault injection and revision/event checks |
| Terminal exclusion conflicts with reporting the just-changed task | ACCEPT | Separate affected-task confirmation from open list |
| Repeated mutations need explicit deduplication | ACCEPT | Distinct affected tasks at final observed status, one final session-summary section |
| Payload and discovery guidance cannot enforce visible App rendering | ACCEPT | Separate App acceptance remains a completion gate; no native UI guarantee |

Critic final verdict: **High confidence; no BLOCKER or unresolved material decision; Awaiting developer approval**.

## Remaining risks and next action

- The App/model may still omit or rewrite a summary; only visible acceptance can close that risk.
- Existing sessions/cache may retain old descriptions; installation/reload is separate and not authorized by this plan alone.
- A concurrent mutation can make a returned task/overview a momentary snapshot; no live/pinned panel is promised.
- Fallback page selection is less actionable than workspace_overview but accurately labeled and avoids extra pagination/token cost.

**Next action:** user explicitly approves Final Plan v1.1. Until then, no implementation, installation, commit or push. Preserve this plan; record later approval separately in the execution record.

## Planning outcome

Source investigation, baseline tests and independent review complete. Only this planning document and private task tracking are updated in this phase. No functional code changes or commit were made.
