"""Normalized device sample — MEDICAL_PRIMITIVES.md §20.

Every importer, whatever the vendor, produces DeviceSample records. Nothing
here strengthens what the export says: unknown device models stay unknown,
vendor-specific metrics keep their vendor and method, and every sample points
back to the exact record in the exact file it came from.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

# Measures whose method differs by vendor. Series with different methods are
# never merged or compared (§20, comparability).
METHOD_SPECIFIC = {"hrv", "sleep_stage", "sleep_stage_minutes", "wrist_temperature", "resting_heart_rate", "vo2_max", "exercise_time"}

# Cumulative measures: overlapping sources double-count unless one is primary.
CUMULATIVE = {"steps", "distance", "active_energy", "flights_climbed"}


@dataclass(frozen=True)
class Provenance:
    file_name: str
    file_sha256: str
    record_index: int          # ordinal of the element in the export, 0-based
    raw_type: str              # vendor's own type identifier, verbatim


@dataclass(frozen=True)
class DeviceSample:
    measure: str               # normalized measure name
    value: Optional[float]     # numeric value (None for category samples)
    unit: str                  # normalized unit
    start: datetime            # timezone-aware
    end: datetime              # timezone-aware
    source_name: str           # as exported (often user-named, e.g. "Keith's Apple Watch")
    source_app: str            # vendor/app family, e.g. "apple_health"
    device_model: Optional[str]  # from the export's device field; None if not stated
    device_class: str          # WATCH | WATCH_OR_TRACKER | PHONE | OTHER_SOURCE (never guessed beyond the export)
    method: Optional[str]      # e.g. "SDNN" for Apple HRV; None if not method-specific
    category: Optional[str]    # category value (sleep stage etc.), verbatim-normalized
    provenance: Provenance
    standing: str = "DEVICE"
    conversion: Optional[str] = None   # logged when a unit was converted
    metadata: dict = field(default_factory=dict, compare=False, hash=False)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["start"] = self.start.isoformat()
        d["end"] = self.end.isoformat()
        return d


@dataclass
class ImportReport:
    importer: str
    importer_status: str       # "UNVERIFIED_AGAINST_REAL_EXPORT" until a real file has passed
    file_name: str
    file_sha256: str
    export_date: Optional[str] = None
    samples: int = 0
    by_measure: dict = field(default_factory=dict)
    by_source: dict = field(default_factory=dict)
    unmapped_types: dict = field(default_factory=dict)   # never silently dropped
    rejected: list = field(default_factory=list)         # (record_index, reason)
    first: Optional[str] = None
    last: Optional[str] = None
    notes: list = field(default_factory=list)              # assumptions applied, declared settings
