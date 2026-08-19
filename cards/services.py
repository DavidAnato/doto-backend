"""
Tokens DotoCard (QR).

La carte est une CARTE D'ACCÈS : le QR ne contient aucune donnée médicale.
Un token opaque court (aléatoire) suffit - la sécurité repose sur :
  1. absence de données patient dans le QR
  2. token imprévisible
  3. révocation / réémission côté serveur

Le chiffrement Fernet précédent alourdissait inutilement le QR (modules denses,
scan difficile). On conserve le déchiffrement des anciens tokens pour compatibilité.
"""
import base64
import hashlib
import json
import re
import secrets
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

# Token opaque ~24 caractères URL-safe → QR version basse, bien scannable
_OPAQUE_RE = re.compile(r"^[A-Za-z0-9_-]{20,48}$")


def _derive_key():
    raw = settings.CARD_TOKEN_KEY
    if raw:
        try:
            Fernet(raw.encode())
            return raw.encode()
        except Exception:
            digest = hashlib.sha256(raw.encode()).digest()
            return base64.urlsafe_b64encode(digest)
    digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet():
    return Fernet(_derive_key())


def generate_token(npi=None):
    """
    Génère un token QR léger (opaque).
    `npi` est ignoré volontairement - rien d'identifiant dans le QR.
    """
    return secrets.token_urlsafe(18)


def decode_token(token):
    """Déchiffre un ancien token Fernet ; lève InvalidToken sinon."""
    raw = _fernet().decrypt(token.encode())
    return json.loads(raw.decode())


def is_valid_token(token: str) -> bool:
    """Valide le format (opaque court ou ancien Fernet)."""
    if not token or len(token) > 512:
        return False
    # Ancien format Fernet (préfixe typique gAAAA…)
    if token.startswith("gAAAA"):
        try:
            decode_token(token)
            return True
        except (InvalidToken, ValueError, Exception):
            return False
    return bool(_OPAQUE_RE.fullmatch(token))


def is_legacy_encrypted(token: str) -> bool:
    return bool(token) and token.startswith("gAAAA")
