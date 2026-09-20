#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────
#  Health Dashboard — Enable OAuth 2.0 on MCP Lambda
#  Adds OAUTH_SECRET + AUTH_PIN env vars and updates CORS
#  Run AFTER deploy-mcp.sh (needs MCP_FUNC_NAME in .deploy-config)
# ─────────────────────────────────────────────

# ── Source .env if present ───────────────────
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
[[ -f "$ENV_FILE" ]] && { set -a; source "$ENV_FILE"; set +a; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/.deploy-config"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "ERROR: No .deploy-config found. Run deploy.sh + deploy-mcp.sh first."
  exit 1
fi

source "$CONFIG_FILE"

if [ -z "${MCP_FUNC_NAME:-}" ]; then
  echo "ERROR: MCP_FUNC_NAME not set in .deploy-config. Run deploy-mcp.sh first."
  exit 1
fi

REGION="${REGION:-eu-west-2}"

echo "╭──────────────────────────────────────────╮"
echo "│  Health Dashboard — MCP OAuth Setup       │"
echo "╰──────────────────────────────────────────╯"
echo ""
echo "  Function: $MCP_FUNC_NAME"
echo "  Region:   $REGION"
echo ""

# ── 1. Generate OAuth secret and PIN ──────────
OAUTH_SECRET=$(openssl rand -hex 32)
AUTH_PIN=$(printf '%06d' $((RANDOM % 1000000)))
echo "  ✓ OAUTH_SECRET generated"
echo "  ✓ AUTH_PIN generated: $AUTH_PIN"

# ── 2. Get current env vars and merge new ones ──
echo "→ Updating Lambda environment variables"

CURRENT_ENV=$(aws lambda get-function-configuration \
  --function-name "$MCP_FUNC_NAME" \
  --region "$REGION" \
  --query 'Environment.Variables' \
  --output json)

# Merge new vars into existing env
UPDATED_ENV=$(echo "$CURRENT_ENV" | python3 -c "
import json, sys
env = json.load(sys.stdin)
env['OAUTH_SECRET'] = '$OAUTH_SECRET'
env['AUTH_PIN'] = '$AUTH_PIN'
print(json.dumps({'Variables': env}))
")

aws lambda update-function-configuration \
  --function-name "$MCP_FUNC_NAME" \
  --environment "$UPDATED_ENV" \
  --region "$REGION" > /dev/null

echo "  ✓ Environment variables updated"

# ── 3. Wait for update to complete ────────────
echo "→ Waiting for Lambda update to complete..."
aws lambda wait function-updated \
  --function-name "$MCP_FUNC_NAME" \
  --region "$REGION"
echo "  ✓ Lambda update complete"

# ── 4. Update Function URL CORS to allow GET ──
echo "→ Updating Function URL CORS (adding GET method)"

aws lambda update-function-url-config \
  --function-name "$MCP_FUNC_NAME" \
  --auth-type NONE \
  --cors 'AllowOrigins=["*"],AllowMethods=["GET","POST"],AllowHeaders=["Content-Type","Authorization","Accept","Mcp-Session-Id"],MaxAge=86400' \
  --region "$REGION" > /dev/null

echo "  ✓ CORS updated to allow GET + POST"

# ── 5. Append to config ──────────────────────
# Remove old values if re-running
grep -v '^OAUTH_SECRET=' "$CONFIG_FILE" | grep -v '^AUTH_PIN=' > "$CONFIG_FILE.tmp" || true
mv "$CONFIG_FILE.tmp" "$CONFIG_FILE"

cat >> "$CONFIG_FILE" <<EOF
OAUTH_SECRET=$OAUTH_SECRET
AUTH_PIN=$AUTH_PIN
EOF

echo ""
echo "╭──────────────────────────────────────────╮"
echo "│  OAuth setup complete!                    │"
echo "╰──────────────────────────────────────────╯"
echo ""
echo "  PIN:  $AUTH_PIN  (save this — you'll enter it in the browser)"
echo "  URL:  ${MCP_FUNC_URL:-<see .deploy-config>}"
echo ""
echo "  ── Claude.ai Setup ──"
echo ""
echo "  1. Go to Settings → Integrations → Add remote MCP server"
echo "  2. Enter URL:  ${MCP_FUNC_URL:-}mcp"
echo "  3. Claude.ai will handle OAuth automatically"
echo "  4. When prompted in the browser, enter PIN: $AUTH_PIN"
echo ""
echo "  Config saved to: $CONFIG_FILE"
