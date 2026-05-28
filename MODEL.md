# Data Model

The hard part of carbon accounting isn't math. It's being able to defend a number to an auditor six months later. Every choice below is in service of that.

## Goals

- Ingest from three messy sources without losing anything.
- Let an analyst review, edit, approve, lock.
- For any locked number, prove: what arrived, when, from where, who touched it, what factor was used.
- Multi-tenant on one DB.
- GHG Scope 1/2/3 taxonomy.

## Non-goals (so I don't over-build)

- Realtime streaming — emissions data is monthly.
- A full emission-factor library — I ship a small seed set.
- Org hierarchies (parent → subsidiary). Flat tenants.
- Double-entry ledger reconciliation. Corrections are new rows, not journal flips.

## Entities

```
Tenant ─┬─ Source ── IngestionRun ── RawRecord ──┐
        ├─ Facility                              │
        ├─ EmissionCategory ── EmissionFactor    │
        │                          │             ▼
        └────── EmissionActivity ◄───────────────┘
                     ├─ ValidationFlag (many, dismissible)
                     └─ AuditLogEntry (many, generic)
```

**Tenant** — the client company. Every business row has `tenant_id`.

**Source** — a configured input for a tenant ("SAP Production – Fuel", "ConEd HQ Electricity"). Has `kind` (`sap_flatfile` / `utility_pdf` / `travel_api`) and `adapter_config` JSONB (column maps, pull fixtures).

**IngestionRun** — one upload / pull / paste = one Run. Holds counts, status, and the per-row error log.

**RawRecord** — what arrived, exactly. Immutable JSONB. Re-ingest writes new rows; old ones stay.

**Facility** — plant / building / meter. `source_codes` JSONB maps to per-source identifiers (`{"sap_plant": "US01", "utility_meter": "MTR-NWK-0042"}`) so adapters resolve foreign keys without baking them into source-specific tables.

**EmissionCategory** — a row, not an enum. Scope + category + subcategory + canonical unit. Auditors need the taxonomy itself to be traceable, including which version was in effect when the row was approved.

**EmissionFactor** — kg-CO₂e per unit. Region, valid_from/to, source ("DEFRA 2023"), version.

**EmissionActivity** — the row analysts review. The center of everything.

**ValidationFlag** — soft signals raised by validators (`MISSING_FACTOR`, `UNIT_UNRESOLVED`, `OUTLIER_VS_PRIOR_PERIOD`, `DUPLICATE_SUSPECTED`, `PLANT_CODE_UNMAPPED`, `BILLING_PERIOD_MISALIGNED`). Each is dismissible with a required reason. Adding a new flag = no migration.

**AuditLogEntry** — generic event log (entity_type + entity_id). One write per analyst *intent*, not per row mutation. "Bulk approve 47 rows" = one entry with a list, not 47.

## One activity table, not three

Polymorphism (FuelActivity / ElectricityActivity / TravelActivity) seems clean until the review dashboard wants "every unapproved row, any source, this period." That's a UNION forever. Field overlap is high (date, quantity, unit, facility); source-specific detail lives in `RawRecord.payload` (JSONB) which is one FK away. Cost of unification: two columns. Cost of splitting: every analytics query.

## Multi-tenancy

`tenant_id` FK on every tenant-scoped row. Middleware resolves tenant from the authenticated session. Standard B2B pattern. Schema-per-tenant (`django-tenants`) multiplies migrations by tenant count; cross-tenant analytics becomes federated. Database-per-tenant only makes sense for hard regulatory isolation, which ESG data doesn't have.

Leakage mitigation: abstract manager that requires a tenant filter, plus isolation tests on every list endpoint.

## EmissionActivity fields that matter

The fields that don't appear in most ESG schemas but should:

| Field | Why |
|---|---|
| `quantity_original` + `unit_original` | Auditor asks "but the bill said 1,247 kWh — where is that in your system?" The answer must be a column, not a JSON path. |
| `quantity_normalized` + `unit_normalized` + `conversion_factor` | What we did to it. Reproducible without re-running the lookup. |
| `factor_value_snapshot` + `factor_source_snapshot` | The single most important design decision after multi-tenancy. See below. |
| `raw_record_id` | Provenance back to the immutable payload. |
| `supersedes_id` / `adjusts_id` | Re-ingestion produces a new row that supersedes the old. Corrections to locked rows are *adjustments* — both old and new rows stay. |
| `region_override` (computed at ingestion) | A London hotel uses the GB factor regardless of the tenant's default region. Without this, hotels would all use the tenant's region — wrong for travel. |

## Factor snapshotting (the load-bearing decision)

Each activity stores the live `factor_id` FK *and* `factor_value_snapshot` + `factor_source_snapshot` frozen at approval. When DEFRA publishes 2024 numbers, importing them does *not* silently change last year's locked emissions. Cost: ~24 bytes per row. Benefit: the system is audit-defensible.

If you only store the FK, then upgrading the factor library retroactively rewrites history. Auditors do not tolerate that.

## Units and billing periods

`unit_original` is free text (whatever arrived — "Litre", "LTR", "L", "GAL", "MWh"). `unit_normalized` is the enum we converted to. A `UNIT_ALIASES` dict maps strings to canonical; unresolved aliases raise `UNIT_UNRESOLVED` and the row goes to the analyst's flagged queue.

Utility bills cover ~30-day windows that don't align with calendar months. The model stores `period_start` and `period_end` and the analyst chooses attribution. Two strategies are explicit in data (bill-date and pro-rata split with `parent_activity_id`), not picked by the schema.

## Status state machine

```
ingestion → pending ─┬─→ approved ─→ locked
                     │      ▲
                     └─→ flagged ──┘   (analyst dismisses or resolves)
                     │
                     └─→ rejected   (analyst's "don't carry forward")
                     
re-ingestion → new row, old row → superseded
```

Rules:
- `pending → approved` requires no un-dismissed error flags.
- `approved → locked` is one-way; subsequent edits raise `LockedRowError`.
- `pending → rejected` requires a reason and is logged.
- Corrections to `locked` rows = new row with `adjusts_id`. Original stays.

Status lives on the row, not in a history table. History is reconstructable from `AuditLogEntry`.

## Audit log

Generic (`entity_type` + `entity_id`), not per-table. One reason: a single analyst action produces one entry, not N. `before`/`after` JSONB store only the fields that changed.

Written at the application layer in the service functions, not via DB triggers. The log records *intent* ("bulk approve") not *mutation* ("UPDATE x SET status='approved' WHERE id=...").

## What the schema deliberately doesn't model

- Org hierarchies (parent_id + recursive CTE when needed)
- Currency conversion for procurement (raw amount kept; no spend-based factors)
- Original file blobs (parsed payload kept; PDF/CSV bytes not retained — see TRADEOFFS.md)
- User roles beyond a single analyst role
- Soft delete (rejection is the supported "do not carry forward" path)
- Scope 3 categories beyond Cat 1 (procurement) and Cat 6 (travel)
