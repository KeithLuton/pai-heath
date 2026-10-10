"""De-duplication and source priority (§20).

Two problems, kept separate:

1. Exact duplicates — the same sample exported twice (e.g. a blood-pressure
   reading nested in a Correlation and repeated at top level, or the same
   export imported twice). Collapsed; the count is reported.

2. Overlapping sources for cumulative measures — the same walk counted by the
   phone and the watch. Summing both doubles the steps. For each cumulative
   measure the user's source priority picks the primary; overlapping samples
   from lower-priority sources are marked SUPERSEDED, never deleted.

Method-specific measures (HRV, sleep staging, wrist temperature) are never
merged across sources or methods: each (measure, method, source) is its own
series.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .schema import CUMULATIVE, METHOD_SPECIFIC, DeviceSample

CLASS_ORDER = {"WATCH": 0, "WATCH_OR_TRACKER": 0, "PHONE": 1, "OTHER_SOURCE": 2}


@dataclass(frozen=True)
class Placed:
    sample: DeviceSample
    role: str                 # PRIMARY | SUPERSEDED | KEPT (non-cumulative)
    superseded_by: Optional[str] = None


def _identity(s: DeviceSample) -> tuple:
    return (s.measure, s.start, s.end, s.value, s.unit, s.source_name, s.category, s.method)


def drop_exact_duplicates(samples: Iterable[DeviceSample]) -> Tuple[List[DeviceSample], int]:
    seen, out, dropped = set(), [], 0
    for s in samples:
        key = _identity(s)
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        out.append(s)
    return out, dropped


def default_priority(samples: Sequence[DeviceSample], measure: str) -> List[str]:
    """Visible, user-overridable default: watch, then phone, then others, then name."""
    sources: Dict[str, str] = {}
    for s in samples:
        if s.measure == measure:
            sources.setdefault(s.source_name, s.device_class)
    return sorted(sources, key=lambda n: (CLASS_ORDER.get(sources[n], 9), n))


def place(samples: Sequence[DeviceSample],
          priority: Optional[Dict[str, List[str]]] = None) -> List[Placed]:
    """Assign roles. `priority` maps measure -> ordered source names (user setting)."""
    priority = priority or {}
    placed: List[Placed] = []
    by_measure: Dict[str, List[DeviceSample]] = defaultdict(list)
    for s in samples:
        if s.measure in CUMULATIVE:
            by_measure[s.measure].append(s)
        else:
            placed.append(Placed(s, "KEPT"))

    for measure, items in by_measure.items():
        order = priority.get(measure) or default_priority(items, measure)
        rank = {name: i for i, name in enumerate(order)}
        items = sorted(items, key=lambda s: (rank.get(s.source_name, len(rank)), s.start))
        accepted: List[Tuple[object, object, str]] = []   # (start, end, source)
        for s in items:
            blocker = next((src for (a, b, src) in accepted
                            if src != s.source_name and s.start < b and a < s.end
                            and rank.get(src, len(rank)) < rank.get(s.source_name, len(rank))), None)
            if blocker:
                placed.append(Placed(s, "SUPERSEDED", blocker))
            else:
                placed.append(Placed(s, "PRIMARY"))
                accepted.append((s.start, s.end, s.source_name))
    return placed


def series_key(s: DeviceSample) -> tuple:
    """Method-specific measures are separate series per method and source."""
    if s.measure in METHOD_SPECIFIC:
        return (s.measure, s.method, s.source_name)
    return (s.measure,)


def daily_totals(placed: Iterable[Placed], measure: str) -> Dict[date, float]:
    """Cumulative totals per local calendar day (the export's own offset), primaries only.

    A sample spanning midnight is attributed to its start day; the derived
    total is labeled device-computed by the caller.
    """
    totals: Dict[date, float] = defaultdict(float)
    for p in placed:
        if p.sample.measure == measure and p.role == "PRIMARY" and p.sample.value is not None:
            totals[p.sample.start.date()] += p.sample.value
    return dict(sorted(totals.items()))
