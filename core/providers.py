"""
Providers externes - SMS OTP et client ANIP.

Interface stable + implémentations Mock (dev) et stubs HTTP (prod).
Sélection via SMS_PROVIDER / ANIP_PROVIDER dans les settings.
"""
from __future__ import annotations

import logging
import random
import string
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


# ─── SMS OTP ──────────────────────────────────────────────────────────────────

class SmsProvider(ABC):
    @abstractmethod
    def send_otp(self, telephone: str, code: str) -> bool:
        """Envoie un code OTP au numéro. Retourne True si accepté."""


def _otp_key(identifier: str) -> str:
    """Clé cache safe (pas d'espaces - warning memcached)."""
    import hashlib
    digest = hashlib.sha256(identifier.encode()).hexdigest()[:24]
    return f"otp:{digest}"


class MockSmsProvider(SmsProvider):
    """Stocke le code en cache et journalise - aucun SMS réel (dev/smoke)."""

    def send_otp(self, telephone: str, code: str) -> bool:
        cache.set(_otp_key(telephone), code, timeout=getattr(settings, "OTP_TTL_SECONDS", 300))
        logger.info("[MockSMS] OTP %s → %s (code démo: %s)", code, telephone, settings.DEMO_OTP_CODE)
        return True


class TwilioSmsProvider(SmsProvider):
    """Stub Twilio - nécessite TWILIO_ACCOUNT_SID / AUTH_TOKEN / FROM_NUMBER."""

    def send_otp(self, telephone: str, code: str) -> bool:
        sid = getattr(settings, "TWILIO_ACCOUNT_SID", "")
        token = getattr(settings, "TWILIO_AUTH_TOKEN", "")
        from_num = getattr(settings, "TWILIO_FROM_NUMBER", "")
        if not (sid and token and from_num):
            logger.error("Twilio non configuré (TWILIO_* manquants).")
            return False
        try:
            from twilio.rest import Client  # type: ignore

            client = Client(sid, token)
            body = f"DOTO+ - votre code OTP : {code}. Valable 5 min."
            client.messages.create(body=body, from_=from_num, to=telephone)
            cache.set(_otp_key(telephone), code, timeout=getattr(settings, "OTP_TTL_SECONDS", 300))
            return True
        except Exception as exc:
            logger.exception("Échec envoi SMS Twilio: %s", exc)
            return False


def get_sms_provider() -> SmsProvider:
    name = (getattr(settings, "SMS_PROVIDER", "mock") or "mock").lower()
    if name == "twilio":
        return TwilioSmsProvider()
    return MockSmsProvider()


def generate_otp(digits: int = 5) -> str:
    return "".join(random.choices(string.digits, k=digits))


def issue_otp(telephone: str) -> str:
    """Génère, envoie et retourne le code (le code n'est renvoyé que pour mock/débogage)."""
    code = generate_otp()
    if (getattr(settings, "SMS_PROVIDER", "mock") or "mock").lower() == "mock":
        code = settings.DEMO_OTP_CODE
    get_sms_provider().send_otp(telephone, code)
    cache.set(_otp_key(telephone), code, timeout=getattr(settings, "OTP_TTL_SECONDS", 300))
    return code


def verify_otp(telephone: str, code: str, username_fallback: str = "") -> bool:
    """Valide un OTP. Accepte toujours DEMO_OTP_CODE si SMS_PROVIDER=mock."""
    if not code:
        return False
    provider = (getattr(settings, "SMS_PROVIDER", "mock") or "mock").lower()
    if provider == "mock" and code == settings.DEMO_OTP_CODE:
        return True
    for key in filter(None, [telephone, username_fallback]):
        cache_key = _otp_key(key)
        stored = cache.get(cache_key)
        if stored and stored == code:
            cache.delete(cache_key)
            return True
    return False


# ─── ANIP ─────────────────────────────────────────────────────────────────────

@dataclass
class AnipIdentity:
    npi: str
    nom: str = ""
    prenom: str = ""
    date_naissance: str = ""
    verifie: bool = False
    source: str = "mock"


class AnipClient(ABC):
    @abstractmethod
    def verify_npi(self, npi: str, nom: str = "", prenom: str = "") -> AnipIdentity:
        ...


class MockAnipClient(AnipClient):
    """Accepte tout NPI au format BJ-XXXX-XXXXXXXX (ou tout non vide)."""

    def verify_npi(self, npi: str, nom: str = "", prenom: str = "") -> AnipIdentity:
        clean = (npi or "").strip()
        ok = bool(clean) and len(clean) >= 8
        return AnipIdentity(
            npi=clean,
            nom=nom,
            prenom=prenom,
            verifie=ok,
            source="ANIP (simulation MockAnipClient)",
        )


class HttpAnipClient(AnipClient):
    """Stub HTTP - ANIP_BASE_URL + ANIP_API_KEY. Non bloquant si absents."""

    def verify_npi(self, npi: str, nom: str = "", prenom: str = "") -> AnipIdentity:
        import urllib.error
        import urllib.request
        import json

        base = getattr(settings, "ANIP_BASE_URL", "").rstrip("/")
        key = getattr(settings, "ANIP_API_KEY", "")
        if not base:
            logger.warning("ANIP_BASE_URL absent - repli MockAnipClient.")
            return MockAnipClient().verify_npi(npi, nom, prenom)

        url = f"{base}/verify?npi={urllib.request.quote(npi)}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            return AnipIdentity(
                npi=data.get("npi", npi),
                nom=data.get("nom", nom),
                prenom=data.get("prenom", prenom),
                date_naissance=data.get("date_naissance", ""),
                verifie=bool(data.get("verifie", data.get("valid", False))),
                source="ANIP (HTTP)",
            )
        except Exception as exc:
            logger.exception("Échec appel ANIP: %s", exc)
            return AnipIdentity(npi=npi, nom=nom, prenom=prenom, verifie=False, source=f"ANIP erreur: {exc}")


def get_anip_client() -> AnipClient:
    name = (getattr(settings, "ANIP_PROVIDER", "mock") or "mock").lower()
    if name == "http":
        return HttpAnipClient()
    return MockAnipClient()
