"""Lambda proxy for Anthropic Claude API — keeps API key server-side.

CORS is handled by the Function URL config, not by this code.
"""

import json
import os
import urllib.request
import urllib.error

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
BEARER_TOKEN = os.environ["BEARER_TOKEN"]
ALLOWED_ORIGIN = os.environ["ALLOWED_ORIGIN"]

MAX_TOKENS_CAP = 8192
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
TIMEOUT_SECONDS = 90


def _response(status, body, extra_headers=None):
    headers = extra_headers or {}
    return {
        "statusCode": status,
        "headers": headers,
        "body": json.dumps(body) if isinstance(body, dict) else body,
    }


def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "")

    # OPTIONS preflight is handled by Function URL CORS config
    if method == "OPTIONS":
        return {"statusCode": 204, "body": ""}

    # Auth: bearer token
    auth = event.get("headers", {}).get("authorization", "")
    if auth != f"Bearer {BEARER_TOKEN}":
        return _response(401, {"error": "Unauthorized"})

    # Auth: origin check
    origin = event.get("headers", {}).get("origin", "")
    if origin != ALLOWED_ORIGIN:
        return _response(403, {"error": "Forbidden: invalid origin"})

    # Only POST allowed
    if method != "POST":
        return _response(405, {"error": "Method not allowed"})

    # Parse request body
    try:
        body = json.loads(event.get("body", "{}"))
    except (json.JSONDecodeError, TypeError):
        return _response(400, {"error": "Invalid JSON"})

    # Build Anthropic request — only forward safe fields
    max_tokens = min(int(body.get("max_tokens", 4096)), MAX_TOKENS_CAP)
    anthropic_body = {
        "model": body.get("model", "claude-sonnet-4-6"),
        "max_tokens": max_tokens,
        "messages": body.get("messages", []),
    }
    if body.get("system"):
        anthropic_body["system"] = body["system"]

    payload = json.dumps(anthropic_body).encode("utf-8")

    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": ANTHROPIC_VERSION,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            resp_body = resp.read().decode("utf-8")
            return _response(200, resp_body, {"content-type": "application/json"})
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return _response(e.code, err_body, {"content-type": "application/json"})
    except urllib.error.URLError as e:
        return _response(502, {"error": f"Upstream error: {e.reason}"})
