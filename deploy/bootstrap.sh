#!/bin/bash
set -euo pipefail
cd /home/fotoce/doto-backend
umask 077
SECRET="$(./venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))')"
cat > .env << EOF
DJANGO_SECRET_KEY=${SECRET}
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=doto-api.await-tech.com,await-origin.fotoce.app,localhost,127.0.0.1
DJANGO_BEHIND_PROXY=True
PUBLIC_API_BASE=https://doto-api.await-tech.com
SMS_PROVIDER=mock
CORS_ALLOW_ALL_ORIGINS=True
OPEN_CSRF=True
DOTO_OCR_ENGINE=tesseract
DOTO_OCR_LANG=fra+eng
TESSERACT_CMD=/usr/bin/tesseract
EOF
chmod 600 .env
./venv/bin/python manage.py migrate --noinput
./venv/bin/python manage.py seeddata
./venv/bin/python manage.py collectstatic --noinput
chmod +x deploy/start.sh deploy/ensure.sh
(
  crontab -l 2>/dev/null | grep -v 'doto-backend/deploy/ensure.sh' || true
  echo '* * * * * /home/fotoce/doto-backend/deploy/ensure.sh'
) | crontab -
./deploy/ensure.sh
sleep 2
curl -sS -m 20 http://127.0.0.1:8020/api/health/
echo
