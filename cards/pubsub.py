"""Pub/sub mémoire pour le flux SSE DotoHub (scan DotoCard → ouverture dossier).

Canal clé = user_id du professionnel. Suffisant pour une démo locale ;
en production multi-workers, remplacer par Redis pub/sub.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any

from django.core.cache import cache

# Un client (web + mobile + reconnexion) ne doit pas empiler des threads gunicorn.
_MAX_LISTENERS = 3
_DROP = "_drop"


class HubEventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._channels: dict[int, list[queue.Queue]] = {}

    def subscribe(self, user_id: int) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=64)
        dropped: list[queue.Queue] = []
        with self._lock:
            listeners = self._channels.setdefault(user_id, [])
            while len(listeners) >= _MAX_LISTENERS:
                dropped.append(listeners.pop(0))
            listeners.append(q)
        for old in dropped:
            try:
                old.put_nowait({"type": _DROP})
            except queue.Full:
                pass
        return q

    def unsubscribe(self, user_id: int, q: queue.Queue) -> None:
        with self._lock:
            listeners = self._channels.get(user_id) or []
            if q in listeners:
                listeners.remove(q)
            if not listeners and user_id in self._channels:
                del self._channels[user_id]

    def publish(self, user_id: int, event: dict[str, Any]) -> int:
        """Publie un événement. Retourne le nombre d'abonnés notifiés."""
        with self._lock:
            listeners = list(self._channels.get(user_id) or [])
        notified = 0
        for q in listeners:
            try:
                q.put_nowait(event)
                notified += 1
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(event)
                    notified += 1
                except queue.Full:
                    pass
        return notified


hub_bus = HubEventBus()


def _sse_pack(event: dict) -> str:
    return "data: " + json.dumps(event, default=str) + "\n\n"


def sse_stream(user_id: int, hello: dict, max_seconds: int = 90):
    """Générateur SSE. Ferme la connexion SQLite et se termine tout seul.

    Un flux infini occupait un des 4 threads gunicorn jusqu'au redémarrage.
    Le client EventSource se reconnecte à la fin des max_seconds.
    """
    from django.db import close_old_connections
    from django.utils import timezone

    close_old_connections()
    q = hub_bus.subscribe(user_id)
    deadline = time.monotonic() + max_seconds
    try:
        yield _sse_pack(hello)
        while time.monotonic() < deadline:
            try:
                event = q.get(timeout=15)
            except queue.Empty:
                yield f": keepalive {int(time.time())}\n\n"
                yield _sse_pack({"type": "ping", "ts": timezone.now().isoformat()})
                continue
            if event.get("type") == _DROP:
                break
            yield _sse_pack(event)
    finally:
        hub_bus.unsubscribe(user_id, q)
        close_old_connections()
_live_lock = threading.Lock()


def remember_hub_event(user_id: int, event: dict) -> None:
    """File courte par professionnel. Le site la relit si le flux SSE a coupé."""
    key = f"doto-hub-live:{user_id}"
    with _live_lock:
        current = list(cache.get(key) or [])
        current.append(event)
        cache.set(key, current[-20:], timeout=120)


def take_hub_events(user_id: int) -> list:
    key = f"doto-hub-live:{user_id}"
    with _live_lock:
        current = list(cache.get(key) or [])
        if current:
            cache.set(key, [], timeout=120)
        return current
