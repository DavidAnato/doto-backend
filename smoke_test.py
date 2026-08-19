"""Test de fumée étendu - auth sans OTP pro, OTP inscription/reset, PIN, DotoCard, RBAC."""
import io
import json
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import Client
from django.utils import timezone
from datetime import timedelta

c = Client()


def show(label, resp):
    try:
        body = resp.json()
    except Exception:
        body = resp.content[:120]
    snippet = json.dumps(body, ensure_ascii=False)[:240] if isinstance(body, (dict, list)) else body
    print(f"{label}: {resp.status_code}", snippet)
    assert resp.status_code < 400 or resp.status_code in (401, 403, 404, 410, 423), label
    return resp


def login_pro(username: str, password: str):
    r = c.post(
        "/api/auth/login/",
        data=json.dumps({"username": username, "password": password}),
        content_type="application/json",
    )
    assert r.status_code == 200, (username, r.content)
    assert "otp_required" not in r.json()
    return {"HTTP_AUTHORIZATION": f"Bearer {r.json()['access']}"}


show("health", c.get("/api/health/"))

# Login pro SANS OTP
H = login_pro("medecin", "Medecin123!")
print("login medecin (sans OTP): 200 OK")

# OTP pour login / inscription patient - numéro unique à chaque run
import time

demo_phone = f"+229 90 {int(time.time()) % 10000000:07d}"
r = show(
    "request otp register",
    c.post(
        "/api/auth/otp/",
        data=json.dumps({"phone": demo_phone, "purpose": "register"}),
        content_type="application/json",
    ),
)
assert r.json().get("sent")

reg = show(
    "patient register (otp only)",
    c.post(
        "/api/auth/patient/register/",
        data=json.dumps(
            {
                "phone": demo_phone,
                "otp": "00000",
                "first_name": "Test",
                "last_name": "Inscription",
            }
        ),
        content_type="application/json",
    ),
)
assert reg.status_code == 201

# Login OTP patient (nouveau compte)
show(
    "request otp login new",
    c.post(
        "/api/auth/otp/",
        data=json.dumps({"phone": demo_phone, "purpose": "login"}),
        content_type="application/json",
    ),
)
show(
    "patient login otp new",
    c.post(
        "/api/auth/patient/login/",
        data=json.dumps({"phone": demo_phone, "otp": "00000"}),
        content_type="application/json",
    ),
)

pid = c.get("/api/patients/search/?npi=1200478821", **H).json()[0]["id"]
show("verify anip", c.post(f"/api/patients/{pid}/verify_anip/", **H))

# Patient login démo via OTP
show(
    "request otp login demo",
    c.post(
        "/api/auth/otp/",
        data=json.dumps({"phone": "+229 97 45 12 88", "purpose": "login"}),
        content_type="application/json",
    ),
)
rp = show(
    "patient login otp",
    c.post(
        "/api/auth/patient/login/",
        data=json.dumps({"phone": "+229 97 45 12 88", "otp": "00000"}),
        content_type="application/json",
    ),
)
assert rp.status_code == 200
pH = {"HTTP_AUTHORIZATION": f"Bearer {rp.json()['access']}"}
assert rp.json().get("patient", {}).get("has_pin") is True
mine = show("mine dodocard", c.get("/api/dodocards/mine/", **pH)).json()
token = mine["token_chiffre"]

# Tokens seed legacy : réémettre si déchiffrement invalide (dev)
from cards import services
from cards.models import DodoCard
from patients.models import Patient

if not services.is_valid_token(token):
    patient = Patient.objects.get(npi="1200478821")
    DodoCard.objects.filter(patient=patient, statut=DodoCard.Statut.ACTIVE).update(
        statut=DodoCard.Statut.REVOQUEE
    )
    token = DodoCard.issue(patient).token_chiffre

# DotoCard scan (pro) -> consentement pending + SSE dodocard_scan cible user_id
scan = show(
    "scan",
    c.post("/api/dodocards/scan/", data=json.dumps({"token": token}), content_type="application/json", **H),
)
scan_body = scan.json()
assert "patient_id" in scan_body
assert "consent_required" in scan_body
assert "access_request" in scan_body
if scan_body.get("consent_required"):
    arid0 = scan_body["access_request"]["id"]
    show("patient approve initial scan", c.post(f"/api/access-requests/{arid0}/approve/", **pH))

# SSE hub - auth requise (ne pas ouvrir le stream infini dans le smoke)
assert c.get("/api/hub/events/").status_code == 401
assert c.get("/api/hub/events/?access=invalid").status_code == 401
print("hub SSE auth gates: OK")

# Suite patient - PIN 4 chiffres (déverrouillage secondaire + verify)
show(
    "pin unlock (secondary)",
    c.post(
        "/api/auth/patient/pin/",
        data=json.dumps({"npi": "1200478821", "pin": "1234"}),
        content_type="application/json",
    ),
)
show(
    "verify pin authenticated",
    c.post(
        "/api/auth/verify-pin/",
        data=json.dumps({"pin": "1234"}),
        content_type="application/json",
        **pH,
    ),
)
# Pro PIN verify
show(
    "pro verify pin",
    c.post(
        "/api/auth/verify-pin/",
        data=json.dumps({"pin": "1234"}),
        content_type="application/json",
        **H,
    ),
)
# Flags sécurité patient
show(
    "patch require_unlock",
    c.patch(
        "/api/auth/me/",
        data=json.dumps({"require_unlock": True, "urgence_when_locked": True}),
        content_type="application/json",
        **pH,
    ),
)
show("patient examens", c.get("/api/examens/", **pH))

# Upload PDF laborantin (après consentement)
lH = login_pro("laborantin", "Labo123!")
req_lab = show(
    "laborantin request access",
    c.post(
        "/api/access-requests/create/",
        data=json.dumps({"patient_id": pid, "mode": "search"}),
        content_type="application/json",
        **lH,
    ),
).json()
if req_lab.get("consent_required") or req_lab.get("status") == "pending":
    show(
        "patient approve laborantin",
        c.post(f"/api/access-requests/{req_lab['id']}/approve/", **pH),
    )
exams = c.get(f"/api/examens/?patient={pid}", **lH).json()
eid = (exams["results"] if "results" in exams else exams)[0]["id"]
pdf = io.BytesIO(b"%PDF-1.4 demo dotoplus+")
pdf.name = "resultat.pdf"
show(
    "upload pdf",
    c.post(f"/api/examens/{eid}/upload/", data={"fichier": pdf}, **lH),
)

# RBAC - ambulancier : urgence + constantes OK, historique / ordo refusés
aH = login_pro("ambulancier", "Ambulancier123!")
dossier_a = show("ambulancier dossier", c.get(f"/api/patients/{pid}/", **aH)).json()
assert "urgence" in dossier_a
assert "access" in dossier_a
assert "constantes" in dossier_a["access"]["sections"]
assert "historique" not in dossier_a["access"]["sections"]
show("ambulancier constantes", c.get(f"/api/constantes/?patient={pid}", **aH))
r_histo = c.get(f"/api/consultations/?patient={pid}", **aH)
assert r_histo.status_code == 403, r_histo.content
print("ambulancier RBAC: OK")

# RBAC - pharmacien : consentement puis ordo OK, consultations refusées
phH = login_pro("pharmacien", "Pharma123!")
req_ph = show(
    "pharmacien request access",
    c.post(
        "/api/access-requests/create/",
        data=json.dumps({"patient_id": pid, "mode": "search"}),
        content_type="application/json",
        **phH,
    ),
).json()
if req_ph.get("consent_required"):
    show(
        "patient approve pharmacien",
        c.post(f"/api/access-requests/{req_ph['id']}/approve/", **pH),
    )
dossier_p = show("pharmacien dossier", c.get(f"/api/patients/{pid}/", **phH)).json()
assert "ordonnances" in dossier_p["access"]["sections"]
assert c.get(f"/api/consultations/?patient={pid}", **phH).status_code == 403
show("pharmacien ordo", c.get(f"/api/ordonnances/?patient={pid}", **phH))
print("pharmacien RBAC: OK")

# RBAC - réceptionniste : assurance, pas d'historique
rH = login_pro("reception", "Reception123!")
req_r = show(
    "reception request access",
    c.post(
        "/api/access-requests/create/",
        data=json.dumps({"patient_id": pid, "mode": "search"}),
        content_type="application/json",
        **rH,
    ),
).json()
if req_r.get("consent_required"):
    show(
        "patient approve reception",
        c.post(f"/api/access-requests/{req_r['id']}/approve/", **pH),
    )
dossier_r = show("reception dossier", c.get(f"/api/patients/{pid}/", **rH)).json()
assert "assurance" in dossier_r["access"]["sections"]
assert c.get(f"/api/consultations/?patient={pid}", **rH).status_code == 403
print("receptionniste RBAC: OK")

# Scan ambulancier (SSE publish + bypass urgence)
scan_a = show(
    "scan ambulancier",
    c.post("/api/dodocards/scan/", data=json.dumps({"token": token}), content_type="application/json", **aH),
)
assert scan_a.json().get("patient_id")
assert scan_a.json().get("emergency") is True or scan_a.json().get("consent_required") is False
print("ambulancier urgency bypass: OK")

# Consentement - médecin scan → pending ; patient approuve
from patients.models import AccessRequest as AR

H2 = login_pro("medecin3", "Medecin123!")
# Isoler le flux : expirer d'éventuels grants restants
AR.objects.filter(requester__username="medecin3", patient_id=pid).update(
    status=AR.Status.EXPIRED, responded_at=timezone.now()
)
scan_m = show(
    "scan medecin (consent)",
    c.post(
        "/api/dodocards/scan/",
        data=json.dumps({"token": token}),
        content_type="application/json",
        **H2,
    ),
)
body_m = scan_m.json()
assert body_m.get("consent_required") is True, body_m
arid = body_m["access_request"]["id"]
# Sans grant : dossier 202
r202 = c.get(f"/api/patients/{pid}/", **H2)
assert r202.status_code == 202, r202.status_code
assert r202.json().get("consent", {}).get("required") is True

# Patient voit la demande et approuve
pending = show("access pending patient", c.get("/api/access-requests/?pending=1", **pH)).json()
assert any(r["id"] == arid for r in pending), pending
show("patient approve", c.post(f"/api/access-requests/{arid}/approve/", **pH))
dossier_ok = show("medecin3 dossier after approve", c.get(f"/api/patients/{pid}/", **H2))
assert dossier_ok.status_code == 200
assert dossier_ok.json().get("consent", {}).get("granted") is True
print("access consent flow: OK")
# Notifications patient
notifs = show("patient notifications", c.get("/api/notifications/", **pH)).json()
assert isinstance(notifs.get("results", notifs), list) or isinstance(notifs, list)
show("unread count", c.get("/api/notifications/unread_count/", **pH))
assert c.get("/api/patient/events/").status_code == 401
print("notifications + patient SSE gate: OK")

# Admin dashboard (sans OTP)
ra = login_pro("admin", "AdminDoto2026!")
show("dashboard", c.get("/api/admin/dashboard/", **ra))

# --- DotoCard PDF + perte + réémission ---
pdf_r = c.get("/api/dodocards/mine/pdf/", **pH)
assert pdf_r.status_code == 200, pdf_r.content[:200]
assert pdf_r["Content-Type"] == "application/pdf"
assert pdf_r.content[:4] == b"%PDF"
print("dodocard PDF: OK", len(pdf_r.content), "bytes")

old_token = token
loss = show(
    "report loss + auto reissue",
    c.post(
        "/api/dodocards/mine/report-loss/",
        data=json.dumps({"motif": "perte"}),
        content_type="application/json",
        **pH,
    ),
)
loss_body = loss.json()
assert loss.status_code == 201
assert "card" in loss_body
new_token = loss_body["card"]["token_chiffre"]
assert new_token != old_token
assert loss_body.get("reissue_ms", 99999) < 60_000
# Ancien token refusé
gone = c.post(
    "/api/dodocards/scan/",
    data=json.dumps({"token": old_token}),
    content_type="application/json",
    **H,
)
assert gone.status_code in (404, 410), gone.status_code
# Nouveau token scannable
scan_new = show(
    "scan new token",
    c.post(
        "/api/dodocards/scan/",
        data=json.dumps({"token": new_token}),
        content_type="application/json",
        **H,
    ),
)
assert scan_new.json().get("patient_id")
print("dodocard lose+reissue+scan: OK")

reiss = show(
    "patient request reissue",
    c.post(
        "/api/dodocards/mine/reissue/",
        data=json.dumps({"motif": "demande_remplacement"}),
        content_type="application/json",
        **pH,
    ),
)
assert reiss.status_code == 201
assert reiss.json()["card"]["token_chiffre"] != new_token
print("dodocard patient reissue: OK")

# --- Profil patient + historique + assurance ---
prof = show(
    "patient profile patch",
    c.patch(
        "/api/patients/me/",
        data=json.dumps(
            {
                "groupe_sanguin": "A+",
                "tel_urgence": "+229 97 45 12 88",
                "contact_urgence_nom": "Marie Adjovi",
                "allergies": ["Pénicilline"],
            }
        ),
        content_type="application/json",
        **pH,
    ),
)
assert prof.status_code == 200
hist = show("patient historique", c.get("/api/patients/me/historique/", **pH)).json()
assert "consultations" in hist and "ordonnances" in hist and "examens" in hist
ass = show("patient assurance", c.get("/api/patients/me/assurance/", **pH))
assert ass.status_code == 200
print("patient profile + history + assurance: OK")

# --- RDV ---
# Patient : lecture seule, création interdite
deny_p = show(
    "patient create rdv denied",
    c.post(
        "/api/appointments/",
        data=json.dumps(
            {
                "debut": (timezone.now() + timedelta(days=5)).isoformat(),
                "motif": "Smoke test RDV patient",
            }
        ),
        content_type="application/json",
        **pH,
    ),
)
assert deny_p.status_code == 403
list_p = show("patient list rdv", c.get("/api/appointments/", **pH))
assert list_p.status_code == 200

# Réception : création interdite (médecin only)
rH2 = login_pro("reception", "Reception123!")
recv_deny = show(
    "reception create rdv denied",
    c.post(
        "/api/appointments/",
        data=json.dumps(
            {
                "patient": pid,
                "debut": (timezone.now() + timedelta(days=7)).isoformat(),
                "motif": "RDV réception",
            }
        ),
        content_type="application/json",
        **rH2,
    ),
)
assert recv_deny.status_code == 403

# Médecin : création OK
med_rdv = show(
    "medecin create rdv",
    c.post(
        "/api/appointments/",
        data=json.dumps(
            {
                "patient": pid,
                "debut": (timezone.now() + timedelta(days=5)).isoformat(),
                "motif": "Smoke test RDV médecin",
            }
        ),
        content_type="application/json",
        **H,
    ),
)
assert med_rdv.status_code == 201
rdv_id = med_rdv.json()["id"]

# Second RDV pour test patch réception / statut
med_rdv2 = show(
    "medecin create rdv 2",
    c.post(
        "/api/appointments/",
        data=json.dumps(
            {
                "patient": pid,
                "debut": (timezone.now() + timedelta(days=9)).isoformat(),
                "motif": "RDV statut",
            }
        ),
        content_type="application/json",
        **H,
    ),
)
assert med_rdv2.status_code == 201
recv_id = med_rdv2.json()["id"]

filt = show(
    "reception list rdv filter statut",
    c.get("/api/appointments/?statut=planifie", **rH2),
)
assert filt.status_code == 200
# Réception ne peut plus modifier (write = médecin / admin)
done = show(
    "reception mark terminé denied",
    c.patch(
        f"/api/appointments/{recv_id}/",
        data=json.dumps({"statut": "termine"}),
        content_type="application/json",
        **rH2,
    ),
)
assert done.status_code == 403
done_ok = show(
    "medecin mark terminé",
    c.patch(
        f"/api/appointments/{recv_id}/",
        data=json.dumps({"statut": "termine"}),
        content_type="application/json",
        **H,
    ),
)
assert done_ok.status_code == 200 and done_ok.json()["statut"] == "termine"
infH = login_pro("infirmier", "Infirmier123!")
inf_list = show("infirmier list rdv (lecture)", c.get("/api/appointments/", **infH))
assert inf_list.status_code == 200
inf_deny = show(
    "infirmier create rdv denied",
    c.post(
        "/api/appointments/",
        data=json.dumps(
            {
                "patient": pid,
                "debut": (timezone.now() + timedelta(days=8)).isoformat(),
                "motif": "interdit",
            }
        ),
        content_type="application/json",
        **infH,
    ),
)
assert inf_deny.status_code == 403
show(
    "patient cancel rdv",
    c.patch(
        f"/api/appointments/{rdv_id}/",
        data=json.dumps({"statut": "annule"}),
        content_type="application/json",
        **pH,
    ),
)
print("appointments: OK")

# --- Blocage permanent ---
# medecin2 : créer demande puis patient bloque
H_block = login_pro("medecin2", "Medecin123!")
AR.objects.filter(requester__username="medecin2", patient_id=pid).update(
    status=AR.Status.EXPIRED, responded_at=timezone.now()
)
req_b = show(
    "medecin2 request before block",
    c.post(
        "/api/access-requests/create/",
        data=json.dumps({"patient_id": pid, "mode": "search"}),
        content_type="application/json",
        **H_block,
    ),
).json()
if req_b.get("status") == "pending":
    show("approve before block", c.post(f"/api/access-requests/{req_b['id']}/approve/", **pH))
# récupérer requester id
from accounts.models import User as U

med2 = U.objects.get(username="medecin2")
blk = show(
    "patient create block",
    c.post(
        "/api/access-blocks/",
        data=json.dumps(
            {"blocked_user_id": med2.id, "reason": "Smoke test blacklist"}
        ),
        content_type="application/json",
        **pH,
    ),
)
assert blk.status_code == 201
# Nouvelle demande doit échouer
blocked = c.post(
    "/api/access-requests/create/",
    data=json.dumps({"patient_id": pid, "mode": "search"}),
    content_type="application/json",
    **H_block,
)
assert blocked.status_code == 403, blocked.content
print("access block: OK")
# Lever le blocage pour ne pas casser les runs suivants
show(
    "lift block",
    c.post(f"/api/access-blocks/{blk.json()['id']}/lift/", **pH),
)

# Admin force revoke
ra = login_pro("admin", "AdminDoto2026!")
# recréer un grant puis force revoke
req_fr = show(
    "medecin2 access for force revoke",
    c.post(
        "/api/access-requests/create/",
        data=json.dumps({"patient_id": pid, "mode": "search"}),
        content_type="application/json",
        **H_block,
    ),
).json()
if req_fr.get("status") == "pending":
    show("approve for force revoke", c.post(f"/api/access-requests/{req_fr['id']}/approve/", **pH))
show(
    "admin force revoke",
    c.post(f"/api/access-requests/{req_fr['id']}/force-revoke/", **ra),
)
print("admin force revoke: OK")

# Role write gates - infirmier peut constantes, pas ordo create
iH = login_pro("infirmier", "Infirmier123!")
req_i = c.post(
    "/api/access-requests/create/",
    data=json.dumps({"patient_id": pid, "mode": "search"}),
    content_type="application/json",
    **iH,
).json()
if req_i.get("status") == "pending" or req_i.get("consent_required"):
    c.post(f"/api/access-requests/{req_i['id']}/approve/", **pH)
show(
    "infirmier create constante",
    c.post(
        "/api/constantes/",
        data=json.dumps(
            {"patient": pid, "tension_systolique": 118, "tension_diastolique": 76}
        ),
        content_type="application/json",
        **iH,
    ),
)
ordo_forbid = c.post(
    "/api/ordonnances/",
    data=json.dumps(
        {
            "patient": pid,
            "date": timezone.now().date().isoformat(),
            "medicaments": [{"nom": "X", "dosage": "1"}],
        }
    ),
    content_type="application/json",
    **iH,
)
assert ordo_forbid.status_code == 403, ordo_forbid.content
print("role write gates: OK")

print("\nOK smoke test étendu terminé.")
