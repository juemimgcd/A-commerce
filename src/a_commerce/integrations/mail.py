"""可选 SMTP 适配器。SMTP 不提供资金级幂等，超时后的邮件有重复投递可能。"""

from email.message import EmailMessage

try:
    import aiosmtplib
except ImportError:
    aiosmtplib = None  # type: ignore[assignment]

from a_commerce.conf.settings import Settings


async def send(settings: Settings, message: EmailMessage) -> None:
    if not settings.mail_enabled or aiosmtplib is None:
        raise ValueError("邮件能力未启用或未安装 mail 可选依赖")
    message["From"] = settings.mail_from
    try:
        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
            start_tls=settings.smtp_start_tls,
            timeout=10,
        )
    except aiosmtplib.SMTPException as exc:
        raise OSError("SMTP delivery failed") from exc
