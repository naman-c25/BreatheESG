from .base import AdapterResult, NormalizedRow, BaseAdapter
from .sap import SAPFlatFileAdapter
from .utility import UtilityPDFAdapter
from .travel import TravelAPIAdapter

REGISTRY = {
    "sap_flatfile": SAPFlatFileAdapter,
    "utility_pdf": UtilityPDFAdapter,
    "travel_api": TravelAPIAdapter,
}
