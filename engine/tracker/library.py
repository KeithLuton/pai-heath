"""Recommendation and rule library (§23.1). Curated, versioned, cited, applied deterministically.

Every figure here comes from the named source, but none has yet been checked
line by line against that source by a person: `verified` is False everywhere,
and the UI says so. Thresholds marked CALIBRATED are the constitution's
`[{calibrated}]` placeholders — sensible starting values, to be tuned on real data.
"""
from __future__ import annotations

ACC_AHA = ("2017 ACC/AHA High Blood Pressure Guideline (Whelton et al., Hypertension 2018;71:e13–e115)",
           "https://www.ahajournals.org/doi/10.1161/HYP.0000000000000065")
ADA = ("ADA Standards of Care in Diabetes, section 2 (Diabetes Care, current January supplement)",
       "https://diabetesjournals.org/care")
ADA_B12 = ("ADA Standards of Care in Diabetes, section 9: periodic B12 measurement with long-term metformin",
           "https://diabetesjournals.org/care")
USPSTF_LIPID = ("USPSTF: Statin use for primary prevention of CVD in adults 40–75 (lipid measurement)",
                "https://www.uspreventiveservicestaskforce.org")
USPSTF_DM = ("USPSTF: Prediabetes and type 2 diabetes screening, adults 35–70 with overweight or obesity",
             "https://www.uspreventiveservicestaskforce.org")
AAFP_WL = ("AAFP: Unintentional weight loss in older adults (Am Fam Physician 2014;89(9):718–722)",
           "https://www.aafp.org/pubs/afp/issues/2014/0501/p718.html")
FDA_LABEL = ("FDA drug labeling (DailyMed), interactions section", "https://dailymed.nlm.nih.gov")

# ---------------------------------------------------------------- lanes (§24)
BP_RULE = {
    "id": "BP-LANE", "version": 1, "source": ACC_AHA,
    "window_days": 14,             # CALIBRATED
    "min_readings": 12,            # CALIBRATED (guideline: two readings morning and evening for a week)
    # Home readings: ACC/AHA Table 11 home equivalents — office 130/80 ≈ home 130/80; office 140/90 ≈ home 135/85.
    "lane1": (130, 80), "lane2": (135, 85), "severe": (180, 120), "moderate_only": (160, 100),
}
GLUCOSE_RULE = {
    "id": "GLU-LANE", "version": 1, "source": ADA,
    "window_days": 30, "lane2_fasting": 126, "lane2_days": 2, "lane1_fasting": 100, "lane1_min": 5,
}
WEIGHT_RULE = {"id": "WT-LOSS", "version": 1, "source": AAFP_WL, "loss_fraction": 0.05, "window_days": 180}

LANE3_TEXT = ("Your blood pressure reading is very high and you've logged {symptom}. "
              "Call 911 or your local emergency number now.")         # §28.3 fixed text, never rephrased
DOSE_SAFETY = ("If you're not sure whether you took it, check your medication's instructions "
               "or ask your pharmacist before taking another dose.")  # §21.4 fixed

# ---------------------------------------------------------------- medication verification (§21)
DOSE_RULE = {"id": "DOSE-PATTERN", "version": 1, "pre_min": 60, "post_start_min": 30, "post_end_min": 150,
             "min_dose_days": 14, "min_comparison_days": 7, "min_effect_bpm": 3.0, "min_separation": 1.0,
             "prompt_after_min": 150}                                   # all CALIBRATED
CHANGE_RULE = {"id": "CHANGE-WINDOW", "version": 1, "days": 14, "min_readings": 6}   # CALIBRATED

# ---------------------------------------------------------------- drug classes (from labels; matched by name)
ACE_ARB_K = {"lisinopril", "enalapril", "ramipril", "benazepril", "captopril", "quinapril", "fosinopril", "perindopril",
             "trandolapril", "moexipril", "losartan", "valsartan", "irbesartan", "olmesartan", "candesartan",
             "telmisartan", "azilsartan", "eprosartan", "spironolactone", "eplerenone", "sacubitril"}
BP_MEDS = ACE_ARB_K | {"amlodipine", "nifedipine", "felodipine", "diltiazem", "verapamil", "hydrochlorothiazide",
                       "chlorthalidone", "indapamide", "metoprolol", "atenolol", "carvedilol", "bisoprolol",
                       "nebivolol", "propranolol", "labetalol", "clonidine", "hydralazine", "doxazosin", "furosemide"}

INTERACTIONS = [
    ({"simvastatin", "atorvastatin", "lovastatin", "amlodipine", "felodipine", "nifedipine"},
     "Grapefruit", "Grapefruit can raise blood levels of this medication. Ask your pharmacist whether to avoid it."),
    ({"warfarin"}, "Vitamin K foods", "Keep vitamin K foods (leafy greens) consistent from week to week rather than "
                                      "changing them suddenly; your clinician sets your dose around your usual intake."),
    (ACE_ARB_K, "Potassium foods and salt substitutes", "This medication can raise potassium. Salt substitutes "
                                                       "(potassium chloride) and potassium supplements are held."),
    ({"levothyroxine"}, "Calcium and iron", "Calcium and iron can reduce absorption; ask your pharmacist how far apart to take them."),
    ({"phenelzine", "tranylcypromine", "isocarboxazid", "selegiline"}, "Tyramine foods",
     "Aged cheeses and cured meats can interact; follow your prescriber's food list."),
    ({"metformin"}, "Alcohol", "Heavy alcohol use with metformin is warned against on its label; ask your clinician what's sensible for you."),
]

RAISES_BP = {
    "NSAID pain reliever": {"ibuprofen", "advil", "motrin", "naproxen", "aleve", "diclofenac", "celecoxib", "meloxicam"},
    "Decongestant": {"pseudoephedrine", "sudafed", "phenylephrine"},
    "Caffeine": {"caffeine", "energy drink", "pre-workout"},
}
RAISES_BP_TEXT = "Some of what you've logged can raise blood pressure; ask your pharmacist about alternatives."

# ---------------------------------------------------------------- suggestions (§26)
BP_SUGGESTIONS = [
    {"id": "BP-DASH", "title": "DASH-style eating", "effect": "Typical systolic effect about 11 mmHg in people with high blood pressure.",
     "text": "Build meals around vegetables, fruit, whole grains and low-fat dairy, with less red meat, sweets and sugary drinks.",
     "gates": ["KIDNEY_CONCERN"]},
    {"id": "BP-SODIUM", "title": "Less sodium", "effect": "Typical systolic effect about 5–6 mmHg.",
     "text": "Aim for under 1,500 mg of sodium a day, or at least 1,000 mg less than now. Most comes from packaged and restaurant food.",
     "gates": []},
    {"id": "BP-POTASSIUM", "title": "More potassium from food", "effect": "Typical systolic effect about 4–5 mmHg.",
     "text": "Fruits, vegetables, beans and yogurt — from food, not supplements.",
     "gates": ["ACE_ARB_K", "KIDNEY_CONCERN", "NO_KIDNEY_LABS"]},
    {"id": "BP-AEROBIC", "title": "Aerobic activity", "effect": "Typical systolic effect about 5–8 mmHg.",
     "text": "About 150 minutes a week of brisk walking, cycling or swimming, spread over most days.",
     "gates": ["CHEST_SYMPTOMS"]},
    {"id": "BP-RESISTANCE", "title": "Resistance and isometric training", "effect": "Typical systolic effect about 4–5 mmHg.",
     "text": "Two or three sessions a week; handgrip or wall-sit isometrics count.",
     "gates": ["CHEST_SYMPTOMS"]},
    {"id": "BP-WEIGHT", "title": "Weight loss if overweight", "effect": "About 1 mmHg for each kilogram lost, on average.",
     "text": "Small, steady changes in portions and activity.",
     "gates": ["RAPID_WEIGHT_LOSS"]},
    {"id": "BP-ALCOHOL", "title": "Limit alcohol", "effect": "Typical systolic effect about 4 mmHg.",
     "text": "No more than one drink a day for women or two for men, or none.",
     "gates": []},
    {"id": "BP-SLEEP", "title": "Sleep and stress routines", "effect": "Supportive; no single average figure.",
     "text": "A regular sleep window and a daily wind-down routine.", "gates": []},
]
GATE_TEXT = {
    "KIDNEY_CONCERN": "Held: your records show a kidney concern — ask your clinician first.",
    "ACE_ARB_K": "Held: one of your medications can raise potassium.",
    "NO_KIDNEY_LABS": "Available once kidney function is known (this year's labs).",
    "CHEST_SYMPTOMS": "Held: you've logged chest symptoms — talk to your clinician before starting new exercise.",
    "RAPID_WEIGHT_LOSS": "Held: your weight has dropped quickly — lower-target suggestions pause.",
}

HOLD_LIST = ["More potassium / potassium supplements — once kidney function is known",
             "Salt substitutes (potassium chloride) — once kidney function is known",
             "High-protein plans — once kidney function is known",
             "Iron supplements — once the relevant level is known",
             "High-dose vitamin D — once the relevant level is known"]
