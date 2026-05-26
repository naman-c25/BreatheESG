"""
Adapter contract. Each source kind implements parse(file_bytes, config) ->
AdapterResult, where the result is a list of NormalizedRow plus per-row errors.

Adapters do NOT touch the DB. The orchestrator (services.ingest) writes
RawRecord + EmissionActivity rows. This keeps adapters testable in isolation
and makes it cheap to add a new source: write a parser, register it.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import date
from typing import Any


@dataclass
class NormalizedRow:
    # Provenance breadcrumbs the orchestrator needs
    source_row_ref: str
    raw_payload: dict[str, Any]

    # The fields that map to EmissionActivity
    category_hint: tuple[int, str, str]  # (scope, category, subcategory)
    activity_date: date
    quantity_original: Decimal
    unit_original: str
    facility_source_code: str | None = None  # e.g. SAP plant code, utility account
    period_start: date | None = None
    period_end: date | None = None
    notes: str = ""


@dataclass
class AdapterResult:
    rows: list[NormalizedRow] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)  # {row_ref, message}


class BaseAdapter:
    kind: str = ""

    def parse(self, file_bytes: bytes, config: dict) -> AdapterResult:
        raise NotImplementedError
