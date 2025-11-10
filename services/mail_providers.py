import logging
import re
from email.utils import formataddr
from typing import Iterable, Sequence

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
            "Messages": [
                {
                    "From": {
                        "Email": from_email,
                        "Name": from_name,
                    },
                    "To": [{"Email": email} for email in recipients],
                    "Subject": subject,
                    "HTMLPart": html_content,
                    "TextPart": text_content or strip_tags(html_content),
                }
            ]
        }

        result = self.client.send.create(data=payload)
        if result.status_code >= 400:
            raise MailProviderError(
                f"Mailjet API error {result.status_code}: {result.json()}"
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
