"""First-person entries (§18, §19). The entry is the source; edits supersede, nothing is overwritten.

Every entry is FIRST_PERSON: accepted as the record for tracking, rendered
"logged by you", never "unverified", never downgraded by a missing sensor.
"""
from __future__ import annotations

import re
import secrets
from datetime import date, datetime, timedelta
from typing import Optional

URGENT_SYMPTOMS = ("chest pain", "shortness of breath", "sudden weakness or numbness", "trouble speaking",
                   "vision change", "severe headache", "confusion")
TARGETS = {"bp": "Blood pressure", "resting_hr": "Resting heart rate", "glucose": "Fasting glucose",
           "weight": "Weight"}
SEXES = {"female", "male", "other", "prefer not to say"}


class LogError(ValueError):
    pass


def _dt(v, field="at") -> str:
    if not v:
        return datetime.now().astimezone().isoformat(timespec="minutes")
    try:
        d = datetime.fromisoformat(str(v))
    except ValueError as exc:
        raise LogError(f"{field} must be a date and time") from exc
    if d.tzinfo is None:
        d = d.astimezone()        # the person's own computer clock
    if d > datetime.now().astimezone() + timedelta(minutes=5):
        raise LogError(f"{field} is in the future")
    return d.isoformat(timespec="minutes")


def _num(v, field, lo, hi) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError) as exc:
        raise LogError(f"{field} must be a number") from exc
    if not lo <= x <= hi:
        raise LogError(f"{field} must be between {lo} and {hi}")
    return x


def _date(v, field) -> Optional[str]:
    if v in (None, ""):
        return None
    try:
        return date.fromisoformat(str(v)).isoformat()
    except ValueError as exc:
        raise LogError(f"{field} must be a date (YYYY-MM-DD)") from exc


def _text(v, field, n=200) -> str:
    s = " ".join(str(v or "").split())
    if not s:
        raise LogError(f"{field} is required")
    return s[:n]


def normalize(e: dict) -> dict:
    t = str(e.get("type", "")).upper()
    if t == "LAB":
        from .manual_labs import normalize as normalize_lab
        return normalize_lab(e)
    if t == "MEDICATION":
        sched = [s for s in (e.get("schedule") or []) if s]
        for s in sched:
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(s)):
                raise LogError("schedule times are HH:MM")
        targets = [x for x in (e.get("targets") or []) if x in TARGETS]
        return {"type": t, "med_id": e.get("med_id") or "MED-" + secrets.token_hex(5),
                "name": _text(e.get("name"), "name", 80), "dose": " ".join(str(e.get("dose") or "").split())[:60],
                "schedule": sorted(set(sched)), "start": _date(e.get("start"), "start"),
                "stopped": _date(e.get("stopped"), "stopped"), "targets": targets,
                "sensor_class": e.get("sensor_class") if e.get("sensor_class") in ("DIRECT_DELIVERY", "CONTAINER_ACCESS") else None}
    if t == "DOSE":
        taken = str(e.get("taken", "")).upper()
        if taken not in ("YES", "NO", "NOT_SURE"):
            raise LogError("taken is YES, NO or NOT_SURE")
        return {"type": t, "med_id": _text(e.get("med_id"), "med_id", 20), "due": e.get("due") or None,
                "taken": taken, "at": _dt(e.get("at"))}
    if t == "BP":
        sys_, dia = _num(e.get("systolic"), "systolic", 50, 300), _num(e.get("diastolic"), "diastolic", 30, 200)
        if dia >= sys_:
            raise LogError("diastolic must be lower than systolic")
        out = {"type": t, "systolic": sys_, "diastolic": dia, "at": _dt(e.get("at"))}
        if e.get("pulse") not in (None, ""):
            out["pulse"] = _num(e["pulse"], "pulse", 20, 250)
        return out
    if t == "GLUCOSE":
        return {"type": t, "mg_dl": _num(e.get("mg_dl"), "glucose", 20, 600), "fasting": bool(e.get("fasting")),
                "at": _dt(e.get("at"))}
    if t == "WEIGHT":
        return {"type": t, "kg": _num(e.get("kg"), "weight", 20, 400), "at": _dt(e.get("at"))}
    if t == "SYMPTOM":
        urgent = [s for s in (e.get("urgent") or []) if s in URGENT_SYMPTOMS]
        return {"type": t, "text": _text(e.get("text") or ", ".join(urgent), "symptom"), "urgent": urgent,
                "at": _dt(e.get("at"))}
    if t == "ITEM":
        return {"type": t, "name": _text(e.get("name"), "name", 80), "at": _dt(e.get("at"))}
    if t == "ADOPT":
        return {"type": t, "suggestion_id": _text(e.get("suggestion_id"), "suggestion_id", 40),
                "on": _date(e.get("on"), "on") or date.today().isoformat(), "active": bool(e.get("active", True))}
    if t == "PROFILE":
        by = e.get("birth_year")
        out = {"type": t, "birth_year": int(_num(by, "birth year", 1900, date.today().year)) if by not in (None, "") else None,
               "sex": e.get("sex") if e.get("sex") in SEXES else None}
        return out
    if t == "MEAL":
        items_raw = e.get("items")
        if not isinstance(items_raw, list) or not (1 <= len(items_raw) <= 30):
            raise LogError("Meal must contain between 1 and 30 items")
        from nutrition import foods
        norm_items = []
        meal_totals = {
            "energy_kcal": 0.0, "protein_g": 0.0, "fat_g": 0.0, "sat_fat_g": 0.0,
            "carbs_g": 0.0, "fiber_g": 0.0, "sugars_g": 0.0, "sodium_mg": 0.0,
            "potassium_mg": 0.0, "calcium_mg": 0.0, "iron_mg": 0.0,
            "vitamin_k_ug": 0.0, "caffeine_mg": 0.0,
        }
        for idx, item in enumerate(items_raw):
            if not isinstance(item, dict):
                raise LogError(f"Item {idx + 1} must be an object")
            try:
                fdc_id = int(item.get("fdc_id"))
            except (TypeError, ValueError) as exc:
                raise LogError(f"Item {idx + 1}: fdc_id must be a number") from exc
            grams = _num(item.get("grams"), f"Item {idx + 1} grams", 1.0, 3000.0)

            nut = foods.nutrients(fdc_id, grams)
            if nut is None:
                raise LogError(f"Item {idx + 1}: unknown food ID {fdc_id}")

            desc = _text(item.get("description") or nut.get("description"), "description", 200)
            portion_label = item.get("portion_label")
            if portion_label:
                portion_label = str(portion_label).strip()[:100]

            item_nutrients = {
                k: round(nut.get(k, 0.0), 2) for k in meal_totals
            }
            for k in meal_totals:
                meal_totals[k] = round(meal_totals[k] + item_nutrients[k], 2)

            norm_items.append({
                "fdc_id": fdc_id,
                "description": desc,
                "grams": round(grams, 1),
                "portion_label": portion_label or None,
                "nutrients": item_nutrients,
                "provenance": {
                    "source": "USDA FoodData Central",
                    "fdc_id": fdc_id,
                    "dataset_date": nut.get("publication_date") or "",
                },
            })
        out = {
            "type": t,
            "at": _dt(e.get("at")),
            "items": norm_items,
            "totals": meal_totals,
        }
        if e.get("note"):
            out["note"] = _text(e.get("note"), "note", 500)
        return out
    raise LogError("Unknown entry type")


def ref_for(vault, e: dict) -> Optional[str]:
    """Entries that represent one evolving thing share a tag, so an edit supersedes the last version."""
    if e["type"] == "LAB":
        if len(e["results"]) != 1:
            raise LogError("stored LAB logs require one analyte per record")
        return vault.tag("typed-lab", e["at"] + "|" + e["results"][0]["analyte"].casefold())
    if e["type"] == "MEDICATION":
        return vault.tag("medication", e["med_id"])
    if e["type"] == "ADOPT":
        return vault.tag("adopt", e["suggestion_id"])
    if e["type"] == "PROFILE":
        return vault.tag("profile", "me")
    return None