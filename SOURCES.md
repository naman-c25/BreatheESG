# Sources

For each of the three source types: what real-world format I researched, what I learned, what my sample data looks like and *why*, and what would break in a real deployment.

I researched each before designing the adapter. Where my conclusions came from a specific document or vendor, I name it.

---

## 1. SAP — fuel & procurement

### What real-world format I researched

SAP exposes data in four meaningful ways:

| Mode | What it is | Why ruled out for this build |
| --- | --- | --- |
| **IDoc** | XML message, classically the EDI transport for batch movement of FI/MM/SD data. | Requires SAP middleware (PI/PO or BTP Integration Suite) on the client side and an EDI-aware parser on ours. Not realistic for an intern prototype. |
| **BAPI / RFC** | Live remote function call into ECC or S/4. | Needs an SAP NetWeaver RFC SDK (`pyrfc`), service-user credentials, and the client to whitelist our IP. None of that exists day one. |
| **OData** | REST endpoints exposed by the SAP Gateway, common in newer S/4 deployments. | Real, modern, but assumes the client has Gateway enabled and the right service catalog activated. Still mostly newer S/4 — most mid-market clients run ECC. |
| **Flat-file export from SE16N or a Z-report** | A user runs a transaction (`SE16N` for table browsing, or a customer-built `Z*` report) and exports the result to a CSV or Excel file. | **This is what I built for.** It is genuinely how a procurement analyst sends data to a third party when nothing else is wired up. |

### What I learned

- **German is everywhere.** SAP ECC in EU-rooted multinationals very often runs with German as the system language for power users. Exports carry German column headers (`Buchungsdatum`, `Werk`, `Menge`, `Basismengeneinheit`). My adapter accepts both via a `HEADER_SYNONYMS` map.
- **Decimal commas, dot thousands.** German number formatting: `1.247,500` means 1,247.5. I parse this explicitly; trusting `Decimal(text)` silently corrupts the value.
- **Dates are `DD.MM.YYYY`.** Never `YYYY-MM-DD` in a German export.
- **Plant codes are short alphanumerics.** `US01`, `DE01`. They mean nothing without a lookup table — and the lookup table lives in the client's head until onboarding extracts it. My `Facility.source_codes` JSONB stores `{"sap_plant": "US01"}` so the adapter can resolve plant code → facility per tenant.
- **Movement type drives meaning.** SAP's `MSEG.BWART` (Bewegungsart) — 261 is "goods issue to cost center" (consumption — Scope 1 if it's fuel), 101 is "goods receipt from PO" (procurement — Scope 3 Cat 1), 311 is internal transfer (no emission impact). My adapter accepts 261/201/101 and refuses the rest. The movement-type-to-category logic lives in a Python dict; a real deployment moves it to a per-tenant DB table because clients customize it.
- **Material codes are client-specific.** Two clients have entirely different conventions. I use a prefix match (`FUEL-DSL`, `FUEL-PET`, `FUEL-NG`, `PROC-*`) which is realistic for clients that adopt a numbering convention but minimal enough to demo.
- **Unit codes are SAP-internal.** `LTR` not `L`, `STK` (Stück / pieces — an unmeasurable count, must be rejected), `M3`, `KG`, `GAL`. My unit alias table covers these.

### What my sample data looks like and why

[sap_fuel_2025_04.csv](backend/sample_data/sap_fuel_2025_04.csv) — 11 rows, pipe-delimited, German headers, decimal commas, DD.MM.YYYY dates. Each row is deliberate:

- 5 clean diesel consumption rows across two plants (US01, DE01) → normal happy path.
- 1 petrol row → tests Scope 1 Mobile Combustion path.
- 1 natural-gas row in `M3` → exercises the M³ → L unit conversion *and* the fact that NG factor is per kWh not per L (which the resolver will fail to match — `MISSING_FACTOR` flag — which is *intended* because real onboarding needs a NG-volume-to-energy conversion the prototype doesn't model).
- 1 procurement row (`PROC-STL-A36`, KG) → Scope 3 Cat 1 path.
- 1 row with unknown plant `XX99` → fires `PLANT_CODE_UNMAPPED` warning.
- 1 row with unit `STK` → adapter rejects with a clear "not measurable" error; appears in the run's error_log, never reaches an EmissionActivity.
- 1 unrealistically-large diesel row (45,000 L) → after a few months of data, fires `OUTLIER_VS_PRIOR_PERIOD`. On a fresh seed, the outlier check is a no-op (not enough history) — which is correct behavior and itself worth demonstrating.

### What would break in a real deployment

- **Reversal documents.** SAP allows posting a movement and then reversing it (movement type 262 reverses 261). I don't model net positions — if a 261 is reversed by a 262, both arrive as separate rows and I'd double-count. Real fix: pair them by reference document and net the quantities.
- **Multiple posting periods on one export.** A user can export *anything*. If they include a row dated last fiscal year, my code will accept it. Real fix: a `PeriodScope` on the run or an analyst-set period gate.
- **Unit of measure per material.** SAP keeps multiple UOMs per material (`MARM` table). An export may report "Menge in Bestellmengeneinheit" which is a different unit than the base UOM. I assume the base UOM; documented as a known gap.
- **CSV vs. Excel exports.** Power users tend to export to Excel, not CSV. Real fix: accept `.xlsx` via `openpyxl`. Trivial addition.
- **Encoding.** I try `utf-8-sig` then fall back to `latin-1`. Cyrillic/Asian-script exports would need `cp1251`/`cp932` detection.

---

## 2. Utility — electricity

### What real-world format I researched

| Mode | What it is | Why ruled out / chosen |
| --- | --- | --- |
| **Green Button (ESPI XML)** | An IEEE/NIST standard for utility usage data, mandated for residential in some US states. | Commercial-account coverage is poor. Most commercial customers cannot self-serve a Green Button download. Ruled out. |
| **Utility API** | Some utilities (ComEd, NYSEG, ConEd partly) have programmatic APIs, often through a third-party aggregator like Arcadia or Urjanet. | Per-utility custom integration; the demo cannot represent "I integrated with one" credibly. |
| **Portal CSV scrape** | Login to the utility website and export usage to CSV. | Real, but the CSV column set is utility-specific and many portals require puppeteer-style automation that breaks weekly. |
| **PDF bill upload** | Facilities team forwards the PDF bill they get monthly. | **Chosen.** Universal, monthly, exists for every account. Also the artifact auditors actually want to see in the workpapers. |

### What I learned

- **Bill layouts vary wildly across utilities.** A ConEd commercial bill, a PG&E commercial bill, and an EDF UK bill share *zero* common structure beyond "there's a number with kWh next to it somewhere." My adapter ships with regex for a single (ConEd-inspired) layout. Real deployment: per-utility adapters or LLM-assisted extraction.
- **Service period is the date that matters, not the bill date.** A bill dated 2025-05-04 might cover 2025-04-01 → 2025-04-30. Using bill date for attribution misstates the period by ~a month.
- **Periods rarely align with calendar months.** Most are ~30 days but offset (the 17th to the 16th). I store `period_start` and `period_end`; the model documents two attribution strategies (bill-date and pro-rata split). The UI ships bill-date only; pro-rata is schema-supported, not UI-built.
- **Meter ID is the key for facility resolution.** A site can have multiple meters (sub-metering, HVAC vs. lighting). Each meter ↔ facility mapping lives in `Facility.source_codes["utility_meter"]`.
- **Demand charges (kW) are not energy and not directly an emission driver.** They appear on the bill; I ignore them. Real Scope 2 reporting only cares about kWh.
- **Tariff structures (time-of-use, real-time pricing) matter for *cost* but not for *emissions* in market-based reporting — and only modestly in location-based reporting.** I do not model tariff. This is a known gap for clients in markets with significant intra-day grid carbon variation.

### What my sample data looks like and why

[utility_bill_2025_04.pdf](backend/sample_data/utility_bill_2025_04.pdf) — generated by `python manage.py generate_sample_pdf` (it's a generated PDF so I can show the parsing path end-to-end rather than shipping a binary I can't justify line-by-line).

Layout deliberately:
- A `Service Period: MM/DD/YYYY - MM/DD/YYYY` line in the format I parse.
- A `Total Energy Usage: 142,318 kWh` line with comma thousands.
- A `Meter ID: MTR-NWK-0042` line matching the seeded facility's `source_codes`.

Why 142,318 kWh: a realistic April-month usage for a single small commercial site at ~5kW average load. Not so large it looks contrived, not so small it looks residential.

### What would break in a real deployment

- **Layout drift.** The utility changes their bill template (which they do every couple of years). My regex breaks silently. Real fix: per-tenant per-utility layout config, plus a "we couldn't extract" flag the analyst resolves manually.
- **Multi-page bills.** I concatenate all page text; a long bill with detail pages could match the wrong values. Real fix: per-page extraction with confidence scoring.
- **Multi-meter bills.** Some utility accounts bill multiple meters on one statement. My adapter extracts the first match. Real fix: iterate matches and create one row per meter.
- **Estimated vs. actual reads.** Utilities mark some readings as estimated when the meter wasn't physically read. The bill text says "ESTIMATED". I don't extract this. For audit, estimated reads should carry a flag.
- **Currency / region inference.** I assume USD and US grid. A UK bill has `Period: DD/MM/YYYY` and £, my regex wouldn't even match the period line.
- **PDF as image (scanned).** If the bill is a scan, `pdfplumber` returns no text. Real fix: OCR (Textract / Tesseract) fallback when extracted text is empty.

---

## 3. Corporate travel — flights, hotels, ground

### What real-world format I researched

Concur (SAP Concur) is the dominant corporate travel platform in the US enterprise market. Their **Travel Booking API v4** is the canonical source of pre-trip booking data. Key shape from their docs:

- Top-level `bookings[]` array.
- Each booking has `traveler` (employee email/ID), and arrays for `airSegments[]`, `hotelStays[]`, `carRentals[]`.
- Air segments include `from` / `to` IATA codes, `cabin`, `departureDate`, and `ticketNumber`. **Distance is usually not included.**
- Hotel stays include `propertyName`, `checkIn`, `checkOut`, `nights`.
- Car rentals include `vendor`, `pickupDate`, `returnDate`, `distance`, `distanceUnit`.

Navan (formerly TripActions) has a similar nested structure with slightly different field names. TravelPerk is flatter (one row per leg).

I picked the Concur shape because it's the most likely real input.

### What I learned

- **Categories drive factors.** Air short-haul vs. long-haul have different per-km factors. DEFRA splits at 3,700 km. Cabin class (economy/premium/business/first) varies the factor by 1.5–4×. My adapter classifies short vs. long; cabin class is captured in the raw payload but not yet differentiated in the factor lookup (because adding 12 air-class factors gives the demo nothing the architecture doesn't already prove).
- **Distance is the hard part.** Concur often doesn't provide it. The standard practice is to compute great-circle distance from airport coordinates. I ship a tiny seeded airport coordinate table (8 airports — enough to demo); a real deployment uses OpenFlights (~7,500 airports, free) or paid IATA data.
- **Hotels are nights × kg-CO₂e-per-night.** Cornell publishes an index, DEFRA publishes one per country. Region matters more than star rating in practice.
- **Ground is messy.** "Car rental" might be 200km of actual driving or might be a car parked for 4 days that was never moved. Concur reports the distance the renter declared at return; treat as best-available.
- **Cancelled bookings are in the export.** Real Concur returns cancelled and ticketed bookings. Filtering them is the consumer's job. I don't currently; that would over-report. Documented as a gap.

### What my sample data looks like and why

[travel_concur_2025_04.json](backend/sample_data/travel_concur_2025_04.json) — 3 bookings, deliberately:

- **BK-2025-04-0001** — JFK ↔ LHR round-trip economy, 7-night London hotel. Both legs short-haul-classified-as-long-haul (NYC-London is ~5,500 km, long-haul). Tests great-circle for transatlantic + the long-haul factor lookup.
- **BK-2025-04-0002** — SFO → SIN one-way business + Marina Bay hotel + 142km car rental in km. Tests long-haul + ground in km. (Cabin class is captured in payload but not factor-differentiated yet.)
- **BK-2025-04-0003** — BOM → DEL → FRA multi-leg with one segment to an unknown airport code `ZZZ`. Tests:
  - Multi-segment parsing.
  - The unknown-airport error path (one segment fails; others succeed).
  - A ground-in-miles row (Sixt rental, `distanceUnit: "mi"`) → exercises mile→km unit conversion.

### What would break in a real deployment

- **OAuth / direct pull.** Real Concur integration is OAuth 2.0 per client tenant with admin consent. My demo accepts the JSON export; switching to a pull is a new adapter that fetches paginated data and emits the same NormalizedRow stream.
- **Cabin class factor differentiation.** Not done. Underreports premium-cabin emissions by ~3×.
- **Cancelled bookings.** Not filtered. Would over-report. Real fix: skip rows with `status != "TICKETED"` or equivalent.
- **Rail.** Concur reports rail under `railSegments[]`. Not handled. UK/EU clients with significant rail travel would have a meaningful blind spot.
- **Ride-hail / taxi from expense reports.** Travel platforms don't capture this; it lives in Concur Expense or Coupa. Different data shape, different ingest path — separate from this adapter.
- **Multi-traveler bookings.** A meeting booking with 5 attendees on the same trip — the JSON shape varies. Naively splitting per-traveler is wrong; per-segment per-traveler attribution is correct.
- **Distance accuracy.** Great-circle vs. actual flown distance can differ by ~3% on typical routes, more on routes with ATC routing constraints (e.g. Atlantic NAT tracks). For category 6, this is well within materiality.
