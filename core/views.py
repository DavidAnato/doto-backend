from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """Sonde de disponibilité (CDC §7 — 99,5% uptime)."""
    return Response(
        {
            "status": "ok",
            "service": "doto-backend",
            "product": settings.PRODUCT_NAME,
            "brand": settings.BRAND,
            "card": settings.CARD_PRODUCT,
        }
    )
