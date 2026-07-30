# Déploiement Render — doto-backend (API DOTO+)

Guide complet : build → migrate → collectstatic → seeddata → serveur.

> **SSE** : le bus d’événements est **en mémoire**. Sur Render, lancer Gunicorn
> avec **`--workers 1`** (ou un seul process) pour que scan / révocation / dossier
> temps réel fonctionnent entre web et mobile du même pro. Multi-workers ⇒
> Redis pub/sub (non inclus ici).

---

## 1. Prérequis Render

1. Compte [Render](https://render.com)
2. Repo GitHub : `DavidAnato/doto-backend` (déjà push)
3. Créer une **PostgreSQL** (Render → New → PostgreSQL) → note `Internal Database URL`
4. Créer un **Web Service** pointant sur ce repo, **Root Directory** vide (racine du repo)

---

## 2. Variables d’environnement (Web Service)

| Variable | Valeur |
|----------|--------|
| `DJANGO_SECRET_KEY` | chaîne aléatoire longue |
| `DJANGO_DEBUG` | `False` |
| `DJANGO_ALLOWED_HOSTS` | `.onrender.com` (ou ton domaine) |
| `DJANGO_BEHIND_PROXY` | `True` |
| `DATABASE_URL` | *(souvent auto-injectée si tu linkes le Postgres)* |
| `CARD_TOKEN_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `CORS_ALLOW_ALL_ORIGINS` | `False` |
| `CORS_ALLOWED_ORIGINS` | URLs front (ex. `https://dotohub.onrender.com,https://dotoplus-admin.onrender.com`) |
| `PUBLIC_API_BASE` | `https://<ton-service>.onrender.com` |
| `SMS_PROVIDER` | `mock` (ou twilio) |
| `DEMO_OTP_CODE` | `000000` |
| `ACCESS_TOKEN_MINUTES` | `60` |
| `REFRESH_TOKEN_DAYS` | `7` |

Optionnel : `CSRF_TRUSTED_ORIGINS=https://<ton-service>.onrender.com`

---

## 3. Build Command (Render → Settings → Build Command)

Copie **toutes** ces lignes (une seule Build Command) :

```bash
pip install --upgrade pip && pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate --noinput
```

> Le seed **n’est pas** dans le build (évite de re-seeder à chaque deploy).
> Lance-le une fois via Shell (étape 5).

---

## 4. Start Command

```bash
gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120 --keep-alive 65
```

- `--workers 1` : obligatoire pour le SSE in-memory
- `--timeout 120` / `--keep-alive 65` : laisse les connexions EventSource ouvertes

Health check path : `/api/health/`

---

## 5. Seeddata (une fois après le 1er deploy)

Render → ton Web Service → **Shell** :

```bash
python manage.py seeddata
```

Alias de `seed_demo` : structures, pros (`mdp123`), patients OTP mock `000000`, dossiers médicaux.

Vérifier :

```bash
python manage.py showmigrations
curl -s https://<ton-service>.onrender.com/api/health/
```

---

## 6. Chaîne locale équivalente (test avant Render)

```bash
cd doto-backend
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
# Unix:    source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env   # ou cp .env.example .env
# renseigner CARD_TOKEN_KEY + éventuel DATABASE_URL
python manage.py collectstatic --noinput
python manage.py migrate --noinput
python manage.py seeddata
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 1 --threads 4 --timeout 120
```

Ou en dév :

```bash
python manage.py runserver 0.0.0.0:8000
```

---

## 7. Fronts (Static Sites Render) — rappel

Après l’API :

**DotoHub / Admin** (Vite) — Build :

```bash
npm ci && npm run build
```

Publish directory : `dist`  
Env : `VITE_API_URL=https://<api>.onrender.com`

**Apps Expo** : pointer `EXPO_PUBLIC_API_URL` vers la même API (EAS / Expo).

---

## 8. Checklist post-deploy

1. `GET /api/health/` → 200  
2. Login pro `medecin` / `mdp123`  
3. Patient OTP `000000`  
4. Scan mobile + Hub web même compte → ouverture dossier (SSE)  
5. Patient révoque l’accès → Hub ferme le dossier + message  

---

## 9. render.yaml (Blueprint optionnel)

Un fichier `render.yaml` est fourni à la racine du repo backend pour un Blueprint
« Web + Postgres ». Ajuste le `name` / plan selon ton compte.
