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

We support a minimal layout: a 'BILLING SUMMARY' line with the billing
period, an 'ENERGY USAGE' line with kWh used, and a meter ID line.
SOURCES.md documents what we'd add for a real deployment.
"""
import io
import re
import hashlib
from decimal import Decimal
from datetime import datetime

from .base import BaseAdapter, AdapterResult, NormalizedRow


PERIOD_RE = re.compile(r"Service Period[: ]+(\d{2}/\d{2}/\d{4})\s*[-–to]+\s*(\d{2}/\d{2}/\d{4})", re.IGNORECASE)
USAGE_RE = re.compile(r"Total\s+(?:Energy\s+)?Usage[: ]+([\d,]+(?:\.\d+)?)\s*(kWh|MWh)", re.IGNORECASE)
METER_RE = re.compile(r"Meter\s+(?:ID|Number)[: ]+([A-Z0-9-]+)", re.IGNORECASE)


def _parse_us_date(s: str):
    return datetime.strptime(s, "%m/%d/%Y").date()


class UtilityPDFAdapter(BaseAdapter):
    kind = "utility_pdf"

    def parse(self, file_bytes: bytes, config: dict) -> AdapterResult:
        result = AdapterResult()
        import pdfplumber
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                text = "\n".join((p.extract_text() or "") for p in pdf.pages)
        except Exception as e:
            result.errors.append({"row_ref": "pdf", "message": f"Could not open PDF: {e}"})
            return result

        period_m = PERIOD_RE.search(text)
        usage_m = USAGE_RE.search(text)
        meter_m = METER_RE.search(text)

        missing = []
        if not period_m: missing.append("service period")
        if not usage_m: missing.append("energy usage")
        if not meter_m: missing.append("meter ID")
        if missing:
            result.errors.append({"row_ref": "pdf", "message": f"Could not extract: {', '.join(missing)}. The bill layout may not match the supported template."})
            return result

        period_start = _parse_us_date(period_m.group(1))
        period_end = _parse_us_date(period_m.group(2))
        qty = Decimal(usage_m.group(1).replace(",", ""))
        unit = usage_m.group(2)
        meter_id = meter_m.group(1)

        # Attribution: bill-date strategy. period_end is activity_date.
        # See MODEL.md §6 for why we expose period_start/period_end too.
        result.rows.append(NormalizedRow(
            source_row_ref=f"meter {meter_id}",
            raw_payload={
                "meter_id": meter_id,
                "period_start": str(period_start),
                "period_end": str(period_end),
                "usage": str(qty),
                "unit": unit,
                "extracted_text_preview": text[:1000],
            },
            category_hint=(2, "Purchased Electricity", "Grid Mix"),
            activity_date=period_end,
            period_start=period_start,
            period_end=period_end,
            quantity_original=qty,
            unit_original=unit,
            facility_source_code=meter_id,
            notes=f"Bill period {period_start} to {period_end}",
        ))
        return result
