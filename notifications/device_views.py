from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import DeviceToken, Notification
from .serializers import DeviceTokenSerializer, NotificationSerializer


class DeviceTokenViewSet(viewsets.ModelViewSet):
    """Enregistrement / liste / désactivation des jetons push."""

    serializer_class = DeviceTokenSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        return DeviceToken.objects.filter(user=self.request.user)

    def create(self, request, *args, **kwargs):
        token = (request.data.get("token") or "").strip()
        if not token:
            return Response({"detail": "token requis."}, status=status.HTTP_400_BAD_REQUEST)
        platform = request.data.get("platform") or DeviceToken.Platform.UNKNOWN
        app = request.data.get("app") or ""
        obj, _ = DeviceToken.objects.update_or_create(
            user=request.user,
            token=token,
            defaults={
                "platform": platform,
                "app": str(app)[:40],
                "enabled": True,
            },
        )
        return Response(DeviceTokenSerializer(obj).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def disable(self, request):
        token = (request.data.get("token") or "").strip()
        qs = self.get_queryset()
        if token:
            qs = qs.filter(token=token)
        updated = qs.update(enabled=False)
        return Response({"disabled": updated})

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.enabled = False
        instance.save(update_fields=["enabled"])
        return Response({"disabled": 1})
