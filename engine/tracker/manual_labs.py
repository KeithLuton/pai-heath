"""Typed laboratory transcription. USER_TYPED is self-reported, never document-verified."""
from __future__ import annotations
import math
from datetime import date
from records.lexicon import ANALYTES

# Conservative transcription error guards, NOT clinical reference ranges.
# Source for unit definitions/conversion: UCUM (https://ucum.org/ucum).
# Broad limits are implementation safeguards inspired by ordinary report scales,
# NOT validated medical bounds. Values beyond these require document ingestion.
LIMITS = {('glucose', 'mg/dL'): (10, 2000),
          ('hemoglobin A1c', '%'): (2, 25),
          ('LDL cholesterol', 'mg/dL'): (1, 1000)}


def canonical(name):
    key = ' '.join(name.casefold().split())
    for label, aliases in ANALYTES.items():
        if key == label.casefold() or key in aliases:
            return label
    return name


def normalize(entry):
    from tracker.logs import LogError
    if not isinstance(entry, dict):
        raise LogError('lab entry must be an object')
    try:
        at = date.fromisoformat(str(entry['at']))
    except (KeyError, TypeError, ValueError) as exc:
        raise LogError('collection date must be YYYY-MM-DD') from exc
    if at > date.today():
        raise LogError('collection date must not be in the future')
    results = entry.get('results')
    if not isinstance(results, list) or not 1 <= len(results) <= 100:
        raise LogError('results must contain 1–100 rows')
    out = []
    for row in results:
        if not isinstance(row, dict):
            raise LogError('each result must be an object')
        name = str(row.get('analyte') or '').strip()
        if not name or len(name) > 120:
            raise LogError('analyte must contain 1–120 characters')
        canon = canonical(name)
        unit = str(row.get('unit') or '').strip()
        if len(unit) > 40:
            raise LogError('unit is too long')
        raw_value, raw_text = row.get('value'), row.get('value_text')
        if (raw_value is not None and str(raw_value).strip() != '') == bool(str(raw_text or '').strip()):
            raise LogError('provide exactly one numeric or qualitative value')
        item = {'analyte': canon, 'analyte_original': name, 'unit': unit or None}
        if raw_value is not None and str(raw_value).strip():
            try:
                value = float(raw_value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise LogError('value must be numeric') from exc
            if not math.isfinite(value):
                raise LogError('value must be finite')
            bounds = LIMITS.get((canon, unit))
            if bounds and not bounds[0] <= value <= bounds[1]:
                raise LogError(f'{canon} {unit} outside transcription limits {bounds[0]}–{bounds[1]}')
            item['value'] = value
        else:
            value_text = str(raw_text).strip()
            if len(value_text) > 100:
                raise LogError('qualitative value is too long')
            item['value_text'] = value_text
        for key in ('ref_low', 'ref_high'):
            if row.get(key) not in (None, ''):
                try:
                    x = float(row[key])
                except (TypeError, ValueError, OverflowError) as exc:
                    raise LogError(f'{key} must be numeric') from exc
                if not math.isfinite(x):
                    raise LogError(f'{key} must be finite')
                item[key] = x
        if item.get('ref_low', float('-inf')) > item.get('ref_high', float('inf')):
            raise LogError('reference low exceeds high')
        for key, limit in [('ref_text', 150)]:
            if row.get(key):
                item[key] = str(row[key]).strip()[:limit]
        flag = str(row.get('flag') or '').strip().upper()
        if flag not in ('', 'H', 'L', 'A'):
            raise LogError('flag must be H, L, A or blank as printed')
        if flag:
            item['flag'] = flag
        out.append(item)
    pairs = [(at.isoformat(), r['analyte'].casefold()) for r in out]
    if len(pairs) != len(set(pairs)):
        raise LogError('duplicate analyte for the same collection date')
    return {'type': 'LAB', 'at': at.isoformat(), 'panel': str(entry.get('panel') or '').strip()[:100] or None,
            'results': out, 'lab_name': str(entry.get('lab_name') or '').strip()[:120] or None,
            'note': str(entry.get('note') or '').strip()[:500] or None, 'origin': 'USER_TYPED',
            'standing': 'FIRST_PERSON', 'verified': False}


def current_results(logs):
    """Additive view: never treats typed values as document evidence."""
    result = []
    for log in logs:
        if log.get('type') == 'LAB' and log.get('origin') == 'USER_TYPED':
            for row in log.get('results', []):
                result.append(dict(row, at=log['at'], panel=log.get('panel'), lab_name=log.get('lab_name'),
                                   note=log.get('note'), origin='USER_TYPED', verified=False,
                                   source='you typed this'))
    return sorted(result, key=lambda r: (r['at'], r['analyte']), reverse=True)
