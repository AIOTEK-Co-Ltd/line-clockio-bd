import httpx

from app.config import get_settings


def _mailgun_post(settings) -> tuple[str, tuple[str, str]]:
    """Return (endpoint_url, auth_tuple) derived from MAILGUN_FROM_EMAIL."""
    domain = settings.mailgun_from_email.split("@", 1)[1]
    return f"https://api.mailgun.net/v3/{domain}/messages", ("api", settings.mailgun_api_key)


async def send_otp_email(to_email: str, otp_code: str) -> bool:
    """Send a 6-digit OTP to *to_email* via Mailgun. Returns True on HTTP 200."""
    settings = get_settings()
    url, auth = _mailgun_post(settings)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            url,
            auth=auth,
            data={
                "from": settings.mailgun_from_email,
                "to": to_email,
                "subject": "【LINE打卡系統】帳號綁定驗證碼",
                "text": (
                    f"您好，\n\n"
                    f"您的帳號綁定驗證碼為：\n\n"
                    f"    {otp_code}\n\n"
                    f"此驗證碼將在 10 分鐘後失效。\n"
                    f"請直接將此 6 位數字回覆給 LINE 機器人完成綁定。\n\n"
                    f"若您未申請綁定，請忽略此信件。"
                ),
            },
        )
    return resp.status_code == 200


async def send_invitation_email(to_email: str, name: str) -> bool:
    """Send a binding invitation to a newly HR-imported employee."""
    settings = get_settings()
    url, auth = _mailgun_post(settings)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            url,
            auth=auth,
            data={
                "from": settings.mailgun_from_email,
                "to": to_email,
                "subject": "【LINE打卡系統】帳號綁定邀請",
                "text": (
                    f"您好 {name}，\n\n"
                    f"請依照以下步驟完成 LINE 打卡系統帳號綁定：\n\n"
                    f"1. 開啟 LINE，搜尋並加入公司的 LINE 官方帳號為好友。\n"
                    f"2. 傳送您的公司 Email（{to_email}）給機器人。\n"
                    f"3. 收到 6 位數驗證碼後，回傳給機器人。\n\n"
                    f"完成後即可透過 LINE 開始打卡。\n\n"
                    f"如有任何問題，請聯繫管理員。"
                ),
            },
        )
    return resp.status_code == 200
