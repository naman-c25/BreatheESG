# Decisions

Every meaningful ambiguity I resolved in the four-day build, what I chose, why, and what I would ask the PM if I could. Pair this with TRADEOFFS.md (what I deliberately didn't build) and SOURCES.md (per-source research).

I am only listing decisions I actually thought about. There are no "I chose React because it's popular" entries here. If a choice is obvious or imposed by the brief, it's not here.

---

## 1. Single normalized `EmissionActivity` vs. per-source tables

**Chose**: one `EmissionActivity` table for fuel, electricity, and travel.

**Considered**: `FuelActivity`, `ElectricityActivity`, `TravelActivity` polymorphism (table per source kind, optional shared base via abstract or concrete inheritance).

**Why one table**: the dashboard reads "every unapproved row, any source, for this client, in this period." A unified table is one query; polymorphism is forever-UNIONs. Source-specific detail belongs in `raw_record.payload` (JSONB) which is right there via FK. The overlap between source schemas is high (date, quantity, unit, facility), so the cost of unification is two columns and a JSON sidecar — much less than the cost of fragmenting every analytics query and every UI table.

**Would ask PM**: are there source-specific *workflow* differences I'm missing? E.g. does procurement need approval from a different person than electricity? If yes, that's an `approver_role` enum, not a separate table.

---

## 2. Multi-tenancy: `tenant_id` FK vs. schema-per-tenant

**Chose**: `tenant_id` FK on every tenant-scoped row + middleware that validates the `X-Tenant-Id` header.

**Considered**: `django-tenants` schema-per-tenant, database-per-tenant.

**Why FK**: standard B2B SaaS pattern (Stripe, Linear, Vercel). Migrations don't multiply by tenant count; cross-tenant queries (benchmarking, internal dashboards) stay possible. The leakage risk is real but mitigated by an abstract manager that requires a tenant filter and by isolation tests on every list endpoint.

**Would ask PM**: any client with a regulatory requirement for physical data isolation? If yes, that's a fundamentally different architecture and the answer changes.

---

## 3. Emission factor snapshotting

**Chose**: every `EmissionActivity` stores `factor_id` (live FK) *and* `factor_value_snapshot` + `factor_source_snapshot` (frozen at approval).

**Considered**: just `factor_id`, recompute on demand.

**Why snapshot**: when DEFRA publishes 2024 factors, importing them would otherwise silently change last year's locked numbers. Auditors do not tolerate that. The cost is ~24 bytes per row.

**Would ask PM**: should re-importing factors retroactively recompute *pending* (un-approved) rows? Current behavior: no, factor is resolved at ingestion time and recomputation requires re-ingestion. Defensible but worth confirming.

---

## 4. Raw payload retention

**Chose**: `RawRecord` is immutable, JSONB, one per logical input row. Original file blob is *not* retained.

**Considered**: keep the original file as well (S3 / disk).

**Why not the file**: file storage is its own infra problem and not strictly necessary if the parsed payload is faithful. Acknowledged in TRADEOFFS.md as the first thing I'd add. For SAP CSV and travel JSON the parsed payload is essentially lossless; for utility PDFs we lose the bill image, which is a real audit artifact.

**Would ask PM**: do auditors require the original PDF, or is the parsed text + extracted values enough? My experience says they want the PDF.

---

## 5. SAP export format choice

**Chose**: pipe-delimited flat file (CSV export from SE16N or a Z-report).

**Considered**: IDoc (XML), BAPI/RFC (live SAP connection), OData service.

**Why flat file**: the only mode an intern can realistically demo without a live SAP system. Also genuinely how mid-market clients move data on day-one onboarding — a procurement analyst exports a report and sends it. SOURCES.md has the full reasoning.

**Would ask PM**: do any current clients want a live SAP feed? That's a 4-week integration, not a 4-day prototype.

---

## 6. Utility format choice

**Chose**: PDF bill upload.

**Considered**: portal CSV scrape, Green Button XML, utility-specific API.

**Why PDF**: the only format every commercial customer has, every month, for every account. Portal CSVs vary per utility; Green Button has poor commercial-account coverage. The fragility cost is real (regex parsing per layout); I lean into it by surfacing parse failures loudly per page rather than silently extracting wrong numbers.

**Would ask PM**: which utilities are in scope for the first three clients? If they all use the same utility (e.g. PG&E), a portal scrape becomes worth building.

---

## 7. Travel format choice

**Chose**: JSON upload mirroring Concur Travel Booking API v4 shape.

**Considered**: pull directly from Concur, CSV export.

**Why JSON upload**: matches what a travel team actually sends a third party. Direct Concur pull requires OAuth + admin consent + per-tenant configuration — same "4-week integration" problem as live SAP.

**Would ask PM**: any client using Navan, TravelPerk, or anything other than Concur? The adapter is shaped around Concur's nested-segments model; non-Concur sources may flatten differently.

---

## 8. Distance computation for flights

**Chose**: great-circle distance from a seeded airport coordinate lookup; flag `MISSING_FACTOR` if either airport is unknown.

**Considered**: trust an external distance API per flight; ignore unknown airports.

**Why great-circle + seeded table**: external calls during ingestion add failure modes and latency. Great-circle is within ~3% of actual flown distance for non-extreme routes — close enough for category 6 reporting. Unknown airports must error, not silently zero out: a flight we can't measure must not become a flight that emitted nothing.

**Would ask PM**: should we use airline-published actual flown distance when available (some travel platforms include it)? Current code prefers it if present in the payload but the sample data doesn't exercise that path.

---

## 9. Unit normalization: dict vs. DB table

**Chose**: in-code `UNIT_ALIASES` dict + `UNIT_FACTORS` table in [services.py](backend/core/services.py).

**Considered**: a `UnitAlias` DB table.

**Why dict for now**: ~10 entries, never changes per tenant. DB table is the right answer at ~100 entries or when tenants want to add aliases. Easy migration when the time comes.

**Would ask PM**: do clients ever submit data with custom units (e.g. company-internal "BBL-EQ" for barrels of oil equivalent)? If yes, the DB table is needed.

---

## 10. Status state machine: enum on the row vs. separate history table

**Chose**: enum on the row. History is reconstructable from `AuditLogEntry`.

**Considered**: `ActivityStatusHistory` table.

**Why enum**: the current state is queried 1000× more than the history. Putting it on the row is a simple `WHERE status=` instead of a `LATERAL JOIN`. The history is for the audit pane, which queries `AuditLogEntry` anyway.

---

## 11. Authentication

**Chose**: no real authn for the demo. Tenant is selected via `X-Tenant-Id` header and the first analyst user for that tenant is used as the actor.

**Considered**: Django sessions, JWT, SSO stub.

**Why nothing**: real authn would eat a day and gives no insight into the data-model questions the brief is grading on. Acknowledged as a TRADEOFF and called out in MODEL.md too.

**Would ask PM**: which auth provider — Auth0, WorkOS, SSO via SAML? Determines a lot of the data model around users, roles, and audit actor identity.

---

## 12. Async ingestion: Celery vs. synchronous

**Chose**: synchronous in the request cycle for the prototype.

**Considered**: Celery + Redis worker.

**Why sync**: a PDF parse takes ~200ms, a SAP CSV ~50ms, a travel JSON ~30ms. None warrants async for the demo. The orchestrator (`services.run_ingestion`) is structured so swapping to Celery is a one-line `delay()` call when row counts grow. Honest tradeoff per the brief.

---

## 13. Outlier detection: 3σ vs. configurable

**Chose**: hardcoded 3σ over the prior 180 days of approved+locked rows for the same (facility, category), minimum 3 prior data points.

**Considered**: configurable per tenant, IQR-based, learned models.

**Why hardcoded**: the right value is something you tune after watching analyst reactions. Shipping a config knob before that data exists is premature. Documented as a thing to revisit.

---

## 14. Subset of each source actually handled

**SAP**: movement types 261/201/101 only; material prefixes FUEL-DSL/FUEL-PET/FUEL-NG and PROC-* only; German *or* English headers; pipe delimiter (configurable in source). Ignored: 311 (transfer postings), 521/541 (third-party deliveries), refined-product hierarchies, batch management.

**Utility**: a single bill layout (ConEd-style commercial summary page) — service period + total energy usage + meter ID. Ignored: time-of-use rate breakdowns, demand charges as a separate emission driver, line-item billing.

**Travel**: airSegments, hotelStays, carRentals. Ignored: rail, ride-hail expense reports, multi-leg fare-class differences within one segment, cancelled bookings.

These choices are also in SOURCES.md per source.

---

## 15. UI styling: hand-rolled CSS vs. Tailwind vs. component library

**Chose**: one CSS file, ~150 lines.

**Considered**: Tailwind, shadcn/ui.

**Why plain CSS**: three screens. Tailwind's toolchain weight and shadcn's component sprawl both pay off at 30+ screens, not 3. The CSS is small enough to read in one sitting and defend.

---

## 16. Currency handling for procurement

**Chose**: store the raw payload (which contains amount + currency) but don't compute spend-based emissions.

**Considered**: implement spend-based Cat 1 (£ × spend factor).

**Why not**: spend-based factors are noisy and the brief's example called out "fuel and procurement" without specifying spend-based. If procurement is intended as activity-based (kg of cement × kg-CO₂e/kg), my SAP adapter handles that path. Confirmed-needed before adding spend-based.

---

## 17. Re-ingestion semantics

**Chose**: re-uploading a file produces new RawRecords (always). If a `row_hash` matches an existing un-locked activity's raw record, the existing activity gets updated. If it matches a *locked* activity, a new activity is created with `supersedes_id` and a `DUPLICATE_OF_LOCKED` flag for the analyst.

**Considered**: idempotent re-ingestion (skip duplicates silently).

**Why this way**: silent dedupe hides upstream changes. If SAP corrected a row in the source system and the value differs, the analyst needs to see both versions. The flag forces them to reconcile.
