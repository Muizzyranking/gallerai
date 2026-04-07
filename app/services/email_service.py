import logging
import re
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal, TypeAlias

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailTemplate(StrEnum):
    """
    All supported email template types.
    """

    WELCOME = "welcome"
    PASSWORD_RESET = "password_reset"
    EMAIL_VERIFICATION = "email_verification"


@dataclass(frozen=True, slots=True)
class RenderedEmail:
    """Immutable result of rendering a Jinja2 email template pair."""

    subject: str
    html: str
    text: str


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """A fully-resolved email ready to be handed to the SMTP layer."""

    to: str
    subject: str
    html_content: str
    text_content: str
    from_email: str
    reply_to: str | None = None
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)


SendDirection: TypeAlias = Literal["outbound", "transactional", "internal"]  # noqa: UP040

_STRIP_TAGS_RE: Final[re.Pattern[str]] = re.compile(r"<[^>]+>")
_COLLAPSE_NEWLINES_RE: Final[re.Pattern[str]] = re.compile(r"\n\s*\n")


class EmailService:
    """
    Async email service with Jinja2 templates and multipart MIME support.
    """

    def __init__(self, template_dir: str | Path = "app/templates/emails") -> None:
        self._smtp_host: str = settings.smtp_host
        self._smtp_port: int = settings.smtp_port
        self._smtp_user: str = settings.smtp_user
        self._smtp_password: str = settings.smtp_password
        self._use_tls: bool = settings.smtp_use_tls
        self._from_email: str = settings.from_email
        self._template_dir: Path = Path(template_dir)

        self._jinja_env: Environment = Environment(
            loader=FileSystemLoader(self._template_dir.parent.parent),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def _render_template(
        self,
        template_type: EmailTemplate | str,
        context: dict[str, Any],
    ) -> RenderedEmail:
        """
        Render HTML + optional plain-text templates.
        Falls back to stripping HTML tags when ``body.txt`` is absent.
        """
        html_template = self._jinja_env.get_template(
            f"emails/{template_type}/body.html"
        )
        html_content = html_template.render(**context)
        subject: str = context.get("subject", "GallerAI Notification")

        try:
            text_template = self._jinja_env.get_template(
                f"emails/{template_type}/body.txt"
            )
            text_content = text_template.render(**context)
        except TemplateNotFound:
            stripped = _STRIP_TAGS_RE.sub("", html_content)
            text_content = _COLLAPSE_NEWLINES_RE.sub("\n\n", stripped).strip()

        return RenderedEmail(subject=subject, html=html_content, text=text_content)

    @staticmethod
    def _build_mime(message: EmailMessage) -> MIMEMultipart:
        """Assemble a ``multipart/alternative`` MIME object from an :class:`EmailMessage`."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = message.subject
        msg["From"] = message.from_email
        msg["To"] = message.to

        if message.reply_to:
            msg["Reply-To"] = message.reply_to
        if message.cc:
            msg["Cc"] = ", ".join(message.cc)

        msg.attach(MIMEText(message.text_content, "plain", "utf-8"))
        msg.attach(MIMEText(message.html_content, "html", "utf-8"))
        return msg

    async def send(
        self,
        to: str,
        template_type: EmailTemplate | str,
        context: dict[str, Any],
        *,
        reply_to: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        direction: SendDirection = "transactional",
    ) -> bool:
        """Render a template and deliver the email via SMTP."""
        try:
            rendered = self._render_template(template_type, context)

            message = EmailMessage(
                to=to,
                subject=rendered.subject,
                html_content=rendered.html,
                text_content=rendered.text,
                from_email=self._from_email,
                reply_to=reply_to,
                cc=cc or [],
                bcc=bcc or [],
            )

            mime = self._build_mime(message)

            all_recipients: list[str] = [to, *(cc or []), *(bcc or [])]

            await aiosmtplib.send(
                mime.as_string(),
                sender=self._from_email,
                recipients=all_recipients,
                hostname=self._smtp_host,
                port=self._smtp_port,
                username=self._smtp_user,
                password=self._smtp_password,
                start_tls=self._use_tls,
            )

            logger.info(
                "Email sent",
                extra={
                    "to": to,
                    "template": str(template_type),
                    "direction": direction,
                    "subject": rendered.subject,
                },
            )
            return True

        except Exception as exc:
            logger.error(
                "Failed to send email",
                extra={"to": to, "template": str(template_type), "error": str(exc)},
                exc_info=True,
            )
            return False

    @classmethod
    async def send_welcome(
        cls, to: str, display_name: str | None, login_url: str
    ) -> bool:
        """Send a welcome email to a newly registered user."""
        return await cls.send(
            to=to,
            template_type=EmailTemplate.WELCOME,
            context={"display_name": display_name, "login_url": login_url},
        )

    @classmethod
    async def send_password_reset(
        cls, to: str, reset_url: str, expires_in_minutes: int = 30
    ) -> bool:
        """Send a password-reset link."""
        return await cls.send(
            to=to,
            template_type=EmailTemplate.PASSWORD_RESET,
            context={"reset_url": reset_url, "expires_in_minutes": expires_in_minutes},
        )

    @classmethod
    async def send_email_verification(cls, to: str, verify_url: str) -> bool:
        """Send an email-address verification link."""
        return await cls.send(
            to=to,
            template_type=EmailTemplate.EMAIL_VERIFICATION,
            context={"verify_url": verify_url},
        )
