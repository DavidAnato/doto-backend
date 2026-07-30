# doto-backend — API DOTO+

API Django + Django REST Framework de l'écosystème santé **DOTO+** :
carte d'accès QR **DotoCard**, plateforme web pro, apps mobiles patient & pro,
back-office. Conçue d'après le cahier des charges v2.

## Stack
- Django 5 · Django REST Framework · SimpleJWT (auth)
- django-cors-headers · django-filter
- cryptography (tokens DotoCard AES/Fernet) · qrcode + Pillow (QR)
- SQLite en dev, PostgreSQL en prod (variables `POSTGRES_*`)

## Applications
| App | Rôle |
|-----|------|
| `accounts` | Utilisateurs (RBAC multi-rôles + admin), structures, auth JWT |
| `patients` | Patients, dossiers médicaux, assurance, en-tête d'urgence |
| `medical` | Consultations, ordonnances (+interactions), examens, constantes |
| `cards` | **DotoCard** : émission/révocation/réémission token QR, scan, dashboard |
| `audit` | Journal d'audit (loi 2017-20) + export CSV |

## Installation (venv local à ce dossier)

```bash
cd doto-backend
python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
# OCR local optionnel (Python 3.11/3.12) : pip install -r requirements-ocr.txt
python manage.py migrate
python manage.py seeddata
# (alias) python manage.py seed_demo
python manage.py runserver 8000
```

> **Pas de `.env` requis** en dév/test : SQLite, CORS ouvert, CSRF assoupli, OTP `000000`.
> Un `.env` reste optionnel pour override (voir `.env.example`).

- API : http://127.0.0.1:8000/api/health/
- Admin Django : http://127.0.0.1:8000/admin/
- **Déploiement Render** : voir [`DEPLOY_RENDER.md`](DEPLOY_RENDER.md) (build, migrate, gunicorn, `seeddata`)

## Comptes de démonstration
Login pro / admin : **identifiant + mot de passe** (pas d'OTP).
OTP mock `000000` : inscription patient ou changement de mot de passe uniquement.
Liste complète affichée par `seeddata` / `seed_demo`.

| Rôle | Identifiants |
|------|--------------|
| Admin | `admin` / `AdminDoto2026!` |
| Médecins | `medecin`, `medecin2`, `medecin3` / `Medecin123!` |
| Infirmiers | `infirmier`, `infirmier2`, `infirmier3` / `Infirmier123!` |
| Pharmaciens | `pharmacien`, `pharmacien2` / `Pharma123!` |
| Laborantins | `laborantin`, `laborantin2` / `Labo123!` |
| Ambulanciers | `ambulancier`, `ambulancier2`, `ambulancier3` / `Ambulancier123!` |
| Réceptionnistes | `reception`, `reception2` / `Reception123!` |
| Patients (DotoPlus) | `+229 97 45 12 88` (+ 3 autres) / `demo123` — NPI pour Hub/ANIP, PIN optionnel `123456` |

Matrice d'accès : voir `core/permissions.py` et le README racine.

## Providers OTP / ANIP
| Variable | Défaut | Rôle |
|----------|--------|------|
| `SMS_PROVIDER` | `mock` | `mock` (code `DEMO_OTP_CODE`) ou `twilio` |
| `DEMO_OTP_CODE` | `000000` | Code accepté en mock |
| `TWILIO_*` | — | Stub Twilio (optionnel) |
| `ANIP_PROVIDER` | `mock` | `mock` ou `http` |
| `ANIP_BASE_URL` / `ANIP_API_KEY` | — | Stub HTTP ANIP |

Auth : `POST /api/auth/login/` (pro, sans OTP), `POST /api/auth/patient/login/` (tél+mdp),
`POST /api/auth/otp/` (purpose=register|password_change|password_reset),
`POST /api/auth/patient/register/`, `POST /api/auth/patient/password-change/`,
`POST /api/auth/patient/pin/` (déverrouillage secondaire), `POST /api/auth/patient/set-pin/`.

Patient démo PIN optionnel : `123456`.
