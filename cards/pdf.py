"""Génération PDF imprimable DotoCard - faces Assuré / Non assuré (marque DOTO+)."""
from __future__ import annotations

import io
from pathlib import Path

import qrcode
from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from accounts.phone import display_phone

# Bandeau de page (hors carte) - inchangé.
NAVY = HexColor("#1E3755")
MUTED = HexColor("#6B7280")
TEXT = HexColor("#1F2937")
INSURANCE = HexColor("#0F5C45")

# Carte patient, alignée sur la maquette.
CARD_NAVY = HexColor("#003462")
CARD_TEAL = HexColor("#3d8ba8")
CARD_RED = HexColor("#c01818")
CARD_ORANGE = HexColor("#d03a00")
CARD_BORDER = HexColor("#1f3a6e")
CARD_LABEL = HexColor("#8b95a1")
FRONT_BG = HexColor("#e3e3e9")
BACK_BG = HexColor("#f7f7f7")
BAND_BG = HexColor("#f6d4d0")
INS_BG = HexColor("#fde6dc")
PHOTO_BG = HexColor("#e7e9ee")
PHOTO_BORDER = HexColor("#c5cad3")

# Ratio 1,6 (960×600).
CARD_W = 160 * mm
CARD_H = 100 * mm
BANNER_H = 28 * mm
CARD_RADIUS = CARD_W * (45 / 960)


def _logo_path() -> Path | None:
    roots = [
        Path(__file__).resolve().parents[2] / "logos" / "DotoCard.png",
        Path(__file__).resolve().parents[2] / "logos" / "grand D+.png",
        Path(__file__).resolve().parents[2] / "dotoplus" / "assets" / "logo-dodocard.png",
    ]
    for p in roots:
        if p.is_file():
            return p
    return None


def _arms_path() -> Path | None:
    p = Path(__file__).resolve().parents[2] / "dotoplus" / "assets" / "armoiries-benin.png"
    return p if p.is_file() else None


def _fmt_date(d) -> str:
    if not d:
        return "-"
    try:
        return d.strftime("%d/%m/%Y")
    except Exception:
        return str(d)


def _allergies_label(patient) -> str:
    dossier = getattr(patient, "dossier", None)
    allergies = getattr(dossier, "allergies", None) or []
    if isinstance(allergies, list):
        items = [" ".join(str(a).split()) for a in allergies if str(a or "").strip()]
        if items:
            return ", ".join(items)
    return "-"


def _format_npi(raw) -> str:
    value = " ".join(str(raw or "").split())
    if not value:
        return "-"
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) == 10:
        return f"{digits[0:3]}-{digits[3:6]}-{digits[6:9]}-{digits[9]}"
    return value


def _or_dash(value) -> str:
    text = " ".join(str(value or "").split())
    return text or "-"


def _draw_rounded(c: canvas.Canvas, x, y, w, h, r, fill=None, stroke=None, stroke_width=0.8):
    c.saveState()
    if fill is not None:
        c.setFillColor(fill)
    if stroke is not None:
        c.setStrokeColor(stroke)
        c.setLineWidth(stroke_width)
    c.roundRect(x, y, w, h, r, fill=1 if fill is not None else 0, stroke=1 if stroke is not None else 0)
    c.restoreState()


def _clean(text) -> str:
    return " ".join(str(text or "").split()) or "-"


def _lines_for(text: str, font: str, size: float, max_w: float) -> list[str]:
    """Découpe sur les espaces. Ne coupe jamais un mot."""
    words = _clean(text).split()
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = word if not cur else f"{cur} {word}"
        if stringWidth(trial, font, size) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or ["-"]


def _fit_lines(text, font, max_size, min_size, max_w, max_lines) -> tuple[list[str], float]:
    size = float(max_size)
    floor = float(min_size)
    while True:
        lines = _lines_for(text, font, size, max_w)
        fits = len(lines) <= max_lines and all(stringWidth(ln, font, size) <= max_w + 0.6 for ln in lines)
        if fits or size <= floor + 0.01:
            if not fits:
                lines = lines[:max_lines] or ["-"]
            return lines, size
        size = max(floor, round(size - 0.25, 2))


def _draw_fitted(
    c: canvas.Canvas,
    text,
    x,
    baseline,
    max_w,
    font,
    max_size,
    color,
    max_lines=1,
    min_size=6,
    align="left",
) -> float:
    """Dessine le texte dans max_w. Retourne l'ordonnée sous la dernière ligne."""
    lines, size = _fit_lines(text, font, max_size, min_size, max_w, max_lines)
    lead = size * 1.28
    c.setFillColor(color)
    c.setFont(font, size)
    y = baseline
    for ln in lines:
        if align == "center":
            c.drawCentredString(x, y, ln)
        elif align == "right":
            c.drawRightString(x, y, ln)
        else:
            c.drawString(x, y, ln)
        y -= lead
    return y


def _open_card(c: canvas.Canvas, x, y, w, h, fill):
    _draw_rounded(c, x, y, w, h, CARD_RADIUS, fill=fill, stroke=None)
    c.saveState()
    path = c.beginPath()
    path.roundRect(x, y, w, h, CARD_RADIUS)
    c.clipPath(path, stroke=0, fill=0)


def _close_card(c: canvas.Canvas, x, y, w, h):
    c.restoreState()
    c.saveState()
    c.setStrokeColor(CARD_BORDER)
    c.setLineWidth(0.7 * mm)
    c.roundRect(x, y, w, h, CARD_RADIUS, fill=0, stroke=1)
    c.restoreState()


def _draw_watermark(c: canvas.Canvas, x, y, w, h):
    c.saveState()
    cx = x + w * 0.50
    cy = y + h * 0.46
    disc = w * 0.28
    c.setStrokeColor(Color(0.66, 0.76, 0.84, alpha=0.45))
    c.setLineWidth(max(2.2, w * 0.028))
    c.circle(cx, cy, disc, stroke=1, fill=0)
    arm = w * 0.05
    length = w * 0.18
    c.setFillColor(Color(0.69, 0.78, 0.86, alpha=0.42))
    c.rect(cx - arm / 2, cy - length / 2, arm, length, fill=1, stroke=0)
    c.rect(cx - length / 2, cy - arm / 2, length, arm, fill=1, stroke=0)
    c.restoreState()


def _draw_header_bar(c, x, y, w, h, title, right=None, with_arms=False):
    header_h = h * 0.125
    c.setFillColor(CARD_NAVY)
    c.rect(x, y + h - header_h, w, header_h, fill=1, stroke=0)
    title_size = 11
    baseline = y + h - header_h + (header_h - title_size) * 0.38
    right_limit = x + w - 3.2 * mm
    if with_arms:
        arms = _arms_path()
        if arms:
            try:
                reader = ImageReader(str(arms))
                iw, ih = reader.getSize()
                ah = header_h * 0.82
                aw = ah * (float(iw) / float(ih)) if ih else ah
                ax = right_limit - aw
                ay = y + h - header_h + (header_h - ah) / 2
                c.drawImage(reader, ax, ay, width=aw, height=ah, mask="auto", preserveAspectRatio=True)
                right_limit = ax - 1.6 * mm
            except Exception:
                pass
    if right:
        c.setFillColor(white)
        c.setFont("Helvetica", 8)
        c.drawRightString(right_limit, baseline, right)
        right_limit -= stringWidth(right, "Helvetica", 8) + 2 * mm
    title_w = max(20 * mm, right_limit - (x + 4 * mm))
    _draw_fitted(
        c,
        title,
        x + 4 * mm,
        baseline,
        title_w,
        "Helvetica-Bold",
        title_size,
        white,
        max_lines=1,
        min_size=8,
    )


def _draw_image_cover(c: canvas.Canvas, path: Path, x, y, w, h):
    reader = ImageReader(str(path))
    iw, ih = reader.getSize()
    if not iw or not ih:
        return
    scale = max(w / float(iw), h / float(ih))
    dw, dh = iw * scale, ih * scale
    dx = x - (dw - w) / 2
    dy = y - (dh - h) / 2
    c.saveState()
    try:
        clip = c.beginPath()
        clip.rect(x, y, w, h)
        c.clipPath(clip, stroke=0, fill=0)
        c.drawImage(reader, dx, dy, width=dw, height=dh, mask="auto")
    finally:
        c.restoreState()


def _draw_photo(c: canvas.Canvas, patient, x, y, side):
    c.setFillColor(PHOTO_BG)
    c.rect(x, y, side, side, fill=1, stroke=0)
    photo = getattr(patient, "photo", None)
    path = getattr(photo, "path", None) if photo else None
    if path:
        p = Path(path)
        if p.is_file():
            try:
                _draw_image_cover(c, p, x, y, side, side)
            except Exception:
                pass
    c.setStrokeColor(PHOTO_BORDER)
    c.setLineWidth(0.6)
    c.rect(x, y, side, side, fill=0, stroke=1)


def _draw_card_front(c: canvas.Canvas, card, x, y, w, h, qr_reader):
    patient = card.patient
    pad = 4.6 * mm
    header_h = h * 0.125
    footer_h = h * 0.195

    _open_card(c, x, y, w, h, FRONT_BG)
    _draw_watermark(c, x, y, w, h)
    _draw_header_bar(c, x, y, w, h, "Carte d'accès santé", "République du Bénin", with_arms=True)

    content_top = y + h - header_h
    brand_base = content_top - 6.2 * mm
    c.setFillColor(CARD_NAVY)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(x + pad, brand_base, "Doto")
    doto_w = stringWidth("Doto", "Helvetica-Bold", 16)
    c.setFillColor(CARD_TEAL)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(x + pad + doto_w, brand_base, "Card")

    card_no = f"Carte N°: {card.id}"
    _draw_fitted(
        c,
        card_no,
        x + pad,
        brand_base - 4.6 * mm,
        w * 0.48,
        "Helvetica-Bold",
        8.5,
        CARD_NAVY,
        max_lines=1,
        min_size=6.5,
    )

    npi = f"NPI: {_format_npi(getattr(patient, 'npi', ''))}"
    npi_size = 9
    npi_max = w * 0.46
    while npi_size > 6.5 and stringWidth(npi, "Helvetica-Bold", npi_size) > npi_max:
        npi_size -= 0.25
    npi_w = stringWidth(npi, "Helvetica-Bold", npi_size)
    npi_x = x + w - pad - npi_w
    c.setFillColor(CARD_NAVY)
    c.setFont("Helvetica-Bold", npi_size)
    c.drawString(npi_x, brand_base, npi)
    c.setStrokeColor(CARD_NAVY)
    c.setLineWidth(0.5)
    c.line(npi_x, brand_base - 0.7, npi_x + npi_w, brand_base - 0.7)

    mid_top = brand_base - 7.2 * mm
    mid_bottom = y + footer_h + 1.4 * mm
    mid_h = max(18 * mm, mid_top - mid_bottom)
    photo_side = min(42 * mm, mid_h)
    photo_x = x + pad
    photo_y = mid_top - photo_side
    _draw_photo(c, patient, photo_x, photo_y, photo_side)

    qr_col = 36 * mm
    qr_left = x + w - pad - qr_col
    id_x = photo_x + photo_side + 3.2 * mm
    id_w = qr_left - 3 * mm - id_x
    fields = [
        ("Nom", (getattr(patient, "nom", "") or "").strip().upper() or "-"),
        ("Prénoms", _or_dash(getattr(patient, "prenom", ""))),
        ("Date de naissance", _fmt_date(getattr(patient, "date_naissance", None))),
        ("Lieu de naissance", _or_dash(getattr(patient, "lieu_naissance", ""))),
    ]
    slot = photo_side / len(fields)
    for index, (label, value) in enumerate(fields):
        top = mid_top - index * slot
        c.setFillColor(CARD_LABEL)
        c.setFont("Helvetica", 6.5)
        c.drawString(id_x, top - 2.5 * mm, label)
        _draw_fitted(
            c,
            value,
            id_x,
            top - 6.2 * mm,
            id_w,
            "Helvetica-Bold",
            9,
            CARD_NAVY,
            max_lines=1,
            min_size=6.5,
        )

    phone = display_phone(getattr(patient, "telephone", "") or "")
    qr_size = min(30 * mm, qr_col - 1 * mm, max(18 * mm, photo_side - 8 * mm))
    qr_x = qr_left + qr_col - qr_size
    qr_y = mid_top - qr_size
    if qr_y - 6 * mm < mid_bottom:
        qr_y = mid_bottom + 6 * mm
        qr_size = min(qr_size, mid_top - qr_y)
    c.setFillColor(white)
    c.rect(qr_x, qr_y, qr_size, qr_size, fill=1, stroke=0)
    c.drawImage(qr_reader, qr_x, qr_y, width=qr_size, height=qr_size, mask="auto")
    _draw_fitted(
        c,
        f"Num. tél: {phone}",
        x + w - pad,
        qr_y - 4.2 * mm,
        qr_col + 6 * mm,
        "Helvetica-Bold",
        7.4,
        CARD_NAVY,
        max_lines=2,
        min_size=5.8,
        align="right",
    )

    c.setFillColor(BAND_BG)
    c.rect(x, y, w, footer_h, fill=1, stroke=0)
    blood = _or_dash(getattr(patient, "groupe_sanguin", ""))
    allergies = _allergies_label(patient)
    urg = display_phone(getattr(patient, "tel_urgence", "") or "")
    col_w = (w - 2 * pad) / 3
    items = (
        ("Groupe sanguin:", blood, 1),
        ("Allergies connues", allergies, 2),
        ("Numéro en cas d'urgence", urg, 2),
    )
    label_y = y + footer_h - 5.2 * mm
    for index, (label, value, lines) in enumerate(items):
        cx = x + pad + index * col_w
        _draw_fitted(c, label, cx, label_y, col_w - 1.5 * mm, "Helvetica-Bold", 6.6, CARD_RED, max_lines=2, min_size=5.4)
        _draw_fitted(
            c,
            value,
            cx,
            label_y - 4.6 * mm,
            col_w - 1.5 * mm,
            "Helvetica-Bold",
            8.5,
            CARD_RED,
            max_lines=lines,
            min_size=6,
        )

    _close_card(c, x, y, w, h)


def _insured(patient) -> bool:
    assurance = getattr(patient, "assurance", None)
    return bool(
        assurance and getattr(assurance, "assureur", None) and getattr(assurance, "droits_valides", True)
    )


def _draw_coverage(c, patient, x, y, w, h):
    assurance = getattr(patient, "assurance", None)
    insured = _insured(patient)
    radius = min(4.2 * mm, h * 0.18)
    c.setFillColor(INS_BG)
    c.roundRect(x, y, w, h, radius, fill=1, stroke=0)
    pad = 4 * mm
    inner_w = w - 2 * pad
    _draw_fitted(
        c,
        "COUVERTURE ASSURANTIELLE",
        x + pad,
        y + h - 5.4 * mm,
        inner_w,
        "Helvetica-Bold",
        8,
        CARD_ORANGE,
        max_lines=1,
        min_size=6.5,
    )

    if insured:
        badge = _clean(getattr(assurance, "assureur", "") or "") or "Assuré"
        lines = [f"Police: {_or_dash(getattr(assurance, 'num_police', ''))}"]
        coverage = " ".join(str(getattr(assurance, "type_couverture", "") or "").split())
        if coverage:
            lines.append(coverage)
    else:
        badge = "Non Assuré"
        lines = [
            "Soins à paiement direct auprès des structures partenaires",
            "Urgence vitale : dispositif national de paiement différé",
        ]

    badge_size = 9
    while badge_size > 6.5 and stringWidth(badge, "Helvetica-Bold", badge_size) > inner_w - 8 * mm:
        badge_size -= 0.25
    badge_w = min(inner_w, stringWidth(badge, "Helvetica-Bold", badge_size) + 7 * mm)
    badge_h = 6.4 * mm
    badge_y = y + h - 13.2 * mm
    c.setFillColor(white)
    c.setStrokeColor(HexColor("#f0c2b0"))
    c.setLineWidth(0.6)
    c.roundRect(x + pad, badge_y, badge_w, badge_h, badge_h / 2, fill=1, stroke=1)
    c.setFillColor(CARD_ORANGE)
    c.setFont("Helvetica-Bold", badge_size)
    c.drawString(x + pad + 3.2 * mm, badge_y + 2.05 * mm, badge)

    cursor = badge_y - 4.4 * mm
    for line in lines:
        cursor = _draw_fitted(
            c,
            line,
            x + pad,
            cursor,
            inner_w,
            "Helvetica",
            8,
            CARD_ORANGE,
            max_lines=2,
            min_size=6.2,
        )
        cursor -= 0.8 * mm


def _pair(c, label, value, x, baseline, max_w):
    c.setFillColor(CARD_NAVY)
    c.setFont("Helvetica", 8)
    c.drawString(x, baseline, label)
    label_w = stringWidth(label + " ", "Helvetica", 8)
    _draw_fitted(
        c,
        value,
        x + label_w,
        baseline,
        max(12 * mm, max_w - label_w),
        "Helvetica-Bold",
        8.5,
        CARD_NAVY,
        max_lines=1,
        min_size=6.5,
    )


def _draw_card_back(c: canvas.Canvas, card, x, y, w, h):
    patient = card.patient
    pad = 5 * mm
    header_h = h * 0.125
    legal_h = 16 * mm

    _open_card(c, x, y, w, h, BACK_BG)
    _draw_watermark(c, x, y, w, h)
    _draw_header_bar(c, x, y, w, h, "Informations complémentaires")

    gutter = 6 * mm
    col_w = (w - 2 * pad - gutter) / 2
    left_x = x + pad
    right_x = left_x + col_w + gutter
    zone_top = y + h - header_h - 7 * mm

    def _title(text, cx, underline):
        c.setFillColor(CARD_NAVY)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(cx, zone_top, text)
        c.setStrokeColor(CARD_NAVY)
        c.setLineWidth(0.6)
        c.line(cx, zone_top - 1.15 * mm, cx + underline, zone_top - 1.15 * mm)

    _title("Filiation", left_x, min(col_w, stringWidth("Filiation", "Helvetica-Bold", 9) + 6 * mm))
    _title("Adresse de résidence", right_x, col_w)

    pere = _or_dash(getattr(patient, "nom_pere", ""))
    mere = _or_dash(getattr(patient, "nom_mere", ""))
    commune = _or_dash(getattr(patient, "adresse_commune", ""))
    quartier = _or_dash(getattr(patient, "adresse_quartier", ""))
    _pair(c, "Père:", pere, left_x, zone_top - 7 * mm, col_w)
    _pair(c, "Mère:", mere, left_x, zone_top - 12.2 * mm, col_w)
    _pair(c, "Com.", commune, right_x, zone_top - 7 * mm, col_w)
    _pair(c, "Qtr:", quartier, right_x, zone_top - 12.2 * mm, col_w)

    box_h = 34 * mm
    box_top = zone_top - 16.5 * mm
    box_y = box_top - box_h
    min_y = y + legal_h + 1.6 * mm
    if box_y < min_y:
        box_y = min_y
        box_h = max(24 * mm, box_top - box_y)
    _draw_coverage(c, patient, x + pad, box_y, w - 2 * pad, box_h)

    disc = "Cette carte est strictement personnelle, non-transférable et demeure la propriété de l'émetteur.*"
    _draw_fitted(
        c,
        disc,
        x + w / 2,
        y + legal_h - 5.2 * mm,
        w - 2 * pad,
        "Helvetica",
        6.6,
        CARD_RED,
        max_lines=2,
        min_size=5.4,
        align="center",
    )
    exp = _fmt_date(card.date_expiration)
    c.setFillColor(CARD_NAVY)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(x + w / 2, y + 3.6 * mm, f"Valable jusqu'au : {exp}")

    _close_card(c, x, y, w, h)


def _card_slots(page_w, page_h):
    card_x = (page_w - CARD_W) / 2
    front_y = page_h - BANNER_H - 10 * mm - CARD_H
    back_y = front_y - 12 * mm - CARD_H
    return card_x, front_y, back_y, CARD_W, CARD_H


def build_dodocard_pdf(card) -> bytes:
    """PDF A4 : face avant + face arrière (Assuré ou Non assuré)."""
    patient = card.patient
    # Prefetch relations utiles
    _ = getattr(patient, "dossier", None)
    _ = getattr(patient, "assurance", None)

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # En-tête page
    c.setFillColor(NAVY)
    c.rect(0, height - BANNER_H, width, BANNER_H, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(18 * mm, height - 14 * mm, "DOTO+")
    c.setFont("Helvetica", 10)
    c.drawString(18 * mm, height - 21 * mm, "DotoCard - carte d'accès santé · République du Bénin")

    logo = _logo_path()
    if logo:
        try:
            c.drawImage(
                str(logo),
                width - 42 * mm,
                height - 24 * mm,
                width=24 * mm,
                height=16 * mm,
                mask="auto",
                preserveAspectRatio=True,
            )
        except Exception:
            pass

    # QR haute rés — même token opaque, correction M.
    qr_img = qrcode.make(card.token_chiffre, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr_buf = io.BytesIO()
    qr_img.save(qr_buf, format="PNG")
    qr_buf.seek(0)
    qr_reader = ImageReader(qr_buf)

    card_x, front_y, back_y, card_w, card_h = _card_slots(width, height)

    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(card_x, front_y + card_h + 3.2 * mm, "Recto")
    _draw_card_front(c, card, card_x, front_y, card_w, card_h, qr_reader)

    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(card_x, back_y + card_h + 3.2 * mm, "Verso")
    _draw_card_back(c, card, card_x, back_y, card_w, card_h)

    c.setFillColor(TEXT)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(width / 2, back_y - 8 * mm, "Présentez le QR au professionnel de santé")
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 8)
    c.drawCentredString(
        width / 2,
        back_y - 13 * mm,
        "Aucune donnée médicale dans le QR - token d'accès opaque uniquement.",
    )

    assurance = getattr(patient, "assurance", None)
    insured = bool(assurance and getattr(assurance, "assureur", None))
    badge = "Assuré" if insured else "Non assuré"
    c.setFillColor(INSURANCE if insured else MUTED)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(width / 2, 15 * mm, f"Statut couverture : {badge}")

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7)
    c.drawCentredString(
        width / 2,
        9 * mm,
        "DOTO+ · Document généré automatiquement - ne pas plastifier le QR trop près des bords.",
    )
    c.showPage()
    c.save()
    return buffer.getvalue()
