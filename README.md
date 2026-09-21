# LINE Clockio

AIOTEK 內部使用的 LINE 打卡系統。員工在 LINE LIFF mini-app 完成上下班打卡、查看當月出勤與送出補打卡申請；主管可在 LIFF 審核補打卡，或透過 Web Dashboard 管理員工、查詢及匯出紀錄。系統部署於 GCP Cloud Run，資料存放於 Cloud SQL for PostgreSQL，並可將打卡資料匯出至工廠 FTP。

## 目前功能

- LINE Official Account 綁定：限定 `@aiotek.com.tw` Email，以 Mailgun 寄送 6 位數 OTP；OTP 有效 10 分鐘、最多嘗試 5 次，資料庫僅保存雜湊。
- LIFF 打卡：後端驗證 LINE ID token，記錄伺服器時間、GPS 經緯度與來源 IP；每天各允許一次上班及下班打卡，下班前必須已有上班紀錄。
- 出勤紀錄：顯示當月每日首筆上班、末筆下班與加班統計。
- 補打卡：員工送出申請，主管可在 LIFF 核准或拒絕；核准會建立打卡紀錄，並嘗試補傳該日期的工廠檔案。
- 主管 Dashboard：以 LINE Login 登入，提供紀錄篩選、CSV 匯出、員工 CSV 匯入、邀請信與工廠格式匯出。
- 排程匯出：內部 API 將前一日有卡號的打卡紀錄上傳 FTP；即使沒有紀錄也會產生空檔。
- LINE 主管指令：傳送 `query YYYY-MM` 查詢月份摘要。

## 系統架構

```text
LINE Messaging API ── POST /webhook ─┐
LINE LIFF ─────────── /liff/* ───────┼── FastAPI / Cloud Run ── Cloud SQL (PostgreSQL)
LINE Login ────────── /dashboard/* ──┤              │
Cloud Scheduler ───── /internal/* ───┘              ├── Mailgun
                                                    └── Factory FTP
```

主要程式位置：

| 路徑 | 職責 |
| --- | --- |
| `app/routers/webhook.py` | LINE webhook、Email/OTP 綁定、卡號設定、主管文字查詢 |
| `app/routers/liff.py` | LIFF 頁面、打卡、出勤紀錄、補打卡、卡號更新 |
| `app/routers/dashboard.py` | 主管 LINE Login、Dashboard、CSV/工廠檔案匯出、員工匯入 |
| `app/routers/jobs.py` | Cloud Scheduler 呼叫的前一日 FTP 匯出 |
| `app/services/overtime.py` | 工時與加班計算 |
| `app/services/ftp_export.py` | 工廠檔案格式與 FTP 上傳 |
| `app/models/`、`migrations/` | SQLAlchemy models 與 Alembic migrations |

## 重要業務規則

- 系統時區預設為 `Asia/Taipei`，資料庫時間使用 timezone-aware datetime。
- 同一員工同一天只能有一筆一般上班與一筆一般下班打卡；主管核准補打卡時可明確覆蓋這項限制。
- 補打卡會依員工在所選本地日期的既有 `clock_in`／`clock_out` 建議缺少的類型；員工可以改選，但必須確認例外，主管核准時也會重新檢查並二次確認異常狀態。
- 每日工時取「第一筆上班」到「最後一筆下班」，固定扣除 60 分鐘午休，再以 8 小時為正常工時。
- 加班以 30 分鐘為單位向下計算；前 2 小時為第一級、後 2 小時為第二級；每日超過 4 小時、每月超過 46 小時會標示警告。
- 工廠檔案格式為 `機台編號,員工卡號,YYYY/MM/DD,HH:MM:SS`，僅包含已設定 8 碼英數卡號的員工。
- GPS 目前僅作稽核紀錄，沒有 geofence 或距離判斷。

## 本機開發

需求：Python 3.12、[uv](https://docs.astral.sh/uv/)、PostgreSQL。

```bash
uv venv
uv pip install -r requirements-dev.txt
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

服務預設位於 <http://127.0.0.1:8000>。`DEBUG=true` 時才會開放 `/docs` 與 `/redoc`；健康檢查為 `GET /health`。

目前專案仍以 `requirements*.txt` 管理依賴，尚未建立 `pyproject.toml`／`uv.lock`。本機測試使用 SQLite 且透過 SQLAlchemy metadata 建表；正式環境與 Alembic migration 以 PostgreSQL 為準。

### 環境變數

| 變數 | 必要性 | 說明 |
| --- | --- | --- |
| `DEBUG` | 選填 | 預設 `false`；本機可設 `true` |
| `APP_BASE_URL` | 必填 | 對外 HTTPS base URL，不含結尾 `/` |
| `TIMEZONE` | 選填 | 預設 `Asia/Taipei` |
| `LINE_CHANNEL_ACCESS_TOKEN` | 必填 | Messaging API channel access token |
| `LINE_CHANNEL_SECRET` | 必填 | 驗證 webhook signature |
| `LIFF_ID` | 啟用 LIFF 時必填 | LIFF app ID |
| `LIFF_CHANNEL_ID` | 啟用 LIFF／Dashboard 時必填 | LINE Login channel ID |
| `LIFF_CHANNEL_SECRET` | 啟用 LIFF／Dashboard 時必填 | LINE Login channel secret |
| `DATABASE_URL` | 必填 | SQLAlchemy PostgreSQL URL；Cloud Run 使用 Cloud SQL Unix socket |
| `MAILGUN_API_KEY` | 寄信時必填 | Mailgun API key |
| `MAILGUN_FROM_EMAIL` | 寄信時必填 | 已驗證網域的寄件地址 |
| `SESSION_SECRET_KEY` | 必填 | Session 簽章密鑰，可用 `openssl rand -hex 32` 產生 |
| `FACTORY_MACHINE_ID` | 選填 | 工廠檔案機台編號，預設 `0000000002` |
| `FTP_HOST` | FTP 匯出時必填 | 工廠 FTP host |
| `FTP_USER` | FTP 匯出時必填 | FTP 帳號 |
| `FTP_PASSWORD` | FTP 匯出時必填 | FTP 密碼 |
| `FTP_REMOTE_DIR` | 選填 | 遠端目錄，預設 `/` |
| `INTERNAL_SECRET` | 排程匯出時必填 | `X-Internal-Secret` header 的共享密鑰 |

`.env.example` 目前尚未列出最後 6 個工廠／排程變數，交接後應優先補齊。

### LINE 與管理員設定

LINE Developers Console 需設定：

- Messaging API webhook：`https://<domain>/webhook`
- LIFF endpoint：`https://<domain>/liff/`
- LINE Login callback：`https://<domain>/dashboard/callback`
- Rich Menu：依 `rich_menu.json` 建立；LIFF ID 異動時同步更新其中的 URI

Dashboard 沒有獨立密碼帳號。使用者先完成 LINE/Email 綁定，再由資料庫管理者將該 `employees` row 的 `is_manager` 設為 `true`，之後以同一個 LINE 帳號登入 `/dashboard/login`。

### 員工 CSV 匯入

Dashboard 的「員工管理」接受 UTF-8 BOM 或 Big5 CSV。欄位可使用中文或英文名稱：

```csv
員工編號,姓名,Email,員工卡號
A001,王小明,michael@aiotek.com.tw,A1234567
```

Email 為必要欄位；卡號必須是 8 碼英數字。整批若發生 unique constraint 衝突會全部 rollback。

## 測試與檢查

```bash
# 完整測試
uv run --with-requirements requirements-dev.txt pytest -q

# 與目前 CI 相同的 lint 範圍
uv run --with-requirements requirements-dev.txt ruff check app/

# 建議交接後擴大到測試程式
uv run --with-requirements requirements-dev.txt ruff check app/ tests/
```

2026-08-31 本機驗證結果：`110 passed`；`ruff check app/` 通過。完整 `app/ tests/` lint 尚有 1 個測試檔未使用變數，詳見下方已知問題。

## 部署

Push 到 `main` 時，GitHub Actions 會先 lint、test，再用同一份 checkout source 建立／更新 `line-clockio-migrate` Cloud Run Job。Job 以單一 task、parallelism 1、max retries 0 執行 `alembic upgrade head`，透過 `--wait` 等待成功後，才以 `gcloud run deploy --source .` 部署 service；migration 失敗會中止部署。Deploy job 設有 concurrency group 且 `cancel-in-progress: false`，避免 CI release 同時執行 migration。部署目標：

- GCP project：`aiotek-bot`
- Cloud Run service：`line-clockio`
- Region：`asia-east1`
- Cloud SQL connection：`aiotek-bot:asia-east1:line-clockio-db-new`
- Migration runtime service account：`600104370576-compute@developer.gserviceaccount.com`

Migration job 只注入 `DATABASE_URL` Secret Manager secret，使用上述 runtime service account 與 Cloud SQL connection。2026-09-15 read-only preflight 確認 service 沒有 Direct VPC／VPC connector，Cloud SQL 啟用 public IP 且未設定 private network，因此 job 沿用 Cloud SQL attachment，不另加 VPC 設定。Application instance 啟動時只執行 Uvicorn，不再執行 Alembic。

CI 需要 GitHub secret `GCP_SA_KEY`，部署身分須能建立、更新、執行 Cloud Run Job，並能 act as runtime service account。`deploy.sh` 的手動路徑會先 Docker build/push，再讓 migration job 與 service 使用同一個 `${IMAGE}`；同樣等待 migration 成功才部署 service。手動部署前需確認沒有 CI 或其他手動 release 正在進行。若 migration 失敗，先排除原因並重跑部署，不可跳過 gate；本次 `005` 是新增 audit 欄位，回退 application revision 時保留 schema，勿在新版本仍運行時 downgrade。部署相關腳本仍有下列設定漂移，重新建置環境前請先處理 P0 項目。

排程端點為 `POST /internal/jobs/factory_export`，Cloud Scheduler 必須帶 `X-Internal-Secret: <INTERNAL_SECRET>`。端點固定匯出「Asia/Taipei 前一日」，目前不支援 query parameter 指定補匯日期。

## 已知問題與交接優先順序

### P0：可能阻斷重建或部署

1. `scripts/setup_cloud_sql.sh` 建立 `line-clockio-db`，但 `deploy.sh` 與 CI 使用 `line-clockio-db-new`；直接照腳本操作會連到不同 instance。
2. `deploy.sh` 需要 `FTP_USER`、`FTP_PASSWORD`、`FTP_REMOTE_DIR`、`INTERNAL_SECRET` 等 Secret Manager secrets，但 `scripts/setup_secrets.sh` 不會建立它們，`scripts/.secrets.env.example` 也未提供欄位。
3. GitHub Actions 的部署步驟不建立 runtime secrets／環境變數；它依賴 Cloud Run service 已事先設定完成。全新 project 僅靠 CI 無法完成首次部署。
4. Cloud SQL setup 使用 `--no-assign-ip`，但 Cloud Run deploy 沒有 VPC connector／Direct VPC 設定；用此腳本重建前必須確認 private IP 與 Cloud Run 的網路路徑，否則 instance 可能無法連線。

### P1：正確性與安全性

1. 一般打卡採「先查詢、再新增」，資料庫沒有每日類型 unique constraint；同時送出的請求仍可能寫入重複打卡。
2. LINE 主管 `query YYYY-MM` 以 UTC 00:00 切月份，而非 `TIMEZONE` 的本地月界線；台北時間每月首日 00:00–07:59 會漏查，次月首日同時段會被誤納。
3. Webhook 對單一 event 的例外只記 log，整體仍回 HTTP 200；失敗事件不會由 LINE 自動重送。LINE reply API 的非 2xx 回應也沒有檢查。
4. OTP 沒有每個 LINE UID／IP 的發送頻率限制，可能被用來消耗 Mailgun 配額。
5. 員工 CSV 匯入只檢查 Email 非空，沒有驗證格式或公司網域；可能建立並邀請一個之後會被 bot 拒絕綁定的帳號。Mailgun 未設定或網路例外時，資料已 commit，但匯入 request 仍可能回 500。
6. Dashboard 只檢查 session 是否有 `manager_id`，登入後若管理員被停權或移除權限，session 到期前仍可操作。
7. `X-Forwarded-For` 未限制可信 proxy；若服務可繞過 Cloud Run proxy 直接接觸，稽核 IP 可能被偽造。
8. 工廠傳輸使用明文 FTP，帳密與出勤資料沒有傳輸層加密；需確認對方是否支援 FTPS/SFTP 或以網路層限制來源。
9. `setup_cloud_sql.sh` 要求把 DB password 直接填入受版控腳本，容易誤 commit；應改成安全輸入或 Secret Manager 流程。

### P2：維護性與規格漂移

1. `docs/superpowers/specs/` 是早期歷史文件，多處已與實作不符：例如登入方式、Mailgun/Resend、每日重複規則，以及加班是否扣午休。現況以程式碼、migration、本 README 與 `AGENTS.md` 為準。
2. 專案沒有 `pyproject.toml`、`uv.lock`、type checker、coverage gate 或 pre-commit；production dependencies 也包含 `pytest`。
3. CI 只 lint `app/`。擴大到 `tests/` 時，`tests/test_liff.py` 有 1 個 `F841` 未使用變數。
4. 自動化測試使用 SQLite metadata 建表，沒有驗證 PostgreSQL-specific migration、Cloud SQL 連線、LINE／Mailgun／FTP 真實整合與 LIFF 手機實機流程。
5. 日誌使用標準 `logging`，尚無 structured logging、request correlation 或監控／告警設定。
6. Repository 內沒有資料保留 5 年、Cloud SQL backup／PITR、還原演練或監控告警的可執行設定；不能僅依早期 spec 視為已落實。

## 交接建議

1. 先統一 Cloud SQL instance 名稱與 secrets 清單，實際盤點 Cloud Run、Secret Manager、Cloud Scheduler、LINE Developers、Mailgun 及 FTP 的擁有者與權限。
2. 用 staging PostgreSQL 從空資料庫執行 `alembic upgrade head`，再做一次備份／還原演練。
3. 補一般打卡的資料庫級冪等保護、OTP rate limit，以及 webhook 外部 API 錯誤處理。
4. 將依賴遷移到 `pyproject.toml` + `uv.lock`，讓 CI 使用 `uv sync --frozen`，並加入 PostgreSQL integration test 與 coverage gate。
5. 以 iOS、Android LINE 實機驗證定位權限、跨午夜打卡、補打卡審核、卡號更新與 Rich Menu。
