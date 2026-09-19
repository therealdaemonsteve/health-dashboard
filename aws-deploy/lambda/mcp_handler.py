"""MCP Streamable HTTP server for Health Dashboard — runs on AWS Lambda.

Implements the MCP JSON-RPC 2.0 protocol over HTTP so Claude.ai (including
iOS) can call the same health-dashboard tools that the local FastMCP stdio
server exposes.  No external dependencies beyond boto3 (built into Lambda).
"""

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from math import sqrt, exp, log, pi, erf
from statistics import mean, median, stdev

# ── Config from environment ──────────────────────────────────────────────────

BEARER_TOKEN = os.environ.get("BEARER_TOKEN", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_REGION = os.environ.get("S3_REGION", "eu-west-2")
OAUTH_SECRET = os.environ.get("OAUTH_SECRET", "")
AUTH_PIN = os.environ.get("AUTH_PIN", "")
CLOUDFRONT_DIST_ID = os.environ.get("CLOUDFRONT_DIST_ID", "")

BLOODWORK_KEY = "bloodwork_data.json"
EVENTS_KEY = "events.json"
NUTRITION_KEY = "nutrition.json"
LIFTING_KEY = "lifting.json"
COACHING_KEY = "coaching.json"
PHASES_KEY = "phases.json"

SERVER_NAME = "health-dashboard"
SERVER_VERSION = "1.0.0"

# ── In-memory cache (persists across warm Lambda invocations) ────────────────

_cache = {}

# ── OAuth 2.0 in-memory client registry (survives warm starts) ───────────────

_oauth_clients = {}  # client_id -> {redirect_uris, client_name, ...}


# ── OAuth token helpers (HMAC-signed, stateless) ────────────────────────────


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _sign_token(claims: dict) -> str:
    payload = _b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    sig = _b64url_encode(hmac.new(OAUTH_SECRET.encode(), payload.encode(), hashlib.sha256).digest())
    return payload + "." + sig


def _verify_token(token: str) -> dict | None:
    parts = token.rsplit(".", 1)
    if len(parts) != 2:
        return None
    payload, sig = parts
    expected = _b64url_encode(hmac.new(OAUTH_SECRET.encode(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        claims = json.loads(_b64url_decode(payload))
    except (json.JSONDecodeError, Exception):
        return None
    if claims.get("exp", 0) < time.time():
        return None
    return claims


# ── Apple Health constants (from mcp_server.py) ─────────────────────────────

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
    "heartRateRecoveryOneMinute": "Heart Rate Recovery",
    # Heart Events
    "highHeartRateEvent": "High Heart Rate Events",
    "lowHeartRateEvent": "Low Heart Rate Events",
    "irregularHeartRhythmEvent": "Irregular Rhythm Events",
    # Activity
    "activeEnergyBurned": "Active Energy Burned",
    "basalEnergyBurned": "Basal Energy Burned",
    "stepCount": "Step Count",
    "distanceWalkingRunning": "Distance Walking/Running",
    "distanceCycling": "Distance Cycling",
    "distanceSwimming": "Distance Swimming",
    "flightsClimbed": "Flights Climbed",
    "appleExerciseTime": "Exercise Time",
    "appleStandTime": "Stand Time",
    "appleStandHour": "Stand Hours",
    "swimmingStrokeCount": "Swimming Strokes",
    "numberOfTimesFallen": "Falls",
    # Mobility & Gait
    "walkingSpeed": "Walking Speed",
    "walkingStepLength": "Walking Step Length",
    "walkingDoubleSupportPercentage": "Double Support %",
    "walkingAsymmetryPercentage": "Walking Asymmetry %",
    "appleWalkingSteadiness": "Walking Steadiness",
    "stairAscentSpeed": "Stair Ascent Speed",
    "stairDescentSpeed": "Stair Descent Speed",
    "sixMinuteWalkTestDistance": "6-Min Walk Distance",
    # Running Dynamics
    "runningSpeed": "Running Speed",
    "runningStrideLength": "Running Stride Length",
    "runningVerticalOscillation": "Running Vertical Oscillation",
    "runningGroundContactTime": "Running Ground Contact Time",
    "runningPower": "Running Power",
    # Cycling
    "cyclingSpeed": "Cycling Speed",
    "cyclingPower": "Cycling Power",
    "cyclingCadence": "Cycling Cadence",
    "cyclingFunctionalThresholdPower": "Cycling FTP",
    # Body Composition
    "bodyMass": "Weight",
    "bodyFatPercentage": "Body Fat %",
    "leanBodyMass": "Lean Body Mass",
    "bodyMassIndex": "BMI",
    "height": "Height",
    "waistCircumference": "Waist Circumference",
    # Vitals
    "appleSleepingWristTemperature": "Sleeping Wrist Temperature",
    "bloodGlucose": "Blood Glucose",
    "peripheralPerfusionIndex": "Perfusion Index",
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
    # Dietary Micronutrients
    "dietaryCholesterol": "Cholesterol (Dietary)",
    "dietaryCalcium": "Calcium",
    "dietaryIron": "Iron",
    "dietaryPotassium": "Potassium",
    "dietaryVitaminC": "Vitamin C",
    "dietaryVitaminD": "Vitamin D",
    "dietaryMagnesium": "Magnesium",
    "dietaryZinc": "Zinc",
    "dietaryFolate": "Folate",
    "dietaryVitaminA": "Vitamin A",
    "dietaryVitaminB12": "Vitamin B12",
    # Audio Exposure
    "environmentalAudioExposure": "Environmental Audio Exposure",
    "headphoneAudioExposure": "Headphone Audio Exposure",
    # Wellness
    "timeInDaylight": "Time in Daylight",
    # Sleep & Mindfulness
    "sleepAnalysis": "Sleep",
    "mindfulSession": "Mindful Minutes",
    # Workouts
    "workout": "Workout Duration",
    "workoutEnergyBurned": "Workout Calories",
    "workoutDistance": "Workout Distance",
}

APPLE_HEALTH_ARTEFACT = {
    # Cardiovascular
    "Resting Heart Rate": lambda v: v < 20 or v > 200,
    "Heart Rate": lambda v: v < 20 or v > 250,
    "HRV (SDNN)": lambda v: v < 0 or v > 300,
    "VO2 Max": lambda v: v < 10 or v > 80,
    "Walking Heart Rate": lambda v: v < 30 or v > 200,
    "Blood Oxygen": lambda v: v < 0.5 or v > 1.0,
    "Respiratory Rate": lambda v: v < 4 or v > 60,
    "Blood Pressure Systolic": lambda v: v < 50 or v > 300,
    "Blood Pressure Diastolic": lambda v: v < 20 or v > 200,
    "Body Temperature": lambda v: v < 30 or v > 45,
    "Heart Rate Recovery": lambda v: v < 0 or v > 100,
    # Heart Events
    "High Heart Rate Events": lambda v: v < 0 or v > 100,
    "Low Heart Rate Events": lambda v: v < 0 or v > 100,
    "Irregular Rhythm Events": lambda v: v < 0 or v > 100,
    # Activity
    "Active Energy Burned": lambda v: v < 0 or v > 5000,
    "Basal Energy Burned": lambda v: v < 500 or v > 4000,
    "Step Count": lambda v: v < 0 or v > 100000,
    "Distance Walking/Running": lambda v: v < 0 or v > 100,
    "Distance Cycling": lambda v: v < 0 or v > 500,
    "Distance Swimming": lambda v: v < 0 or v > 20000,
    "Flights Climbed": lambda v: v < 0 or v > 500,
    "Exercise Time": lambda v: v < 0 or v > 1440,
    "Stand Time": lambda v: v < 0 or v > 1440,
    "Stand Hours": lambda v: v < 0 or v > 24,
    "Swimming Strokes": lambda v: v < 0 or v > 50000,
    "Falls": lambda v: v < 0 or v > 50,
    # Mobility & Gait
    "Walking Speed": lambda v: v < 0 or v > 5,
    "Walking Step Length": lambda v: v < 10 or v > 200,
    "Double Support %": lambda v: v < 0 or v > 1.0,
    "Walking Asymmetry %": lambda v: v < 0 or v > 1.0,
    "Walking Steadiness": lambda v: v < 0 or v > 1.0,
    "Stair Ascent Speed": lambda v: v < 0 or v > 5,
    "Stair Descent Speed": lambda v: v < 0 or v > 5,
    "6-Min Walk Distance": lambda v: v < 0 or v > 1000,
    # Running Dynamics
    "Running Speed": lambda v: v < 0 or v > 15,
    "Running Stride Length": lambda v: v < 0 or v > 5,
    "Running Vertical Oscillation": lambda v: v < 0 or v > 30,
    "Running Ground Contact Time": lambda v: v < 50 or v > 500,
    "Running Power": lambda v: v < 0 or v > 1000,
    # Cycling
    "Cycling Speed": lambda v: v < 0 or v > 30,
    "Cycling Power": lambda v: v < 0 or v > 2000,
    "Cycling Cadence": lambda v: v < 0 or v > 200,
    "Cycling FTP": lambda v: v < 0 or v > 600,
    # Body Composition
    "Lean Body Mass": lambda v: v < 20 or v > 150,
    "BMI": lambda v: v < 10 or v > 80,
    "Height": lambda v: v < 50 or v > 250,
    "Waist Circumference": lambda v: v < 30 or v > 200,
    # Vitals
    "Sleeping Wrist Temperature": lambda v: v < -5 or v > 5,
    "Blood Glucose": lambda v: v < 20 or v > 600,
    "Perfusion Index": lambda v: v < 0 or v > 0.20,
    # Nutrition
    "Calories In": lambda v: v < 0 or v > 10000,
    "Protein": lambda v: v < 0 or v > 1000,
    "Carbs": lambda v: v < 0 or v > 2000,
    "Fat": lambda v: v < 0 or v > 1000,
    "Fibre": lambda v: v < 0 or v > 500,
    "Sugar": lambda v: v < 0 or v > 2000,
    "Sodium": lambda v: v < 0 or v > 10000,
    "Water": lambda v: v < 0 or v > 20,
    "Caffeine": lambda v: v < 0 or v > 2000,
    # Dietary Micronutrients
    "Cholesterol (Dietary)": lambda v: v < 0 or v > 2000,
    "Calcium": lambda v: v < 0 or v > 5000,
    "Iron": lambda v: v < 0 or v > 200,
    "Potassium": lambda v: v < 0 or v > 10000,
    "Vitamin C": lambda v: v < 0 or v > 10000,
    "Vitamin D": lambda v: v < 0 or v > 10000,
    "Magnesium": lambda v: v < 0 or v > 5000,
    "Zinc": lambda v: v < 0 or v > 500,
    "Folate": lambda v: v < 0 or v > 10000,
    "Vitamin A": lambda v: v < 0 or v > 50000,
    "Vitamin B12": lambda v: v < 0 or v > 10000,
    # Audio Exposure
    "Environmental Audio Exposure": lambda v: v < 0 or v > 150,
    "Headphone Audio Exposure": lambda v: v < 0 or v > 150,
    # Wellness
    "Time in Daylight": lambda v: v < 0 or v > 1440,
    # Sleep & Mindfulness
    "Sleep": lambda v: v < 0 or v > 1440,
    "Mindful Minutes": lambda v: v < 0 or v > 1440,
    # Workouts
    "Workout Duration": lambda v: v < 0 or v > 1440,
    "Workout Calories": lambda v: v < 0 or v > 10000,
    "Workout Distance": lambda v: v < 0 or v > 500,
}

APPLE_HEALTH_CATEGORY = {
    # Cardiovascular
    "Heart Rate": "Cardiovascular",
    "Blood Oxygen": "Cardiovascular",
    "Blood Pressure Systolic": "Cardiovascular",
    "Blood Pressure Diastolic": "Cardiovascular",
    "Heart Rate Recovery": "Cardiovascular",
    "High Heart Rate Events": "Cardiovascular",
    "Low Heart Rate Events": "Cardiovascular",
    "Irregular Rhythm Events": "Cardiovascular",
    # Vitals
    "Body Temperature": "Vitals",
    "Sleeping Wrist Temperature": "Vitals",
    "Blood Glucose": "Vitals",
    "Perfusion Index": "Vitals",
    # Activity
    "Active Energy Burned": "Activity",
    "Basal Energy Burned": "Activity",
    "Step Count": "Activity",
    "Distance Walking/Running": "Activity",
    "Distance Cycling": "Activity",
    "Distance Swimming": "Activity",
    "Flights Climbed": "Activity",
    "Exercise Time": "Activity",
    "Stand Time": "Activity",
    "Stand Hours": "Activity",
    "Swimming Strokes": "Activity",
    "Falls": "Activity",
    # Mobility & Gait
    "Walking Speed": "Mobility",
    "Walking Step Length": "Mobility",
    "Double Support %": "Mobility",
    "Walking Asymmetry %": "Mobility",
    "Walking Steadiness": "Mobility",
    "Stair Ascent Speed": "Mobility",
    "Stair Descent Speed": "Mobility",
    "6-Min Walk Distance": "Mobility",
    # Running Dynamics
    "Running Speed": "Running",
    "Running Stride Length": "Running",
    "Running Vertical Oscillation": "Running",
    "Running Ground Contact Time": "Running",
    "Running Power": "Running",
    # Cycling
    "Cycling Speed": "Cycling",
    "Cycling Power": "Cycling",
    "Cycling Cadence": "Cycling",
    "Cycling FTP": "Cycling",
    # Body Composition
    "Lean Body Mass": "Body Composition",
    "BMI": "Body Composition",
    "Height": "Body Composition",
    "Waist Circumference": "Body Composition",
    # Nutrition
    "Calories In": "Nutrition",
    "Protein": "Nutrition",
    "Carbs": "Nutrition",
    "Fat": "Nutrition",
    "Fibre": "Nutrition",
    "Sugar": "Nutrition",
    "Sodium": "Nutrition",
    "Water": "Nutrition",
    "Caffeine": "Nutrition",
    # Dietary Micronutrients
    "Cholesterol (Dietary)": "Micronutrients",
    "Calcium": "Micronutrients",
    "Iron": "Micronutrients",
    "Potassium": "Micronutrients",
    "Vitamin C": "Micronutrients",
    "Vitamin D": "Micronutrients",
    "Magnesium": "Micronutrients",
    "Zinc": "Micronutrients",
    "Folate": "Micronutrients",
    "Vitamin A": "Micronutrients",
    "Vitamin B12": "Micronutrients",
    # Audio Exposure
    "Environmental Audio Exposure": "Audio",
    "Headphone Audio Exposure": "Audio",
    # Wellness
    "Time in Daylight": "Wellness",
    # Sleep & Mindfulness
    "Sleep": "Sleep",
    "Mindful Minutes": "Mindfulness",
    # Workouts
    "Workout Duration": "Workouts",
    "Workout Calories": "Workouts",
    "Workout Distance": "Workouts",
}

APPLE_HEALTH_UNIT_MAP = {"mL/min/kg": "mL/kg/min"}

# ── S3 helpers ───────────────────────────────────────────────────────────────

import boto3  # noqa: E402 — available in Lambda runtime


def _s3_client():
    return boto3.client("s3", region_name=S3_REGION)


def _load_data(force=False):
    if not force and "bloodwork" in _cache and "events" in _cache:
        return _cache
    s3 = _s3_client()
    resp = s3.get_object(Bucket=S3_BUCKET, Key=BLOODWORK_KEY)
    _cache["bloodwork"] = json.loads(resp["Body"].read())
    resp = s3.get_object(Bucket=S3_BUCKET, Key=EVENTS_KEY)
    _cache["events"] = json.loads(resp["Body"].read())
    try:
        resp = s3.get_object(Bucket=S3_BUCKET, Key=NUTRITION_KEY)
        _cache["nutrition"] = json.loads(resp["Body"].read())
    except Exception:
        _cache["nutrition"] = {"entries": []}
    try:
        resp = s3.get_object(Bucket=S3_BUCKET, Key=LIFTING_KEY)
        _cache["lifting"] = json.loads(resp["Body"].read())
    except Exception:
        _cache["lifting"] = {"version": 1, "sessions": []}
    try:
        resp = s3.get_object(Bucket=S3_BUCKET, Key=COACHING_KEY)
        _cache["coaching"] = json.loads(resp["Body"].read())
    except Exception:
        _cache["coaching"] = {"version": 1, "goals": [], "notes": [], "action_items": []}
    try:
        resp = s3.get_object(Bucket=S3_BUCKET, Key=PHASES_KEY)
        _cache["phases"] = json.loads(resp["Body"].read())
    except Exception:
        _cache["phases"] = {"version": 1, "phases": []}
    _cache["loaded_at"] = datetime.utcnow().isoformat() + "Z"
    return _cache


def _sanitize_for_json(obj):
    """Replace NaN/Infinity with None for valid JSON output."""
    if isinstance(obj, float) and (obj != obj or obj == float('inf') or obj == float('-inf')):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    return obj


def _write_s3(key, data):
    s3 = _s3_client()
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=json.dumps(_sanitize_for_json(data), indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    _cache.clear()
    _invalidate_cloudfront(key)


def _invalidate_cloudfront(key):
    """Invalidate the CloudFront cache for a given S3 key."""
    if not CLOUDFRONT_DIST_ID:
        return
    try:
        cf = boto3.client("cloudfront", region_name="us-east-1")
        cf.create_invalidation(
            DistributionId=CLOUDFRONT_DIST_ID,
            InvalidationBatch={
                "Paths": {"Quantity": 1, "Items": [f"/{key}"]},
                "CallerReference": f"{key}-{time.time()}",
            },
        )
    except Exception:
        pass  # best-effort — don't fail the tool call


def _fuzzy_match_biomarker(name, biomarkers):
    name_lower = name.lower().strip()
    for b in biomarkers:
        if b["name"].lower() == name_lower:
            return b
    for b in biomarkers:
        if name_lower in b["name"].lower() or b["name"].lower() in name_lower:
            return b
    best, best_score = None, 0.0
    for b in biomarkers:
        score = SequenceMatcher(None, name_lower, b["name"].lower()).ratio()
        if score > best_score:
            best, best_score = b, score
    return best if best_score >= 0.5 else None


def _get_latest_measurement(bw, name):
    """Return the most recent measurement for a given biomarker name."""
    latest = None
    for m in bw.get("measurements", []):
        if m.get("biomarker") == name:
            if latest is None or m.get("date", "") > latest.get("date", ""):
                latest = m
    return latest


def _classify_value(ref, value):
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


def _compute_biomarker_stats(bw, biomarker_name, erroneous_ids=None):
    """Compute full stats dict for a biomarker from its measurements."""
    erroneous_ids = erroneous_ids or set()
    rows = [m for m in bw.get("measurements", [])
            if m.get("biomarker") == biomarker_name]
    valid = [r for r in rows
             if r.get("value") is not None and r.get("date")
             and r.get("id") not in erroneous_ids]
    valid.sort(key=lambda r: r["date"])
    if not valid:
        return {"n": 0}
    nums = [r["value"] for r in valid]
    first, last = valid[0], valid[-1]
    try:
        days = (datetime.fromisoformat(last["date"])
                - datetime.fromisoformat(first["date"])).days
    except (ValueError, TypeError):
        days = 0
    pct = ((last["value"] - first["value"]) / first["value"] * 100
           ) if first["value"] else None
    return {
        "first_value": first["value"], "first_date": first["date"],
        "latest_value": last["value"], "latest_date": last["date"],
        "min": min(nums), "max": max(nums),
        "mean": round(mean(nums), 3), "median": round(median(nums), 3),
        "n": len(valid),
        "span_days": days,
        "pct_change": round(pct, 1) if pct is not None else None,
    }


def _refresh_biomarker_status(bw, biomarker, erroneous_ids=None):
    """Recompute stats and latest_status on a biomarker dict in-place."""
    stats = _compute_biomarker_stats(bw, biomarker["name"], erroneous_ids)
    biomarker["stats"] = stats
    ref = biomarker.get("reference")
    if ref and stats.get("latest_value") is not None:
        biomarker["latest_status"] = _classify_value(ref, stats["latest_value"])
    elif stats.get("n", 0) == 0:
        biomarker["latest_status"] = None
    return biomarker


# ── Shared analytics helpers ─────────────────────────────────────────────────


def _get_measurements_for(bw, biomarker_name, erroneous_ids=None,
                          start_date=None, end_date=None):
    """Get sorted, filtered measurements for a biomarker."""
    erroneous_ids = erroneous_ids or set()
    rows = [
        m for m in bw.get("measurements", [])
        if m.get("biomarker") == biomarker_name
        and m.get("value") is not None
        and m.get("date")
        and m.get("id") not in erroneous_ids
    ]
    if start_date:
        rows = [m for m in rows if m["date"] >= start_date]
    if end_date:
        rows = [m for m in rows if m["date"] <= end_date]
    rows.sort(key=lambda m: m["date"])
    return rows


def _linear_regression(date_strings, values):
    """Compute linear regression on (date_string, value) pairs.
    Returns {"slope", "intercept", "r_squared"} or None if < 2 points."""
    if len(date_strings) < 2 or len(values) < 2:
        return None
    try:
        base = datetime.fromisoformat(date_strings[0])
        xs = [(datetime.fromisoformat(d) - base).days for d in date_strings]
    except (ValueError, TypeError):
        return None
    ys = values
    n = len(xs)
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return None
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    y_mean = sy / n
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 1.0
    return {
        "slope": round(slope, 6),
        "intercept": round(intercept, 3),
        "r_squared": round(r_squared, 4),
    }


def _approx_t_pvalue(t_stat, df):
    """Approximate two-tailed p-value for t-distribution."""
    if df <= 0:
        return 1.0
    # Normal approximation (good for df > 30, reasonable for smaller)
    z = abs(t_stat) * (1 - 1 / (4 * max(df, 1))) / sqrt(1 + t_stat * t_stat / (2 * max(df, 1)))
    p = 1.0 - 0.5 * (1.0 + erf(z / sqrt(2)))
    return min(1.0, max(0.0, 2 * p))


def _pearson_r(a, b):
    """Compute Pearson correlation coefficient and p-value."""
    n = len(a)
    if n < 3:
        return {"r": 0, "p": 1.0, "n": n}
    sa, sb = sum(a), sum(b)
    sa2 = sum(x * x for x in a)
    sb2 = sum(x * x for x in b)
    sab = sum(x * y for x, y in zip(a, b))
    num = n * sab - sa * sb
    den_sq = (n * sa2 - sa * sa) * (n * sb2 - sb * sb)
    if den_sq <= 0:
        return {"r": 0, "p": 1.0, "n": n}
    r = max(-1.0, min(1.0, num / sqrt(den_sq)))
    t_stat = r * sqrt((n - 2) / (1 - r * r + 1e-15))
    p = _approx_t_pvalue(t_stat, n - 2)
    return {"r": round(r, 4), "p": round(p, 4), "n": n}


def _spearman_r(a, b):
    """Compute Spearman rank correlation."""
    n = len(a)
    if n < 3:
        return {"rho": 0, "p": 1.0, "n": n}

    def _rank(values):
        indexed = sorted(enumerate(values), key=lambda x: x[1])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and indexed[j + 1][1] == indexed[j][1]:
                j += 1
            avg_rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[indexed[k][0]] = avg_rank
            i = j + 1
        return ranks

    ra, rb = _rank(a), _rank(b)
    result = _pearson_r(ra, rb)
    return {"rho": result["r"], "p": result["p"], "n": n}


def _align_time_series(series_a, series_b, max_gap_days=90):
    """Align two measurement series by date with linear interpolation.
    Each series is list of {"date": "YYYY-MM-DD", "value": float}.
    Returns (aligned_a_values, aligned_b_values, aligned_dates)."""
    if len(series_a) < 2 or len(series_b) < 2:
        return [], [], []

    epoch = datetime(1970, 1, 1)

    def to_day(d):
        return (datetime.fromisoformat(d) - epoch).days

    a = sorted([(to_day(m["date"]), m["value"]) for m in series_a], key=lambda x: x[0])
    b = sorted([(to_day(m["date"]), m["value"]) for m in series_b], key=lambda x: x[0])

    all_days = sorted(set(d for d, _ in a) | set(d for d, _ in b))

    def interp(series, day):
        if day < series[0][0] or day > series[-1][0]:
            return None
        for i in range(len(series)):
            if series[i][0] == day:
                return series[i][1]
            if i > 0 and series[i][0] > day:
                prev_d, prev_v = series[i - 1]
                next_d, next_v = series[i]
                if next_d - prev_d > max_gap_days:
                    return None
                t = (day - prev_d) / (next_d - prev_d)
                return prev_v + t * (next_v - prev_v)
        return None

    aligned_a, aligned_b, dates = [], [], []
    for d in all_days:
        va = interp(a, d)
        vb = interp(b, d)
        if va is not None and vb is not None:
            aligned_a.append(va)
            aligned_b.append(vb)
            dates.append((epoch + timedelta(days=d)).strftime("%Y-%m-%d"))

    return aligned_a, aligned_b, dates


def _compute_biomarker_score(ref, value):
    """Compute a 0-100 health score for a single biomarker value given its reference range.
    100 = perfectly in green, 50 = amber boundary, 0 = deep red."""
    if ref is None or value is None:
        return None
    green = ref.get("green")
    amber = ref.get("amber")
    red_low = ref.get("red_low")
    red_high = ref.get("red_high")

    # In green range → 100
    if green and len(green) == 2:
        if green[0] <= value <= green[1]:
            return 100.0

        # In amber range → 50-99
        if amber:
            for rng in amber:
                if len(rng) == 2 and rng[0] <= value <= rng[1]:
                    # How close to green boundary?
                    if rng[1] <= green[0]:
                        # Lower amber band
                        width = green[0] - rng[0]
                        dist = value - rng[0]
                        return round(50 + 49 * (dist / width) if width > 0 else 50, 1)
                    elif rng[0] >= green[1]:
                        # Upper amber band
                        width = rng[1] - green[1]
                        dist = rng[1] - value
                        return round(50 + 49 * (dist / width) if width > 0 else 50, 1)

        # In red zone → 0-49
        if red_low is not None and value < red_low:
            if amber:
                # Distance from amber lower bound
                amber_low = min(r[0] for r in amber if len(r) == 2)
                dist = amber_low - value
                span = amber_low - red_low if amber_low > red_low else 1
                return round(max(0, 49 * (1 - dist / span)), 1)
            return max(0, round(49 * value / red_low, 1)) if red_low > 0 else 0

        if red_high is not None and value > red_high:
            if amber:
                amber_high = max(r[1] for r in amber if len(r) == 2)
                dist = value - amber_high
                span = red_high - amber_high if red_high > amber_high else 1
                return round(max(0, 49 * (1 - dist / span)), 1)
            return max(0, round(49 * red_high / value, 1)) if value > 0 else 0

        # Outside green but no amber/red defined
        mid = (green[0] + green[1]) / 2
        half = (green[1] - green[0]) / 2 if green[1] > green[0] else 1
        deviation = abs(value - mid) / half
        return round(max(0, 100 - deviation * 50), 1)

    return None


# ── Tool definitions (JSON Schema for MCP tools/list) ───────────────────────

TOOLS = [
    {
        "name": "get_health_overview",
        "description": (
            "Get an AI-generated health summary including headline, category "
            "breakdowns, and prioritised recommendations. No parameters needed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_biomarkers",
        "description": (
            "List all biomarkers with their latest value and status. "
            "Optionally filter by category or RAG status."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": 'Filter by category (e.g. "Hormones", "Lipids"). Case-insensitive.',
                },
                "status": {
                    "type": "string",
                    "description": 'Filter by RAG status: "red", "amber", "green", or "flagged" (red+amber).',
                },
            },
        },
    },
    {
        "name": "get_biomarker_detail",
        "description": (
            "Get full detail for a single biomarker: stats, reference ranges, "
            "AI insight, and recent measurements."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": 'Biomarker name (fuzzy matched, e.g. "testosterone", "ApoB").',
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_measurements",
        "description": "Get raw measurement history for a biomarker with optional date filtering.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "biomarker": {
                    "type": "string",
                    "description": "Biomarker name (fuzzy matched).",
                },
                "start_date": {
                    "type": "string",
                    "description": "Include measurements on or after this date (YYYY-MM-DD).",
                },
                "end_date": {
                    "type": "string",
                    "description": "Include measurements on or before this date (YYYY-MM-DD).",
                },
            },
            "required": ["biomarker"],
        },
    },
    {
        "name": "get_flagged_biomarkers",
        "description": (
            "Get all red and amber biomarkers, sorted by severity (red first, "
            "then amber), with stats and AI insights for each."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_events",
        "description": "Get health events (TRT doses, supplement changes, scans, etc.) with optional filtering.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_type": {
                    "type": "string",
                    "description": 'Filter by type (e.g. "trt_dose", "supplement_change", "scan_dexa").',
                },
                "start_date": {
                    "type": "string",
                    "description": "Include events on or after this date (YYYY-MM-DD).",
                },
                "end_date": {
                    "type": "string",
                    "description": "Include events on or before this date (YYYY-MM-DD).",
                },
            },
        },
    },
    {
        "name": "get_category_summary",
        "description": "Get a summary of all biomarkers in a specific category.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": 'Category name (e.g. "Hormones", "Lipids", "Liver"). Case-insensitive.',
                },
            },
            "required": ["category"],
        },
    },
    {
        "name": "refresh_data",
        "description": "Force re-fetch of all data from S3. Use after uploading new bloodwork results.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "add_event",
        "description": "Add a health event (e.g. started a supplement, TRT dose change, scan).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Event date in YYYY-MM-DD format.",
                },
                "type": {
                    "type": "string",
                    "description": 'Event type (e.g. "supplement_change", "trt_dose", "scan_dexa", "diet_change", "lifestyle", "medical").',
                },
                "title": {
                    "type": "string",
                    "description": "Short description of the event.",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional longer notes or context.",
                },
            },
            "required": ["date", "type", "title"],
        },
    },
    {
        "name": "add_measurement",
        "description": "Add a manual measurement for a biomarker.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Measurement date in YYYY-MM-DD format.",
                },
                "biomarker": {
                    "type": "string",
                    "description": "Biomarker name (fuzzy matched to canonical name).",
                },
                "value": {
                    "type": "number",
                    "description": "Numeric measurement value.",
                },
                "unit": {
                    "type": "string",
                    "description": 'Unit of measurement (e.g. "nmol/L", "mg/dL").',
                },
                "auto_create": {
                    "type": "boolean",
                    "description": "If true and no matching biomarker exists, create it automatically.",
                },
                "category": {
                    "type": "string",
                    "description": 'Category for auto-created biomarkers (default: "Uncategorised").',
                },
            },
            "required": ["date", "biomarker", "value", "unit"],
        },
    },
    {
        "name": "create_biomarker",
        "description": (
            "Create a brand new biomarker/metric definition with metadata. "
            "Use this to define a new trackable metric before adding measurements, "
            "or use add_measurement with auto_create=true for a simpler flow."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": 'Canonical name for the biomarker (e.g. "Sleep Quality", "Grip Strength").',
                },
                "category": {
                    "type": "string",
                    "description": 'Category grouping (e.g. "Sleep", "Biometrics"). New categories are auto-added.',
                },
                "unit": {
                    "type": "string",
                    "description": 'Unit of measurement (e.g. "hours", "score", "kg").',
                },
                "reference": {
                    "type": "object",
                    "description": (
                        "Optional reference ranges for RAG classification. "
                        'Example: {"green": [7, 9], "amber": [[6, 7], [9, 10]], "red_low": 5, "red_high": 11, "tag": "Optimal 7-9h"}'
                    ),
                },
            },
            "required": ["name", "category", "unit"],
        },
    },
    {
        "name": "update_biomarker",
        "description": (
            "Update metadata on an existing biomarker (fuzzy matched). "
            "Can change category, unit, reference ranges, or insight text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Biomarker name (fuzzy matched to existing).",
                },
                "category": {
                    "type": "string",
                    "description": "New category for the biomarker.",
                },
                "unit": {
                    "type": "string",
                    "description": "New unit of measurement.",
                },
                "reference": {
                    "type": "object",
                    "description": "Set or replace reference ranges for RAG classification.",
                },
                "clear_reference": {
                    "type": "boolean",
                    "description": "If true, remove reference ranges entirely.",
                },
                "insight": {
                    "type": "string",
                    "description": "Set or update the insight text. Pass empty string to clear.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "delete_biomarker",
        "description": (
            "Delete a biomarker definition and optionally its measurements. "
            "Use with caution — this removes the biomarker from the dashboard."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Biomarker name (fuzzy matched).",
                },
                "delete_measurements": {
                    "type": "boolean",
                    "description": "If true, also delete all measurements for this biomarker. Default false.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "mark_erroneous",
        "description": "Flag a measurement as erroneous so it's excluded from analysis.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "measurement_id": {
                    "type": "string",
                    "description": 'The measurement ID to flag (e.g. "m_abc123def456").',
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason for flagging.",
                },
            },
            "required": ["measurement_id"],
        },
    },
    {
        "name": "delete_measurement",
        "description": "Permanently delete a measurement by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "measurement_id": {
                    "type": "string",
                    "description": 'The measurement ID to delete (e.g. "m_abc123def456").',
                },
            },
            "required": ["measurement_id"],
        },
    },
    {
        "name": "import_apple_health",
        "description": (
            "Bulk-import Apple Health records into the dashboard. "
            "Accepts JSON-encoded records array. Each record should have: "
            "metric, value, date, unit. Supports: restingHeartRate, "
            "heartRateVariabilitySDNN, vo2Max, walkingHeartRateAverage, respiratoryRate, "
            "activeEnergyBurned, basalEnergyBurned, stepCount, bodyMass, "
            "bodyFatPercentage, leanBodyMass, distanceWalkingRunning, "
            "dietaryEnergyConsumed, dietaryProtein, dietaryCarbohydrates, "
            "dietaryFatTotal, dietaryFiber."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "records_json": {
                    "type": "string",
                    "description": "JSON string of the Apple Health records array.",
                },
            },
            "required": ["records_json"],
        },
    },
    {
        "name": "log_nutrition",
        "description": (
            "Log daily nutrition intake. Upserts by date — re-logging the same date "
            "replaces the previous entry. Also calculates TDEE from Apple Health "
            "activity data and injects nutrition biomarkers into the dashboard."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format.",
                },
                "calories": {
                    "type": "number",
                    "description": "Total calories consumed.",
                },
                "protein_g": {
                    "type": "number",
                    "description": "Protein in grams.",
                },
                "carbs_g": {
                    "type": "number",
                    "description": "Carbohydrates in grams.",
                },
                "fat_g": {
                    "type": "number",
                    "description": "Fat in grams.",
                },
                "fibre_g": {
                    "type": "number",
                    "description": "Fibre in grams (default 0).",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional notes about the day's nutrition.",
                },
            },
            "required": ["date", "calories", "protein_g", "carbs_g", "fat_g"],
        },
    },
    {
        "name": "get_nutrition",
        "description": (
            "Retrieve nutrition log entries with optional date filtering. "
            "Each entry is enriched with TDEE and daily deficit calculated "
            "from Apple Health activity data."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Include entries on or after this date (YYYY-MM-DD).",
                },
                "end_date": {
                    "type": "string",
                    "description": "Include entries on or before this date (YYYY-MM-DD).",
                },
            },
        },
    },
    {
        "name": "import_macrofactor",
        "description": (
            "Import a MacroFactor workout CSV export. Parses exercises and sets, "
            "computes e1RM and volume biomarkers under 'Strength' category, and "
            "stores in lifting.json. Re-importing is safe — duplicates are detected "
            "by set-level hash."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "csv_data": {
                    "type": "string",
                    "description": "The full CSV file content as a string.",
                },
            },
            "required": ["csv_data"],
        },
    },
    {
        "name": "import_macrofactor_nutrition",
        "description": (
            "Import a MacroFactor food-level CSV export and aggregate into daily "
            "nutrition totals. Each row is a food item; they are summed per date for "
            "Calories, Protein, Carbs, Fat, and Fibre. Re-importing is safe — existing "
            "days are overwritten with fresh totals."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "csv_data": {
                    "type": "string",
                    "description": "The full CSV file content as a string.",
                },
            },
            "required": ["csv_data"],
        },
    },
    {
        "name": "get_lifting",
        "description": (
            "Query lifting sessions with optional exercise and date filters. "
            "Returns matching sessions and a list of all exercise names."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "exercise": {
                    "type": "string",
                    "description": "Filter by exercise name (case-insensitive substring match).",
                },
                "start_date": {
                    "type": "string",
                    "description": "Include sessions on or after this date (YYYY-MM-DD).",
                },
                "end_date": {
                    "type": "string",
                    "description": "Include sessions on or before this date (YYYY-MM-DD).",
                },
            },
        },
    },
    {
        "name": "get_coaching_brief",
        "description": (
            "Primary entry point for new coaching chats. Returns a comprehensive "
            "snapshot: active goals, recently achieved goals, pending and recently "
            "completed action items, last coaching notes, plus a health snapshot "
            "(overview headline, flagged biomarkers, recent events, latest weight/body fat). "
            "No parameters needed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "add_goal",
        "description": "Create a new coaching goal. Status defaults to 'active'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": 'Goal title (e.g. "Cut to 85kg by Sep 2026").',
                },
                "category": {
                    "type": "string",
                    "description": 'Optional category (e.g. "body_composition", "lipids", "strength").',
                },
                "target_value": {
                    "type": "number",
                    "description": "Optional numeric target.",
                },
                "target_unit": {
                    "type": "string",
                    "description": 'Optional unit for target (e.g. "kg", "mg/dL").',
                },
                "target_date": {
                    "type": "string",
                    "description": "Optional target date (YYYY-MM-DD).",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_goal",
        "description": "Update a coaching goal and/or append a progress note. Status: active/achieved/abandoned.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal_id": {
                    "type": "string",
                    "description": 'The goal ID (e.g. "g_abc123def456").',
                },
                "status": {
                    "type": "string",
                    "description": 'New status: "active", "achieved", or "abandoned".',
                },
                "title": {
                    "type": "string",
                    "description": "Updated title.",
                },
                "target_value": {
                    "type": "number",
                    "description": "Updated numeric target.",
                },
                "target_unit": {
                    "type": "string",
                    "description": "Updated unit for target.",
                },
                "target_date": {
                    "type": "string",
                    "description": "Updated target date (YYYY-MM-DD).",
                },
                "progress_note": {
                    "type": "string",
                    "description": "Text to append as a progress note with today's date.",
                },
            },
            "required": ["goal_id"],
        },
    },
    {
        "name": "add_coaching_note",
        "description": "Add a free-form coaching note for persistence across sessions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The note text.",
                },
                "tags": {
                    "type": "string",
                    "description": 'Optional comma-separated tags (e.g. "lipids,strategy").',
                },
                "date": {
                    "type": "string",
                    "description": "Note date (YYYY-MM-DD). Defaults to today.",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "add_action_item",
        "description": "Create a coaching action item (to-do), optionally linked to a goal.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": 'Action item title (e.g. "Book blood panel at Randox").',
                },
                "due_date": {
                    "type": "string",
                    "description": "Optional due date (YYYY-MM-DD).",
                },
                "goal_id": {
                    "type": "string",
                    "description": "Optional goal ID to link this action to.",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_action_item",
        "description": "Update a coaching action item. Status: pending/done/skipped.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action_id": {
                    "type": "string",
                    "description": 'The action item ID (e.g. "a_abc123def456").',
                },
                "status": {
                    "type": "string",
                    "description": 'New status: "pending", "done", or "skipped".',
                },
                "title": {
                    "type": "string",
                    "description": "Updated title.",
                },
                "due_date": {
                    "type": "string",
                    "description": "Updated due date (YYYY-MM-DD).",
                },
            },
            "required": ["action_id"],
        },
    },
    {
        "name": "update_overview",
        "description": (
            "Rewrite the AI health overview in bloodwork_data.json. "
            "categories and recommendations are JSON strings (array of objects)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "headline": {
                    "type": "string",
                    "description": "The overview headline text.",
                },
                "categories": {
                    "type": "string",
                    "description": 'JSON string — array of category objects.',
                },
                "recommendations": {
                    "type": "string",
                    "description": 'JSON string — array of recommendation objects.',
                },
            },
            "required": ["headline", "categories", "recommendations"],
        },
    },
    {
        "name": "get_phases",
        "description": "List all training phases. Optionally filter by status (active/completed).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": 'Filter by status: "active" or "completed".',
                },
            },
        },
    },
    {
        "name": "get_phase_detail",
        "description": "Get full detail for a single training phase by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "phase_id": {
                    "type": "string",
                    "description": 'The phase ID (e.g. "ph_abc123def456").',
                },
            },
            "required": ["phase_id"],
        },
    },
    {
        "name": "add_phase",
        "description": (
            "Create a new training phase with name, date range, targets, "
            "supplements, medications, and notes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": 'Phase name (e.g. "2026.5 — Cut to 75kg").',
                },
                "start_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format.",
                },
                "target": {
                    "type": "string",
                    "description": 'Phase target/goal description (e.g. "Cut to 75kg, 500cal deficit/day").',
                },
                "supplements": {
                    "type": "string",
                    "description": 'JSON string — array of {name, dose, timing} objects.',
                },
                "medications": {
                    "type": "string",
                    "description": 'JSON string — array of {name, dose, timing} objects.',
                },
                "notes": {
                    "type": "string",
                    "description": "Free-form notes for this phase.",
                },
            },
            "required": ["name", "start_date"],
        },
    },
    {
        "name": "update_phase",
        "description": (
            "Update any field on a training phase: status, name, target, "
            "supplements, medications, notes, end_date."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "phase_id": {
                    "type": "string",
                    "description": 'The phase ID (e.g. "ph_abc123def456").',
                },
                "name": {
                    "type": "string",
                    "description": "Updated phase name.",
                },
                "status": {
                    "type": "string",
                    "description": 'New status: "active" or "completed".',
                },
                "end_date": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format.",
                },
                "target": {
                    "type": "string",
                    "description": "Updated target description.",
                },
                "supplements": {
                    "type": "string",
                    "description": 'JSON string — array of {name, dose, timing} objects. Replaces existing.',
                },
                "medications": {
                    "type": "string",
                    "description": 'JSON string — array of {name, dose, timing} objects. Replaces existing.',
                },
                "notes": {
                    "type": "string",
                    "description": "Updated notes.",
                },
            },
            "required": ["phase_id"],
        },
    },
    {
        "name": "toggle_checklist",
        "description": "Toggle a supplement/medication checklist item for today (or a given date).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": 'The checklist item ID (e.g. "s_ph_abc123_Creatine").',
                },
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format. Defaults to today.",
                },
            },
            "required": ["item_id"],
        },
    },
    {
        "name": "get_checklist",
        "description": "Get checked items for today (or a given date).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format. Defaults to today.",
                },
            },
        },
    },
    # ── Analytics tools ──────────────────────────────────────────────────────
    {
        "name": "get_rolling_averages",
        "description": (
            "Compute rolling averages for a biomarker. Returns date-aligned "
            "rolling mean values alongside raw data points. Default window is 7 days."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "biomarker": {"type": "string", "description": "Biomarker name (fuzzy matched)."},
                "window_days": {"type": "integer", "description": "Rolling window size in days (default: 7)."},
                "start_date": {"type": "string", "description": "YYYY-MM-DD."},
                "end_date": {"type": "string", "description": "YYYY-MM-DD."},
            },
            "required": ["biomarker"],
        },
    },
    {
        "name": "detect_trends",
        "description": (
            "Detect biomarker trends using linear regression over recent measurements. "
            "Flags biomarkers trending towards amber/red thresholds with projected breach dates."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "lookback_days": {"type": "integer", "description": "Days of recent data to analyse (default: 90)."},
                "biomarker": {"type": "string", "description": "Specific biomarker (fuzzy matched). If omitted, scans all."},
            },
        },
    },
    {
        "name": "compute_correlation",
        "description": (
            "Compute Pearson and Spearman correlation between two biomarkers with "
            "automatic date alignment via linear interpolation. Includes lag analysis."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "biomarker_a": {"type": "string", "description": "First biomarker name (fuzzy matched)."},
                "biomarker_b": {"type": "string", "description": "Second biomarker name (fuzzy matched)."},
                "start_date": {"type": "string", "description": "YYYY-MM-DD."},
                "end_date": {"type": "string", "description": "YYYY-MM-DD."},
                "max_gap_days": {"type": "integer", "description": "Max interpolation gap in days (default: 90)."},
            },
            "required": ["biomarker_a", "biomarker_b"],
        },
    },
    {
        "name": "analyse_event_impact",
        "description": (
            "Analyse the impact of a health event on biomarkers by comparing "
            "measurements before and after the event date."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "The event ID to analyse."},
                "biomarkers": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Biomarker names to analyse. If omitted, auto-selects those with data in both periods.",
                },
                "window_days": {"type": "integer", "description": "Before/after window in days (default: 60)."},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "get_health_scores",
        "description": (
            "Compute composite health scores (0-100) per category based on deviation "
            "from optimal ranges. Higher = healthier. Also computes overall score and grade."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Specific category only. If omitted, computes all."},
            },
        },
    },
    {
        "name": "get_goal_progress",
        "description": (
            "Get progress tracking for coaching goals by linking to biomarker data. "
            "Auto-computes current value, progress %, rate of change, and projected completion."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string", "description": "Specific goal ID. If omitted, returns all active goals with targets."},
            },
        },
    },
    {
        "name": "generate_insights",
        "description": (
            "Generate AI-powered insights for biomarkers by analysing stats, trends, "
            "and context. Updates the insight field on the biomarker. Requires ANTHROPIC_API_KEY."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "biomarker": {"type": "string", "description": "Biomarker name (fuzzy matched). If omitted, regenerates all flagged."},
                "include_context": {"type": "boolean", "description": "Include events, phase, and goals as context (default: true)."},
            },
        },
    },
]

# ── Tool implementations ─────────────────────────────────────────────────────


def tool_get_health_overview(args):
    data = _load_data()
    bw = data["bloodwork"]
    overview = bw.get("overview", {})
    total = len(bw.get("biomarkers", []))
    by_status = {}
    for b in bw.get("biomarkers", []):
        s = b.get("latest_status") or "unknown"
        by_status[s] = by_status.get(s, 0) + 1
    return {
        "recipient": bw.get("recipient"),
        "generated_at": bw.get("generated_at"),
        "data_loaded_at": data.get("loaded_at"),
        "total_biomarkers": total,
        "status_counts": by_status,
        "headline": overview.get("headline"),
        "categories": overview.get("categories", []),
        "recommendations": overview.get("recommendations", []),
    }


def tool_list_biomarkers(args):
    data = _load_data()
    bw = data["bloodwork"]
    biomarkers = bw.get("biomarkers", [])
    erroneous_ids = set(data["events"].get("erroneous_ids", []))
    category = args.get("category")
    status = args.get("status")
    results = []
    for b in biomarkers:
        if category and b.get("category", "").lower() != category.lower():
            continue
        s = b.get("latest_status")
        if status:
            if status.lower() == "flagged":
                if s not in ("red", "amber"):
                    continue
            elif s != status.lower():
                continue
        stats = _compute_biomarker_stats(bw, b["name"], erroneous_ids)
        results.append({
            "name": b["name"],
            "category": b.get("category"),
            "latest_value": stats.get("latest_value"),
            "latest_date": stats.get("latest_date"),
            "unit": (b.get("units") or [None])[0],
            "status": s,
            "measurement_count": stats.get("n"),
        })
    return results


def tool_get_biomarker_detail(args):
    data = _load_data()
    bw = data["bloodwork"]
    biomarkers = bw.get("biomarkers", [])
    match = _fuzzy_match_biomarker(args["name"], biomarkers)
    if not match:
        available = sorted(set(b["name"] for b in biomarkers))
        return {"error": f"No biomarker matching '{args['name']}'", "available": available}
    erroneous_ids = set(data["events"].get("erroneous_ids", []))
    stats = _compute_biomarker_stats(bw, match["name"], erroneous_ids)
    measurements = [
        m for m in bw.get("measurements", [])
        if m.get("biomarker") == match["name"]
    ]
    measurements.sort(key=lambda m: m.get("date", ""), reverse=True)
    ref = match.get("reference")
    status = _classify_value(ref, stats.get("latest_value")) if ref and stats.get("latest_value") is not None else match.get("latest_status")
    return {
        "name": match["name"],
        "category": match.get("category"),
        "units": match.get("units"),
        "status": status,
        "stats": stats,
        "reference": ref,
        "insight": match.get("insight"),
        "recent_measurements": measurements[:10],
    }


def tool_get_measurements(args):
    data = _load_data()
    bw = data["bloodwork"]
    match = _fuzzy_match_biomarker(args["biomarker"], bw.get("biomarkers", []))
    if not match:
        available = sorted(set(b["name"] for b in bw.get("biomarkers", [])))
        return {"error": f"No biomarker matching '{args['biomarker']}'", "available": available}
    resolved_name = match["name"]
    erroneous_ids = set(data["events"].get("erroneous_ids", []))
    start_date = args.get("start_date")
    end_date = args.get("end_date")
    measurements = []
    for m in bw.get("measurements", []):
        if m.get("biomarker") != resolved_name:
            continue
        d = m.get("date", "")
        if start_date and d < start_date:
            continue
        if end_date and d > end_date:
            continue
        entry = {
            "date": d,
            "value": m.get("value"),
            "unit": m.get("unit"),
            "source": m.get("source_label"),
            "rag": m.get("rag"),
        }
        if m.get("id") in erroneous_ids:
            entry["flagged_erroneous"] = True
        measurements.append(entry)
    measurements.sort(key=lambda m: m["date"])
    return {
        "biomarker": resolved_name,
        "unit": (match.get("units") or [None])[0],
        "count": len(measurements),
        "measurements": measurements,
    }


def tool_get_flagged_biomarkers(args):
    data = _load_data()
    bw = data["bloodwork"]
    biomarkers = bw.get("biomarkers", [])
    erroneous_ids = set(data["events"].get("erroneous_ids", []))
    severity_order = {"red": 0, "amber": 1}
    flagged = []
    for b in biomarkers:
        stats = _compute_biomarker_stats(bw, b["name"], erroneous_ids)
        ref = b.get("reference")
        s = _classify_value(ref, stats.get("latest_value")) if ref and stats.get("latest_value") is not None else b.get("latest_status")
        if s not in severity_order:
            continue
        flagged.append({
            "name": b["name"],
            "category": b.get("category"),
            "status": s,
            "latest_value": stats.get("latest_value"),
            "latest_date": stats.get("latest_date"),
            "unit": (b.get("units") or [None])[0],
            "reference": ref,
            "insight": b.get("insight"),
            "pct_change": stats.get("pct_change"),
        })
    flagged.sort(key=lambda b: (severity_order.get(b["status"], 99), b["name"]))
    return flagged


def tool_get_events(args):
    data = _load_data()
    events = data["events"].get("events", [])
    event_type = args.get("event_type")
    start_date = args.get("start_date")
    end_date = args.get("end_date")
    results = []
    for e in events:
        if event_type and e.get("type") != event_type:
            continue
        d = e.get("date", "")
        if start_date and d < start_date:
            continue
        if end_date and d > end_date:
            continue
        results.append(e)
    results.sort(key=lambda e: e.get("date", ""))
    return results


def tool_get_category_summary(args):
    data = _load_data()
    bw = data["bloodwork"]
    biomarkers = bw.get("biomarkers", [])
    categories = bw.get("categories", [])
    matched_cat = None
    for c in categories:
        if c.lower() == args["category"].lower():
            matched_cat = c
            break
    if not matched_cat:
        return {"error": f"Unknown category '{args['category']}'", "available": categories}
    items = []
    status_counts = {}
    for b in biomarkers:
        if b.get("category") != matched_cat:
            continue
        s = b.get("latest_status") or "unknown"
        status_counts[s] = status_counts.get(s, 0) + 1
        stats = b.get("stats") or {}
        items.append({
            "name": b["name"],
            "status": s,
            "latest_value": stats.get("latest_value"),
            "latest_date": stats.get("latest_date"),
            "unit": (b.get("units") or [None])[0],
            "pct_change": stats.get("pct_change"),
            "insight": b.get("insight"),
        })
    overview_match = None
    for oc in bw.get("overview", {}).get("categories", []):
        if matched_cat.lower() in oc.get("title", "").lower():
            overview_match = oc
            break
    return {
        "category": matched_cat,
        "biomarker_count": len(items),
        "status_counts": status_counts,
        "overview": overview_match,
        "biomarkers": items,
    }


def tool_refresh_data(args):
    _cache.clear()
    data = _load_data(force=True)
    bw = data["bloodwork"]
    return {
        "status": "refreshed",
        "loaded_at": data.get("loaded_at"),
        "biomarker_count": len(bw.get("biomarkers", [])),
        "measurement_count": len(bw.get("measurements", [])),
        "event_count": len(data["events"].get("events", [])),
    }


def tool_add_event(args):
    data = _load_data(force=True)
    events_data = data["events"]
    event = {
        "id": "e_" + secrets.token_hex(6),
        "date": args["date"],
        "type": args["type"],
        "title": args["title"],
    }
    if args.get("notes"):
        event["notes"] = args["notes"]
    events_data.setdefault("events", []).append(event)
    _write_s3(EVENTS_KEY, events_data)
    return {"status": "added", "event": event}


def _ensure_category(bw, category):
    """Add a category to bw['categories'] if it doesn't already exist."""
    categories = bw.setdefault("categories", [])
    if not any(c.lower() == category.lower() for c in categories):
        categories.append(category)


def tool_create_biomarker(args):
    """Create a brand new biomarker definition."""
    data = _load_data(force=True)
    bw = data["bloodwork"]
    biomarkers = bw.setdefault("biomarkers", [])
    name = args["name"].strip()
    # Reject if already exists (case-insensitive)
    for b in biomarkers:
        if b["name"].lower() == name.lower():
            return {"error": f"Biomarker '{b['name']}' already exists"}
    category = args["category"].strip()
    unit = args["unit"].strip()
    biomarker = {
        "name": name,
        "category": category,
        "units": [unit],
        "stats": {"n": 0},
        "latest_status": None,
    }
    if args.get("reference"):
        biomarker["reference"] = args["reference"]
    biomarkers.append(biomarker)
    _ensure_category(bw, category)
    _write_s3(BLOODWORK_KEY, bw)
    return {"status": "created", "biomarker": {
        "name": name, "category": category, "unit": unit,
        "has_reference": bool(args.get("reference")),
    }}


def tool_update_biomarker(args):
    """Update metadata on an existing biomarker."""
    data = _load_data(force=True)
    bw = data["bloodwork"]
    match = _fuzzy_match_biomarker(args["name"], bw.get("biomarkers", []))
    if not match:
        available = sorted(set(b["name"] for b in bw.get("biomarkers", [])))
        return {"error": f"No biomarker matching '{args['name']}'", "available": available}
    changes = []
    if args.get("category"):
        match["category"] = args["category"].strip()
        _ensure_category(bw, match["category"])
        changes.append("category")
    if args.get("unit"):
        match["units"] = [args["unit"].strip()]
        changes.append("unit")
    if args.get("clear_reference"):
        match.pop("reference", None)
        match["latest_status"] = None
        changes.append("cleared_reference")
    elif args.get("reference"):
        match["reference"] = args["reference"]
        changes.append("reference")
    if "insight" in args:
        if args["insight"]:
            match["insight"] = args["insight"]
        else:
            match.pop("insight", None)
        changes.append("insight")
    # Recompute status if reference ranges changed
    if "reference" in changes or "cleared_reference" in changes:
        erroneous_ids = set(data["events"].get("erroneous_ids", []))
        _refresh_biomarker_status(bw, match, erroneous_ids)
    _write_s3(BLOODWORK_KEY, bw)
    return {"status": "updated", "biomarker": match["name"], "changes": changes}


def tool_delete_biomarker(args):
    """Delete a biomarker definition and optionally its measurements."""
    data = _load_data(force=True)
    bw = data["bloodwork"]
    biomarkers = bw.setdefault("biomarkers", [])
    match = _fuzzy_match_biomarker(args["name"], biomarkers)
    if not match:
        available = sorted(set(b["name"] for b in biomarkers))
        return {"error": f"No biomarker matching '{args['name']}'", "available": available}
    resolved_name = match["name"]
    category = match.get("category")
    biomarkers.remove(match)
    deleted_measurements = 0
    if args.get("delete_measurements"):
        measurements = bw.get("measurements", [])
        before = len(measurements)
        measurements[:] = [m for m in measurements if m.get("biomarker") != resolved_name]
        deleted_measurements = before - len(measurements)
    # Clean up orphaned categories
    if category:
        still_used = any(b.get("category") == category for b in biomarkers)
        if not still_used:
            cats = bw.get("categories", [])
            bw["categories"] = [c for c in cats if c != category]
    _write_s3(BLOODWORK_KEY, bw)
    result = {"status": "deleted", "biomarker": resolved_name}
    if args.get("delete_measurements"):
        result["deleted_measurements"] = deleted_measurements
    return result


def tool_add_measurement(args):
    data = _load_data(force=True)
    bw = data["bloodwork"]
    match = _fuzzy_match_biomarker(args["biomarker"], bw.get("biomarkers", []))
    auto_created = False
    if not match:
        if args.get("auto_create"):
            # Auto-create the biomarker
            name = args["biomarker"].strip()
            category = (args.get("category") or "Uncategorised").strip()
            unit = args["unit"].strip()
            match = {
                "name": name,
                "category": category,
                "units": [unit],
                "stats": {"n": 0},
                "latest_status": None,
            }
            bw.setdefault("biomarkers", []).append(match)
            _ensure_category(bw, category)
            auto_created = True
        else:
            available = sorted(set(b["name"] for b in bw.get("biomarkers", [])))
            return {"error": f"No biomarker matching '{args['biomarker']}'", "available": available}
    resolved_name = match["name"]
    id_key = f"claude_chat|claude_chat|{resolved_name}|{args['date']}|{args['value']}"
    measurement_id = "m_" + hashlib.sha1(id_key.encode()).hexdigest()[:12]
    measurement = {
        "id": measurement_id,
        "source": "claude_chat",
        "source_label": "Claude Chat",
        "biomarker": resolved_name,
        "date": args["date"],
        "value": args["value"],
        "unit": args["unit"],
    }
    bw.setdefault("measurements", []).append(measurement)
    erroneous_ids = set(data["events"].get("erroneous_ids", []))
    _refresh_biomarker_status(bw, match, erroneous_ids)
    _write_s3(BLOODWORK_KEY, bw)
    result = {"status": "added", "measurement": measurement, "matched_biomarker": resolved_name}
    if auto_created:
        result["auto_created_biomarker"] = True
    return result


def tool_mark_erroneous(args):
    data = _load_data(force=True)
    events_data = data["events"]
    erroneous_ids = events_data.setdefault("erroneous_ids", [])
    if args["measurement_id"] in erroneous_ids:
        return {"status": "already_flagged", "measurement_id": args["measurement_id"]}
    erroneous_ids.append(args["measurement_id"])
    _write_s3(EVENTS_KEY, events_data)
    result = {"status": "flagged", "measurement_id": args["measurement_id"]}
    if args.get("reason"):
        result["reason"] = args["reason"]
    return result


def tool_delete_measurement(args):
    """Delete a measurement by ID."""
    mid = args.get("measurement_id", "")
    if not mid:
        return {"error": "measurement_id is required"}

    data = _load_data(force=True)
    bw = data["bloodwork"]
    measurements = bw.setdefault("measurements", [])

    before = len(measurements)
    measurements[:] = [m for m in measurements if m.get("id") != mid]
    after = len(measurements)

    if before == after:
        return {"error": f"Measurement not found: {mid}"}

    # Also remove from erroneous list if present
    events_data = data["events"]
    erroneous = events_data.get("erroneous_ids", [])
    if mid in erroneous:
        erroneous.remove(mid)
        _write_s3(EVENTS_KEY, events_data)

    _write_s3(BLOODWORK_KEY, bw)
    return {"status": "deleted", "measurement_id": mid}


def tool_import_apple_health(args):
    try:
        records = json.loads(args["records_json"])
    except (json.JSONDecodeError, TypeError) as e:
        return {"error": f"Invalid JSON: {e}"}
    if not isinstance(records, list):
        return {"error": "Expected a JSON array of records"}
    data = _load_data(force=True)
    bw = data["bloodwork"]
    measurements = bw.setdefault("measurements", [])
    biomarkers = bw.setdefault("biomarkers", [])
    existing_ids = {m["id"] for m in measurements if "id" in m}
    biomarker_index = {b["name"]: b for b in biomarkers}

    # Build set of existing (biomarker, date) pairs from apple_health source
    # to enforce one-value-per-day for dietary metrics (prevents carry-forward dupes)
    _DAILY_UNIQUE_BIOMARKERS = {"Calories In", "Protein", "Carbs", "Fat", "Fibre",
                                 "Active Energy Burned", "Basal Energy Burned",
                                 "Step Count", "Distance Walking/Running"}
    existing_bio_date = set()
    for m in measurements:
        if m.get("source") == "apple_health" and m.get("biomarker") in _DAILY_UNIQUE_BIOMARKERS:
            existing_bio_date.add((m["biomarker"], m.get("date")))

    imported = 0
    skipped_unmapped = 0
    skipped_non_numeric = 0
    skipped_artefact = 0
    skipped_duplicate = 0
    per_metric = {}

    # Group records by (biomarker, date), keeping only the latest record per day
    # This prevents the carry-forward bug where yesterday's value appears on today
    from collections import defaultdict
    grouped = defaultdict(list)
    ungrouped = []
    for rec in records:
        metric = rec.get("metric")
        name = APPLE_HEALTH_MAP.get(metric)
        if not name:
            skipped_unmapped += 1
            continue
        if name in _DAILY_UNIQUE_BIOMARKERS:
            date_raw = rec.get("date") or ""
            date = date_raw[:10] if len(date_raw) >= 10 else ""
            grouped[(name, date)].append(rec)
        else:
            ungrouped.append(rec)

    # For daily-unique biomarkers, keep only the last record per (name, date)
    deduped_records = []
    for (name, date), recs in grouped.items():
        # Keep the last record (most recently added = most up-to-date value)
        deduped_records.append(recs[-1])
    deduped_records.extend(ungrouped)

    for rec in deduped_records:
        metric = rec.get("metric")
        name = APPLE_HEALTH_MAP.get(metric)
        if not name:
            skipped_unmapped += 1
            continue
        raw_value = rec.get("value")
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            skipped_non_numeric += 1
            continue
        if name in APPLE_HEALTH_ARTEFACT and APPLE_HEALTH_ARTEFACT[name](value):
            skipped_artefact += 1
            continue
        date_raw = rec.get("date") or ""
        date = date_raw[:10] if len(date_raw) >= 10 else None
        if not date:
            skipped_non_numeric += 1
            continue
        unit = rec.get("unit", "")
        unit = APPLE_HEALTH_UNIT_MAP.get(unit, unit)

        # Skip if this (biomarker, date) already exists in the DB
        if name in _DAILY_UNIQUE_BIOMARKERS and (name, date) in existing_bio_date:
            skipped_duplicate += 1
            continue
        existing_bio_date.add((name, date))

        id_key = f"apple_health|apple_watch|{name}|{date}|{raw_value}"
        mid = "m_" + hashlib.sha1(id_key.encode()).hexdigest()[:12]
        if mid in existing_ids:
            skipped_duplicate += 1
            continue
        existing_ids.add(mid)
        if name not in biomarker_index:
            category = APPLE_HEALTH_CATEGORY.get(name, "Biometrics")
            biomarker_entry = {
                "name": name,
                "category": category,
                "units": [unit] if unit else [],
                "stats": {"n": 0},
            }
            biomarkers.append(biomarker_entry)
            biomarker_index[name] = biomarker_entry
        measurements.append({
            "id": mid,
            "source": "apple_health",
            "source_label": "Apple Health",
            "biomarker": name,
            "date": date,
            "value": value,
            "unit": unit,
        })
        bio = biomarker_index[name]
        stats = bio.setdefault("stats", {})
        stats["n"] = stats.get("n", 0) + 1
        per_metric[name] = per_metric.get(name, 0) + 1
        imported += 1
    if "count" in bw:
        bw["count"] = len(biomarkers)
    if imported > 0:
        _write_s3(BLOODWORK_KEY, bw)
    return {
        "status": "ok",
        "total_records": len(records),
        "imported": imported,
        "skipped_unmapped": skipped_unmapped,
        "skipped_non_numeric": skipped_non_numeric,
        "skipped_artefact": skipped_artefact,
        "skipped_duplicate": skipped_duplicate,
        "per_metric": per_metric,
        "s3_written": imported > 0,
    }


# ── TDEE calculation helper ───────────────────────────────────────────────────


def _calculate_tdee(data, date_str):
    """Calculate TDEE for a given date from Active + Basal Energy Burned measurements."""
    bw = data["bloodwork"]
    measurements = bw.get("measurements", [])

    active = None
    basal = None
    for m in measurements:
        if m.get("date") != date_str:
            continue
        if m.get("biomarker") == "Active Energy Burned":
            active = m.get("value")
        elif m.get("biomarker") == "Basal Energy Burned":
            basal = m.get("value")

    # Fallback for basal: Mifflin-St Jeor estimate (single-user dashboard)
    if basal is None:
        # Look up latest weight
        weight = None
        for m in sorted(measurements, key=lambda x: x.get("date", ""), reverse=True):
            if m.get("biomarker") == "Weight" and m.get("value"):
                weight = m["value"]
                break
        # Hardcoded for single-user: height_cm=183, age=35, male
        if weight:
            basal = round(10 * weight + 6.25 * 183 - 5 * 35 + 5)
        else:
            basal = 1800  # conservative default

    if active is None:
        active = 0

    tdee = round(basal + active)
    return {"tdee": tdee, "basal": round(basal), "active": round(active)}


def _inject_nutrition_biomarkers(data, entry, tdee_info):
    """Create/update biomarker entries + measurements for nutrition metrics."""
    bw = data["bloodwork"]
    biomarkers = bw.setdefault("biomarkers", [])
    measurements = bw.setdefault("measurements", [])
    biomarker_index = {b["name"]: b for b in biomarkers}

    date_str = entry["date"]
    deficit = tdee_info["tdee"] - entry.get("calories", 0)

    nutrition_metrics = {
        "Calories In": {"value": entry.get("calories", 0), "unit": "kcal"},
        "Protein": {"value": entry.get("protein_g", 0), "unit": "g"},
        "Carbs": {"value": entry.get("carbs_g", 0), "unit": "g"},
        "Fat": {"value": entry.get("fat_g", 0), "unit": "g"},
        "Fibre": {"value": entry.get("fibre_g", 0), "unit": "g"},
        "TDEE": {"value": tdee_info["tdee"], "unit": "kcal"},
        "Daily Deficit": {"value": deficit, "unit": "kcal"},
    }

    for metric_name, info in nutrition_metrics.items():
        # Ensure biomarker entry exists
        if metric_name not in biomarker_index:
            bio_entry = {
                "name": metric_name,
                "category": "Nutrition",
                "units": [info["unit"]],
                "stats": {"n": 0},
            }
            biomarkers.append(bio_entry)
            biomarker_index[metric_name] = bio_entry

        # Generate deterministic ID
        id_key = f"nutrition|log|{metric_name}|{date_str}|{info['value']}"
        mid = "m_" + hashlib.sha1(id_key.encode()).hexdigest()[:12]

        # Remove any existing measurement for this metric+date (upsert)
        measurements[:] = [
            m for m in measurements
            if not (m.get("biomarker") == metric_name and m.get("date") == date_str
                    and m.get("source") == "nutrition_log")
        ]

        measurements.append({
            "id": mid,
            "source": "nutrition_log",
            "source_label": "Nutrition Log",
            "biomarker": metric_name,
            "date": date_str,
            "value": info["value"],
            "unit": info["unit"],
        })

        # Update stats count
        bio = biomarker_index[metric_name]
        n = sum(1 for m in measurements if m.get("biomarker") == metric_name)
        bio.setdefault("stats", {})["n"] = n

    if "count" in bw:
        bw["count"] = len(biomarkers)


def _recompute_all_nutrition_biomarkers(data):
    """Remove old nutrition_log measurements, recompute from all nutrition entries."""
    bw = data["bloodwork"]
    measurements = bw.setdefault("measurements", [])
    biomarkers = bw.setdefault("biomarkers", [])

    # Remove all existing nutrition_log measurements
    measurements[:] = [m for m in measurements if m.get("source") != "nutrition_log"]

    # Remove biomarkers that only had nutrition_log data (category == "Nutrition")
    biomarkers[:] = [b for b in biomarkers if b.get("category") != "Nutrition"]

    nutrition = data.get("nutrition", {})
    entries = nutrition.get("entries", [])

    for entry in entries:
        date_str = entry.get("date")
        if not date_str:
            continue
        tdee_info = _calculate_tdee(data, date_str)
        _inject_nutrition_biomarkers(data, entry, tdee_info)

    # Populate latest_value / latest_date on each Nutrition biomarker's stats
    for b in biomarkers:
        if b.get("category") != "Nutrition":
            continue
        latest = None
        for m in measurements:
            if m.get("biomarker") == b["name"] and m.get("source") == "nutrition_log":
                if latest is None or m.get("date", "") > latest.get("date", ""):
                    latest = m
        if latest:
            stats = b.setdefault("stats", {})
            stats["latest_value"] = latest["value"]
            stats["latest_date"] = latest["date"]

    # Ensure "Nutrition" is in categories list
    categories = bw.setdefault("categories", [])
    if "Nutrition" not in categories and any(b.get("category") == "Nutrition" for b in biomarkers):
        categories.append("Nutrition")

    if "count" in bw:
        bw["count"] = len(biomarkers)


def tool_log_nutrition(args):
    """Log daily nutrition intake."""
    date_str = args.get("date")
    if not date_str:
        return {"error": "date is required (YYYY-MM-DD)"}

    calories = args.get("calories", 0)
    protein_g = args.get("protein_g", 0)
    carbs_g = args.get("carbs_g", 0)
    fat_g = args.get("fat_g", 0)
    fibre_g = args.get("fibre_g", 0)
    notes = args.get("notes", "")

    data = _load_data(force=True)
    nutrition = data["nutrition"]
    entries = nutrition.setdefault("entries", [])

    # Upsert by date
    entry = None
    for e in entries:
        if e.get("date") == date_str:
            entry = e
            break
    if entry is None:
        entry = {"date": date_str}
        entries.append(entry)

    entry.update({
        "calories": calories,
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
        "fibre_g": fibre_g,
        "notes": notes,
        "logged_at": datetime.utcnow().isoformat() + "Z",
    })

    # Calculate TDEE and inject biomarker measurements
    tdee_info = _calculate_tdee(data, date_str)
    _inject_nutrition_biomarkers(data, entry, tdee_info)

    # Save references before writing (writes clear cache)
    bw = data["bloodwork"]
    nutrition_snapshot = json.loads(json.dumps(nutrition))
    bw_snapshot = json.loads(json.dumps(bw))

    # Write both files
    s3 = _s3_client()
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=NUTRITION_KEY,
        Body=json.dumps(nutrition_snapshot, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=BLOODWORK_KEY,
        Body=json.dumps(bw_snapshot, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    _cache.clear()
    _invalidate_cloudfront(BLOODWORK_KEY)

    deficit = tdee_info["tdee"] - calories
    return {
        "status": "logged",
        "date": date_str,
        "calories": calories,
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
        "fibre_g": fibre_g,
        "tdee": tdee_info["tdee"],
        "basal": tdee_info["basal"],
        "active": tdee_info["active"],
        "deficit": deficit,
    }


def tool_get_nutrition(args):
    """Retrieve nutrition entries with optional date filtering."""
    data = _load_data()
    nutrition = data["nutrition"]
    entries = nutrition.get("entries", [])

    start_date = args.get("start_date")
    end_date = args.get("end_date")

    results = []
    for e in entries:
        d = e.get("date", "")
        if start_date and d < start_date:
            continue
        if end_date and d > end_date:
            continue
        # Enrich with TDEE/deficit
        tdee_info = _calculate_tdee(data, d)
        deficit = tdee_info["tdee"] - e.get("calories", 0)
        results.append({
            **e,
            "fibre_g": e.get("fibre_g", 0),
            "tdee": tdee_info["tdee"],
            "basal": tdee_info["basal"],
            "active": tdee_info["active"],
            "deficit": deficit,
        })

    results.sort(key=lambda x: x.get("date", ""))
    return {"count": len(results), "entries": results}


def tool_import_macrofactor_nutrition(args):
    """Import a MacroFactor food-level CSV export and aggregate into daily nutrition totals."""
    csv_data = args.get("csv_data", "")
    if not csv_data:
        return {"error": "csv_data is required"}

    data = _load_data(force=True)
    nutrition = data["nutrition"]
    entries = nutrition.setdefault("entries", [])

    # Strip BOM if present
    if csv_data.startswith("\ufeff"):
        csv_data = csv_data[1:]

    # Parse CSV and aggregate daily totals
    reader = csv.DictReader(io.StringIO(csv_data))
    daily = {}

    rows_parsed = 0
    for row in reader:
        date = (row.get("Date") or "").strip()
        if not date:
            continue
        rows_parsed += 1

        if date not in daily:
            daily[date] = {"calories": 0.0, "protein_g": 0.0,
                           "carbs_g": 0.0, "fat_g": 0.0, "fibre_g": 0.0}

        d = daily[date]
        d["calories"] += float(row.get("Calories (kcal)") or 0)
        d["protein_g"] += float(row.get("Protein (g)") or 0)
        d["carbs_g"] += float(row.get("Carbs (g)") or 0)
        d["fat_g"] += float(row.get("Fat (g)") or 0)
        d["fibre_g"] += float(row.get("Fiber (g)") or 0)

    # Build index for existing entries
    entry_index = {e["date"]: e for e in entries}

    days_added = 0
    days_updated = 0

    for date, totals in sorted(daily.items()):
        if date in entry_index:
            entry = entry_index[date]
            days_updated += 1
        else:
            entry = {"date": date}
            entries.append(entry)
            entry_index[date] = entry
            days_added += 1

        entry.update({
            "calories": round(totals["calories"], 1),
            "protein_g": round(totals["protein_g"], 1),
            "carbs_g": round(totals["carbs_g"], 1),
            "fat_g": round(totals["fat_g"], 1),
            "fibre_g": round(totals["fibre_g"], 1),
            "logged_at": datetime.utcnow().isoformat() + "Z",
        })

    # Sort entries by date
    entries.sort(key=lambda e: e.get("date", ""))

    # Recompute ALL nutrition biomarkers from scratch
    _recompute_all_nutrition_biomarkers(data)

    # Snapshot before writing (writes clear cache)
    nutrition_snapshot = json.loads(json.dumps(nutrition))
    bw_snapshot = json.loads(json.dumps(data["bloodwork"]))

    s3 = _s3_client()
    s3.put_object(
        Bucket=S3_BUCKET, Key=NUTRITION_KEY,
        Body=json.dumps(nutrition_snapshot, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    s3.put_object(
        Bucket=S3_BUCKET, Key=BLOODWORK_KEY,
        Body=json.dumps(bw_snapshot, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    _cache.clear()
    _invalidate_cloudfront(BLOODWORK_KEY)

    return {
        "status": "ok",
        "rows_parsed": rows_parsed,
        "days_total": len(daily),
        "days_added": days_added,
        "days_updated": days_updated,
        "total_entries": len(entries),
        "date_range": {
            "start": min(daily.keys()) if daily else None,
            "end": max(daily.keys()) if daily else None,
        },
    }


# ── Lifting helpers ──────────────────────────────────────────────────────────

WORKING_SET_TYPES = {"Standard Set", "Failure Set",
                     "Standard Set (L)", "Standard Set (R)",
                     "Failure Set (L)", "Failure Set (R)"}


def _upsert_lifting_measurement(bw, biomarker_index, name, category, date, value, unit):
    """Create biomarker if needed and append a lifting_log measurement."""
    biomarkers = bw.setdefault("biomarkers", [])
    measurements = bw.setdefault("measurements", [])

    if name not in biomarker_index:
        bio_entry = {
            "name": name,
            "category": category,
            "units": [unit],
            "stats": {"n": 0},
        }
        biomarkers.append(bio_entry)
        biomarker_index[name] = bio_entry

    id_key = f"lifting_log|macrofactor|{name}|{date}|{value}"
    mid = "m_" + hashlib.sha1(id_key.encode()).hexdigest()[:12]

    measurements.append({
        "id": mid,
        "source": "lifting_log",
        "source_label": "Lifting Log",
        "biomarker": name,
        "date": date,
        "value": round(value, 2),
        "unit": unit,
    })

    bio = biomarker_index[name]
    bio.setdefault("stats", {})["n"] = bio["stats"].get("n", 0) + 1


def _inject_lifting_biomarkers(data):
    """Remove old lifting_log measurements, recompute from lifting sessions."""
    bw = data["bloodwork"]
    measurements = bw.setdefault("measurements", [])
    biomarkers = bw.setdefault("biomarkers", [])

    # Remove all existing lifting_log measurements
    measurements[:] = [m for m in measurements if m.get("source") != "lifting_log"]

    # Remove biomarkers that only had lifting_log data (category == "Strength")
    biomarkers[:] = [b for b in biomarkers if b.get("category") != "Strength"]

    biomarker_index = {b["name"]: b for b in biomarkers}

    lifting = data.get("lifting", {})
    sessions = lifting.get("sessions", [])

    for session in sessions:
        date = session["date"]
        session_volume = 0.0
        exercise_names = set()

        for exercise in session.get("exercises", []):
            ex_name = exercise["name"]
            exercise_names.add(ex_name)
            working_sets = [s for s in exercise.get("sets", [])
                           if s.get("set_type") in WORKING_SET_TYPES]

            if not working_sets:
                continue

            # e1RM: best Epley estimate across working sets
            best_e1rm = 0.0
            for s in working_sets:
                w = s.get("weight_kg") or 0
                r = s.get("reps") or 0
                if w > 0 and r > 0:
                    e1rm = w * (1 + r / 30)
                    best_e1rm = max(best_e1rm, e1rm)

            if best_e1rm > 0:
                _upsert_lifting_measurement(
                    bw, biomarker_index,
                    f"e1RM: {ex_name}", "Strength", date, best_e1rm, "kg",
                )

            # Volume: sum(weight * reps) for working sets
            vol = sum((s.get("weight_kg") or 0) * (s.get("reps") or 0)
                      for s in working_sets)
            if vol > 0:
                _upsert_lifting_measurement(
                    bw, biomarker_index,
                    f"Volume: {ex_name}", "Strength", date, vol, "kg",
                )
                session_volume += vol

        # Session-level metrics
        if session_volume > 0:
            _upsert_lifting_measurement(
                bw, biomarker_index,
                "Session Volume", "Strength", date, session_volume, "kg",
            )

        duration_s = session.get("duration_seconds")
        if duration_s and duration_s > 0:
            _upsert_lifting_measurement(
                bw, biomarker_index,
                "Session Duration", "Strength", date, round(duration_s / 60, 1), "min",
            )

        if exercise_names:
            _upsert_lifting_measurement(
                bw, biomarker_index,
                "Session Exercises", "Strength", date, len(exercise_names), "count",
            )

    # Ensure "Strength" is in categories list
    categories = bw.setdefault("categories", [])
    if "Strength" not in categories and any(b.get("category") == "Strength" for b in biomarkers):
        categories.append("Strength")

    if "count" in bw:
        bw["count"] = len(biomarkers)


def tool_import_macrofactor(args):
    """Import a MacroFactor workout CSV export."""
    csv_data = args.get("csv_data", "")
    if not csv_data:
        return {"error": "csv_data is required"}

    data = _load_data(force=True)
    lifting = data["lifting"]
    existing_sessions = lifting.setdefault("sessions", [])

    # Build index of existing sessions by date+workout for merging
    session_index = {}
    for s in existing_sessions:
        key = (s["date"], s.get("workout_name", ""))
        session_index[key] = s

    # Build set of existing set IDs for dedup
    existing_set_ids = set()
    for s in existing_sessions:
        for ex in s.get("exercises", []):
            for st in ex.get("sets", []):
                if "id" in st:
                    existing_set_ids.add(st["id"])

    reader = csv.DictReader(io.StringIO(csv_data))
    imported_sets = 0
    skipped_duplicate = 0
    session_dates = set()

    for row in reader:
        date = (row.get("Date") or "").strip()
        if not date:
            continue

        workout_name = (row.get("Workout") or "").strip()
        duration_str = (row.get("Workout Duration") or "").strip()
        exercise_name = (row.get("Exercise") or "").strip()
        if not exercise_name:
            continue

        weight_str = (row.get("Weight (kg)") or "").strip()
        reps_str = (row.get("Reps") or "").strip()
        rir_str = (row.get("RIR") or "").strip()
        set_type = (row.get("Set Type") or "").strip()
        base_weight_str = (row.get("Exercise Base Weight (kg)") or "").strip()
        duration_set_str = (row.get("Duration") or "").strip()
        distance_short_str = (row.get("Distance short (Yd)") or "").strip()
        distance_long_str = (row.get("Distance long (Mi)") or "").strip()

        weight_kg = float(weight_str) if weight_str else None
        reps = float(reps_str) if reps_str else None
        rir = int(float(rir_str)) if rir_str else None
        base_weight_kg = float(base_weight_str) if base_weight_str else None
        duration_set = float(duration_set_str) if duration_set_str else None
        distance_short = float(distance_short_str) if distance_short_str else None
        distance_long = float(distance_long_str) if distance_long_str else None

        # Deterministic set ID
        id_key = f"{date}|{exercise_name}|{set_type}|{weight_str}|{reps_str}|{rir_str}|{duration_set_str}|{distance_short_str}|{distance_long_str}"
        set_id = "s_" + hashlib.sha1(id_key.encode()).hexdigest()[:12]

        if set_id in existing_set_ids:
            skipped_duplicate += 1
            continue
        existing_set_ids.add(set_id)

        # Get or create session
        session_key = (date, workout_name)
        if session_key not in session_index:
            session = {
                "date": date,
                "workout_name": workout_name,
                "duration_seconds": int(float(duration_str)) if duration_str else None,
                "exercises": [],
            }
            existing_sessions.append(session)
            session_index[session_key] = session

        session = session_index[session_key]

        if not session.get("duration_seconds") and duration_str:
            session["duration_seconds"] = int(float(duration_str))

        # Get or create exercise within session
        exercise = None
        for ex in session["exercises"]:
            if ex["name"] == exercise_name:
                exercise = ex
                break
        if exercise is None:
            exercise = {
                "name": exercise_name,
                "base_weight_kg": base_weight_kg,
                "sets": [],
            }
            session["exercises"].append(exercise)

        set_entry = {
            "id": set_id,
            "set_type": set_type or "Standard Set",
            "weight_kg": weight_kg,
            "reps": reps,
        }
        if rir is not None:
            set_entry["rir"] = rir
        if duration_set is not None:
            set_entry["duration"] = duration_set
        if distance_short is not None:
            set_entry["distance_short_yd"] = distance_short
        if distance_long is not None:
            set_entry["distance_long_mi"] = distance_long

        exercise["sets"].append(set_entry)
        imported_sets += 1
        session_dates.add(date)

    existing_sessions.sort(key=lambda s: s["date"])

    lifting["version"] = 1
    lifting["imported_at"] = datetime.utcnow().isoformat() + "Z"

    # Recompute all lifting biomarkers
    _inject_lifting_biomarkers(data)

    # Snapshot before writing
    lifting_snapshot = json.loads(json.dumps(lifting))
    bw_snapshot = json.loads(json.dumps(data["bloodwork"]))

    s3 = _s3_client()
    s3.put_object(
        Bucket=S3_BUCKET, Key=LIFTING_KEY,
        Body=json.dumps(lifting_snapshot, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    s3.put_object(
        Bucket=S3_BUCKET, Key=BLOODWORK_KEY,
        Body=json.dumps(bw_snapshot, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    _cache.clear()
    _invalidate_cloudfront(BLOODWORK_KEY)

    all_exercises = set()
    for s in existing_sessions:
        for ex in s.get("exercises", []):
            all_exercises.add(ex["name"])

    return {
        "status": "ok",
        "imported_sets": imported_sets,
        "skipped_duplicate": skipped_duplicate,
        "session_count": len(existing_sessions),
        "session_dates_affected": len(session_dates),
        "exercise_count": len(all_exercises),
        "exercises": sorted(all_exercises),
    }


def tool_get_lifting(args):
    """Query lifting sessions with optional exercise and date filters."""
    data = _load_data()
    lifting = data.get("lifting", {})
    sessions = lifting.get("sessions", [])

    all_exercises = set()
    for s in sessions:
        for ex in s.get("exercises", []):
            all_exercises.add(ex["name"])

    exercise = args.get("exercise")
    start_date = args.get("start_date")
    end_date = args.get("end_date")
    exercise_lower = exercise.lower().strip() if exercise else None

    results = []
    for s in sessions:
        d = s.get("date", "")
        if start_date and d < start_date:
            continue
        if end_date and d > end_date:
            continue

        if exercise_lower:
            matched_exercises = [
                ex for ex in s.get("exercises", [])
                if exercise_lower in ex["name"].lower()
            ]
            if not matched_exercises:
                continue
            results.append({
                "date": s["date"],
                "workout_name": s.get("workout_name"),
                "duration_seconds": s.get("duration_seconds"),
                "exercises": matched_exercises,
            })
        else:
            results.append(s)

    results.sort(key=lambda s: s.get("date", ""))

    return {
        "count": len(results),
        "sessions": results,
        "all_exercises": sorted(all_exercises),
    }


def tool_get_coaching_brief(args):
    """Primary entry point for new coaching chats."""
    from datetime import timedelta
    data = _load_data(force=True)
    coaching = data["coaching"]
    bw = data["bloodwork"]

    cutoff_90 = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d")
    cutoff_30 = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")

    # Active goals with latest progress note
    active_goals = []
    for g in coaching.get("goals", []):
        if g.get("status") == "active":
            entry = {**g}
            notes = g.get("progress_notes", [])
            entry["progress_notes"] = notes[-1:] if notes else []
            active_goals.append(entry)

    achieved_goals = [
        g for g in coaching.get("goals", [])
        if g.get("status") == "achieved" and g.get("updated_at", "") >= cutoff_90
    ]

    pending_actions = [
        a for a in coaching.get("action_items", [])
        if a.get("status") == "pending"
    ]
    recent_done_actions = [
        a for a in coaching.get("action_items", [])
        if a.get("status") in ("done", "skipped")
        and a.get("completed_at", "") >= cutoff_30
    ]

    all_notes = sorted(coaching.get("notes", []), key=lambda n: n.get("date", ""), reverse=True)
    recent_notes = all_notes[:10]

    overview = bw.get("overview", {})
    severity_order = {"red": 0, "amber": 1}
    flagged = []
    for b in bw.get("biomarkers", []):
        s = b.get("latest_status")
        if s in severity_order:
            stats = b.get("stats") or {}
            flagged.append({
                "name": b["name"],
                "status": s,
                "latest_value": stats.get("latest_value"),
                "unit": (b.get("units") or [None])[0],
            })
    flagged.sort(key=lambda b: (severity_order.get(b["status"], 99), b["name"]))

    events = data["events"].get("events", [])
    events_sorted = sorted(events, key=lambda e: e.get("date", ""), reverse=True)

    weight_m = _get_latest_measurement(bw, "Weight")
    bf_m = _get_latest_measurement(bw, "Body Fat %")

    return {
        "active_goals": active_goals,
        "achieved_goals_90d": achieved_goals,
        "pending_action_items": pending_actions,
        "recent_completed_actions_30d": recent_done_actions,
        "recent_coaching_notes": recent_notes,
        "health_snapshot": {
            "headline": overview.get("headline"),
            "flagged_biomarkers": flagged,
            "recent_events": events_sorted[:10],
            "latest_weight": {"value": weight_m["value"], "date": weight_m["date"], "unit": weight_m.get("unit")} if weight_m else None,
            "latest_body_fat": {"value": bf_m["value"], "date": bf_m["date"], "unit": bf_m.get("unit")} if bf_m else None,
            "recommendations": overview.get("recommendations", []),
        },
    }


def tool_add_goal(args):
    """Create a new coaching goal."""
    data = _load_data(force=True)
    coaching = data["coaching"]
    now = datetime.utcnow().isoformat() + "Z"

    goal = {
        "id": "g_" + secrets.token_hex(6),
        "title": args["title"],
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "progress_notes": [],
    }
    if args.get("category"):
        goal["category"] = args["category"]
    if args.get("target_value") is not None:
        goal["target_value"] = args["target_value"]
    if args.get("target_unit"):
        goal["target_unit"] = args["target_unit"]
    if args.get("target_date"):
        goal["target_date"] = args["target_date"]
    if args.get("linked_biomarker"):
        goal["linked_biomarker"] = args["linked_biomarker"]

    coaching.setdefault("goals", []).append(goal)
    _write_s3(COACHING_KEY, coaching)
    return {"status": "created", "goal": goal}


def tool_update_goal(args):
    """Update a coaching goal and/or append a progress note."""
    data = _load_data(force=True)
    coaching = data["coaching"]
    now = datetime.utcnow().isoformat() + "Z"
    today = datetime.utcnow().strftime("%Y-%m-%d")

    goal = None
    for g in coaching.get("goals", []):
        if g.get("id") == args["goal_id"]:
            goal = g
            break
    if not goal:
        return {"error": f"Goal not found: {args['goal_id']}"}

    if args.get("status"):
        goal["status"] = args["status"]
    if args.get("title"):
        goal["title"] = args["title"]
    if args.get("target_value") is not None:
        goal["target_value"] = args["target_value"]
    if args.get("target_unit"):
        goal["target_unit"] = args["target_unit"]
    if args.get("target_date"):
        goal["target_date"] = args["target_date"]
    if args.get("linked_biomarker"):
        goal["linked_biomarker"] = args["linked_biomarker"]
    if args.get("progress_note"):
        goal.setdefault("progress_notes", []).append({"date": today, "note": args["progress_note"]})

    goal["updated_at"] = now
    _write_s3(COACHING_KEY, coaching)
    return {"status": "updated", "goal": goal}


def tool_add_coaching_note(args):
    """Add a free-form coaching note."""
    data = _load_data(force=True)
    coaching = data["coaching"]
    now = datetime.utcnow().isoformat() + "Z"
    today = args.get("date") or datetime.utcnow().strftime("%Y-%m-%d")
    tags_str = args.get("tags", "")

    note = {
        "id": "n_" + secrets.token_hex(6),
        "date": today,
        "text": args["text"],
        "tags": [t.strip() for t in tags_str.split(",")] if tags_str else [],
        "created_at": now,
    }

    coaching.setdefault("notes", []).append(note)
    _write_s3(COACHING_KEY, coaching)
    return {"status": "added", "note": note}


def tool_add_action_item(args):
    """Create a coaching action item."""
    data = _load_data(force=True)
    coaching = data["coaching"]
    now = datetime.utcnow().isoformat() + "Z"

    item = {
        "id": "a_" + secrets.token_hex(6),
        "title": args["title"],
        "status": "pending",
        "created_at": now,
        "completed_at": None,
    }
    if args.get("due_date"):
        item["due_date"] = args["due_date"]
    if args.get("goal_id"):
        item["goal_id"] = args["goal_id"]

    coaching.setdefault("action_items", []).append(item)
    _write_s3(COACHING_KEY, coaching)
    return {"status": "created", "action_item": item}


def tool_update_action_item(args):
    """Update a coaching action item."""
    data = _load_data(force=True)
    coaching = data["coaching"]
    now = datetime.utcnow().isoformat() + "Z"

    item = None
    for a in coaching.get("action_items", []):
        if a.get("id") == args["action_id"]:
            item = a
            break
    if not item:
        return {"error": f"Action item not found: {args['action_id']}"}

    if args.get("status"):
        item["status"] = args["status"]
        if args["status"] in ("done", "skipped"):
            item["completed_at"] = now
    if args.get("title"):
        item["title"] = args["title"]
    if args.get("due_date"):
        item["due_date"] = args["due_date"]

    _write_s3(COACHING_KEY, coaching)
    return {"status": "updated", "action_item": item}


def tool_update_overview(args):
    """Rewrite the AI health overview."""
    try:
        cats = json.loads(args["categories"])
    except (json.JSONDecodeError, TypeError) as e:
        return {"error": f"Invalid categories JSON: {e}"}
    try:
        recs = json.loads(args["recommendations"])
    except (json.JSONDecodeError, TypeError) as e:
        return {"error": f"Invalid recommendations JSON: {e}"}

    data = _load_data(force=True)
    bw = data["bloodwork"]

    bw.setdefault("overview", {})["headline"] = args["headline"]
    bw["overview"]["categories"] = cats
    bw["overview"]["recommendations"] = recs
    bw["overview"]["updated_at"] = datetime.utcnow().isoformat() + "Z"

    _write_s3(BLOODWORK_KEY, bw)
    return {
        "status": "updated",
        "headline": args["headline"],
        "category_count": len(cats),
        "recommendation_count": len(recs),
    }


# ── Phases tool handlers ────────────────────────────────────────────────────


def tool_get_phases(args):
    """List all training phases, optionally filtered by status."""
    data = _load_data()
    phases_data = data["phases"]
    phases = phases_data.get("phases", [])
    status = args.get("status")
    if status:
        phases = [p for p in phases if p.get("status") == status]
    phases.sort(key=lambda p: p.get("start_date", ""), reverse=True)
    return {"count": len(phases), "phases": phases}


def tool_get_phase_detail(args):
    """Get full detail for a single phase by ID."""
    data = _load_data()
    phases_data = data["phases"]
    for p in phases_data.get("phases", []):
        if p.get("id") == args["phase_id"]:
            return p
    return {"error": f"Phase not found: {args['phase_id']}"}


def tool_add_phase(args):
    """Create a new training phase."""
    data = _load_data(force=True)
    phases_data = data["phases"]
    now = datetime.utcnow().isoformat() + "Z"

    phase = {
        "id": "ph_" + secrets.token_hex(6),
        "name": args["name"],
        "status": "active",
        "start_date": args["start_date"],
        "end_date": None,
        "created_at": now,
        "updated_at": now,
    }
    if args.get("target"):
        phase["target"] = args["target"]
    if args.get("notes"):
        phase["notes"] = args["notes"]

    # Parse supplements/medications from JSON strings
    if args.get("supplements"):
        try:
            phase["supplements"] = json.loads(args["supplements"]) if isinstance(args["supplements"], str) else args["supplements"]
        except (json.JSONDecodeError, TypeError):
            phase["supplements"] = []
    else:
        phase["supplements"] = []

    if args.get("medications"):
        try:
            phase["medications"] = json.loads(args["medications"]) if isinstance(args["medications"], str) else args["medications"]
        except (json.JSONDecodeError, TypeError):
            phase["medications"] = []
    else:
        phase["medications"] = []

    phases_data.setdefault("phases", []).append(phase)
    _write_s3(PHASES_KEY, phases_data)
    return {"status": "created", "phase": phase}


def tool_update_phase(args):
    """Update any field on a training phase."""
    data = _load_data(force=True)
    phases_data = data["phases"]
    now = datetime.utcnow().isoformat() + "Z"

    phase = None
    for p in phases_data.get("phases", []):
        if p.get("id") == args["phase_id"]:
            phase = p
            break
    if not phase:
        return {"error": f"Phase not found: {args['phase_id']}"}

    if args.get("name"):
        phase["name"] = args["name"]
    if args.get("status"):
        phase["status"] = args["status"]
    if args.get("end_date"):
        phase["end_date"] = args["end_date"]
    if args.get("target"):
        phase["target"] = args["target"]
    if "notes" in args:
        phase["notes"] = args["notes"]

    if args.get("supplements"):
        try:
            phase["supplements"] = json.loads(args["supplements"]) if isinstance(args["supplements"], str) else args["supplements"]
        except (json.JSONDecodeError, TypeError):
            pass
    if args.get("medications"):
        try:
            phase["medications"] = json.loads(args["medications"]) if isinstance(args["medications"], str) else args["medications"]
        except (json.JSONDecodeError, TypeError):
            pass

    phase["updated_at"] = now
    _write_s3(PHASES_KEY, phases_data)
    return {"status": "updated", "phase": phase}


def tool_toggle_checklist(args):
    """Toggle a checklist item for today (or a given date). Returns updated checked list."""
    data = _load_data(force=True)
    phases_data = data["phases"]
    item_id = args.get("item_id", "")
    date = args.get("date") or datetime.utcnow().strftime("%Y-%m-%d")

    if not item_id:
        return {"error": "item_id is required"}

    checklist = phases_data.setdefault("checklist", {})
    day_list = checklist.setdefault(date, [])

    if item_id in day_list:
        day_list.remove(item_id)
        action = "unchecked"
    else:
        day_list.append(item_id)
        action = "checked"

    # Prune old dates (keep last 7 days)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    for old_date in list(checklist.keys()):
        if old_date < today[:8] + "01":  # rough month-ago prune
            del checklist[old_date]

    _write_s3(PHASES_KEY, phases_data)
    return {"status": action, "item_id": item_id, "date": date, "checked": day_list}


def tool_get_checklist(args):
    """Get today's (or a given date's) checked items."""
    data = _load_data()
    phases_data = data["phases"]
    date = args.get("date") or datetime.utcnow().strftime("%Y-%m-%d")
    checklist = phases_data.get("checklist", {})
    return {"date": date, "checked": checklist.get(date, [])}


# ── Analytics tool implementations ───────────────────────────────────────────


def tool_get_rolling_averages(args):
    """Compute rolling averages for a biomarker."""
    data = _load_data()
    bw = data["bloodwork"]
    match = _fuzzy_match_biomarker(args["biomarker"], bw.get("biomarkers", []))
    if not match:
        return {"error": f"No biomarker matching '{args['biomarker']}'"}
    erroneous_ids = set(data["events"].get("erroneous_ids", []))
    window = args.get("window_days", 7)
    measurements = _get_measurements_for(
        bw, match["name"], erroneous_ids,
        args.get("start_date"), args.get("end_date"),
    )
    if not measurements:
        return {"biomarker": match["name"], "count": 0, "raw": [], "rolling_average": []}

    half_window = window / 2
    raw_points = []
    rolling_points = []
    for m in measurements:
        raw_points.append({"date": m["date"], "value": m["value"]})
        try:
            centre = datetime.fromisoformat(m["date"])
            window_vals = []
            for other in measurements:
                other_date = datetime.fromisoformat(other["date"])
                if abs((other_date - centre).days) <= half_window:
                    window_vals.append(other["value"])
            avg = round(mean(window_vals), 3) if window_vals else m["value"]
        except (ValueError, TypeError):
            avg = m["value"]
        rolling_points.append({"date": m["date"], "value": avg})

    return {
        "biomarker": match["name"],
        "unit": (match.get("units") or [None])[0],
        "window_days": window,
        "count": len(raw_points),
        "raw": raw_points,
        "rolling_average": rolling_points,
    }


def tool_detect_trends(args):
    """Detect biomarker trends and project threshold breaches."""
    data = _load_data()
    bw = data["bloodwork"]
    erroneous_ids = set(data["events"].get("erroneous_ids", []))
    lookback = args.get("lookback_days", 90)
    cutoff = (datetime.utcnow() - timedelta(days=lookback)).strftime("%Y-%m-%d")

    target_biomarkers = bw.get("biomarkers", [])
    if args.get("biomarker"):
        match = _fuzzy_match_biomarker(args["biomarker"], target_biomarkers)
        if not match:
            return {"error": f"No biomarker matching '{args['biomarker']}'"}
        target_biomarkers = [match]

    trends = []
    summary = {"critical": 0, "warning": 0, "watch": 0, "stable": 0}

    for b in target_biomarkers:
        ms = _get_measurements_for(bw, b["name"], erroneous_ids, start_date=cutoff)
        if len(ms) < 3:
            continue

        dates = [m["date"] for m in ms]
        values = [m["value"] for m in ms]
        reg = _linear_regression(dates, values)
        if not reg:
            continue

        slope = reg["slope"]
        current_value = values[-1]
        ref = b.get("reference")
        stats = _compute_biomarker_stats(bw, b["name"], erroneous_ids)
        current_status = _classify_value(ref, current_value) if ref else None

        direction = "stable"
        if abs(slope) > 1e-6:
            direction = "increasing" if slope > 0 else "decreasing"

        days_to_red = None
        days_to_amber = None
        projection = None
        alert_level = "stable"

        if ref and abs(slope) > 1e-6:
            green = ref.get("green")
            red_low = ref.get("red_low")
            red_high = ref.get("red_high")

            # Project to red threshold
            if red_high is not None and slope > 0 and current_value < red_high:
                days_to_red = int((red_high - current_value) / slope)
            elif red_low is not None and slope < 0 and current_value > red_low:
                days_to_red = int((current_value - red_low) / abs(slope))

            # Project to amber/green boundary
            if green and len(green) == 2:
                if slope > 0 and current_value < green[1] and current_status == "green":
                    days_to_amber = int((green[1] - current_value) / slope)
                elif slope < 0 and current_value > green[0] and current_status == "green":
                    days_to_amber = int((current_value - green[0]) / abs(slope))

            # Assign alert level
            if days_to_red is not None and 0 < days_to_red <= 30:
                alert_level = "critical"
            elif days_to_red is not None and 0 < days_to_red <= 90:
                alert_level = "warning"
            elif days_to_amber is not None and 0 < days_to_amber <= 90:
                alert_level = "warning"
            elif (days_to_red and 0 < days_to_red <= 365) or (days_to_amber and 0 < days_to_amber <= 365):
                alert_level = "watch"

            if days_to_red and 0 < days_to_red <= 365:
                projection = f"At current rate, may reach red threshold in ~{days_to_red} days."
            elif days_to_amber and 0 < days_to_amber <= 365:
                projection = f"At current rate, may leave optimal range in ~{days_to_amber} days."

        summary[alert_level] = summary.get(alert_level, 0) + 1
        if alert_level != "stable" or args.get("biomarker"):
            trends.append({
                "biomarker": b["name"],
                "unit": (b.get("units") or [None])[0],
                "current_value": round(current_value, 3),
                "current_status": current_status,
                "slope_per_day": reg["slope"],
                "direction": direction,
                "r_squared": reg["r_squared"],
                "n": len(ms),
                "alert_level": alert_level,
                "days_to_red": days_to_red if (days_to_red and 0 < days_to_red <= 365) else None,
                "days_to_amber": days_to_amber if (days_to_amber and 0 < days_to_amber <= 365) else None,
                "projection": projection,
            })

    # Sort: critical first, then warning, then watch
    level_order = {"critical": 0, "warning": 1, "watch": 2, "stable": 3}
    trends.sort(key=lambda t: (level_order.get(t["alert_level"], 9), t["biomarker"]))

    return {"lookback_days": lookback, "trends": trends, "summary": summary}


def tool_compute_correlation(args):
    """Compute cross-biomarker correlation with lag analysis."""
    data = _load_data()
    bw = data["bloodwork"]
    biomarkers = bw.get("biomarkers", [])
    erroneous_ids = set(data["events"].get("erroneous_ids", []))

    match_a = _fuzzy_match_biomarker(args["biomarker_a"], biomarkers)
    match_b = _fuzzy_match_biomarker(args["biomarker_b"], biomarkers)
    if not match_a:
        return {"error": f"No biomarker matching '{args['biomarker_a']}'"}
    if not match_b:
        return {"error": f"No biomarker matching '{args['biomarker_b']}'"}

    max_gap = args.get("max_gap_days", 90)
    ms_a = _get_measurements_for(bw, match_a["name"], erroneous_ids,
                                  args.get("start_date"), args.get("end_date"))
    ms_b = _get_measurements_for(bw, match_b["name"], erroneous_ids,
                                  args.get("start_date"), args.get("end_date"))

    series_a = [{"date": m["date"], "value": m["value"]} for m in ms_a]
    series_b = [{"date": m["date"], "value": m["value"]} for m in ms_b]

    aligned_a, aligned_b, dates = _align_time_series(series_a, series_b, max_gap)

    if len(aligned_a) < 3:
        return {
            "biomarker_a": match_a["name"], "biomarker_b": match_b["name"],
            "n_aligned": len(aligned_a),
            "error": f"Insufficient overlapping data ({len(aligned_a)} points, need >= 3).",
        }

    pearson = _pearson_r(aligned_a, aligned_b)
    spearman = _spearman_r(aligned_a, aligned_b)

    # Lag analysis: shift series_b by -90..+90 days in 7-day steps
    best_lag = 0
    best_r = abs(pearson["r"])
    for lag in range(-90, 91, 7):
        if lag == 0:
            continue
        shifted_b = [{"date": m["date"], "value": m["value"]} for m in ms_b]
        for pt in shifted_b:
            try:
                d = datetime.fromisoformat(pt["date"]) + timedelta(days=lag)
                pt["date"] = d.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass
        la, lb, _ = _align_time_series(series_a, shifted_b, max_gap)
        if len(la) >= 3:
            r_val = abs(_pearson_r(la, lb)["r"])
            if r_val > best_r:
                best_r = r_val
                best_lag = lag

    lag_direction = "synchronous"
    if best_lag > 0:
        lag_direction = f"{match_a['name']} leads {match_b['name']}"
    elif best_lag < 0:
        lag_direction = f"{match_b['name']} leads {match_a['name']}"

    # Generate interpretation
    r = pearson["r"]
    strength = "weak" if abs(r) < 0.3 else "moderate" if abs(r) < 0.7 else "strong"
    sign = "positive" if r > 0 else "negative"
    sig = " (statistically significant)" if pearson["p"] < 0.05 else " (not statistically significant)"
    interp = f"{strength.capitalize()} {sign} correlation (r={r}, p={pearson['p']}){sig}."
    if best_lag != 0:
        interp += f" Optimal lag: {abs(best_lag)} days ({lag_direction})."

    return {
        "biomarker_a": match_a["name"],
        "biomarker_b": match_b["name"],
        "n_aligned": len(aligned_a),
        "pearson": {"r": pearson["r"], "p": pearson["p"]},
        "spearman": {"rho": spearman["rho"], "p": spearman["p"]},
        "lag_analysis": {
            "optimal_lag_days": abs(best_lag),
            "max_correlation": round(best_r, 4),
            "direction": lag_direction,
        },
        "interpretation": interp,
    }


def tool_analyse_event_impact(args):
    """Analyse before/after impact of a health event on biomarkers."""
    data = _load_data()
    bw = data["bloodwork"]
    events = data["events"].get("events", [])
    erroneous_ids = set(data["events"].get("erroneous_ids", []))

    event_id = args["event_id"]
    event = next((e for e in events if e.get("id") == event_id), None)
    if not event:
        return {"error": f"Event '{event_id}' not found."}

    event_date = event["date"]
    window = args.get("window_days", 60)
    before_start = (datetime.fromisoformat(event_date) - timedelta(days=window)).strftime("%Y-%m-%d")
    after_end = (datetime.fromisoformat(event_date) + timedelta(days=window)).strftime("%Y-%m-%d")

    # Determine which biomarkers to analyse
    requested = args.get("biomarkers")
    biomarker_names = []
    if requested:
        for name in requested:
            match = _fuzzy_match_biomarker(name, bw.get("biomarkers", []))
            if match:
                biomarker_names.append(match["name"])
    else:
        # Auto-select biomarkers with data in both periods
        for b in bw.get("biomarkers", []):
            before = _get_measurements_for(bw, b["name"], erroneous_ids, before_start, event_date)
            after = _get_measurements_for(bw, b["name"], erroneous_ids, event_date, after_end)
            # Exclude the event date itself from after
            after = [m for m in after if m["date"] > event_date]
            if len(before) >= 2 and len(after) >= 2:
                biomarker_names.append(b["name"])

    impacts = []
    for bname in biomarker_names:
        before_ms = _get_measurements_for(bw, bname, erroneous_ids, before_start, event_date)
        before_ms = [m for m in before_ms if m["date"] < event_date]
        after_ms = _get_measurements_for(bw, bname, erroneous_ids, event_date, after_end)
        after_ms = [m for m in after_ms if m["date"] > event_date]

        if not before_ms or not after_ms:
            continue

        before_vals = [m["value"] for m in before_ms]
        after_vals = [m["value"] for m in after_ms]
        before_mean = round(mean(before_vals), 3)
        after_mean = round(mean(after_vals), 3)
        change_pct = round((after_mean - before_mean) / before_mean * 100, 1) if before_mean else None

        before_reg = _linear_regression([m["date"] for m in before_ms], before_vals)
        after_reg = _linear_regression([m["date"] for m in after_ms], after_vals)

        # Significance: after mean outside 1 SD of before values
        likely_sig = False
        if len(before_vals) >= 3:
            try:
                sd = stdev(before_vals)
                likely_sig = abs(after_mean - before_mean) > sd
            except Exception:
                pass

        direction = "unchanged"
        if change_pct is not None:
            if change_pct > 2:
                direction = "increased"
            elif change_pct < -2:
                direction = "decreased"

        bio = _fuzzy_match_biomarker(bname, bw.get("biomarkers", []))
        impacts.append({
            "biomarker": bname,
            "unit": (bio.get("units") or [None])[0] if bio else None,
            "before": {
                "mean": before_mean,
                "median": round(median(before_vals), 3),
                "n": len(before_vals),
                "trend_slope": before_reg["slope"] if before_reg else None,
            },
            "after": {
                "mean": after_mean,
                "median": round(median(after_vals), 3),
                "n": len(after_vals),
                "trend_slope": after_reg["slope"] if after_reg else None,
            },
            "change_pct": change_pct,
            "direction": direction,
            "likely_significant": likely_sig,
        })

    # Sort by absolute change
    impacts.sort(key=lambda x: abs(x.get("change_pct") or 0), reverse=True)

    return {
        "event": {"id": event["id"], "date": event["date"],
                  "type": event.get("type"), "title": event.get("title")},
        "window_days": window,
        "biomarker_impacts": impacts,
    }


def tool_get_health_scores(args):
    """Compute composite health scores per category."""
    data = _load_data()
    bw = data["bloodwork"]
    erroneous_ids = set(data["events"].get("erroneous_ids", []))
    filter_cat = args.get("category")

    categories = {}
    for b in bw.get("biomarkers", []):
        cat = b.get("category", "Uncategorised")
        if filter_cat and cat.lower() != filter_cat.lower():
            continue
        ref = b.get("reference")
        if not ref:
            continue
        stats = _compute_biomarker_stats(bw, b["name"], erroneous_ids)
        val = stats.get("latest_value")
        if val is None:
            continue
        score = _compute_biomarker_score(ref, val)
        if score is None:
            continue
        status = _classify_value(ref, val)
        if cat not in categories:
            categories[cat] = []
        categories[cat].append({
            "biomarker": b["name"],
            "score": score,
            "status": status,
            "value": round(val, 3),
            "unit": (b.get("units") or [None])[0],
        })

    def grade(s):
        if s >= 90: return "A"
        if s >= 75: return "B"
        if s >= 60: return "C"
        if s >= 40: return "D"
        return "F"

    cat_results = []
    total_score = 0
    total_count = 0
    for cat, items in sorted(categories.items()):
        scores = [i["score"] for i in items]
        cat_score = round(mean(scores), 1)
        cat_results.append({
            "category": cat,
            "score": cat_score,
            "grade": grade(cat_score),
            "biomarker_count": len(items),
            "biomarker_scores": sorted(items, key=lambda x: x["score"]),
        })
        total_score += cat_score * len(items)
        total_count += len(items)

    overall = round(total_score / total_count, 1) if total_count > 0 else 0

    return {
        "overall_score": overall,
        "overall_grade": grade(overall),
        "categories": sorted(cat_results, key=lambda c: c["score"]),
    }


def tool_get_goal_progress(args):
    """Compute progress tracking for coaching goals linked to biomarker data."""
    data = _load_data()
    bw = data["bloodwork"]
    coaching = data["coaching"]
    erroneous_ids = set(data["events"].get("erroneous_ids", []))
    biomarkers = bw.get("biomarkers", [])

    goals = coaching.get("goals", [])
    if args.get("goal_id"):
        goals = [g for g in goals if g["id"] == args["goal_id"]]
    else:
        goals = [g for g in goals if g.get("status") == "active" and g.get("target_value") is not None]

    results = []
    for g in goals:
        target_val = g.get("target_value")
        if target_val is None:
            continue

        # Find linked biomarker
        linked = g.get("linked_biomarker")
        matched_bio = None
        if linked:
            matched_bio = _fuzzy_match_biomarker(linked, biomarkers)

        if not matched_bio:
            # Auto-detect: match by unit and category
            target_unit = g.get("target_unit", "").lower()
            goal_cat = g.get("category", "").lower()
            goal_title = g.get("title", "").lower()
            candidates = []
            for b in biomarkers:
                units = [u.lower() for u in (b.get("units") or [])]
                ref_unit = (b.get("reference") or {}).get("unit", "").lower()
                b_cat = b.get("category", "").lower()
                score = 0
                if target_unit and (target_unit in units or target_unit == ref_unit):
                    score += 2
                if goal_cat and goal_cat == b_cat:
                    score += 1
                # Fuzzy match goal title to biomarker name
                name_ratio = SequenceMatcher(None, goal_title, b["name"].lower()).ratio()
                if name_ratio > 0.5:
                    score += name_ratio * 2
                if score > 0:
                    candidates.append((score, b))
            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                matched_bio = candidates[0][1]

        if not matched_bio:
            continue

        ms = _get_measurements_for(bw, matched_bio["name"], erroneous_ids)
        if not ms:
            continue

        # Start value: closest to goal creation date
        created = g.get("created_at", "")[:10]
        start_value = ms[0]["value"]
        if created:
            closest = min(ms, key=lambda m: abs(
                (datetime.fromisoformat(m["date"]) - datetime.fromisoformat(created[:10])).days
            ) if m["date"] >= "2000" else 9999)
            start_value = closest["value"]

        current_value = ms[-1]["value"]
        total_change = target_val - start_value
        current_change = current_value - start_value
        progress_pct = round(current_change / total_change * 100, 1) if abs(total_change) > 1e-6 else 100

        # Rate: linear regression on last 30 days
        recent_cutoff = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
        recent_ms = [m for m in ms if m["date"] >= recent_cutoff]
        rate = None
        projected = None
        status_vs = None

        if len(recent_ms) >= 2:
            reg = _linear_regression([m["date"] for m in recent_ms], [m["value"] for m in recent_ms])
            if reg and abs(reg["slope"]) > 1e-8:
                rate = reg["slope"]
                remaining = target_val - current_value
                days_needed = remaining / rate
                if days_needed > 0:
                    proj_date = datetime.utcnow() + timedelta(days=days_needed)
                    projected = proj_date.strftime("%Y-%m-%d")

                    if g.get("target_date"):
                        target_d = datetime.fromisoformat(g["target_date"])
                        if proj_date <= target_d:
                            status_vs = "ahead_of_schedule"
                        elif (proj_date - target_d).days <= 14:
                            status_vs = "on_track"
                        else:
                            status_vs = "behind_schedule"

        days_remaining = None
        if g.get("target_date"):
            try:
                days_remaining = (datetime.fromisoformat(g["target_date"]) - datetime.utcnow()).days
            except (ValueError, TypeError):
                pass

        results.append({
            "goal_id": g["id"],
            "title": g.get("title"),
            "linked_biomarker": matched_bio["name"],
            "start_value": round(start_value, 2),
            "current_value": round(current_value, 2),
            "target_value": target_val,
            "progress_pct": max(0, min(100, progress_pct)),
            "rate_per_day": round(rate, 4) if rate else None,
            "target_date": g.get("target_date"),
            "projected_completion": projected,
            "status_vs_schedule": status_vs,
            "days_remaining": days_remaining,
        })

    return {"goals": results}


def tool_generate_insights(args):
    """Generate AI-powered insights for biomarkers using Claude API."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY not configured on Lambda."}

    data = _load_data(force=True)
    bw = data["bloodwork"]
    erroneous_ids = set(data["events"].get("erroneous_ids", []))
    biomarkers = bw.get("biomarkers", [])
    include_ctx = args.get("include_context", True)

    # Determine targets
    targets = []
    if args.get("biomarker"):
        match = _fuzzy_match_biomarker(args["biomarker"], biomarkers)
        if not match:
            return {"error": f"No biomarker matching '{args['biomarker']}'"}
        targets = [match]
    else:
        targets = [b for b in biomarkers if b.get("latest_status") in ("red", "amber")]

    if not targets:
        return {"status": "no_targets", "message": "No flagged biomarkers to generate insights for."}

    generated = []
    for b in targets[:10]:  # Limit to 10 to avoid Lambda timeout
        stats = _compute_biomarker_stats(bw, b["name"], erroneous_ids)
        ref = b.get("reference")
        status = _classify_value(ref, stats.get("latest_value")) if ref else None

        # Recent trend
        cutoff_90 = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d")
        recent = _get_measurements_for(bw, b["name"], erroneous_ids, start_date=cutoff_90)
        trend_info = ""
        if len(recent) >= 3:
            reg = _linear_regression([m["date"] for m in recent], [m["value"] for m in recent])
            if reg:
                direction = "increasing" if reg["slope"] > 0 else "decreasing" if reg["slope"] < 0 else "stable"
                trend_info = f"Trend: {direction} at {reg['slope']:.4f}/day over {len(recent)} measurements (R²={reg['r_squared']})"

        # Build context
        context_parts = []
        if include_ctx:
            # Recent events
            events = data["events"].get("events", [])
            recent_events = [e for e in events if e.get("date", "") >= cutoff_90]
            if recent_events:
                context_parts.append("Recent events: " + "; ".join(
                    f"{e['date']}: {e.get('title', '')} ({e.get('type', '')})" for e in recent_events[-5:]
                ))
            # Active phase supplements
            phases = data["phases"].get("phases", [])
            active = [p for p in phases if p.get("status") == "active"]
            if active:
                supps = []
                for p in active:
                    for s in p.get("supplements", []):
                        supps.append(f"{s.get('name', '')} {s.get('dose', '')}")
                    for m in p.get("medications", []):
                        supps.append(f"{m.get('name', '')} {m.get('dose', '')}")
                if supps:
                    context_parts.append("Current supplements/medications: " + ", ".join(supps))
            # Goals
            coaching_goals = [g for g in data["coaching"].get("goals", []) if g.get("status") == "active"]
            if coaching_goals:
                context_parts.append("Active goals: " + "; ".join(g.get("title", "") for g in coaching_goals[:3]))

        ref_str = ""
        if ref:
            green = ref.get("green")
            if green and len(green) == 2:
                ref_str = f"Optimal: {green[0]}-{green[1]}"
            if ref.get("red_low") is not None:
                ref_str += f", Red below: {ref['red_low']}"
            if ref.get("red_high") is not None:
                ref_str += f", Red above: {ref['red_high']}"

        unit = (b.get("units") or [""])[0]
        prompt = f"""You are a health analytics assistant. Generate a concise insight for this biomarker.

Biomarker: {b['name']}
Category: {b.get('category', 'Unknown')}
Current value: {stats.get('latest_value')} {unit} (status: {status})
Reference range: {ref_str}
{trend_info}
History: {stats.get('first_value')} on {stats.get('first_date')} → {stats.get('latest_value')} on {stats.get('latest_date')} ({stats.get('pct_change', 'N/A')}% change over {stats.get('span_days', 0)} days)
{'Context: ' + '; '.join(context_parts) if context_parts else ''}

Provide a 2-3 sentence interpretation covering current status, trend, and one actionable recommendation. Keep under 100 words. Use British English."""

        try:
            import urllib.request
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps({
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 200,
                    "messages": [{"role": "user", "content": prompt}],
                }).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read())
                insight_text = body["content"][0]["text"]
        except Exception as e:
            insight_text = f"Insight generation failed: {str(e)}"

        # Update biomarker insight in data
        for bio in bw.get("biomarkers", []):
            if bio["name"] == b["name"]:
                bio["insight"] = insight_text
                break

        generated.append({"biomarker": b["name"], "insight": insight_text})

    # Persist
    _write_s3(BLOODWORK_KEY, bw)

    return {"status": "ok", "generated": generated}


# ── Tool dispatch table ─────────────────────────────────────────────────────

TOOL_DISPATCH = {
    "get_health_overview": tool_get_health_overview,
    "list_biomarkers": tool_list_biomarkers,
    "get_biomarker_detail": tool_get_biomarker_detail,
    "get_measurements": tool_get_measurements,
    "get_flagged_biomarkers": tool_get_flagged_biomarkers,
    "get_events": tool_get_events,
    "get_category_summary": tool_get_category_summary,
    "refresh_data": tool_refresh_data,
    "add_event": tool_add_event,
    "add_measurement": tool_add_measurement,
    "create_biomarker": tool_create_biomarker,
    "update_biomarker": tool_update_biomarker,
    "delete_biomarker": tool_delete_biomarker,
    "mark_erroneous": tool_mark_erroneous,
    "delete_measurement": tool_delete_measurement,
    "import_apple_health": tool_import_apple_health,
    "log_nutrition": tool_log_nutrition,
    "get_nutrition": tool_get_nutrition,
    "import_macrofactor": tool_import_macrofactor,
    "import_macrofactor_nutrition": tool_import_macrofactor_nutrition,
    "get_lifting": tool_get_lifting,
    "get_coaching_brief": tool_get_coaching_brief,
    "add_goal": tool_add_goal,
    "update_goal": tool_update_goal,
    "add_coaching_note": tool_add_coaching_note,
    "add_action_item": tool_add_action_item,
    "update_action_item": tool_update_action_item,
    "update_overview": tool_update_overview,
    "get_phases": tool_get_phases,
    "get_phase_detail": tool_get_phase_detail,
    "add_phase": tool_add_phase,
    "update_phase": tool_update_phase,
    "toggle_checklist": tool_toggle_checklist,
    "get_checklist": tool_get_checklist,
    "get_rolling_averages": tool_get_rolling_averages,
    "detect_trends": tool_detect_trends,
    "compute_correlation": tool_compute_correlation,
    "analyse_event_impact": tool_analyse_event_impact,
    "get_health_scores": tool_get_health_scores,
    "get_goal_progress": tool_get_goal_progress,
    "generate_insights": tool_generate_insights,
}

# ── Session tracking ─────────────────────────────────────────────────────────

_session_id = None


def _get_session_id():
    global _session_id
    if _session_id is None:
        _session_id = str(uuid.uuid4())
    return _session_id


# ── JSON-RPC helpers ─────────────────────────────────────────────────────────


def _jsonrpc_response(id, result):
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _jsonrpc_error(id, code, message):
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


def _http(status, body=None, extra_headers=None):
    headers = {"content-type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    resp = {"statusCode": status, "headers": headers}
    if body is not None:
        resp["body"] = json.dumps(body)
    else:
        resp["body"] = ""
    return resp


# ── MCP protocol handlers ───────────────────────────────────────────────────


def _handle_initialize(req_id, params):
    result = {
        "protocolVersion": "2025-03-26",
        "capabilities": {
            "tools": {"listChanged": True},
        },
        "serverInfo": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
        },
    }
    return _http(
        200,
        _jsonrpc_response(req_id, result),
        {"mcp-session-id": _get_session_id()},
    )


def _handle_tools_list(req_id, params):
    return _http(200, _jsonrpc_response(req_id, {"tools": TOOLS}))


def _handle_tools_call(req_id, params):
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})

    fn = TOOL_DISPATCH.get(tool_name)
    if not fn:
        return _http(200, _jsonrpc_error(req_id, -32601, f"Unknown tool: {tool_name}"))

    try:
        result = fn(arguments)
    except Exception as e:
        return _http(200, _jsonrpc_response(req_id, {
            "content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
            "isError": True,
        }))

    # MCP tools/call result wraps output in content array
    text = json.dumps(result, default=str)
    return _http(200, _jsonrpc_response(req_id, {
        "content": [{"type": "text", "text": text}],
    }))


# ── OAuth 2.0 endpoint handlers ─────────────────────────────────────────────


def _get_base_url(event):
    """Derive the public base URL from the request."""
    headers = event.get("headers", {})
    host = headers.get("host", headers.get("x-forwarded-host", ""))
    proto = headers.get("x-forwarded-proto", "https")
    if host:
        return f"{proto}://{host}"
    # Fallback: construct from Lambda Function URL domain
    domain = event.get("requestContext", {}).get("domainName", "")
    return f"https://{domain}" if domain else ""


def _handle_protected_resource_metadata(event):
    """GET /.well-known/oauth-protected-resource (RFC 9728)."""
    base = _get_base_url(event)
    return _http(200, {
        "resource": base,
        "authorization_servers": [base],
    })


def _handle_authorization_server_metadata(event):
    """GET /.well-known/oauth-authorization-server (RFC 8414)."""
    base = _get_base_url(event)
    return _http(200, {
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "registration_endpoint": f"{base}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
    })


def _handle_register(event):
    """POST /register — Dynamic Client Registration (RFC 7591)."""
    try:
        body = json.loads(event.get("body", "{}"))
    except (json.JSONDecodeError, TypeError):
        return _http(400, {"error": "invalid_request", "error_description": "Invalid JSON body"})

    client_id = secrets.token_hex(16)
    redirect_uris = body.get("redirect_uris", [])
    client_name = body.get("client_name", "")

    _oauth_clients[client_id] = {
        "client_id": client_id,
        "redirect_uris": redirect_uris,
        "client_name": client_name,
    }

    return _http(201, {
        "client_id": client_id,
        "redirect_uris": redirect_uris,
        "client_name": client_name,
        "token_endpoint_auth_method": "none",
    })


def _handle_authorize_get(event):
    """GET /authorize — Render HTML consent page with PIN field."""
    qs = event.get("queryStringParameters") or {}
    client_id = qs.get("client_id", "")
    redirect_uri = qs.get("redirect_uri", "")
    code_challenge = qs.get("code_challenge", "")
    code_challenge_method = qs.get("code_challenge_method", "")
    state = qs.get("state", "")
    scope = qs.get("scope", "")

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Health Dashboard — Authorize</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; background: #0f172a; color: #e2e8f0;
         display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }}
  .card {{ background: #1e293b; border-radius: 12px; padding: 2rem; max-width: 380px; width: 90%;
           box-shadow: 0 4px 24px rgba(0,0,0,0.3); }}
  h1 {{ font-size: 1.25rem; margin: 0 0 0.5rem; }}
  p {{ color: #94a3b8; font-size: 0.9rem; margin: 0 0 1.5rem; }}
  label {{ display: block; font-size: 0.85rem; color: #94a3b8; margin-bottom: 0.5rem; }}
  input {{ width: 100%; padding: 0.75rem; border: 1px solid #334155; border-radius: 8px;
           background: #0f172a; color: #e2e8f0; font-size: 1.1rem; letter-spacing: 0.3em;
           text-align: center; box-sizing: border-box; }}
  input:focus {{ outline: none; border-color: #3b82f6; }}
  button {{ width: 100%; padding: 0.75rem; border: none; border-radius: 8px; background: #3b82f6;
            color: white; font-size: 1rem; font-weight: 600; cursor: pointer; margin-top: 1rem; }}
  button:hover {{ background: #2563eb; }}
  .error {{ color: #f87171; font-size: 0.85rem; margin-top: 0.5rem; display: none; }}
</style>
</head>
<body>
<div class="card">
  <h1>Health Dashboard</h1>
  <p>Enter your PIN to authorize access.</p>
  <form method="POST" action="/authorize">
    <input type="hidden" name="client_id" value="{client_id}">
    <input type="hidden" name="redirect_uri" value="{redirect_uri}">
    <input type="hidden" name="code_challenge" value="{code_challenge}">
    <input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
    <input type="hidden" name="state" value="{state}">
    <input type="hidden" name="scope" value="{scope}">
    <label for="pin">PIN</label>
    <input type="password" id="pin" name="pin" maxlength="10" autocomplete="off" inputmode="numeric" autofocus>
    <button type="submit">Approve</button>
  </form>
</div>
</body>
</html>"""
    return {
        "statusCode": 200,
        "headers": {"content-type": "text/html; charset=utf-8"},
        "body": html,
    }


def _handle_authorize_post(event):
    """POST /authorize — Validate PIN, issue auth code, redirect."""
    body_str = event.get("body", "")
    if event.get("isBase64Encoded"):
        body_str = base64.b64decode(body_str).decode("utf-8")

    params = dict(urllib.parse.parse_qsl(body_str))
    pin = params.get("pin", "")
    client_id = params.get("client_id", "")
    redirect_uri = params.get("redirect_uri", "")
    code_challenge = params.get("code_challenge", "")
    state = params.get("state", "")
    scope = params.get("scope", "")

    if not hmac.compare_digest(pin, AUTH_PIN):
        # Re-render the form with an error indicator
        html = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Health Dashboard — Authorize</title>
<style>
  body { font-family: -apple-system, system-ui, sans-serif; background: #0f172a; color: #e2e8f0;
         display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
  .card { background: #1e293b; border-radius: 12px; padding: 2rem; max-width: 380px; width: 90%;
           box-shadow: 0 4px 24px rgba(0,0,0,0.3); }
  h1 { font-size: 1.25rem; margin: 0 0 0.5rem; }
  p { color: #94a3b8; font-size: 0.9rem; margin: 0 0 1.5rem; }
  .error { color: #f87171; font-size: 0.9rem; margin: 0 0 1rem; }
  label { display: block; font-size: 0.85rem; color: #94a3b8; margin-bottom: 0.5rem; }
  input { width: 100%; padding: 0.75rem; border: 1px solid #334155; border-radius: 8px;
           background: #0f172a; color: #e2e8f0; font-size: 1.1rem; letter-spacing: 0.3em;
           text-align: center; box-sizing: border-box; }
  input:focus { outline: none; border-color: #3b82f6; }
  button { width: 100%; padding: 0.75rem; border: none; border-radius: 8px; background: #3b82f6;
            color: white; font-size: 1rem; font-weight: 600; cursor: pointer; margin-top: 1rem; }
  button:hover { background: #2563eb; }
</style>
</head>
<body>
<div class="card">
  <h1>Health Dashboard</h1>
  <p class="error">Incorrect PIN. Please try again.</p>
  <form method="POST" action="/authorize">"""
        # Re-inject hidden fields
        for key in ("client_id", "redirect_uri", "code_challenge", "code_challenge_method", "state", "scope"):
            val = params.get(key, "")
            html += f'\n    <input type="hidden" name="{key}" value="{val}">'
        html += """
    <label for="pin">PIN</label>
    <input type="password" id="pin" name="pin" maxlength="10" autocomplete="off" inputmode="numeric" autofocus>
    <button type="submit">Approve</button>
  </form>
</div>
</body>
</html>"""
        return {
            "statusCode": 200,
            "headers": {"content-type": "text/html; charset=utf-8"},
            "body": html,
        }

    # PIN correct — issue authorization code (expires in 5 minutes)
    code_claims = {
        "cid": client_id,
        "ruri": redirect_uri,
        "cc": code_challenge,
        "scope": scope,
        "exp": int(time.time()) + 300,
    }
    code = _sign_token(code_claims)

    # Build redirect URL
    redirect_params = {"code": code}
    if state:
        redirect_params["state"] = state
    location = redirect_uri + ("&" if "?" in redirect_uri else "?") + urllib.parse.urlencode(redirect_params)

    return {
        "statusCode": 302,
        "headers": {
            "location": location,
            "cache-control": "no-store",
        },
        "body": "",
    }


def _handle_token(event):
    """POST /token — Exchange auth code for access token (with PKCE)."""
    body_str = event.get("body", "")
    if event.get("isBase64Encoded"):
        body_str = base64.b64decode(body_str).decode("utf-8")

    content_type = (event.get("headers") or {}).get("content-type", "")
    if "application/json" in content_type:
        try:
            params = json.loads(body_str)
        except (json.JSONDecodeError, TypeError):
            return _http(400, {"error": "invalid_request", "error_description": "Invalid JSON"})
    else:
        params = dict(urllib.parse.parse_qsl(body_str))

    grant_type = params.get("grant_type", "")

    if grant_type == "refresh_token":
        refresh_token_str = params.get("refresh_token", "")
        if not refresh_token_str:
            return _http(400, {"error": "invalid_request", "error_description": "refresh_token required"})
        claims = _verify_token(refresh_token_str)
        if not claims or claims.get("type") != "refresh":
            return _http(400, {"error": "invalid_grant", "error_description": "Invalid or expired refresh token"})
        scope = claims.get("scope", "")
    elif grant_type == "authorization_code":
        code = params.get("code", "")
        code_verifier = params.get("code_verifier", "")

        # Verify authorization code
        claims = _verify_token(code)
        if not claims:
            return _http(400, {"error": "invalid_grant", "error_description": "Invalid or expired authorization code"})

        # Verify PKCE: base64url(sha256(code_verifier)) must match code_challenge
        if not code_verifier:
            return _http(400, {"error": "invalid_request", "error_description": "code_verifier required"})

        expected_challenge = _b64url_encode(hashlib.sha256(code_verifier.encode("ascii")).digest())
        if not hmac.compare_digest(expected_challenge, claims.get("cc", "")):
            return _http(400, {"error": "invalid_grant", "error_description": "PKCE verification failed"})
        scope = claims.get("scope", "")
    else:
        return _http(400, {"error": "unsupported_grant_type"})

    # Issue access token (30 days)
    token_ttl = 30 * 24 * 3600
    access_claims = {
        "sub": "owner",
        "scope": scope,
        "exp": int(time.time()) + token_ttl,
    }
    access_token = _sign_token(access_claims)

    # Issue refresh token (365 days)
    refresh_ttl = 365 * 24 * 3600
    refresh_claims = {
        "sub": "owner",
        "scope": scope,
        "type": "refresh",
        "exp": int(time.time()) + refresh_ttl,
    }
    refresh_token = _sign_token(refresh_claims)

    return _http(200, {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": token_ttl,
        "refresh_token": refresh_token,
    }, {"cache-control": "no-store"})


def _check_oauth_token(event):
    """Validate OAuth bearer token. Returns claims dict or None."""
    headers = event.get("headers", {})
    auth = headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    # Support legacy static bearer token for backwards compat
    if BEARER_TOKEN and hmac.compare_digest(token, BEARER_TOKEN):
        return {"sub": "owner", "scope": "", "legacy": True}
    return _verify_token(token)


# ── Lambda entry point ──────────────────────────────────────────────────────


def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")

    # OPTIONS preflight handled by Function URL CORS config
    if method == "OPTIONS":
        return {"statusCode": 204, "body": ""}

    # ── OAuth discovery endpoints (GET, no auth) ──

    if method == "GET" and path == "/.well-known/oauth-protected-resource":
        return _handle_protected_resource_metadata(event)

    if method == "GET" and path == "/.well-known/oauth-authorization-server":
        return _handle_authorization_server_metadata(event)

    # ── OAuth registration (POST, no auth) ──

    if method == "POST" and path == "/register":
        return _handle_register(event)

    # ── OAuth authorization (GET = consent page, POST = submit PIN) ──

    if path == "/authorize":
        if method == "GET":
            return _handle_authorize_get(event)
        if method == "POST":
            return _handle_authorize_post(event)
        return _http(405, {"error": "Method not allowed"})

    # ── OAuth token exchange (POST, no auth) ──

    if method == "POST" and path == "/token":
        return _handle_token(event)

    # ── Bulk import REST API (POST, requires auth) ──

    if method == "POST" and path == "/api/import/apple-health":
        token_claims = _check_oauth_token(event)
        if not token_claims:
            return _http(401, {"error": "Unauthorized"})
        body = event.get("body", "")
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        result = tool_import_apple_health({"records_json": body})
        return _http(200, result)

    if method == "POST" and path == "/api/import/macrofactor":
        token_claims = _check_oauth_token(event)
        if not token_claims:
            return _http(401, {"error": "Unauthorized"})
        body = event.get("body", "")
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        result = tool_import_macrofactor({"csv_data": body})
        return _http(200, result)

    if method == "POST" and path == "/api/import/macrofactor-nutrition":
        token_claims = _check_oauth_token(event)
        if not token_claims:
            return _http(401, {"error": "Unauthorized"})
        body = event.get("body", "")
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        result = tool_import_macrofactor_nutrition({"csv_data": body})
        return _http(200, result)

    # ── Checklist REST API (for web dashboard) ──

    if path == "/api/checklist":
        headers = event.get("headers", {})
        auth = headers.get("authorization", "")
        if auth != f"Bearer {BEARER_TOKEN}":
            return _http(401, {"error": "Unauthorized"})

        if method == "GET":
            qs = event.get("queryStringParameters") or {}
            result = tool_get_checklist({"date": qs.get("date")})
            return _http(200, result)

        if method == "POST":
            body = event.get("body", "")
            if event.get("isBase64Encoded"):
                body = base64.b64decode(body).decode("utf-8")
            try:
                args = json.loads(body) if body else {}
            except json.JSONDecodeError:
                args = {}
            result = tool_toggle_checklist(args)
            return _http(200, result)

    # ── Delete measurement REST API (for web dashboard) ──

    if method == "POST" and path == "/api/delete-measurement":
        headers = event.get("headers", {})
        auth = headers.get("authorization", "")
        if auth != f"Bearer {BEARER_TOKEN}":
            return _http(401, {"error": "Unauthorized"})
        body = event.get("body", "")
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        try:
            args = json.loads(body) if body else {}
        except json.JSONDecodeError:
            args = {}
        result = tool_delete_measurement(args)
        return _http(200, result)

    # ── MCP endpoint (POST /mcp or POST /) — requires OAuth token ──

    if method == "POST" and path in ("/mcp", "/"):
        # Validate OAuth bearer token
        token_claims = _check_oauth_token(event)
        if not token_claims:
            base = _get_base_url(event)
            return _http(401, {"error": "Unauthorized"}, {
                "www-authenticate": f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"',
            })

        headers = event.get("headers", {})

        # Validate Accept header
        accept = headers.get("accept", "")
        if "application/json" not in accept and "*/*" not in accept:
            return _http(406, {"error": "Not Acceptable: must accept application/json"})

        # Parse JSON-RPC request
        try:
            body = json.loads(event.get("body", "{}"))
        except (json.JSONDecodeError, TypeError):
            return _http(400, _jsonrpc_error(None, -32700, "Parse error"))

        rpc_method = body.get("method", "")
        req_id = body.get("id")
        params = body.get("params", {})

        # Notifications (no id) — return 202 with no body
        if req_id is None:
            if rpc_method == "notifications/initialized":
                return _http(202)
            return _http(202)

        # Dispatch JSON-RPC methods
        if rpc_method == "initialize":
            return _handle_initialize(req_id, params)
        elif rpc_method == "tools/list":
            return _handle_tools_list(req_id, params)
        elif rpc_method == "tools/call":
            return _handle_tools_call(req_id, params)
        elif rpc_method == "ping":
            return _http(200, _jsonrpc_response(req_id, {}))
        else:
            return _http(200, _jsonrpc_error(req_id, -32601, f"Method not found: {rpc_method}"))

    # Unknown route
    if method != "POST":
        return _http(405, {"error": "Method not allowed"})
    return _http(404, {"error": "Not found"})
