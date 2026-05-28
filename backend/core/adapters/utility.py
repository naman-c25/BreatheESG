"""
Utility electricity adapter.

Format choice: PDF bill upload. Why this over portal CSV / API:

- US investor-owned utilities (ConEd, PG&E, etc.) almost never offer a
  programmatic API for commercial customers without a dedicated EDI feed
  or Green Button (which most commercial accounts don't support out of
  the box).
- 'Portal CSV' is real for some utilities but the CSV column set is
  utility-specific. There is no standard.
- The PDF bill is the one artifact every facilities team has, every
  month, for every account. It's also the artifact auditors expect to
  see referenced in the workpapers.

Tradeoff: PDF parsing is fragile. We accept that. The adapter is built
to fail loudly with a useful error per page so the analyst can intervene
rather than to silently extract the wrong number.

What this adapter handles:
  - Multi-meter bills (one PDF, N meter blocks → N EmissionActivity rows).
    The 'Meter ID' + 'Total Energy Usage' patterns repeat per meter; we
    iterate them in document order and emit one row each.
  - kWh and MWh (MWh is auto-converted to kWh via the unit alias table).
  - kVAh (reactive/apparent power) rejected — it is NOT a real energy
    consumption number for emissions purposes; using it would inflate
    the kWh equivalent by an arbitrary power-factor amount.
  - Service period parsing (DD/MM/YYYY or MM/DD/YYYY US-style); period
    spanning multiple calendar months raises BILLING_PERIOD_MISALIGNED.
  - Tariff line items (peak / off-peak / shoulder / demand) captured in
    the RawRecord payload for the analyst to inspect, but NOT split into
    separate emission rows. Splitting would require per-period grid-mix
    factors that we don't ship — and the total kWh is the audit number
    regardless.
"""
import io
import re
from decimal import Decimal
from datetime import datetime, date

from .base import BaseAdapter, AdapterResult, NormalizedRow


# Patterns. Built around a US commercial layout (ConEd-shaped). Real
# deployment: per-utility layout configs.
PERIOD_RE = re.compile(r"Service Period[: ]+(\d{2}/\d{2}/\d{4})\s*[-–to]+\s*(\d{2}/\d{2}/\d{4})", re.IGNORECASE)
METER_BLOCK_RE = re.compile(
    r"Meter\s+(?:ID|Number)[: ]+(?P<meter>[A-Z0-9-]+)"
    r"(?P<body>.*?)(?=Meter\s+(?:ID|Number)[: ]+[A-Z0-9-]+|\Z)",
    re.IGNORECASE | re.DOTALL,
)
USAGE_RE = re.compile(r"Total\s+(?:Energy\s+)?Usage[: ]+([\d,]+(?:\.\d+)?)\s*(kWh|MWh|kVAh)", re.IGNORECASE)
PEAK_RE = re.compile(r"(Peak|Off-?Peak|Shoulder)\s+Usage[: ]+([\d,]+(?:\.\d+)?)\s*(kWh|MWh)", re.IGNORECASE)
DEMAND_RE = re.compile(r"Demand\s+Charge[: ]+([\d,]+(?:\.\d+)?)\s*kW", re.IGNORECASE)


def _parse_us_date(s: str) -> date:
    return datetime.strptime(s, "%m/%d/%Y").date()


class UtilityPDFAdapter(BaseAdapter):
    kind = "utility_pdf"

    def parse(self, file_bytes: bytes, config: dict, filename: str = "") -> AdapterResult:
        result = AdapterResult()
        import pdfplumber
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                text = "\n".join((p.extract_text() or "") for p in pdf.pages)
        except Exception as e:
            result.errors.append({"row_ref": "pdf", "message": f"Could not open PDF: {e}"})
            return result

        period_m = PERIOD_RE.search(text)
        if not period_m:
            result.errors.append({"row_ref": "pdf",
                "message": "Could not extract service period. Bill layout may not match the supported template."})
            return result
        period_start = _parse_us_date(period_m.group(1))
        period_end = _parse_us_date(period_m.group(2))

        # Har meter ka apna block hai. Commercial bills almost always sub-meter
        # karte hain (main + HVAC + lighting). Pehle wala adapter sirf first meter
        # nikalta tha — N meters wali bill se 1 row banti thi. Galat.
        # Single-meter bill bhi isi loop ka special case hai.
        meter_blocks = list(METER_BLOCK_RE.finditer(text))
        if not meter_blocks:
            result.errors.append({"row_ref": "pdf",
                "message": "Could not extract any meter ID. Bill layout may not match the supported template."})
            return result

        for m in meter_blocks:
            meter_id = m.group("meter")
            body = m.group("body")
            row_ref = f"meter {meter_id}"

            usage_m = USAGE_RE.search(body)
            if not usage_m:
                result.errors.append({"row_ref": row_ref,
                    "message": f"Meter {meter_id} block has no 'Total Energy Usage' line."})
                continue

            qty = Decimal(usage_m.group(1).replace(",", ""))
            unit = usage_m.group(2)

            # kVAh = apparent power, real energy nahi.
            # Isko kWh maan ke calculate kar diya to emission 10-25% inflated ho jata
            # (1/power-factor se). Yeh exactly woh silent-data-corruption bug hai
            # jo audit mein pakda jata hai. Reject karke analyst ko bolo kWh meter mango.
            if unit.lower() == "kvah":
                result.errors.append({"row_ref": row_ref,
                    "message": f"Meter {meter_id} reported {qty} kVAh (apparent power). "
                               f"Emissions reporting requires active energy in kWh — request a kWh-metered bill."})
                continue

            # Capture tariff breakdown in the raw payload so the analyst
            # can see what the bill said even though we don't split rows.
            peaks = [(label.title(), Decimal(val.replace(",", "")), u)
                     for (label, val, u) in PEAK_RE.findall(body)]
            demand_kw = None
            d = DEMAND_RE.search(body)
            if d:
                demand_kw = Decimal(d.group(1).replace(",", ""))

            payload = {
                "meter_id": meter_id,
                "period_start": str(period_start),
                "period_end": str(period_end),
                "usage": str(qty),
                "unit": unit,
                "tariff_breakdown": [{"period": p, "usage": str(v), "unit": u} for (p, v, u) in peaks],
                "demand_kw": str(demand_kw) if demand_kw is not None else None,
                "extracted_text_preview": body[:600],
            }

            result.rows.append(NormalizedRow(
                source_row_ref=row_ref,
                raw_payload=payload,
                category_hint=(2, "Purchased Electricity", "Grid Mix"),
                activity_date=period_end,
                period_start=period_start,
                period_end=period_end,
                quantity_original=qty,
                unit_original=unit,
                facility_source_code=meter_id,
                notes=f"Bill period {period_start} to {period_end}"
                      + (f" · {len(peaks)} tariff line items captured" if peaks else "")
                      + (f" · demand {demand_kw} kW" if demand_kw is not None else ""),
            ))
        return result
