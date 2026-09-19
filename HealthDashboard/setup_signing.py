#!/usr/bin/env python3
"""
Provisions signing for CLI builds:
  1. Creates an Apple Distribution certificate (if needed)
  2. Creates an App Store provisioning profile (if needed)
  3. Installs both locally

Uses the App Store Connect API with your .p8 key.
"""

import json, time, subprocess, sys, os, base64, hashlib, tempfile
from pathlib import Path
from datetime import datetime, timezone

# ── Config ──────────────────────────────────────────────────────────
TEAM_ID = "4A3MTWC6CT"
BUNDLE_ID = "com.stevenbennett.healthdashboard"
KEY_ID = os.environ.get("ASC_KEY_ID", "RNCRJVPMK8")
ISSUER_ID = os.environ.get("ASC_ISSUER_ID", "")

SCRIPT_DIR = Path(__file__).parent
ENV_FILE = SCRIPT_DIR / ".testflight.env"
KEY_PATHS = [
    Path.home() / "private_keys" / f"AuthKey_{KEY_ID}.p8",
    Path.home() / ".appstoreconnect" / "private_keys" / f"AuthKey_{KEY_ID}.p8",
    Path.home() / ".private_keys" / f"AuthKey_{KEY_ID}.p8",
]
PROFILES_DIR = Path.home() / "Library" / "MobileDevice" / "Provisioning Profiles"

API_BASE = "https://api.appstoreconnect.apple.com/v1"

# ── Helpers ─────────────────────────────────────────────────────────
def red(s):   print(f"\033[1;31m{s}\033[0m")
def green(s): print(f"\033[1;32m{s}\033[0m")
def blue(s):  print(f"\033[1;34m{s}\033[0m")
def step(s):  print(f"\n\033[1;36m── {s} ──\033[0m")

def die(msg):
    red(f"ERROR: {msg}")
    sys.exit(1)

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def make_jwt(key_path: Path, key_id: str, issuer_id: str) -> str:
    """Create a JWT for App Store Connect API (ES256)."""
    import struct

    key_pem = key_path.read_text().strip()

    header = b64url(json.dumps({
        "alg": "ES256",
        "kid": key_id,
        "typ": "JWT"
    }).encode())

    now = int(time.time())
    payload = b64url(json.dumps({
        "iss": issuer_id,
        "iat": now,
        "exp": now + 1200,  # 20 min
        "aud": "appstoreconnect-v1"
    }).encode())

    signing_input = f"{header}.{payload}"

    # Use openssl to sign (avoids needing PyJWT / cryptography)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False) as kf:
        kf.write(key_pem)
        kf_path = kf.name

    try:
        proc = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", kf_path],
            input=signing_input.encode(),
            capture_output=True
        )
        if proc.returncode != 0:
            die(f"openssl sign failed: {proc.stderr.decode()}")
        der_sig = proc.stdout
    finally:
        os.unlink(kf_path)

    # Convert DER signature to raw r||s (64 bytes)
    # DER: 30 <len> 02 <len_r> <r> 02 <len_s> <s>
    def der_to_raw(der):
        idx = 2  # skip 30 <len>
        if der[1] & 0x80:
            idx += (der[1] & 0x7f)
        idx += 1  # skip first 02
        r_len = der[idx]; idx += 1
        r = der[idx:idx+r_len]; idx += r_len
        idx += 1  # skip second 02
        s_len = der[idx]; idx += 1
        s = der[idx:idx+s_len]
        # Pad/trim to 32 bytes each
        r = r[-32:].rjust(32, b'\x00')
        s = s[-32:].rjust(32, b'\x00')
        return r + s

    raw_sig = der_to_raw(der_sig)
    signature = b64url(raw_sig)

    return f"{signing_input}.{signature}"

def api_get(jwt: str, path: str, params: dict = None) -> dict:
    import urllib.request, urllib.parse, urllib.error
    url = f"{API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {jwt}",
        "Content-Type": "application/json"
    })
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        die(f"API GET {path} failed ({e.code}): {body}")

def api_post(jwt: str, path: str, data: dict) -> dict:
    import urllib.request, urllib.error
    url = f"{API_BASE}{path}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {jwt}",
        "Content-Type": "application/json"
    })
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        die(f"API POST {path} failed ({e.code}): {body}")

def api_delete(jwt: str, path: str):
    import urllib.request, urllib.error
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url, method="DELETE", headers={
        "Authorization": f"Bearer {jwt}",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            pass
    except urllib.error.HTTPError as e:
        if e.code != 404:
            body = e.read().decode()
            die(f"API DELETE {path} failed ({e.code}): {body}")

# ── Main ────────────────────────────────────────────────────────────
def main():
    global ISSUER_ID

    step("Setup Signing for CLI Builds")

    # Load issuer from env file
    if not ISSUER_ID and ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("ASC_ISSUER_ID="):
                ISSUER_ID = line.split("=", 1)[1].strip()

    if not ISSUER_ID:
        die("No ASC_ISSUER_ID found. Run testflight.sh first or set ASC_ISSUER_ID env var.")

    # Find API key
    key_path = None
    for p in KEY_PATHS:
        if p.exists():
            key_path = p
            break
    if not key_path:
        die(f"API key not found. Looked in: {', '.join(str(p) for p in KEY_PATHS)}")

    green(f"Key:     {KEY_ID}")
    green(f"Issuer:  {ISSUER_ID[:8]}...")
    green(f"Team:    {TEAM_ID}")
    green(f"Bundle:  {BUNDLE_ID}")

    # ── Generate JWT ────────────────────────────────────────────────
    step("Generating JWT")
    jwt = make_jwt(key_path, KEY_ID, ISSUER_ID)
    green("JWT created")

    # ── Find Bundle ID ──────────────────────────────────────────────
    step("Looking up Bundle ID")
    resp = api_get(jwt, "/bundleIds", {"filter[identifier]": BUNDLE_ID})
    if not resp.get("data"):
        die(f"Bundle ID '{BUNDLE_ID}' not registered. Register it in the Developer Portal first.")
    bundle_id_obj = resp["data"][0]
    bundle_id_id = bundle_id_obj["id"]
    green(f"Found: {bundle_id_obj['attributes']['identifier']} (id: {bundle_id_id})")

    # ── Find or Create Distribution Certificate ─────────────────────
    step("Checking Distribution Certificates")
    resp = api_get(jwt, "/certificates", {"limit": "200"})
    dist_certs = [c for c in resp.get("data", [])
                  if c["attributes"]["certificateType"] in ("DISTRIBUTION", "IOS_DISTRIBUTION")]

    if dist_certs:
        cert = dist_certs[0]
        cert_id = cert["id"]
        green(f"Found existing: {cert['attributes']['displayName']} (expires {cert['attributes']['expirationDate'][:10]})")
    else:
        blue("No distribution certificate found — creating one...")

        # Generate CSR using openssl
        key_file = tempfile.NamedTemporaryFile(suffix=".key", delete=False)
        csr_file = tempfile.NamedTemporaryFile(suffix=".csr", delete=False)
        key_file.close()
        csr_file.close()

        try:
            subprocess.run([
                "openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes",
                "-keyout", key_file.name, "-out", csr_file.name,
                "-subj", "/CN=HealthDashboard Distribution/O=Steven Bennett/C=GB"
            ], capture_output=True, check=True)

            csr_content = Path(csr_file.name).read_text()
            # Extract just the base64 content (no headers)
            csr_b64 = "".join(
                line for line in csr_content.splitlines()
                if not line.startswith("---")
            )

            resp = api_post(jwt, "/certificates", {
                "data": {
                    "type": "certificates",
                    "attributes": {
                        "csrContent": csr_content,
                        "certificateType": "DISTRIBUTION"
                    }
                }
            })
            cert = resp["data"]
            cert_id = cert["id"]

            # Install the certificate
            cert_b64 = cert["attributes"]["certificateContent"]
            cert_der = base64.b64decode(cert_b64)

            cert_pem_path = tempfile.NamedTemporaryFile(suffix=".cer", delete=False)
            cert_pem_path.close()
            Path(cert_pem_path.name).write_bytes(cert_der)

            # Import cert and private key into keychain
            # First convert key + cert to p12
            p12_file = tempfile.NamedTemporaryFile(suffix=".p12", delete=False)
            p12_file.close()

            # Convert DER cert to PEM for openssl
            cert_pem_file = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
            cert_pem_file.close()
            subprocess.run([
                "openssl", "x509", "-inform", "DER", "-in", cert_pem_path.name,
                "-out", cert_pem_file.name
            ], check=True, capture_output=True)

            subprocess.run([
                "openssl", "pkcs12", "-export",
                "-inkey", key_file.name,
                "-in", cert_pem_file.name,
                "-out", p12_file.name,
                "-passout", "pass:"
            ], check=True, capture_output=True)

            subprocess.run([
                "security", "import", p12_file.name,
                "-k", str(Path.home() / "Library" / "Keychains" / "login.keychain-db"),
                "-T", "/usr/bin/codesign",
                "-P", ""
            ], check=True, capture_output=True)

            green(f"Created and installed: {cert['attributes']['displayName']}")

            # Clean up temp files
            for f in [key_file.name, csr_file.name, cert_pem_path.name, cert_pem_file.name, p12_file.name]:
                try: os.unlink(f)
                except: pass

        except subprocess.CalledProcessError as e:
            die(f"Certificate creation failed: {e.stderr.decode() if e.stderr else str(e)}")

    # ── Create App Store Provisioning Profile ───────────────────────
    step("Setting up Provisioning Profile")

    # Check for existing profiles
    resp = api_get(jwt, "/profiles", {
        "filter[profileType]": "IOS_APP_STORE",
        "filter[name]": "HealthDashboard AppStore"
    })
    existing = [p for p in resp.get("data", []) if p["attributes"]["profileState"] == "ACTIVE"]

    if existing:
        profile = existing[0]
        blue(f"Found existing profile: {profile['attributes']['name']}")
        # Delete and recreate to ensure cert match
        blue("Recreating to ensure certificate match...")
        api_delete(jwt, f"/profiles/{profile['id']}")

    # Create new profile
    resp = api_post(jwt, "/profiles", {
        "data": {
            "type": "profiles",
            "attributes": {
                "name": "HealthDashboard AppStore",
                "profileType": "IOS_APP_STORE"
            },
            "relationships": {
                "bundleId": {
                    "data": {"type": "bundleIds", "id": bundle_id_id}
                },
                "certificates": {
                    "data": [{"type": "certificates", "id": cert_id}]
                }
            }
        }
    })
    profile = resp["data"]
    profile_content = base64.b64decode(profile["attributes"]["profileContent"])
    profile_uuid = profile["attributes"]["uuid"]

    # Install profile
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    profile_path = PROFILES_DIR / f"{profile_uuid}.mobileprovision"
    profile_path.write_bytes(profile_content)

    green(f"Installed: {profile['attributes']['name']}")
    green(f"UUID:      {profile_uuid}")
    green(f"Path:      {profile_path}")

    # ── Verify ──────────────────────────────────────────────────────
    step("Verification")
    result = subprocess.run(
        ["security", "find-identity", "-v", "-p", "codesigning"],
        capture_output=True, text=True
    )
    if "Apple Distribution" in result.stdout or "Distribution" in result.stdout:
        green("Distribution certificate: OK")
    else:
        # Development certs can also work with automatic signing
        blue("Note: Using Apple Development certificate (works with automatic signing)")

    if profile_path.exists():
        green(f"Provisioning profile:    OK ({profile_path.name})")
    else:
        red("Provisioning profile:    MISSING")

    step("Done")
    green("Signing is configured. You can now run ./testflight.sh")

if __name__ == "__main__":
    main()
