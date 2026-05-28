# Tradeoffs

Three things I deliberately did not build, and why.

---

### 1. Original file storage (PDFs and CSVs as blobs)

I parse the file into `RawRecord.payload` and drop the bytes. For CSV/JSON the payload is lossless. For utility PDFs we lose the rendered bill, which auditors actually want.

**Why not now**: file storage means S3 (or persistent disk) + signed URLs. Real infra to deploy. Doesn't change the schema or any grading axis — adding `IngestionRun.source_blob_url` later is a one-column migration.

**First thing I'd build after submission.**

---

### 2. Background ingestion (Celery / queue)

Ingestion runs in the request cycle. A 50,000-row SAP file would block and time out.

**Why not now**: real demo files parse in 50–200 ms. Celery adds Redis + a worker + a second Render service for a problem the demo doesn't have. The orchestrator (`services.run_ingestion`) is structured so the swap is one line: `run_ingestion.delay(...)` and return the run ID immediately.

**Will fall over at the first real client.** Half-day fix once it matters.

---

### 3. RBAC and SSO

Auth exists (session cookies, login, audit-actor recording — see DECISIONS §15). What's missing: any role besides "analyst." Every authenticated user can do everything.

**Why not now**:
- Roles are a *product* question, not engineering. "Can a client-side user see their own tenant but not lock for audit?" needs a PM answer. Building roles on top of a guessed policy is worse than no roles.
- SSO is a B2B sales-cycle requirement, not a day-one need. When it lands, the right move is a managed provider (WorkOS / Auth0), not custom SAML code.

**What it costs**: the app can't safely give read-only access to a client-side user. The schema supports adding `User.role` + DRF permission classes when the policy is decided.

---

## Honorable mentions (so you don't ask)

- **Org hierarchies** — flat tenants. `parent_id` + recursive CTE when a client needs subsidiary rollups.
- **Adjustments UI** — schema has `EmissionActivity.adjusts` for post-lock corrections; no UI flow to issue one yet.
- **Pro-rata bill splitting UI** — model supports it (`parent_activity_id`), UI does bill-date attribution only.
- **Postgres row-level security** — FK-based tenancy is primary defense; RLS is belt-and-braces, worth adding before the first enterprise security review.
- **Spend-based procurement factors** — kept the currency in raw payload, didn't compute. Confirmed-needed before building.
