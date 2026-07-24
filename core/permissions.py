"""Permissions RBAC — rôles professionnels de santé (CDC §1.3 / §6.2).

## Matrice LECTURE (qui voit quoi)

| Rôle            | Urgence | Dossier clinique | Historique | Ordo | Examens | Constantes | Assurance | Scan |
|-----------------|---------|------------------|------------|------|---------|------------|-----------|------|
| Médecin         | oui     | complet          | oui        | oui  | oui     | oui        | oui       | oui  |
| Infirmier       | oui     | soins (sous-ens.)| oui        | —    | oui     | oui        | —         | oui  |
| Pharmacien      | oui*    | —                | —          | oui  | —       | —          | —         | oui  |
| Laborantin      | oui*    | —                | —          | —    | oui     | —          | —         | oui  |
| Ambulancier     | oui**   | —                | —          | —    | —       | oui (É)    | —         | oui  |
| Réceptionniste  | identité| —                | —          | —    | —       | —          | oui       | oui  |
| Patient         | propre  | propre dossier   | propre     | propre| propre | propre     | propre    | —    |
| Admin           | oui     | oui              | oui        | oui  | oui     | oui        | oui       | oui  |

* Pharmacien/Laborantin : urgence = allergies / groupe sanguin pertinents.
** Ambulancier : sous-ensemble urgence (groupe sanguin, allergies, chroniques, contacts).

## Matrice ÉCRITURE (qui ajoute / modifie)

| Action                         | Rôles autorisés                          |
|--------------------------------|------------------------------------------|
| Consultations / diagnostic     | Médecin, Admin                           |
| Ordonnances (créer)            | Médecin, Admin                           |
| Dispenser ordonnance           | Pharmacien, Admin                        |
| Notes de soins / constantes    | Infirmier, Médecin, Ambulancier*, Admin  |
| Résultats examens (upload)     | Laborantin, Admin                        |
| Démographie                    | Réceptionniste, Admin                    |
| Assurance (assureur / police)  | Réceptionniste, Médecin, Admin           |
| RDV (création / gestion)       | Médecin, Réceptionniste, Admin           |
| Profil contact / consentements | Patient (soi-même uniquement)            |
| Données médicales patient      | Patient : LECTURE seule (pas d'écriture) |

* Ambulancier : constantes / notes d'urgence limitées uniquement.

## Consentement patient

Toute ouverture dossier pro (scan ou recherche) crée une AccessRequest.
Le patient confirme via Doto+ (SSE + notif). Exceptions : mode urgence
explicite ou rôle ambulancier (emergency_open) — bypass audité.
"""
from rest_framework.permissions import BasePermission, SAFE_METHODS


class Roles:
    PATIENT = "patient"
    MEDECIN = "medecin"
    INFIRMIER = "infirmier"
    PHARMACIEN = "pharmacien"
    LABORANTIN = "laborantin"
    AMBULANCIER = "ambulancier"
    RECEPTIONNISTE = "receptionniste"
    ADMIN = "admin"

    PROFESSIONALS = (
        MEDECIN,
        INFIRMIER,
        PHARMACIEN,
        LABORANTIN,
        AMBULANCIER,
        RECEPTIONNISTE,
        ADMIN,
    )


# Sections exposées dans la réponse dossier (`access.sections`) et filtrées côté API.
ROLE_SECTIONS: dict[str, frozenset[str]] = {
    Roles.MEDECIN: frozenset(
        {
            "urgence",
            "dossier",
            "historique",
            "ordonnances",
            "examens",
            "constantes",
            "assurance",
            "scan",
            "prescrire",
            "emergency_open",
            "rdv",
        }
    ),
    Roles.INFIRMIER: frozenset(
        {
            "urgence",
            "dossier",
            "historique",
            "examens",
            "constantes",
            "scan",
            "soins",
            "emergency_open",
            "rdv",  # lecture agenda uniquement (écriture via SECTION_WRITE_ROLES)
        }
    ),
    Roles.PHARMACIEN: frozenset({"urgence", "ordonnances", "scan", "dispenser"}),
    Roles.LABORANTIN: frozenset({"urgence", "examens", "scan", "upload_lab"}),
    Roles.AMBULANCIER: frozenset({"urgence", "constantes", "scan", "emergency_open"}),
    Roles.RECEPTIONNISTE: frozenset(
        {"urgence", "assurance", "scan", "demographie", "rdv"}
    ),
    Roles.ADMIN: frozenset(
        {
            "urgence",
            "dossier",
            "historique",
            "ordonnances",
            "examens",
            "constantes",
            "assurance",
            "scan",
            "prescrire",
            "dispenser",
            "upload_lab",
            "emergency_open",
            "demographie",
            "soins",
            "rdv",
        }
    ),
}

# Lecture des ressources médicales listées
SECTION_READ_ROLES = {
    "historique": (Roles.MEDECIN, Roles.INFIRMIER, Roles.ADMIN),
    "ordonnances": (Roles.MEDECIN, Roles.PHARMACIEN, Roles.ADMIN),
    "examens": (Roles.MEDECIN, Roles.INFIRMIER, Roles.LABORANTIN, Roles.ADMIN),
    "constantes": (Roles.MEDECIN, Roles.INFIRMIER, Roles.AMBULANCIER, Roles.ADMIN),
}

# Écriture explicite (miroir des write_roles ViewSets)
SECTION_WRITE_ROLES = {
    "historique": (Roles.MEDECIN, Roles.ADMIN),
    "ordonnances": (Roles.MEDECIN, Roles.ADMIN),
    "dispenser": (Roles.PHARMACIEN, Roles.ADMIN),
    "examens": (Roles.LABORANTIN, Roles.ADMIN),
    "constantes": (Roles.INFIRMIER, Roles.MEDECIN, Roles.AMBULANCIER, Roles.ADMIN),
    "assurance": (Roles.RECEPTIONNISTE, Roles.MEDECIN, Roles.ADMIN),
    "dossier": (Roles.MEDECIN, Roles.INFIRMIER, Roles.ADMIN),
    "demographie": (Roles.RECEPTIONNISTE, Roles.ADMIN, Roles.MEDECIN),
    "rdv": (Roles.MEDECIN, Roles.RECEPTIONNISTE, Roles.ADMIN),
}


def role_sections(role: str) -> frozenset[str]:
    return ROLE_SECTIONS.get(role, frozenset({"urgence", "scan"}))


def role_can(role: str, section: str) -> bool:
    return section in role_sections(role)


def role_can_write(role: str, section: str) -> bool:
    return role in SECTION_WRITE_ROLES.get(section, ())


class HasRole(BasePermission):
    """Autorise si le rôle de l'utilisateur est dans `allowed_roles` de la vue."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        allowed = getattr(view, "allowed_roles", None)
        if allowed is None:
            return True
        return request.user.role in allowed


class IsProfessional(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role in Roles.PROFESSIONALS
        )


class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.role == Roles.ADMIN or request.user.is_superuser)
        )


class IsMedecin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == Roles.MEDECIN
        )


class IsPatient(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role == Roles.PATIENT
        )


class ReadOnlyOrRole(BasePermission):
    """Lecture pour tout pro authentifié, écriture réservée à `write_roles`."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return request.user.role in Roles.PROFESSIONALS
        return request.user.role in getattr(view, "write_roles", ())
