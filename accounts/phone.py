"""Normalisation / affichage des numéros Bénin (+229)."""
from __future__ import annotations

BJ_CC = "229"
BJ_MOBILE_PREFIX = "01"


def digits_only(raw: str) -> str:
    return "".join(ch for ch in (raw or "") if ch.isdigit())


def national_digits(raw: str) -> str:
    d = digits_only(raw)
    if d.startswith(BJ_CC):
        d = d[len(BJ_CC) :]
    return d[:10]


def ensure_bj_01(raw: str) -> str:
    """Préfixe 01 si absent. Ex. 97586174 → 0197586174. Vide → vide."""
    d = national_digits(raw)
    if not d:
        return ""
    if d.startswith(BJ_MOBILE_PREFIX):
        return d[:10]
    d = d.lstrip("0")
    if not d.startswith(BJ_MOBILE_PREFIX):
        d = BJ_MOBILE_PREFIX + d
    return d[:10]


def format_national(raw: str) -> str:
    d = national_digits(raw)
    if not d:
        return ""
    parts = [d[i : i + 2] for i in range(0, len(d), 2)]
    return " ".join(parts)


def normalize_phone(raw: str) -> str:
    """
    Forme canonique API / DB : +229XXXXXXXX (sans espaces).
    Accepte saisie locale, +229, 00229, etc. Ajoute 01 si absent.
    """
    raw = (raw or "").strip()
    if not raw:
        return raw
    nat = ensure_bj_01(raw)
    if not nat or nat == BJ_MOBILE_PREFIX:
        # Fallback : conserver un +digits générique si rien d'exploitable
        keep_plus = raw.startswith("+")
        digits = digits_only(raw)
        if not digits:
            return raw
        if digits.startswith(BJ_CC):
            return f"+{BJ_CC}{ensure_bj_01(digits[len(BJ_CC):])}"
        return f"+{digits}" if keep_plus else digits
    return f"+{BJ_CC}{nat}"


def display_phone(raw: str, fallback: str = "-") -> str:
    """Affichage carte / PDF : +229 01 XX XX XX XX."""
    nat = national_digits(raw)
    if not nat:
        return fallback
    return f"+{BJ_CC} {format_national(ensure_bj_01(nat))}"
