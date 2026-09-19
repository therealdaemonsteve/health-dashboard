#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────
#  Bloodwork Dashboard — Tear down AWS resources
#  Removes: CloudFront distribution, function, OAC, S3 bucket
# ─────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/.deploy-config"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "ERROR: No .deploy-config found."
  exit 1
fi

source "$CONFIG_FILE"

echo "╭──────────────────────────────────────────╮"
echo "│  Bloodwork Dashboard — Teardown           │"
echo "╰──────────────────────────────────────────╯"
echo ""
echo "This will DELETE all AWS resources for this dashboard:"
echo "  Bucket: $BUCKET_NAME"
echo "  Distribution: $DIST_ID"
echo "  CF Function: $CF_FUNC_NAME"
echo ""
read -p "Are you sure? (yes/no): " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
  echo "Aborted."
  exit 0
fi

# ── 0. Delete Lambda proxy resources ─────────
if [ -n "${LAMBDA_FUNC_NAME:-}" ]; then
  echo "→ Deleting Lambda Function URL"
  aws lambda delete-function-url-config \
    --function-name "$LAMBDA_FUNC_NAME" \
    --region "$REGION" 2>/dev/null || true

  echo "→ Deleting Lambda function: $LAMBDA_FUNC_NAME"
  aws lambda delete-function \
    --function-name "$LAMBDA_FUNC_NAME" \
    --region "$REGION" 2>/dev/null || true
  echo "  ✓ Lambda function deleted"
fi

if [ -n "${LAMBDA_ROLE_ARN:-}" ]; then
  LAMBDA_ROLE_NAME="bloodwork-lambda-role"
  echo "→ Detaching policy from IAM role: $LAMBDA_ROLE_NAME"
  aws iam detach-role-policy \
    --role-name "$LAMBDA_ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole" 2>/dev/null || true

  echo "→ Deleting IAM role: $LAMBDA_ROLE_NAME"
  aws iam delete-role \
    --role-name "$LAMBDA_ROLE_NAME" 2>/dev/null || true
  echo "  ✓ IAM role deleted"
fi

# ── 1. Disable CloudFront distribution ───────
echo "→ Disabling CloudFront distribution"
DIST_CONFIG=$(aws cloudfront get-distribution-config --id "$DIST_ID" --region us-east-1)
ETAG=$(echo "$DIST_CONFIG" | jq -r '.ETag')
# Set Enabled=false
UPDATED_CONFIG=$(echo "$DIST_CONFIG" | jq '.DistributionConfig.Enabled = false | .DistributionConfig')
aws cloudfront update-distribution \
  --id "$DIST_ID" \
  --distribution-config "$UPDATED_CONFIG" \
  --if-match "$ETAG" \
  --region us-east-1 > /dev/null
echo "  ✓ Distribution disabled (takes a few minutes to propagate)"

echo "→ Waiting for distribution to be Deployed..."
aws cloudfront wait distribution-deployed --id "$DIST_ID" --region us-east-1
echo "  ✓ Distribution deployed (disabled state)"

# ── 2. Delete CloudFront distribution ────────
echo "→ Deleting CloudFront distribution"
ETAG=$(aws cloudfront get-distribution --id "$DIST_ID" --region us-east-1 | jq -r '.ETag')
aws cloudfront delete-distribution --id "$DIST_ID" --if-match "$ETAG" --region us-east-1
echo "  ✓ Distribution deleted"

# ── 3. Delete CloudFront Function ────────────
echo "→ Deleting CloudFront Function: $CF_FUNC_NAME"
FUNC_ETAG=$(aws cloudfront describe-function --name "$CF_FUNC_NAME" --region us-east-1 | jq -r '.ETag')
aws cloudfront delete-function --name "$CF_FUNC_NAME" --if-match "$FUNC_ETAG" --region us-east-1
echo "  ✓ Function deleted"

# ── 4. Delete OAC ────────────────────────────
if [ -n "${OAC_ID:-}" ]; then
  echo "→ Deleting Origin Access Control: $OAC_ID"
  OAC_ETAG=$(aws cloudfront get-origin-access-control --id "$OAC_ID" --region us-east-1 | jq -r '.ETag')
  aws cloudfront delete-origin-access-control --id "$OAC_ID" --if-match "$OAC_ETAG" --region us-east-1
  echo "  ✓ OAC deleted"
fi

# ── 5. Empty and delete S3 bucket ────────────
echo "→ Emptying S3 bucket: $BUCKET_NAME"
aws s3 rm "s3://$BUCKET_NAME" --recursive --region "$REGION"
echo "→ Deleting S3 bucket"
aws s3api delete-bucket --bucket "$BUCKET_NAME" --region "$REGION"
echo "  ✓ Bucket deleted"

# ── 6. Clean up config ──────────────────────
rm -f "$CONFIG_FILE"
echo ""
echo "  All resources deleted. Config file removed."
