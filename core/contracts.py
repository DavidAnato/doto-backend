"""Contrats API figés — source de vérité partagée (PIN / OTP / notifications / conflits).

Les clients (DotoHub web, DotoHub mobile, DotoPlus, Admin) DOIVENT s'aligner
sur ces constantes. Ne pas diverger (longueur PIN/OTP, types de notif, payloads).

Conflits hors-ligne
-------------------
Stratégie : last-write-wins horodaté.
- Chaque ressource mutable expose `updated_at` (ISO-8601).
- En PATCH, le client PEUT envoyer `client_updated_at` (copie de `updated_at` lu).
- Si `client_updated_at` < `updated_at` serveur → HTTP 409 + objet courant.
- Si le champ est absent → last-write (la requête gagne).
- La file hors-ligne rejoue dans l'ordre FIFO ; un 409 écarte l'action locale.
"""

PIN_LENGTH = 4
PIN_REGEX = r"^\d{4}$"
PIN_ERROR = "Le PIN doit contenir exactement 4 chiffres."

OTP_LENGTH = 5
OTP_REGEX = r"^\d{5}$"
OTP_ERROR = "Le code OTP doit contenir exactement 5 chiffres."
DEMO_OTP_CODE = "00000"

# Session longue : PIN / biométrie au réveil, pas identifiant+mdp.
ACCESS_TOKEN_MINUTES_DEFAULT = 720  # 12 h
REFRESH_TOKEN_DAYS_DEFAULT = 30

SPECIALITES = [
    "Médecine générale",
    "ORL",
    "Gynécologie-obstétrique",
    "Cardiologie",
    "Pédiatrie",
    "Dermatologie",
    "Ophtalmologie",
    "Chirurgie générale",
    "Traumatologie-orthopédie",
    "Neurologie",
    "Psychiatrie",
    "Urologie",
    "Gastro-entérologie",
    "Pneumologie",
    "Rhumatologie",
    "Endocrinologie",
    "Néphrologie",
    "Oncologie",
    "Anesthésie-réanimation",
    "Médecine interne",
    "Médecine d'urgence",
    "Radiologie",
    "Biologie médicale",
    "Santé publique",
    "Stomatologie",
    "Néonatologie",
]

PRISE_EN_CHARGE = [
    ("consultation", "Consultation"),
    ("hospitalisation", "Hospitalisation"),
    ("urgence", "Urgence"),
    ("suivi", "Suivi/Contrôle"),
    ("chirurgie", "Chirurgie"),  # rétrocompat
]

MEDICAMENT_FORMES = [
    "comprimé",
    "gélule",
    "sirop",
    "sachet",
    "ampoule",
    "flacon",
    "gouttes",
    "pommade",
    "crème",
    "suppositoire",
    "inhalateur",
    "patch",
    "injectable",
    "autre",
]

MEDICAMENT_MOMENTS = [
    ("a_jeun", "À jeun"),
    ("avant_repas", "Avant les repas"),
    ("pendant_repas", "Pendant les repas"),
    ("apres_repas", "Après les repas"),
    ("entre_repas", "Entre les repas"),
    ("au_coucher", "Au coucher"),
]

BON_EXAMEN_STATUTS = [
    ("demande", "Demandé"),
    ("recu", "Reçu"),
    ("en_cours", "En cours"),
    ("resultat_disponible", "Résultat disponible"),
    ("cloture", "Clôturé"),
]

# Rôles pro qui doivent rattacher au moins un hôpital + un principal.
HOSPITAL_REQUIRED_ROLES = (
    "medecin",
    "infirmier",
    "pharmacien",
    "laborantin",
    "ambulancier",
    "receptionniste",
)
# Payloads notification : toujours JSON plat + ids.
# type (Notification.Type) + payload.kind déterminent le deep-link client.
NOTIFICATION_ROUTES = {
    "access_request": {"screen": "consent", "ids": ["access_request_id", "patient_id"]},
    "access_granted": {"screen": "patient", "ids": ["patient_id"]},
    "access_denied": {"screen": "notifications", "ids": ["patient_id"]},
    "access_expired": {"screen": "notifications", "ids": ["patient_id"]},
    "dossier_updated": {"screen": "patient_historique", "ids": ["patient_id", "consultation_id"]},
    "ordonnance": {"screen": "patient_ordonnances", "ids": ["patient_id", "ordonnance_id"]},
    "examen": {"screen": "patient_examens", "ids": ["patient_id", "examen_id", "bon_id"]},
    "appointment": {"screen": "rdv", "ids": ["appointment_id", "patient_id"]},
    "bon_examen": {"screen": "patient_examens", "ids": ["bon_id", "patient_id"]},
    "emergency": {"screen": "urgence", "ids": ["patient_id"]},
    "system": {"screen": "notifications", "ids": []},
}

# Types d'événements SSE (même bus `hub_bus`, canal = user_id).
# Pas de WebSocket : EventSource `?access=<jwt>` sur /api/hub/events/ (pro)
# et /api/patient/events/ ou /api/events/ (patient / admin).
SSE_EVENT_TYPES = {
    "connected": "Hello à l'ouverture du flux",
    "ping": "Keepalive (~15 s)",
    "notification": "Notification in-app (title/body/payload)",
    "patient_list": "Patient créé/modifié — rafraîchir listes / dashboard",
    "appointment": (
        "RDV créé/modifié/annulé/confirmé — payload : patient_id, appointment_id, "
        "debut, fin, statut, professionnel_id, professionnel_nom, structure_id, kind"
    ),
    "insurance_updated": (
        "Assurance ajoutée/modifiée/retirée — payload : patient_id, has_insurance, "
        "assureur, num_police, droits_valides, kind (created|updated|removed)"
    ),
    "dossier_updated": "Dossier clinique (consultation, constantes…)",
    "ordonnance": "Ordonnance créée / dispensée",
    "examen": "Examen / bon d'examen",
    "access_request": "Demande d'accès dossier",
    "access_granted": "Consentement accordé",
    "access_denied": "Consentement refusé",
    "access_expired": "Demande expirée",
    "access_revoked": "Accès révoqué",
    "dodocard_scan": "Scan DotoCard (même compte pro)",
}

# kind → même routing (payload.kind prioritaire si présent)
NOTIFICATION_KIND_ROUTES = {
    "consultation": "dossier_updated",
    "consultation_annulee": "dossier_updated",
    "ordonnance": "ordonnance",
    "ordonnance_dispensee": "ordonnance",
    "examen": "examen",
    "examen_fichier": "examen",
    "rdv_created": "appointment",
    "rdv_pending": "appointment",
    "rdv_confirmed": "appointment",
    "rdv_updated": "appointment",
    "rdv_annule": "appointment",
    "insurance_updated": "dossier_updated",
    "insurance_removed": "dossier_updated",
    "bon_examen": "bon_examen",
    "bon_resultat": "examen",
    "access_request": "access_request",
}


def contracts_payload():
    return {
        "pin_length": PIN_LENGTH,
        "otp_length": OTP_LENGTH,
        "demo_otp": DEMO_OTP_CODE,
        "specialites": SPECIALITES,
        "prise_en_charge": [{"value": v, "label": l} for v, l in PRISE_EN_CHARGE],
        "medicament_formes": MEDICAMENT_FORMES,
        "medicament_moments": [{"value": v, "label": l} for v, l in MEDICAMENT_MOMENTS],
        "bon_examen_statuts": [{"value": v, "label": l} for v, l in BON_EXAMEN_STATUTS],
        "notification_routes": NOTIFICATION_ROUTES,
        "sse_event_types": list(SSE_EVENT_TYPES.keys()),
        "conflict": "last_write_wins",
        "hospital_required_roles": list(HOSPITAL_REQUIRED_ROLES),
        "offline": {
            "strategy": "cache + FIFO queue + replay on reconnect",
            "conflict": "last-write / timestamp (client_updated_at → 409 si stale)",
        },
    }
