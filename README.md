# Breathe ESG — Ingest & Review Console

Django + React. Ingests emissions data from SAP, utility bills, and corporate travel; normalizes it; lets an analyst review, approve, and lock for audit.

Read in this order:
1. **[MODEL.md](MODEL.md)** — schema and why.
2. **[SOURCES.md](SOURCES.md)** — what real SAP / utility / travel data looks like and what I do with it.
3. **[DECISIONS.md](DECISIONS.md)** — every choice.
4. **[TRADEOFFS.md](TRADEOFFS.md)** — what I didn't build.

## Run it

Needs Python 3.11/3.12/3.14 and Node 18+.

**Backend:**
```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py seed
python manage.py generate_sample_pdf
python manage.py generate_sample_xlsx
python manage.py load_demo_data
python manage.py runserver 0.0.0.0:8000
```

**Frontend (separate terminal):**
```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Log in:
- `analyst@acme.example` / `demo1234` (US tenant)
- `analyst@globex.example` / `demo1234` (DE tenant — proves multi-tenancy)

`load_demo_data` already ingested 20+ activities, including ones flagged for outliers, missing factors, unmapped plants, and a kVAh meter rejected. Upload more from `backend/sample_data/` to see the flow live.

## What to look at

- [backend/core/models.py](backend/core/models.py) — schema as code, mirrors MODEL.md.
- [backend/core/adapters/](backend/core/adapters/) — the three source adapters. Each has a docstring explaining the format choice.
- [backend/core/services.py](backend/core/services.py) — orchestration: unit normalization, factor resolution, validators, audit writes.
- [backend/core/tests/](backend/core/tests/) — 34 tests focused on the adapter edge cases that the demo data exercises.

## API

All endpoints require a session cookie except `/api/auth/login/` and `/api/auth/logout/`.

```
POST   /api/auth/login/                { email, password }
POST   /api/auth/logout/
GET    /api/auth/me/

POST   /api/ingest/                    mode=upload (multipart) | paste (content) | pull (uses fixture)
GET    /api/activities/                filters: status, scope, source, facility, run, q
PATCH  /api/activities/<id>/
POST   /api/activities/<id>/approve/   { reason? }
POST   /api/activities/<id>/reject/    { reason }   (required)
POST   /api/activities/<id>/lock/      { reason? }
POST   /api/activities/bulk_approve/   { ids: [...] }
POST   /api/flags/<id>/dismiss/        { reason }   (required)

GET    /api/sources/, /api/facilities/, /api/categories/, /api/runs/
GET    /api/audit/?entity_id=<id>
GET    /api/dashboard/summary/
```

## Deploy

[render.yaml](render.yaml) + [build.sh](build.sh) — Blueprint mode creates a web service and a Postgres in one click. Build script installs deps, runs migrations, seeds, and ingests the demo files.

## Known omissions (full reasoning in TRADEOFFS.md)

- No RBAC beyond a single analyst role; no SSO.
- Synchronous ingestion (no Celery).
- Original PDFs/CSVs parsed but not retained as files.
- No pro-rata bill splitting UI (schema supports it).
