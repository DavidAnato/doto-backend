"""Pub/sub mémoire pour le flux SSE DotoHub (scan DodoCard → ouverture dossier).

Canal clé = user_id du professionnel. Suffisant pour une démo locale ;
en production multi-workers, remplacer par Redis pub/sub.
"""
from __future__ import annotations

import queue
import threading
from typing import Any


class HubEventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._channels: dict[int, list[queue.Queue]] = {}

    def subscribe(self, user_id: int) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=32)
        with self._lock:
            self._channels.setdefault(user_id, []).append(q)
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
