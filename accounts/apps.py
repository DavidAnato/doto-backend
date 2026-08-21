import logging
import threading

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Comptes & structures"

    def ready(self):
        # Sonde le moteur OCR configuré (Tesseract en prod ; RapidOCR en local).
        def _warm():
            try:
                from .id_card_ocr import ocr_engine_status

                status = ocr_engine_status()
                engine = status.get("engine") or "?"
                if status.get("available"):
                    logger.info(
                        "OCR prêt engine=%s detail=%s cmd=%s langs=%s",
                        engine,
                        status.get("detail"),
                        status.get("tesseract_cmd"),
                        status.get("langs"),
                    )
                else:
                    logger.warning(
                        "OCR absent engine=%s: %s",
                        engine,
                        status.get("detail"),
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning("Préchargement OCR ignoré: %s", e)

        threading.Thread(target=_warm, daemon=True, name="ocr-warmup").start()
