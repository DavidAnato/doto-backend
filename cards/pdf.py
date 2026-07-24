"""Génération PDF imprimable DodoCard — faces Assuré / Non assuré (marque DOTO+)."""
from __future__ import annotations

import io
from pathlib import Path

import qrcode
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from accounts.phone import display_phone

NAVY = HexColor("#1E3755")
TEAL = HexColor("#3E8295")
MUTED = HexColor("#6B7280")
BORDER = HexColor("#E5E7EB")
TEXT = HexColor("#1F2937")
EMERGENCY_BG = HexColor("#FCE8E8")
EMERGENCY = HexColor("#8B1E1E")  # contraste renforcé vs rouge clair
INSURANCE_BG = HexColor("#E8F5EE")
INSURANCE = HexColor("#0F5C45")
SOFT_PLUS = HexColor("#EEF1F4")


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


def _fmt_date(d) -> str:
    if not d:
        return "—"
    try:
        return d.strftime("%d/%m/%Y")
    except Exception:
        return str(d)


def _allergies_label(patient) -> str:
    dossier = getattr(patient, "dossier", None)
    allergies = getattr(dossier, "allergies", None) or []
    if isinstance(allergies, list) and allergies:
        return ", ".join(str(a) for a in allergies if a) or "Non identifié"
    return "Non identifié"


def _taux_pills(assurance) -> list[str]:
    garanties = getattr(assurance, "garanties", None) or []
    if not garanties:
        return ["Consult. —", "Soins. —", "Pharma. —"]
    labels = []
    for g in garanties[:3]:
        if not isinstance(g, dict):
            continue
        cat = str(g.get("categorie") or "Garantie")
        short = cat.split()[0][:8] + "."
        taux = g.get("taux", "—")
        labels.append(f"{short} {taux}%")
    while len(labels) < 3:
        labels.append("—")
    return labels[:3]


def _draw_rounded(c: canvas.Canvas, x, y, w, h, r, fill=None, stroke=None):
    c.saveState()
    if fill is not None:
        c.setFillColor(fill)
    if stroke is not None:
        c.setStrokeColor(stroke)
    c.roundRect(x, y, w, h, r, fill=1 if fill is not None else 0, stroke=1 if stroke is not None else 0)
    c.restoreState()


def _draw_card_front(c: canvas.Canvas, card, x, y, w, h, qr_reader):
    patient = card.patient
    # Corps blanc
    _draw_rounded(c, x, y, w, h, 3 * mm, fill=white, stroke=BORDER)

    # En-tête navy
    header_h = 9 * mm
    c.setFillColor(NAVY)
    c.rect(x, y + h - header_h, w, header_h, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(x + 3 * mm, y + h - 5.8 * mm, "Carte d'accès santé")
    c.setFont("Helvetica", 7)
    c.drawRightString(x + w - 3 * mm, y + h - 5.8 * mm, "République du Bénin")

    # Bande urgence (bas) — contraste texte renforcé
    foot_h = 12 * mm
    c.setFillColor(EMERGENCY_BG)
    c.rect(x, y, w, foot_h, fill=1, stroke=0)
    c.setStrokeColor(HexColor("#F0C4C4"))
    c.line(x, y + foot_h, x + w, y + foot_h)

    blood = getattr(patient, "groupe_sanguin", "") or "—"
    allergies = _allergies_label(patient)
    urg = display_phone(getattr(patient, "tel_urgence", "") or "")

    col_w = w / 3
    c.setFillColor(EMERGENCY)
    c.setFont("Helvetica", 5.5)
    c.drawString(x + 2.5 * mm, y + foot_h - 3.2 * mm, "Groupe sanguin")
    c.setFont("Helvetica-Bold", 9)
    c.drawString(x + 2.5 * mm, y + foot_h - 7.5 * mm, blood[:8])

    c.setFont("Helvetica", 5.5)
    c.drawString(x + col_w + 1 * mm, y + foot_h - 3.2 * mm, "Allergies connues")
    c.setFont("Helvetica-Bold", 6.5)
    # Wrap allergies
    allerg_y = y + foot_h - 6.5 * mm
    for i, chunk in enumerate(_wrap(allergies, 22)[:2]):
        c.drawString(x + col_w + 1 * mm, allerg_y - i * 2.8 * mm, chunk)

    c.setFont("Helvetica", 5.5)
    c.drawString(x + 2 * col_w + 1 * mm, y + foot_h - 3.2 * mm, "Numéro en cas d'urgence")
    c.setFont("Helvetica-Bold", 7)
    c.drawString(x + 2 * col_w + 1 * mm, y + foot_h - 7.5 * mm, urg)

    # Corps identité
    body_top = y + h - header_h - 2 * mm
    body_bottom = y + foot_h + 2 * mm

    # Marque + n° carte
    c.setFillColor(TEAL)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x + 3 * mm, body_top - 4 * mm, "DotoCard")
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 6.5)
    c.drawString(x + 3 * mm, body_top - 7.5 * mm, f"Carte N°: {card.id}")

    # Photo
    photo_size = 18 * mm
    photo_x = x + 3 * mm
    photo_y = body_bottom + 4 * mm
    photo = getattr(patient, "photo", None)
    c.setStrokeColor(BORDER)
    c.setFillColor(SOFT_PLUS)
    c.rect(photo_x, photo_y, photo_size, photo_size, fill=1, stroke=1)
    if photo and getattr(photo, "path", None):
        try:
            p = Path(photo.path)
            if p.is_file():
                c.drawImage(
                    str(p),
                    photo_x,
                    photo_y,
                    width=photo_size,
                    height=photo_size,
                    preserveAspectRatio=True,
                    mask="auto",
                )
        except Exception:
            pass

    # Champs identité
    info_x = photo_x + photo_size + 3 * mm
    fields = [
        ("Nom", (patient.nom or "").upper()),
        ("Prénoms", patient.prenom or "—"),
        ("Date de naissance", _fmt_date(patient.date_naissance)),
        ("Lieu de naissance", getattr(patient, "lieu_naissance", "") or "—"),
    ]
    fy = body_top - 12 * mm
    for label, val in fields:
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 5.5)
        c.drawString(info_x, fy + 3.2 * mm, label)
        c.setFillColor(TEXT)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(info_x, fy, (val or "—")[:28])
        fy -= 6.5 * mm

    # NPI + QR + tél — QR agrandi
    right_x = x + w - 32 * mm
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 5.5)
    c.drawString(right_x, body_top - 3 * mm, "NPI")
    c.setFillColor(TEXT)
    c.setFont("Helvetica-Bold", 7)
    c.drawString(right_x, body_top - 6 * mm, (patient.npi or "—")[:18])

    qr_size = 26 * mm
    c.drawImage(qr_reader, right_x, body_top - 7 * mm - qr_size, width=qr_size, height=qr_size, mask="auto")

    tel = display_phone(getattr(patient, "telephone", "") or "")
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 5.5)
    c.drawString(right_x, body_bottom + 6 * mm, "Num. tél")
    c.setFillColor(TEXT)
    c.setFont("Helvetica-Bold", 6.5)
    c.drawString(right_x, body_bottom + 3 * mm, tel)


def _wrap(text: str, width: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return ["—"]
    lines, cur = [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if len(trial) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or ["—"]


def _draw_card_back(c: canvas.Canvas, card, x, y, w, h):
    patient = card.patient
    assurance = getattr(patient, "assurance", None)
    is_insured = bool(assurance and getattr(assurance, "assureur", None) and getattr(assurance, "droits_valides", True))

    _draw_rounded(c, x, y, w, h, 3 * mm, fill=white, stroke=BORDER)

    header_h = 9 * mm
    c.setFillColor(NAVY)
    c.rect(x, y + h - header_h, w, header_h, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(x + 3 * mm, y + h - 5.8 * mm, "Informations complémentaires")

    # Zone filiation / adresse
    mid_y = y + h - header_h - 2 * mm
    pere = getattr(patient, "nom_pere", "") or "—"
    mere = getattr(patient, "nom_mere", "") or "—"
    commune = getattr(patient, "adresse_commune", "") or "—"
    quartier = getattr(patient, "adresse_quartier", "") or "—"
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 6)
    c.drawString(x + 3 * mm, mid_y - 4 * mm, "Filiation")
    c.drawString(x + w / 2, mid_y - 4 * mm, "Adresse de résidence")
    c.setFillColor(TEXT)
    c.setFont("Helvetica", 7)
    c.drawString(x + 3 * mm, mid_y - 8 * mm, f"Père: {pere}"[:42])
    c.drawString(x + 3 * mm, mid_y - 12 * mm, f"Mère: {mere}"[:42])
    c.drawString(x + w / 2, mid_y - 8 * mm, f"Com. {commune}"[:36])
    c.drawString(x + w / 2, mid_y - 12 * mm, f"Qtr: {quartier}"[:36])

    # Bloc couverture
    block_h = 22 * mm
    block_y = y + 10 * mm
    if is_insured:
        c.setFillColor(INSURANCE_BG)
        c.rect(x, block_y, w, block_h, fill=1, stroke=0)
        c.setFillColor(INSURANCE)
        c.setFont("Helvetica-Bold", 7)
        c.drawString(x + 3 * mm, block_y + block_h - 4.5 * mm, "COUVERTURE ASSURANTIELLE")
        c.setFont("Helvetica", 6)
        c.drawRightString(x + w - 3 * mm, block_y + block_h - 4.5 * mm, "Tél: +229 21 30 31 30")

        c.setFont("Helvetica-Bold", 8)
        c.drawString(x + 3 * mm, block_y + block_h - 9 * mm, (assurance.assureur or "")[:36])
        c.setFont("Helvetica", 6)
        c.setFillColor(TEXT)
        line = f"Police: {assurance.num_police or '—'}  ·  {assurance.type_couverture or '—'}"
        c.drawString(x + 3 * mm, block_y + block_h - 13 * mm, line[:55])

        # Pills taux (info, pas boutons)
        pills = _taux_pills(assurance)
        px = x + 3 * mm
        for label in pills:
            pw = 24 * mm
            c.setFillColor(white)
            c.roundRect(px, block_y + 2.5 * mm, pw, 5.5 * mm, 2 * mm, fill=1, stroke=0)
            c.setFillColor(INSURANCE)
            c.setFont("Helvetica-Bold", 6)
            c.drawCentredString(px + pw / 2, block_y + 4.2 * mm, label)
            px += pw + 2 * mm
    else:
        c.setFillColor(HexColor("#F3F4F6"))
        c.rect(x, block_y, w, block_h, fill=1, stroke=0)
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 7)
        c.drawString(x + 3 * mm, block_y + block_h - 5 * mm, "COUVERTURE ASSURANTIELLE")
        c.setFillColor(TEXT)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(x + 3 * mm, block_y + block_h - 11 * mm, "Non Assuré")
        c.setFont("Helvetica", 6.5)
        c.setFillColor(MUTED)
        c.drawString(x + 3 * mm, block_y + block_h - 15.5 * mm, "Soins à paiement direct auprès des structures partenaires")
        c.drawString(x + 3 * mm, block_y + block_h - 19 * mm, "Urgence vitale : dispositif national de paiement différé")

    # Mentions
    c.setFillColor(EMERGENCY)
    c.setFont("Helvetica", 5)
    c.drawCentredString(
        x + w / 2,
        y + 6.5 * mm,
        "Cette carte est strictement personnelle, non-transférable et demeure la propriété de l'émetteur.*",
    )
    c.setFillColor(TEXT)
    c.setFont("Helvetica-Bold", 6.5)
    exp = _fmt_date(card.date_expiration)
    c.drawCentredString(x + w / 2, y + 2.8 * mm, f"Valable jusqu'au : {exp}")


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
    c.rect(0, height - 28 * mm, width, 28 * mm, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(18 * mm, height - 14 * mm, "DOTO+")
    c.setFont("Helvetica", 10)
    c.drawString(18 * mm, height - 21 * mm, "DodoCard — carte d'accès santé · République du Bénin")

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

    # QR haute rés
    qr_img = qrcode.make(card.token_chiffre, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr_buf = io.BytesIO()
    qr_img.save(qr_buf, format="PNG")
    qr_buf.seek(0)
    qr_reader = ImageReader(qr_buf)

    card_w, card_h = 86 * mm, 54 * mm
    card_x = (width - card_w) / 2

    # Face avant
    front_y = height - 95 * mm
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(card_x, front_y + card_h + 3 * mm, "Recto")
    _draw_card_front(c, card, card_x, front_y, card_w, card_h, qr_reader)

    # Face arrière
    back_y = front_y - card_h - 14 * mm
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(card_x, back_y + card_h + 3 * mm, "Verso")
    _draw_card_back(c, card, card_x, back_y, card_w, card_h)

    # Rappel scan
    c.setFillColor(TEXT)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(width / 2, back_y - 10 * mm, "Présentez le QR au professionnel de santé")
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 8)
    c.drawCentredString(
        width / 2,
        back_y - 15 * mm,
        "Aucune donnée médicale dans le QR — token d'accès opaque uniquement.",
    )

    # Badge statut
    assurance = getattr(patient, "assurance", None)
    insured = bool(assurance and getattr(assurance, "assureur", None))
    badge = "Assuré" if insured else "Non assuré"
    c.setFillColor(INSURANCE if insured else MUTED)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(width / 2, 16 * mm, f"Statut couverture : {badge}")

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7)
    c.drawCentredString(
        width / 2,
        10 * mm,
        "DOTO+ · Document généré automatiquement — ne pas plastifier le QR trop près des bords.",
    )
    c.showPage()
    c.save()
    return buffer.getvalue()
