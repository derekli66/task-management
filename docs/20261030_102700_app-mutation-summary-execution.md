# App mutation summaries — execution record

## Approval and baseline

- Approved baseline: [Final Plan v1.1](20261030_102700_app-mutation-summary-plan.md), preserved unchanged.
- Approval: the user's “請實作” reply directly following presentation of v1.1 on 2026-10-30.
- Scope: source implementation, isolated verification and task-scoped local commit. No installation, App restart, production-data test, push or DB change is authorized.
- HEAD: `2a71f2eb68623090ed9bf0cb7a78986a16c112af`, branch `main`; only this task's reviewed plan was untracked before execution.
- Ownership evidence: private baseline, original bytes/modes, exact edit intents and verification under `.git/task-records/20261030_102700_app-mutation-summary/`.
- Planning baseline: 105 tests passed, one opt-in real-host test skipped. No visible App acceptance claimed.

## Progress

Source implementation and isolated verification complete. This record accompanies the task-scoped local implementation commit; its final hash is recorded in task #186 and the user-facing result after commit verification. Installed-App acceptance remains pending.

- RED: the new 10-test production presentation suite failed as expected because `render_mutation_summary` / `display_summary` did not exist (16 errors including parameterized cases).
- Added mutation-only formatter and additive MCP result field; existing renderers, hooks and storage untouched.
- Evidence-backed test detail adjustment: the HTML-literal assertion initially rejected the substring `<img` even inside valid escaped `\<img`. It now rejects an unescaped opening angle bracket and explicitly requires the escaped token. This verifies the approved literal-rendering criterion without weakening it.
- Fixture correction: rapidly created tasks share second-resolution timestamps and are tie-broken by UUID, not task number. The outside-top-five transport fixture explicitly gives the affected task low priority so it deterministically exercises the intended boundary; production ordering and assertions are unchanged.
- Formatting-only correction: isolated candidate `git diff --cached --check` detected an extra blank line at EOF in the previously untracked plan. Removed only that blank line; approved requirements/design/acceptance text is unchanged. Original reviewed bytes remain in the private planning snapshot.

## Acceptance boundary

Task #186 remains unfinished until separately authorized installed-App acceptance proves visible replies for create, metadata/state update, terminal zero and batched updates. Tool delivery alone is not UI acceptance.

## Implemented behavior

- New mutation-only `display_summary` confirms the affected number/title/actual status, including terminal states, and exact unfinished counts with at most five open rows and Ready/Waiting groups.
- Titles are sanitized/Markdown-escaped and the complete presentation is bounded to 2,200 characters; stored titles, research and history are unchanged.
- JSON text and structured results remain equal; errors and reads do not carry success presentations. Reused create responses describe existing tasks.
- Overview failure reports unavailable, never zero; formatter failure preserves committed success and existing warnings without repeating a write.
- Discovery instructions and skill require visible full confirmations and one final summary per turn. Batch deduplication and the one-page backward-compatible fallback are explicitly documented.
- Build identifier: `0.2.0+codex.20261030195500`; no dependency, storage schema, hook, privacy or cadence change.

## Verification

| Check | Observed result |
|---|---|
| RED | 10 new test methods failed before implementation because formatter/field were absent (16 parameterized errors) |
| Focused GREEN | 10 production presentation/fault tests passed in 0.849s |
| Full live regression | 116 tests passed in 45.818s; one opt-in host test skipped |
| Independent candidate | 116 tests passed in 43.847s with no skips, including real-host ephemeral session isolation and create/update display-summary delivery |
| Real host scope | CLI 0.165.0-alpha.1.2; temporary data, ephemeral host IDs, no model turn and no production task fixtures |
| Skill | quick_validate passed in live and independent candidate; this checks structure, not actual assistant compliance |
| Commit checks | Final independent candidate is rechecked by Safe-Commit, including full regression, skill validation and staged whitespace check |

The candidate is a standalone temporary clone at the recorded HEAD, not a linked worktree. Only journaled task-owned paths are copied from verified intended bytes. A generated ignored `.mcp.json` points to the existing pinned Python for launcher tests; test storage is temporary. No install script or live plugin update was run. Original Git hooks and selected-path filters were inspected: no active hooks or matching filters. Global LFS config does not apply to selected paths.

## Visible App acceptance checklist — NOT YET RUN

After separate authorization for installation/reload and a chosen test session:

1. Create a task: visible reply includes number, literal title, pending and exact unfinished summary.
2. Update title or state: visible reply includes the updated title/actual state, not only its number.
3. Complete/cancel the last task: one terminal confirmation plus exact zero counts, no empty groups.
4. Several mutations in a turn: each affected task at final observed status, one final session-summary section.
5. Confirm these replies remain visible without relying on a hook notification.

No native/pinned panel or model compliance guarantee is claimed. The source fix is ready for separately authorized installation and visible acceptance; #186 must not be marked complete until that gate passes.
