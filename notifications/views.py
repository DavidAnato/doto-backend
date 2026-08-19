import json
import queue
import time

from django.http import StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken

from cards.pubsub import hub_bus
from core.permissions import Roles

from .models import Notification
from .serializers import NotificationSerializer


def _authenticate_access_token(raw: str):
    if not raw:
        return None
    try:
        token = AccessToken(raw)
        return JWTAuthentication().get_user(token)
    except (InvalidToken, TokenError, Exception):
        return None


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """Liste / détail / marquage lu - pour patient, pro et admin."""

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)

    @action(detail=False, methods=["get"])
    def unread_count(self, request):
        n = self.get_queryset().filter(read_at__isnull=True).count()
        return Response({"unread": n})

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        notif = self.get_object()
        notif.mark_read()
        return Response(NotificationSerializer(notif).data)

    @action(detail=False, methods=["post"])
    def read_all(self, request):
        updated = (
            self.get_queryset()
            .filter(read_at__isnull=True)
            .update(read_at=timezone.now())
        )
        return Response({"marked": updated})


class UserEventsView(APIView):
    """SSE générique patient / pro / admin (même bus que le hub).

    EventSource : `?access=<jwt>`.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get(self, request):
        raw = request.GET.get("access") or ""
        if not raw:
            auth = request.META.get("HTTP_AUTHORIZATION", "")
            if auth.lower().startswith("bearer "):
                raw = auth.split(" ", 1)[1].strip()
        user = _authenticate_access_token(raw)
        if user is None or not user.is_authenticated:
            return Response(
                {"detail": "Authentification requise."}, status=status.HTTP_401_UNAUTHORIZED
            )

        role = getattr(user, "role", None)
        if role not in (*Roles.PROFESSIONALS, Roles.PATIENT, Roles.ADMIN):
            return Response({"detail": "Rôle non autorisé."}, status=status.HTTP_403_FORBIDDEN)

        user_id = user.id

        def stream():
            q = hub_bus.subscribe(user_id)
            try:
                hello = {
                    "type": "connected",
                    "user_id": user_id,
                    "role": role,
                    "ts": timezone.now().isoformat(),
                }
                yield f"data: {json.dumps(hello)}\n\n"
                while True:
                    try:
                        event = q.get(timeout=15)
                        yield f"data: {json.dumps(event, default=str)}\n\n"
                    except queue.Empty:
                        yield f": keepalive {int(time.time())}\n\n"
                        yield (
                            "data: "
                            + json.dumps({"type": "ping", "ts": timezone.now().isoformat()})
                            + "\n\n"
                        )
            finally:
                hub_bus.unsubscribe(user_id, q)

        response = StreamingHttpResponse(stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        return response
