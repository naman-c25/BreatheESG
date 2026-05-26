# Data Model

This document explains the schema behind the ingestion & review console: what each entity is, why it exists, and what was deliberately left out. It is the most important document in this submission — if a choice here is wrong, no amount of UI polish saves the product.

The model is designed around one observation: **the hard part of carbon accounting is not the arithmetic, it is being able to defend a number to an auditor six months later**. Every design decision below is in service of that.

---

## 1. Goals and non-goals

**Goals**
1. Ingest heterogeneous source data without losing any of it.
2. Produce a normalized activity row that an analyst can review, edit, and lock.
3. Survive an audit: for any locked number, prove what arrived, when, from where, who touched it, and which emission factor turned it into kg CO₂e.
4. Support multiple client companies (tenants) on one database.
5. Categorize every activity under the GHG Protocol's Scope 1 / 2 / 3 taxonomy.

**Non-goals (called out so I don't accidentally over-engineer)**
- Real-time streaming ingest. Batch is fine; emissions data is monthly at best.
- Full emission-factor library. I ship a tiny seed set (DEFRA 2023 for a handful of categories) and the schema to extend it.
- A double-entry ledger style reconciliation. Activity rows are the unit of truth; corrections are versioned, not journaled.
- Org hierarchies (parent companies with sub-entities). One tenant = one reporting entity. Real-world clients have this; I list it in TRADEOFFS.md.

---

## 2. Entities, at a glance

```
Tenant ─┬─ Source ──── IngestionRun ──── RawRecord ──┐
        │                                            │
        ├─ Facility (plant / building / cost center) │
        │                                            ▼
        ├─ EmissionCategory (Scope 1/2/3 taxonomy)   │
        │       │                                    │
        │       └─ EmissionFactor (versioned)        │
        │                  │                         │
        │                  ▼                         │
        └────── EmissionActivity ◄───────────────────┘
                     │
                     ├─ ValidationFlag (many)
                     └─ AuditLogEntry (many, generic)
```

One short paragraph per box below.

### Tenant
The client company. Every other row in the database has `tenant_id` (see §3). A tenant has a name, a default reporting currency, and a default region (drives which emission factor library is preferred).

### Source
A configured data source for a tenant. Examples: "SAP Production – Fuel", "ConEd Portal – HQ Electricity", "Concur – Travel". A source has a `kind` enum (`sap_flatfile`, `utility_pdf`, `travel_api`) and an `adapter_config` JSONB blob (column mappings, plant-code lookup, meter ID, etc.). Adapter code is keyed off `kind`.

Why a row, not a hardcoded string: tenants have multiple instances of the same kind (two SAP systems, three utility accounts), each with its own config. The analyst needs to filter the dashboard by source.

### IngestionRun
One upload, one API pull, one PDF parse = one IngestionRun. Records `started_at`, `finished_at`, `status` (`pending`, `succeeded`, `partial`, `failed`), `triggered_by` (user FK), `file_name`, `row_count_received`, `row_count_normalized`, `row_count_failed`, and an `error_log` JSONB.

This is the analyst's anchor when something goes wrong: "the Tuesday upload" is a Run, not a vague set of rows.

### RawRecord
One row as it arrived from the source, **before any interpretation**. Stored as `payload JSONB`. Has FK to IngestionRun, a `row_hash` (sha256 of payload, used for dedupe), and a `source_row_ref` (line number for files, record ID for APIs — used in error messages so analysts can find it in the original file).

RawRecord is **immutable**. If an upload is re-run, new RawRecord rows are inserted; old ones stay. This is non-negotiable for audit: "what did we receive on 2025-04-12" must always be answerable.

### Facility
A plant, building, cost center, or other physical/organizational unit emissions are attributed to. Has tenant, name, type, region, and a `source_codes JSONB` mapping (e.g. `{"sap_plant": "DE01", "utility_account": "AC-9921"}`) used by adapters to resolve foreign keys without baking them into source-specific tables.

### EmissionCategory
The GHG taxonomy. Tree-shaped: `scope` (1/2/3), `category` (e.g. "Stationary Combustion", "Purchased Electricity", "Business Travel"), `subcategory` (e.g. "Diesel", "Grid Mix", "Air – Short-haul"). Seeded with the GHG Protocol categories; clients can add custom subcategories but not custom scopes.

Why a table and not an enum: categories are versioned, regional (US EPA categorizes some refrigerants differently than DEFRA), and auditors need the taxonomy itself to be traceable — including which version was in effect when the row was approved.

### EmissionFactor
The kg-CO₂e-per-unit number. Has FK to EmissionCategory, `region` (ISO country or "GLOBAL"), `unit` (the activity unit it applies to — kWh, liter, km), `value_kgco2e_per_unit`, `valid_from`, `valid_to`, `source` (text — "DEFRA 2023", "EPA eGRID 2022"), and `version`.

**Critical detail**: an EmissionActivity does not just FK to a factor — it also snapshots the factor's value and source at the time of approval (`factor_value_snapshot`, `factor_source_snapshot`). Re-importing DEFRA next year will not silently change last year's locked numbers. This is the single most important design decision after multi-tenancy.

### EmissionActivity
The normalized row. The center of the model. See §4 below for the full field list.

### ValidationFlag
Per-EmissionActivity warnings raised at ingestion or on-demand: `MISSING_FACTOR`, `UNIT_UNRESOLVED`, `OUTLIER_VS_PRIOR_PERIOD`, `DUPLICATE_SUSPECTED`, `PLANT_CODE_UNMAPPED`, `BILLING_PERIOD_MISALIGNED`. Each has a `severity` (`info`, `warn`, `error`), a `message`, and a `dismissed_by` / `dismissed_at` (analyst can acknowledge a flag without resolving it — with a reason).

Flags are data, not code. Validators write them; the UI reads them. Adding a new flag does not require a migration.

### AuditLogEntry
Generic event log. `tenant_id`, `actor_id`, `entity_type`, `entity_id`, `action` (`created`, `updated`, `approved`, `flagged`, `locked`, `dismissed_flag`, `re_ingested`), `before JSONB`, `after JSONB`, `ts`, `reason` (optional free text — required for edits to approved rows).

Written explicitly in serializer `.save()` paths, not via DB triggers. Reason: the audit log should record *intent*, not *mutation* — a single API call can produce one log entry that summarizes a multi-row change.

---

## 3. Multi-tenancy

**Choice: shared schema, `tenant_id` FK on every tenant-scoped table.**

Implementation:
- A `TenantScopedModel` abstract base adds `tenant = ForeignKey(Tenant)` and a `default_manager` that *requires* a tenant filter or raises (caught in tests, not relied on for security).
- A middleware reads `X-Tenant-Id` from the request, validates it against the user's allowed tenants, and stashes it on the request. DRF viewsets filter by `request.tenant`.
- The Tenant model itself is the only non-tenant-scoped business table.

**Alternatives considered and rejected**:

| Approach | Why rejected |
| --- | --- |
| Schema-per-tenant (`django-tenants`) | Migrations multiply by tenant count. Painful at 50 tenants, fatal at 500. Cross-tenant analytics (which Breathe will eventually need for benchmarking) become a federated query. |
| Database-per-tenant | Same problems, worse. Justified only for hard data-isolation regulatory requirements. ESG data is sensitive but not HIPAA. |
| Row-level security (Postgres RLS) | A good addition *on top of* FK-based scoping, not a replacement. Out of scope for the prototype; noted in TRADEOFFS.md. |

The risk with FK-based tenancy is forgetting a filter and leaking data. Mitigations in this prototype: (a) the abstract manager, (b) every list endpoint test asserts cross-tenant isolation, (c) admin UI is not exposed.

---

## 4. EmissionActivity, in detail

This is the row an analyst reviews. It exists to be queried, edited, approved, and locked.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | UUIDs everywhere — activity IDs end up in URLs and audit trails. |
| `tenant_id` | FK | §3. |
| `raw_record_id` | FK (nullable) | Null for manually-entered rows; never null for ingested rows. |
| `source_id` | FK Source | Denormalized from RawRecord for query speed and so manual rows have a source too. |
| `ingestion_run_id` | FK IngestionRun (nullable) | Same reasoning. |
| `facility_id` | FK Facility (nullable) | Nullable because some travel activities aren't facility-attributable. |
| `category_id` | FK EmissionCategory | Determines Scope. Required. |
| `activity_date` | Date | When the emission actually occurred. For billing periods that span months, see §6. |
| `period_start`, `period_end` | Date (nullable) | Set for utility bills; otherwise null. `activity_date` is `period_start` by convention when a range exists. |
| `quantity_original` | Decimal(18,6) | The value as it came in. |
| `unit_original` | string | The unit as it came in (e.g. "kWh", "Gallons", "Litre", "L", "MWh"). Free-text on purpose. |
| `quantity_normalized` | Decimal(18,6) | Converted to the category's canonical unit. |
| `unit_normalized` | string (enum) | Canonical unit (e.g. "kWh", "L", "km", "kg"). |
| `conversion_factor` | Decimal | What we multiplied by. Stored so the conversion is reproducible without re-running the lookup. |
| `factor_id` | FK EmissionFactor (nullable) | Null if no factor resolved → ValidationFlag.MISSING_FACTOR raised. |
| `factor_value_snapshot` | Decimal | Snapshot — see §5. |
| `factor_source_snapshot` | string | Snapshot — "DEFRA 2023 v1.1". |
| `emissions_kgco2e` | Decimal | `quantity_normalized * factor_value_snapshot`. Computed at approval, not on the fly. |
| `status` | enum | `pending` → `approved` → `locked`, with `flagged` as a side state. See §7. |
| `notes` | text | Analyst annotations. |
| `created_at`, `updated_at` | timestamps | |
| `approved_at`, `approved_by` | timestamp + FK user | |
| `locked_at`, `locked_by` | timestamp + FK user | Set once; row becomes immutable. |

**Why not a polymorphic per-source table** (one table for fuel, one for electricity, one for travel)? Three reasons:
1. The review dashboard wants one query: "all unapproved rows for tenant X in May". Polymorphic = UNIONs forever.
2. The set of "interesting" fields is small and overlaps heavily (date, quantity, unit, facility). Source-specific detail belongs in `raw_record.payload`, which is right there via FK.
3. The price of unification is two columns (`quantity_original`, `unit_original`) and a JSONB on the side. The price of polymorphism is every analytics query, every export, and every UI table getting source-aware.

**Why `quantity_original` + `quantity_normalized` instead of just normalized**: auditors ask "but the bill said 1,247 kWh — where is that number in your system?" The answer must be a column, not a JSON path into the raw payload.

---

## 5. Source-of-truth tracking and factor snapshotting

Three pieces of provenance live on every activity row:

1. **What arrived**: `raw_record_id` → the immutable JSON payload, plus `source_row_ref` for "line 47 of `sap_fuel_2025_04.csv`".
2. **What we did to it**: `conversion_factor`, `unit_original` → `unit_normalized`, and the `IngestionRun` it came in on. Re-ingestion creates a new activity row linked to a new raw record; the old one is *superseded* (status moves to `superseded`), not deleted. The new row has `supersedes_id` FK to the old one.
3. **Which factor applied**: `factor_id` for the live lookup, plus `factor_value_snapshot` and `factor_source_snapshot` frozen at approval.

The snapshot pattern matters because emission factors are revised yearly. If we only stored `factor_id`, then importing DEFRA 2024 would silently change the kg-CO₂e of every 2023 activity. Snapshots make the historical number reproducible even if the upstream factor table is wiped.

---

## 6. Units and billing periods

**Unit normalization** is a pair: a free-text `unit_original` (what came in, including misspellings like "Litre") and an enum `unit_normalized`. A `UnitAlias` table maps strings to canonical units (`"Litre" → "L"`, `"Gallon" → "gal_us"`, `"MWh" → "kWh"` with factor 1000). Unresolved aliases raise `UNIT_UNRESOLVED` and the row lands in the analyst's flagged queue.

Canonical units per category: kWh (electricity), L (liquid fuel), kg (solid fuel), km (distance), nights (hotel), kg (refrigerants). Choices were pragmatic — pick whatever the dominant emission-factor library uses to avoid a conversion at calculation time.

**Billing periods**: utility bills cover ~30-day windows that rarely align with calendar months. The model stores `period_start` and `period_end`; the analyst chooses how to attribute it. Two strategies are supported and both are explicit in the data:
- **Bill date attribution**: `activity_date = period_end`. Simple. Used by default.
- **Pro-rata split**: a single bill produces multiple activity rows, one per calendar month it touches, with `quantity_original` divided by day-count. The split rows share a `parent_activity_id` so the original bill is recoverable.

This is the kind of thing analysts argue about. The model doesn't pick a winner; it lets both exist and tracks which one was used.

---

## 7. Status state machine

```
                ┌─────────┐
   ingestion ──▶│ pending │──┬──▶ approved ──▶ locked
                └─────────┘  │       ▲
                     ▲       │       │
                     │       ▼       │
                     │   flagged ────┘   (analyst dismisses or resolves the flag)
                     │
                     └── re-ingestion → new row, old row → superseded
```

Rules:
- `pending → approved`: requires no error-severity flags (warns are OK if dismissed).
- `approved → locked`: one-way, writes to AuditLog, sets `locked_at` / `locked_by`. Subsequent edits raise `LockedRowError`.
- `approved → pending`: allowed (analyst un-approves). Logged.
- `locked → anything`: never. To correct a locked row, the analyst issues an *adjustment* — a new activity with `adjusts_id` FK and a required `reason`. The original locked row stays.
- `re-ingestion`: when an IngestionRun produces a row whose `row_hash` matches an existing un-locked activity's raw record, the existing activity is updated. If the existing is locked, a new activity is created with `supersedes_id` and a `DUPLICATE_OF_LOCKED` flag for the analyst to reconcile.

Status is an enum on the row, not a separate `ActivityStatusHistory` table. Status changes are reconstructable from `AuditLogEntry`.

---

## 8. Audit trail

`AuditLogEntry` is generic (entity_type + entity_id), not per-table. One reason: a single user action ("bulk approve 47 rows") should produce one entry with a list, not 47 entries. Per-table audit tables make this awkward.

Logged actions, minimum: `created`, `updated`, `approved`, `unapproved`, `flagged`, `flag_dismissed`, `locked`, `re_ingested`, `superseded`, `adjusted`. Each carries `before` and `after` JSONB snapshots of the fields that changed (not the whole row — keeps the log compact).

The audit log itself is append-only at the application layer (no UPDATE/DELETE endpoints). A future hardening step is a Postgres revocation of UPDATE/DELETE for the app user on this table; noted in TRADEOFFS.md.

---

## 9. What the schema deliberately does not model

- **Org hierarchies.** Tenants are flat. Real clients have parent/sub-entity rollups. Adding it later is a `parent_id` on Tenant and a recursive CTE in the rollup query.
- **Currency.** Procurement rows have amounts; we store them but don't convert. Spend-based emission factors need this; out of scope.
- **Document storage.** PDFs and source files are processed in-memory and the parsed payload is kept in RawRecord. The original blob is not retained. In production: S3 + a `source_blob_url` on IngestionRun. Noted in TRADEOFFS.md.
- **User roles beyond analyst.** No reviewer/approver split, no client-side users. Single role: analyst, with one seeded user per tenant. Auth is a hardcoded header for the demo.
- **Soft delete.** Activities are not deletable from the UI. Wrong rows are flagged and superseded. This is a feature.
- **Scope 3 categories 1–15 in full.** Only Cat 6 (Business Travel) and a token Cat 1 (Purchased Goods, via SAP procurement) are modeled. The other 13 are valid `EmissionCategory` rows the schema supports but no adapter produces.

---

## 10. Things I would ask the PM

These are in DECISIONS.md too but the model-shaped ones land here:

1. **Restatement policy.** When a locked row turns out to be wrong post-audit, do we issue an adjustment (current design) or unlock-and-edit with a comment? Different jurisdictions / frameworks have different rules.
2. **Tenant-vs-source priority for factor selection.** If a tenant has a region preference but a source's data implies a different region (a US tenant's German plant), which wins? Current code prefers facility region → tenant region → "GLOBAL".
3. **Outlier definition.** I flag rows whose quantity is >3σ from the prior 6 months for the same (facility, category). Should this be configurable per tenant? Probably yes; not built.
4. **Procurement scope.** SAP procurement covers a lot. Am I expected to do spend-based Cat 1 (£ × factor) or activity-based (kg of cement × factor)? I chose activity-based for the subset I handle and ignored spend; the PM should confirm.
