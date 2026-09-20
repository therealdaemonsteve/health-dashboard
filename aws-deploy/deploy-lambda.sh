#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────
#  Bloodwork Dashboard — Lambda proxy setup
#  Creates: IAM role, Lambda function, Function URL
#  Run AFTER deploy.sh (needs DIST_DOMAIN in .deploy-config)
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

if [ -z "${DIST_DOMAIN:-}" ]; then
  echo "ERROR: DIST_DOMAIN not set in .deploy-config. Run deploy.sh first."
  exit 1
fi

REGION="${REGION:-eu-west-2}"

echo "╭──────────────────────────────────────────╮"
echo "│  Bloodwork Dashboard — Lambda Proxy Setup │"
echo "╰──────────────────────────────────────────╯"
echo ""

# ── 1. Get Anthropic API key ─────────────────
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  read -sp "Enter your Anthropic API key: " ANTHROPIC_API_KEY
  echo ""
  if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "ERROR: API key cannot be empty."
    exit 1
  fi
fi

# ── 2. Generate bearer token ────────────────
BEARER_TOKEN=$(openssl rand -hex 32)
echo "  ✓ Bearer token generated"

# ── 3. Create IAM role ──────────────────────
LAMBDA_ROLE_NAME="bloodwork-lambda-role"

echo "→ Creating IAM role: $LAMBDA_ROLE_NAME"

TRUST_POLICY='{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "lambda.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}'

LAMBDA_ROLE_ARN=$(aws iam create-role \
  --role-name "$LAMBDA_ROLE_NAME" \
  --assume-role-policy-document "$TRUST_POLICY" \
  --query 'Role.Arn' --output text)

aws iam attach-role-policy \
  --role-name "$LAMBDA_ROLE_NAME" \
  --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"

echo "  ✓ IAM role created: $LAMBDA_ROLE_ARN"

# ── 4. Wait for IAM propagation ─────────────
echo "→ Waiting for IAM propagation..."
sleep 10

# ── 5. Create Lambda function ───────────────
LAMBDA_FUNC_NAME="bloodwork-claude-proxy"
ALLOWED_ORIGIN="https://${DIST_DOMAIN}"

echo "→ Creating Lambda function: $LAMBDA_FUNC_NAME"

ZIP_FILE="/tmp/proxy-$$.zip"
(cd "$SCRIPT_DIR/lambda" && zip -q "$ZIP_FILE" proxy.py)

aws lambda create-function \
  --function-name "$LAMBDA_FUNC_NAME" \
  --runtime python3.12 \
  --handler proxy.handler \
  --role "$LAMBDA_ROLE_ARN" \
  --zip-file "fileb://$ZIP_FILE" \
  --timeout 120 \
  --memory-size 128 \
  --environment "Variables={ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY,BEARER_TOKEN=$BEARER_TOKEN,ALLOWED_ORIGIN=$ALLOWED_ORIGIN}" \
  --region "$REGION" > /dev/null

rm -f "$ZIP_FILE"
echo "  ✓ Lambda function created"

# ── 6. Create Function URL ──────────────────
echo "→ Creating Function URL"

FUNC_URL_RESULT=$(aws lambda create-function-url-config \
  --function-name "$LAMBDA_FUNC_NAME" \
  --auth-type NONE \
  --cors "AllowOrigins=\"$ALLOWED_ORIGIN\",AllowMethods=\"POST\",AllowHeaders=\"Content-Type\",\"Authorization\",MaxAge=86400" \
  --region "$REGION")

LAMBDA_FUNC_URL=$(echo "$FUNC_URL_RESULT" | jq -r '.FunctionUrl')
echo "  ✓ Function URL: $LAMBDA_FUNC_URL"

# ── 7. Add public invoke permission ─────────
echo "→ Adding public invoke permission"

aws lambda add-permission \
  --function-name "$LAMBDA_FUNC_NAME" \
  --statement-id "AllowPublicInvokeFunctionUrl" \
  --action "lambda:InvokeFunctionUrl" \
  --principal "*" \
  --function-url-auth-type NONE \
  --region "$REGION" > /dev/null

aws lambda add-permission \
  --function-name "$LAMBDA_FUNC_NAME" \
  --statement-id "AllowPublicInvokeFunction" \
  --action "lambda:InvokeFunction" \
  --principal "*" \
  --region "$REGION" > /dev/null

echo "  ✓ Public invoke permissions added"

# ── 8. Append to config ─────────────────────
cat >> "$CONFIG_FILE" <<EOF
LAMBDA_FUNC_NAME=$LAMBDA_FUNC_NAME
LAMBDA_FUNC_URL=$LAMBDA_FUNC_URL
LAMBDA_ROLE_ARN=$LAMBDA_ROLE_ARN
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
BEARER_TOKEN=$BEARER_TOKEN
EOF

echo ""
echo "╭──────────────────────────────────────────╮"
echo "│  Lambda proxy setup complete!             │"
echo "╰──────────────────────────────────────────╯"
echo ""
echo "  Function:   $LAMBDA_FUNC_NAME"
echo "  URL:        $LAMBDA_FUNC_URL"
echo "  Origin:     $ALLOWED_ORIGIN"
echo ""
echo "  Config saved to: $CONFIG_FILE"
echo "  Run ./update.sh to deploy dashboard with proxy settings."
