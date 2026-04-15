import httpx

from app.config import get_settings


async def send_otp_email(to_email: str, otp_code: str) -> bool:
    """Send a 6-digit OTP to *to_email* via Mailgun. Returns True on HTTP 200."""
    settings = get_settings()
    # Sending domain is derived from the from-address (e.g. noreply@company.com → company.com)
    domain = settings.mailgun_from_email.split("@", 1)[1]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.mailgun.net/v3/{domain}/messages",
            auth=("api", settings.mailgun_api_key),
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
