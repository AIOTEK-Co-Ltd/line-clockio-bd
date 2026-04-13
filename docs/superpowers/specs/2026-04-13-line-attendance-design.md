# LINE 打卡系統設計規格

**日期：** 2026-04-13
**專案：** line-clockio-bd
**狀態：** 待審閱

---

## 1. 背景與目標

公司希望以 LINE 作為員工打卡介面，取代傳統紙本或獨立 App，純粹用於稽核目的。目標在 AI 驅動開發流程下，1～2 天內完成規格確定並開始開發。

**成功標準：**
- 員工可透過 LINE 完成上班 / 下班打卡，並記錄真實位置與 IP
- 主管可透過 Web Dashboard 或 LINE 指令查詢出勤紀錄
- 系統可在 Railway 上穩定運行，資料保存至少 5 年

---

## 2. 範疇限制

- 員工人數：50 人以下
- 部署平台：Railway（含 PostgreSQL）
- 前端：LIFF mini-app（僅打卡用）；主管後台為 Jinja2 server-side render
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
│           FastAPI (Railway)             │
│                                         │
│  POST /webhook       文字訊息互動        │
│  POST /liff/checkin  打卡（位置+IP）     │
│  GET  /dashboard     主管後台（Jinja2）  │
│  GET  /api/records   出勤查詢 API        │
└─────────────────┬───────────────────────┘
                  │
       ┌──────────▼──────────┐
       │  PostgreSQL          │
       │  (Railway add-on)    │
       └─────────────────────┘
```

**技術選型：**

| 項目 | 選擇 | 理由 |
|------|------|------|
| 後端框架 | FastAPI (Python) | 開發速度快、自動 API 文件 |
| 資料庫 | PostgreSQL | 生產級穩定，Railway 一鍵整合 |
| 打卡介面 | LINE LIFF mini-app | 唯一能取得 Geolocation + 真實 IP 的方式 |
| 主管後台 | Jinja2 templates | 後端工程師可直接開發，無需前端框架 |
| 部署 | Railway | HTTPS 自動提供，GitHub push 自動部署 |
| Email 發送 | Resend（免費方案） | OTP 發送，比 Gmail SMTP 穩定 |

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
| Railway 冷啟動 | 免費方案 sleep 後首次回應慢 | 升級至 Hobby Plan（$5/月）或設 keep-alive ping |
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
- LINE Developer Console：建立 Official Account + Messaging API Channel + LIFF App
- Railway：建立 Project、PostgreSQL、設定環境變數
- GitHub repo 確認、推上 spec + plan

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
- Railway 正式部署、Webhook URL 設定
- 實機測試（iPhone + Android）
- 推上 GitHub

---

## 10. 環境變數清單

```env
# LINE
LINE_CHANNEL_ACCESS_TOKEN=
LINE_CHANNEL_SECRET=
LIFF_ID=

# Database
DATABASE_URL=

# Email (Resend)
RESEND_API_KEY=

# Auth
SESSION_SECRET_KEY=

# App
APP_BASE_URL=           # Railway 提供的 HTTPS URL，供 LIFF redirect 使用
TIMEZONE=Asia/Taipei
```
