#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────
#  Bloodwork Dashboard — Sync files to S3
#  Run this after rebuilding dashboard.html or bloodwork_data.json
# ─────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/.deploy-config"
DASHBOARD_DIR="$SCRIPT_DIR/.."  # assumes aws-deploy/ is inside outputs/

if [ ! -f "$CONFIG_FILE" ]; then
  echo "ERROR: No .deploy-config found. Run deploy.sh first."
  exit 1
fi

source "$CONFIG_FILE"

echo "╭──────────────────────────────────────────╮"
echo "│  Bloodwork Dashboard — Update             │"
echo "╰──────────────────────────────────────────╯"
echo ""
echo "  Bucket: $BUCKET_NAME"
echo "  Distribution: $DIST_ID"
echo ""

# ── Update embedded fallback data in dashboard.html ──
if [ -f "$DASHBOARD_DIR/dashboard.html" ] && [ -f "$DASHBOARD_DIR/bloodwork_data.json" ]; then
  echo "→ Updating embedded BLOODWORK_DATA_FALLBACK in dashboard.html"
  python3 -c "
import pathlib
html = pathlib.Path('$DASHBOARD_DIR/dashboard.html').read_text()
data = pathlib.Path('$DASHBOARD_DIR/bloodwork_data.json').read_text().rstrip()
start_marker = 'const BLOODWORK_DATA_FALLBACK = '
start = html.index(start_marker)
# Find the closing '};' — scan for matching braces
brace_start = html.index('{', start)
depth, i = 0, brace_start
while i < len(html):
    if html[i] == '{': depth += 1
    elif html[i] == '}': depth -= 1
    if depth == 0: break
    i += 1
end = i + 1  # past the closing }
# Include the trailing semicolon
if end < len(html) and html[end] == ';': end += 1
updated = html[:start] + start_marker + data + ';' + html[end:]
pathlib.Path('$DASHBOARD_DIR/dashboard.html').write_text(updated)
"
  echo "  ✓ Embedded fallback data updated"
fi

# ── Upload dashboard.html (with template substitution) ──
if [ -f "$DASHBOARD_DIR/dashboard.html" ]; then
  echo "→ Uploading dashboard.html"
  UPLOAD_HTML=$(mktemp)
  sed -e "s|__PROXY_URL__|${LAMBDA_FUNC_URL:-}|g" \
      -e "s|__PROXY_TOKEN__|${BEARER_TOKEN:-}|g" \
      -e "s|__MCP_URL__|${MCP_FUNC_URL:-}|g" \
      -e "s|__MCP_TOKEN__|${MCP_BEARER_TOKEN:-}|g" \
      "$DASHBOARD_DIR/dashboard.html" > "$UPLOAD_HTML"
  aws s3 cp "$UPLOAD_HTML" "s3://$BUCKET_NAME/dashboard.html" \
    --content-type "text/html; charset=utf-8" \
    --cache-control "max-age=0, must-revalidate" \
    --region "$REGION"
  rm -f "$UPLOAD_HTML"
  echo "  ✓ dashboard.html uploaded"
else
  echo "  ⚠ dashboard.html not found in $DASHBOARD_DIR"
fi

# ── Upload bloodwork_data.json (merge Lambda-added measurements) ──
if [ -f "$DASHBOARD_DIR/bloodwork_data.json" ]; then
  echo "→ Merging and uploading bloodwork_data.json"
  MERGED_BW=$(mktemp)
  S3_BW=$(mktemp)
  # Download current S3 version (may have Lambda-added measurements)
  if aws s3 cp "s3://$BUCKET_NAME/bloodwork_data.json" "$S3_BW" --region "$REGION" 2>/dev/null; then
    python3 -c "
import json, sys

with open('$DASHBOARD_DIR/bloodwork_data.json') as f:
    local = json.load(f)
with open('$S3_BW') as f:
    remote = json.load(f)

# Build set of local measurement IDs
local_ids = {m['id'] for m in local.get('measurements', []) if 'id' in m}

# Find measurements in S3 that were added by Lambda (not in local build)
remote_only = [m for m in remote.get('measurements', []) if m.get('id') and m['id'] not in local_ids]

if remote_only:
    # Merge remote-only measurements into local
    local.setdefault('measurements', []).extend(remote_only)

    # Merge any biomarkers that only exist in remote
    local_bm_names = {b['name'] for b in local.get('biomarkers', [])}
    for b in remote.get('biomarkers', []):
        if b['name'] not in local_bm_names:
            local.setdefault('biomarkers', []).append(b)

    # Update counts on merged biomarkers
    for b in local.get('biomarkers', []):
        n = sum(1 for m in local['measurements'] if m.get('biomarker') == b['name'])
        if b.get('stats') is None:
            b['stats'] = {}
        b['stats']['n'] = n
        b['count'] = n

    print(f'  Merged {len(remote_only)} remote-only measurements', file=sys.stderr)
else:
    print('  No remote-only measurements to merge', file=sys.stderr)

# Preserve overview if remote has a newer updated_at
r_ov = remote.get('overview', {})
l_ov = local.get('overview', {})
if r_ov.get('updated_at', '') > l_ov.get('updated_at', ''):
    local['overview'] = r_ov
    print('  Kept newer remote overview', file=sys.stderr)

with open('$MERGED_BW', 'w') as f:
    json.dump(local, f, indent=2)
"
    aws s3 cp "$MERGED_BW" "s3://$BUCKET_NAME/bloodwork_data.json" \
      --content-type "application/json" \
      --cache-control "max-age=0, must-revalidate" \
      --region "$REGION"
    # Also update local copy so embedded fallback stays in sync
    cp "$MERGED_BW" "$DASHBOARD_DIR/bloodwork_data.json"
  else
    # No S3 version yet, just upload local
    aws s3 cp "$DASHBOARD_DIR/bloodwork_data.json" "s3://$BUCKET_NAME/bloodwork_data.json" \
      --content-type "application/json" \
      --cache-control "max-age=0, must-revalidate" \
      --region "$REGION"
  fi
  rm -f "$MERGED_BW" "$S3_BW"
  echo "  ✓ bloodwork_data.json uploaded"
else
  echo "  ⚠ bloodwork_data.json not found in $DASHBOARD_DIR"
fi

# ── Upload events.json if it exists ──────────
if [ -f "$DASHBOARD_DIR/events.json" ]; then
  echo "→ Uploading events.json"
  aws s3 cp "$DASHBOARD_DIR/events.json" "s3://$BUCKET_NAME/events.json" \
    --content-type "application/json" \
    --cache-control "max-age=0, must-revalidate" \
    --region "$REGION"
  echo "  ✓ events.json uploaded"
fi

# ── Upload nutrition.json if it exists ───────
if [ -f "$DASHBOARD_DIR/nutrition.json" ]; then
  echo "→ Uploading nutrition.json"
  aws s3 cp "$DASHBOARD_DIR/nutrition.json" "s3://$BUCKET_NAME/nutrition.json" \
    --content-type "application/json" \
    --cache-control "max-age=0, must-revalidate" \
    --region "$REGION"
  echo "  ✓ nutrition.json uploaded"
fi

# ── Upload lifting.json if it exists ────────
if [ -f "$DASHBOARD_DIR/lifting.json" ]; then
  echo "→ Uploading lifting.json"
  aws s3 cp "$DASHBOARD_DIR/lifting.json" "s3://$BUCKET_NAME/lifting.json" \
    --content-type "application/json" \
    --cache-control "max-age=0, must-revalidate" \
    --region "$REGION"
  echo "  ✓ lifting.json uploaded"
fi

# ── Upload coaching.json if it exists ──────
if [ -f "$DASHBOARD_DIR/coaching.json" ]; then
  echo "→ Uploading coaching.json"
  aws s3 cp "$DASHBOARD_DIR/coaching.json" "s3://$BUCKET_NAME/coaching.json" \
    --content-type "application/json" \
    --cache-control "max-age=0, must-revalidate" \
    --region "$REGION"
  echo "  ✓ coaching.json uploaded"
fi

# ── Update Lambda code if configured ──────────
if [ -n "${LAMBDA_FUNC_NAME:-}" ]; then
  echo "→ Updating Lambda function code"
  ZIP_FILE="/tmp/proxy-$$.zip"
  (cd "$SCRIPT_DIR/lambda" && zip -q "$ZIP_FILE" proxy.py)
  aws lambda update-function-code \
    --function-name "$LAMBDA_FUNC_NAME" \
    --zip-file "fileb://$ZIP_FILE" \
    --region "$REGION" > /dev/null
  rm -f "$ZIP_FILE"
  echo "  ✓ Lambda function code updated"
fi

# ── Update MCP Lambda code if configured ─────
if [ -n "${MCP_FUNC_NAME:-}" ]; then
  echo "→ Updating MCP server Lambda code"
  ZIP_FILE="/tmp/mcp-handler-$$.zip"
  (cd "$SCRIPT_DIR/lambda" && zip -q "$ZIP_FILE" mcp_handler.py)
  aws lambda update-function-code \
    --function-name "$MCP_FUNC_NAME" \
    --zip-file "fileb://$ZIP_FILE" \
    --region "$REGION" > /dev/null
  rm -f "$ZIP_FILE"
  echo "  ✓ MCP server Lambda code updated"

  # Ensure CLOUDFRONT_DIST_ID env var is set for auto-invalidation
  if [ -n "${DIST_ID:-}" ]; then
    echo "→ Ensuring CLOUDFRONT_DIST_ID env var on MCP Lambda"
    aws lambda wait function-updated \
      --function-name "$MCP_FUNC_NAME" \
      --region "$REGION"
    CURRENT_ENV=$(aws lambda get-function-configuration \
      --function-name "$MCP_FUNC_NAME" \
      --region "$REGION" \
      --query 'Environment.Variables' \
      --output json)
    NEEDS_UPDATE=$(echo "$CURRENT_ENV" | python3 -c "
import json, sys
env = json.load(sys.stdin)
print('yes' if env.get('CLOUDFRONT_DIST_ID') != '$DIST_ID' else 'no')
")
    if [ "$NEEDS_UPDATE" = "yes" ]; then
      UPDATED_ENV=$(echo "$CURRENT_ENV" | python3 -c "
import json, sys
env = json.load(sys.stdin)
env['CLOUDFRONT_DIST_ID'] = '$DIST_ID'
print(json.dumps({'Variables': env}))
")
      aws lambda update-function-configuration \
        --function-name "$MCP_FUNC_NAME" \
        --environment "$UPDATED_ENV" \
        --region "$REGION" > /dev/null
      echo "  ✓ CLOUDFRONT_DIST_ID set to $DIST_ID"
    else
      echo "  ✓ CLOUDFRONT_DIST_ID already set"
    fi
  fi
fi

# ── Invalidate CloudFront cache ──────────────
echo "→ Invalidating CloudFront cache"
INVALIDATION=$(aws cloudfront create-invalidation \
  --distribution-id "$DIST_ID" \
  --paths "/*" \
  --region us-east-1)

INV_ID=$(echo "$INVALIDATION" | jq -r '.Invalidation.Id')
echo "  ✓ Invalidation created: $INV_ID"

echo ""
echo "  Done! Changes will be live in ~30 seconds."
echo "  URL: https://$DIST_DOMAIN"
