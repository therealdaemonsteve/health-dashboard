#!/usr/bin/env python3
"""Normalize blood test data from manual.co, bluecrest, mymedichecks into one timeseries."""
import json
import re
import os
import hashlib
from datetime import datetime

OUTPUTS = os.environ.get("HEALTH_DASHBOARD_DIR", os.path.dirname(os.path.abspath(__file__)))

# ---------- Biomarker name normalization ----------
# Map provider-specific names to canonical names
CANONICAL = {
    # Hormones
    "testosterone": "Testosterone",
    "free testosterone": "Free Testosterone",
    "sex-hormone binding globulin (shbg)": "SHBG",
    "sex hormone binding globulin": "SHBG",
    "shbg": "SHBG",
    "oestradiol": "Oestradiol",
    "estradiol": "Oestradiol",
    "prolactin": "Prolactin",
    "follicle-stimulating hormone": "FSH",
    "follicle stimulating hormone": "FSH",
    "fsh": "FSH",
    "luteinising hormone": "LH",
    "luteinizing hormone": "LH",
    "lh": "LH",
    "thyroid function (tsh)": "TSH",
    "tsh": "TSH",
    "free t4": "Free T4",
    # Lipids
    "total cholesterol": "Total Cholesterol",
    "ldl cholesterol": "LDL Cholesterol",
    "hdl cholesterol": "HDL Cholesterol",
    "hdl (high-density lipoprotein)": "HDL Cholesterol",
    "non-hdl cholesterol": "Non-HDL Cholesterol",
    "non hdl cholesterol": "Non-HDL Cholesterol",
    "triglycerides": "Triglycerides",
    "total cholesterol/hdl ratio": "Total Cholesterol / HDL Ratio",
    "total cholesterol / hdl ratio": "Total Cholesterol / HDL Ratio",
    "total cholesterol : hdl": "Total Cholesterol / HDL Ratio",
    "apolipoprotein a1": "Apolipoprotein A1",
    "apolipoprotein b": "Apolipoprotein B",
    "apob : apoa ratio": "ApoB / ApoA Ratio",
    "lipoprotein(a)": "Lipoprotein(a)",
    # Liver
    "alt": "ALT",
    "alanine transaminase (alt)": "ALT",
    "aspartate transaminase (ast)": "AST",
    "gamma-gt (ggt)": "GGT",
    "ggt": "GGT",
    "alkaline phosphatase (alp)": "ALP",
    "alp": "ALP",
    "albumin": "Albumin",
    "globulin": "Globulin",
    "total protein": "Total Protein",
    "bilirubin": "Bilirubin",
    "total bilirubin": "Bilirubin",
    # Kidney
    "creatinine": "Creatinine",
    "urea": "Urea",
    "sodium": "Sodium",
    "egfr": "eGFR",
    "estimated glomerular filtration rate": "eGFR",
    # CBC
    "haemoglobin": "Haemoglobin",
    "haematocrit": "Haematocrit",
    "red blood cells": "Red Blood Cells",
    "red blood cell count (rbc)": "Red Blood Cells",
    "white blood cells": "White Blood Cells",
    "white blood cell count (wbc)": "White Blood Cells",
    "platelets": "Platelets",
    "platelet count": "Platelets",
    "mean cell volume": "MCV",
    "mean cell haemoglobin": "MCH",
    "mean cell haemoglobin (mch)": "MCH",
    "mchc": "MCHC",
    "neutrophils": "Neutrophils",
    "lymphocytes": "Lymphocytes",
    "monocytes": "Monocytes",
    "eosinophils": "Eosinophils",
    "basophils": "Basophils",
    # Metabolic
    "glucose": "Glucose",
    "hba1c": "HbA1c",
    "uric acid": "Uric Acid",
    # Inflammation
    "crp hs": "CRP (high sensitivity)",
    # Other
    "prostate specific antigen (psa)": "PSA",
    "psa": "PSA",
    "ferritin": "Ferritin",
    "iron": "Iron",
    "tibc": "TIBC",
    "vitamin d": "Vitamin D",
    "active vitamin b12": "Vitamin B12 (active)",
    "folate": "Folate",
    "magnesium": "Magnesium",
    "zinc": "Zinc",
    "calcium": "Calcium",
    "corrected calcium": "Corrected Calcium",
    "phosphate": "Phosphate",
    # Biometrics
    "body mass index (bmi)": "BMI",
    "weight": "Weight",
    "height": "Height",
    "body fat": "Body Fat %",
    "visceral fat": "Visceral Fat",
    "muscle mass": "Muscle Mass",
    "bone mass": "Bone Mass",
    "total body water %": "Total Body Water %",
    "bmr": "BMR",
    "metabolic age": "Metabolic Age",
    "fat free mass": "Fat Free Mass",
    "resting heart rate": "Resting Heart Rate",
    "resting heart rate ": "Resting Heart Rate",
    "resting heart rate": "Resting Heart Rate",
    "vo2 max": "VO2 Max",
    # Apple Health camelCase keys
    "restingheartrate": "Resting Heart Rate",
    "heartratevariabilitysdnn": "HRV (SDNN)",
    "vo2max": "VO2 Max",
    "walkingheartrateaverage": "Walking Heart Rate",
    "respiratoryrate": "Respiratory Rate",
    # BP
    "systolic": "Systolic BP",
    "diastolic": "Diastolic BP",
    "blood pressure": "Blood Pressure",
}

# Categories for grouping
CATEGORIES = {
    "Hormones": ["Testosterone", "Free Testosterone", "SHBG", "Oestradiol", "Prolactin", "FSH", "LH", "TSH", "Free T4"],
    "Lipids": ["Total Cholesterol", "LDL Cholesterol", "HDL Cholesterol", "Non-HDL Cholesterol", "Triglycerides", "Total Cholesterol / HDL Ratio", "Apolipoprotein A1", "Apolipoprotein B", "ApoB / ApoA Ratio", "Lipoprotein(a)"],
    "Liver": ["ALT", "AST", "GGT", "ALP", "Albumin", "Globulin", "Total Protein", "Bilirubin"],
    "Kidney": ["Creatinine", "Urea", "Sodium", "eGFR"],
    "CBC": ["Haemoglobin", "Haematocrit", "Red Blood Cells", "White Blood Cells", "Platelets", "MCV", "MCH", "MCHC", "Neutrophils", "Lymphocytes", "Monocytes", "Eosinophils", "Basophils"],
    "Metabolic": ["Glucose", "HbA1c", "Uric Acid"],
    "Inflammation": ["CRP (high sensitivity)"],
    "Nutrients": ["Ferritin", "Iron", "TIBC", "Vitamin D", "Vitamin B12 (active)", "Folate", "Magnesium", "Zinc", "Calcium", "Corrected Calcium", "Phosphate"],
    "Other": ["PSA"],
    "Biometrics": ["BMI", "Weight", "Height", "Body Fat %", "Visceral Fat", "Muscle Mass", "Bone Mass", "Total Body Water %", "BMR", "Metabolic Age", "Fat Free Mass", "Resting Heart Rate", "VO2 Max", "HRV (SDNN)", "Walking Heart Rate", "Respiratory Rate"],
    "Blood Pressure": ["Systolic BP", "Diastolic BP", "Blood Pressure"],
}

def canon_name(raw):
    if not raw: return None
    key = raw.strip().lower()
    return CANONICAL.get(key, raw.strip())

def category_for(canonical):
    for cat, names in CATEGORIES.items():
        if canonical in names:
            return cat
    return "Uncategorised"

# ---------- Date parsing ----------
def parse_date(date_str):
    """Return ISO YYYY-MM-DD or None."""
    if not date_str: return None
    date_str = date_str.strip()
    formats = [
        "%d %B, %Y",     # 3 May, 2025
        "%d %B %Y",      # 3 May 2025
        "%d %b %Y",      # 09 Apr 2026
        "%Y-%m-%d",
        "%d/%m/%Y",
    ]
    for f in formats:
        try:
            return datetime.strptime(date_str, f).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None

# ---------- Value parsing ----------
def parse_value(v):
    """Try to parse a numeric value, handling <, >, and ranges."""
    if v is None: return None, None
    s = str(v).strip()
    # Handle <X or >X as ~0 or ~max (keep the numeric bit and a qualifier)
    m = re.match(r"^<\s*([\d\.]+)$", s)
    if m: return float(m.group(1)), "<"
    m = re.match(r"^>\s*([\d\.]+)$", s)
    if m: return float(m.group(1)), ">"
    # Plain number
    try:
        return float(s), None
    except ValueError:
        return None, s  # keep string value as qualifier

def status_to_norm(status):
    """Normalize status string into 'low'|'normal'|'high'|'borderline'|null."""
    if not status: return None
    s = str(status).lower().strip()
    if s in ("low", "red_low"): return "low"
    if s in ("high", "red_high", "red"): return "high"
    if s in ("normal", "optimal", "green", "inside"): return "normal"
    if s in ("high normal", "borderline", "amber"): return "borderline"
    if s in ("outside",): return "outside"
    return s

# ---------- Loaders ----------
def measurement_id(source, test_id, biomarker_name, date, value_raw):
    key = f"{source}|{test_id}|{biomarker_name}|{date}|{value_raw}"
    return "m_" + hashlib.sha1(key.encode()).hexdigest()[:12]

def load_manual():
    with open(f"{OUTPUTS}/manual_blood_tests.json") as f:
        data = json.load(f)
    out = []
    for t in data.get("tests", []):
        date = parse_date(t.get("date"))
        test_id = f"manual_{t.get('testId')}"
        for b in t.get("biomarkers", []):
            name_raw = b.get("name")
            name = canon_name(name_raw)
            value, qualifier = parse_value(b.get("value"))
            out.append({
                "id": measurement_id("manual.co", test_id, name_raw, date, b.get("value")),
                "source": "manual.co",
                "source_label": "Manual.co",
                "test_id": test_id,
                "test_name": "Manual.co Blood Test",
                "date": date,
                "biomarker": name,
                "biomarker_raw": name_raw,
                "category": category_for(name),
                "value": value,
                "value_raw": b.get("value"),
                "qualifier": qualifier,
                "unit": b.get("unit"),
                "status": status_to_norm(b.get("status")),
                "status_raw": b.get("status"),
                "reference_range": None,
                "url": t.get("url"),
            })
    return out

def load_bluecrest():
    with open(f"{OUTPUTS}/bluecrest_blood_tests.json") as f:
        data = json.load(f)
    out = []
    for order in data.get("orders", []):
        oid = order.get("orderId")
        date = parse_date(order.get("order_date")) or _year_to_date(order.get("order_date"))
        pkg = order.get("package")
        for cat in order.get("categories", []):
            cat_name = cat.get("category_name")
            for b in cat.get("biomarkers", []):
                name_raw = b.get("name")
                name = canon_name(name_raw)
                value, qualifier = parse_value(b.get("value"))
                out.append({
                    "id": measurement_id("bluecrest", oid, name_raw, date, b.get("value")),
                    "source": "bluecrest",
                    "source_label": "Bluecrest",
                    "test_id": oid,
                    "test_name": f"Bluecrest {pkg}",
                    "date": date,
                    "biomarker": name,
                    "biomarker_raw": name_raw,
                    "category": category_for(name),
                    "bluecrest_category": cat_name,
                    "value": value,
                    "value_raw": b.get("value"),
                    "qualifier": qualifier,
                    "unit": b.get("unit"),
                    "status": status_to_norm(b.get("status")),
                    "status_raw": b.get("status"),
                    "reference_range": None,
                })
    return out

def _year_to_date(s):
    """If a year-only string like '2024', default to Jan 1 of that year."""
    if not s: return None
    m = re.search(r"(\d{4})", str(s))
    return f"{m.group(1)}-01-01" if m else None

def load_medichecks():
    with open(f"{OUTPUTS}/mymedichecks_blood_tests.json") as f:
        data = json.load(f)
    out = []
    for t in data.get("tests", []):
        date = parse_date(t.get("sample_date")) or parse_date(t.get("date")) or parse_date(t.get("last_updated"))
        test_id = str(t.get("orderId"))
        test_name = t.get("test_name")
        # Determine sub-source: mymedichecks if id is numeric, randox if RE...
        if test_id.startswith("RE"):
            source = "randox"
            label = "Randox / MyMedichecks"
        else:
            source = "medichecks"
            label = "Medichecks"
        for b in t.get("biomarkers", []):
            name_raw = b.get("name")
            name = canon_name(name_raw)
            value, qualifier = parse_value(b.get("value"))
            out.append({
                "id": measurement_id(source, test_id, name_raw, date, b.get("value")),
                "source": source,
                "source_label": label,
                "test_id": test_id,
                "test_name": test_name,
                "date": date,
                "biomarker": name,
                "biomarker_raw": name_raw,
                "category": category_for(name),
                "value": value,
                "value_raw": b.get("value"),
                "qualifier": qualifier,
                "unit": b.get("unit"),
                "status": status_to_norm(b.get("status")),
                "status_raw": b.get("status"),
                "reference_range": b.get("reference_range"),
                "flag": b.get("flag"),
                "ranges": b.get("ranges"),
            })
    return out

# ---------- Apple Health artefact filters ----------
APPLE_HEALTH_ARTEFACT = {
    "Resting Heart Rate": lambda v: v < 0,
    "HRV (SDNN)": lambda v: v > 200,
    "VO2 Max": lambda v: v > 80,
}

# Apple Health metric key -> canonical name (direct mapping, not via canon_name)
APPLE_HEALTH_MAP = {
    # Cardiovascular
    "restingHeartRate": "Resting Heart Rate",
    "heartRate": "Heart Rate",
    "heartRateVariabilitySDNN": "HRV (SDNN)",
    "vo2Max": "VO2 Max",
    "walkingHeartRateAverage": "Walking Heart Rate",
    "oxygenSaturation": "Blood Oxygen",
    "respiratoryRate": "Respiratory Rate",
    "bloodPressureSystolic": "Blood Pressure Systolic",
    "bloodPressureDiastolic": "Blood Pressure Diastolic",
    "bodyTemperature": "Body Temperature",
    # Activity
    "activeEnergyBurned": "Active Energy Burned",
    "basalEnergyBurned": "Basal Energy Burned",
    "stepCount": "Step Count",
    "distanceWalkingRunning": "Distance Walking/Running",
    "distanceCycling": "Distance Cycling",
    "flightsClimbed": "Flights Climbed",
    # Body Composition
    "bodyMass": "Weight",
    "bodyFatPercentage": "Body Fat %",
    "leanBodyMass": "Lean Body Mass",
    "bodyMassIndex": "BMI",
    "height": "Height",
    "waistCircumference": "Waist Circumference",
    # Nutrition
    "dietaryEnergyConsumed": "Calories In",
    "dietaryProtein": "Protein",
    "dietaryCarbohydrates": "Carbs",
    "dietaryFatTotal": "Fat",
    "dietaryFiber": "Fibre",
    "dietarySugar": "Sugar",
    "dietarySodium": "Sodium",
    "dietaryWater": "Water",
    "dietaryCaffeine": "Caffeine",
    # Sleep & Mindfulness
    "sleepAnalysis": "Sleep",
    "mindfulSession": "Mindful Minutes",
    # Workouts
    "workout": "Workout Duration",
    "workoutEnergyBurned": "Workout Calories",
    "workoutDistance": "Workout Distance",
}

# Apple Health unit normalization
APPLE_HEALTH_UNIT_MAP = {
    "mL/min/kg": "mL/kg/min",
}

def load_apple_health():
    path = f"{OUTPUTS}/apple health export.json"
    if not os.path.exists(path):
        return []
    with open(path) as f:
        data = json.load(f)
    out = []
    for rec in data.get("records", []):
        metric = rec.get("metric")
        name = APPLE_HEALTH_MAP.get(metric)
        if not name:
            continue
        value, qualifier = parse_value(rec.get("value"))
        if value is None:
            continue
        # Artefact filter
        if name in APPLE_HEALTH_ARTEFACT and APPLE_HEALTH_ARTEFACT[name](value):
            continue
        # Date: slice first 10 chars of ISO timestamp
        date_raw = rec.get("date") or ""
        date = date_raw[:10] if len(date_raw) >= 10 else None
        # Unit normalization
        unit = rec.get("unit", "")
        unit = APPLE_HEALTH_UNIT_MAP.get(unit, unit)
        out.append({
            "id": measurement_id("apple_health", "apple_watch", name, date, rec.get("value")),
            "source": "apple_health",
            "source_label": "Apple Health",
            "test_id": "apple_watch",
            "test_name": "Apple Watch",
            "date": date,
            "biomarker": name,
            "biomarker_raw": metric,
            "category": category_for(name),
            "value": value,
            "value_raw": rec.get("value"),
            "qualifier": qualifier,
            "unit": unit,
            "status": None,
            "status_raw": None,
            "reference_range": None,
        })
    return out

def main():
    rows = []
    rows.extend(load_manual())
    rows.extend(load_bluecrest())
    rows.extend(load_medichecks())
    rows.extend(load_apple_health())
    # Sort by date asc then biomarker
    rows.sort(key=lambda r: (r.get("date") or "0", r.get("biomarker") or ""))

    # Build biomarker index with categories
    biomarkers = {}
    for r in rows:
        name = r["biomarker"]
        if name not in biomarkers:
            biomarkers[name] = {
                "name": name,
                "category": r["category"],
                "units": set(),
                "count": 0,
            }
        biomarkers[name]["count"] += 1
        if r.get("unit"): biomarkers[name]["units"].add(r["unit"])
    biomarker_list = []
    for b in biomarkers.values():
        b["units"] = sorted(b["units"])
        biomarker_list.append(b)
    biomarker_list.sort(key=lambda b: (b["category"], b["name"]))

    out = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "recipient": "Steven Bennett",
        "sources": [
            {"id": "manual.co", "label": "Manual.co"},
            {"id": "bluecrest", "label": "Bluecrest"},
            {"id": "medichecks", "label": "Medichecks"},
            {"id": "randox", "label": "Randox / MyMedichecks"},
            {"id": "apple_health", "label": "Apple Health"},
        ],
        "categories": list(CATEGORIES.keys()) + ["Uncategorised"],
        "biomarkers": biomarker_list,
        "measurements": rows,
    }

    with open(f"{OUTPUTS}/bloodwork_data.json", "w") as f:
        json.dump(out, f, indent=2)

    print(f"Total measurements: {len(rows)}")
    print(f"Unique biomarkers: {len(biomarker_list)}")
    print(f"Dated measurements: {sum(1 for r in rows if r['date'])}")
    print(f"Undated measurements: {sum(1 for r in rows if not r['date'])}")

if __name__ == "__main__":
    main()
