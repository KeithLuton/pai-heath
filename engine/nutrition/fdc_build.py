"""Build engine/nutrition/fdc_foods.db from USDA FoodData Central bulk CSVs.

Converts Foundation Foods + SR Legacy CSVs into an offline SQLite database
with an FTS5 full-text search index and household portion weight definitions.

Usage:
    python engine/nutrition/fdc_build.py --csv-dir /path/to/FDC_CSVs --out engine/nutrition/fdc_foods.db
    python engine/nutrition/fdc_build.py --zip /path/to/FoodData_Central_csv_2024-10-31.zip --out engine/nutrition/fdc_foods.db
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, Optional, Set, Tuple

# USDA Nutrient ID mapping -> SQLite columns (per 100g)
NUTRIENT_ID_MAP: Dict[int, str] = {
    1008: "energy_kcal",       # Energy (kcal)
    1003: "protein_g",         # Protein (g)
    1004: "fat_g",             # Total lipid (fat) (g)
    1258: "sat_fat_g",         # Fatty acids, total saturated (g)
    1005: "carbs_g",           # Carbohydrate, by difference (g)
    1079: "fiber_g",           # Fiber, total dietary (g)
    2000: "sugars_g",          # Sugars, total including NLEA (g)
    1063: "sugars_g",          # Sugars, total (g) - fallback
    1093: "sodium_mg",         # Sodium, Na (mg)
    1092: "potassium_mg",      # Potassium, K (mg)
    1087: "calcium_mg",        # Calcium, Ca (mg)
    1089: "iron_mg",           # Iron, Fe (mg)
    1185: "vitamin_k_ug",      # Vitamin K (phylloquinone) (ug)
    1057: "caffeine_mg",       # Caffeine (mg)
}

DEFAULT_DATA_TYPES = {"foundation_food", "sr_legacy_food"}


def _open_csv_reader(source: Path, filename: str) -> Tuple[Iterator[dict], Optional[zipfile.ZipFile]]:
    """Open a CSV file from either a directory or a ZIP archive."""
    if source.is_file() and zipfile.is_zipfile(source):
        zf = zipfile.ZipFile(source)
        names = [n for n in zf.namelist() if n == filename or n.endswith("/" + filename)]
        if not names:
            zf.close()
            raise FileNotFoundError(f"{filename} not found in zip archive {source}")
        text_stream = io.TextIOWrapper(zf.open(names[0]), encoding="utf-8-sig")
        return csv.DictReader(text_stream), zf
    elif source.is_dir():
        target = source / filename
        if not target.is_file():
            matches = list(source.rglob(filename))
            if not matches:
                raise FileNotFoundError(f"{filename} not found in {source}")
            target = matches[0]
        f = open(target, "r", encoding="utf-8-sig")
        return csv.DictReader(f), None
    else:
        raise ValueError(f"Source must be a directory or zip archive: {source}")


def build(
    source_path: str | Path,
    out_db_path: str | Path,
    data_types: Optional[Set[str]] = None,
) -> dict:
    """Build SQLite database from USDA FDC CSVs."""
    source = Path(source_path)
    out_path = Path(out_db_path)
    allowed_types = {t.lower() for t in (data_types or DEFAULT_DATA_TYPES)}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    # Step 1: Read categories
    categories: Dict[int, str] = {}
    cat_reader, zf = _open_csv_reader(source, "food_category.csv")
    try:
        for row in cat_reader:
            try:
                cid = int(row["id"])
                categories[cid] = (row.get("description") or "").strip()
            except (ValueError, KeyError):
                continue
    finally:
        if zf:
            zf.close()

    # Step 2: Read food.csv
    foods: Dict[int, dict] = {}
    dataset_dates: Set[str] = set()
    food_reader, zf = _open_csv_reader(source, "food.csv")
    try:
        for row in food_reader:
            dtype = (row.get("data_type") or "").strip().lower()
            if dtype not in allowed_types:
                continue
            try:
                fdc_id = int(row["fdc_id"])
            except (ValueError, KeyError):
                continue
            cat_id = None
            if row.get("food_category_id"):
                try:
                    cat_id = int(row["food_category_id"])
                except ValueError:
                    pass
            pub_date = (row.get("publication_date") or "").strip()
            if pub_date:
                dataset_dates.add(pub_date)
            foods[fdc_id] = {
                "fdc_id": fdc_id,
                "description": (row.get("description") or "").strip(),
                "food_category": categories.get(cat_id, ""),
                "data_type": dtype,
                "publication_date": pub_date,
                "nutrients": {k: 0.0 for k in set(NUTRIENT_ID_MAP.values())},
            }
    finally:
        if zf:
            zf.close()

    # Step 3: Read food_nutrient.csv
    nut_reader, zf = _open_csv_reader(source, "food_nutrient.csv")
    try:
        for row in nut_reader:
            try:
                fdc_id = int(row["fdc_id"])
                nut_id = int(row["nutrient_id"])
            except (ValueError, KeyError):
                continue
            if fdc_id not in foods or nut_id not in NUTRIENT_ID_MAP:
                continue
            col = NUTRIENT_ID_MAP[nut_id]
            try:
                val = float(row.get("amount") or 0.0)
            except ValueError:
                val = 0.0
            # If 2000 vs 1063 for sugars, prefer 2000
            if col == "sugars_g" and nut_id == 1063 and foods[fdc_id]["nutrients"]["sugars_g"] > 0:
                continue
            foods[fdc_id]["nutrients"][col] = round(val, 2)
    finally:
        if zf:
            zf.close()

    # Step 4: Read food_portion.csv
    portions: Dict[int, list] = {}
    portion_count = 0
    try:
        portion_reader, zf = _open_csv_reader(source, "food_portion.csv")
        try:
            for row in portion_reader:
                try:
                    fdc_id = int(row["fdc_id"])
                except (ValueError, KeyError):
                    continue
                if fdc_id not in foods:
                    continue
                try:
                    gw = float(row.get("gram_weight") or 0.0)
                except ValueError:
                    continue
                if gw <= 0:
                    continue

                seq = None
                if row.get("seq_num"):
                    try:
                        seq = int(row["seq_num"])
                    except ValueError:
                        pass
                amt = None
                if row.get("amount"):
                    try:
                        amt = float(row["amount"])
                    except ValueError:
                        pass

                desc = (row.get("portion_description") or "").strip()
                mod = (row.get("modifier") or "").strip()
                portions.setdefault(fdc_id, []).append((seq, amt, mod, desc, round(gw, 2)))
                portion_count += 1
        finally:
            if zf:
                zf.close()
    except FileNotFoundError:
        # Portions file may be omitted in partial exports
        pass

    # Step 5: Write SQLite DB
    conn = sqlite3.connect(out_path)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")

    conn.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE foods (
            fdc_id INTEGER PRIMARY KEY,
            description TEXT NOT NULL,
            food_category TEXT,
            data_type TEXT NOT NULL,
            publication_date TEXT,
            energy_kcal REAL NOT NULL DEFAULT 0.0,
            protein_g REAL NOT NULL DEFAULT 0.0,
            fat_g REAL NOT NULL DEFAULT 0.0,
            sat_fat_g REAL NOT NULL DEFAULT 0.0,
            carbs_g REAL NOT NULL DEFAULT 0.0,
            fiber_g REAL NOT NULL DEFAULT 0.0,
            sugars_g REAL NOT NULL DEFAULT 0.0,
            sodium_mg REAL NOT NULL DEFAULT 0.0,
            potassium_mg REAL NOT NULL DEFAULT 0.0,
            calcium_mg REAL NOT NULL DEFAULT 0.0,
            iron_mg REAL NOT NULL DEFAULT 0.0,
            vitamin_k_ug REAL NOT NULL DEFAULT 0.0,
            caffeine_mg REAL NOT NULL DEFAULT 0.0
        );
        CREATE TABLE portions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fdc_id INTEGER NOT NULL,
            seq_num INTEGER,
            amount REAL,
            modifier TEXT,
            portion_description TEXT,
            gram_weight REAL NOT NULL,
            FOREIGN KEY (fdc_id) REFERENCES foods(fdc_id)
        );
        CREATE INDEX idx_portions_fdc_id ON portions(fdc_id);
    """)

    food_rows = []
    for fdc_id, f in foods.items():
        n = f["nutrients"]
        food_rows.append((
            fdc_id, f["description"], f["food_category"], f["data_type"], f["publication_date"],
            n["energy_kcal"], n["protein_g"], n["fat_g"], n["sat_fat_g"],
            n["carbs_g"], n["fiber_g"], n["sugars_g"], n["sodium_mg"],
            n["potassium_mg"], n["calcium_mg"], n["iron_mg"],
            n["vitamin_k_ug"], n["caffeine_mg"]
        ))
    conn.executemany("""
        INSERT INTO foods VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, food_rows)

    portion_rows = []
    for fdc_id, p_list in portions.items():
        for p in p_list:
            portion_rows.append((fdc_id, p[0], p[1], p[2], p[3], p[4]))
    if portion_rows:
        conn.executemany("""
            INSERT INTO portions (fdc_id, seq_num, amount, modifier, portion_description, gram_weight)
            VALUES (?,?,?,?,?,?)
        """, portion_rows)

    # Step 6: Create FTS5 index
    conn.executescript("""
        CREATE VIRTUAL TABLE foods_fts USING fts5(
            description,
            food_category,
            content='foods',
            content_rowid='fdc_id'
        );
        INSERT INTO foods_fts(rowid, description, food_category)
        SELECT fdc_id, description, COALESCE(food_category, '') FROM foods;
    """)

    dataset_ver = max(dataset_dates) if dataset_dates else "2024-10-31"
    meta_info = {
        "schema_version": "1",
        "dataset_version": dataset_ver,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "USDA FoodData Central (Foundation Foods + SR Legacy)",
        "food_count": str(len(foods)),
        "portion_count": str(portion_count),
    }
    conn.executemany("INSERT INTO meta VALUES (?,?)", meta_info.items())
    conn.commit()
    conn.execute("VACUUM")
    conn.close()

    return {
        "foods_count": len(foods),
        "portions_count": portion_count,
        "db_size_bytes": out_path.stat().st_size,
        "dataset_version": dataset_ver,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv-dir", "--zip", dest="source", required=True, help="Directory or ZIP file of USDA FDC CSVs")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "fdc_foods.db"), help="Output SQLite database")
    parser.add_argument("--types", default="foundation_food,sr_legacy_food", help="Comma-separated data types to include")
    args = parser.parse_args()

    types = {t.strip().lower() for t in args.types.split(",") if t.strip()}
    res = build(args.source, args.out, types)
    print(f"Built {args.out}: {res['foods_count']} foods, {res['portions_count']} portions, {res['db_size_bytes'] / (1024*1024):.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())