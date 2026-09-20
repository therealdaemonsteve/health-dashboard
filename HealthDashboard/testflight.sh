#!/usr/bin/env bash
set -euo pipefail

# ── Source .env if present ──────────────────────────────────────────
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
[[ -f "$ENV_FILE" ]] && { set -a; source "$ENV_FILE"; set +a; }

# ── Config ──────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$SCRIPT_DIR/HealthDashboard.xcodeproj"
SCHEME="HealthDashboard"
TEAM_ID="${HD_DEVELOPMENT_TEAM:?Run setup.sh first}"
EXPORT_OPTIONS="$SCRIPT_DIR/ExportOptions.plist"

ARCHIVE_DIR="/tmp/HealthDashboard_archives"
ARCHIVE_PATH="$ARCHIVE_DIR/HealthDashboard.xcarchive"
EXPORT_DIR="$ARCHIVE_DIR/export"

# ── Helpers ─────────────────────────────────────────────────────────
red()   { printf '\033[1;31m%s\033[0m\n' "$*"; }
green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
blue()  { printf '\033[1;34m%s\033[0m\n' "$*"; }
step()  { printf '\n\033[1;36m── %s ──\033[0m\n' "$*"; }

die() { red "ERROR: $*" >&2; exit 1; }

# ── Parse args ──────────────────────────────────────────────────────
BUMP_TYPE="build"
DRY_RUN=false

usage() {
    cat <<'EOF'
Usage: testflight.sh [OPTIONS]

Options:
  --patch       Bump patch version (1.0.0 → 1.0.1) and reset build to 1
  --minor       Bump minor version (1.0.0 → 1.1.0) and reset build to 1
  --major       Bump major version (1.0.0 → 2.0.0) and reset build to 1
  --build       Increment build number only (default)
  --dry-run     Archive only, skip upload
  -h, --help    Show this help

Requires:
  - Xcode signed in with your Apple Developer account
    (Xcode → Settings → Accounts → add Apple ID, ensure Distribution cert exists)
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --patch)   BUMP_TYPE="patch"; shift ;;
        --minor)   BUMP_TYPE="minor"; shift ;;
        --major)   BUMP_TYPE="major"; shift ;;
        --build)   BUMP_TYPE="build"; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        -h|--help) usage ;;
        *) die "Unknown option: $1" ;;
    esac
done

# ── Preflight ───────────────────────────────────────────────────────
step "Preflight"

[[ -f "$PROJECT/project.pbxproj" ]] || die "Project not found at $PROJECT"
[[ -f "$EXPORT_OPTIONS" ]]          || die "ExportOptions.plist not found"
command -v xcodebuild >/dev/null    || die "xcodebuild not found"

green "Team: $TEAM_ID"
green "Checks passed"

# ── Version ─────────────────────────────────────────────────────────
step "Version"

PBXPROJ="$PROJECT/project.pbxproj"

CURRENT_VERSION=$(grep -m1 'MARKETING_VERSION' "$PBXPROJ" | sed 's/.*= *//;s/ *;.*//')
CURRENT_BUILD=$(grep -m1 'CURRENT_PROJECT_VERSION' "$PBXPROJ" | sed 's/.*= *//;s/ *;.*//')

blue "Current: v${CURRENT_VERSION} (${CURRENT_BUILD})"

IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT_VERSION"
case "$BUMP_TYPE" in
    major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0; NEW_BUILD=1 ;;
    minor) MINOR=$((MINOR + 1)); PATCH=0; NEW_BUILD=1 ;;
    patch) PATCH=$((PATCH + 1)); NEW_BUILD=1 ;;
    build) NEW_BUILD=$((CURRENT_BUILD + 1)) ;;
esac

NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}"
: "${NEW_BUILD:=$((CURRENT_BUILD + 1))}"

green "New:     v${NEW_VERSION} (${NEW_BUILD})"

sed -i '' "s/MARKETING_VERSION = ${CURRENT_VERSION}/MARKETING_VERSION = ${NEW_VERSION}/g" "$PBXPROJ"
sed -i '' "s/CURRENT_PROJECT_VERSION = ${CURRENT_BUILD}/CURRENT_PROJECT_VERSION = ${NEW_BUILD}/g" "$PBXPROJ"

# ── Clean ───────────────────────────────────────────────────────────
step "Clean"
rm -rf "$ARCHIVE_DIR"
mkdir -p "$ARCHIVE_DIR"

# ── Archive ─────────────────────────────────────────────────────────
step "Archive"

set +e
ARCHIVE_LOG=$(xcodebuild archive \
    -project "$PROJECT" \
    -scheme "$SCHEME" \
    -destination "generic/platform=iOS" \
    -archivePath "$ARCHIVE_PATH" \
    -allowProvisioningUpdates \
    CODE_SIGN_STYLE=Automatic \
    DEVELOPMENT_TEAM="$TEAM_ID" \
    CURRENT_PROJECT_VERSION="$NEW_BUILD" \
    MARKETING_VERSION="$NEW_VERSION" \
    2>&1)
ARCHIVE_EXIT=$?
set -e

if [[ $ARCHIVE_EXIT -ne 0 ]]; then
    echo "$ARCHIVE_LOG" | grep -E "error:" | head -5

    # Revert version on failure
    sed -i '' "s/MARKETING_VERSION = ${NEW_VERSION}/MARKETING_VERSION = ${CURRENT_VERSION}/g" "$PBXPROJ"
    sed -i '' "s/CURRENT_PROJECT_VERSION = ${NEW_BUILD}/CURRENT_PROJECT_VERSION = ${CURRENT_BUILD}/g" "$PBXPROJ"
    red "Version reverted to v${CURRENT_VERSION} (${CURRENT_BUILD})"

    if echo "$ARCHIVE_LOG" | grep -q "No Account for Team"; then
        echo ""
        red "Xcode doesn't have your Apple Developer account."
        blue "One-time fix:"
        blue "  1. Open Xcode → Settings → Accounts"
        blue "  2. Click '+' → Apple ID → sign in"
        blue "  3. Click your team → 'Manage Certificates'"
        blue "  4. Ensure 'Apple Distribution' cert exists (click + if not)"
        blue "  5. Re-run this script"
    fi
    exit 1
fi

[[ -d "$ARCHIVE_PATH" ]] || die "Archive produced no output"
green "Archive: $ARCHIVE_PATH"

# ── Export & Upload ─────────────────────────────────────────────────
if $DRY_RUN; then
    step "Dry run — skipping upload"
    green "Archive ready at: $ARCHIVE_PATH"
    blue "Run without --dry-run to upload to TestFlight"
else
    step "Export & Upload to TestFlight"

    set +e
    EXPORT_LOG=$(xcodebuild -exportArchive \
        -archivePath "$ARCHIVE_PATH" \
        -exportOptionsPlist "$EXPORT_OPTIONS" \
        -exportPath "$EXPORT_DIR" \
        -allowProvisioningUpdates \
        2>&1)
    EXPORT_EXIT=$?
    set -e

    if [[ $EXPORT_EXIT -ne 0 ]]; then
        echo "$EXPORT_LOG" | grep -E "error:" | head -5
        echo "$EXPORT_LOG" | tail -5
        die "Export/upload failed (exit $EXPORT_EXIT)"
    fi

    # Show upload progress
    echo "$EXPORT_LOG" | grep -E "Upload|EXPORT" | tail -3
    green "Upload complete!"
fi

# ── Summary ─────────────────────────────────────────────────────────
step "Done"
if $DRY_RUN; then
    green "v${NEW_VERSION} (${NEW_BUILD}) archived"
else
    green "v${NEW_VERSION} (${NEW_BUILD}) → TestFlight"
fi
echo ""
