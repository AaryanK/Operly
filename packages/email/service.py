import logging
import os

from packages.email import messages
from packages.email.providers.base import EmailEnvelope, EmailProvider
from packages.email.providers.smtp import SMTPEmailProvider


logger = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    pass


class EmailConfigurationError(RuntimeError):
    pass


class EmailService:
    def __init__(self, provider: EmailProvider):
        self.provider = provider

    async def _send(self, envelope: EmailEnvelope) -> None:
        try:
            await self.provider.send(envelope)
        except Exception as error:
            logger.warning(
                "Transactional email delivery failed (provider_error=%s)",
                type(error).__name__,
            )
            raise EmailDeliveryError("Transactional email could not be delivered") from error

    async def send_email_verification(self, **kwargs) -> None:
        await self._send(messages.email_verification(**kwargs))

    async def send_password_reset(self, **kwargs) -> None:
        await self._send(messages.password_reset(**kwargs))

    async def send_welcome(self, **kwargs) -> None:
        await self._send(messages.welcome(**kwargs))

    async def send_password_changed(self, **kwargs) -> None:
        await self._send(messages.password_changed(**kwargs))

    async def send_security_alert(self, **kwargs) -> None:
        await self._send(messages.security_alert(**kwargs))


_service_override: EmailService | None = None


def set_email_service_for_tests(service: EmailService | None) -> None:
    global _service_override
    _service_override = service


def get_email_service() -> EmailService:
    if _service_override is not None:
        return _service_override
    provider_name = os.getenv("MAIL_PROVIDER", "").strip().lower()
    if provider_name != "smtp":
        raise EmailConfigurationError("MAIL_PROVIDER must be configured as smtp")
    return EmailService(SMTPEmailProvider())
