"""Validation et URLs absolues pour les photos d'identité."""
from __future__ import annotations

from io import BytesIO
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

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


def _public_media_url(path: str, request=None) -> str:
    """URL publique. Un chemin relatif serait collé à /api/… par build_absolute_uri."""
    if not path.startswith("/"):
        path = "/" + path.lstrip("/")
    built = None
    if request is not None:
        try:
            built = request.build_absolute_uri(path)
        except Exception:
            built = None
    host = (urlsplit(built).hostname or "").lower() if built else ""
    public_host = (urlsplit(public_api_base()).hostname or "").lower()
    internal = host in {"", "localhost", "127.0.0.1", "0.0.0.0", "await-origin.fotoce.app"}
    if built and not internal:
        return built
    if public_host:
        return f"{public_api_base()}{path}"
    return built or f"{public_api_base()}{path}"


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
    return _cache_bust(_public_media_url(relative, request=request), file_field)


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


def prepare_identity_photo(uploaded) -> tuple[bytes, str]:
    """
    Photo d'identité : JPEG/PNG/WebP, max 5 Mo, min 200×200.
    Retourne les octets et l'extension réelle (pas celle du nom de fichier).
    Image.verify() rejette des JPEG de téléphone pourtant lisibles : on décode avec load().
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
    except Exception as exc:
        raise ValidationError("Fichier image invalide ou corrompu.") from exc
    if not raw:
        raise ValidationError("Fichier image vide.")

    try:
        img = Image.open(BytesIO(raw))
        img.load()
        width, height = img.size
        fmt = (img.format or "").upper()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationError("Fichier image invalide ou corrompu.") from exc

    ext = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}.get(fmt)
    if ext is None:
        raise ValidationError(
            "Format non accepté. Utilisez une photo JPEG, PNG ou WebP (photo d'identité)."
        )

    if width < MIN_WIDTH or height < MIN_HEIGHT:
        raise ValidationError(
            f"Photo trop petite ({width}×{height}). Minimum {MIN_WIDTH}×{MIN_HEIGHT} px "
            "(visage centré, type photo d'identité)."
        )
    return raw, ext


def validate_identity_photo(uploaded) -> None:
    """Compat : valide sans renvoyer les octets."""
    prepare_identity_photo(uploaded)
