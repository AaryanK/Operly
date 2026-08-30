from dataclasses import dataclass
from html import escape
from pathlib import Path
from string import Template

from packages.email.providers.base import EmailEnvelope


TEMPLATE_DIR = Path(__file__).with_name("templates")


@dataclass(frozen=True, slots=True)
class MessageContent:
    subject: str
    preheader: str
    heading: str
    body_html: str
    text_body: str


def _read_template(name: str) -> Template:
    return Template((TEMPLATE_DIR / name).read_text(encoding="utf-8"))


def _envelope(to_email: str, content: MessageContent, template_name: str, **values: str) -> EmailEnvelope:
    inner = _read_template(template_name).substitute(
        {key: escape(str(value), quote=True) for key, value in values.items()}
    )
    html = _read_template("base.html").substitute(
        preheader=escape(content.preheader),
        heading=escape(content.heading),
        content=inner,
    )
    return EmailEnvelope(
        to_email=to_email,
        subject=content.subject,
        html_body=html,
        text_body=content.text_body,
    )


def email_verification(to_email: str, display_name: str, code: str, verify_url: str, minutes: int) -> EmailEnvelope:
    text = (
        f"Hi {display_name},\n\nYour OPERLY verification code is {code}.\n"
        f"Verify your email: {verify_url}\n\nThis code and link expire in {minutes} minutes. "
        "If you did not create this account, you can ignore this email."
    )
    return _envelope(
        to_email,
        MessageContent("Verify your OPERLY email", f"Your verification code is {code}", "Verify your email", "", text),
        "verify_email.html",
        display_name=display_name,
        code=code,
        action_url=verify_url,
        expiry_minutes=str(minutes),
    )


def password_reset(to_email: str, display_name: str, code: str, reset_url: str, minutes: int) -> EmailEnvelope:
    text = (
        f"Hi {display_name},\n\nYour OPERLY password reset code is {code}.\n"
        f"Reset your password: {reset_url}\n\nThis code and link expire in {minutes} minutes. "
        "If you did not request this, you do not need to do anything."
    )
    return _envelope(
        to_email,
        MessageContent("Reset your OPERLY password", "A password reset was requested for your OPERLY account", "Reset your password", "", text),
        "password_reset.html",
        display_name=display_name,
        code=code,
        action_url=reset_url,
        expiry_minutes=str(minutes),
    )


def welcome(to_email: str, display_name: str, app_url: str) -> EmailEnvelope:
    text = f"Welcome to OPERLY, {display_name}.\n\nYour workspace is ready: {app_url}"
    return _envelope(to_email, MessageContent("Welcome to OPERLY", "Your OPERLY workspace is ready", "Your workspace is ready", "", text), "welcome.html", display_name=display_name, action_url=app_url)


def password_changed(to_email: str, display_name: str, app_url: str) -> EmailEnvelope:
    text = f"Hi {display_name},\n\nYour OPERLY password was changed. If this was not you, contact support immediately.\n\nOpen OPERLY: {app_url}"
    return _envelope(to_email, MessageContent("Your OPERLY password was changed", "Security notice for your OPERLY account", "Your password was changed", "", text), "password_changed.html", display_name=display_name, action_url=app_url)


def security_alert(to_email: str, display_name: str, summary: str, app_url: str) -> EmailEnvelope:
    text = f"Hi {display_name},\n\n{summary}\n\nReview your account: {app_url}"
    return _envelope(to_email, MessageContent("OPERLY account security notice", "A security event may need your attention", "Account security notice", "", text), "security_alert.html", display_name=display_name, summary=summary, action_url=app_url)
