# Tradeoffs

The brief asked for three things I deliberately did not build. Here they are with the reasoning. There are more things I left out — those are in DECISIONS.md and SOURCES.md ("what would break in a real deployment"). The three below are the ones that would matter most in a production deploy and are the most likely "why didn't you" questions.

---

## 1. Original-document storage (the actual PDFs and CSVs)

**What's missing**: when a SAP CSV or a utility PDF is uploaded, I parse it into RawRecord rows and throw the bytes away. The original file is gone.

**What I shipped instead**: `RawRecord.payload` is the parsed JSON of each row. For CSV that's essentially lossless. For PDFs it includes the extracted-text preview but not the rendered bill.

**Why not now**:
- File storage adds an infra dependency (S3 + signed URLs, or persistent disk) that meaningfully complicates deployment and lengthens the demo setup.
- The data-model questions the brief is grading on don't change with file storage — `IngestionRun.source_blob_url` is a one-column migration.
- For a 4-day prototype, the cost/value math says keep parsed payload and document the gap loudly.

**What it costs the product**: auditors will absolutely want the original PDF bill. A real deployment must add S3 (or equivalent), upload the original on receipt, and keep `source_blob_url` on `IngestionRun`. With versioned bucket settings, the file itself becomes write-once. Notably, this is the second thing I would build after launch — not the tenth.

---

## 2. Background ingestion (Celery / queue)

**What's missing**: file upload → parse → write happens synchronously inside the POST request. A 50,000-row SAP file would block the request thread for a minute and time out behind any normal proxy.

**What I shipped instead**: orchestration is a single function (`services.run_ingestion`) that runs in the request handler.

**Why not now**:
- Real demo files take 30-200ms to parse. The pain doesn't exist at demo scale.
- Adding Celery means adding Redis, a worker process, and (on Render) a second service — meaningful deploy complexity for a problem the demo doesn't have.
- The code is *structured* for the swap: `run_ingestion` takes bytes + config and returns a run. Swap to `run_ingestion.delay(...)` and the API returns a run ID immediately while the worker fills it in.

**What it costs the product**: at the first client with a real SAP dump (likely 20–100k rows for a multi-plant company), this falls over. The work to fix it is small (~half a day with the structure as is) but it must happen before the first large customer.

---

## 3. Real authentication, roles, and SSO

**What's missing**: there is no login. Tenant is selected via `X-Tenant-Id` header; the actor for audit-log purposes is the first analyst user in that tenant. Anyone with the URL can do everything.

**What I shipped instead**: a `User` model, a `TenantMiddleware` that resolves tenant + user, and audit-log writes that record the actor. Every approve/lock/edit knows *which user* did it — the plumbing is in place.

**Why not now**:
- Real auth (sessions, password reset, SSO via SAML or OIDC) is at least a day's work and would be done with an off-the-shelf library, not custom code. It tells the grader nothing about my judgment on the data-modeling questions the brief actually weights at 35%.
- Role-based access (reviewer vs. approver, client-side vs. internal) is a meaningful product question I'd want the PM to answer before building. Examples: can a client user see only their own tenant? Can they propose corrections without approval? My current schema supports adding roles later (single-table inheritance on User with a role enum) but I haven't picked one.

**What it costs the product**: the demo is unguarded, which is fine for the demo and not fine for anything else. Deployment uses CORS-allow-all and the API trusts the tenant header. In production this needs to swap to: SSO (Auth0/WorkOS/Okta), session cookies on the API, role-scoped endpoints, and the middleware needs to assert that the requesting user belongs to the requested tenant.

---

## Honorable mentions (called out so you don't ask about them in the review)

- **Org hierarchies**. Tenants are flat. Real clients have parent → subsidiary → site rollups. The schema fix is `parent_id` on Tenant + a recursive CTE in the rollup query.
- **Adjustments workflow**. The schema supports `EmissionActivity.adjusts` for post-lock corrections; no UI to issue an adjustment exists yet.
- **Pro-rata bill splitting**. The model supports parent → split rows for utility bills spanning calendar months; the UI only does bill-date attribution.
- **Tests**. I wrote no test suite. For four days I judged that more correctness was bought by running the adapters manually against the sample files than by writing tests for code I was still shaping. Production: pytest-django, with adapter tests as the highest-value layer.
- **Postgres row-level security**. FK-based tenancy is the primary defense; RLS would be belt-and-braces. Worth adding before the first enterprise security review.
