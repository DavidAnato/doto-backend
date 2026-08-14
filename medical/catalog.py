"""Catalogue d'examens prescrits (bons)."""
from __future__ import annotations

import json
from pathlib import Path

JSON_PATH = Path(__file__).resolve().parent / "data" / "exam_catalog.json"


def load_exam_catalog() -> list[dict]:
    if not JSON_PATH.exists():
        return []
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))
