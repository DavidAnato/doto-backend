"""Envoi push Expo - stub en DEV, réel si EXPO_ACCESS_TOKEN est défini."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def _is_expo_token(token: str) -> bool:
    return token.startswith("ExponentPushToken[") or token.startswith("ExpoPushToken[")


def send_expo_push(
    tokens: list[str],
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Envoie des notifications push via l'API Expo.

    Sans EXPO_ACCESS_TOKEN : log uniquement (mock DEV).
    """
    tokens = [t for t in tokens if t and _is_expo_token(t)]
    if not tokens:
        logger.info("push: aucun jeton Expo valide (titre=%s)", title)
        return {"sent": 0, "mode": "skip", "detail": "no_expo_tokens"}

    access = getattr(settings, "EXPO_ACCESS_TOKEN", "") or ""
    payload = [
        {
            "to": t,
            "title": title,
            "body": body,
            "sound": "default",
            "data": data or {},
        }
        for t in tokens
    ]

    if not access:
        logger.info(
            "push MOCK (EXPO_ACCESS_TOKEN absent): %s → %s destinataire(s) · %s",
            title,
            len(tokens),
            json.dumps(data or {}, ensure_ascii=False)[:200],
        )
        return {"sent": len(tokens), "mode": "mock", "tokens": len(tokens)}

    req = urllib.request.Request(
        EXPO_PUSH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {access}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8")
            result = json.loads(raw) if raw else {}
            logger.info("push Expo OK: %s", title)
            return {"sent": len(tokens), "mode": "expo", "result": result}
    except urllib.error.URLError as exc:
        logger.warning("push Expo échec: %s", exc)
        return {"sent": 0, "mode": "error", "detail": str(exc)}
