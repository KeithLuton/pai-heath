"""Seed lexicon.

A deliberately small, general vocabulary so the extractor can work before
real terminologies are wired in. It contains no record-specific entries.
Replace / extend with LOINC (labs), RxNorm (drugs) and SNOMED CT (findings)
once licensed; every lookup here is a function so the swap is local.

Unit conversions are declared, cited structural constants (§5.2). They enter
arithmetic as EpiFloat.structural so they never demote or launder status.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# ---- lab analytes: canonical name -> aliases (lowercase). Seed set of common tests.
ANALYTES: Dict[str, List[str]] = {
    "vitamin B12": ["vitamin b12", "b12", "cobalamin", "cyanocobalamin"],
    "methylmalonic acid": ["methylmalonic acid", "mma"],
    "hemoglobin A1c": ["hemoglobin a1c", "hba1c", "a1c", "glycated hemoglobin"],
    "glucose": ["glucose", "blood glucose", "fasting glucose", "fbg", "fasting blood sugar", "blood sugar"],
    "creatinine": ["creatinine", "scr", "creat"],
    "eGFR": ["egfr", "estimated gfr", "gfr calc"],
    "potassium": ["potassium"],
    "sodium": ["sodium"],
    "TSH": ["tsh", "thyroid stimulating hormone"],
    "LDL cholesterol": ["ldl", "ldl cholesterol", "ldl-c"],
    "total cholesterol": ["total cholesterol", "cholesterol"],
    "triglycerides": ["triglycerides"],
    "vitamin D": ["vitamin d", "25-oh vitamin d", "25-hydroxyvitamin d"],
    "ferritin": ["ferritin"],
    "hemoglobin": ["hemoglobin", "hgb"],
    "urine albumin-to-creatinine ratio": ["uacr", "urine albumin-to-creatinine ratio", "microalbumin"],
    "CBC": ["cbc", "complete blood count"],
    "metabolic panel": ["cmp", "bmp", "metabolic panel", "comprehensive metabolic panel", "basic metabolic panel"],
    "MRI": ["mri", "magnetic resonance imaging"],
    # Added from the retired extract_labs registry (no specimen in the canonical name: §2.5)
    "blood urea nitrogen": ["bun", "blood urea nitrogen"],
    "chloride": ["chloride"],
    "carbon dioxide": ["carbon dioxide", "co2", "bicarbonate"],
    "calcium": ["calcium", "calcium total"],
    "HDL cholesterol": ["hdl", "hdl cholesterol", "hdl-c", "cholesterol hdl"],
    "free T4": ["free t4", "t4, free"],
    "troponin I": ["troponin i", "trop i", "high sensitivity troponin", "troponin"],
    "urine protein": ["urine protein", "protein, urine"],
    "urine leukocyte esterase": ["leukocyte esterase", "leuk est", "urine leukocyte esterase"],
    "urine nitrites": ["nitrites", "nitrite", "urine nitrites"],
}

# ---- analyte-specific multiplicative conversions: (analyte, from, to) -> (factor, basis)
CONVERSIONS: Dict[Tuple[str, str, str], Tuple[float, str]] = {
    ("vitamin B12", "pg/mL", "pmol/L"): (0.73781, "cyanocobalamin molar mass 1355.37 g/mol"),
    ("glucose", "mg/dL", "mmol/L"): (1 / 18.0156, "glucose molar mass 180.156 g/mol"),
    ("creatinine", "mg/dL", "umol/L"): (88.42, "creatinine molar mass 113.12 g/mol"),
    ("total cholesterol", "mg/dL", "mmol/L"): (0.02586, "cholesterol molar mass 386.65 g/mol"),
    ("LDL cholesterol", "mg/dL", "mmol/L"): (0.02586, "cholesterol molar mass 386.65 g/mol"),
    ("triglycerides", "mg/dL", "mmol/L"): (0.01129, "triolein molar mass 885.4 g/mol"),
    ("vitamin D", "ng/mL", "nmol/L"): (2.496, "25-hydroxyvitamin D molar mass 400.6 g/mol"),
}

# ---- drugs: seed generic names + class suffixes (RxNorm replaces this)
DRUGS = {
    "metformin", "lisinopril", "losartan", "amlodipine", "atorvastatin", "simvastatin", "rosuvastatin",
    "levothyroxine", "metoprolol", "carvedilol", "hydrochlorothiazide", "furosemide", "spironolactone",
    "insulin", "glipizide", "glyburide", "omeprazole", "pantoprazole", "warfarin", "apixaban",
    "aspirin", "ibuprofen", "naproxen", "acetaminophen", "albuterol", "prednisone", "gabapentin",
    "sertraline", "fluoxetine", "cyanocobalamin", "semaglutide", "empagliflozin",
}
DRUG_SUFFIXES = ("pril", "sartan", "olol", "statin", "dipine", "formin", "gliptin", "gliflozin", "prazole",
                 "tidine", "mycin", "cillin", "floxacin", "azole", "glutide")

FREQUENCIES = r"\b(?:QD|QOD|BID|TID|QID|QHS|QAM|QPM|PRN|q\d+h|daily|twice daily|three times daily|nightly|weekly|as needed)\b"

# ---- cues
ATTRIBUTION_RE = re.compile(r"^(?:the\s+)?(?:patient|pt\.?|he|she)\s+(?:reports|states|endorses|notes|describes|complains of)\s+"
                            r"|^(?:reports|states|endorses|complains of|c/o)\s+", re.I)
MED_PLAN_VERBS = [
    (re.compile(r"^(?:continue|cont\.?)\s+", re.I), "CONTINUE_PLANNED"),
    (re.compile(r"^(?:start|begin|initiate|add)\s+", re.I), "START_PLANNED"),
    (re.compile(r"^(?:stop|discontinue|d/c|hold)\s+", re.I), "STOP_PLANNED"),
    (re.compile(r"^(?:increase|decrease|titrate|adjust)\s+", re.I), "DOSE_CHANGE_PLANNED"),
]
ACTION_VERBS = re.compile(r"^(?:check|order|obtain|repeat|draw|send|get|schedule|refer(?: to)?|consult|recheck|follow up|f/u)\s+", re.I)
ACTION_STATE_POST = [
    (re.compile(r"\b(?:ordered)\b", re.I), "ORDERED"),
    (re.compile(r"\b(?:performed|done|completed|obtained)\b", re.I), "PERFORMED"),
    (re.compile(r"\b(?:resulted|results? (?:back|available))\b", re.I), "RESULTED"),
    (re.compile(r"\b(?:cancel+ed)\b", re.I), "CANCELLED"),
]
LAB_DOC_TITLE = re.compile(r"\b(?:lab|labs|laboratory|result|results|pathology|radiology|imaging report)\b", re.I)
HEDGE_PREFIX = re.compile(r"^(?:likely|probable|probably|possible|possibly|suspected|presumed|rule out|r/o|"
                          r"concern for|cannot exclude|unlikely(?: to be)?)\s+", re.I)
DIFFERENTIAL_SUFFIX = re.compile(r"\s+(?:remains\s+)?(?:on|in)\s+(?:the\s+)?differential$", re.I)
ONSET_PREFIX = re.compile(r"^(?:gradual|sudden|acute|insidious)?\s*onset\s+of\s+", re.I)

STOP = {"the", "a", "an", "of", "in", "on", "with", "and", "or", "to", "for", "at", "by", "both", "test", "level",
        "levels", "low", "high", "reduced", "decreased", "elevated", "deficiency", "serum", "result"}


def analyte_of(text: str) -> Optional[str]:
    """Canonical analyte named in text (longest alias wins), or None."""
    low = text.lower()
    best: Optional[Tuple[int, str]] = None
    for canon, aliases in ANALYTES.items():
        for a in aliases:
            if re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", low):
                if best is None or len(a) > best[0]:
                    best = (len(a), canon)
    return best[1] if best else None


def analyte_match(text: str) -> Optional[Tuple[str, int, int]]:
    """(canonical analyte, start, end) of the longest alias in text, or None."""
    low = text.lower()
    best: Optional[Tuple[str, int, int]] = None
    for canon, aliases in ANALYTES.items():
        for a in aliases:
            m = re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", low)
            if m and (best is None or (m.end() - m.start()) > (best[2] - best[1])):
                best = (canon, m.start(), m.end())
    return best


def aliases_of(canon: str) -> List[str]:
    return ANALYTES.get(canon, [canon.lower()])


def drug_of(text: str) -> Optional[str]:
    for word in re.findall(r"[A-Za-z][A-Za-z\-]+", text):
        w = word.lower()
        if w in DRUGS or (len(w) > 6 and w.endswith(DRUG_SUFFIXES)):
            return w
    return None


def core_terms(text: str) -> set:
    """Content words for loose concept matching ('low vitamin B12' ~ 'vitamin B12 deficiency')."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    out = {w for w in words if w not in STOP and len(w) > 1}
    canon = analyte_of(text)
    if canon:
        out |= set(re.findall(r"[a-z0-9]+", canon.lower())) - STOP
    return out


def same_concept(a: str, b: str) -> bool:
    ca, cb = analyte_of(a), analyte_of(b)
    if ca and cb:
        return ca == cb
    ta, tb = core_terms(a), core_terms(b)
    return bool(ta and tb and (ta <= tb or tb <= ta))


def conversion(analyte: str, from_unit: str, to_unit: str) -> Optional[Tuple[float, str]]:
    if from_unit == to_unit:
        return 1.0, "same unit"
    if (analyte, from_unit, to_unit) in CONVERSIONS:
        return CONVERSIONS[(analyte, from_unit, to_unit)]
    if (analyte, to_unit, from_unit) in CONVERSIONS:
        f, basis = CONVERSIONS[(analyte, to_unit, from_unit)]
        return 1 / f, basis
    return None
