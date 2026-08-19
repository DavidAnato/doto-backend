"""
OCR CIP / CEDEAO - Tesseract (Render) + fallback RapidOCR/Paddle optionnels.

CIP  : valeur à droite du libellé
CEDEAO : valeur sous le libellé (sauf NPI / dates parfois à droite)

Sur Render (512 Mo) : Tesseract via apt (`tesseract-ocr`, `tesseract-ocr-fra`,
`tesseract-ocr-eng`). RapidOCR/Paddle trop lourds - ne pas les forcer en prod.
"""
from __future__ import annotations

import io
import logging
import os
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps

logger = logging.getLogger(__name__)

_MONTHS_FR = {
    "janvier": 1,
    "fevrier": 2,
    "février": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "août": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
    "décembre": 12,
}

# Mots EN à ignorer s'ils sont pris pour une « valeur »
_EN_NOISE = {
    "surname",
    "first",
    "names",
    "name",
    "nationality",
    "place",
    "birth",
    "of",
    "date",
    "expiry",
    "card",
    "number",
    "authority",
    "issuance",
    "eid",
    "identity",
    "ecowas",
    "bilhete",
    "de",
    "identidade",
    "cedeao",
}


def _norm(s: str) -> str:
    return (
        s.replace("’", "'")
        .replace("`", "'")
        .replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("à", "a")
        .replace("ù", "u")
        .replace("ô", "o")
        .strip()
    )


def _compact(s: str) -> str:
    """Texte OCR souvent sans espaces : 'Nom/Surname', 'Lieudenaissance'."""
    return re.sub(r"[^a-z0-9]", "", _norm(s).lower())


def _is_noise_value(text: str) -> bool:
    t = _norm(text).lower()
    if not t:
        return True
    # Traductions EN seules
    tokens = re.findall(r"[a-z]+", t)
    if tokens and all(tok in _EN_NOISE for tok in tokens):
        return True
    if t in _EN_NOISE:
        return True
    if re.fullmatch(r"surname|first names?|nationality|place of birth|date of birth|date of expiry|card number|eid number|authority of issuance", t):
        return True
    return False


def _parse_date(raw: str) -> str | None:
    raw = raw.strip()
    m = re.search(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", raw)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d).date().isoformat()
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})", raw, flags=re.I)
    if m:
        mon = _MONTHS_FR.get(m.group(2).lower())
        if mon:
            try:
                return datetime(int(m.group(3)), mon, int(m.group(1))).date().isoformat()
            except ValueError:
                return None
    # OCR collé : « 25janvier2004 »
    m = re.search(r"(\d{1,2})\s*([A-Za-zÀ-ÿ]{3,})\s*(\d{4})", raw, flags=re.I)
    if m:
        mon = _MONTHS_FR.get(_norm(m.group(2)).lower())
        if mon:
            try:
                return datetime(int(m.group(3)), mon, int(m.group(1))).date().isoformat()
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# OCR → items {text, x, y, w, h}
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _paddle_engine():
    import os

    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    os.environ.setdefault("FLAGS_onnxruntime_engine_enable", "0")
    try:
        import paddle

        paddle.set_flags({"FLAGS_use_mkldnn": False})
    except Exception:  # noqa: BLE001
        pass

    from paddleocr import PaddleOCR

    # paddleocr 3.x : use_textline_orientation ; 2.x : use_angle_cls
    for kwargs in (
        {
            "lang": "fr",
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": True,
        },
        {"lang": "fr", "use_textline_orientation": True},
        {"lang": "fr"},
        {"use_angle_cls": True, "lang": "fr"},
    ):
        try:
            return PaddleOCR(**kwargs)
        except TypeError:
            continue
    return PaddleOCR()


def _box_from_points(box) -> dict[str, float] | None:
    try:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
    except Exception:  # noqa: BLE001
        return None
    return {
        "x": sum(xs) / 4,
        "y": sum(ys) / 4,
        "x0": min(xs),
        "x1": max(xs),
        "y0": min(ys),
        "y1": max(ys),
    }


def _items_from_paddle(image_path: str) -> list[dict[str, Any]]:
    ocr = _paddle_engine()
    result = None
    for call in (
        lambda: ocr.predict(image_path),
        lambda: ocr.ocr(image_path, cls=True),
        lambda: ocr.ocr(image_path),
    ):
        try:
            result = call()
            break
        except TypeError:
            continue
        except AttributeError:
            continue
    if result is None:
        raise RuntimeError("PaddleOCR: aucune méthode ocr/predict utilisable")

    items: list[dict[str, Any]] = []

    # paddleocr 3.x / paddlex : liste de dicts avec rec_texts + rec_polys
    pages = result if isinstance(result, list) else [result]
    for page in pages:
        if isinstance(page, dict):
            texts = page.get("rec_texts") or page.get("texts") or []
            polys = page.get("rec_polys") or page.get("dt_polys") or page.get("rec_boxes") or []
            for text, box in zip(texts, polys):
                geom = _box_from_points(box)
                if not geom:
                    continue
                items.append({"text": str(text).strip(), **geom})
            if items:
                continue
            # parfois nested
            nested = page.get("ocr_result") or page.get("results")
            if nested:
                page = nested

        lines = page
        if isinstance(lines, list) and lines and isinstance(lines[0], list):
            # [[box, (text, score)], ...] ou wrapping [[...]]
            if lines and isinstance(lines[0][0], (list, tuple)) and len(lines[0]) == 2:
                pass
            elif len(lines) == 1 and isinstance(lines[0], list):
                lines = lines[0]

        for line in lines or []:
            try:
                if isinstance(line, dict):
                    text = line.get("text") or line.get("transcription") or ""
                    box = line.get("box") or line.get("points") or line.get("poly")
                    geom = _box_from_points(box) if box is not None else None
                else:
                    box, payload = line[0], line[1]
                    text = payload[0] if isinstance(payload, (list, tuple)) else str(payload)
                    geom = _box_from_points(box)
                if not geom or not str(text).strip():
                    continue
                items.append({"text": str(text).strip(), **geom})
            except Exception:  # noqa: BLE001
                continue

    items.sort(key=lambda i: (i["y"], i["x"]))
    return items


def _prepare_ocr_image(image_bytes: bytes) -> Image.Image:
    """RGB, orientation EXIF, taille bornée - photos mobile trop grandes = timeout."""
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img) or img
    img = img.convert("RGB")
    w, h = img.size
    longest = max(w, h)
    # Trop petit → upscale ; trop grand → downscale léger (garde + détail pour flou)
    if longest < 1400:
        scale = 1400 / longest
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    elif longest > 2000:
        scale = 2000 / longest
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    return img


_TESSDATA_CANDIDATES = (
    "/usr/share/tesseract-ocr/5/tessdata",
    "/usr/share/tesseract-ocr/4.00/tessdata",
    "/usr/share/tesseract-ocr/tessdata",
    "/usr/share/tessdata",
)


def _configure_tesseract() -> str:
    """Pointe pytesseract vers le binaire + TESSDATA_PREFIX si besoin."""
    import pytesseract

    raw_cmd = (os.environ.get("TESSERACT_CMD") or "").strip()
    cmd = raw_cmd
    if cmd and not Path(cmd).exists():
        cmd = shutil.which(cmd) or ""
    if not cmd:
        cmd = shutil.which("tesseract") or ""
    if not cmd:
        for candidate in (
            "/usr/bin/tesseract",
            "/usr/local/bin/tesseract",
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        ):
            if Path(candidate).exists():
                cmd = candidate
                break
    if not cmd:
        raise RuntimeError(
            "Tesseract introuvable. Sur Render, installer les paquets Native Environment "
            "tesseract-ocr, tesseract-ocr-fra, tesseract-ocr-eng (render.yaml aptPackages) "
            "puis redéployer. En local : installer Tesseract OCR."
        )
    pytesseract.pytesseract.tesseract_cmd = cmd

    prefix = (os.environ.get("TESSDATA_PREFIX") or "").strip()
    if prefix and not Path(prefix).is_dir():
        logger.warning("TESSDATA_PREFIX invalide (%s) - détection auto", prefix)
        prefix = ""
    if not prefix:
        for cand in _TESSDATA_CANDIDATES:
            tessdir = Path(cand)
            if tessdir.is_dir() and any(tessdir.glob("*.traineddata")):
                os.environ["TESSDATA_PREFIX"] = cand
                prefix = cand
                break
    return cmd


def _tesseract_langs() -> list[str]:
    preferred = (os.environ.get("DOTO_OCR_LANG") or "fra+eng").strip() or "fra+eng"
    # fra manquant → eng seul, plutôt qu'un crash « Error opening data file »
    ordered = [preferred, "fra+eng", "eng+fra", "fra", "eng"]
    out: list[str] = []
    for lang in ordered:
        if lang not in out:
            out.append(lang)
    return out


def _items_from_tesseract(image_bytes: bytes) -> list[dict[str, Any]]:
    import pytesseract
    from pytesseract import Output

    _configure_tesseract()
    img = _prepare_ocr_image(image_bytes)
    gray = ImageOps.autocontrast(img.convert("L"), cutoff=1)
    gray = gray.filter(ImageFilter.SHARPEN)

    last_err: Exception | None = None
    data = None
    for lang in _tesseract_langs():
        for psm in ("6", "4", "11"):
            try:
                candidate = pytesseract.image_to_data(
                    gray,
                    lang=lang,
                    config=f"--psm {psm}",
                    output_type=Output.DICT,
                    timeout=20,
                )
                nonempty = sum(1 for t in (candidate.get("text") or []) if str(t).strip())
                if nonempty >= 4:
                    data = candidate
                    logger.info("Tesseract ok lang=%s psm=%s blocs=%s", lang, psm, nonempty)
                    break
                last_err = RuntimeError(f"tesseract lang={lang} psm={psm}: peu de texte ({nonempty})")
            except pytesseract.TesseractNotFoundError as e:
                raise RuntimeError(
                    "Tesseract introuvable (binaire). Vérifier aptPackages / TESSERACT_CMD."
                ) from e
            except pytesseract.TesseractError as e:
                last_err = e
                msg = str(e).lower()
                if "failed loading language" in msg or "error opening data file" in msg:
                    logger.warning("Tesseract langue indisponible (%s): %s", lang, e)
                    break
                continue
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
        if data:
            break

    if not data:
        raise RuntimeError(f"Tesseract n'a lu aucun texte ({last_err})")

    lines: dict[tuple[int, int, int], dict[str, Any]] = {}
    n = len(data["text"])
    for i in range(n):
        text = str(data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = 0.0
        if conf < 0:
            continue
        left = int(data["left"][i])
        top = int(data["top"][i])
        width = int(data["width"][i])
        height = int(data["height"][i])
        key = (int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i]))
        box = lines.get(key)
        if box is None:
            lines[key] = {
                "text": text,
                "x0": left,
                "y0": top,
                "x1": left + width,
                "y1": top + height,
            }
        else:
            box["text"] = f"{box['text']} {text}"
            box["x0"] = min(box["x0"], left)
            box["y0"] = min(box["y0"], top)
            box["x1"] = max(box["x1"], left + width)
            box["y1"] = max(box["y1"], top + height)

    items: list[dict[str, Any]] = []
    for box in lines.values():
        x0, y0, x1, y1 = box["x0"], box["y0"], box["x1"], box["y1"]
        items.append(
            {
                "text": box["text"].strip(),
                "x": (x0 + x1) / 2,
                "y": (y0 + y1) / 2,
                "x0": float(x0),
                "x1": float(x1),
                "y0": float(y0),
                "y1": float(y1),
            }
        )
    items.sort(key=lambda i: (i["y"], i["x"]))
    return items


@lru_cache(maxsize=1)
def ocr_engine_status() -> dict[str, Any]:
    """Sonde légère pour /api/health/ - pas d'OCR d'image."""
    status: dict[str, Any] = {
        "engine": os.environ.get("DOTO_OCR_ENGINE", "tesseract"),
        "available": False,
        "tesseract_cmd": None,
        "tessdata": os.environ.get("TESSDATA_PREFIX") or None,
        "version": None,
        "langs": [],
        "detail": None,
    }
    try:
        import pytesseract

        cmd = _configure_tesseract()
        status["tesseract_cmd"] = cmd
        status["tessdata"] = os.environ.get("TESSDATA_PREFIX") or status["tessdata"]
        try:
            status["version"] = str(pytesseract.get_tesseract_version())
        except Exception as e:  # noqa: BLE001
            status["detail"] = f"version: {e}"
            return status
        try:
            langs = pytesseract.get_languages(config="")
            status["langs"] = langs
        except Exception:  # noqa: BLE001
            langs = []
        missing = [x for x in ("fra", "eng") if x not in langs]
        if missing and langs:
            status["detail"] = f"langues manquantes: {', '.join(missing)}"
        status["available"] = True
        if not status["detail"]:
            status["detail"] = "tesseract prêt"
    except Exception as e:  # noqa: BLE001
        status["detail"] = str(e)
        logger.warning("OCR status: %s", e)
    return status


@lru_cache(maxsize=1)
def _rapid_engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _items_from_rapid(image_bytes: bytes) -> list[dict[str, Any]]:
    import numpy as np

    img = _prepare_ocr_image(image_bytes)
    engine = _rapid_engine()
    result, _ = engine(np.array(img))
    items: list[dict[str, Any]] = []
    for line in result or []:
        if len(line) < 2:
            continue
        box, text = line[0], line[1]
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
        except Exception:  # noqa: BLE001
            continue
        items.append(
            {
                "text": str(text).strip(),
                "x": sum(xs) / 4,
                "y": sum(ys) / 4,
                "x0": min(xs),
                "x1": max(xs),
                "y0": min(ys),
                "y1": max(ys),
            }
        )
    items.sort(key=lambda i: (i["y"], i["x"]))
    return items


def read_items(image_bytes: bytes) -> tuple[list[dict[str, Any]], str]:
    """Retourne (items spatiaux, moteur).

    Défaut : Tesseract (léger, viable Render 512 Mo). RapidOCR / Paddle seulement
    si installés et DOTO_OCR_ENGINE=rapid|paddle|auto - trop lourds pour le free.
    """
    errors: list[str] = []
    engine_pref = os.environ.get("DOTO_OCR_ENGINE", "tesseract").lower().strip()
    try_tesseract = engine_pref in ("tesseract", "tess", "auto", "")
    try_rapid = engine_pref in ("rapid", "rapidocr", "auto")
    try_paddle = engine_pref in ("paddle", "paddleocr", "auto")

    if try_tesseract:
        try:
            items = _items_from_tesseract(image_bytes)
            if items:
                return items, "tesseract"
            errors.append("tesseract: aucun texte")
        except Exception as e:  # noqa: BLE001
            errors.append(f"tesseract: {e}")
            logger.warning("Tesseract indisponible: %s", e)

    if try_rapid:
        try:
            items = _items_from_rapid(image_bytes)
            if items:
                return items, "rapidocr-spatial"
            errors.append("rapidocr: aucun texte")
        except Exception as e:  # noqa: BLE001
            errors.append(f"rapidocr: {e}")
            logger.warning("RapidOCR indisponible: %s", e)

    if try_paddle:
        suffix = ".jpg"
        path = ""
        try:
            img = _prepare_ocr_image(image_bytes)
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                path = tmp.name
                img.save(path, format="JPEG", quality=90)
            items = _items_from_paddle(path)
            if len(items) >= 8:
                return items, "paddleocr"
            if items:
                errors.append(f"paddleocr: peu de blocs ({len(items)})")
        except Exception as e:  # noqa: BLE001
            errors.append(f"paddleocr: {e}")
            logger.warning("PaddleOCR indisponible: %s", e)
        finally:
            if path:
                Path(path).unlink(missing_ok=True)

    if not try_tesseract:
        try:
            items = _items_from_tesseract(image_bytes)
            if items:
                return items, "tesseract"
        except Exception as e:  # noqa: BLE001
            errors.append(f"tesseract: {e}")

    hint = (
        "OCR indisponible. Sur Render : paquets apt tesseract-ocr + tesseract-ocr-fra "
        "+ tesseract-ocr-eng (Native Environment). "
    )
    raise RuntimeError(hint + " | ".join(errors[:3]))


# ---------------------------------------------------------------------------
# Géométrie : droite / dessous
# ---------------------------------------------------------------------------

def _line_tol(items: list[dict]) -> float:
    if len(items) < 2:
        return 18.0
    ys = sorted(i["y"] for i in items)
    gaps = [ys[i + 1] - ys[i] for i in range(len(ys) - 1) if ys[i + 1] - ys[i] > 4]
    med = sorted(gaps)[len(gaps) // 2] if gaps else 28.0
    return max(14.0, min(med * 0.45, 28.0))


def _find_label(items: list[dict], *keywords: str) -> dict | None:
    """Trouve le bloc libellé (évite les valeurs seules). Match compact anti-OCR."""
    for item in items:
        tc = _compact(item["text"])
        for kw in keywords:
            kc = _compact(kw)
            if not kc:
                continue
            if kc in tc:
                # Refuser si le bloc est uniquement la valeur EN
                if _is_noise_value(item["text"]) and kc not in ("nom", "prenom", "npi"):
                    continue
                return item
    return None


def _value_after_colon(text: str) -> str:
    m = re.search(r":\s*(.+)$", text)
    if not m:
        return ""
    v = m.group(1).strip()
    return "" if _is_noise_value(v) else v


def _value_after_bilingual_label(text: str) -> str:
    """Ex. « Date d'expiration / Date of expiry 26/07/2028 » ou « Cardnumber100524259 »."""
    # Date DD/MM/YYYY collée
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", text)
    if m:
        return m.group(1)
    # Chiffres en fin (n° carte)
    m = re.search(r"(\d{6,})$", re.sub(r"\s", "", text))
    if m and not re.search(r"npi|personnel", text, flags=re.I):
        return m.group(1)
    # Après dernier /
    if "/" in text:
        tail = text.split("/")[-1]
        # Enlever la partie EN du libellé
        for noise in (
            "Date of expiry",
            "Date of birth",
            "Card number",
            "eID number",
            "Place of birth",
            "First names",
            "Surname",
            "Nationality",
            "Authority of issuance",
        ):
            tail = re.sub(re.escape(noise), "", tail, flags=re.I)
        tail = tail.strip(" :/-")
        if tail and not _is_noise_value(tail) and _compact(tail) not in (
            "dateofexpiry",
            "dateofbirth",
            "cardnumber",
            "eidnumber",
        ):
            return tail
    return ""


def right_value(items: list[dict], *keywords: str, max_gap: float | None = None) -> str:
    """Valeur à droite du libellé (même ligne Y) - layout CIP."""
    label = _find_label(items, *keywords)
    if not label:
        return ""

    # Valeur collée dans le même bloc (ex. « Expirele:08/06/2025 », « Nom:GNONLONFOUN »)
    same = _value_after_colon(label["text"])
    if same:
        return same
    same = _value_after_bilingual_label(label["text"])
    if same and any(ch.isdigit() for ch in same):
        return same

    tol = _line_tol(items)
    candidates = []
    for other in items:
        if other is label:
            continue
        if abs(other["y"] - label["y"]) > tol:
            continue
        if other["x0"] <= label["x1"] - 4:
            continue
        if max_gap is not None and other["x0"] - label["x1"] > max_gap:
            continue
        if _is_noise_value(other["text"]):
            continue
        # Ignorer un autre libellé collé (sauf valeur adresse type Com.:X)
        ot = _norm(other["text"]).lower()
        otc = _compact(other["text"])
        label_c = _compact(label["text"])
        if ot.endswith(":") and not re.search(r":\s*\S", other["text"]):
            continue
        if otc in ("com", "qt", "arr", "nom", "pere", "mere", "lieu"):
            continue
        # Ne pas traiter « Com.: PORTO-NOVO » comme un libellé si on cherche
        # le lieu de naissance (c'est la valeur).
        if otc.startswith(("com", "qt", "arr")) and ":" in other["text"]:
            if "naissance" not in label_c and "nationalite" not in label_c:
                # C'est un bloc adresse autonome - pas une valeur « à droite »
                # sauf si on cherche précisément Com/Qt/Arr
                if not any(k in label_c for k in ("com", "qt", "arr", "lieu")):
                    continue
        candidates.append(other)
    if not candidates:
        return ""
    candidates.sort(key=lambda i: i["x"])
    # Prendre le plus proche à droite, fusionner voisins proches
    first = candidates[0]
    parts = [first["text"]]
    for c in candidates[1:]:
        if c["x0"] - first["x1"] < 40 and abs(c["y"] - first["y"]) < tol:
            parts.append(c["text"])
            first = c
        else:
            break
    return " ".join(parts).strip()


def below_value(items: list[dict], *keywords: str, x_tol: float | None = None) -> str:
    """Valeur sous le libellé - layout CEDEAO."""
    label = _find_label(items, *keywords)
    if not label:
        return ""

    same = _value_after_colon(label["text"])
    if same:
        return same
    # Ne pas prendre la traduction EN du même bloc bilingue comme « valeur »
    same = _value_after_bilingual_label(label["text"])
    if same and not _is_noise_value(same) and "/" not in same:
        # Seulement si ça ressemble à une vraie valeur (pas le reste du libellé EN)
        if re.search(r"\d", same) or same.isupper() or len(same) <= 40:
            if _compact(same) not in (
                "surname",
                "firstnames",
                "nationality",
                "placeofbirth",
                "dateofbirth",
                "dateofexpiry",
                "cardnumber",
            ):
                # Pour noms/lieux sans chiffres, préférer le bloc dessous
                if re.search(r"\d", same):
                    return same

    tol_y = _line_tol(items)
    xt = x_tol if x_tol is not None else max(80.0, (label["x1"] - label["x0"]) * 1.2)
    candidates = []
    for other in items:
        if other is label:
            continue
        if other["y"] <= label["y"] + tol_y * 0.5:
            continue
        if other["y"] > label["y"] + tol_y * 4.5:
            continue
        if abs(other["x"] - label["x"]) > xt and abs(other["x0"] - label["x0"]) > xt:
            continue
        if _is_noise_value(other["text"]):
            continue
        ot = _norm(other["text"]).lower()
        otc = _compact(other["text"])
        # Libellé suivant bilingue
        if "/" in other["text"] and any(
            k in otc
            for k in (
                "nationalit",
                "lieudenaissance",
                "datede",
                "autorite",
                "numero",
                "prenom",
                "expire",
                "signature",
                "surname",
                "firstnames",
            )
        ):
            continue
        if ot.endswith(":"):
            continue
        candidates.append(other)
    if not candidates:
        return ""
    candidates.sort(key=lambda i: i["y"])
    for c in candidates:
        if _is_noise_value(c["text"]):
            continue
        t = c["text"].strip()
        if "/" in t and any(
            w in t.lower() for w in ("surname", "first", "nationality", "place", "date", "authority")
        ):
            continue
        return t
    return ""


def _digits(text: str, n: int | None = None) -> str:
    d = re.sub(r"\D", "", _ocr_fix_digits(text or ""))
    if n and len(d) >= n:
        m = re.search(rf"(\d{{{n}}})", d)
        return m.group(1) if m else d[:n]
    return d


def _ocr_fix_digits(text: str) -> str:
    """Corrige confusions OCR fréquentes sur images floues (O→0, I→1…)."""
    if not text:
        return ""
    return text.translate(
        str.maketrans(
            {
                "O": "0",
                "o": "0",
                "Q": "0",
                "D": "0",
                "I": "1",
                "l": "1",
                "|": "1",
                "!": "1",
                "i": "1",
                "Z": "2",
                "z": "2",
                "S": "5",
                "s": "5",
                "B": "8",
                "G": "6",
                "g": "9",
            }
        )
    )


def _extract_n_digit_runs(text: str, n: int = 10) -> list[str]:
    """Tous les runs de n chiffres, après correction OCR."""
    fixed = _ocr_fix_digits(text)
    only = re.sub(r"[^\d]", " ", fixed)
    compact = re.sub(r"\D", "", fixed)
    found: list[str] = []
    for m in re.finditer(rf"(?<!\d)(\d{{{n}}})(?!\d)", only):
        found.append(m.group(1))
    for m in re.finditer(rf"(?<!\d)(\d{{{n}}})(?!\d)", compact):
        found.append(m.group(1))
    if re.fullmatch(rf"\d{{{n}}}", compact):
        found.append(compact)
    out: list[str] = []
    for x in found:
        if x not in out:
            out.append(x)
    return out


# ---------------------------------------------------------------------------
# Parseurs carte
# ---------------------------------------------------------------------------

def detect_card(items: list[dict]) -> str:
    txt = " ".join(i["text"] for i in items)
    c = _compact(txt)
    if "cedeao" in c or "ecowas" in c or "bilhetedeidentidade" in c:
        return "cedeao"
    if "certificatdidentificationpersonnelle" in c or "certificatidentificationpersonnelle" in c:
        return "cip"
    if "anip" in c and ("personnel" in c or "npi" in c):
        return "cip"
    if "cartedidentite" in c or "carteidentite" in c:
        return "cedeao"
    return "unknown"


def _npi_from_items(items: list[dict]) -> str:
    """NPI = 10 chiffres. Robuste au flou (O/0, blocs collés, voisin géométrique)."""

    # 1) Libellé NPI → droite / dessous
    for kw in (
        "numero personnel d'identification",
        "numero personnel d identification",
        "personnel d'identification",
        "personneldidentification",
        "(npi)",
        "npi",
        "eid number",
        "eidnumber",
    ):
        for v in (right_value(items, kw), below_value(items, kw)):
            for cand in _extract_n_digit_runs(v, 10):
                return cand

    # 2) Bloc libellé NPI + voisins
    for item in items:
        tc = _compact(item["text"])
        if not any(k in tc for k in ("npi", "personnel", "eidnumber", "eid")):
            continue
        for cand in _extract_n_digit_runs(item["text"], 10):
            return cand
        for other in items:
            if other is item:
                continue
            if abs(other["y"] - item["y"]) < 40 and other["x"] >= item["x"] - 10:
                for cand in _extract_n_digit_runs(other["text"], 10):
                    return cand
            if (
                other["y"] > item["y"]
                and other["y"] < item["y"] + 50
                and abs(other["x"] - item["x"]) < 140
            ):
                for cand in _extract_n_digit_runs(other["text"], 10):
                    return cand

    # 3) Blocs exactement 10 chiffres (exclure sous-chaînes d'un n° certificat 12-16)
    cert_subs: set[str] = set()
    for item in items:
        raw = re.sub(r"\D", "", _ocr_fix_digits(item["text"]))
        if 12 <= len(raw) <= 16:
            for i in range(0, len(raw) - 9):
                cert_subs.add(raw[i : i + 10])

    exact: list[str] = []
    loose: list[str] = []
    for item in items:
        compact = re.sub(r"\D", "", _ocr_fix_digits(item["text"]))
        for cand in _extract_n_digit_runs(item["text"], 10):
            if compact == cand:
                exact.append(cand)
            elif cand not in cert_subs:
                loose.append(cand)

    for cand in exact + loose:
        return cand

    # 4) Texte global (fallback type ancien regex)
    blob = " ".join(i["text"] for i in items)
    for cand in _extract_n_digit_runs(blob, 10):
        if cand not in cert_subs:
            return cand
    for cand in _extract_n_digit_runs(blob, 10):
        return cand
    return ""


def parse_cedeao(items: list[dict]) -> dict[str, Any]:
    """Valeurs sous les libellés ; NPI / expiration / n° carte souvent à droite ou collés."""
    nom = below_value(items, "Nom /", "Nom / Surname", "Nom")
    if _is_noise_value(nom) or _compact(nom) in ("surname",):
        lab = _find_label(items, "Nom /", "Nom / Surname", "Nom")
        nom = ""
        if lab:
            for other in sorted(items, key=lambda i: i["y"]):
                if other["y"] > lab["y"] + 8 and abs(other["x"] - lab["x"]) < 120:
                    if not _is_noise_value(other["text"]) and "/" not in other["text"]:
                        nom = other["text"]
                        break

    prenoms = below_value(items, "Prenoms /", "Prénoms /", "Prenoms", "Prénoms", "First names")
    if _is_noise_value(prenoms):
        prenoms = ""

    nationalite = below_value(items, "Nationalite", "Nationalité")
    if _is_noise_value(nationalite):
        nationalite = ""

    lieu = below_value(items, "Lieu de naissance", "Place of birth", "Lieudenaissance")
    if _is_noise_value(lieu):
        lieu = ""

    # Date naissance : sous le libellé (souvent à droite de Nationalité)
    birth = below_value(items, "Date de naissance", "Date of birth", "Datedenaissance")
    if not birth:
        birth = right_value(items, "Date de naissance", "Date of birth")
    if not birth:
        lab = _find_label(items, "Date de naissance", "Date of birth", "Datedenaissance")
        if lab:
            same = _value_after_bilingual_label(lab["text"])
            if same:
                birth = same
            else:
                for other in items:
                    if abs(other["y"] - lab["y"]) < 30 and other["x"] > lab["x"] - 20:
                        if re.search(r"\d{2}/\d{2}/\d{4}", other["text"]):
                            birth = other["text"]
                            break
                    if other["y"] > lab["y"] + 5 and abs(other["x"] - lab["x"]) < 150:
                        if re.search(r"\d{2}/\d{2}/\d{4}", other["text"]):
                            birth = other["text"]
                            break
    birth_iso = _parse_date(birth) if birth else None

    autorite = below_value(items, "Autorite", "Autorité", "Authority")
    expiry = ""
    for item in items:
        if "expir" in _compact(item["text"]) or "expiry" in _compact(item["text"]):
            expiry = _value_after_bilingual_label(item["text"]) or _value_after_colon(item["text"])
            if not expiry:
                expiry = right_value(items, "Date d'expiration", "Date of expiry")
            if not expiry:
                expiry = below_value(items, "Date d'expiration", "Date of expiry")
            break
    expiry_iso = _parse_date(expiry) if expiry else None

    card_no = ""
    for item in items:
        tc = _compact(item["text"])
        if "numerodecarte" in tc or "cardnumber" in tc:
            card_no = _value_after_bilingual_label(item["text"]) or _value_after_colon(item["text"])
            if not card_no:
                card_no = right_value(items, "Numero de carte", "Numéro de carte", "Card number")
            if not card_no:
                card_no = below_value(items, "Numero de carte", "Numéro de carte", "Card number")
            break

    nat = (nationalite or "").upper()
    if "BENIN" in _compact(nat).upper():
        nat = "BENIN"

    return {
        "npi": _npi_from_items(items),
        "last_name": nom.strip() if nom and not _is_noise_value(nom) else None,
        "first_name": prenoms.strip() if prenoms and not _is_noise_value(prenoms) else None,
        "nationality": nat or None,
        "birth_place": lieu.strip() if lieu else None,
        "birth_date": birth_iso,
        "expiry_date": expiry_iso,
        "card_number": _digits(card_no) or None,
        "father_name": None,
        "mother_name": None,
        "phone": None,
        "address_commune": None,
        "address_arrondissement": None,
        "address_quartier": None,
        "address_lieu": None,
        "certificate_number": None,
        "authority": autorite or None,
        "card_type": "cedeao",
    }


def _cip_inline_field(items: list[dict], prefixes: tuple[str, ...], *, address_only: bool = False) -> str:
    """Extrait « Pref.: VALEUR » même si tout est dans un seul bloc OCR."""
    for item in items:
        t = item["text"]
        tc = _compact(t)
        if address_only and "naissance" in tc:
            continue
        for pref in prefixes:
            pc = _compact(pref)
            # Exiger le préfixe comme libellé (début ou après /)
            if not (
                tc.startswith(pc)
                or re.search(rf"(?:^|/){re.escape(pref)}\s*\.?\s*:", t, flags=re.I)
            ):
                continue
            pat = re.compile(
                rf"(?:^|/)\s*{re.escape(pref)}\s*\.?\s*:\s*(.+)",
                flags=re.I,
            )
            m = pat.search(t)
            if not m:
                # Début de ligne sans slash
                m = re.search(
                    rf"^{re.escape(pref)}\s*\.?\s*:?\s*(.+)",
                    t,
                    flags=re.I,
                )
            if not m:
                continue
            val = m.group(1).strip()
            val = re.split(
                r"\s*/\s*(?:Arr|Qt|Com|Lieu)\s*\.?\s*:",
                val,
                maxsplit=1,
                flags=re.I,
            )[0]
            val = val.strip(" :/")
            if val and not _is_noise_value(val) and "naissance" not in _compact(val):
                return val
    return ""


def parse_cip(items: list[dict]) -> dict[str, Any]:
    """Valeurs à droite des libellés (ou collées dans le même bloc)."""
    nom = right_value(items, "Nom")
    lab_nom = _find_label(items, "Nom")
    if lab_nom and re.search(r"pere|mere|père|mère", lab_nom["text"], flags=re.I):
        nom = ""
        for item in items:
            tc = _compact(item["text"])
            if tc.startswith("nom") and "pere" not in tc and "mere" not in tc and "prenom" not in tc:
                nom = right_value(items, item["text"][:20]) or _value_after_colon(item["text"])
                if nom:
                    break

    prenoms = right_value(items, "Prenom", "Prénom", "Prenoms", "Prénoms")
    birth_raw = right_value(items, "Date de naissance")
    lieu = right_value(items, "Lieu de naissance")
    if lieu:
        lieu = re.sub(r"^Com\.?\s*:?\s*", "", lieu, flags=re.I).strip()
        lieu = re.split(r"\s*/\s*", lieu)[0].strip()

    nationalite = right_value(items, "Nationalite", "Nationalité")
    phone = right_value(
        items, "Numero de telephone", "Numéro de téléphone", "telephone", "téléphone"
    )

    # Adresse : souvent sous « Adresse de résidence », blocs Com./Qt./Lieu
    addr_items = items
    addr_lab = _find_label(items, "Adresse de residence", "Adresse de résidence", "Adresse")
    if addr_lab:
        addr_items = [i for i in items if i["y"] >= addr_lab["y"] - 5]

    commune = _cip_inline_field(addr_items, ("Com",), address_only=True)
    arr = _cip_inline_field(addr_items, ("Arr",), address_only=True)
    if commune and "/" in commune and not arr:
        parts = [p.strip() for p in re.split(r"\s*/\s*", commune, maxsplit=1)]
        commune = re.sub(r"^Com\.?\s*:?\s*", "", parts[0], flags=re.I).strip()
        if len(parts) > 1:
            arr = re.sub(r"^Arr\.?\s*:?\s*", "", parts[1], flags=re.I).strip()

    quartier = _cip_inline_field(addr_items, ("Qt",), address_only=True)
    address_lieu = ""
    for item in addr_items:
        tc = _compact(item["text"])
        if not tc.startswith("lieu"):
            continue
        if "naissance" in tc:
            continue
        address_lieu = _value_after_colon(item["text"])
        if address_lieu:
            break
        m = re.match(r"Lie[uU]\s*\.?\s*:?\s*(.+)", item["text"])
        if m:
            address_lieu = m.group(1).strip()
            break

    def _clean_addr(v: str) -> str:
        if not v:
            return ""
        if any(x in _compact(v) for x in ("titulaire", "anip", "signature", "naissance")):
            return ""
        return v

    quartier = _clean_addr(quartier)
    address_lieu = _clean_addr(address_lieu)
    commune = _clean_addr(commune)
    arr = _clean_addr(arr)

    pere = right_value(items, "Pere", "Père")
    mere = right_value(items, "Mere", "Mère")
    expiry_raw = right_value(items, "Expire")
    cert = ""
    for item in items:
        m = re.search(r"N[°o`']?\s*(\d{12,16})", item["text"], flags=re.I)
        if m:
            cert = m.group(1)
            break
        if re.fullmatch(r"\d{14}", re.sub(r"\s", "", item["text"])):
            cert = re.sub(r"\s", "", item["text"])

    nat = (nationalite or "").upper()
    if "BENIN" in _compact(nat).upper():
        nat = "BENIN"

    return {
        "npi": _npi_from_items(items),
        "last_name": nom.strip() if nom else None,
        "first_name": prenoms.strip() if prenoms else None,
        "birth_date": _parse_date(birth_raw) if birth_raw else None,
        "birth_place": lieu or None,
        "nationality": nat or None,
        "phone": _digits(phone) or None,
        "address_commune": commune or None,
        "address_arrondissement": arr or None,
        "address_quartier": quartier or None,
        "address_lieu": address_lieu or None,
        "father_name": pere or None,
        "mother_name": mere or None,
        "expiry_date": _parse_date(expiry_raw) if expiry_raw else None,
        "certificate_number": cert or None,
        "card_number": None,
        "card_type": "cip",
    }


def parse_items(items: list[dict]) -> dict[str, Any]:
    kind = detect_card(items)
    if kind == "cedeao":
        data = parse_cedeao(items)
    elif kind == "cip":
        data = parse_cip(items)
    else:
        # Essayer les deux, garder celui avec le plus de champs
        a, b = parse_cedeao(items), parse_cip(items)
        a["card_type"] = "unknown"
        b["card_type"] = "unknown"
        score = lambda d: sum(1 for k, v in d.items() if v and k != "card_type")
        data = a if score(a) >= score(b) else b

    # Nettoyage final anti-EN
    for key in ("last_name", "first_name", "birth_place", "nationality"):
        if data.get(key) and _is_noise_value(str(data[key])):
            data[key] = None
    return data


def ocr_id_card(image_bytes: bytes) -> dict[str, Any]:
    timeout = int(os.environ.get("DOTO_OCR_TIMEOUT", "25"))

    def _run() -> dict[str, Any]:
        items, engine = read_items(image_bytes)
        data = parse_items(items)
        data["ocr_engine"] = engine
        data["raw_text"] = "\n".join(i["text"] for i in items)[:3000]
        logger.info(
            "OCR ok engine=%s npi=%s type=%s blocs=%s",
            engine,
            data.get("npi") or "-",
            data.get("card_type"),
            len(items),
        )
        if not data.get("npi"):
            raise ValueError(
                "NPI introuvable. Cadrez toute la carte CIP ou CEDEAO, NPI bien lisible."
            )
        return data

    if timeout <= 0:
        return _run()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeout as e:
            raise TimeoutError(
                f"OCR timeout après {timeout}s. Réessayez avec une photo nette et cadrée."
            ) from e


# Compat tests unitaires texte-only (sans géométrie)
def parse_benin_id_text(text: str) -> dict[str, Any]:
    """Fallback regex minimal si seuls les textes sont dispo (tests)."""
    fake = []
    y = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fake.append({"text": line, "x": 100, "y": y, "x0": 40, "x1": 400, "y0": y, "y1": y + 10})
        y += 30
    return parse_items(fake)
