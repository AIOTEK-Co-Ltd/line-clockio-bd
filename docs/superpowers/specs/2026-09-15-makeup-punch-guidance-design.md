# 補打卡類型建議與例外確認設計

## 背景

目前補打卡表單每次開啟都預設為「上班打卡」，但不顯示所選日期的既有打卡。後端只檢查申請時間早於現在，以及是否存在員工、類型與時間完全相同的 pending 申請；主管核准時會依申請的類型與時間直接新增 `CheckIn`，並刻意略過一般打卡的重複檢查。

2026-08-26 的實際資料呈現此風險：原本已有上午 `09:25`，後續補入 `09:00`，導致同日上午出現兩筆、下班仍缺漏。系統應利用資料庫保存的 `clock_in`／`clock_out` 類型，在員工送出前提示可能缺少的打卡，同時保留經明確確認的例外申請能力。

## 目標

- 員工選擇補卡日期後，顯示當日既有打卡並建議缺少的類型。
- 員工仍可選擇與建議不同的類型，但必須明確確認例外。
- 後端在送出與核准時重新查詢資料庫，並比較當日紀錄 snapshot token，避免只依賴前端或只比較粗略狀態。
- 保存送出當下的判斷、系統建議及員工是否確認例外，供主管審核與稽核。
- 主管審核時能看見既有紀錄、建議與例外原因，異常核准需再次確認。
- 維持現有補打卡核准、資料庫 commit 與 FTP best-effort 補傳語意。

## 非目標

- 不修正 Daisy 或其他員工的既有資料。
- 不改變 FTP 欄位格式、檔名或補卡後的全日重傳行為。
- 不新增修改或撤銷已核准補卡的功能。
- 不處理一般打卡缺少資料庫級併發 constraint 的技術債。
- 不承諾把主管核准與同時間發生的一般打卡做 transaction-level serialization；snapshot token 提供 optimistic stale detection，最後一次重新查詢後仍存在極短的 TOCTOU 視窗。
- 不推導跨日班、輪班表或排班時間；本次只依指定本地日期內的 `CheckIn.type` 判斷。

## 當日狀態判斷

所有日界線使用 `Settings.timezone`，預設為 `Asia/Taipei`。先將所選本地日期轉成 timezone-aware 起訖邊界，再查詢該員工的 `CheckIn`，不可直接以 UTC 日期切割。

| 上班筆數 | 下班筆數 | 狀態 | 系統建議 | 員工操作 |
| ---: | ---: | --- | --- | --- |
| 0 | 0 | `no_records` | 無 | 自行選擇；顯示「當日沒有打卡紀錄」提醒，不視為例外 |
| 0 | 1 | `missing_clock_in` | `clock_in` | 預選上班；可確認後改選下班 |
| 1 | 0 | `missing_clock_out` | `clock_out` | 預選下班；可確認後改選上班 |
| 1 | 1 | `complete` | 無 | 顯示紀錄完整；確認例外後仍可送出 |
| 其他 | 其他 | `ambiguous` | 無 | 顯示所有既有紀錄；確認例外後才可送出 |

`ambiguous` 包含任一類型超過一筆的狀況，即使另一類型為零或一筆。系統不應在已有重複紀錄時繼續自動推斷。

## 後端元件

新增 `app/services/makeup_validation.py`，集中提供當日狀態判斷，避免 day-status、申請及核准三處複製日期篩選與分類規則。

建議介面：

```python
class MakeupDayState(str, Enum):
    no_records = "no_records"
    missing_clock_in = "missing_clock_in"
    missing_clock_out = "missing_clock_out"
    complete = "complete"
    ambiguous = "ambiguous"


@dataclass(frozen=True)
class MakeupDayAssessment:
    state: MakeupDayState
    suggested_type: CheckInType | None
    records: tuple[CheckIn, ...]
    snapshot_token: str


def assess_makeup_day(
    db: Session,
    employee_id: int,
    local_date: date,
    tz: ZoneInfo,
) -> MakeupDayAssessment:
    ...
```

查詢結果依 `checked_at`、`id` 升冪排序。`snapshot_token` 對 `employee_id`、local date，以及每筆紀錄的 `id`、`type`、UTC `checked_at` 做 canonical serialization 後計算 SHA-256；token 不可跨員工或日期重用，即使狀態仍為 `ambiguous`，新增或改動紀錄也會得到不同 token。服務只負責狀態、建議與 snapshot，不處理 HTTP、LINE token 或 UI 文案。

## API 設計

### 查詢所選日期狀態

新增 `POST /liff/makeup/day-status`，沿用 LIFF ID token 驗證與 active employee 檢查。

Request：

```json
{
  "id_token": "<LIFF ID token>",
  "date": "2026-08-26"
}
```

Response：

```json
{
  "state": "missing_clock_out",
  "suggested_type": "clock_out",
  "snapshot_token": "sha256:...",
  "records": [
    {
      "type": "clock_in",
      "type_label": "上班",
      "time": "09:25"
    }
  ]
}
```

日期必須是有效的 ISO local date，且不得晚於 `Settings.timezone` 的今天。未綁定或 inactive 員工維持現有 `403` 行為。

### 送出補打卡申請

現有 `POST /liff/makeup/request` 的新版 payload 使用應用程式本地日期與時間，不由瀏覽器轉成 UTC：

```json
{
  "requested_local_date": "2026-08-26",
  "requested_local_time": "18:00",
  "observed_day_state": "missing_clock_out",
  "observed_snapshot_token": "sha256:...",
  "exception_confirmed": true
}
```

後端以 `Settings.timezone` 組合 `requested_local_date` 與 `requested_local_time`，再轉成 UTC 保存；不得依賴手機或瀏覽器 timezone。過渡期間保留既有 `requested_at` 為 optional legacy 欄位；缺少新版日期、時間、state 或 snapshot 的舊頁面請求不建立申請，改回傳純字串 `409`：「系統已更新，請關閉並重新開啟打卡頁面。」讓舊版 JavaScript 也能正常顯示，而不是得到 422 或 `[object Object]`。

後端重新執行 `assess_makeup_day()`：

1. `observed_snapshot_token` 與重新計算結果不同時，回傳 `409 stale_day_state`，不得建立申請；`observed_day_state` 仍需相同，作為 payload 完整性檢查與 UI 語意。
2. `missing_clock_in`／`missing_clock_out` 且申請類型符合建議時，正常建立申請。
3. 申請類型與建議不同時，必須有 `exception_confirmed=true`。
4. `complete` 或 `ambiguous` 必須有 `exception_confirmed=true`。
5. `no_records` 可選任一類型，不要求例外確認。
6. `system_suggested_type`、`day_state_at_submission` 與 `snapshot_token_at_submission` 全部保存後端重新計算的結果；`exception_confirmed` 只保存為「後端判定需要例外確認，且員工確實傳入確認」的結果，不採信前端提供的建議值。

結構化衝突回應：

```json
{
  "detail": {
    "code": "exception_confirmation_required",
    "message": "當日已有上班紀錄，系統建議補下班卡。",
    "day_status": {
      "state": "missing_clock_out",
      "suggested_type": "clock_out",
      "snapshot_token": "sha256:...",
      "records": [
        {"type": "clock_in", "type_label": "上班", "time": "09:25"}
      ]
    }
  }
}
```

狀態已變更時使用相同結構，`code` 改為 `stale_day_state`。前端必須支援 `detail` 為物件，不得把物件直接顯示成 `[object Object]`。

### 主管查詢與核准

現有 `POST /liff/makeup/pending` 每筆增加：

- `day_state_at_submission`
- `snapshot_token_at_submission`
- `system_suggested_type`
- `exception_confirmed`
- `current_day_status`
- `records_changed_since_submission`（由 submission snapshot 與目前 snapshot 比較；legacy 為 `null`）

`current_day_status` 由後端依目前 DB 狀態即時計算，用來識別申請後出現的新打卡。既有 legacy 申請的 audit 欄位可為空，主管端顯示「舊版申請，無送出時判斷紀錄」。

現有 `POST /liff/makeup/review` payload 增加 `observed_day_state`、`observed_snapshot_token` 與 `exception_confirmed`，只在 `action=approve` 時使用：

- 舊版主管頁面核准時缺少 state／snapshot，回傳可由舊 JavaScript 顯示的純字串 `409` refresh 訊息；拒絕仍可正常執行。
- 一般、未過期且非例外申請維持一鍵核准。
- 申請本身為例外，或目前狀態與送出時不同時，第一次核准回 `409 exception_confirmation_required` 並附最新狀態。
- 主管在 UI 二次確認後，以最新的 state、snapshot token 及 `exception_confirmed=true` 重送。後端再次查詢；若紀錄 snapshot 又有變化，仍回 `409 stale_day_state`，不得使用先前確認核准。
- 拒絕不需要二次確認。
- 仍保留 atomic `UPDATE ... WHERE status='pending'`，避免兩位主管重複核准。

## 資料模型與 migration

新增 migration `005`，只增加欄位，不修改已部署的 `001`–`004`：

- `day_state_at_submission VARCHAR(32) NULL`
- `snapshot_token_at_submission VARCHAR(80) NULL`
- `system_suggested_type VARCHAR(20) NULL`
- `exception_confirmed BOOLEAN NOT NULL DEFAULT FALSE`

`system_suggested_type` 在應用層僅允許 `clock_in`、`clock_out` 或 `NULL`。SQLAlchemy model 使用 `Enum(CheckInType, native_enum=False)`，避免建立或依賴新的 PostgreSQL native enum type。

舊資料不回填：`day_state_at_submission`、`snapshot_token_at_submission` 與 `system_suggested_type` 保持 `NULL`，`exception_confirmed` 為 `false`。downgrade 只移除這四個新增欄位。

## 員工端 UI

- 補打卡表單開啟時不再固定預選上班；先選日期並取得 day-status。預設日期必須使用 `Settings.timezone` 對應的本地日期，不得再以 `new Date().toISOString().split("T")[0]` 取得 UTC 日期。
- 日期變更後顯示 loading，停用類型與送出按鈕，直到狀態成功載入。
- 以一個區塊顯示當日所有既有打卡的類型與本地時間。
- 有建議時顯示「系統建議：補上班卡／補下班卡」，並預選對應類型。
- 上下班改用兩個完整寬度的可點選控制，不只依賴小型 radio 圓點辨識。
- 員工改選與建議不同的類型時，在控制項下方顯示行內警告與「我已確認類型與時間正確」checkbox。
- `complete` 顯示「當日打卡已完整」；`ambiguous` 顯示「當日有多筆紀錄，系統無法判斷缺卡類型」。兩者都要求 checkbox。
- `no_records` 顯示提醒，但不顯示例外 checkbox。
- API 失敗時保留日期、時間與原因，顯示可重試錯誤，禁止在未取得狀態時送出。
- 收到 `stale_day_state` 時，以回應中的最新狀態更新畫面，清除原本的例外確認，要求員工重新檢查。
- 送出時只傳 `requested_local_date`／`requested_local_time`；不得使用 `new Date(localString).toISOString()` 轉換補卡時間。

## 主管端 UI

每張 pending 申請顯示：

- 員工姓名、申請類型、補卡日期時間與原因。
- 送出當下的系統建議。
- 目前該日期的既有打卡類型與時間。
- 例外申請的醒目提示：「員工已確認與系統建議不同」。
- 送出後紀錄有變化時顯示：「申請送出後，當日打卡紀錄已更新」。
- legacy 申請的 audit 缺失提示。

一般申請維持目前的一鍵核准。例外申請或目前狀態已改變時，第一次核准顯示二次確認；確認文案必須列出申請類型、申請時間與目前既有紀錄，不能只顯示泛用的「確定嗎」。

## FTP 與 transaction

核准成功時仍先 commit `MakeupRequest` 與 `CheckIn`，再呼叫 `_try_supplemental_ftp_export()`。FTP 失敗只記錄 exception，不得回滾已核准資料或將 API 改回失敗。

本功能不改變補傳內容：仍查詢補卡日期所有符合資格的打卡，產生完整的 `factory_YYYYMMDD.txt`。HR 端究竟採覆蓋、upsert 或追加匯入，需要另案確認。

## 測試與驗收

### 單元與 API 測試

- `assess_makeup_day()`：覆蓋五種狀態及排序。
- `assess_makeup_day()`：覆蓋 `Asia/Taipei` 跨 UTC 日期邊界。
- `assess_makeup_day()`：紀錄新增後即使狀態仍為 `ambiguous`，snapshot token 也必須改變。
- day-status：正常回傳、未來日期、無效日期、未綁定與 inactive 員工。
- request：符合建議時建立申請並保存 audit。
- request：改選但未確認時回 `exception_confirmation_required`。
- request：確認例外後成功並保存 `exception_confirmed=true`。
- request：`complete`／`ambiguous` 未確認時拒絕。
- request：`no_records` 允許自行選擇且不標記例外。
- request：送出前狀態改變時回 `stale_day_state`，不建立申請。
- request：裝置 timezone 不參與補卡 instant 計算；legacy payload 得到可由舊版 UI 顯示的 refresh 訊息。
- pending：回傳送出時 audit 與目前紀錄，legacy 欄位可為空。
- review：一般申請一鍵核准；例外或狀態變更需二次確認。
- review：二次確認後只建立一筆 `CheckIn`，保留原有 concurrent review 防護。
- review：核准後仍觸發正確日期的 FTP 補傳；FTP 失敗不影響核准。

### Migration 與整體驗證

- `uv run --with-requirements requirements-dev.txt pytest tests/test_liff.py tests/test_jobs.py -q`
- `uv run --with-requirements requirements-dev.txt pytest -q`
- `uv run --with-requirements requirements-dev.txt ruff check app/`
- 在空白 PostgreSQL 執行 `alembic upgrade head`，確認 `005` upgrade。
- 執行 `alembic downgrade 004`，確認只移除新增欄位，再升回 head。

### 手機驗收

- LINE LIFF 實機檢查日期切換、loading、既有紀錄與建議顯示。
- 在窄螢幕確認上下班控制、警告與 checkbox 不重疊或截斷。
- 驗證符合建議、忽略建議、完整日、無紀錄及狀態過期五條流程。
- 驗證主管一般核准與例外二次確認。

## 部署與 rollback

部署順序固定為：以同一 commit source、production runtime service account、Cloud SQL attachment 與 `DATABASE_URL` 建立／更新一次性 Cloud Run migration job → 執行 `alembic upgrade head` 並等待成功 → 才部署同一 commit 的 service revision。migration job 失敗必須停止 deployment；不得先部署會讀取 `005` 欄位的應用程式。實作前先用 read-only `gcloud run services describe` 與 `gcloud sql instances describe` 核對實際 service account、instance connection name 與網路設定，不可只依腳本推斷。

新增欄位對舊資料提供 nullable／default，因此 migration 可以安全地先於應用程式部署，且不需要資料回填。migration job 不得輸出 `DATABASE_URL` 或其他 secret 值。

若應用程式需 rollback，可先回退至舊版程式並保留新增欄位；舊程式不讀取它們，不影響既有流程。只有確認不再需要 audit 資料時才執行 downgrade 至 `004`，避免不可逆地遺失新申請的確認紀錄。
