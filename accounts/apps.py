import logging
import threading

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Comptes & structures"

    def ready(self):
        # Précharge RapidOCR en arrière-plan (évite timeout 1er scan mobile ~40s).
        def _warm():
            try:
                from .id_card_ocr import _rapid_engine

                _rapid_engine()
                logger.info("OCR RapidOCR préchargé")
            except Exception as e:  # noqa: BLE001
                logger.warning("Préchargement OCR ignoré: %s", e)

        threading.Thread(target=_warm, daemon=True, name="ocr-warmup").start()
