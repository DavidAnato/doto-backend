import logging
import threading

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Comptes & structures"

    def ready(self):
        from django.db.backends.signals import connection_created

        def _sqlite_wal(sender, connection, **kwargs):
            if connection.vendor != "sqlite":
                return
            with connection.cursor() as cursor:
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA busy_timeout=20000;")

        connection_created.connect(_sqlite_wal, dispatch_uid="doto_sqlite_wal")

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
