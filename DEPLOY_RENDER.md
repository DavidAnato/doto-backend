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

> En dév local : **aucune variable obligatoire**.  
> Sur Render, renseigner au minimum `DATABASE_URL` (auto si Postgres lié) et idéalement `DJANGO_SECRET_KEY`.  
> CORS/CSRF restent ouverts par défaut (`CORS_ALLOW_ALL_ORIGINS=True`) tant que tu ne les désactives pas — adapté démo/test.

| Variable | Valeur | Obligatoire ? |
|----------|--------|---------------|
| `DATABASE_URL` | Internal DB URL Render | Oui (prod Postgres) |
| `DJANGO_SECRET_KEY` | chaîne aléatoire | Recommandé |
| `DJANGO_DEBUG` | `False` en prod stricte (défaut code = `True`) | Optionnel |
| `DJANGO_ALLOWED_HOSTS` | `*` ou `.onrender.com` | Optionnel (`*` par défaut) |
| `DJANGO_BEHIND_PROXY` | `True` | Recommandé sur Render |
| `CARD_TOKEN_KEY` | Fernet key | Non (dérivé de SECRET_KEY) |
| `PUBLIC_API_BASE` | `https://<service>.onrender.com` | Recommandé (URLs media) |
| `CORS_ALLOW_ALL_ORIGINS` | `True` (défaut) / `False` en durcissement | Non |
| `OPEN_CSRF` | `True` (défaut si DEBUG/CORS open) | Non |
| `DEMO_OTP_CODE` | `000000` | Non (déjà défaut) |


---

## 3. Build Command (Render → Settings → Build Command)

Copie **toutes** ces lignes (une seule Build Command) :

```bash
pip install --upgrade pip && pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate --noinput
```

> `runtime.txt` force **Python 3.11** (évite l’échec `rapidocr` / wheels 3.13).  
> L’OCR pièce d’identité n’est **pas** dans `requirements.txt` (trop lourd pour Render) — voir `requirements-ocr.txt` en local.

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
# .env optionnel — sans fichier : SQLite + CORS/CSRF ouverts
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
