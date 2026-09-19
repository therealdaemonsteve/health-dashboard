#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────
#  Bloodwork Dashboard — One-time AWS setup
#  Creates: S3 bucket, CloudFront Function (basic auth), CloudFront distribution
# ─────────────────────────────────────────────

# ── CONFIG (edit these) ──────────────────────
BUCKET_NAME="bloodwork-dashboard-$(openssl rand -hex 4)"
REGION="eu-west-2"          # London — change if you prefer
AUTH_USER="steve"
AUTH_PASS="CHANGE_ME"                    # ← set your password here
CF_COMMENT="Bloodwork Dashboard"
# ─────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/.deploy-config"

echo "╭──────────────────────────────────────────╮"
echo "│  Bloodwork Dashboard — AWS Deploy         │"
echo "╰──────────────────────────────────────────╯"
echo ""

# Validate password was changed
if [ "$AUTH_PASS" = "CHANGE_ME" ]; then
  echo "ERROR: Edit deploy.sh and set AUTH_PASS before running."
  exit 1
fi

# ── 1. Create S3 bucket ──────────────────────
echo "→ Creating S3 bucket: $BUCKET_NAME ($REGION)"
if [ "$REGION" = "us-east-1" ]; then
  aws s3api create-bucket \
    --bucket "$BUCKET_NAME" \
    --region "$REGION"
else
  aws s3api create-bucket \
    --bucket "$BUCKET_NAME" \
    --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION"
fi

# Block all public access
aws s3api put-public-access-block \
  --bucket "$BUCKET_NAME" \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

echo "  ✓ Bucket created (private)"

# ── 2. Create CloudFront Function (Basic Auth) ──
echo "→ Creating CloudFront Function for Basic Auth"

AUTH_B64=$(printf '%s:%s' "$AUTH_USER" "$AUTH_PASS" | base64)

CF_FUNC_NAME="bloodwork-basic-auth-$(openssl rand -hex 4)"
CF_FUNC_CODE=$(cat <<JSEOF
function handler(event) {
  var request = event.request;
  var headers = request.headers;
  var expected = 'Basic ${AUTH_B64}';
  if (headers.authorization && headers.authorization.value === expected) {
    return request;
  }
  return {
    statusCode: 401,
    statusDescription: 'Unauthorized',
    headers: {
      'www-authenticate': { value: 'Basic realm="Bloodwork Dashboard"' },
      'content-type': { value: 'text/plain' }
    },
    body: 'Unauthorized'
  };
}
JSEOF
)

# Write function code to temp file
FUNC_TMP=$(mktemp)
echo "$CF_FUNC_CODE" > "$FUNC_TMP"

aws cloudfront create-function \
  --name "$CF_FUNC_NAME" \
  --function-config '{"Comment":"Basic auth for bloodwork dashboard","Runtime":"cloudfront-js-2.0"}' \
  --function-code "fileb://$FUNC_TMP" \
  --region us-east-1 > /tmp/cf-func-create.json

CF_FUNC_ETAG=$(jq -r '.ETag' /tmp/cf-func-create.json)
CF_FUNC_ARN=$(jq -r '.FunctionSummary.FunctionMetadata.FunctionARN' /tmp/cf-func-create.json)

# Publish the function
aws cloudfront publish-function \
  --name "$CF_FUNC_NAME" \
  --if-match "$CF_FUNC_ETAG" \
  --region us-east-1 > /dev/null

rm -f "$FUNC_TMP"
echo "  ✓ CloudFront Function created and published: $CF_FUNC_NAME"

# ── 3. Create Origin Access Control ──────────
echo "→ Creating Origin Access Control"
OAC_NAME="bloodwork-oac-$(openssl rand -hex 4)"
OAC_RESULT=$(aws cloudfront create-origin-access-control \
  --origin-access-control-config "{
    \"Name\": \"$OAC_NAME\",
    \"Description\": \"OAC for bloodwork dashboard\",
    \"SigningProtocol\": \"sigv4\",
    \"SigningBehavior\": \"always\",
    \"OriginAccessControlOriginType\": \"s3\"
  }" --region us-east-1)

OAC_ID=$(echo "$OAC_RESULT" | jq -r '.OriginAccessControl.Id')
echo "  ✓ OAC created: $OAC_ID"

# ── 4. Create CloudFront distribution ────────
echo "→ Creating CloudFront distribution (this takes ~2 min to deploy)"

S3_ORIGIN="${BUCKET_NAME}.s3.${REGION}.amazonaws.com"

DIST_CONFIG=$(cat <<DISTEOF
{
  "CallerReference": "bloodwork-$(date +%s)",
  "Comment": "$CF_COMMENT",
  "Enabled": true,
  "DefaultRootObject": "dashboard.html",
  "Origins": {
    "Quantity": 1,
    "Items": [
      {
        "Id": "S3-bloodwork",
        "DomainName": "$S3_ORIGIN",
        "OriginAccessControlId": "$OAC_ID",
        "S3OriginConfig": {
          "OriginAccessIdentity": ""
        }
      }
    ]
  },
  "DefaultCacheBehavior": {
    "TargetOriginId": "S3-bloodwork",
    "ViewerProtocolPolicy": "redirect-to-https",
    "AllowedMethods": {
      "Quantity": 2,
      "Items": ["GET", "HEAD"]
    },
    "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6",
    "FunctionAssociations": {
      "Quantity": 1,
      "Items": [
        {
          "FunctionARN": "$CF_FUNC_ARN",
          "EventType": "viewer-request"
        }
      ]
    }
  },
  "PriceClass": "PriceClass_100",
  "ViewerCertificate": {
    "CloudFrontDefaultCertificate": true
  }
}
DISTEOF
)

DIST_RESULT=$(aws cloudfront create-distribution \
  --distribution-config "$DIST_CONFIG" \
  --region us-east-1)

DIST_ID=$(echo "$DIST_RESULT" | jq -r '.Distribution.Id')
DIST_DOMAIN=$(echo "$DIST_RESULT" | jq -r '.Distribution.DomainName')

echo "  ✓ Distribution created: $DIST_ID"

# ── 5. Add S3 bucket policy to allow CloudFront ──
echo "→ Setting bucket policy for CloudFront access"

AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

BUCKET_POLICY=$(cat <<POLEOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowCloudFrontServicePrincipal",
      "Effect": "Allow",
      "Principal": {
        "Service": "cloudfront.amazonaws.com"
      },
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::${BUCKET_NAME}/*",
      "Condition": {
        "StringEquals": {
          "AWS:SourceArn": "arn:aws:cloudfront::${AWS_ACCOUNT_ID}:distribution/${DIST_ID}"
        }
      }
    }
  ]
}
POLEOF
)

aws s3api put-bucket-policy --bucket "$BUCKET_NAME" --policy "$BUCKET_POLICY"
echo "  ✓ Bucket policy applied"

# ── 6. Save config for update.sh ─────────────
cat > "$CONFIG_FILE" <<EOF
BUCKET_NAME=$BUCKET_NAME
REGION=$REGION
DIST_ID=$DIST_ID
DIST_DOMAIN=$DIST_DOMAIN
CF_FUNC_NAME=$CF_FUNC_NAME
OAC_ID=$OAC_ID
EOF

echo ""
echo "╭──────────────────────────────────────────╮"
echo "│  Setup complete!                          │"
echo "╰──────────────────────────────────────────╯"
echo ""
echo "  Bucket:       $BUCKET_NAME"
echo "  Distribution: $DIST_ID"
echo "  URL:          https://$DIST_DOMAIN"
echo "  Auth:         $AUTH_USER / ****"
echo ""
echo "  CloudFront takes 5-10 min to deploy globally."
echo "  Run ./update.sh to upload your dashboard files."
echo ""
echo "  Config saved to: $CONFIG_FILE"
