"""Création notification + SSE + push."""
from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.utils import timezone

from cards.pubsub import hub_bus

from .models import DeviceToken, Notification
from .push import send_expo_push

User = get_user_model()


def notify_user(
    user,
    *,
    title: str,
    body: str = "",
    type: str = Notification.Type.SYSTEM,
    payload: dict[str, Any] | None = None,
    push: bool = True,
) -> Notification:
    """Crée une notif in-app, publie SSE sur le canal user, optionnellement push."""
    notif = Notification.objects.create(
        user=user,
        title=title[:160],
        body=body or "",
        type=type,
        payload=payload or {},
    )
    event = {
        "type": "notification",
        "notification_id": notif.id,
        "notif_type": notif.type,
        "title": notif.title,
        "body": notif.body,
        "payload": notif.payload,
        "ts": timezone.now().isoformat(),
    }
    # Canal unifié par user_id (pro hub SSE + patient SSE)
    hub_bus.publish(user.id, event)

    if push:
        tokens = list(
            DeviceToken.objects.filter(user=user, enabled=True).values_list("token", flat=True)
        )
        if tokens:
            send_expo_push(
                list(tokens),
                title=notif.title,
                body=notif.body,
                data={
                    "notification_id": notif.id,
                    "type": notif.type,
                    **(notif.payload or {}),
                },
            )
            DeviceToken.objects.filter(user=user, enabled=True).update(
                last_used_at=timezone.now()
            )
    return notif


def notify_admins(
    *,
    title: str,
    body: str = "",
    type: str = Notification.Type.SYSTEM,
    payload: dict[str, Any] | None = None,
) -> int:
    from core.permissions import Roles

    count = 0
    for u in User.objects.filter(role=Roles.ADMIN, is_active=True):
        notify_user(u, title=title, body=body, type=type, payload=payload)
        count += 1
    return count
