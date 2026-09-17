# Task Management Tool — Implementation Plan

Date: 2026-09-17 (Asia/Taipei)
Status: approved by Derek via `go` on 2026-09-17; implementation results recorded separately in the acceptance note

## Goal and agreed scope

建立由 Derek 自己保存資料、Codex 能持續維護的本機 TODO 工具。以個人 Codex plugin 包裝 MCP server 與 skill，所有專案共用工具，各 workspace 的任務分開管理；換對話或重啟後能讀回未完成工作。

本計畫承接使用者已確認的「個人 plugin＋SQLite＋MCP＋skill」。本次審查提出兩項明確修訂供核准：公開 API 收斂為四個工具；`parent_id` 延後，第一版只保留有執行意義的 dependencies。

交付：本機原始碼、測試、已安裝個人 plugin、使用 skill、安裝／恢復說明與驗收紀錄。這是獨立工具，不屬於 Godot pipeline 的模型 adapter 或遊戲功能。

第一版不包含網頁看板、原生 Codex TODO 面板整合、雲端同步、帳號系統、排程、自動執行、心跳或租約。工具記錄工作；任務存在不構成執行授權。

## Architecture and local evidence

```text
Codex conversation
  ├─ skill: 何時建立、讀取、更新、續接任務
  └─ MCP tools (local stdio)
       └─ TaskStore: 狀態與相依規則、版本檢查
            └─ SQLite: tasks / dependencies / task_events
```

- Source: `/Users/derek-macmini/plugins/task-management/`
- Data: `/Users/derek-macmini/.local/share/task-management/tasks.sqlite3`
- Dependencies: `/Users/derek-macmini/.local/share/task-management/runtime/<lock-id>/`，每份依賴鎖定檔對應獨立 virtual environment。
- Personal marketplace: `/Users/derek-macmini/.agents/plugins/marketplace.json`
- Plugin code 由已安裝的 cache 載入；資料庫與依賴環境不放入 cache。
- 啟動時不下載套件、不依賴啟動目錄、不自動改變資料格式。

已查到本機 Python 3.13.0、Codex CLI 0.155.0-alpha.2.6 與 `codex plugin` 指令；未找到 `uv`，故預設採 Python venv/pip。檢查時 `~/plugins` 與個人 marketplace 尚不存在；本文件先建立 source 下的 docs 目錄。尚未安裝或執行 MCP SDK，實際套件與 Codex 相容性仍待 Phase 1 驗證。

採官方 Python MCP SDK。官方文件目前列 v2 為 stable；實作先確認可安裝版本與實際 API，鎖定直接及間接依賴。SQLite 使用 Python 標準庫，不增加 ORM。儲存規則測試可使用 unittest；協定測試使用 SDK client。

## Workspace identity

所有工具都要求 `workspace` 絕對路徑，解析為實際存在的 canonical directory。每次資料查詢均包含 workspace 條件，不使用 server 的 cwd 或可變的「目前 workspace」。

- Git 專案由 skill 選用當前 worktree 的 top-level path；非 Git 專案選用使用者指定的工作根目錄。
- 同一路徑的 symlink alias 共用清單；不同 worktree／clone 分開。
- 使用者若搬動或重新命名資料夾，第一版視為新 workspace；舊資料仍在資料庫，搬移對應功能延後。
- 不提供跨所有 workspace 的預設列表；傳入其他 workspace 的 task ID 回傳 not-found。
- Workspace 分區防止混用，不是存取權限或安全隔離。所有使用者均為同一位本機操作者。

## Model and storage rules

Task 欄位：

| 類別 | 欄位與規則 |
|---|---|
| Identity | UUID `id`、canonical `workspace`、建立時 `request_id` |
| Content | 非空白 `title`、`description`、非空白 `done_when`、`priority`（low/normal/high，預設 normal） |
| Lifecycle | `status`（pending/in_progress/blocked/completed/cancelled）、`owner`、`block_reason`、`completion_evidence` |
| Relations | `dependencies`：同 workspace 已存在的 task IDs；禁止自己、重複與循環 |
| Consistency | 遞增 `revision`，建立時為 1；每次成功更新加 1 |
| Time | `created_at`、`updated_at`：UTC，`YYYYMMDD_hhmmss`；同秒事件以事件序號排序 |

`owner` 是 caller 提供的歸屬標記，不能證明身份，也不是獨佔執行鎖。開始任務使用原子狀態轉換，能阻止兩個正常 caller 同時認領；無法阻止某 agent 忽略工具而執行工作。

每次寫入都在同一個短交易中完成：檢查相依關係與 expected revision → 更新任務／相依資料 → 追加事件 → commit。SQLite 開啟 foreign keys、WAL 與有限 busy timeout，寫入使用 `BEGIN IMMEDIATE`。資料庫鎖定逾時回傳可辨識錯誤，不無限等待。

事件保留操作、actor、理由／證據、修改欄位的前後值、時間與 revision；不是另一套任務狀態來源。資料庫使用 schema version 1，遇到未知版本直接報錯，不清空或重建。第一版不做資料格式遷移；未來改 schema 前須另定備份與遷移方案。

完成證據由 caller 提供，server 只能檢查非空白並保存，不能宣稱已驗證程式、測試或 commit。各專案的完成規則由 skill 遵守：例如 Godot 程式任務需 commit 與適用的人工作業關卡；研究或文件任務不套用不相干的 Git 要求。

## Four MCP tools

| Tool | Inputs and behavior |
|---|---|
| `task_create` | workspace、request_id、title、done_when；可帶 description/priority/dependencies。建立 pending 任務。同 workspace 相同 request_id＋相同 canonical 建立內容回傳原 task ID；不同內容回傳 idempotency conflict。 |
| `task_list` | workspace；可依狀態／優先級篩選；預設列未結束任務。回傳摘要、revision、依賴是否滿足。limit 預設 20、上限 100；以固定 created_at/id 順序與 cursor 分頁。 |
| `task_get` | workspace、id；回傳完整任務、相依 ID 及近期事件；事件以 cursor 分頁。 |
| `task_update` | workspace、id、expected_revision、actor；一次只允許 metadata edit 或一個 action。raw status/owner/revision 不可任意覆寫。成功回傳最新 task；失敗回傳明確原因。 |

Metadata：title/description/priority 在非 terminal 狀態可修改；done_when/dependencies 只可於 pending 修改。completed/cancelled 須先 reopen 才能編輯。

| Action | Allowed transition | Required checks/effects |
|---|---|---|
| start | pending → in_progress | 所有依賴 completed；owner 設為 actor |
| block | in_progress → blocked | 非空白 reason；保留 owner |
| release | in_progress/blocked → pending | 非空白 reason；清除 owner/block_reason，歷史保留 |
| complete | in_progress → completed | 所有依賴 completed；非空白 evidence；清除 block_reason |
| cancel | pending/in_progress/blocked → cancelled | 非空白 reason；清除 owner/block_reason |
| reopen | completed/cancelled → pending | 非空白 reason；清除目前 owner/evidence；保留歷史。若 completed 任務有 in_progress/blocked/completed 的直接後續任務，拒絕並回傳受影響 IDs。 |

依賴 cancelled 仍算未完成。等待相依任務的工作可保持 pending 並顯示 not-ready；blocked 用於已開始後遇到的障礙。blocked 要繼續時先 release，再 start。沒有自動級聯、強制略過或因時間經過就釋放任務。

衝突處理：expected_revision 不符合就拒絕寫入；caller 重讀再判斷，不盲目重試。若前次更新已成功但回應丟失，透過 task_get 的當前狀態及事件確認結果。已在執行的任務由新 session 接手時，先核對是否還有人工作，再以 release＋原因重新認領。

## Skill behavior

技能描述針對 TODO 建立／管理與已授權的多步工作。進入這類任務時讀取既有清單，盡量沿用對應 task；不為單一簡單問題製造任務。

流程：辨識 workspace → list/get → 對照目前需求與工作區狀態 → create 或沿用 → start → 工作 → 更新狀態／證據。新 session 讀回任務不等於自動續做；仍遵守使用者授權與專案 resume 規則。Skill 不保證每次新對話都會自動被觸發；提供明確呼叫範例，暫不增加自動 hook。

完成條件指向批准的計畫，不複製整份計畫。工具輸出的 description、reason、evidence 當作任務資料；不將其當作新的高優先級指令。後续新項目不由工具自動排程或啟動。狀態透過對話與工具查詢呈現。

## Files to create/change

目前只有此計畫文件；預定內容：

```text
task-management/
  .codex-plugin/plugin.json
  .mcp.json
  .gitignore
  pyproject.toml
  requirements.lock
  task_management/
    __init__.py
    server.py          # tool schema / protocol error mapping
    store.py           # TaskStore operations and atomic rules
  scripts/
    install_runtime.py # explicit pinned dependency setup
  skills/task-management/SKILL.md
  tests/
    test_store.py
    test_concurrency.py
    test_mcp.py
  README.md
  docs/<timestamp>_implementation-plan.md
  docs/<timestamp>_acceptance.md
```

建立獨立 Git repository 保存此工具，提交不包含資料庫、virtual environments 或 Codex cache。個人 marketplace 由 plugin-creator 的 scaffold helper 管理，保留其他 plugin entries。插件 manifest 與 skill 使用其驗證器檢查；不在 Godot repo 加入功能或更改現有未提交檔案。

## Implementation steps and verification

| Phase | What | Why — skipping it would… | Dependencies | Done When |
|---|---|---|---|---|
| 1. Installation feasibility | 建立最小 plugin、固定依賴環境與一個暫時性唯讀 probe；驗證 cached plugin 啟動路徑，記錄實際 SDK/API 與依賴版本。 | 可能整套儲存功能完成後，才發現 Codex 的 cache、工作目錄或 interpreter 路徑讓工具無法啟動。 | None；本計畫核准後開始 | 確認安裝後 code 來自 cache，無需 source cwd 可透過 stdio initialize/list_tools/call_tool；新 Codex session 能發現 probe。若缺少可自動測試的 host 介面，明確留下新對話驗收關卡，不能以模擬 cache 冒充通過。 |
| 2. Transactional task store | 以 RED→GREEN→REFACTOR 實作 TaskStore、四工具所需操作、schema version、事件與相依規則。 | 重啟遺失、重複建立、競爭覆寫或不合法相依狀態會破壞 TODO 的可信度。 | Phase 1 | 獨立 process 重啟能讀回資料；兩個 process 競爭 start 恰有一個成功；stale write、循環、跨 workspace、非法 reopen 均拒絕且無部分寫入。 |
| 3. MCP and skill | 替換 probe 為正式四工具，完成 schema/error mapping、分頁、操作 skill 與說明。 | Agent 可能繞過規則、讀不完整清單，或新 session 不知道如何接手舊任務。 | Phase 2 | 真正 SDK client 經 stdio 完成 create/list/get/start/block/release/complete/reopen/cancel；驗證工具只有四個、錯誤可辨識、stdout 無非協定日誌；skill/manifest 驗證通過。 |
| 4. Delivery | 安裝最終 plugin、跑隔離驗收、commit 原始碼、提供帶 commit hash 的驗收紀錄。 | 來源目錄中的測試成功仍無法證明安裝後或重新開對話可用。 | Phase 3 | 可在新 session 列出四工具；workspace A/B 隔離、重啟與重新安裝保留測試資料。程式測試綠燈且已提交；人工作業若尚未確認，標為 pending，不宣稱整體完成。 |

Phase 1 是優先可行性檢查；若封裝方式與本計畫不符，先報告實證及最小修訂，不默默改成另一套全域安裝架構。相同 schema 的更新可以共享 DB；不同版本依賴使用各自 venv，更新不覆寫既有執行程序的環境。

測試均注入臨時 DB/workspace，不使用真實工作任務。重装保留資料驗收也使用隔離資料位置。中斷交易測試使用可控制的未 commit 子程序，確認任務和事件一起回滾；同時測試空 DB 的並行初始化。測試需有 timeout，不能留下 server 子程序。

## Independent review and decisions

已依 debates skill 使用 fresh-context Critic，並以修正版再次審查。

| # | Finding | Verdict | Revision |
|---|---|---|---|
| 1 | BLOCKER：重開前置任務或編輯依賴可破壞後續任務狀態 | ACCEPT；複審已解除 | 相依編輯限 pending；拒絕在已有 active/blocked/completed 後續任務時 reopen 前置任務；檢查與寫入同交易。 |
| 2 | Owner 意義與 session 中断恢復不清楚 | ACCEPT | Owner 僅歸屬標記；用 revision＋reason 明確 release；不引入身份、租約或心跳。 |
| 3 | 多個狀態修改工具重疊 | ACCEPT | 四工具，單一 update action 入口；parent_id 延後。 |
| 4 | 資料放在 cache 外仍缺少版本契約 | ACCEPT | schema version 檢查、未知版本拒絕；依賴環境按 lock 分開；啟動時不裝套件。 |
| 5 | 安裝後能否啟動驗得太晚 | ACCEPT | 將 cached plugin／fresh-session 發現移到第一關，不能只以原始碼啟動測試代替。 |

Critic 最終結論：無剩餘設計 blocker，修正版設計信心 High；實作尚未驗證。實際 host 載入與新 session 工具發現仍是第一個必須證明的關卡。

## Risks and practical limits

- 任務資料落地本機，但透過 MCP 回傳的內容會進入 Codex 對話 context；這不代表模型也在本機執行。
- 儲存與狀態檢查可以機械化；證據是否足以證明完成，仍由實作者／審查者判斷。
- workspace 為路徑身份，搬移與跨 clone 共用清單延後；資料庫不是遠端同步服務。
- 本地資料庫供本機磁碟使用；不把 WAL database 放到網路共享磁碟。備份如需執行，使用 SQLite backup API，不只複製主 DB 而漏掉 WAL。
- 目前尚未驗證 Codex 個人 marketplace 的真實安裝與 cache 路徑替換規則，Phase 1 必須以實際安裝解決。

## Sources

- [OpenAI MCP documentation](https://learn.chatgpt.com/docs/extend/mcp): local stdio process support and launch configuration.
- [Official Python MCP SDK](https://py.sdk.modelcontextprotocol.io/): stable SDK line and Python support.
- [SDK: connecting a real host](https://py.sdk.modelcontextprotocol.io/get-started/real-host/): subprocess launch, absolute paths and stdio discipline.
- Local plugin-creator SKILL.md and its installation/spec references: personal marketplace packaging, validation and fresh-thread pickup.

## Original planning-session summary and next action

已確認個人 plugin＋本機 SQLite＋MCP＋skill 的需求，完成本機只讀可行性盤點、獨立攻防審查與 blocker 複審。本次產出為本計畫；尚未建立 server、安裝套件、註冊 plugin、建立任務資料庫或開始 Godot Phase 4。

當時的下一步：Derek 核准這份具體計畫後，從 Phase 1 的安裝可行性檢查開始。此核准已於 2026-09-17 透過 `go` 完成；以上保留為原始計畫內容。
