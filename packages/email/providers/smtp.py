import asyncio
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from packages.email.providers.base import EmailEnvelope


class SMTPConfigurationError(RuntimeError):
    pass


class SMTPEmailProvider:
    def __init__(self) -> None:
        self.host = os.getenv("SMTP_HOST", "").strip()
        self.port_text = os.getenv("SMTP_PORT", "").strip()
        self.username = os.getenv("SMTP_USERNAME", "").strip()
        self.password = os.getenv("SMTP_PASSWORD", "")
        self.security = os.getenv("SMTP_SECURITY", "").strip().lower()
        self.from_email = os.getenv("OPERLY_FROM_EMAIL", "operly@dragonzpyder.xyz").strip()
        self.from_name = os.getenv("OPERLY_FROM_NAME", "OPERLY").strip() or "OPERLY"

    def _validated_port(self) -> int:
        missing = [
            name
            for name, value in (
                ("SMTP_HOST", self.host),
                ("SMTP_PORT", self.port_text),
                ("SMTP_USERNAME", self.username),
                ("SMTP_PASSWORD", self.password),
                ("SMTP_SECURITY", self.security),
                ("OPERLY_FROM_EMAIL", self.from_email),
            )
            if not value
        ]
        if missing:
            raise SMTPConfigurationError(
                "Transactional email is not configured: " + ", ".join(missing)
            )
        if self.security not in {"starttls", "ssl"}:
            raise SMTPConfigurationError("SMTP_SECURITY must be 'starttls' or 'ssl'")
        try:
            port = int(self.port_text)
        except ValueError as error:
            raise SMTPConfigurationError("SMTP_PORT must be a valid port") from error
        if not 1 <= port <= 65535:
            raise SMTPConfigurationError("SMTP_PORT must be a valid port")
        return port

    async def send(self, envelope: EmailEnvelope) -> None:
        port = self._validated_port()
        await asyncio.to_thread(self._send_sync, envelope, port)

    def _send_sync(self, envelope: EmailEnvelope, port: int) -> None:
        message = EmailMessage()
        message["To"] = envelope.to_email
        message["From"] = formataddr((self.from_name, self.from_email))
        message["Subject"] = envelope.subject
        message["Auto-Submitted"] = "auto-generated"
        message.set_content(envelope.text_body)
        message.add_alternative(envelope.html_body, subtype="html")

        context = ssl.create_default_context()
        if self.security == "ssl":
            client = smtplib.SMTP_SSL(self.host, port, timeout=20, context=context)
        else:
            client = smtplib.SMTP(self.host, port, timeout=20)
        try:
            if self.security == "starttls":
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
            client.login(self.username, self.password)
            client.send_message(message)
        finally:
            try:
                client.quit()
            except smtplib.SMTPException:
                client.close()
