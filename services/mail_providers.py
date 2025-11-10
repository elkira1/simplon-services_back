import logging
import re
import time
from email.utils import formataddr
from typing import Iterable, Sequence

import requests
from django.conf import settings
from django.core.mail import EmailMultiAlternatives

logger = logging.getLogger(__name__)

try:  # mailjet-rest est optionnel selon la configuration
    from mailjet_rest import Client as MailjetClient
except ImportError:  # pragma: no cover
    MailjetClient = None  # type: ignore


class MailProviderError(RuntimeError):
    """Erreur remontée lorsqu'un fournisseur d'emails échoue."""


class BaseMailProvider:
    def send(
        self,
        *,
        subject: str,
        html_content: str,
        text_content: str,
        recipients: Sequence[str],
        from_email: str,
        from_name: str,
    ) -> None:
        raise NotImplementedError


class SMTPMailProvider(BaseMailProvider):
    """Utilise la configuration Django (SMTP classique ou console backend)."""

    def send(
        self,
        *,
        subject: str,
        html_content: str,
        text_content: str,
        recipients: Sequence[str],
        from_email: str,
        from_name: str,
    ) -> None:
        email = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=formataddr((from_name, from_email)),
            to=list(recipients),
            reply_to=[from_email],
        )
        email.attach_alternative(html_content, "text/html")
        email.send(fail_silently=False)


class MailjetMailProvider(BaseMailProvider):
    """Envoi des emails via l'API Mailjet (offre gratuite disponible)."""

    def __init__(self) -> None:
        if not MailjetClient:
            raise MailProviderError(
                "mailjet-rest n'est pas installé. Ajoutez-le à requirements pour utiliser Mailjet."
            )

        api_key = getattr(settings, "MAILJET_API_KEY", None)
        secret_key = getattr(settings, "MAILJET_SECRET_KEY", None)
        if not api_key or not secret_key:
            raise MailProviderError("MAILJET_API_KEY/MAILJET_SECRET_KEY non configurés.")

        self.client = MailjetClient(auth=(api_key, secret_key), version="v3.1")
        self.max_retries = getattr(settings, "MAILJET_MAX_RETRIES", 3)
        self.retry_backoff = getattr(settings, "MAILJET_RETRY_BACKOFF", 1.5)
        self.track_opens = getattr(
            settings, "MAILJET_TRACK_OPENS", "enabled"
        )  # enabled | disabled
        self.track_clicks = getattr(
            settings, "MAILJET_TRACK_CLICKS", "enabled"
        )
        self.sandbox_mode = getattr(settings, "MAILJET_SANDBOX_MODE", False)

    def send(
        self,
        *,
        subject: str,
        html_content: str,
        text_content: str,
        recipients: Sequence[str],
        from_email: str,
        from_name: str,
    ) -> None:
        base_message = {
            "From": {
                "Email": from_email,
                "Name": from_name,
            },
            "To": [{"Email": email} for email in recipients],
            "Subject": subject,
            "HTMLPart": html_content,
            "TextPart": text_content or strip_tags(html_content),
            "TrackOpens": self.track_opens,
            "TrackClicks": self.track_clicks,
        }

        if self.sandbox_mode:
            base_message["SandboxMode"] = True

        payload = {"Messages": [base_message]}

        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                result = self.client.send.create(data=payload)
                if result.status_code >= 400:
                    raise MailProviderError(
                        f"Mailjet API error {result.status_code}: {result.json()}"
                    )
                return
            except Exception as exc:  # pragma: no cover - dépend du réseau
                last_exc = exc
                logger.warning(
                    "Mailjet send attempt %s/%s failed: %s",
                    attempt,
                    self.max_retries,
                    exc,
                )
                if attempt < self.max_retries:
                    sleep_for = self.retry_backoff * attempt
                    time.sleep(sleep_for)
                else:
                    raise MailProviderError(str(exc)) from exc


class BrevoMailProvider(BaseMailProvider):
    """Envoi des emails via l'API Brevo (ex-Sendinblue)."""

    API_URL = "https://api.brevo.com/v3/smtp/email"

    def __init__(self) -> None:
        api_key = getattr(settings, "BREVO_API_KEY", None) or getattr(
            settings, "SENDINBLUE_API_KEY", None
        )
        if not api_key:
            raise MailProviderError(
                "BREVO_API_KEY (ou SENDINBLUE_API_KEY) non configuré."
            )
        self.api_key = api_key

    def send(
        self,
        *,
        subject: str,
        html_content: str,
        text_content: str,
        recipients: Sequence[str],
        from_email: str,
        from_name: str,
    ) -> None:
        payload = {
            "sender": {"email": from_email, "name": from_name},
            "to": [{"email": email} for email in recipients],
            "subject": subject,
            "htmlContent": html_content,
            "textContent": text_content or strip_tags(html_content),
        }

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "api-key": self.api_key,
        }

        response = requests.post(self.API_URL, json=payload, headers=headers, timeout=10)
        if response.status_code >= 400:
            raise MailProviderError(
                f"Brevo API error {response.status_code}: {response.text}"
            )


class ResendMailProvider(BaseMailProvider):
    """Envoi des emails via Resend (API HTTPS)."""

    API_URL = "https://api.resend.com/emails"

    def __init__(self) -> None:
        api_key = getattr(settings, "RESEND_API_KEY", None)
        if not api_key:
            raise MailProviderError("RESEND_API_KEY non configuré.")
        self.api_key = api_key

    def send(
        self,
        *,
        subject: str,
        html_content: str,
        text_content: str,
        recipients: Sequence[str],
        from_email: str,
        from_name: str,
    ) -> None:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "from": f"{from_name} <{from_email}>",
            "to": list(recipients),
            "subject": subject,
            "html": html_content,
            "text": text_content or strip_tags(html_content),
        }
        response = requests.post(self.API_URL, json=payload, headers=headers, timeout=10)
        if response.status_code >= 400:
            raise MailProviderError(
                f"Resend API error {response.status_code}: {response.text}"
            )


class ConsoleMailProvider(BaseMailProvider):
    """Fallback: loggue les emails dans la console (utile en dev)."""

    def send(
        self,
        *,
        subject: str,
        html_content: str,
        text_content: str,
        recipients: Sequence[str],
        from_email: str,
        from_name: str,
    ) -> None:
        logger.info(
            "Email (console fallback) - To: %s | Subject: %s\n%s",
            ", ".join(recipients),
            subject,
            text_content,
        )


def strip_tags(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


PROVIDERS = {
    "smtp": SMTPMailProvider,
    "gmail": SMTPMailProvider,  # alias pratique pour EMAIL_PROVIDER=gmail
    "mailjet": MailjetMailProvider,
    "brevo": BrevoMailProvider,
    "resend": ResendMailProvider,
    "console": ConsoleMailProvider,
}


def get_mail_provider() -> BaseMailProvider:
    provider_name = getattr(settings, "EMAIL_PROVIDER", "smtp").lower()
    provider_cls = PROVIDERS.get(provider_name)

    if not provider_cls:
        raise MailProviderError(f"Fournisseur email inconnu: {provider_name}")

    try:
        return provider_cls()
    except MailProviderError as exc:
        if provider_name != "console":
            logger.warning(
                "Impossible d'initialiser le fournisseur '%s' (%s). "
                "Bascule sur la sortie console.",
                provider_name,
                exc,
            )
            return ConsoleMailProvider()
        raise
