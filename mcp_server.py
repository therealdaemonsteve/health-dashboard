"""Health Dashboard MCP Server.

Fetches bloodwork and events data from S3 and exposes it as tools
that Claude Desktop can call directly via stdio.
"""

import csv
import hashlib
import io
import json
import os
import secrets
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import boto3
from fastmcp import FastMCP

S3_BUCKET = os.environ.get("S3_BUCKET", "")
if not S3_BUCKET:
    raise RuntimeError("S3_BUCKET environment variable is required")
S3_REGION = os.environ.get("S3_REGION", "eu-west-2")
BLOODWORK_KEY = "bloodwork_data.json"
EVENTS_KEY = "events.json"
NUTRITION_KEY = "nutrition.json"
LIFTING_KEY = "lifting.json"
COACHING_KEY = "coaching.json"

mcp = FastMCP("health-dashboard")

# In-memory cache
_cache: dict = {}

# Apple Health constants (ported from build_unified_data.py)
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

APPLE_HEALTH_ARTEFACT = {
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
    "Active Energy Burned": lambda v: v < 0 or v > 5000,
    "Basal Energy Burned": lambda v: v < 500 or v > 4000,
    "Step Count": lambda v: v < 0 or v > 100000,
    "Distance Walking/Running": lambda v: v < 0 or v > 100,
    "Distance Cycling": lambda v: v < 0 or v > 500,
    "Flights Climbed": lambda v: v < 0 or v > 500,
    "Lean Body Mass": lambda v: v < 20 or v > 150,
    "BMI": lambda v: v < 10 or v > 80,
    "Height": lambda v: v < 50 or v > 250,
    "Waist Circumference": lambda v: v < 30 or v > 200,
    "Calories In": lambda v: v < 0 or v > 10000,
    "Protein": lambda v: v < 0 or v > 1000,
    "Carbs": lambda v: v < 0 or v > 2000,
    "Fat": lambda v: v < 0 or v > 1000,
    "Fibre": lambda v: v < 0 or v > 500,
    "Sugar": lambda v: v < 0 or v > 2000,
    "Sodium": lambda v: v < 0 or v > 10000,
    "Water": lambda v: v < 0 or v > 20,
    "Caffeine": lambda v: v < 0 or v > 2000,
    "Sleep": lambda v: v < 0 or v > 1440,
    "Mindful Minutes": lambda v: v < 0 or v > 1440,
    "Workout Duration": lambda v: v < 0 or v > 1440,
    "Workout Calories": lambda v: v < 0 or v > 10000,
    "Workout Distance": lambda v: v < 0 or v > 500,
}

APPLE_HEALTH_CATEGORY = {
    "Heart Rate": "Cardiovascular",
    "Blood Oxygen": "Cardiovascular",
    "Blood Pressure Systolic": "Cardiovascular",
    "Blood Pressure Diastolic": "Cardiovascular",
    "Body Temperature": "Vitals",
    "Active Energy Burned": "Activity",
    "Basal Energy Burned": "Activity",
    "Step Count": "Activity",
    "Distance Walking/Running": "Activity",
    "Distance Cycling": "Activity",
    "Flights Climbed": "Activity",
    "Lean Body Mass": "Body Composition",
    "BMI": "Body Composition",
    "Height": "Body Composition",
    "Waist Circumference": "Body Composition",
    "Calories In": "Nutrition",
    "Protein": "Nutrition",
    "Carbs": "Nutrition",
    "Fat": "Nutrition",
    "Fibre": "Nutrition",
    "Sugar": "Nutrition",
    "Sodium": "Nutrition",
    "Water": "Nutrition",
    "Caffeine": "Nutrition",
    "Sleep": "Sleep",
    "Mindful Minutes": "Mindfulness",
    "Workout Duration": "Workouts",
    "Workout Calories": "Workouts",
    "Workout Distance": "Workouts",
}

APPLE_HEALTH_UNIT_MAP = {"mL/min/kg": "mL/kg/min"}


def _s3_client():
    return boto3.client("s3", region_name=S3_REGION)


def _get_cloudfront_dist_id() -> str:
    """Read CloudFront distribution ID from deploy config."""
    config_path = Path(__file__).parent / "aws-deploy" / ".deploy-config"
    try:
        for line in config_path.read_text().splitlines():
            if line.startswith("DIST_ID="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def _invalidate_cloudfront(key: str) -> None:
    """Invalidate the CloudFront cache for a given S3 key (best-effort)."""
    dist_id = _get_cloudfront_dist_id()
    if not dist_id:
        return
    try:
        cf = boto3.client("cloudfront", region_name="us-east-1")
        cf.create_invalidation(
            DistributionId=dist_id,
            InvalidationBatch={
                "Paths": {"Quantity": 1, "Items": [f"/{key}"]},
                "CallerReference": f"{key}-{time.time()}",
            },
        )
    except Exception:
        pass


def _load_data(force: bool = False) -> dict:
    """Fetch data from S3, caching after first load."""
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

    _cache["loaded_at"] = datetime.utcnow().isoformat() + "Z"
    return _cache


def _fuzzy_match_biomarker(name: str, biomarkers: list[dict]) -> Optional[dict]:
    """Find the best matching biomarker by name (case-insensitive, fuzzy)."""
    name_lower = name.lower().strip()

    # Exact match first
    for b in biomarkers:
        if b["name"].lower() == name_lower:
            return b

    # Substring match
    for b in biomarkers:
        if name_lower in b["name"].lower() or b["name"].lower() in name_lower:
            return b

    # Fuzzy match
    best, best_score = None, 0.0
    for b in biomarkers:
        score = SequenceMatcher(None, name_lower, b["name"].lower()).ratio()
        if score > best_score:
            best, best_score = b, score

    return best if best_score >= 0.5 else None


# ── Tool 1: get_health_overview ──────────────────────────────────────────────


@mcp.tool()
def get_health_overview() -> dict:
    """Get an AI-generated health summary including headline, category
    breakdowns, and prioritised recommendations. No parameters needed."""
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


# ── Tool 2: list_biomarkers ─────────────────────────────────────────────────


@mcp.tool()
def list_biomarkers(
    category: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict]:
    """List all biomarkers with their latest value and status.

    Args:
        category: Filter by category (e.g. "Hormones", "Lipids"). Case-insensitive.
        status: Filter by RAG status: "red", "amber", "green", or "flagged" (red+amber).
    """
    data = _load_data()
    biomarkers = data["bloodwork"].get("biomarkers", [])

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

        stats = b.get("stats") or {}
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


# ── Tool 3: get_biomarker_detail ─────────────────────────────────────────────


@mcp.tool()
def get_biomarker_detail(name: str) -> dict:
    """Get full detail for a single biomarker: stats, reference ranges,
    AI insight, and recent measurements.

    Args:
        name: Biomarker name (fuzzy matched, e.g. "testosterone", "ApoB").
    """
    data = _load_data()
    bw = data["bloodwork"]
    biomarkers = bw.get("biomarkers", [])

    match = _fuzzy_match_biomarker(name, biomarkers)
    if not match:
        available = sorted(set(b["name"] for b in biomarkers))
        return {"error": f"No biomarker matching '{name}'", "available": available}

    # Get measurements for this biomarker
    measurements = [
        m for m in bw.get("measurements", [])
        if m.get("biomarker") == match["name"]
    ]
    measurements.sort(key=lambda m: m.get("date", ""), reverse=True)

    return {
        "name": match["name"],
        "category": match.get("category"),
        "units": match.get("units"),
        "status": match.get("latest_status"),
        "stats": match.get("stats"),
        "reference": match.get("reference"),
        "insight": match.get("insight"),
        "recent_measurements": measurements[:10],
    }


# ── Tool 4: get_measurements ────────────────────────────────────────────────


@mcp.tool()
def get_measurements(
    biomarker: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """Get raw measurement history for a biomarker.

    Args:
        biomarker: Biomarker name (fuzzy matched).
        start_date: Include measurements on or after this date (YYYY-MM-DD).
        end_date: Include measurements on or before this date (YYYY-MM-DD).
    """
    data = _load_data()
    bw = data["bloodwork"]

    # Resolve biomarker name
    match = _fuzzy_match_biomarker(biomarker, bw.get("biomarkers", []))
    if not match:
        available = sorted(set(b["name"] for b in bw.get("biomarkers", [])))
        return {"error": f"No biomarker matching '{biomarker}'", "available": available}

    resolved_name = match["name"]
    erroneous_ids = set(data["events"].get("erroneous_ids", []))

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


# ── Tool 5: get_flagged_biomarkers ───────────────────────────────────────────


@mcp.tool()
def get_flagged_biomarkers() -> list[dict]:
    """Get all red and amber biomarkers, sorted by severity (red first,
    then amber), with stats and AI insights for each."""
    data = _load_data()
    biomarkers = data["bloodwork"].get("biomarkers", [])

    severity_order = {"red": 0, "amber": 1}
    flagged = []
    for b in biomarkers:
        s = b.get("latest_status")
        if s not in severity_order:
            continue
        stats = b.get("stats") or {}
        flagged.append({
            "name": b["name"],
            "category": b.get("category"),
            "status": s,
            "latest_value": stats.get("latest_value"),
            "latest_date": stats.get("latest_date"),
            "unit": (b.get("units") or [None])[0],
            "reference": b.get("reference"),
            "insight": b.get("insight"),
            "pct_change": stats.get("pct_change"),
        })

    flagged.sort(key=lambda b: (severity_order.get(b["status"], 99), b["name"]))
    return flagged


# ── Tool 6: get_events ───────────────────────────────────────────────────────


@mcp.tool()
def get_events(
    event_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[dict]:
    """Get health events (TRT doses, supplement changes, scans, etc.).

    Args:
        event_type: Filter by type (e.g. "trt_dose", "supplement_change", "scan_dexa").
        start_date: Include events on or after this date (YYYY-MM-DD).
        end_date: Include events on or before this date (YYYY-MM-DD).
    """
    data = _load_data()
    events = data["events"].get("events", [])

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


# ── Tool 7: get_category_summary ─────────────────────────────────────────────


@mcp.tool()
def get_category_summary(category: str) -> dict:
    """Get a summary of all biomarkers in a specific category.

    Args:
        category: Category name (e.g. "Hormones", "Lipids", "Liver"). Case-insensitive.
    """
    data = _load_data()
    bw = data["bloodwork"]
    biomarkers = bw.get("biomarkers", [])

    # Find matching category (case-insensitive)
    categories = bw.get("categories", [])
    matched_cat = None
    for c in categories:
        if c.lower() == category.lower():
            matched_cat = c
            break
    if not matched_cat:
        return {"error": f"Unknown category '{category}'", "available": categories}

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

    # Find the matching overview category
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


# ── Tool 8: refresh_data ────────────────────────────────────────────────────


@mcp.tool()
def refresh_data() -> dict:
    """Force re-fetch of all data from S3. Use after uploading new
    bloodwork results."""
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


# ── S3 write helper ──────────────────────────────────────────────────────────


def _sanitize_for_json(obj):
    """Replace NaN/Infinity with None for valid JSON output."""
    if isinstance(obj, float) and (obj != obj or obj == float('inf') or obj == float('-inf')):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    return obj


def _write_s3(key: str, data: dict) -> None:
    """Write JSON data to S3, clear the local cache, and invalidate CloudFront."""
    s3 = _s3_client()
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=json.dumps(_sanitize_for_json(data), indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    _cache.clear()
    _invalidate_cloudfront(key)


# ── Tool 9: add_event ───────────────────────────────────────────────────────


@mcp.tool()
def add_event(
    date: str,
    type: str,
    title: str,
    notes: Optional[str] = None,
) -> dict:
    """Add a health event (e.g. started a supplement, TRT dose change, scan).

    Args:
        date: Event date in YYYY-MM-DD format.
        type: Event type (e.g. "supplement_change", "trt_dose", "scan_dexa",
              "diet_change", "lifestyle", "medical").
        title: Short description of the event.
        notes: Optional longer notes or context.
    """
    data = _load_data(force=True)
    events_data = data["events"]

    event = {
        "id": "e_" + secrets.token_hex(6),
        "date": date,
        "type": type,
        "title": title,
    }
    if notes:
        event["notes"] = notes

    events_data.setdefault("events", []).append(event)
    _write_s3(EVENTS_KEY, events_data)

    return {"status": "added", "event": event}


# ── Tool 10: add_measurement ────────────────────────────────────────────────


@mcp.tool()
def add_measurement(
    date: str,
    biomarker: str,
    value: float,
    unit: str,
) -> dict:
    """Add a manual measurement for a biomarker.

    Args:
        date: Measurement date in YYYY-MM-DD format.
        biomarker: Biomarker name (fuzzy matched to canonical name).
        value: Numeric measurement value.
        unit: Unit of measurement (e.g. "nmol/L", "mg/dL").
    """
    data = _load_data(force=True)
    bw = data["bloodwork"]

    match = _fuzzy_match_biomarker(biomarker, bw.get("biomarkers", []))
    if not match:
        available = sorted(set(b["name"] for b in bw.get("biomarkers", [])))
        return {"error": f"No biomarker matching '{biomarker}'", "available": available}

    resolved_name = match["name"]

    # Generate ID using same pattern as build_unified_data.py
    id_key = f"claude_chat|claude_chat|{resolved_name}|{date}|{value}"
    measurement_id = "m_" + hashlib.sha1(id_key.encode()).hexdigest()[:12]

    measurement = {
        "id": measurement_id,
        "source": "claude_chat",
        "source_label": "Claude Chat",
        "biomarker": resolved_name,
        "date": date,
        "value": value,
        "unit": unit,
    }

    bw.setdefault("measurements", []).append(measurement)

    # Update biomarker stats count
    stats = match.get("stats")
    if stats and "n" in stats:
        stats["n"] += 1

    _write_s3(BLOODWORK_KEY, bw)

    return {"status": "added", "measurement": measurement, "matched_biomarker": resolved_name}


# ── Tool 11: mark_erroneous ─────────────────────────────────────────────────


@mcp.tool()
def mark_erroneous(
    measurement_id: str,
    reason: Optional[str] = None,
) -> dict:
    """Flag a measurement as erroneous so it's excluded from analysis.

    Args:
        measurement_id: The measurement ID to flag (e.g. "m_abc123def456").
        reason: Optional reason for flagging.
    """
    data = _load_data(force=True)
    events_data = data["events"]

    erroneous_ids = events_data.setdefault("erroneous_ids", [])
    if measurement_id in erroneous_ids:
        return {"status": "already_flagged", "measurement_id": measurement_id}

    erroneous_ids.append(measurement_id)
    _write_s3(EVENTS_KEY, events_data)

    result = {"status": "flagged", "measurement_id": measurement_id}
    if reason:
        result["reason"] = reason
    return result


# ── Tool 12: import_apple_health ─────────────────────────────────────────────


@mcp.tool()
def import_apple_health(records_json: str) -> dict:
    """Bulk-import Apple Health records into the dashboard.

    Accepts the JSON-encoded records array from an Apple Health export.
    Each record should have: metric, value, date, unit.
    Supports: restingHeartRate, heartRateVariabilitySDNN, vo2Max,
    walkingHeartRateAverage, respiratoryRate, activeEnergyBurned,
    basalEnergyBurned, stepCount, bodyMass, bodyFatPercentage,
    leanBodyMass, distanceWalkingRunning, dietaryEnergyConsumed,
    dietaryProtein, dietaryCarbohydrates, dietaryFatTotal, dietaryFiber.

    Args:
        records_json: JSON string of the Apple Health records array.
    """
    try:
        records = json.loads(records_json)
    except (json.JSONDecodeError, TypeError) as e:
        return {"error": f"Invalid JSON: {e}"}

    if not isinstance(records, list):
        return {"error": "Expected a JSON array of records"}

    # Force-reload current data from S3
    data = _load_data(force=True)
    bw = data["bloodwork"]
    measurements = bw.setdefault("measurements", [])
    biomarkers = bw.setdefault("biomarkers", [])

    # Build existing_ids set for O(1) dedup
    existing_ids = {m["id"] for m in measurements if "id" in m}

    # Build biomarker_index for fast lookups
    biomarker_index = {b["name"]: b for b in biomarkers}

    imported = 0
    skipped_unmapped = 0
    skipped_non_numeric = 0
    skipped_artefact = 0
    skipped_duplicate = 0
    per_metric = {}

    for rec in records:
        metric = rec.get("metric")
        name = APPLE_HEALTH_MAP.get(metric)
        if not name:
            skipped_unmapped += 1
            continue

        # Parse numeric value
        raw_value = rec.get("value")
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            skipped_non_numeric += 1
            continue

        # Artefact filter
        if name in APPLE_HEALTH_ARTEFACT and APPLE_HEALTH_ARTEFACT[name](value):
            skipped_artefact += 1
            continue

        # Extract date from ISO timestamp
        date_raw = rec.get("date") or ""
        date = date_raw[:10] if len(date_raw) >= 10 else None
        if not date:
            skipped_non_numeric += 1
            continue

        # Normalize unit
        unit = rec.get("unit", "")
        unit = APPLE_HEALTH_UNIT_MAP.get(unit, unit)

        # Deterministic ID matching build_unified_data.py exactly
        id_key = f"apple_health|apple_watch|{name}|{date}|{raw_value}"
        mid = "m_" + hashlib.sha1(id_key.encode()).hexdigest()[:12]

        # Dedup against existing and within-batch
        if mid in existing_ids:
            skipped_duplicate += 1
            continue
        existing_ids.add(mid)

        # Create biomarker entry if new
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

        # Append measurement
        measurements.append({
            "id": mid,
            "source": "apple_health",
            "source_label": "Apple Health",
            "biomarker": name,
            "date": date,
            "value": value,
            "unit": unit,
        })

        # Update stats count
        bio = biomarker_index[name]
        stats = bio.setdefault("stats", {})
        stats["n"] = stats.get("n", 0) + 1

        per_metric[name] = per_metric.get(name, 0) + 1
        imported += 1

    # Update biomarker count field if present
    if "count" in bw:
        bw["count"] = len(biomarkers)

    # Only write to S3 if we actually added records
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


def _calculate_tdee(data: dict, date_str: str) -> dict:
    """Calculate TDEE for a given date from Active + Basal Energy Burned measurements."""
    measurements = data["bloodwork"].get("measurements", [])

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


def _inject_nutrition_biomarkers(data: dict, entry: dict, tdee_info: dict) -> None:
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

        # Remove existing measurement for this metric+date (upsert)
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

        bio = biomarker_index[metric_name]
        n = sum(1 for m in measurements if m.get("biomarker") == metric_name)
        bio.setdefault("stats", {})["n"] = n

    if "count" in bw:
        bw["count"] = len(biomarkers)


# ── Recompute all nutrition biomarkers ────────────────────────────────────────


def _recompute_all_nutrition_biomarkers(data):
    """Remove old nutrition_log measurements, recompute from all nutrition entries.

    Same full-recompute pattern as _inject_lifting_biomarkers: wipe all
    nutrition_log measurements and Nutrition-category biomarkers, then
    re-derive from every entry in nutrition.json.
    """
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
    biomarker_index = {b["name"]: b for b in biomarkers}
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


# ── Tool 13: log_nutrition ───────────────────────────────────────────────────


@mcp.tool()
def log_nutrition(
    date: str,
    calories: float,
    protein_g: float,
    carbs_g: float,
    fat_g: float,
    fibre_g: float = 0,
    notes: Optional[str] = None,
) -> dict:
    """Log daily nutrition intake. Upserts by date — re-logging the same date
    replaces the previous entry. Also calculates TDEE from Apple Health
    activity data and injects nutrition biomarkers into the dashboard.

    Args:
        date: Date in YYYY-MM-DD format.
        calories: Total calories consumed.
        protein_g: Protein in grams.
        carbs_g: Carbohydrates in grams.
        fat_g: Fat in grams.
        fibre_g: Fibre in grams (default 0).
        notes: Optional notes about the day's nutrition.
    """
    data = _load_data(force=True)
    nutrition = data["nutrition"]
    entries = nutrition.setdefault("entries", [])

    # Upsert by date
    entry = None
    for e in entries:
        if e.get("date") == date:
            entry = e
            break
    if entry is None:
        entry = {"date": date}
        entries.append(entry)

    entry.update({
        "calories": calories,
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
        "fibre_g": fibre_g,
        "notes": notes or "",
        "logged_at": datetime.utcnow().isoformat() + "Z",
    })

    # Calculate TDEE and inject biomarker measurements
    tdee_info = _calculate_tdee(data, date)
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
        "date": date,
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


# ── Tool 14: get_nutrition ───────────────────────────────────────────────────


@mcp.tool()
def get_nutrition(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """Retrieve nutrition log entries with optional date filtering.
    Each entry is enriched with TDEE and daily deficit calculated
    from Apple Health activity data.

    Args:
        start_date: Include entries on or after this date (YYYY-MM-DD).
        end_date: Include entries on or before this date (YYYY-MM-DD).
    """
    data = _load_data()
    nutrition = data["nutrition"]
    entries = nutrition.get("entries", [])

    results = []
    for e in entries:
        d = e.get("date", "")
        if start_date and d < start_date:
            continue
        if end_date and d > end_date:
            continue
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


# ── Tool 15: import_macrofactor_nutrition ─────────────────────────────────────


@mcp.tool()
def import_macrofactor_nutrition(csv_data: str) -> dict:
    """Import a MacroFactor food-level CSV export and aggregate into daily
    nutrition totals. Each row is a food item; they are summed per date for
    Calories, Protein, Carbs, Fat, and Fibre. Re-importing is safe — existing
    days are overwritten with fresh totals.

    Args:
        csv_data: The full CSV file content as a string.
    """
    data = _load_data(force=True)
    nutrition = data["nutrition"]
    entries = nutrition.setdefault("entries", [])

    # Strip BOM if present
    if csv_data.startswith("\ufeff"):
        csv_data = csv_data[1:]

    # Parse CSV and aggregate daily totals
    reader = csv.DictReader(io.StringIO(csv_data))
    daily: dict[str, dict] = {}

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


# ── Tool 16: import_macrofactor ──────────────────────────────────────────────


@mcp.tool()
def import_macrofactor(csv_data: str) -> dict:
    """Import a MacroFactor workout CSV export. Parses exercises and sets,
    computes e1RM and volume biomarkers, and stores in lifting.json.
    Re-importing is safe — duplicates are detected by set-level hash.

    Args:
        csv_data: The full CSV file content as a string.
    """
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

        # Parse set data
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

        # Update duration if not set
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

        # Build set entry
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

    # Sort sessions by date
    existing_sessions.sort(key=lambda s: s["date"])

    lifting["version"] = 1
    lifting["imported_at"] = datetime.utcnow().isoformat() + "Z"

    # Recompute all lifting biomarkers
    _inject_lifting_biomarkers(data)

    # Snapshot before writing (writes clear cache)
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

    # Collect unique exercise names
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


# ── Tool 17: get_lifting ────────────────────────────────────────────────────


@mcp.tool()
def get_lifting(
    exercise: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """Query lifting sessions with optional exercise and date filters.
    Returns matching sessions and a list of all exercise names.

    Args:
        exercise: Filter by exercise name (case-insensitive substring match).
        start_date: Include sessions on or after this date (YYYY-MM-DD).
        end_date: Include sessions on or before this date (YYYY-MM-DD).
    """
    data = _load_data()
    lifting = data.get("lifting", {})
    sessions = lifting.get("sessions", [])

    all_exercises = set()
    for s in sessions:
        for ex in s.get("exercises", []):
            all_exercises.add(ex["name"])

    results = []
    exercise_lower = exercise.lower().strip() if exercise else None

    for s in sessions:
        d = s.get("date", "")
        if start_date and d < start_date:
            continue
        if end_date and d > end_date:
            continue

        if exercise_lower:
            # Filter exercises within session
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


# ── Coaching helpers ──────────────────────────────────────────────────────────


def _get_latest_measurement(bw: dict, name: str) -> Optional[dict]:
    """Return the most recent measurement for a given biomarker name."""
    latest = None
    for m in bw.get("measurements", []):
        if m.get("biomarker") == name:
            if latest is None or m.get("date", "") > latest.get("date", ""):
                latest = m
    return latest


# ── Tool 18: get_coaching_brief ──────────────────────────────────────────────


@mcp.tool()
def get_coaching_brief() -> dict:
    """Primary entry point for new coaching chats. Returns a comprehensive
    snapshot: active goals, recently achieved goals, pending and recently
    completed action items, last coaching notes, plus a health snapshot
    (overview headline, flagged biomarkers, recent events, latest weight/body fat).
    No parameters needed."""
    data = _load_data(force=True)
    coaching = data["coaching"]
    bw = data["bloodwork"]
    now = datetime.utcnow().strftime("%Y-%m-%d")

    # Active goals with latest progress note
    active_goals = []
    for g in coaching.get("goals", []):
        if g.get("status") == "active":
            entry = {**g}
            notes = g.get("progress_notes", [])
            entry["progress_notes"] = notes[-1:] if notes else []
            active_goals.append(entry)

    # Goals achieved in last 90 days
    from datetime import timedelta
    cutoff_90 = (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d")
    achieved_goals = [
        g for g in coaching.get("goals", [])
        if g.get("status") == "achieved" and g.get("updated_at", "") >= cutoff_90
    ]

    # Pending action items + recently completed (30 days)
    cutoff_30 = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    pending_actions = [
        a for a in coaching.get("action_items", [])
        if a.get("status") == "pending"
    ]
    recent_done_actions = [
        a for a in coaching.get("action_items", [])
        if a.get("status") in ("done", "skipped")
        and a.get("completed_at", "") >= cutoff_30
    ]

    # Last 10 coaching notes
    all_notes = sorted(coaching.get("notes", []), key=lambda n: n.get("date", ""), reverse=True)
    recent_notes = all_notes[:10]

    # Health snapshot
    overview = bw.get("overview", {})

    flagged = []
    severity_order = {"red": 0, "amber": 1}
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


# ── Tool 19: add_goal ────────────────────────────────────────────────────────


@mcp.tool()
def add_goal(
    title: str,
    category: Optional[str] = None,
    target_value: Optional[float] = None,
    target_unit: Optional[str] = None,
    target_date: Optional[str] = None,
) -> dict:
    """Create a new coaching goal. Status defaults to 'active'.

    Args:
        title: Goal title (e.g. "Cut to 85kg by Sep 2026").
        category: Optional category (e.g. "body_composition", "lipids", "strength").
        target_value: Optional numeric target.
        target_unit: Optional unit for target (e.g. "kg", "mg/dL").
        target_date: Optional target date (YYYY-MM-DD).
    """
    data = _load_data(force=True)
    coaching = data["coaching"]
    now = datetime.utcnow().isoformat() + "Z"

    goal = {
        "id": "g_" + secrets.token_hex(6),
        "title": title,
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "progress_notes": [],
    }
    if category:
        goal["category"] = category
    if target_value is not None:
        goal["target_value"] = target_value
    if target_unit:
        goal["target_unit"] = target_unit
    if target_date:
        goal["target_date"] = target_date

    coaching.setdefault("goals", []).append(goal)
    _write_s3(COACHING_KEY, coaching)

    return {"status": "created", "goal": goal}


# ── Tool 20: update_goal ─────────────────────────────────────────────────────


@mcp.tool()
def update_goal(
    goal_id: str,
    status: Optional[str] = None,
    title: Optional[str] = None,
    target_value: Optional[float] = None,
    target_unit: Optional[str] = None,
    target_date: Optional[str] = None,
    progress_note: Optional[str] = None,
) -> dict:
    """Update a coaching goal and/or append a progress note.

    Args:
        goal_id: The goal ID (e.g. "g_abc123def456").
        status: New status: "active", "achieved", or "abandoned".
        title: Updated title.
        target_value: Updated numeric target.
        target_unit: Updated unit for target.
        target_date: Updated target date (YYYY-MM-DD).
        progress_note: Text to append as a progress note with today's date.
    """
    data = _load_data(force=True)
    coaching = data["coaching"]
    now = datetime.utcnow().isoformat() + "Z"
    today = datetime.utcnow().strftime("%Y-%m-%d")

    goal = None
    for g in coaching.get("goals", []):
        if g.get("id") == goal_id:
            goal = g
            break

    if not goal:
        return {"error": f"Goal not found: {goal_id}"}

    if status:
        goal["status"] = status
    if title:
        goal["title"] = title
    if target_value is not None:
        goal["target_value"] = target_value
    if target_unit:
        goal["target_unit"] = target_unit
    if target_date:
        goal["target_date"] = target_date
    if progress_note:
        goal.setdefault("progress_notes", []).append({"date": today, "note": progress_note})

    goal["updated_at"] = now
    _write_s3(COACHING_KEY, coaching)

    return {"status": "updated", "goal": goal}


# ── Tool 21: add_coaching_note ───────────────────────────────────────────────


@mcp.tool()
def add_coaching_note(
    text: str,
    tags: Optional[str] = None,
    date: Optional[str] = None,
) -> dict:
    """Add a free-form coaching note for persistence across sessions.

    Args:
        text: The note text.
        tags: Optional comma-separated tags (e.g. "lipids,strategy").
        date: Note date (YYYY-MM-DD). Defaults to today.
    """
    data = _load_data(force=True)
    coaching = data["coaching"]
    now = datetime.utcnow().isoformat() + "Z"
    today = date or datetime.utcnow().strftime("%Y-%m-%d")

    note = {
        "id": "n_" + secrets.token_hex(6),
        "date": today,
        "text": text,
        "tags": [t.strip() for t in tags.split(",")] if tags else [],
        "created_at": now,
    }

    coaching.setdefault("notes", []).append(note)
    _write_s3(COACHING_KEY, coaching)

    return {"status": "added", "note": note}


# ── Tool 22: add_action_item ────────────────────────────────────────────────


@mcp.tool()
def add_action_item(
    title: str,
    due_date: Optional[str] = None,
    goal_id: Optional[str] = None,
) -> dict:
    """Create a coaching action item (to-do), optionally linked to a goal.

    Args:
        title: Action item title (e.g. "Book blood panel at Randox").
        due_date: Optional due date (YYYY-MM-DD).
        goal_id: Optional goal ID to link this action to.
    """
    data = _load_data(force=True)
    coaching = data["coaching"]
    now = datetime.utcnow().isoformat() + "Z"

    item = {
        "id": "a_" + secrets.token_hex(6),
        "title": title,
        "status": "pending",
        "created_at": now,
        "completed_at": None,
    }
    if due_date:
        item["due_date"] = due_date
    if goal_id:
        item["goal_id"] = goal_id

    coaching.setdefault("action_items", []).append(item)
    _write_s3(COACHING_KEY, coaching)

    return {"status": "created", "action_item": item}


# ── Tool 23: update_action_item ──────────────────────────────────────────────


@mcp.tool()
def update_action_item(
    action_id: str,
    status: Optional[str] = None,
    title: Optional[str] = None,
    due_date: Optional[str] = None,
) -> dict:
    """Update a coaching action item.

    Args:
        action_id: The action item ID (e.g. "a_abc123def456").
        status: New status: "pending", "done", or "skipped".
        title: Updated title.
        due_date: Updated due date (YYYY-MM-DD).
    """
    data = _load_data(force=True)
    coaching = data["coaching"]
    now = datetime.utcnow().isoformat() + "Z"

    item = None
    for a in coaching.get("action_items", []):
        if a.get("id") == action_id:
            item = a
            break

    if not item:
        return {"error": f"Action item not found: {action_id}"}

    if status:
        item["status"] = status
        if status in ("done", "skipped"):
            item["completed_at"] = now
    if title:
        item["title"] = title
    if due_date:
        item["due_date"] = due_date

    _write_s3(COACHING_KEY, coaching)

    return {"status": "updated", "action_item": item}


# ── Tool 24: update_overview ────────────────────────────────────────────────


@mcp.tool()
def update_overview(
    headline: str,
    categories: str,
    recommendations: str,
) -> dict:
    """Rewrite the AI health overview in bloodwork_data.json.

    Args:
        headline: The overview headline text.
        categories: JSON string — array of category objects (e.g. [{"title": "Lipids", "status": "amber", "summary": "..."}]).
        recommendations: JSON string — array of recommendation objects (e.g. [{"priority": "high", "text": "..."}]).
    """
    try:
        cats = json.loads(categories)
    except (json.JSONDecodeError, TypeError) as e:
        return {"error": f"Invalid categories JSON: {e}"}

    try:
        recs = json.loads(recommendations)
    except (json.JSONDecodeError, TypeError) as e:
        return {"error": f"Invalid recommendations JSON: {e}"}

    data = _load_data(force=True)
    bw = data["bloodwork"]

    bw.setdefault("overview", {})["headline"] = headline
    bw["overview"]["categories"] = cats
    bw["overview"]["recommendations"] = recs
    bw["overview"]["updated_at"] = datetime.utcnow().isoformat() + "Z"

    _write_s3(BLOODWORK_KEY, bw)

    return {
        "status": "updated",
        "headline": headline,
        "category_count": len(cats),
        "recommendation_count": len(recs),
    }


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
