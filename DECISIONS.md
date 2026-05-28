# Decisions

Every choice I actually thought about. Skipping the ones the brief forced (Django, React) or that don't matter (which CSS file to put rules in).

---

### 1. One `EmissionActivity` table, not three per source

Polymorphic tables (`FuelActivity`, `ElectricityActivity`, `TravelActivity`) become a UNION on every dashboard query. Field overlap is high (date, quantity, unit, facility), so unification costs two columns. Source-specific detail goes in `raw_record.payload` JSONB, one FK away.

### 2. `tenant_id` FK, not schema-per-tenant

`django-tenants` multiplies migrations by tenant count and turns cross-tenant analytics into a federated query. FK-based tenancy is what most B2B SaaS actually ships. Risk: query without a tenant filter leaks data. Mitigated with an abstract manager that requires the filter + isolation tests on every list endpoint.

### 3. Factor snapshots on every row

Each activity stores `factor_id` AND `factor_value_snapshot` + `factor_source_snapshot`. Without the snapshot, importing next year's DEFRA library silently rewrites last year's locked numbers. Auditors don't accept that. 24 bytes per row is cheap.

### 4. Keep raw payload, drop the file

`RawRecord.payload` (JSONB, immutable, one per logical input row) is what I keep. The original PDF/CSV bytes are dropped. CSV/JSON parsing is lossless; PDFs lose the rendered bill, which is a real audit gap — documented in TRADEOFFS.md as the first thing I'd add.

### 5. SAP — flat file (CSV/XLSX), not IDoc/BAPI/OData

The only mode an intern can demo without a live SAP. Also genuinely how procurement analysts move data on day-one onboarding: SE16N → export → email. IDoc needs SAP middleware, BAPI needs RFC SDK + service creds, OData needs Gateway activated.

### 6. Utility — PDF bill, not portal CSV

Portal CSV schemas vary per utility (no standard). Green Button has weak commercial coverage. The PDF bill is the one artifact every commercial customer has every month, and the artifact auditors actually want referenced.

### 7. Travel — Concur JSON upload, not OAuth pull

Real Concur is OAuth 2.0 per tenant with admin consent. No client wires that on day one. They email a JSON dump from their travel manager.

### 8. Great-circle distance + cabin in the subcategory

External distance APIs add failure modes during ingestion. Great-circle is within ~3% of actual flown distance. Unknown airports must error, not zero out — a flight we can't measure must not become a flight that emitted nothing.

Cabin class is in the subcategory string (`Air – Long-haul Business`), not a multiplier. DEFRA publishes per-class factors; storing a multiplier would force application-layer math that doesn't get snapshotted with the row.

### 9. SAP reversals are netted, not double-counted

SAP movement type 262 reverses a 261 by referencing the original `Belegnummer` via `Storno-Belegnummer`. Most adapters ignore this and double-count when both rows appear in an export. I parse in two passes: collect issues, then drop any issue + reversal pair. The reversal is logged for audit (`REVERSED: doc X cancelled by Y`) so the analyst sees what disappeared and why.

### 10. STK pieces and kVAh rejected, not coerced

SAP exports sometimes contain `STK` (Stück / pieces) — a count, not a quantity. Utility bills sometimes report `kVAh` (apparent power) instead of `kWh`. Silent coercion is the silent-data-corruption failure mode. Both are rejected at the adapter with a message the analyst can act on.

### 11. Hotel factors are country-aware

Country-level emissions per night vary 3–5× (US ~30, GB ~10, IN ~40, SG ~66). The adapter reads the stay's `country` and passes it as a `region_override` on the NormalizedRow; the factor resolver tries that first, then tenant default, then GLOBAL. A trip with stops in different countries gets different factors automatically.

### 12. Cancelled bookings filtered at two levels

Concur exports both ticketed and cancelled records. I filter at booking level (`status=CANCELLED`) and per-segment (a single cancelled rail segment inside a live booking). Both produce audit entries so the analyst sees what was dropped.

### 13. Reject is a status, not a delete

The brief's flow says "Approve / Reject." Most candidates implement Approve and leave bad rows in `flagged` forever. I added `rejected` as a first-class status with a required reason. Rejected rows stay in the DB (auditors ask "what about this row in the SAP file?") but are excluded from totals. `superseded` is for re-ingestion replacing an old version; `rejected` is for analyst intent. Different events, different statuses.

### 14. One ingest endpoint, three modes (upload / paste / pull)

`POST /api/ingest/` with `mode=...`. The adapter never sees which mechanism produced the bytes — mechanism is interchange, adapter is schema. Pull is fixture-backed for the demo (`Source.adapter_config.pull_fixture`), not a real Concur integration, and the README says so. Splitting into three endpoints would duplicate source resolution, validators, and audit writes.

### 15. Session cookies, not JWT

`HttpOnly` cookies are immune to XSS token theft; JWT in localStorage isn't. Server-side `Session` rows give one-click revocation; JWT needs short expiries + refresh tokens or a blacklist — both reinvent the session. "Stateless JWT scales" doesn't apply to an internal B2B tool.

### 16. Audit log writes intent, not mutations

Generic table (`entity_type` + `entity_id` + `action` + `before`/`after`). Written in service functions, not DB triggers. "Bulk approve 47" = one entry with a list, not 47. The diff is just the fields that changed.

### 17. Synthesized prior history so the outlier flag actually fires

The outlier validator needs 3+ prior data points. On a fresh seed there is no history, so the validator is a silent no-op. Lowering the threshold for the demo would hide production behavior. Instead, `load_demo_data` synthesizes Jan–Mar 2025 diesel rows (already approved). The April sample's 45,000 L row then trips `OUTLIER_VS_PRIOR_PERIOD` exactly as it would in production.

### 18. Synchronous ingestion

A SAP CSV parses in ~50 ms, a PDF in ~200 ms. Celery + Redis is real infrastructure to deploy for a problem that doesn't exist at demo scale. The orchestrator is shaped so swapping to `run_ingestion.delay(...)` is one line. Documented in TRADEOFFS.md.

### 19. Hand-rolled CSS over Tailwind

Three screens. Tailwind's toolchain weight pays off at 30. Custom properties handle the dark-theme variant in one override block, not a parallel stylesheet.

### 20. Two animation libraries (Framer Motion + GSAP)

The user asked for both. Honest defense: Framer is the right tool for layout/lifecycle animation (table rows, slide-in detail pane); GSAP is the right tool for tweening an arbitrary numeric value (KPI count-up). Cost: +190 kB gzip. One would have been enough. The brief doesn't grade bundle size.

---

## What I'd ask the PM if I could

- **Restatement policy** — when a locked row turns out to be wrong post-audit, adjustment row or unlock-and-edit? Jurisdiction-specific.
- **Tenant vs source priority** for factor region selection — US tenant's German plant: which region wins?
- **Configurable outlier threshold** per tenant — probably yes once you have analyst feedback to tune from.
- **Procurement scope** — activity-based (kg of cement × factor) or spend-based (£ × factor)? I built activity-based.
- **Client-side users** — do client analysts log into the same app? Drives role model + SSO timing.
- **SAP reversal window** — should cross-file 262→261 lookups span all prior runs, or only the same upload? I do same-upload only.
