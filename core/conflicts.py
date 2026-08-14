"""Conflits hors-ligne : last-write-wins horodaté.

Si le client envoie `client_updated_at` (ISO) plus ancien que `instance.updated_at`,
on refuse avec HTTP 409 et l'objet courant. Sinon la requête gagne.
"""
from __future__ import annotations

from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response


class StaleWrite(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Conflit : une version plus récente existe déjà (last-write)."
    default_code = "stale_write"


def check_last_write(instance, request, serializer_class=None):
    raw = None
    if hasattr(request, "data"):
        raw = request.data.get("client_updated_at")
    if not raw:
        raw = request.headers.get("If-Unmodified-Since") or request.META.get(
            "HTTP_IF_UNMODIFIED_SINCE"
        )
    if not raw or not getattr(instance, "updated_at", None):
        return None
    client_dt = parse_datetime(str(raw).replace("Z", "+00:00"))
    if client_dt is None:
        return None
    server_dt = instance.updated_at
    if client_dt.tzinfo is None and server_dt.tzinfo is not None:
        from django.utils import timezone as tz

        client_dt = tz.make_aware(client_dt, tz.get_current_timezone())
    if client_dt < server_dt:
        payload = {"detail": StaleWrite.default_detail, "conflict": "last_write_wins"}
        if serializer_class:
            payload["current"] = serializer_class(
                instance, context={"request": request}
            ).data
        return Response(payload, status=status.HTTP_409_CONFLICT)
    return None
