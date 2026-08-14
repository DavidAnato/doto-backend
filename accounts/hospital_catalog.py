"""Catalogue des hôpitaux du Bénin + seed StructureSante."""
from __future__ import annotations

import json
from pathlib import Path

JSON_PATH = Path(__file__).resolve().parent / "data" / "benin_hospitals.json"

TYPE_MAP = {
    "CHU": "hopital",
    "hôpital_de_zone": "hopital",
    "hôpital": "hopital",
    "clinique": "clinique",
    "polyclinique": "polyclinique",
    "laboratoire": "laboratoire",
    "centre": "centre",
    "pharmacie": "pharmacie",
}


def load_hospitals() -> list[dict]:
    if not JSON_PATH.exists():
        return []
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))


def catalog_code(hid: int) -> str:
    return f"BJ-HOSP-{int(hid):03d}"


def seed_structures(stdout=None):
    from .models import StructureSante

    created = 0
    updated = 0
    for h in load_hospitals():
        code = catalog_code(h["id"])
        stype = TYPE_MAP.get(h.get("type") or "", "hopital")
        loc_parts = [h.get("commune") or "", h.get("department") or ""]
        localisation = ", ".join(p for p in loc_parts if p)
        defaults = dict(
            nom=h.get("name") or h.get("full_name") or code,
            type=stype,
            localisation=localisation,
            telephone=h.get("phone") or "",
            full_name=h.get("full_name") or "",
            ownership=h.get("ownership") or "",
            department=h.get("department") or "",
            commune=h.get("commune") or "",
            address=h.get("address") or "",
            latitude=h.get("latitude"),
            longitude=h.get("longitude"),
            catalog_id=h.get("id"),
            statut_partenaire=True,
        )
        obj, is_new = StructureSante.objects.update_or_create(
            code_structure=code, defaults=defaults
        )
        if is_new:
            created += 1
        else:
            updated += 1
        if stdout:
            stdout.write(f"  {'+' if is_new else '~'} {obj.nom}")
    return created, updated
