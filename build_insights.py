#!/usr/bin/env python3
"""
Add per-biomarker reference ranges (red/amber/green zones) and AI insights
to bloodwork_data.json. Designed for an adult male on TRT.
"""
import json
import os
from datetime import date, datetime
from statistics import mean, median

OUTPUTS = "/Users/stevenbennett/claude/health-dashboard"

# ---------------------------------------------------------------------------
# Reference ranges (adult male, fasting where relevant). Each entry has:
#   unit:          canonical unit
#   green:         [low, high]   inclusive optimal band
#   amber:         optional [low,high] band(s) — borderline outside green
#   red_low:       optional max value below which is red (low)
#   red_high:      optional min value above which is red (high)
#   one_sided:     "low_better" | "high_better" | None
# Sources: NHS, Cleveland Clinic, US ATP-IV / ESC guidelines, Manual.co thresholds,
# Randox/Medichecks reports, plus performance-medicine consensus (Peter Attia / TRT).
# ---------------------------------------------------------------------------
REF = {
    # ---------------- HORMONES ----------------
    "Testosterone": {  # nmol/L (TRT-aware optimum is upper end of normal)
        "unit": "nmol/L",
        "amber": [[8.64, 12.0], [29.0, 35.0]],
        "green": [12.0, 29.0],
        "red_low": 8.64, "red_high": 35.0,
        "tag": "Higher end of normal is the goal on TRT (~20-30 nmol/L is typical for well-titrated protocols)."
    },
    "Free Testosterone": {  # nmol/L
        "unit": "nmol/L",
        "amber": [[0.149, 0.30], [0.50, 0.60]],
        "green": [0.30, 0.50],
        "red_low": 0.149, "red_high": 0.60,
        "tag": "Free T is the bioavailable fraction; optimum on TRT is mid-to-upper range."
    },
    "SHBG": {  # nmol/L
        "unit": "nmol/L",
        "amber": [[18.31, 25.0], [45.0, 54.1]],
        "green": [25.0, 45.0],
        "red_low": 18.31, "red_high": 54.1,
        "tag": "Low SHBG is common on TRT and means more free hormone; very low (<15) can blunt anabolic signalling."
    },
    "Oestradiol": {  # pmol/L (Randox/Manual ultrasensitive)
        "unit": "pmol/L",
        "amber": [[40, 80], [160, 220]],
        "green": [80, 160],
        "red_low": 40, "red_high": 220,
        "tag": "Sweet-spot for men on TRT is roughly 80-160 pmol/L (22-44 pg/mL). Crashed E2 hurts joints, libido and bone."
    },
    "LH": {  # IU/L
        "unit": "U/l",
        "amber": [[1.7, 2.0], [8.0, 8.6]],
        "green": [2.0, 8.0],
        "red_low": 1.7, "red_high": 8.6,
        "tag": "Suppression to <0.3 is expected on TRT — your testes are no longer being told to make T."
    },
    "FSH": {  # IU/L
        "unit": "U/l",
        "amber": [[1.5, 2.0], [10.0, 12.4]],
        "green": [2.0, 10.0],
        "red_low": 1.5, "red_high": 12.4,
        "tag": "Drives spermatogenesis. Suppressed on TRT unless HCG/HMG is co-administered."
    },
    "Prolactin": {  # mIU/L
        "unit": "mIU/l",
        "amber": [[86, 100], [280, 324]],
        "green": [100, 280],
        "red_low": 86, "red_high": 324,
        "tag": "Elevated prolactin (>500) warrants pituitary work-up and can suppress libido/T axis."
    },
    "TSH": {  # mIU/L
        "unit": "mIU/L",
        "amber": [[0.27, 0.5], [2.5, 4.2]],
        "green": [0.5, 2.5],
        "red_low": 0.27, "red_high": 4.2,
        "tag": "Functional optimum is 0.5-2.5; >2.5 with symptoms can suggest sub-clinical hypothyroidism."
    },
    "Free T4": {
        "unit": "pmol/L",
        "green": [12, 22],
        "amber": [[10, 12], [22, 24]],
        "red_low": 10, "red_high": 24,
    },

    # ---------------- LIPIDS ----------------
    "Total Cholesterol": {  # mmol/L
        "unit": "mmol/L",
        "green": [3.0, 5.0],
        "amber": [[5.0, 6.2]],
        "red_high": 6.2,
        "tag": "Less informative than ApoB / LDL-P; total chol alone is a blunt marker."
    },
    "LDL Cholesterol": {  # mmol/L
        "unit": "mmol/L",
        "green": [0, 2.6],
        "amber": [[2.6, 3.4]],
        "red_high": 3.4,
        "one_sided": "low_better",
        "tag": "For ASCVD prevention: <2.6 is optimal, <1.8 if existing risk. Lifelong exposure matters."
    },
    "HDL Cholesterol": {  # mmol/L
        "unit": "mmol/L",
        "green": [1.2, 2.5],
        "amber": [[1.0, 1.2], [2.5, 2.8]],
        "red_low": 1.0, "red_high": 2.8,
        "tag": "Sweet spot ~1.4-2.0. Very high HDL (>2.8) is paradoxically associated with risk in some studies."
    },
    "Non-HDL Cholesterol": {
        "unit": "mmol/L",
        "green": [0, 3.4],
        "amber": [[3.4, 4.0]],
        "red_high": 4.0,
        "one_sided": "low_better",
    },
    "Triglycerides": {
        "unit": "mmol/L",
        "green": [0, 1.0],
        "amber": [[1.0, 1.7]],
        "red_high": 1.7,
        "one_sided": "low_better",
        "tag": "<1.0 is metabolically optimal; high TG implies insulin resistance and small dense LDL."
    },
    "Total Cholesterol / HDL Ratio": {
        "unit": "ratio",
        "green": [0, 3.5],
        "amber": [[3.5, 5.0]],
        "red_high": 5.0,
        "one_sided": "low_better",
    },
    "Apolipoprotein B": {
        "unit": "g/L",
        "green": [0, 0.9],
        "amber": [[0.9, 1.0]],
        "red_high": 1.0,
        "one_sided": "low_better",
        "tag": "ApoB counts atherogenic particles directly — the single best lipid marker. Aim <0.9 g/L (<90 mg/dL)."
    },
    "Apolipoprotein A1": {
        "unit": "g/L",
        "green": [1.25, 2.0],
        "amber": [[1.0, 1.25]],
        "red_low": 1.0,
    },
    "ApoB / ApoA Ratio": {
        "unit": "ratio",
        "green": [0, 0.7],
        "amber": [[0.7, 0.9]],
        "red_high": 0.9,
        "one_sided": "low_better",
    },
    "Lipoprotein(a)": {
        "unit": "nmol/L",
        "green": [0, 75],
        "amber": [[75, 125]],
        "red_high": 125,
        "one_sided": "low_better",
        "tag": "Genetically determined; <75 nmol/L is reassuring. High Lp(a) is independent ASCVD risk."
    },

    # ---------------- LIVER ----------------
    "ALT": {
        "unit": "U/L",
        "green": [10, 33],
        "amber": [[33, 50]],
        "red_high": 50,
    },
    "AST": {
        "unit": "U/L",
        "green": [10, 35],
        "amber": [[35, 50]],
        "red_high": 50,
    },
    "GGT": {
        "unit": "U/L",
        "green": [9, 40],
        "amber": [[40, 60]],
        "red_high": 60,
    },
    "ALP": {
        "unit": "U/L",
        "green": [30, 130],
        "amber": [[130, 150]],
        "red_high": 150,
    },
    "Albumin": {
        "unit": "g/L",
        "green": [38, 50],
        "amber": [[35, 38], [50, 52]],
        "red_low": 35, "red_high": 52,
    },
    "Globulin": {
        "unit": "g/L",
        "green": [20, 35],
        "amber": [[18, 20], [35, 38]],
        "red_low": 18, "red_high": 38,
    },
    "Total Protein": {
        "unit": "g/L",
        "green": [60, 80],
        "amber": [[57, 60], [80, 84]],
        "red_low": 57, "red_high": 84,
    },
    "Bilirubin": {
        "unit": "umol/L",
        "green": [3, 21],
        "amber": [[21, 30]],
        "red_high": 30,
    },

    # ---------------- KIDNEY ----------------
    "Creatinine": {
        "unit": "umol/L",
        "green": [60, 110],
        "amber": [[55, 60], [110, 120]],
        "red_low": 55, "red_high": 120,
    },
    "Urea": {
        "unit": "mmol/L",
        "green": [2.5, 7.8],
        "amber": [[7.8, 9.0]],
        "red_high": 9.0,
    },
    "Sodium": {
        "unit": "mmol/L",
        "green": [136, 145],
        "amber": [[133, 136], [145, 148]],
        "red_low": 133, "red_high": 148,
    },
    "eGFR": {
        "unit": "mL/min/1.73m²",
        "green": [90, 200],
        "amber": [[60, 90]],
        "red_low": 60,
        "one_sided": "high_better",
    },

    # ---------------- CBC ----------------
    "Haemoglobin": {
        "unit": "g/L",
        "green": [135, 170],
        "amber": [[130, 135], [170, 180]],
        "red_low": 130, "red_high": 180,
        "tag": "TRT raises Hgb. Donate blood if it climbs over 175 to manage haematocrit."
    },
    "Haematocrit": {
        "unit": "%",
        "green": [40, 50],
        "amber": [[38, 40], [50, 52]],
        "red_low": 38, "red_high": 52,
        "tag": "Aim to keep <52% on TRT to manage stroke/clot risk."
    },
    "Red Blood Cells": {
        "unit": "10^12/L",
        "green": [4.5, 5.9],
        "amber": [[4.3, 4.5], [5.9, 6.2]],
        "red_low": 4.3, "red_high": 6.2,
    },
    "White Blood Cells": {
        "unit": "10^9/L",
        "green": [4.0, 10.0],
        "amber": [[3.5, 4.0], [10.0, 11.5]],
        "red_low": 3.5, "red_high": 11.5,
    },
    "Platelets": {
        "unit": "10^9/L",
        "green": [150, 400],
        "amber": [[140, 150], [400, 450]],
        "red_low": 140, "red_high": 450,
    },
    "MCV": {
        "unit": "fL",
        "green": [80, 100],
        "amber": [[78, 80], [100, 102]],
        "red_low": 78, "red_high": 102,
    },
    "MCH": {
        "unit": "pg",
        "green": [27, 33],
        "amber": [[26, 27], [33, 34]],
        "red_low": 26, "red_high": 34,
    },
    "MCHC": {
        "unit": "g/L",
        "green": [320, 360],
        "amber": [[310, 320], [360, 370]],
        "red_low": 310, "red_high": 370,
    },
    "Neutrophils": {
        "unit": "10^9/L",
        "green": [1.8, 7.5],
        "amber": [[1.5, 1.8], [7.5, 8.5]],
        "red_low": 1.5, "red_high": 8.5,
    },
    "Lymphocytes": {
        "unit": "10^9/L",
        "green": [1.0, 4.0],
        "amber": [[0.9, 1.0], [4.0, 4.5]],
        "red_low": 0.9, "red_high": 4.5,
    },
    "Monocytes": {
        "unit": "10^9/L",
        "green": [0.2, 1.0],
        "amber": [[0.15, 0.2], [1.0, 1.2]],
        "red_low": 0.15, "red_high": 1.2,
    },
    "Eosinophils": {
        "unit": "10^9/L",
        "green": [0.0, 0.5],
        "amber": [[0.5, 0.7]],
        "red_high": 0.7,
    },
    "Basophils": {
        "unit": "10^9/L",
        "green": [0.0, 0.1],
        "amber": [[0.1, 0.2]],
        "red_high": 0.2,
    },

    # ---------------- METABOLIC ----------------
    "Glucose": {
        "unit": "mmol/L",
        "green": [3.9, 5.4],
        "amber": [[5.4, 6.0]],
        "red_high": 6.0,
        "tag": "Fasting glucose should sit <5.4 mmol/L (97 mg/dL). Above 6 = pre-diabetes."
    },
    "HbA1c": {
        "unit": "mmol/mol",
        "green": [0, 38],
        "amber": [[38, 42]],
        "red_high": 42,
        "one_sided": "low_better",
        "tag": "<38 mmol/mol (5.6%) is metabolically optimal."
    },
    "Uric Acid": {
        "unit": "umol/L",
        "green": [200, 380],
        "amber": [[380, 430]],
        "red_high": 430,
    },

    # ---------------- INFLAMMATION ----------------
    "CRP (high sensitivity)": {
        "unit": "mg/L",
        "green": [0, 1.0],
        "amber": [[1.0, 3.0]],
        "red_high": 3.0,
        "one_sided": "low_better",
        "tag": "<1.0 mg/L = low cardiovascular risk. Acute illness can spike it transiently."
    },

    # ---------------- NUTRIENTS ----------------
    "Ferritin": {
        "unit": "ug/L",
        "green": [50, 200],
        "amber": [[30, 50], [200, 300]],
        "red_low": 30, "red_high": 300,
    },
    "Iron": {
        "unit": "umol/L",
        "green": [11, 30],
        "amber": [[10, 11], [30, 32]],
        "red_low": 10, "red_high": 32,
    },
    "TIBC": {
        "unit": "umol/L",
        "green": [45, 75],
        "amber": [[40, 45], [75, 80]],
        "red_low": 40, "red_high": 80,
    },
    "Vitamin D": {
        "unit": "nmol/L",
        "green": [75, 175],
        "amber": [[50, 75], [175, 200]],
        "red_low": 50, "red_high": 200,
        "tag": "Aim for 75-150 nmol/L (30-60 ng/mL). Deficiency hurts T, mood, immunity."
    },
    "Vitamin B12 (active)": {
        "unit": "pmol/L",
        "green": [37, 150],
        "amber": [[25, 37]],
        "red_low": 25,
    },
    "Folate": {
        "unit": "ug/L",
        "green": [3.9, 20],
        "amber": [[3.0, 3.9]],
        "red_low": 3.0,
    },
    "Magnesium": {
        "unit": "mmol/L",
        "green": [0.75, 1.0],
        "amber": [[0.70, 0.75]],
        "red_low": 0.70,
    },
    "Calcium": {
        "unit": "mmol/L",
        "green": [2.20, 2.55],
        "amber": [[2.10, 2.20], [2.55, 2.65]],
        "red_low": 2.10, "red_high": 2.65,
    },
    "Corrected Calcium": {
        "unit": "mmol/L",
        "green": [2.20, 2.55],
        "amber": [[2.10, 2.20], [2.55, 2.65]],
        "red_low": 2.10, "red_high": 2.65,
    },
    "Phosphate": {
        "unit": "mmol/L",
        "green": [0.80, 1.40],
        "amber": [[0.74, 0.80], [1.40, 1.50]],
        "red_low": 0.74, "red_high": 1.50,
    },
    "Zinc": {
        "unit": "umol/L",
        "green": [11, 24],
        "amber": [[10, 11], [24, 25]],
        "red_low": 10, "red_high": 25,
    },

    # ---------------- OTHER ----------------
    "PSA": {
        "unit": "ug/L",
        "green": [0, 1.5],
        "amber": [[1.5, 3.0]],
        "red_high": 3.0,
        "one_sided": "low_better",
        "tag": "TRT can mildly elevate PSA but >1.5 deserves monitoring; >3 needs urology referral."
    },

    # ---------------- BIOMETRICS ----------------
    "BMI": {
        "unit": "kg/m²",
        "green": [20, 25],
        "amber": [[18.5, 20], [25, 28]],
        "red_low": 18.5, "red_high": 28,
        "tag": "BMI is unreliable for muscular individuals — body fat % is the better metric."
    },
    "Body Fat %": {
        "unit": "%",
        "green": [10, 18],
        "amber": [[8, 10], [18, 22]],
        "red_low": 8, "red_high": 22,
        "tag": "10-15% is athletic-lean for males; <8% is unsustainable; >22% raises metabolic risk."
    },
    "Visceral Fat": {
        "unit": "level",
        "green": [0, 9],
        "amber": [[9, 13]],
        "red_high": 13,
        "one_sided": "low_better",
    },
    "Resting Heart Rate": {
        "unit": "bpm",
        "green": [50, 70],
        "amber": [[45, 50], [70, 80]],
        "red_low": 45, "red_high": 80,
        "tag": "Trained adults sit 50-70; below 45 with symptoms = bradycardia work-up."
    },
    "VO2 Max": {
        "unit": "mL/kg/min",
        "green": [40, 200],
        "amber": [[35, 40]],
        "red_low": 35,
        "one_sided": "high_better",
        "tag": "Gold-standard cardiorespiratory fitness marker. >50 is excellent for an adult male; higher is always better."
    },
    "HRV (SDNN)": {
        "unit": "ms",
        "green": [50, 150],
        "amber": [[30, 50], [150, 200]],
        "red_low": 30, "red_high": 200,
        "tag": "Higher HRV indicates better autonomic recovery. Track the 7-day rolling average, not individual days."
    },
    "Walking Heart Rate": {
        "unit": "bpm",
        "green": [80, 120],
        "amber": [[70, 80], [120, 140]],
        "red_high": 140,
        "tag": "Aerobic fitness indicator — lower walking HR at the same pace reflects better cardiovascular conditioning."
    },
    "Respiratory Rate": {
        "unit": "breaths/min",
        "green": [12, 20],
        "amber": [[10, 12], [20, 22]],
        "red_low": 10, "red_high": 22,
        "tag": "12-20 is normal at rest. Persistent elevation can signal illness, stress or deconditioning."
    },
    "Systolic BP": {
        "unit": "mmHg",
        "green": [105, 125],
        "amber": [[125, 135]],
        "red_high": 135,
        "one_sided": "low_better",
    },
    "Diastolic BP": {
        "unit": "mmHg",
        "green": [65, 80],
        "amber": [[80, 85]],
        "red_high": 85,
        "one_sided": "low_better",
    },
    "Weight": {
        "unit": "kg",
        # No universal range — tag only.
        "tag": "Track in conjunction with body-fat % to know whether weight changes are muscle or fat."
    },

    # ---------------- ACTIVITY ----------------
    "Active Energy Burned": {
        "unit": "kcal",
        "green": [300, 900],
        "amber": [[200, 300], [900, 1200]],
        "red_low": 200,
        "tag": "Calories burned through movement and exercise. 400-800 kcal/day is typical for active adults."
    },
    "Basal Energy Burned": {
        "unit": "kcal",
        "green": [1500, 2200],
        "amber": [[1300, 1500], [2200, 2500]],
        "red_low": 1300,
        "tag": "Resting metabolic rate — calories burned just existing. Driven by lean mass, age, and hormones."
    },
    "Step Count": {
        "unit": "steps",
        "green": [7000, 15000],
        "amber": [[5000, 7000]],
        "red_low": 5000,
        "one_sided": "high_better",
        "tag": "8000-10000 steps/day is the sweet spot for all-cause mortality reduction. Beyond 12000 gives diminishing returns."
    },
    "Distance Walking/Running": {
        "unit": "km",
        "green": [4, 12],
        "amber": [[2, 4]],
        "red_low": 2,
        "one_sided": "high_better",
        "tag": "Daily distance from walking and running combined. Tracks closely with step count."
    },
    "Lean Body Mass": {
        "unit": "kg",
        "green": [60, 85],
        "amber": [[55, 60], [85, 95]],
        "red_low": 55,
        "tag": "Total weight minus fat. Higher lean mass = better metabolic health, strength, and longevity."
    },

    # ---------------- NUTRITION ----------------
    "Calories In": {
        "unit": "kcal",
        "green": [1800, 2800],
        "amber": [[1500, 1800], [2800, 3200]],
        "red_low": 1500, "red_high": 3200,
        "tag": "Daily caloric intake. Target depends on goal: deficit for fat loss, surplus for muscle gain."
    },
    "Protein": {
        "unit": "g",
        "green": [130, 220],
        "amber": [[100, 130]],
        "red_low": 100,
        "one_sided": "high_better",
        "tag": "1.6-2.2 g/kg bodyweight is optimal for muscle retention and growth. Higher during cuts."
    },
    "Carbs": {
        "unit": "g",
        "green": [150, 350],
        "amber": [[100, 150], [350, 450]],
        "red_low": 100,
        "tag": "Primary fuel for high-intensity training. Adjust based on activity level and goals."
    },
    "Fat": {
        "unit": "g",
        "green": [50, 100],
        "amber": [[40, 50], [100, 130]],
        "red_low": 40, "red_high": 130,
        "tag": "Essential for hormones (especially testosterone). Don't drop below 0.7 g/kg during cuts."
    },
    "TDEE": {
        "unit": "kcal",
        "green": [2200, 3500],
        "amber": [[1800, 2200]],
        "red_low": 1800,
        "tag": "Total Daily Energy Expenditure = basal + active calories. The target your intake works against."
    },
    "Daily Deficit": {
        "unit": "kcal",
        "green": [0, 750],
        "amber": [[-300, 0], [750, 1000]],
        "red_high": 1000,
        "tag": "Positive = calorie deficit (fat loss). 300-500 kcal deficit is sustainable. >1000 risks muscle loss."
    },
}


# ---------------------------------------------------------------------------
# Personalized authored insights for the most clinically important biomarkers.
# Authored by Claude based on your full dataset. Steve = adult male on TRT.
# ---------------------------------------------------------------------------
PERSONAL = {
    "Testosterone": (
        "Total T tracks into the optimal TRT band (mid-20s–low-30s nmol/L). The 31.6 nmol/L reading on 13 Feb 2026 "
        "is just above the lab’s upper reference but is where most well-titrated protocols land. "
        "Optimise: (1) standardise blood-draw timing — take samples at the trough (day before next injection) so results are comparable; "
        "(2) if consistently >30, consider shortening injection frequency (e.g. E3.5D → E3D) to flatten peaks; "
        "(3) don’t chase higher numbers — above 30 nmol/L adds haematocrit and estradiol problems without clear benefit."
    ),
    "Free Testosterone": (
        "Free T is upper-optimal — exactly what we want on TRT. Combined with low SHBG it means your bioavailable T is ample. "
        "Optimise: (1) if libido/energy flag despite good free T, check oestradiol and thyroid first; (2) caffeine, alcohol "
        "and poor sleep transiently lower free T — protect sleep above all."
    ),
    "SHBG": (
        "Low SHBG is typical on TRT and amplifies free hormone effect. As long as it stays >18 nmol/L, no concern. "
        "Persistent low SHBG + high insulin = metabolic syndrome flag. "
        "Optimise: (1) keep fasting insulin low (weight training + fibre + low refined carbs); (2) adequate dietary protein "
        "(1.6–2.2g/kg) and boron 3–10 mg/day can modestly raise SHBG if you want more; (3) excess bodyfat and liver fat drive SHBG down — "
        "keep visceral fat low."
    ),
    "Oestradiol": (
        "Two clear outliers — 2321 pmol/L (Nov 2025) and 1104 pmol/L (Feb 2026) — almost certainly assay errors (non-ultrasensitive ELISA). "
        "Real readings cluster 80–160 pmol/L which is the male TRT sweet spot. "
        "Optimise: (1) always request LC-MS/MS or ultrasensitive E2 — standard ECLIA is unreliable at male levels; "
        "(2) mark the outliers erroneous in this dashboard; (3) avoid reactive AI dosing based on one-off results — "
        "crashed E2 wrecks joints, libido and bone; (4) if E2 genuinely runs high at trough, lower injection dose slightly "
        "before reaching for anastrozole."
    ),
    "LH": (
        "Suppressed below detection on TRT — expected (pituitary isn’t signalling the testes). "
        "Optimise: (1) if you want to preserve testicular volume and fertility, add HCG 250 IU EOD alongside TRT; "
        "(2) no action needed otherwise."
    ),
    "FSH": (
        "Suppressed on TRT — expected. "
        "Optimise: (1) conceiving? Add HMG or HCG+HMG; (2) otherwise no action."
    ),
    "Prolactin": (
        "Mid-range and stable — no pituitary trouble. "
        "Optimise: (1) re-test if nipple sensitivity/galactorrhoea, loss of libido or visual changes appear; "
        "(2) large-meal stress-testing can transiently elevate it, so fast before testing; (3) B6 50 mg/day gently lowers prolactin if needed."
    ),
    "Total Cholesterol": (
        "Hovering ~5 mmol/L — borderline on NHS thresholds, but total cholesterol is a blunt marker. ApoB and LDL matter more. "
        "Optimise: (1) don’t fixate on total — focus on ApoB; (2) same dietary levers as LDL (fibre, EVOO, plant sterols, less saturated fat)."
    ),
    "LDL Cholesterol": (
        "LDL has crept to 3.27 mmol/L (target <2.6). Combined with elevated ApoB this is the most addressable item in your panel. "
        "Optimise: (1) 30–40 g/day soluble fibre (oats, psyllium husk 5–10g, beans, berries) — drops LDL ~10%; "
        "(2) replace saturated fat with EVOO 2–3 tbsp/day and 25–40g mixed nuts; (3) add fatty fish 2×/week or 1–2g EPA+DHA; "
        "(4) plant sterols/stanols 2g/day can drop LDL another 6–10%; (5) re-test in 3 months — if still >2.6, discuss rosuvastatin 5–10mg "
        "with your prescriber. Cumulative ApoB exposure in your 30s matters most."
    ),
    "HDL Cholesterol": (
        "Solid HDL ~1.5 mmol/L — protective range. TRT often nudges HDL down; yours is holding up. "
        "Optimise: (1) keep aerobic volume 150+ min/week (Zone 2); (2) EVOO, nuts, fatty fish all nudge HDL up; "
        "(3) avoid excess refined carbs and added sugar which suppress HDL."
    ),
    "Non-HDL Cholesterol": (
        "Just under the 4 mmol/L threshold but drifting up. Same driver as LDL/ApoB. "
        "Optimise: follow the LDL playbook (fibre, EVOO, plant sterols, less sat-fat) — it moves all three metrics together."
    ),
    "Triglycerides": (
        "Holding under 1 mmol/L — excellent. Indicates no insulin resistance and protective particle profile. "
        "Optimise: (1) maintain resistance training and low refined-carb intake; (2) 1–2g/day EPA+DHA keeps TG in check; "
        "(3) watch alcohol — the single biggest acute driver of TG."
    ),
    "Total Cholesterol / HDL Ratio": (
        "Comfortably under 5 — reassuring even with borderline absolute LDL. "
        "Optimise: addressing ApoB/LDL will drop this further automatically."
    ),
    "Apolipoprotein B": (
        "ApoB 0.96 g/L — borderline-high (target <0.9, ideal <0.8). ApoB is THE best lipid marker because it counts atherogenic particles directly. "
        "This is your single biggest optimisation target. "
        "Optimise: (1) 30–40g soluble fibre daily; (2) swap saturated fats for mono/polyunsaturated (EVOO, nuts, avocado, fatty fish); "
        "(3) 2g/day phytosterols; (4) Zone 2 cardio 150+ min/week; (5) re-test at 3 months; "
        "(6) if still >0.9, rosuvastatin 5mg is highly effective and well-tolerated — most men drop ApoB 30-40%."
    ),
    "Lipoprotein(a)": (
        "Lp(a) <7 nmol/L — excellent. You don’t carry the genetic Lp(a) risk that ~20% of people do, and this number won’t change. "
        "Optimise: no action needed — one good reading is enough. Keep the lucky genes."
    ),
    "ApoB / ApoA Ratio": (
        "Borderline, driven by elevated ApoB. "
        "Optimise: the ApoB playbook (fibre, EVOO, plant sterols, less sat-fat, cardio) improves this automatically."
    ),
    "ALT": (
        "Within range — no liver stress. TRT alone doesn’t raise ALT; only oral 17-alpha-alkylated compounds do. "
        "Optimise: (1) avoid oral steroids and excess acetaminophen stacking; (2) keep alcohol moderate; "
        "(3) milk thistle 150mg/day and NAC 600mg/day are low-risk liver supports during heavy cuts if you want them."
    ),
    "AST": (
        "Within range. Mild transient bumps after heavy training are normal. "
        "Optimise: schedule bloods at least 48h after your heaviest session to avoid training-induced artefacts."
    ),
    "GGT": (
        "Good — GGT is the most alcohol-sensitive liver enzyme and yours is comfortable. "
        "Optimise: (1) stay under 14 units/week; (2) 3+ alcohol-free days weekly lets GGT reset."
    ),
    "ALP": (
        "Normal. Rises in bone growth and pregnancy; in adults, persistent elevation warrants bone/biliary work-up. "
        "Optimise: nothing needed — just keep adequate Vit D and calcium for bone health."
    ),
    "Albumin": (
        "Upper-normal range — reflects good protein status and hydration. "
        "Optimise: (1) maintain 1.6–2.2 g/kg/day protein; (2) hydrate consistently — low albumin can just be haemodilution; "
        "(3) persistent drops warrant renal/liver/nutritional investigation."
    ),
    "Bilirubin": (
        "Normal. Mildly elevated total bilirubin is often Gilbert’s syndrome (benign). "
        "Optimise: no action."
    ),
    "Creatinine": (
        "Upper-normal — typical for muscular men since creatinine comes from muscle turnover. Not a kidney concern if eGFR is preserved. "
        "Optimise: (1) avoid bloods within 48h of heavy training and creatine loading to get the real number; "
        "(2) stay well hydrated around draws."
    ),
    "Urea": (
        "Within range. Higher values on high-protein diets are normal as long as eGFR is fine. "
        "Optimise: hydrate, don’t need to cut protein — kidney filtration capacity is unaffected by high protein in healthy adults."
    ),
    "eGFR": (
        "Above 90 mL/min — filtration is preserved. Note creatinine-based eGFR underestimates true filtration in muscular men. "
        "Optimise: (1) request cystatin-C-based eGFR next time if you want a more accurate read; "
        "(2) blood pressure control is the #1 kidney longevity lever — keep systolic <125; "
        "(3) stay well hydrated."
    ),
    "Sodium": (
        "Normal. Watch during extreme heat or long endurance events. "
        "Optimise: (1) adequate sodium intake is healthy — don’t fear salt; (2) electrolytes during long training sessions (1000+ mg Na in 16oz water)."
    ),
    "Haemoglobin": (
        "Upper-normal, consistent with TRT-driven erythropoiesis. "
        "Optimise: (1) routine whole-blood donation every 12–16 weeks; (2) if Hb >175 g/L persists, reduce TRT dose or shorten injection frequency; "
        "(3) stay well hydrated around testing — dehydration falsely elevates Hb."
    ),
    "Haematocrit": (
        "TRT raises Hct. Keep it under 52% — blood donation is primary management. Above 54% meaningfully raises stroke/clot risk. "
        "Optimise: (1) donate every 12–16 weeks; (2) manage BP aggressively; (3) stay well hydrated; "
        "(4) if Hct creeps past 52% despite donation, reduce TRT dose — prevention beats reaction."
    ),
    "Red Blood Cells": (
        "Tracks with Hb/Hct — same TRT story. "
        "Optimise: donation cadence keeps all three markers in check."
    ),
    "White Blood Cells": (
        "Normal — no chronic infection or immune issue. "
        "Optimise: adequate sleep, vitamin D, zinc, and omega-3s all support healthy immune function."
    ),
    "Platelets": (
        "Normal and stable. "
        "Optimise: nothing needed. Avoid excessive NSAID use on TRT (already slightly pro-thrombotic)."
    ),
    "Neutrophils": "Normal. Maintain good sleep and nutrition for stable immune baseline.",
    "Lymphocytes": "Normal. Adequate protein, vitamin D, and sleep support this baseline.",
    "Monocytes": "Normal.",
    "Eosinophils": "Normal — no allergic/parasitic burden. No action.",
    "Basophils": "Normal.",
    "Glucose": (
        "Fasting glucose is good. "
        "Optimise: (1) pair with HbA1c and fasting insulin for full insulin-sensitivity picture; "
        "(2) protein + fibre before carbs blunts postprandial spikes; "
        "(3) 10-minute walk after meals improves glucose disposal by ~30%; "
        "(4) short cuts with proper refeeds don’t hurt glucose; extreme ones can."
    ),
    "HbA1c": (
        "34 mmol/mol (5.3%) — metabolically excellent. "
        "Optimise: maintain by (1) resistance training 3–4×/week; (2) 150+ min Zone 2 cardio; (3) low refined-carb intake; "
        "(4) avoid prolonged sedentary blocks >1h."
    ),
    "Uric Acid": (
        "Within range. Elevated uric acid raises gout and metabolic risk. "
        "Optimise: (1) keep fructose intake moderate (<50g/day); (2) moderate alcohol — beer and spirits drive it up fastest; "
        "(3) stay well hydrated; (4) cherries and coffee modestly lower it."
    ),
    "CRP (high sensitivity)": (
        "0.24 mg/L — excellent. Low background inflammation. "
        "Optimise: (1) retest 2 weeks later if it spikes acutely before assuming chronic inflammation; "
        "(2) sleep, Zone 2 cardio, omega-3s, and low visceral fat all keep hs-CRP suppressed; "
        "(3) avoid the overtraining + under-sleeping combination — it drives CRP up."
    ),
    "PSA": (
        "Below 1 ug/L — reassuring. TRT can mildly elevate PSA over years; annual monitoring is standard. "
        "Optimise: (1) annual PSA is sufficient at your level; (2) avoid ejaculation and heavy cycling in the 48h before a PSA draw — "
        "both transiently elevate it; (3) if it ever climbs >1.5, recheck in 6–8 weeks before worrying; >3 needs urology."
    ),
    "Vitamin D": (
        "Aim 75–150 nmol/L. Vitamin D affects testosterone, mood, immunity and bone. "
        "Optimise: (1) 2000–4000 IU/day D3 maintenance; (2) take with fatty meal for absorption; "
        "(3) co-supplement vitamin K2 (100–200 mcg MK-7) to direct calcium to bone not arteries; "
        "(4) retest after 3 months at a new dose."
    ),
    "Ferritin": (
        "Iron storage marker. Sweet spot 50–200 ug/L. "
        "Optimise: (1) if <50, add iron-rich foods (red meat, liver, lentils) or iron bisglycinate 25mg 3×/week; "
        "(2) if >300, rule out haemochromatosis and inflammation — blood donation clears excess iron; "
        "(3) vitamin C with iron aids absorption; coffee/tea with meals blocks it."
    ),
    "Iron": (
        "Within normal — pair with TIBC and ferritin for the full picture. "
        "Optimise: same as ferritin if drifting; most men don’t need supplementation."
    ),
    "TIBC": "Within normal range. Optimise: no action needed unless paired with abnormal ferritin/iron.",
    "BMI": (
        "BMI is unreliable for trained individuals — body-fat % is the better metric. "
        "Optimise: use body-fat % and waist-to-height ratio (<0.5) as primary measures."
    ),
    "Body Fat %": (
        "10–15% is athletic-lean — the TRT sweet spot. <8% suppresses libido and recovery; >22% raises metabolic risk. "
        "Optimise: (1) cut slowly (0.5% bodyweight/week max) to preserve muscle; (2) keep protein at 1.8–2.2g/kg during deficits; "
        "(3) 3–4 resistance sessions/week minimum during cuts; (4) refeed every 7–10 days if cut is deep."
    ),
    "Visceral Fat": (
        "Low visceral fat is the single biggest metabolic-longevity lever. "
        "Optimise: (1) resistance training + Zone 2 cardio; (2) low refined carbs and added sugar; "
        "(3) adequate sleep (poor sleep drives visceral deposition); (4) manage alcohol."
    ),
    "Resting Heart Rate": (
        "Trained adults sit 50–70 bpm. Your trend is slightly up — worth watching. "
        "Optimise: (1) Zone 2 cardio 150+ min/week is the single biggest RHR-lowering lever; "
        "(2) track HRV in parallel — if HRV drops alongside RHR rise, you're under-recovered; "
        "(3) limit caffeine after noon; (4) sleep 7–8h consistently."
    ),
    "VO2 Max": (
        "Apple Watch VO2 Max is an estimate derived from walking/running GPS + heart rate — not a true lab test. "
        "Don't compare Apple Watch values directly to lab-measured VO2 Max from other sources; instead track "
        "the Apple Watch trend over time. A rising trend means your cardiorespiratory fitness is improving. "
        "Optimise: (1) Zone 2 cardio 150+ min/week is the primary driver; (2) add 1-2 high-intensity interval sessions/week; "
        "(3) consistency beats intensity — 4×30min is better than 1×2hr."
    ),
    "HRV (SDNN)": (
        "Sleep quality is the #1 lever for HRV. Alcohol suppresses HRV for 24-48h even at moderate intake. "
        "Track the 7-day rolling average rather than reacting to individual days — daily variation is high. "
        "Optimise: (1) consistent sleep/wake times ±30min; (2) no alcohol within 3h of bed; "
        "(3) Zone 2 cardio raises baseline HRV over weeks; (4) evening downregulation (breath work, reading) "
        "improves overnight HRV."
    ),
    "Walking Heart Rate": (
        "Walking heart rate average reflects aerobic fitness during low-intensity movement. A lower value at "
        "the same walking pace indicates better cardiovascular conditioning. "
        "Optimise: (1) Zone 2 training progressively lowers walking HR; (2) hydration and heat both raise it "
        "acutely — compare readings from similar conditions; (3) a sudden persistent rise may signal illness "
        "or overtraining."
    ),
    "Respiratory Rate": (
        "12–20 breaths/min is normal at rest. Apple Watch measures this overnight. "
        "A persistent rise above your personal baseline is an early illness signal — often showing up before "
        "symptoms. Optimise: (1) nasal breathing during exercise improves respiratory efficiency; "
        "(2) elevated respiratory rate + dropping HRV = possible illness onset — consider a rest day."
    ),
    "Systolic BP": (
        "Aim <125 mmHg. TRT can nudge BP up via haematocrit and sodium retention. "
        "Optimise: (1) manage haematocrit with donations; (2) Zone 2 cardio; (3) 3000–5000mg/day dietary potassium "
        "(leafy greens, potatoes, bananas); (4) limit alcohol; (5) home cuff monthly — office readings are unreliable."
    ),
    "Diastolic BP": (
        "Aim <80 mmHg. Same drivers as systolic. "
        "Optimise: Zone 2 cardio, potassium-rich diet, weight management, alcohol moderation, stress management."
    ),
    "Weight": (
        "Track alongside body-fat % — weight alone tells you nothing about composition. "
        "Optimise: (1) weigh daily at the same time; use 7-day rolling average; (2) body-fat % quarterly via same-method measurement (DEXA ideal, calipers consistent second-best)."
    ),
    "Active Energy Burned": (
        "Active calories from Apple Watch — includes exercise and general movement above resting. "
        "Optimise: (1) aim for 400-800 kcal/day active burn through a mix of Zone 2 cardio and resistance training; "
        "(2) NEAT (non-exercise activity) often contributes more than formal exercise — walk more, stand more; "
        "(3) pair with nutrition logging to calculate accurate daily deficit."
    ),
    "Basal Energy Burned": (
        "Your resting metabolic rate as estimated by Apple Watch. Driven primarily by lean body mass, age, and hormones. "
        "TRT supports higher basal metabolism by maintaining lean mass. "
        "Optimise: (1) resistance training is the #1 lever — more muscle = higher BMR; "
        "(2) adequate protein (1.6-2.2g/kg) preserves lean mass during cuts; "
        "(3) don't crash-diet — severe restriction downregulates BMR."
    ),
    "Step Count": (
        "Daily step count from Apple Watch. Research shows 8000-10000 steps/day reduces all-cause mortality by ~50% "
        "compared to sedentary baselines. Beyond 12000, benefits plateau. "
        "Optimise: (1) aim for 8000+ daily; (2) walk after meals for glucose disposal; "
        "(3) take calls walking; (4) steps are NEAT — they don't replace structured exercise but complement it powerfully."
    ),
    "Distance Walking/Running": (
        "Combined walking and running distance from Apple Watch GPS. Closely tracks step count. "
        "Optimise: (1) use this alongside step count to gauge intensity — more distance per step = running/jogging; "
        "(2) aim for 5-8 km/day minimum through walking alone."
    ),
    "Lean Body Mass": (
        "Total body weight minus fat mass. The single best body composition metric for health and longevity. "
        "TRT supports lean mass maintenance and growth. "
        "Optimise: (1) resistance train 3-4x/week; (2) protein 1.6-2.2 g/kg/day; "
        "(3) track trend over months, not individual readings; (4) preserve during cuts with slow deficit and high protein."
    ),
    "Calories In": (
        "Daily caloric intake from nutrition logging. The input side of the energy balance equation. "
        "Optimise: (1) log consistently — accuracy matters more than precision; "
        "(2) during cuts, target 300-500 kcal below TDEE; (3) during lean bulk, target 200-300 kcal above TDEE; "
        "(4) prioritise protein first, then fill remaining calories with carbs and fat."
    ),
    "Protein": (
        "Daily protein intake in grams. The most important macronutrient for body composition on TRT. "
        "Optimise: (1) aim for 1.6-2.2 g/kg bodyweight daily; (2) during aggressive cuts, push to 2.2-2.5 g/kg to preserve muscle; "
        "(3) distribute across 4-5 meals with 30-50g per serving for maximal MPS; "
        "(4) leucine-rich sources (whey, eggs, meat) are most anabolic."
    ),
    "Carbs": (
        "Daily carbohydrate intake. Primary fuel for resistance training and high-intensity work. "
        "Optimise: (1) prioritise around training — pre and post workout; "
        "(2) higher carb days on heavy training days, lower on rest days; "
        "(3) focus on whole food sources (rice, oats, potatoes, fruit) over refined; "
        "(4) fibre counts here — aim for 30-40g/day total fibre for ApoB management."
    ),
    "Fat": (
        "Daily fat intake. Essential for testosterone production, hormone signalling, and cell membranes. "
        "Optimise: (1) don't drop below 0.7 g/kg during cuts — low fat crashes hormones; "
        "(2) prioritise mono/polyunsaturated (EVOO, nuts, avocado, fatty fish) over saturated for ApoB management; "
        "(3) omega-3 (EPA+DHA 1-2g/day) from fish or supplement."
    ),
    "TDEE": (
        "Total Daily Energy Expenditure calculated from Apple Watch basal + active energy. "
        "This is what your body actually burns — your intake target is relative to this number. "
        "Optimise: (1) increase TDEE by adding NEAT (steps, standing) rather than just formal exercise; "
        "(2) resistance training raises basal component over time; (3) track weekly average TDEE, not daily."
    ),
    "Daily Deficit": (
        "Calorie deficit = TDEE minus Calories In. Positive means fat loss, negative means surplus. "
        "Optimise: (1) 300-500 kcal deficit is sustainable for fat loss while preserving muscle; "
        "(2) >750 kcal deficit risks muscle loss even on TRT; (3) during lean bulk, aim for -200 to -300 (slight surplus); "
        "(4) track weekly average deficit — daily fluctuation is normal and expected."
    ),
}

# ---------------------------------------------------------------------------
# Compute analysis
# ---------------------------------------------------------------------------
def classify_value(ref, value):
    """Return 'green'|'amber'|'red'|None for a value given a reference dict."""
    if ref is None or value is None:
        return None
    if "green" in ref:
        lo, hi = ref["green"]
        if lo <= value <= hi:
            return "green"
    for band in ref.get("amber", []) or []:
        lo, hi = band
        if lo <= value <= hi:
            return "amber"
    if "red_low" in ref and value < ref["red_low"]:
        return "red"
    if "red_high" in ref and value > ref["red_high"]:
        return "red"
    # Outside green and amber — call it red
    if "green" in ref:
        return "red"
    return None


def compute_stats(rows, erroneous=None):
    erroneous = erroneous or set()
    valid = [r for r in rows if r.get("value") is not None and r.get("date") and r["id"] not in erroneous]
    valid.sort(key=lambda r: r["date"])
    if not valid:
        return None
    nums = [r["value"] for r in valid]
    first, last = valid[0], valid[-1]
    days = (datetime.fromisoformat(last["date"]) - datetime.fromisoformat(first["date"])).days
    pct = ((last["value"] - first["value"]) / first["value"] * 100) if first["value"] else None
    return {
        "first_value": first["value"], "first_date": first["date"],
        "latest_value": last["value"], "latest_date": last["date"],
        "min": min(nums), "max": max(nums), "mean": round(mean(nums), 3), "median": round(median(nums), 3),
        "n": len(valid),
        "span_days": days,
        "pct_change": round(pct, 1) if pct is not None else None,
    }


def trend_phrase(stats):
    if not stats: return ""
    pct = stats.get("pct_change")
    if pct is None or stats["n"] < 2: return ""
    if abs(pct) < 5: return "essentially stable"
    direction = "up" if pct > 0 else "down"
    return f"trending {direction} ({pct:+.0f}% over {stats['span_days']}d)"


def build_insight(name, ref, stats, latest_status):
    parts = []
    # Personalized opener if available
    if name in PERSONAL:
        parts.append(PERSONAL[name])
    elif ref and ref.get("tag"):
        parts.append(ref["tag"])
    # Trend statement
    if stats:
        trend = trend_phrase(stats)
        latest = stats["latest_value"]
        unit = ref.get("unit", "") if ref else ""
        latest_str = f"Latest: {latest:g} {unit}".strip()
        if trend:
            latest_str += f" — {trend}."
        else:
            latest_str += "."
        if latest_status == "green":
            parts.append(f"{latest_str} In the optimal zone.")
        elif latest_status == "amber":
            parts.append(f"{latest_str} Borderline — worth attention but not urgent.")
        elif latest_status == "red":
            parts.append(f"{latest_str} Outside the healthy range — review and act.")
        else:
            parts.append(latest_str)
    return " ".join(parts).strip()


def build_overview(biomarkers):
    """Produce the top-level narrative summary."""
    # Headline: TL;DR written by Claude based on the dataset
    headline = (
        "The big picture: a fit, TRT-using adult male with broadly excellent metabolic health, "
        "strong inflammation markers, and a well-suppressed HPG axis as expected on exogenous testosterone. "
        "The cardiovascular lipid panel has one area worth attention (ApoB/LDL). "
        "Everything else is either in the optimal zone or an artefact of TRT physiology."
    )

    # Category-level commentary
    categories = [
        {
            "title": "Hormones & TRT profile",
            "tone": "neutral",
            "text": (
                "Your HPG axis looks exactly like a well-titrated TRT patient: testosterone in the upper end of normal, "
                "free T optimal, SHBG pulled down into the low-20s (typical on TRT), and both LH and FSH suppressed below "
                "detection (also expected — the pituitary is no longer signalling the testes). Prolactin is mid-range. "
                "The Randox oestradiol of 1104 pmol/L and a Manual.co reading of 2321 pmol/L are near-certain assay errors "
                "(likely a non-ultrasensitive ELISA). Mark these as erroneous; the rest of your E2 readings show a healthy "
                "80–160 pmol/L sweet spot most of the time. If fertility becomes a goal, add HCG to your protocol."
            ),
        },
        {
            "title": "Cardiovascular risk — the main area to address",
            "tone": "watch",
            "text": (
                "ApoB is 0.96 g/L (target <0.9, ideal <0.8), LDL is 3.27 mmol/L (target <2.6), and Non-HDL is 3.71 (target <3.4). "
                "These aren't emergency numbers but they're the most actionable thing in the whole dataset — "
                "cumulative lifetime exposure to ApoB is the strongest driver of atherosclerotic risk. "
                "Fortunately your Lp(a) is <7 nmol/L (excellent, won't change), HDL is 1.55 (protective), "
                "and triglycerides are under 1 (superb). First-line changes: 30–40g/day soluble fibre, cut saturated fat, "
                "2–3 tbsp EVOO/day, 25g mixed nuts. If ApoB hasn't dropped below 0.9 in 3–6 months, a low-dose statin "
                "(e.g. rosuvastatin 5–10mg) is reasonable given you're in your 30s and the risk is compounding."
            ),
        },
        {
            "title": "Metabolic & inflammation — excellent",
            "tone": "positive",
            "text": (
                "Fasting glucose in the 4s, HbA1c 34 mmol/mol (5.3%), uric acid normal, hs-CRP 0.24 mg/L. "
                "These are elite-tier metabolic markers — you have no insulin resistance, no chronic inflammation. "
                "This is the foundation everything else is built on; protect it."
            ),
        },
        {
            "title": "Haematology — watch for TRT-driven polycythaemia",
            "tone": "watch",
            "text": (
                "Haemoglobin and haematocrit sit in the upper-normal band, which is normal on TRT. "
                "Establish a 4–6 monthly blood donation cadence if Hb climbs above 170 g/L or Hct above 0.52. "
                "This is the cheapest, safest way to manage the erythropoietic side effect of TRT."
            ),
        },
        {
            "title": "Liver & kidney — quietly healthy",
            "tone": "positive",
            "text": (
                "ALT/AST/GGT/ALP all comfortably mid-range. No signal of liver stress from training or supplementation. "
                "Creatinine is upper-normal (typical for muscular men), urea normal, eGFR above 90 — kidneys are fine. "
                "If you want certainty on renal function in a muscular body, request a cystatin-C-based eGFR next time."
            ),
        },
        {
            "title": "Cardiovascular fitness & biometrics",
            "tone": "positive",
            "text": (
                "Resting heart rate sits 60–70 bpm (trained), blood pressure well-controlled around 120/75. "
                "Apple Health data adds daily tracking of HRV, walking heart rate, respiratory rate, and VO2 Max — "
                "giving a much richer picture of cardiovascular fitness trends over time. "
                "Body composition metrics track an athletic-lean profile. Keep this."
            ),
        },
        {
            "title": "Prostate & PSA",
            "tone": "positive",
            "text": (
                "PSA well under 1 ug/L. TRT raises PSA slightly over years; annual monitoring is the standard of care "
                "and yours is easily under the action threshold."
            ),
        },
        {
            "title": "Activity & daily movement",
            "tone": "positive",
            "text": (
                "Apple Watch activity data (active energy, basal energy, steps, distance) provides daily tracking "
                "of movement and energy expenditure. Combined with basal metabolic rate, this gives an accurate "
                "TDEE for nutrition planning. Aim for 8000+ steps and 400-800 kcal active burn daily."
            ),
        },
        {
            "title": "Nutrition & energy balance",
            "tone": "neutral",
            "text": (
                "Nutrition tracking enables calorie deficit/surplus monitoring against TDEE from Apple Watch data. "
                "Protein intake is the most critical macro for body composition on TRT — target 1.6-2.2 g/kg daily. "
                "Log consistently to spot trends in energy balance and macronutrient distribution."
            ),
        },
    ]

    # Authored recommendations — the "what to do" block
    recommendations = [
        {
            "priority": "high",
            "title": "Bring ApoB under 0.9 g/L",
            "text": (
                "Most impactful long-term change. Start with 30-40g soluble fibre/day (oats, psyllium, beans, berries), "
                "replace saturated fat with mono/polyunsaturated sources (EVOO, nuts, fatty fish), cap alcohol. "
                "Re-test in 3 months. If ApoB hasn't dropped, discuss a statin trial with your prescriber."
            ),
        },
        {
            "priority": "medium",
            "title": "Repeat oestradiol with an ultrasensitive assay (LC-MS)",
            "text": (
                "Two clearly outlier readings (1104 and 2321 pmol/L) look like assay errors. Request LC-MS/MS or "
                "ultrasensitive E2 (different from standard ECLIA) next time to avoid decision-making on noisy data."
            ),
        },
        {
            "priority": "medium",
            "title": "Establish a blood-donation cadence",
            "text": (
                "Routine whole-blood donation every 12–16 weeks is the simplest way to manage TRT-driven erythrocytosis. "
                "Book your next one in your calendar now."
            ),
        },
        {
            "priority": "low",
            "title": "Annual check-ins: PSA, HbA1c, ApoB, hs-CRP, LFTs, FBC",
            "text": (
                "Your current cadence is great. Lock this in annually and you'll catch drift early."
            ),
        },
        {
            "priority": "low",
            "title": "Log events as you cut/bulk/adjust TRT",
            "text": (
                "The dashboard now supports cut/bulk/TRT-dose events — logging them lets you causally link lifestyle "
                "changes to lab trends. Historical events are retrospective gold."
            ),
        },
    ]

    return {
        "headline": headline,
        "categories": categories,
        "recommendations": recommendations,
    }


def main():
    src = f"{OUTPUTS}/bloodwork_data.json"
    with open(src) as f:
        data = json.load(f)

    measurements = data["measurements"]
    biomarkers = data["biomarkers"]

    # Index measurements by biomarker
    by_bm = {}
    for m in measurements:
        by_bm.setdefault(m["biomarker"], []).append(m)

    # Augment each biomarker with reference + stats + insight + classify each measurement
    for b in biomarkers:
        name = b["name"]
        ref = REF.get(name)
        rows = by_bm.get(name, [])
        stats = compute_stats(rows)
        latest_status = None
        if ref and stats:
            latest_status = classify_value(ref, stats["latest_value"])
        insight = build_insight(name, ref, stats, latest_status)

        b["reference"] = ref
        b["stats"] = stats
        b["latest_status"] = latest_status
        b["insight"] = insight

    # Add inline rag classification to each measurement (so the table can show it)
    for m in measurements:
        ref = REF.get(m["biomarker"])
        m["rag"] = classify_value(ref, m["value"]) if ref else None

    # Attach the overall narrative (authored commentary across categories)
    data["overview"] = build_overview(biomarkers)

    with open(src, "w") as f:
        json.dump(data, f, indent=2)

    have_ref = sum(1 for b in biomarkers if b.get("reference"))
    have_insight = sum(1 for b in biomarkers if b.get("insight"))
    print(f"Biomarkers: {len(biomarkers)}")
    print(f"  with reference range: {have_ref}")
    print(f"  with insight: {have_insight}")
    rag_counts = {"green":0,"amber":0,"red":0,"none":0}
    for m in measurements:
        rag_counts[m.get("rag") or "none"] += 1
    print(f"Measurements RAG: green={rag_counts['green']} amber={rag_counts['amber']} red={rag_counts['red']} unranged={rag_counts['none']}")


if __name__ == "__main__":
    main()
