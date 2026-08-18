import logging
import threading

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Comptes & structures"

    def ready(self):
        # Vérifie Tesseract au boot (pas RapidOCR : trop lourd / OOM sur Render 512 Mo).
        def _warm():
            try:
                from .id_card_ocr import ocr_engine_status

                status = ocr_engine_status()
                if status.get("available"):
                    logger.info(
                        "OCR Tesseract prêt cmd=%s langs=%s",
                        status.get("tesseract_cmd"),
                        status.get("langs"),
                    )
                else:
                    logger.warning("OCR Tesseract absent: %s", status.get("detail"))
            except Exception as e:  # noqa: BLE001
                logger.warning("Préchargement OCR ignoré: %s", e)

        threading.Thread(target=_warm, daemon=True, name="ocr-warmup").start()
