# LINE 打卡系統設計規格

**日期：** 2026-04-13
**更新：** 2026-04-13（部署平台改為 GCP，PR 工作流程）
**專案：** line-clockio-bd
**狀態：** 進行中

---

## 0. 專案工作流程（Project Workflow）

本專案為公司內部小型專案，以 PRD 作為唯一核心文件（不需 SOW 或 NRE Budget）。

### 0.1 PRD 結構

```
PRD
├── Product Summary      ← 第 1 節（背景與目標）
├── Product Spec         ← 第 3–8 節（架構、DB、流程、安全、MVP 範圍）
│   ├── * 功能清單（Features）
│   └── * 詳細規格（Specifications）
└── Timeline             ← 第 0.2 節（下方）
```

### 0.2 開發時程（Timeline）

```
4/13        4/14              4/17          4/18–4/23       4/24–25
  │            │                │               │               │
  ▼            ▼                ▼               ▼               ▼
[PRD 完成] → [核准 + Kickoff] → [MVP 交付] → [Sprint 衝刺] → [Production]
  今天         明天              本週五        下週完整功能     正式上線
  規格定稿     開始開發          核心流程可用   Dashboard+QA    所有員工可用
```

**各階段目標：**

- **PRD 完成（4/13，今天）：** 規格確定，交由主管與 PM 審閱。

- **核准 + Kickoff（4/14，明天）：** PRD 核准；完成開發環境建置（GCP Cloud Run/Cloud SQL、LINE Developer Console（需 Tim 協助開帳號）、GitHub）；確認分工。

- **MVP（4/17，本週五）：** 員工綁定（OTP）可跑通；LIFF 打卡（Geolocation + IP）寫入 DB；重複打卡防護；Webhook 基本路由正常。

- **Sprint（4/18–4/23）：** 主管 Web Dashboard（登入、篩選、CSV 匯出）；主管 LINE 查詢指令；iOS／Android 實機測試；Bug 修正。

- **Production（4/24–4/25）：** GCP Cloud Run 正式部署；Webhook URL 設定完成；員工完成綁定培訓；正式上線。

---

## 1. 背景與目標

公司希望以 LINE 作為員工打卡介面，取代傳統紙本或獨立 App，純粹用於稽核目的。目標在 AI 驅動開發流程下，1～2 天內完成規格確定並開始開發。

**成功標準：**
- 員工可透過 LINE 完成上班 / 下班打卡，並記錄真實位置與 IP
- 主管可透過 Web Dashboard 或 LINE 指令查詢出勤紀錄
- 系統可在 GCP 上穩定運行，資料保存至少 5 年

---

## 2. 範疇限制

- 員工人數：50 人以下
- 部署平台：GCP（Cloud Run + Cloud SQL PostgreSQL）— 公司已有帳號，無需另外申請
- 前端：LIFF mini-app（僅打卡用）；主管後台為 Jinja2 server-side render
- LINE 開發者帳號：需 Tim 協助建立 LINE Official Account + Messaging API + LIFF App
- 不包含：薪資計算、排班管理、HR 系統整合、多主管權限層級

---

## 3. 系統架構

```
┌─────────────────────────────────────────┐
│              LINE Platform              │
│  Messaging API Webhook  │  LIFF App     │
└────────────┬────────────┴───────┬───────┘
             │ Webhook events      │ HTTPS API calls
             ▼                    ▼
┌─────────────────────────────────────────┐
│        FastAPI (GCP Cloud Run)          │
│                                         │
│  POST /webhook       文字訊息互動        │
│  POST /liff/checkin  打卡（位置+IP）     │
│  GET  /dashboard     主管後台（Jinja2）  │
│  GET  /api/records   出勤查詢 API        │
└─────────────────┬───────────────────────┘
                  │
       ┌──────────▼──────────┐
       │  PostgreSQL          │
       │  (GCP Cloud SQL)     │
       └─────────────────────┘
```

**技術選型：**

| 項目 | 選擇 | 理由 |
|------|------|------|
| 後端框架 | FastAPI (Python) | 開發速度快、自動 API 文件 |
| 資料庫 | PostgreSQL（GCP Cloud SQL）| 生產級穩定，公司 GCP 帳號直接使用 |
| 打卡介面 | LINE LIFF mini-app | 唯一能取得 Geolocation + 真實 IP 的方式 |
| 主管後台 | Jinja2 templates | 後端工程師可直接開發，無需前端框架 |
| 部署 | GCP Cloud Run | 容器化、HTTPS 自動、按用量計費、公司已有帳號 |
| Email 發送 | Resend（免費方案）| OTP 發送，比 Gmail SMTP 穩定 |

### 部署平台比較

| 平台 | 估計月費 | 優點 | 缺點 | 結論 |
|------|---------|------|------|------|
| **GCP Cloud Run + Cloud SQL** | Cloud SQL db-f1-micro ~$7/月；Cloud Run 免費額度高 | **公司已有帳號**，無需另外申請付款；企業級安全；IAM 整合 | 設定流程較繁瑣（Dockerfile、gcloud CLI）| ✅ **選用** |
| Railway | $5/月 Hobby Plan | 設定最簡單，一鍵部署 | 需另開帳號付款；資料不在公司 GCP 環境內 | — |
| Fly.io | 免費方案有限，付費按用量 | FastAPI 友善，全球 edge | 需另開帳號；台灣 region 較少 | — |
| Zeabur（台灣） | 按用量計費 | 台灣服務，中文支援 | 較新，生態較小，穩定性待驗證 | — |
| Linode (Akamai) | VPS $5/月起 | 成本低、彈性高 | 需自行管理伺服器、SSL、監控 | — |

---

## 4. 資料庫設計

```sql
-- 員工帳號（LINE ↔ email 綁定後建立）
employees
  id              SERIAL PRIMARY KEY
  line_user_id    VARCHAR(50)  UNIQUE NOT NULL  -- LINE Uid（Uxxxxxxx 格式）
  email           VARCHAR(100) UNIQUE NOT NULL  -- 公司 email
  display_name    VARCHAR(100)                  -- 從 LINE profile 取得
  is_active       BOOLEAN DEFAULT true
  created_at      TIMESTAMPTZ DEFAULT now()

-- 打卡紀錄
check_ins
  id              SERIAL PRIMARY KEY
  employee_id     INTEGER REFERENCES employees(id)
  type            VARCHAR(10) NOT NULL           -- 'clock_in' | 'clock_out'
  checked_at      TIMESTAMPTZ DEFAULT now()
  latitude        DOUBLE PRECISION NOT NULL      -- 強制，拒絕授權則無法打卡
  longitude       DOUBLE PRECISION NOT NULL
  ip_address      VARCHAR(50) NOT NULL           -- 從 X-Forwarded-For 取得
  created_at      TIMESTAMPTZ DEFAULT now()

-- 主管帳號（Web Dashboard 登入用）
managers
  id              SERIAL PRIMARY KEY
  username        VARCHAR(50)  UNIQUE NOT NULL
  password_hash   VARCHAR(255) NOT NULL          -- bcrypt hash
  email           VARCHAR(100)
  created_at      TIMESTAMPTZ DEFAULT now()

-- Email OTP 驗證（員工綁定流程用）
email_verifications
  id              SERIAL PRIMARY KEY
  line_user_id    VARCHAR(50)  NOT NULL
  email           VARCHAR(100) NOT NULL
  otp_code        VARCHAR(6)   NOT NULL
  expires_at      TIMESTAMPTZ  NOT NULL          -- 建立後 10 分鐘過期
  used            BOOLEAN DEFAULT false
  created_at      TIMESTAMPTZ DEFAULT now()
```

---

## 5. 核心流程

### 5.1 員工綁定（一次性）

```
1. 員工加入 LINE 官方帳號
2. 傳送公司 email（如 john@company.com）
3. 系統發 6 位 OTP 到該 email（10 分鐘有效）
4. 員工回傳 OTP
5. 驗證通過 → 建立 employees 記錄，LINE user ID ↔ email 綁定完成
6. 機器人回覆「綁定成功，可以開始打卡了 ✓」
```

### 5.2 打卡流程（每日）

```
1. 員工點選 LINE Rich Menu 的「上班打卡」或「下班打卡」按鈕
2. 開啟 LIFF mini-app（LINE 內建瀏覽器）
3. LIFF 呼叫 liff.getProfile() 確認 LINE 身份
4. 請求 Geolocation 權限（強制）
   ✗ 拒絕 → 顯示「需要開啟定位權限才能打卡」，流程終止
   ✓ 同意 → 取得 latitude / longitude
5. POST /liff/checkin { type, latitude, longitude }
   → 後端從 X-Forwarded-For 取得 ip_address
6. 後端驗證：
   - 員工已綁定？
   - 同類型打卡是否在 2 小時內重複？
7. 寫入 check_ins
8. LIFF 顯示「上班打卡成功 09:03」
```

### 5.3 主管查詢 — LINE 指令

```
主管傳：「查詢 2026-04」
→ 機器人回傳當月各員工出勤摘要（出勤天數、首次打卡、末次打卡）
```

### 5.4 主管查詢 — Web Dashboard

```
1. 登入（username / password）
2. 篩選：員工 / 日期範圍
3. 列出打卡紀錄（時間、類型、位置、IP）
4. 下載 CSV
```

---

## 6. 安全設計

| 項目 | 措施 |
|------|------|
| LINE Webhook 防偽造 | 驗證 `X-Line-Signature` header（HMAC-SHA256）|
| 主管密碼 | bcrypt hash，不明文儲存 |
| 主管 Session | FastAPI session cookie，設定 HttpOnly + Secure |
| LIFF 身份驗證 | 後端用 LINE LIFF ID Token 驗證（`/oauth2/v2.1/verify`）|
| OTP 防爆破 | 10 分鐘過期，用完即標記 `used=true` |

---

## 7. 風險與對策

| 風險 | 說明 | 對策 |
|------|------|------|
| LIFF Geolocation 在 iOS LINE 受限 | iOS 需使用者手動允許 | 上線前實機測試，文件說明如何開啟定位權限 |
| Email OTP 進垃圾信 | 員工收不到 OTP | 使用 Resend，設定 SPF/DKIM |
| GCP Cloud Run 冷啟動 | 無流量時 container 回收，首次回應約 1–3 秒 | 設定最小 instance 數為 1（min-instances=1），或設定 Cloud Scheduler keep-alive |
| LINE 帳號設定延遲 | 需 Tim 協助開 LINE Developer 帳號 | 提早聯繫 Tim，不要等到開發完才申請 |
| 時區顯示錯誤 | 打卡時間顯示不對 | 全程 TIMESTAMPTZ，後端統一轉 UTC+8 顯示 |
| 代打（借手機） | 無法 100% 防止 | Geolocation + IP 已提供稽核證據，足夠稽核用途 |

---

## 8. MVP 範圍

**必做（MVP）：**
- [ ] 員工 email 綁定（OTP 驗證）
- [ ] LIFF 打卡（強制 Geolocation + IP，上班 / 下班）
- [ ] 重複打卡防護（同類型 2 小時內不可重複）
- [ ] 主管 Web Dashboard（登入、篩選、查看紀錄、CSV 匯出）
- [ ] 主管 LINE 查詢（月份摘要）

**後 MVP（暫不做）：**
- Rich Menu 視覺設計
- LINE 推播打卡確認通知
- 多主管權限層級
- 異常打卡告警（如地點異常）

---

## 9. 第一天 / 第二天工作拆分

### Day 1 — 地基建設 + 核心打卡

**上午：環境設定**
- LINE Developer Console：聯繫 Tim 建立 Official Account + Messaging API Channel + LIFF App
- GCP：建立 Cloud Run Service、Cloud SQL PostgreSQL instance、設定環境變數（Secret Manager）
- GitHub repo 確認、推上 spec + plan（開 PR 流程）

**下午：核心功能**
- FastAPI 骨架 + SQLAlchemy models + Alembic migration
- Webhook 端點（LINE signature 驗證 + 訊息路由）
- 員工綁定流程（接收 email → 寄 OTP via Resend → 驗證 → 寫 DB）
- LIFF mini-app HTML（Geolocation 取得 → POST /liff/checkin）
- 打卡 API 端點（LIFF ID Token 驗證 + 寫 check_ins）

### Day 2 — Dashboard + 收尾部署

**上午：主管功能**
- 主管登入（session auth + bcrypt）
- Jinja2 Dashboard（列表、篩選員工 / 日期）
- CSV 匯出

**下午：整合 + 測試**
- 主管 LINE 查詢指令（「查詢 YYYY-MM」）
- 重複打卡防護邏輯
- GCP Cloud Run 部署、設定 Webhook URL（在 LINE Developer Console）
- 實機測試（iPhone + Android）
- 推上 GitHub（開 PR 供主管 review）

---

## 10. 環境變數清單

```env
# LINE（需 Tim 協助設定）
LINE_CHANNEL_ACCESS_TOKEN=
LINE_CHANNEL_SECRET=
LIFF_ID=
LIFF_CHANNEL_ID=        # LINE Login channel ID，用於 LIFF ID Token 驗證

# Database（GCP Cloud SQL）
DATABASE_URL=postgresql://user:pass@/dbname?host=/cloudsql/project:region:instance

# Email (Resend)
RESEND_API_KEY=
RESEND_FROM_EMAIL=      # 需在 Resend 驗證的寄件網域

# Auth
SESSION_SECRET_KEY=     # openssl rand -hex 32

# App
APP_BASE_URL=           # GCP Cloud Run 提供的 HTTPS URL（e.g. https://xxx.run.app）
TIMEZONE=Asia/Taipei
```

> **GCP 建議做法：** 將以上 secrets 儲存於 GCP Secret Manager，在 Cloud Run 透過 `--set-secrets` 或 Volume Mount 注入，避免在 `.env` 或環境變數中明文存放。
