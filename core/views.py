from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """Sonde de disponibilité (CDC §7 - 99,5% uptime)."""
    payload = {
        "status": "ok",
        "service": "doto-backend",
        "product": settings.PRODUCT_NAME,
        "brand": settings.BRAND,
        "card": settings.CARD_PRODUCT,
    }
    try:
        from accounts.id_card_ocr import ocr_engine_status

        payload["ocr"] = ocr_engine_status()
    except Exception as exc:  # noqa: BLE001
        payload["ocr"] = {"available": False, "detail": str(exc)}
    return Response(payload)
