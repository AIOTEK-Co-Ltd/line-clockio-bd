# AGENTS.md

本文件是 AI coding agent 在此 repository 的專案級操作說明。人類交接與操作手冊請先讀 `README.md`。

## 專案摘要

- AIOTEK 內部 LINE 打卡系統，服務對象少於 50 人。
- Python 3.12、FastAPI、SQLAlchemy 2、Alembic、Jinja2；production 為 GCP Cloud Run + Cloud SQL PostgreSQL。
- 外部整合：LINE Messaging API、LINE Login/LIFF、Mailgun、工廠 FTP、Cloud Scheduler。
- 主要入口是 `app.main:app`。正式環境預設關閉 `/docs` 與 `/redoc`，健康檢查為 `/health`。
- `docs/superpowers/` 內的 spec/plan 是歷史文件，已與現況有差異，不能單獨視為 source of truth。

## 開始工作前

1. 先讀 `README.md` 的「重要業務規則」與「已知問題」。
2. 檢查 `git status --short --branch`，保留使用者的既有修改。
3. 依任務讀最小必要範圍：router → service → model/migration → 對應 tests。
4. 把任務轉成 `step → verify`；bugfix 必須先重現，功能修改必須先定義可觀察結果。
5. 不要因為歷史 spec 與 code 不同就擅自把 code 改回舊規格；先確認目前業務需求。

## Source of truth 與模組地圖

| 領域 | Source of truth | 對應測試 |
| --- | --- | --- |
| 設定 | `app/config.py`、`.env.example` | `tests/conftest.py` |
| DB schema | `migrations/versions/`；model 必須同步 | 各 router tests（目前未測 migration） |
| LINE 綁定／OTP／文字指令 | `app/routers/webhook.py` | `tests/test_webhook.py` |
| LIFF 打卡／補打卡／卡號 | `app/routers/liff.py` | `tests/test_liff.py` |
| Dashboard／員工匯入／匯出 | `app/routers/dashboard.py`、templates | `tests/test_dashboard.py` |
| 工廠排程匯出 | `app/routers/jobs.py`、`app/services/ftp_export.py` | `tests/test_jobs.py` |
| 加班計算 | `app/services/overtime.py` | `tests/test_overtime.py` |
| Deploy | `.github/workflows/ci.yml`、`Dockerfile`、`deploy.sh`、`scripts/` | 無整合測試 |

## 不可破壞的現行規則

- Email 綁定只接受小寫化後以 `@aiotek.com.tw` 結尾的地址。
- OTP 是 6 位數、10 分鐘有效、最多錯 5 次；只能保存 `_hash_otp()` 結果，禁止明文落庫或 log。只有 `DEBUG=true` 且 Mailgun 未設定時可在 LINE reply 顯示 OTP。
- LINE webhook 必須先驗證 `X-Line-Signature`；LIFF API 必須由 LINE verify endpoint 驗證 ID token，不能信任前端傳入的 user ID。
- 時間必須是 timezone-aware。DB 保存 UTC／TIMESTAMPTZ，顯示與日界線依 `Settings.timezone`（預設 `Asia/Taipei`）。
- 一般下班打卡必須已有同日上班；同日同類型不可重複。若修改併發行為，要同時考慮 DB constraint 與 API idempotency。
- 補打卡時間必須含 timezone 且早於現在；只有 manager 可審核。核准會新增 `CheckIn`，GPS 為 `0.0, 0.0`、IP marker 為 `makeup:approved`，並在 commit 後 best-effort 補傳 FTP；FTP 失敗不得回滾核准。
- 補打卡類型建議以 `Settings.timezone` 的本地日期查詢 DB；前端只負責顯示，request 與 approve 都必須重新判斷。與建議不同、當日完整或紀錄異常時，需保留員工 audit 並要求主管二次確認。
- 每日工時採第一筆 `clock_in` 到最後一筆 `clock_out`，固定扣 60 分鐘午休；加班超過 8 小時後，以 30 分鐘向下計算，分 2h + 2h 級距。不要依舊 spec 改成不扣午休。
- 員工卡號固定 8 碼英數、儲存為大寫且全表唯一。修改格式時須同步 `CARD_NUMBER_RE`、Pydantic validation、LIFF JavaScript regex、DB migration、匯入與 tests。
- 工廠匯出只包含有卡號的 active employee；格式為 `machine_id,card_number,YYYY/MM/DD,HH:MM:SS`。每日 job 的空檔是刻意行為，不能因 `lines == []` 跳過上傳。單一日期的組檔邏輯集中在 `ftp_export.build_factory_day_file()`，`jobs.py` 與 `liff.py` 補傳都必須經由它，不要再各自組檔。
- Dashboard 寫入型 POST 必須驗證 CSRF token。CSV 輸出文字欄位必須經 `_csv_safe()` 防止 formula injection。
- Production session cookie 必須維持 `https_only=True`；API docs 只在 debug 開啟。
- Production migration 必須由單一 Cloud Run migration job 在 service deploy 前完成；Cloud Run service container startup 不得自行執行 Alembic。

## 開發與驗證指令

本 repo 尚未有 `pyproject.toml`／`uv.lock`，目前使用 `requirements.txt` 與 `requirements-dev.txt`。除非任務明確要求依賴遷移，不要順手更換套件管理結構或升級 major versions。

```bash
# 一次性建立本機環境
uv venv
uv pip install -r requirements-dev.txt

# 測試
uv run pytest -q

# 不建立持久 venv 的等價驗證
uv run --with-requirements requirements-dev.txt pytest -q

# CI 現行 lint 範圍
uv run --with-requirements requirements-dev.txt ruff check app/

# 修改測試或準備擴大 CI 時
uv run --with-requirements requirements-dev.txt ruff check app/ tests/
```

最小驗證矩陣：

| 修改範圍 | 至少執行 |
| --- | --- |
| `webhook.py`、Mailgun | `pytest tests/test_webhook.py -q` |
| `liff.py`、LIFF template | `pytest tests/test_liff.py tests/test_overtime.py -q`；另做 LINE 手機實機驗證 |
| `dashboard.py`、Dashboard template | `pytest tests/test_dashboard.py -q` |
| `jobs.py`、FTP service | `pytest tests/test_jobs.py tests/test_dashboard.py -q` |
| models／migration | 完整 pytest；另用空白 PostgreSQL 跑 `alembic upgrade head` |
| config／startup／dependencies | 完整 pytest、ruff、`uv run uvicorn app.main:app` smoke test |
| deploy／secrets | 完整 pytest、ruff、Docker build；以 read-only gcloud 指令核對目標資源後才部署 |

完成前回報實際執行的命令與結果。不要把 SQLite unit tests 宣稱為 PostgreSQL migration 或外部整合已驗證。

## 實作慣例

- 採最小、局部修改；不要順手重排 400–900 行 router/template 或清理無關 dead code。
- 新增／修改的公開 Python 函數要有 type hints、精簡 docstring 與正常／邊界測試。
- 使用參數化 SQLAlchemy query；外部輸入交由 Pydantic／FastAPI 驗證，錯誤回明確 4xx。
- async route 內的 LINE／Mailgun HTTP 使用 `httpx.AsyncClient`。FTP 是 blocking I/O；若調整呼叫位置，避免阻塞 event loop，並維持既有 failure semantics。
- DB transaction 要明確：外部通知通常在 commit 後執行，避免 DB rollback 但信件／FTP 已不可逆地送出。
- 新 migration 只能新增新 revision，不能改已部署的 `001`–`004` 歷史內容；upgrade/downgrade 與 SQLAlchemy model 要同步。
- 複製日期篩選邏輯前，優先使用 `build_checkin_query()`，並保留 local calendar date → timezone-aware boundary 的行為。
- Python 用 `uv`，不要使用 `pip` 指令；Node.js 工具若未來需要，一律用 `bun`。
- Commit message 使用 `type(scope): subject`；有 ClickUp 單號時使用 `type(scope): [CU-xxxxx] subject`。

## 外部服務與敏感資料

- 不得 commit `.env`、`scripts/.secrets.env`、LINE token、Mailgun key、DB password、FTP password、session/internal secrets 或 service-account JSON。
- Production 設定應放 GCP Secret Manager。先以 read-only 指令確認 GCP project、Cloud Run service、Cloud SQL instance 與 secret 名稱，再提出或執行變更。
- 現行固定資源：project `aiotek-bot`、region `asia-east1`、service `line-clockio`；Cloud SQL 名稱在腳本間不一致，不能憑單一檔案推斷。
- 不要對真實 LINE user、Email、FTP 或 production DB 發送測試資料，除非使用者明確授權並指定測試帳號／目標。
- FTP 是明文協定；不得在 log 中輸出認證資訊或完整出勤檔內容。

## 已知技術債（除非任務要求，不要順手修）

- `setup_cloud_sql.sh` 與 deploy/CI 使用不同 Cloud SQL instance 名稱。
- Secret setup/template 缺少 deploy 所需的 FTP 與 `INTERNAL_SECRET` 欄位；CI 也不負責首次建立 runtime config。
- Cloud SQL setup 關閉 public IP，但 deploy 沒有 VPC 網路設定；重建前需先確認實際 topology。
- 一般打卡缺少資料庫級併發冪等 constraint。
- LINE 主管月份查詢錯用 UTC 月界線，沒有依 `TIMEZONE` 轉換。
- Webhook event 例外被轉成整體 HTTP 200，LINE reply response 未檢查；OTP 無 rate limit。
- 員工 CSV 匯入未驗證公司 Email，且寄信失敗可能發生在 DB commit 之後。
- Dashboard session 不會在每次請求重新查 manager active/role。
- `X-Forwarded-For` 信任邊界未明確，FTP 無傳輸加密。
- `migrations/env.py` 的 `target_metadata = None`，`alembic revision --autogenerate` 會直接失敗（大聲失敗、不會靜默產生空 migration），model↔migration 漂移已無自動偵測手段。原因是 migration job 只有 `DATABASE_URL` secret，import `app.config.get_settings()` 會因缺其他必填欄位而爆掉；要恢復偵測需改成 lazy import 或在 CI 加 `alembic check`。
- migration `005` 的 `system_suggested_type` 宣告為 `sa.String(20)`，model 是 `Enum(CheckInType, native_enum=False)`。兩者在 PostgreSQL 上都是 VARCHAR 且無 CHECK constraint，行為一致；但寬度宣告不同，未來 autogenerate 比對會有噪音。
- 測試沒有覆蓋 Alembic on PostgreSQL、Cloud Run、LINE、Mailgun、FTP 與手機 LIFF E2E。
- Repo 沒有可執行的資料保留、backup/PITR、還原演練與監控告警設定。

若本次任務碰到以上項目，先新增能重現風險的測試，再做最小修正；若需要改 business rule、DB constraint、外部資源或部署拓撲，先向使用者列出影響與 migration／rollback 方案。
