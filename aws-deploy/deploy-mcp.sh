#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────
#  Health Dashboard — MCP Server Lambda Setup
#  Creates: S3 policy, Lambda function, Function URL
#  Run AFTER deploy.sh + deploy-lambda.sh (needs .deploy-config)
# ─────────────────────────────────────────────

# ── Source .env if present ───────────────────
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
[[ -f "$ENV_FILE" ]] && { set -a; source "$ENV_FILE"; set +a; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/.deploy-config"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "ERROR: No .deploy-config found. Run deploy.sh first."
  exit 1
fi

source "$CONFIG_FILE"

if [ -z "${BUCKET_NAME:-}" ]; then
  echo "ERROR: BUCKET_NAME not set in .deploy-config. Run deploy.sh first."
  exit 1
fi

REGION="${REGION:-eu-west-2}"
LAMBDA_ROLE_NAME="bloodwork-lambda-role"

echo "╭──────────────────────────────────────────╮"
echo "│  Health Dashboard — MCP Server Setup      │"
echo "╰──────────────────────────────────────────╯"
echo ""
echo "  Bucket: $BUCKET_NAME"
echo "  Region: $REGION"
echo ""

# ── 1. Generate bearer token for MCP auth ────
MCP_BEARER_TOKEN=$(openssl rand -hex 32)
echo "  ✓ MCP bearer token generated"

# ── 2. Add S3 read/write policy to Lambda role ──
echo "→ Adding S3 access policy to $LAMBDA_ROLE_NAME"

S3_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject"
      ],
      "Resource": "arn:aws:s3:::${BUCKET_NAME}/*"
    }
  ]
}
EOF
)

aws iam put-role-policy \
  --role-name "$LAMBDA_ROLE_NAME" \
  --policy-name "bloodwork-mcp-s3-access" \
  --policy-document "$S3_POLICY" || {
    echo "  ⚠ Policy may already exist, continuing..."
  }

echo "  ✓ S3 access policy attached"

# ── 3. Create Lambda function ────────────────
MCP_FUNC_NAME="bloodwork-mcp-server"

echo "→ Creating Lambda function: $MCP_FUNC_NAME"

ZIP_FILE="/tmp/mcp-handler-$$.zip"
(cd "$SCRIPT_DIR/lambda" && zip -q "$ZIP_FILE" mcp_handler.py)

aws lambda create-function \
  --function-name "$MCP_FUNC_NAME" \
  --runtime python3.12 \
  --handler mcp_handler.handler \
  --role "${LAMBDA_ROLE_ARN}" \
  --zip-file "fileb://$ZIP_FILE" \
  --timeout 120 \
  --memory-size 256 \
  --environment "Variables={BEARER_TOKEN=$MCP_BEARER_TOKEN,S3_BUCKET=$BUCKET_NAME,S3_REGION=$REGION}" \
  --region "$REGION" > /dev/null

rm -f "$ZIP_FILE"
echo "  ✓ Lambda function created"

# ── 4. Create Function URL ───────────────────
echo "→ Creating Function URL"

FUNC_URL_RESULT=$(aws lambda create-function-url-config \
  --function-name "$MCP_FUNC_NAME" \
  --auth-type NONE \
  --cors 'AllowOrigins="*",AllowMethods="POST",AllowHeaders="Content-Type","Authorization","Accept","Mcp-Session-Id",MaxAge=86400' \
  --region "$REGION")

MCP_FUNC_URL=$(echo "$FUNC_URL_RESULT" | jq -r '.FunctionUrl')
echo "  ✓ Function URL: $MCP_FUNC_URL"

# ── 5. Add public invoke permission ──────────
echo "→ Adding public invoke permission"

aws lambda add-permission \
  --function-name "$MCP_FUNC_NAME" \
  --statement-id "AllowPublicInvokeFunctionUrl" \
  --action "lambda:InvokeFunctionUrl" \
  --principal "*" \
  --function-url-auth-type NONE \
  --region "$REGION" > /dev/null

aws lambda add-permission \
  --function-name "$MCP_FUNC_NAME" \
  --statement-id "AllowPublicInvokeFunction" \
  --action "lambda:InvokeFunction" \
  --principal "*" \
  --region "$REGION" > /dev/null

echo "  ✓ Public invoke permissions added"

# ── 6. Append to config ─────────────────────
cat >> "$CONFIG_FILE" <<EOF
MCP_FUNC_NAME=$MCP_FUNC_NAME
MCP_FUNC_URL=$MCP_FUNC_URL
MCP_BEARER_TOKEN=$MCP_BEARER_TOKEN
EOF

echo ""
echo "╭──────────────────────────────────────────╮"
echo "│  MCP Server setup complete!               │"
echo "╰──────────────────────────────────────────╯"
echo ""
echo "  Function: $MCP_FUNC_NAME"
echo "  URL:      $MCP_FUNC_URL"
echo ""
echo "  ── Claude.ai Configuration ──"
echo ""
echo "  1. Go to Settings → Integrations → Add remote MCP server"
echo "  2. URL:   ${MCP_FUNC_URL}mcp"
echo "  3. Token: $MCP_BEARER_TOKEN"
echo ""
echo "  Config saved to: $CONFIG_FILE"
echo "  Run ./update.sh to deploy code updates."
