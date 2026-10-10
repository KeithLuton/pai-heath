"""Device importers — each produces DeviceSample records and an ImportReport.

Nothing here strengthens what the export says. See schema.py for the contract.
"""
from .schema import CUMULATIVE, METHOD_SPECIFIC, DeviceSample, ImportReport, Provenance
from . import apple_health, fitbit, garmin_activities, generic_csv
from .dedupe import drop_exact_duplicates, place, series_key, daily_totals, Placed

__all__ = [
    "CUMULATIVE", "METHOD_SPECIFIC", "DeviceSample", "ImportReport", "Provenance",
    "apple_health", "fitbit", "garmin_activities", "generic_csv",
    "drop_exact_duplicates", "place", "series_key", "daily_totals", "Placed",
]
