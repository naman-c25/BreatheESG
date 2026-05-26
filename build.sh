#!/usr/bin/env bash
set -euo pipefail

# Frontend
cd frontend
npm ci
npm run build
cd ..

# Backend
cd backend
pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate
python manage.py seed
python manage.py generate_sample_pdf
python manage.py load_demo_data
