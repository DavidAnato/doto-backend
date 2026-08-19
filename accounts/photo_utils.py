"""Validation et URLs absolues pour les photos d'identité."""
from __future__ import annotations

from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
MAX_PHOTO_BYTES = 5 * 1024 * 1024  # 5 Mo
MIN_WIDTH = 200
MIN_HEIGHT = 200


def public_api_base() -> str:
    return getattr(settings, "PUBLIC_API_BASE", "http://127.0.0.1:8000").rstrip("/")


def _is_seed_placeholder(file_field) -> bool:
    """Photos générées par seed_demo - on préfère les initiales côté client."""
    if not file_field:
        return False
    name = (getattr(file_field, "name", None) or "").replace("\\", "/")
    base = name.rsplit("/", 1)[-1].lower()
    return base.startswith("seed_")


def _cache_bust(url: str, file_field) -> str:
    """Évite le cache navigateur/Image après remplacement du même nom de fichier."""
    if not url or "?" in url:
        return url
    stamp = None
    try:
        storage = getattr(file_field, "storage", None)
        name = getattr(file_field, "name", None)
        if storage and name and hasattr(storage, "get_modified_time"):
            stamp = int(storage.get_modified_time(name).timestamp())
    except Exception:
        stamp = None
    if stamp is None:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}v={stamp}"


def absolute_media_url(file_field, request=None) -> str | None:
    """URL absolue pour apps mobiles (BlueStacks → 127.0.0.1:8000)."""
    if not file_field:
        return None
    if _is_seed_placeholder(file_field):
        return None
    try:
        relative = file_field.url
    except ValueError:
        return None
    if relative.startswith("http://") or relative.startswith("https://"):
        return _cache_bust(relative, file_field)
    if request is not None:
        return _cache_bust(request.build_absolute_uri(relative), file_field)
    if not relative.startswith("/"):
        relative = f"/{relative}"
    return _cache_bust(f"{public_api_base()}{relative}", file_field)


def user_photo_url(user, request=None) -> str | None:
    return absolute_media_url(getattr(user, "photo", None), request=request)


def clear_seed_photo(user) -> bool:
    """Retire une photo seed_* (placeholders hideux) - le client affiche les initiales."""
    photo = getattr(user, "photo", None)
    if not photo or not _is_seed_placeholder(photo):
        return False
    photo.delete(save=False)
    user.photo = None
    user.save(update_fields=["photo"])
    return True


def patient_photo_url(patient, request=None) -> str | None:
    """Préfère Patient.photo, sinon User.photo lié."""
    photo = getattr(patient, "photo", None)
    if photo and not _is_seed_placeholder(photo):
        url = absolute_media_url(photo, request=request)
        if url:
            return url
    user = getattr(patient, "user", None)
    if user is not None:
        return user_photo_url(user, request=request)
    return None


def validate_identity_photo(uploaded) -> None:
    """
    Photo d'identité : JPEG/PNG/WebP, max 5 Mo, min 200×200.
    Pas de détection de visage IA - contraintes techniques seulement.
    """
    if uploaded is None:
        raise ValidationError("Fichier photo requis.")

    content_type = (getattr(uploaded, "content_type", None) or "").lower()
    name = (getattr(uploaded, "name", "") or "").lower()
    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        if not (name.endswith(".jpg") or name.endswith(".jpeg") or name.endswith(".png") or name.endswith(".webp")):
            raise ValidationError(
                "Format non accepté. Utilisez une photo JPEG, PNG ou WebP (photo d'identité)."
            )

    size = getattr(uploaded, "size", None)
    if size is not None and size > MAX_PHOTO_BYTES:
        raise ValidationError("La photo ne doit pas dépasser 5 Mo.")

    try:
        uploaded.seek(0)
        raw = uploaded.read()
        uploaded.seek(0)
        img = Image.open(BytesIO(raw))
        img.verify()
        uploaded.seek(0)
        img = Image.open(BytesIO(raw))
        width, height = img.size
    except Exception as exc:
        raise ValidationError("Fichier image invalide ou corrompu.") from exc

    if width < MIN_WIDTH or height < MIN_HEIGHT:
        raise ValidationError(
            f"Photo trop petite ({width}×{height}). Minimum {MIN_WIDTH}×{MIN_HEIGHT} px "
            "(visage centré, type photo d'identité)."
        )
