"""Runtime queries for USDA FoodData Central reference foods and portions."""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

_DEFAULT_DB: Optional[Path] = None


def set_default_db(path: Optional[Path | str]) -> None:
    """Set the database path override (e.g. for testing)."""
    global _DEFAULT_DB
    _DEFAULT_DB = Path(path) if path else None


def get_db_path(override: Optional[Path | str] = None) -> Path:
    if override:
        return Path(override)
    if _DEFAULT_DB and _DEFAULT_DB.is_file():
        return _DEFAULT_DB
    env_path = os.environ.get("PAI_HEALTH_FDC_DB")
    if env_path and Path(env_path).is_file():
        return Path(env_path)
    local = Path(__file__).resolve().parent / "fdc_foods.db"
    if local.is_file():
        return local
    try:
        import runtime
        res = runtime.resources() / "nutrition" / "fdc_foods.db"
        if res.is_file():
            return res
    except Exception:
        pass
    return local


def get_db(db_path: Optional[Path | str] = None) -> sqlite3.Connection:
    p = get_db_path(db_path)
    if not p.is_file():
        raise FileNotFoundError(f"FDC foods database not found at {p}")
    uri = p.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def search(query: str, limit: int = 25, db_path: Optional[Path | str] = None) -> List[dict]:
    """Search foods using FTS5 prefix matching, best matches first."""
    q = (query or "").strip()
    if not q:
        return []
    words = re.findall(r"[A-Za-z0-9]+", q)
    if not words:
        return []

    fts_query = " ".join(f'"{w}"*' for w in words)
    try:
        conn = get_db(db_path)
    except FileNotFoundError:
        return []

    try:
        cursor = conn.cursor()
        sql = """
            SELECT f.fdc_id, f.description, f.food_category, f.data_type, f.publication_date,
                   f.energy_kcal, f.protein_g, f.fat_g, f.sat_fat_g, f.carbs_g, f.fiber_g,
                   f.sugars_g, f.sodium_mg, f.potassium_mg, f.calcium_mg, f.iron_mg,
                   f.vitamin_k_ug, f.caffeine_mg
            FROM foods_fts
            JOIN foods f ON f.fdc_id = foods_fts.rowid
            WHERE foods_fts MATCH ?
            ORDER BY rank, f.description
            LIMIT ?
        """
        rows = cursor.execute(sql, (fts_query, limit)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def portions(fdc_id: int, db_path: Optional[Path | str] = None) -> List[dict]:
    """Retrieve household portions for an FDC food id, plus 100g baseline."""
    try:
        conn = get_db(db_path)
    except FileNotFoundError:
        return [{"id": 0, "amount": 100.0, "modifier": "g", "description": "100 g", "gram_weight": 100.0}]

    try:
        cursor = conn.cursor()
        rows = cursor.execute("""
            SELECT id, seq_num, amount, modifier, portion_description, gram_weight
            FROM portions
            WHERE fdc_id = ?
            ORDER BY seq_num, id
        """, (fdc_id,)).fetchall()

        out = []
        has_100g = False
        for r in rows:
            gw = float(r["gram_weight"])
            desc = (r["portion_description"] or "").strip()
            amt = r["amount"]
            mod = (r["modifier"] or "").strip()
            if desc:
                label = desc
            elif amt is not None and mod:
                label = f"{amt:g} {mod}"
            elif mod:
                label = mod
            else:
                label = f"{gw:g} g"

            if not re.search(r"\b\d+(?:\.\d+)?\s*g\b", label, re.I):
                label = f"{label} ({round(gw)} g)"

            if round(gw, 1) == 100.0:
                has_100g = True

            out.append({
                "id": r["id"],
                "amount": amt,
                "modifier": mod,
                "description": label,
                "gram_weight": gw,
            })

        if not has_100g:
            out.append({
                "id": 0,
                "amount": 100.0,
                "modifier": "g",
                "description": "100 g",
                "gram_weight": 100.0,
            })
        return out
    finally:
        conn.close()


def nutrients(fdc_id: int, grams: float, db_path: Optional[Path | str] = None) -> Optional[dict]:
    """Calculate nutrient totals for `grams` of food `fdc_id`."""
    try:
        conn = get_db(db_path)
    except FileNotFoundError:
        return None

    try:
        row = conn.execute("SELECT * FROM foods WHERE fdc_id = ?", (fdc_id,)).fetchone()
        if not row:
            return None
        factor = grams / 100.0
        nutrient_keys = (
            "energy_kcal", "protein_g", "fat_g", "sat_fat_g", "carbs_g",
            "fiber_g", "sugars_g", "sodium_mg", "potassium_mg",
            "calcium_mg", "iron_mg", "vitamin_k_ug", "caffeine_mg"
        )
        scaled = {k: round(float(row[k] or 0.0) * factor, 2) for k in nutrient_keys}
        return {
            "fdc_id": row["fdc_id"],
            "description": row["description"],
            "food_category": row["food_category"],
            "data_type": row["data_type"],
            "publication_date": row["publication_date"],
            "grams": round(float(grams), 1),
            **scaled,
        }
    finally:
        conn.close()


def meta(db_path: Optional[Path | str] = None) -> dict:
    """Return database metadata."""
    try:
        conn = get_db(db_path)
    except FileNotFoundError:
        return {}
    try:
        rows = conn.execute("SELECT key, value FROM meta").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()