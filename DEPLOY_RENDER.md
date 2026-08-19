# Déploiement Render - doto-backend (API DOTO+)

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
> CORS/CSRF restent ouverts par défaut (`CORS_ALLOW_ALL_ORIGINS=True`) tant que tu ne les désactives pas - adapté démo/test.

| Variable | Valeur | Obligatoire ? |
|----------|--------|---------------|
| `PYTHON_VERSION` | `3.11.11` | **Oui** (aligné `runtime.txt` / `.python-version`) |
| `DATABASE_URL` | Internal DB URL Render | Oui (prod Postgres) |
| `DJANGO_SECRET_KEY` | chaîne aléatoire | Recommandé |
| `DJANGO_DEBUG` | `False` en prod stricte (défaut code = `True`) | Optionnel |
| `DJANGO_ALLOWED_HOSTS` | `*` ou `.onrender.com` | Optionnel (`*` par défaut) |
| `DJANGO_BEHIND_PROXY` | `True` | Recommandé sur Render |
| `CARD_TOKEN_KEY` | Fernet key | Non (dérivé de SECRET_KEY) |
| `PUBLIC_API_BASE` | `https://<service>.onrender.com` | Recommandé (URLs media) |
| `CORS_ALLOW_ALL_ORIGINS` | `True` (défaut) / `False` en durcissement | Non |
| `OPEN_CSRF` | `True` (défaut si DEBUG/CORS open) | Non |
| `DEMO_OTP_CODE` | `00000` | Non (déjà défaut) |
| `DOTO_OCR_ENGINE` | `tesseract` | **Oui** sur Render |
| `DOTO_OCR_LANG` | `fra+eng` | Recommandé |
| `DOTO_OCR_TIMEOUT` | `25` (secondes, sous le proxy ~30 s) | Recommandé |
| `TESSERACT_CMD` | `/usr/bin/tesseract` | Recommandé sur Render |
| `TESSDATA_PREFIX` | auto si vide (souvent `/usr/share/tesseract-ocr/5/tessdata`) | Seulement si Tesseract ne trouve pas `fra` |

---

## 2 bis. OCR Tesseract (Native Environment)

RapidOCR / Paddle / EasyOCR **ne sont pas** utilisés sur Render : trop lourds
(OOM sur 512 Mo, 1er scan > 30 s, build timeout). Le moteur prod est **Tesseract**.

### Paquets apt (obligatoires)

Dans `render.yaml` → `aptPackages` :

- `tesseract-ocr`
- `tesseract-ocr-fra`
- `tesseract-ocr-eng`

**Service déjà créé** (Blueprint souvent ignoré) : Dashboard → service →
**Settings** → **Environment** → **Native Environment Packages** (ou Advanced) →
ajouter les 3 paquets ci-dessus → **Save** → **Manual Deploy**.

Sans `tesseract-ocr-fra`, les cartes CIP/CEDEAO se lisent mal (libellés FR).

### RAM / plan

| Plan | RAM | OCR |
|------|-----|-----|
| **Free / Starter 512 Mo** | suffisant | Tesseract **oui** |
| Starter 1 Go+ | optionnel | RapidOCR/Paddle seulement en local (`requirements-ocr.txt`) |

Un plan payant **n’est pas obligatoire** pour l’OCR, tant que Tesseract système est installé.

### Vérifier après deploy

```bash
curl -s https://<ton-service>.onrender.com/api/health/
```

Attendu : `"ocr": { "available": true, "langs": [..., "fra", "eng"], "detail": "tesseract prêt" }`.

Si `available: false` : paquets apt absents, ou `TESSERACT_CMD` / `TESSDATA_PREFIX` faux.
Logs : `OCR Tesseract prêt` au boot, ou `OCR Tesseract absent`.

L’endpoint `POST /api/auth/patient/ocr-id/` répond en JSON :

| HTTP | `code` | Sens |
|------|--------|------|
| 200 | - | `ok: true` + NPI / identité |
| 422 | `npi_not_found` | Image lue, NPI illisible |
| 503 | `ocr_unavailable` | Binaire Tesseract manquant |
| 504 | `ocr_timeout` | Plus de ~25 s (photo trop lourde) |

---

## 3. Build Command (Render → Settings → Build Command)

Copie **toutes** ces lignes (une seule Build Command) :

```bash
pip install --upgrade pip && pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate --noinput
```

> Forcer **Python 3.11.11** : fichier `.python-version` **et** variable d’env `PYTHON_VERSION=3.11.11`  
> (Dashboard → Environment - le Blueprint seul ne met pas à jour un service déjà créé).  
> **Ne pas** `pip install -r requirements-ocr.txt` sur Render (Paddle/RapidOCR).

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

Alias de `seed_demo` : structures, pros (`mdp123`), patients OTP mock `00000`, dossiers médicaux.

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
# .env optionnel - sans fichier : SQLite + CORS/CSRF ouverts
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

## 7. Fronts (Static Sites Render) - rappel

Après l’API :

**DotoHub / Admin** (Vite) - Build :

```bash
npm ci && npm run build
```

Publish directory : `dist`  
Env : `VITE_API_URL=https://<api>.onrender.com`

**Apps Expo** : pointer `EXPO_PUBLIC_API_URL` vers la même API (EAS / Expo).

---

## 8. Checklist post-deploy

1. `GET /api/health/` → 200 et `"ocr": { "available": true }`  
2. Login pro `medecin` / `mdp123`  
3. Patient OTP `00000`  
4. Scan CIP / CEDEAO (inscription) → NPI lu  
5. Scan mobile + Hub web même compte → ouverture dossier (SSE)  
6. Patient révoque l’accès → Hub ferme le dossier + message  

---

## 9. render.yaml (Blueprint optionnel)

Un fichier `render.yaml` est fourni à la racine du repo backend pour un Blueprint
« Web + Postgres ». Ajuste le `name` / plan selon ton compte.
