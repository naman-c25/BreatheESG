# Sources

Per source: what format I picked, what I learned researching, what the sample data is, and what would break in production.

---

## 1. SAP — fuel & procurement

### Format I picked: flat-file (CSV or XLSX) from SE16N

| Mode | Why not |
|---|---|
| IDoc (XML) | Needs SAP PI/PO middleware. Not realistic for an intern prototype. |
| BAPI / RFC | Live SAP connection + NetWeaver SDK + service creds. None of that exists day one. |
| OData | Needs SAP Gateway enabled. Mostly newer S/4; most mid-market clients still run ECC. |
| **Flat file** | **A procurement analyst can run SE16N → Local file → CSV or Excel and email it. Genuinely how day-one onboarding data moves.** |

### What I learned

- **German is everywhere in EU-rooted SAP installs.** Columns come back as `Buchungsdatum`, `Werk`, `Menge`, `Basismengeneinheit`. My adapter accepts German and English via a synonym map.
- **Decimal commas + dot thousands.** `1.247,500` means 1247.5. Trusting `Decimal(text)` silently 1000× corrupts the value. I parse DE format explicitly.
- **Three date formats coexist**: `DD.MM.YYYY` (DE), `YYYYMMDD` (technical exports), `YYYY-MM-DD` (newer S/4). Adapter tries all three.
- **Plant codes are short alphanumerics and mean nothing without a lookup.** `US01`, `DE01`. The lookup lives in the client's head until onboarding extracts it. `Facility.source_codes` stores `{"sap_plant": "US01"}`.
- **Movement type drives meaning.** 261 = goods issue (fuel consumption → Scope 1). 101 = goods receipt (procurement → Scope 3). 262 = reversal of 261. **Most adapters ignore 262, which means double-counting.** Mine pairs 262 with its 261 by `Storno-Belegnummer` and drops both, logging the cancellation for audit.
- **`STK` (Stück / pieces) is not a measurable quantity.** Refuse it loudly; never coerce.
- **Encodings**: utf-8 → chardet auto-detect → latin-1 fallback. Windows SAP exports are commonly cp1252.

### Sample: [sap_fuel_2025_04.csv](backend/sample_data/sap_fuel_2025_04.csv)

12 rows, every one deliberate:
- 5 clean diesel rows across two plants (US01, DE01) — happy path.
- 1 petrol, 1 natural gas in M³ (tests M³→L conversion + missing-NG-factor flag).
- 1 procurement row (steel, KG, Scope 3).
- **1 reversal pair** — doc `4900001002` (issue) + doc `4900001099` (262 with `Storno-Belegnummer = 4900001002`). Adapter nets them and logs the cancellation.
- **1 row with YYYYMMDD date** — tests the multi-format date parser.
- 1 unknown plant `XX99` → `PLANT_CODE_UNMAPPED`.
- 1 `STK` row → rejected.
- 1 unrealistic 45,000 L row → `OUTLIER_VS_PRIOR_PERIOD` (against synthesized Jan–Mar history).

Plus [sap_fuel_2025_05.xlsx](backend/sample_data/sap_fuel_2025_05.xlsx) — proves the Excel path. SE16N exports to Excel almost as often as to CSV.

### What would break in production

- **Cross-file 262 lookups** — my reversal netting works only within one file. A 262 in May cancelling a 261 from April is logged as "references unknown issue doc." Real fix: look up `Belegnummer` across all prior runs and mark the original activity as `superseded`.
- **Per-material UOMs** (`MARM` table) — I assume base UOM. An export reporting `Menge in Bestellmengeneinheit` would silently use the wrong unit.
- **Multiple posting periods on one export** — I accept anything dated; a row from last fiscal year would land in current totals. Real fix: a period gate on the run.
- **Reversal documents** — I handle them. Most adapters don't. Putting it in the "would break" column would be wrong now.

---

## 2. Utility — electricity

### Format I picked: PDF bill upload

| Mode | Why not |
|---|---|
| Green Button (ESPI XML) | Mandated for residential in some US states. Commercial coverage is poor. |
| Utility API / aggregator (Arcadia, Urjanet) | Per-utility custom integration; not credibly representable in a demo. |
| Portal CSV scrape | Real but the CSV schema is utility-specific. No standard. |
| **PDF bill** | **Universal. Every commercial customer has one every month. Also the artifact auditors expect to see in the workpapers.** |

### What I learned

- **Bill layouts are utility-specific.** ConEd, PG&E, EDF UK share zero structure. My adapter ships one (ConEd-shaped) layout; real deployment is per-utility layout configs or LLM extraction.
- **Service period drives attribution, not the bill date.** A bill dated 2025-05-04 might cover April. Using the wrong one misstates the period by ~a month.
- **Periods rarely align with calendar months.** Most are ~30 days offset (the 17th to the 16th). The model stores `period_start` and `period_end` and offers two attribution strategies (bill-date and pro-rata split).
- **Commercial bills sub-meter.** One main + HVAC + lighting. A single-meter parser hides this and produces one row when it should produce N.
- **`kVAh` (apparent power) ≠ `kWh`.** Treating it as kWh inflates emissions by 1/power-factor (10–25%). Refuse it.
- **Demand charges (kW) are billing units, not emission drivers.** Capture but don't compute on.
- **Tariff structure (peak/off-peak) matters for cost, modestly for emissions in location-based reporting.** I capture line items in the raw payload but emit one row per meter using the total — splitting into per-tariff rows needs time-of-use grid factors I don't ship.

### Sample: [utility_bill_2025_04.pdf](backend/sample_data/utility_bill_2025_04.pdf)

Generated by `python manage.py generate_sample_pdf`. Three meters on one bill:

- **MTR-NWK-0042** — main, 142,318 kWh total with Peak / Off-Peak / Shoulder breakdown + 412 kW demand. Maps to seeded Newark facility.
- **MTR-NWK-HVAC-01** — sub-meter, 38,710 kWh, no tariff split. Not in the facility lookup → `PLANT_CODE_UNMAPPED`.
- **MTR-NWK-REAC-01** — 12,400 **kVAh**. Adapter rejects with explanation.

### What would break in production

- **Layout drift** — utilities change templates every couple of years. Regex breaks silently. Real fix: per-tenant layout configs + "couldn't extract" flag.
- **Multi-page bills** — I concatenate all page text; a detail page could match the summary regex.
- **Estimated vs actual reads** — utilities mark some readings ESTIMATED. I don't extract this; should carry a flag.
- **Region inference** — I assume USD and US grid. A UK bill (DD/MM/YYYY, £) wouldn't match the period regex at all.
- **Scanned PDFs** — `pdfplumber` returns nothing on images. OCR fallback needed.

---

## 3. Corporate travel — flights, hotels, ground, rail

### Format I picked: JSON upload (Concur Travel Booking API v4 shape)

| Mode | Why not |
|---|---|
| Direct Concur OAuth pull | Per-tenant OAuth + admin consent + pagination + rate limits. 4-week integration. |
| CSV export | Concur's CSV loses the nested structure (segments, stays) that the JSON preserves. |
| **JSON upload** | **What a travel team actually sends when asked for "the Concur dump." Demo has a fixture-backed "Pull" button to demonstrate where the real API call would slot in.** |

### What I learned

- **Concur shape**: top-level `bookings[]`, each with `airSegments[]`, `hotelStays[]`, `carRentals[]`, `railSegments[]`. Cabin in air, country on hotel, distance + unit on car.
- **Distance is rarely included on air segments.** Concur gives IATA codes. Industry practice: great-circle from a coordinate table (OpenFlights, ~7,500 airports). I seed 8 for the demo. Great-circle is within ~3% of actual flown distance — fine for category 6 reporting.
- **Cabin class matters a lot.** DEFRA business is ~2.9× economy per km. Ignoring it underreports premium-cabin emissions by ~3×. I put cabin in the subcategory (`Air – Long-haul Business`) and seed a factor per (haul, cabin) combination.
- **Hotel emissions are country-specific, not star-rating-specific (mostly).** Cornell HSB 2023: US ~30, GB ~10, DE ~20, IN ~40, SG ~66 kg/night. A trip with stops in different countries should get different factors. My adapter reads `country` from the stay and passes it as a `region_override` on the row; the factor resolver tries that first, then tenant default, then GLOBAL.
- **Cancelled bookings are in the export.** Real Concur returns both ticketed and cancelled. Silent processing over-reports. I filter at both booking and segment level.
- **Rail matters for UK/EU clients.** Ignoring `railSegments[]` is a real blind spot for any client with significant European travel.

### Sample: [travel_concur_2025_04.json](backend/sample_data/travel_concur_2025_04.json)

5 bookings:
- **BK-0001** — JFK ↔ LHR economy + 7-night London hotel + Heathrow rail leg (`country: GB`).
- **BK-0002** — SFO → SIN **business class** + 4-night Singapore hotel (`country: SG`, ~6× GB factor) + 142 km car.
- **BK-0003** — BOM → DEL → FRA multi-leg with mixed cabins (economy + premium economy) + one unknown airport (`ZZZ`, surfaces error) + Delhi hotel + 215 mi rental (mi→km).
- **BK-0004** — `status: CANCELLED` with a business JFK→LHR + Savoy hotel inside. Must produce zero rows. Without filtering this would leak ~3,000 kg CO₂e.
- **BK-0005** — Two Berlin↔Frankfurt rail segments, return marked `status: CANCELLED`. Outbound emits, return doesn't.

### What would break in production

- **Live Concur pull** — OAuth 2.0, pagination, rate limits, refresh tokens. The "Pull" button in the demo reads a fixture; real version is a new HTTP client per source kind.
- **Multi-traveler bookings** — meeting trips with shared bookings. Concur's JSON varies; naive per-traveler split is wrong.
- **Ride-hail / taxi** — lives in Concur Expense, not Travel. Different shape, different ingest path.
- **Hotel star rating** — Cornell HSB has it as a dimension on top of country; I use country only.
- **Airline-published flown distance** — when present, more accurate than great-circle (ATC routing on Atlantic NAT tracks adds a few %). My code prefers it if present in the payload; sample data doesn't include it.
