#!/usr/bin/env python3
"""
One-time cleanup: remove duplicate TDEE / Daily Deficit measurements from S3.

For dates with duplicates from nutrition_log source:
- TDEE: keep the higher value (incorporates Apple Health activity data)
- Daily Deficit: keep the more negative value (uses correct, higher TDEE)

Then trigger a full recomputation to ensure consistent IDs.
"""
import json
import hashlib
import subprocess
import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, ".deploy-config")

# Read deploy config
config = {}
with open(CONFIG_FILE) as f:
    for line in f:
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, _, val = line.partition("=")
            config[key.strip()] = val.strip().strip('"')

BUCKET = config["BUCKET_NAME"]
REGION = config["REGION"]

print(f"Bucket: {BUCKET}, Region: {REGION}")

# Download bloodwork_data.json
print("Downloading bloodwork_data.json from S3...")
result = subprocess.run(
    ["aws", "s3", "cp", f"s3://{BUCKET}/bloodwork_data.json", "/tmp/bw_cleanup.json", "--region", REGION],
    capture_output=True, text=True
)
if result.returncode != 0:
    print(f"ERROR: {result.stderr}")
    sys.exit(1)

with open("/tmp/bw_cleanup.json") as f:
    data = json.load(f)

measurements = data.get("measurements", [])
print(f"Total measurements before cleanup: {len(measurements)}")

# Find duplicates for TDEE and Daily Deficit
TARGETS = {"TDEE", "Daily Deficit"}
dupes_removed = 0

for target in TARGETS:
    # Group by date
    by_date = {}
    for i, m in enumerate(measurements):
        if m.get("biomarker") == target:
            date = m.get("date", "")
            by_date.setdefault(date, []).append(i)

    dates_with_dupes = {d: idxs for d, idxs in by_date.items() if len(idxs) > 1}
    print(f"\n{target}: {len(dates_with_dupes)} dates with duplicates")

    indices_to_remove = set()
    for date, idxs in sorted(dates_with_dupes.items()):
        values = [(i, measurements[i]["value"]) for i in idxs]
        if target == "TDEE":
            # Keep highest value (incorporates Apple Health activity)
            keep_idx = max(values, key=lambda x: x[1])[0]
        else:
            # Daily Deficit: keep most negative (correct TDEE was used)
            keep_idx = min(values, key=lambda x: x[1])[0]

        for idx, val in values:
            if idx != keep_idx:
                indices_to_remove.add(idx)
                print(f"  {date}: removing {val:.0f} (keeping {measurements[keep_idx]['value']:.0f})")

    dupes_removed += len(indices_to_remove)
    # Remove in reverse order to preserve indices
    for idx in sorted(indices_to_remove, reverse=True):
        measurements.pop(idx)

print(f"\nRemoved {dupes_removed} duplicate measurements")
print(f"Total measurements after cleanup: {len(measurements)}")

# Now regenerate deterministic IDs for all nutrition_log measurements
# to use the new format (without value in the ID)
id_updates = 0
for m in measurements:
    if m.get("source") == "nutrition_log":
        old_id = m.get("id", "")
        id_key = f"nutrition|log|{m['biomarker']}|{m['date']}"
        new_id = "m_" + hashlib.sha1(id_key.encode()).hexdigest()[:12]
        if old_id != new_id:
            m["id"] = new_id
            id_updates += 1

print(f"Updated {id_updates} measurement IDs to new format")

# Update biomarker stats
biomarker_index = {b["name"]: b for b in data.get("biomarkers", [])}
for target in TARGETS:
    if target in biomarker_index:
        n = sum(1 for m in measurements if m.get("biomarker") == target)
        biomarker_index[target].setdefault("stats", {})["n"] = n
        biomarker_index[target]["count"] = n
        print(f"{target}: updated count to {n}")

# Write back
with open("/tmp/bw_cleanup.json", "w") as f:
    json.dump(data, f, indent=2)

# Upload
print("\nUploading cleaned data to S3...")
result = subprocess.run(
    ["aws", "s3", "cp", "/tmp/bw_cleanup.json", f"s3://{BUCKET}/bloodwork_data.json",
     "--content-type", "application/json", "--cache-control", "max-age=0, must-revalidate",
     "--region", REGION],
    capture_output=True, text=True
)
if result.returncode != 0:
    print(f"ERROR: {result.stderr}")
    sys.exit(1)

print("Done! Duplicates cleaned.")

# Also update local copy
local_bw = os.path.join(SCRIPT_DIR, "..", "bloodwork_data.json")
if os.path.exists(local_bw):
    with open("/tmp/bw_cleanup.json") as src, open(local_bw, "w") as dst:
        dst.write(src.read())
    print(f"Updated local copy: {local_bw}")
