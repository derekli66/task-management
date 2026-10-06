# Implementation Plan: session-scoped Markdown tasks

## Goal

將 task-management 的正式任務儲存改成 workspace 內的 Markdown，每個 Codex session 保存獨立 TODO。任務資料不被 Git 追蹤，並保存暫緩工作時的研究結果、解法、下一步與驗收條件。

使用者已選擇保留現有編號、UUID、依賴、revision 衝突檢查、request_id 防重複、狀態轉換、完成證據與事件歷史。本文件是待核准的實作計畫；尚未更換儲存後端。

## Requirements and scope

| 需求 | 設計及可觀察結果 |
|---|---|
| Markdown 儲存 | 任務及歷史可從正式 Markdown 還原；正常 CRUD 不開啟 SQLite |
| session 獨立 | 所有工具明確接收 workspace + session_id；同一 workspace 的兩個 session 不混用任務 |
| 持久恢復 | 相同 session ID 的 resume/compact/clear 使用原資料；新 ID 使用另一份清單 |
| 保存研究與解法 | description 是完整 Markdown 筆記，task_get 取回全文；skill 要求暫緩前更新、恢復時閱讀 |
| 保留管理規則 | 現有正確性與錯誤語意沿用；因 scope 新增 session，request_id 唯一範圍改成 workspace + session |
| 保留編號 | 本機共用遞增編號；task_number、UUID 與既有任務編號維持不變 |
| 本機資料不進 Git | 寫入前安裝並驗證 local exclude，已追蹤或無法排除時停止 |
| Ready / Waiting | 可開始優先的既有摘要排序與分組繼續使用 |

不承諾速度改善。上一輪臨時量測：50 個任務、每頁 20 筆，直接儲存層 list 中位數 0.42 ms、暖 MCP list 1.26 ms、create 含摘要 3.29 ms、啟動至第一個 list 約 300 ms。未涵蓋模型、UI、hooks、大量歷史或並行負載。

## Architecture

現況：一份機器本地 SQLite，workspace 是任務命名空間；四個 MCP 工具及 hooks 讀取它。

改版後：

```text
<workspace>/
  .codex/task-management/sessions/<validated-session-id>/
    tasks.md       # 正式來源：全部任務、筆記及完整事件歷史
    index.md       # 產生的未完成摘要，Ready / Waiting
    archive.md     # 產生的完成／取消任務檢視
    .lock          # 穩定的協作寫入鎖，與被替換的資料檔分離

~/.local/share/task-management/
  numbering.md     # 全機編號命名空間及已保留的最高編號
  numbering.lock   # 全機編號配置鎖
  activation.md    # 啟用證明、ledger generation/digest、未完成的 ledger 提交
  tasks.sqlite3    # 舊資料，僅明確匯入操作會唯讀開啟
```

index.md、archive.md 不接受獨立資料更新，標記「generated」及來源 generation。完成任務只提交 tasks.md 的狀態及事件，接著重建檢視。檢視失敗回傳已提交成功及警告，避免呼叫者誤重試；下次明確刷新可由 tasks.md 修復。

保留 Python 與現有 MCP 依賴。鎖定使用目前 macOS 執行環境的標準函式庫；不引入新的資料庫、服務或 Markdown 資料庫框架。鎖功能的可攜性界線必須在 README 寫清楚。

### Session identity and hooks

官方 hooks 的共用輸入提供 session_id；subagent hooks 使用 parent session ID。因此子 agent 協作使用主 session 清單，不另建子 agent 清單。參考：[OpenAI Hooks — Common input fields](https://learn.chatgpt.com/docs/hooks#common-input-fields)。

- 四個 MCP 工具新增必要 session_id。未提供或非法值，明確拒絕；不回退到共用清單，不保存 workspace 的「目前 session」指標。
- SessionStart 無論是否已有任務，都提供有界的 session ID、路徑及查詢指引。提醒畫面是否顯示與 scope context 分開。
- resume、clear、compact 依實際 session_id 選檔，不推測所有 clear 都保留或更換 ID。若新 ID，需要明確 handoff 接續來源。
- 新增只匹配 create/update 的 PreToolUse 檢查，呼叫 scope 與 hook session 不一致時在寫入前拒絕。PostToolUse 比對 hook/input/result 的 workspace、session，再顯示摘要。
- 讀取其他 session 清單須明確指定來源。接續既有 session 應 resume；跨 session 複製／移交不是本次自動行為。
- session_id 是明確命名空間，hooks 是協作防錯，不宣稱身份驗證或工具存取控制。

### Markdown format and research notes

使用 versioned Markdown 格式：文件層 metadata 記錄 format_version、session_id、numbering_namespace、generation；每筆任務有單一結構化 metadata 區塊與一般 Markdown 筆記。完整初始建立內容與事件 before/after snapshots 置於可折疊歷史區，以保留防重複及歷史查詢。

範例輪廓（UUID／數值為示意）：

````markdown
# Session TODO

<!-- task-management document metadata: version / session / generation -->

## #N：修正登入後設定沒有更新

```json task-management-task
{"id":"<uuid>","task_number":"#N","title":"修正登入後設定沒有更新",
 "status":"pending","revision":1,"priority":"normal",
 "request_id":"<creation-key>","dependencies":[],"done_when":"回歸驗證通過"}
```

### 工作筆記

#### 問題與影響
登入後仍顯示上一位使用者的設定。

#### 已研究結果與證據
目前證據指向 cache key 缺少 user ID；記錄相關檔案及測試輸出。

#### 建議解法與實作步驟
更新 cache key，登出時清理舊值，再驗證使用者切換。

#### 待確認事項
尚未確認離線模式是否也使用同一份快取。

#### 下一步
先檢查離線模式呼叫點，再新增回歸測試。

#### 參考資料
記錄檔案路徑、文件連結及已執行的指令。

<details>
<summary>事件歷史</summary>
<!-- 版本化事件資料，包括 actor、reason、evidence、before、after -->
</details>
````

實作第 1 階段須將此輪廓定為完整可解析 grammar，包含文件 metadata、record 邊界及事件格式。metadata 是 title/status 等欄位的唯一正式值；可見標題由其產生。工作筆記只在普通 Markdown body 保存，禁止維護另一份可獨立編輯的 description 副本。歷史快照的重複是歷史資料，不作為目前狀態。

現有 description metadata update 保留為完整筆記的 revision-controlled replacement：先 task_get，再保留既有段落並更新全文；不增加專門的研究筆記資料庫或第五個 MCP 工具。done_when 仍是正式驗收條件，完成證據仍由 complete action 記錄。

解析器需正確處理中文字、多行文字、Markdown headings、內嵌 code fences 及保留標記的逃逸；讀取未知版本或格式損壞時拒絕，不重建空清單。空間／事件分頁維持既有 API 語意，但讀取 Markdown 的成本須以實際檔案大小量測。

### Storage and consistency

- 提取共用的狀態、依賴與輸入規則，再實作 Markdown backend；避免同時修改既有規則。
- 每次變更持有獨立 .lock，讀取及驗證完整正式資料，再檢查 expected_revision／request_id；同一次 tasks.md 替換提交任務與事件。
- 使用同目錄暫存檔、flush/fsync、os.replace，並在平台支援時同步父目錄。中斷後只能看到原版本或新版本；剩餘暫存檔不當作資料來源。
- 建立新任務先在 session 鎖下檢查 request_id，才配置全機編號。鎖序固定為 session → numbering；先提交編號保留，再提交任務，失敗允許跳號，已保留編號永不重用。
- 普通 CRUD 永不初始化缺少的 numbering.md／activation.md。首次啟用只能經明確 cutover initialization，以舊 DB 的最高編號及 sqlite_sequence 高水位初始化，資料只用 mode=ro 讀取；保留 namespace 以辨認不同機器或分歧備份。既有 ledger 遺失／損壞須拒絕配置，不能靜默從 #1 重啟。詳見下方全機 ledger 提交協定。
- 編號 seed 及匯入都須考慮舊 server 仍在寫入的情況；切换前停止舊版寫入者並驗證，再 seed。否則不能承諾舊 DB 與新 ledger 的共同編號唯一性。
- 多工具 writer 的正確性保證以遵守同一鎖為前提。人工編輯須在工具閒置時進行；提交前比對讀取時檔案 digest，偵測已觀察到的外部修改。此比對不宣稱能阻止不遵守鎖的 editor 在最後時刻覆寫。
- 人工修改 metadata 不得跳過編號、依賴、revision 與事件一致性檢核。工具寫入前比對下述 persisted-field projection 與獨立 integrity baseline；若外部修改沒有對應 revision/event，拒絕變更並保留檔案，不能拿未增加的 revision 宣稱已偵測所有人工修改。正常筆記更新透過 description metadata action 完成。未知／不一致內容須明確指出並停止；記錄 Markdown 不代表任意修改內部欄位都有效。
- 拒絕路徑逃逸、非法 session ID、資料目錄／檔案的 symlink 逃逸。獨立 session 僅能依同 scope 的 UUID 建立依賴。

#### Persisted fields and integrity baseline

投影欄位固定為 id、task_number、request_id、initial_payload、title、description、done_when、priority、status、owner、block_reason、completion_evidence、revision、created_at、updated_at、排序後的 dependencies。ready、unfinished_dependencies 都是由目前同 session 任務圖計算的欄位；workspace/session scope 另由文件及呼叫驗證，不拿新增的 scope 欄位去比對舊事件。

每筆正式 record metadata 保存此投影的 canonical SHA-256 integrity_digest。工具變更在同一次 tasks.md commit 更新 record、digest 與事件；人工未經工具改筆記／欄位會與 digest 不一致。digest 是協作防錯，不能防止有人同時改內容與 digest，不宣稱防竄改。

匯入時直接從當前 SQL rows／number mapping／dependency rows 建立初始 integrity baseline，保留所有舊事件 JSON 原樣；不要求舊事件具備所有現代欄位。包括僅有 id 的 schema-1 時代事件，以及不含 task_number 的歷史快照。第一版匯入來源本身必須是 schema 2；未升級的 schema 1 DB 明確拒絕，不由唯讀匯入工具偷偷升級。

驗收必須包括「完成 A → B 自身 revision 未变但 ready 改變 → B 可正常 start」及「schema 2 DB 含稀疏 schema-1 歷史 → 匯入 → 正常 update」，不能將這兩種正常狀況誤判為人工修改。

#### Global ledger commit and recovery protocol

numbering.md 保存 format_version、namespace、generation、max_reserved_number、唯一 cutover source_id／source snapshot digest、來源 workspace 匯入 reservations/receipts。activation.md 是独立啟用證明，正常狀態保存 namespace、ledger generation、ledger digest 及號碼高水位，供偵測只遺失／恢復其中一個檔案。

全機 ledger 的每次變更（配置號碼或變更匯入 reservation）都在 numbering.lock 下執行：

1. 讀取及檢查 ledger 與 activation 的已提交 generation/digest；不一致時先走復原，不配置新號碼。
2. 將 activation.md 原子寫成 pending，包含上一版 ledger digest/generation 與完整的 proposed ledger payload、proposed digest/generation；flush/fsync 及同步目錄。
3. 原子替換 numbering.md 為 proposed ledger，確定耐久提交。
4. 將 activation.md 原子寫回 active，保存新 generation/digest/high water，移除 proposed payload；確認耐久性後，才允許提交 session task。

pending 復原：若 ledger 符合 previous digest（或首次啟用時尚未存在），重播 proposed payload；若已符合 proposed digest，直接完成 active marker；若兩者皆非，拒絕並保留檔案。所有重播在同一全機鎖下完成，已保留號碼永不回退。普通 CRUD 只可完成可證明的 pending 提交，不能從 legacy DB 或 #1 重建遺失的 active ledger。

首次啟用是獨立操作：有舊 DB 時先完成下述寫入封鎖及一致唯讀 snapshot；無舊 DB 的全新安裝也需明確 initialization。僅兩個檔案皆不存在時可開始新的 namespace bootstrap；任一存在但不完整時先依 pending 協定恢復或停止。已啟用標記 active 但 ledger 缺少時停止，必須用驗證過的備份／reservation 記錄復原。

同 namespace 的單檔舊備份會與另檔的 generation/digest 不符而被拒絕；同時恢復兩份自洽的舊備份仍無法自動辨識所有之後的 session 寫入，因此不承諾任意備份回退後全域唯一性。恢復要先核對涉及的 session Markdown、匯入 receipts 與切換後記錄；不得把分歧歷史合併為一個編號序列。這項界線沿用現有獨立／分歧資料庫的編號限制。

### Git exclusion

在任何正式檔、檢視、鎖或暫存檔寫入前，先 resolve workspace 的 Git root 及 `git rev-parse --git-path info/exclude`，安全加入根目錄限定 `/.codex/task-management/`。保留既有 exclude 內容；並行新增排除規則也要協調。

以 git ls-files 檢查既有追蹤、git check-ignore 檢查實際規則（包含 negate）。已追蹤或規則無效時，停止資料初始化，不擅自 git rm。linked worktree 與 subdirectory 執行都定位到正確 root/exclude。

非 Git workspace 在寫任務前建立資料目錄的 `.gitignore`（內容排除整個資料目錄下的檔案），未來初始化 Git 仍能忽略任務。所有這些規則只影響本機任務資料；repo 的程式碼、skill 與文件照常可被追蹤。無法防止使用者刻意 git add -f。

### Legacy data and handoff

提供獨立、明確執行的唯讀 SQLite export script，先 preview workspace／任務／事件數量，再輸出到使用者選定的真實 session ID。舊 DB 只有 workspace 欄位，沒有原始 session ID，無法可靠還原每個舊 session 的清單；匯入不猜測任務屬於哪段對話。第一版以一個舊 workspace 的完整任務圖匯入一個選定 session，避免切開相依任務；需要拆分時另做明確映射。

讀取使用一致的唯讀 transaction，不呼叫舊 TaskStore 初始化，不遷移或改寫 DB 內容。匯入限空目標 session，或相同來源、相同 session 的重跑；已有新任務或來源不同時拒絕混合。來源 workspace 的匯入收據記錄目的 session，禁止無意間再匯入另一個 session 並重複已保留編號。

保留 task_number、UUID、revision、原始建立 payload、所有狀態、描述、依賴、完成證據及事件 JSON；缺少原有 task_number 的舊歷史快照維持原樣。以來源識別及比對防重複匯入，目標已存在且內容不同時停止。

#### Durable import reservation

cutover initialization 產生並耐久保存唯一 source_id，記錄 canonical DB path、schema 2、全來源 snapshot digest 及編號高水位；後續只接受這份唯讀切換 snapshot 的 workspace 匯入，不把獨立 DB 或較舊／較新的分歧 snapshot 混入同 namespace。

匯入程序固定持有 destination session .lock，再持有 numbering.lock；以 source_id + original canonical workspace 作唯一 reservation key。不同 session 的競爭匯入會由全機鎖串行化。

1. 驗證 Git 排除、目標為空或相同 import 重跑；以一致唯讀 transaction 讀取來源并核對 snapshot。先序列化完整 candidate tasks.md，為所有未來重跑固定 import_id、document_id、generation、scope 及 metadata，不隨重跑時間生成不同檔案。
2. 經全機 ledger 提交協定記錄 pending reservation：source key、destination workspace/session、import_id、source snapshot digest、完整 candidate 檔案 SHA-256、serializer format version，以及完整 candidate_metadata（含 document_id、generation、任何產生的 timestamp 或其他非來源值）。耐久成功前不寫 tasks.md；重跑只能使用 reservation 已保存的值與指定 serializer，不能重新生成 ID／時間／格式。
3. 原子提交 candidate tasks.md。文件 metadata 保存 source_id、import_id、source snapshot digest；canonical candidate 的預期 hash 只在 receipt 記錄，避免檔案自含自己 hash 的循環。
4. 經全機 ledger 協定將 reservation 改為 committed。成功後才允許該 session 一般 CRUD 並產生檢視。

中斷後重跑只允許同 destination：若 tasks.md 缺少，從相同 verified snapshot 重建預期 candidate；若檔案 hash／import metadata 相符，完成 receipt；若不同，停止並保留兩邊供檢查。任何 pending reservation 都阻止把同來源匯入另一個 session，也阻止在尚未完成匯入的目的 session 新增／修改任務；不自動清除 reservation 或更改 destination。

committed 重跑依 receipt/import_id 識別原匯入，檢核現存文件的合法 namespace／scope／integrity 后回報已匯入；既有任務已經經工具合法變更時不要求當前檔案仍等於最初 candidate，也不重新覆蓋。初始 source 內容不同或同編號／UUID 内容衝突則拒絕。

測試邊界涵蓋 reservation 前後、canonical replace 前後、receipt finalize 前後、ledger pending 前後，以及同一 source 的兩個 destination 同時匯入。可見結果只能是尚未匯入、可同目的復原的 pending，或一份 committed 匯入。

啟用 Markdown 後提示可用的舊資料及接續方式；不讓新 session 静默繼承全部 workspace 任務。handoff 記錄來源 session ID／正式檔路徑／相關任務。真正跨 session 複製任務的編號、UUID 與依賴重映射另行明確設計；本次先支援指定來源查閱與已存在 Codex session 的 resume。

### Installed plugin update and cutover

已核對本機 personal marketplace 的 root 為 `/Users/derek-macmini`，`source.path` 是 `./plugins/task-management`；該位置是指向此 repo 的 symlink。因此原始碼更新在本 repo，但執行中的安裝快取及 MCP process 不會因為修改 repo 就自行換版。

改版實作／測試完成後，更新 manifest 版本及說明，必要時以 install_runtime.py 重建 machine-local .mcp.json，再使用本機已確認支援的 `codex plugin remove task-management@personal`、`codex plugin add task-management@personal` 更新安裝快取。執行前確認資料仍在 repo/cache 外；新 Markdown 在各 workspace，舊 DB 在 application data，均不得隨 cache 清除刪除。

切換順序：完成舊 session handoff → 停止所有仍可寫舊 DB 的 MCP instance → guard/fence 舊來源及一致唯讀備份／preview → 安裝新版 → 啟動新 session 取得真實 ID → 明確初始化全機 numbering 並匯入選定 workspace → 驗證任務及事件數量、UUID／編號／依賴與筆記 → 驗證 git check-ignore → 啟用正常 Markdown CRUD。

#### Legacy writer fence and restart

切換不能只檢查某個時間點沒有舊 process：在已停用／關閉所有舊 launcher 和其 MCP connections 后，cutover guard 對來源 DB 取得 BEGIN IMMEDIATE writer lock，全程不變更資料 rows。鎖持有期間透過獨立唯讀 connection／SQLite backup API 產生一致 snapshot，記錄 original filesystem modes，再將舊 DB 及已有 WAL／SHM sidecars 設為 owner-read-only；這是明確的切換操作，僅變更檔案權限，不修改原始任務及事件內容。

guard 關閉前確認所有舊連線已結束、新版 launcher/skill/tools 已載入。接著關閉 guard，再以新的舊版 process 嘗試建立任務，驗證收到 readonly/storage error 而非僅 database_busy，且原始資料不變；通過後才可初始化編號及接受 Markdown 寫入。同時驗證新版唯讀匯入仍可讀取來源，涵蓋 guard 關閉時 WAL／SHM 清理或後續重建的情況。唯讀 fence 防止一般未提權的舊 client 在之後重啟寫入；不防止使用者刻意恢復寫權限或修改 launcher 指向另一份 DB。既有 fd 的寫權不會被 chmod 撤銷，所以不能省略舊連線停止與 reload 驗證。

若來源 filesystem 無法提供／驗證唯讀 fence，切換不能通過驗收或啟用 Markdown 編號；不默默縮減為只停止一次 process。回退時只有在停止所有 Markdown writers、保留新資料並明確核對后，才可依記錄的 modes 解除 fence；需呈現切換後工作不在舊 DB 的事實。

ChatGPT desktop App 更新本機 plugin 後須 restart，CLI 重新啟動／resume 以載入新 tools 和 skill。[OpenAI local plugin update guidance](https://developers.openai.com/plugins/build/plugins#install-a-local-plugin-manually)。新版匯入 script 尚未實作，命令參數在第 1 階段定稿，不把示意命令當成可立即執行的工具。

回退採分支切換及舊 plugin 重新安裝，但舊 DB 只包含切換前的資料：新版新增／修改過的任務不能靠回退 plugin 就自动回寫到 DB。回退前先保留所有 Markdown；需繼續使用新版資料時另行明確轉換，不能默默忽略切換後工作。

## Data flow

SessionStart(session_id, cwd) → canonical workspace / bounded scope context → MCP(workspace, session_id) → scope validation → rules → locked tasks.md commit → generated views → bounded session overview / hook redraw。

## Files to change

| File | Action | Responsibility |
|---|---|---|
| task_management/store.py | Refactor, then replace backend | 共用規則、Markdown CRUD、revision／事件與分頁 |
| task_management/markdown.py | Add | 版本化格式解析、序列化、錯誤定位與筆記保存 |
| task_management/config.py | Update | session paths、numbering path、Git 排除檢查 |
| task_management/locking.py | Add | 有限逾時、穩定 lock file、原子替換 |
| task_management/numbering.py | Add | activation/ledger pending 協定、編號配置、匯入 reservations |
| task_management/server.py | Update | 四個工具的 session_id、明確 scope、已提交與檢視警告 |
| task_management/overview.py | Update | 從正式 Markdown 產生 index/archive/有界摘要 |
| hooks/task_reminder.py / hooks/hooks.json | Update | 空 session context、PreToolUse 寫入 scope 檢查、同 session 提醒 |
| scripts/export_legacy_tasks.py | Add | 唯讀 preview/export、durable reservation／重跑復原 |
| scripts/cutover_tasks.py | Add | 明確初始化、來源 backup／writer guard／readonly fence、模式記錄 |
| scripts/install_runtime.py | Update | 新設定及升級說明，不偷偷執行匯入 |
| skills/task-management/SKILL.md / README.md | Update | session 規則、工作筆記、handoff、Git 忽略與手動修改界線 |
| tests/* | Update/add | 既有規則、新格式、隔離、故障、Git、真實 MCP/hooks |

## Implementation steps with verification

| Step | What | Why：跳過的後果 | Dependencies | Done When |
|---|---|---|---|---|
| 1 | 定稿完整 Markdown grammar、session 工具契約及 legacy 接續規則，建立 fixtures | 格式／scope 不明確會使讀寫互相矛盾、漏失研究筆記 | None | 中文、多行、code fence、歷史／格式錯誤 fixtures 與契約測試可重現 |
| 2 | 提取既有規則為 backend 可用模組，獨立 structural commit | 直接改儲存與規則會難以判斷回歸原因 | 1 | 既有 baseline 與提取後測試通過，工具行為不變並有獨立 commit |
| 3 | 實作 Markdown backend、編號 ledger、鎖定、Git 排除、唯讀匯入 | 缺少這些會覆蓋任務、重複編號或暴露本機筆記於 Git | 2 | 故障／並行／匯入／Git 測試通過，DB 原檔不變，正常 CRUD 不連 DB |
| 4 | 串接 session-aware MCP、scope hooks、index/archive 與 Ready/Waiting | 後端正確但入口仍共用 workspace 時，session 任務會混用 | 3 | 空 session 有 context；錯誤 session 寫入被阻止；兩個 session 互不影響；檢視失敗不變成假失敗 |
| 5 | 更新 skill／README，完成 deferred-work 及真實 CLI/App 驗收 | 模型可能只保存 Title 或恢復時不讀研究，使用者需求仍未達成 | 4 | 暫緩筆記在 resume 原樣保留；兩個實際 session 隔離；git status 不含任務檔；對照量測有範圍說明 |
| 6 | 分開提交 behavioral changes，記錄驗收與升級／回退步驟 | 未提交結果不能重現，也無法安全切換舊 server | 5 | 所需測試通過、commit hash 可查、舊 DB 保留、切換新 plugin 的操作明確 |

## Testing strategy

- Rules: 現有狀態轉換、完成證據、依賴循環／active dependents、revision、request_id、#N／UUID 查詢及頁數摘要逐項對照；完成前置任務后依赖任務的 derived ready 改變不得造成 integrity error。
- Research: 暫緩的問題、根因證據、解法、參考連結、待確認與下一步，經更新、restart、resume、compact 後完整保存；metadata update 不吞掉未修改段落。
- Concurrency/failure: 同 session 多 writer、不同 session 同時配置編號、重複 request、殺死未提交 writer、replace 前後中斷、檢視生成失敗、lock timeout、外部已觀察修改。
- Parsing: Unicode、多行 title/description/evidence、內嵌 fences、保留標記、schema 未知／metadata 破損、不一致事件與非法 path；失敗不得重置資料。
- Git: 普通 repo、linked worktree、空白路徑、subdirectory/nested repo、既有 tracked data、exclude 寫失敗、ignore negate、資料 symlink、非 Git 後初始化。
- Migration: 一致唯讀 snapshot、稀疏歷史保真及匯入後 update、既有編號 seed、高水位、durable receipts 各中斷邊界、同來源兩 destination 競爭、ledger／activation 遺失或單檔舊備份、重跑與不同目標內容、舊 server 未停止時拒絕切換、切換後舊 client 重啟仍不能寫；保留原 DB 內容。
- Integration: 四個 MCP 工具及 hooks 真實 transport，兩個真實 Codex sessions、空清單啟動、resume/clear/compact、CLI warning 與 App 末尾摘要 fallback。
- Performance: 與前述基準同範圍量測；另測長研究笔記／事件歷史。只報告實測，不假定 Markdown 更快。

## Independent review and remaining risks

| Concern | Verdict | Revision |
|---|---|---|
| 空 session 沒有識別 context | ACCEPT | context 與 reminder 分離，空清單也注入 scope |
| session_id 只是 caller 輸入，可能寫錯 session | ACCEPT | 加入變更前 hook 檢查及變更後三方 scope 比對 |
| 任意並行人工存檔的無損保證不成立 | ACCEPT | 明確限制為協作工具 writer，手動編輯限閒置並描述剩餘風險 |
| Markdown 與研究內容契約不完整 | ACCEPT | 第 1 階段定稿 grammar；description 全文更新，研究普通 Markdown 保存 |
| Git 排除／檢視失败／legacy 匯入可能先寫後失敗 | ACCEPT | 排除先行、已提交成功分開檢視警告、legacy 嚴格唯讀 |

事件完整保留會使 Markdown 變大；全機編號仍需共享文字 ledger；檔案型儲存依然有一致性程式碼。這些是使用者已選擇保留規則的成本。人工修改若忽略協作鎖，無法保證與工具並行寫入不互相覆蓋。舊 server 未停止前也不能宣稱完成跨儲存後端編號切換。

### Requested fresh debates review

使用者要求在實作前再次執行 debates。新獨立 Critic 完整閱讀本計畫與現行 store／numbering／concurrency／migration fixtures；初次結論是先修訂，找出一項 migration BLOCKER 及三项其他問題。主 agent 全數 ACCEPT，修訂後由同一 Critic 覆核。

| # | Finding | Verdict | Action taken |
|---|---|---|---|
| 1 | ready／unfinished_dependencies 可在任務自身 revision 不變時改變；稀疏舊事件也不能拿來全面 equality | ACCEPT / cleared | 明確 persisted projection 與匯入 integrity baseline，保留舊歷史原樣 |
| 2 | 匯入檔與全機 receipt 跨檔提交／中斷／並行協定未定，可能重複匯入；BLOCKER | ACCEPT / cleared | 全機 pending reservation 先耐久固定目的地，再提交 tasks.md，最後 finalize；明確每個中斷點復原 |
| 3 | 無法區別第一次啟用與編號 ledger 遺失 | ACCEPT / cleared | 普通 CRUD 不 bootstrap；獨立 activation marker + pending ledger journal；不自動採用單檔舊備份 |
| 4 | 已停止的舊 MCP 可能稍後重啟，獨立配置同一號碼 | ACCEPT / cleared at design level | 舊來源 read-only fence、writer guard、停止既有 FD／reload、guard 關閉後舊 process 寫入拒絕驗收 |

最終覆核：無剩餘設計 BLOCKER。協定一致性的信心 High；filesystem fence 在實際 runtime 測試前為 Medium。保留兩個重點驗收：reservation 後目的檔尚不存在時仍能按原 ID／時間／serializer 重建；guard 關閉後舊 writer 依然因 readonly 而失敗。此次 review 只修改計畫，未實作或操作真實資料遷移。

## Conclusion and next action

需求與方案已整理，待使用者核准本計畫後開始第 1 階段。此次僅新增本計畫文件；未更換 backend、建立 session 任務檔或匯入舊資料。
