# Health Dashboard

A personal health tracking system with three components:

1. **MCP Server** - A Model Context Protocol server that gives Claude (Desktop & claude.ai) full read/write access to your health data. Claude becomes your health coach - tracking biomarkers, setting goals, analysing trends, and managing training phases.
2. **iOS App (HealthDashboard)** - A SwiftUI app that syncs Apple Health data (heart rate, HRV, VO2 max, steps, weight, etc.) to the MCP server via its Lambda API.
3. **Web Dashboard** - A self-contained HTML dashboard hosted on S3/CloudFront for viewing biomarker charts and trends in a browser.

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌──────────┐
│  Claude.ai  │────▶│  Lambda (MCP)    │────▶│  S3      │
│  or Desktop │◀────│  mcp_handler.py  │◀────│  (data)  │
└─────────────┘     └──────────────────┘     └──────────┘
                           ▲                       │
┌─────────────┐            │                       ▼
│  iOS App    │────────────┘               ┌──────────────┐
│  HealthKit  │                            │  CloudFront  │
└─────────────┘                            │  dashboard   │
                                           └──────────────┘
┌──────────────────┐
│  Claude Desktop  │  (local stdio MCP)
│  mcp_server.py   │──▶ reads S3 directly
└──────────────────┘
```

**Data flow:**
- Blood test results are normalised from provider exports (manual.co, Bluecrest, Medichecks) via `build_unified_data.py`
- Apple Health metrics sync from the iOS app to S3 via the MCP Lambda
- Claude reads and writes all data through MCP tools (60+ tools for biomarkers, coaching, nutrition, lifting, phases, analytics)
- The web dashboard is a static HTML file with embedded data, served via CloudFront with basic auth

## Prerequisites

- **AWS CLI** configured with credentials (`aws configure`)
- **Python 3.10+** with `boto3` and `fastmcp` (`pip install boto3 fastmcp`)
- **Xcode 15+** (for the iOS app)
- **jq** (`brew install jq`)
- A **Claude** subscription (Desktop for local MCP, or Pro/Team for claude.ai remote MCP)

## Setup (Step by Step)

> **For Claude:** Follow these steps in order. Each deploy script creates a `.deploy-config` file that subsequent scripts read from.

### 1. Clone and set up Python

```bash
git clone https://github.com/rancidmonkey/health-dashboard.git
cd health-dashboard
python3 -m venv .venv
source .venv/bin/activate
pip install boto3 fastmcp
```

### 2. Deploy AWS infrastructure

The deploy scripts must be run in order. Each one appends config to `aws-deploy/.deploy-config`.

#### 2a. S3 + CloudFront (web dashboard hosting)

```bash
cd aws-deploy
# Edit deploy.sh first - set AUTH_USER and AUTH_PASS for your dashboard
vim deploy.sh
./deploy.sh
```

This creates:
- A private S3 bucket
- A CloudFront distribution with basic auth
- Origin Access Control for secure S3 access

#### 2b. Claude proxy Lambda (for web dashboard AI features)

```bash
./deploy-lambda.sh
# You'll be prompted for your Anthropic API key
```

This creates:
- A Lambda function that proxies Claude API calls from the dashboard
- A public Function URL
- Bearer token auth

#### 2c. MCP server Lambda (the main API)

```bash
./deploy-mcp.sh
```

This creates:
- The MCP server Lambda (`bloodwork-mcp-server`)
- A public Function URL that speaks MCP JSON-RPC 2.0
- Bearer token for authentication

#### 2d. OAuth for Claude.ai (optional - enables claude.ai remote MCP)

```bash
./update-mcp-env.sh
```

This adds OAuth 2.0 support so you can connect the MCP server as a remote integration in Claude.ai settings. You'll get a PIN to enter in the browser during OAuth flow.

### 3. Prepare your health data

#### Blood test data

Place your blood test exports as JSON files in the project root:

- `manual_blood_tests.json` - from manual.co
- `bluecrest_blood_tests.json` - from Bluecrest
- `mymedichecks_blood_tests.json` - from Medichecks

Then run the normaliser:

```bash
python3 build_unified_data.py
```

This creates `bloodwork_data.json` with all biomarkers normalised to canonical names.

If you don't have blood test data yet, you can create an empty seed file:

```json
{
  "metadata": { "version": "1.0", "updated_at": "" },
  "biomarkers": [],
  "measurements": [],
  "events": [],
  "overview": {}
}
```

Save this as `bloodwork_data.json` and also create an empty `events.json`:

```json
[]
```

#### Upload data to S3

```bash
./aws-deploy/update.sh
```

This uploads `dashboard.html`, `bloodwork_data.json`, `events.json`, `lifting.json`, etc. to S3 and invalidates the CloudFront cache.

### 4. Connect Claude Desktop (local MCP - optional)

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "health-dashboard": {
      "command": "/path/to/health-dashboard/.venv/bin/python",
      "args": ["/path/to/health-dashboard/mcp_server.py"],
      "env": {
        "S3_BUCKET": "your-bucket-name",
        "S3_REGION": "eu-west-2"
      }
    }
  }
}
```

The bucket name is in `aws-deploy/.deploy-config` after running `deploy.sh`.

### 5. Connect Claude.ai (remote MCP)

1. Go to Claude.ai → Settings → Integrations → Add remote MCP server
2. Enter URL: `{MCP_FUNC_URL}mcp` (the URL from `deploy-mcp.sh` output, with `/mcp` appended)
3. If you ran `update-mcp-env.sh`, you'll go through OAuth and enter the PIN
4. Otherwise, use the bearer token from `.deploy-config`

### 6. Build the iOS app

Open `HealthDashboard/HealthDashboard.xcodeproj` in Xcode.

Before building:

1. **Set your config** in `HealthDashboard/HealthDashboard/Config/AppConstants.swift`:
   - `userId` - your identifier (used as a namespace in S3)
   - `apiBaseURL` - your MCP Lambda Function URL (from `deploy-mcp.sh` output)

2. **Set your signing team** in Xcode project settings (Signing & Capabilities)

3. **Enable HealthKit** capability (should already be in the entitlements file)

4. Build and run on a physical device (HealthKit requires a real device)

The app:
- Reads Apple Health data (resting HR, HRV, VO2 max, steps, weight, body fat, etc.)
- Batches and uploads to the MCP Lambda, which stores in S3
- Supports background refresh for continuous sync
- Shows sync status, biomarker charts, coaching goals, and training phases

### 7. TestFlight distribution (optional)

Create `HealthDashboard/.testflight.env`:

```
ASC_ISSUER_ID=your_app_store_connect_issuer_id
```

Then run:

```bash
cd HealthDashboard
./testflight.sh
```

## Project structure

```
health-dashboard/
├── mcp_server.py              # Local MCP server (Claude Desktop, stdio)
├── build_unified_data.py      # Blood test data normaliser
├── build_insights.py          # AI insights generator
├── dashboard.html             # Web dashboard (generated, do not edit directly)
│
├── aws-deploy/
│   ├── deploy.sh              # Step 1: S3 + CloudFront setup
│   ├── deploy-lambda.sh       # Step 2: Claude proxy Lambda
│   ├── deploy-mcp.sh          # Step 3: MCP server Lambda
│   ├── update-mcp-env.sh      # Step 4: OAuth setup (optional)
│   ├── update.sh              # Upload files to S3 + update Lambda code
│   ├── teardown.sh            # Delete all AWS resources
│   └── lambda/
│       ├── mcp_handler.py     # MCP JSON-RPC handler (the big one - 167KB)
│       └── proxy.py           # Claude API proxy for dashboard
│
├── HealthDashboard/           # iOS app
│   ├── HealthDashboard.xcodeproj/
│   └── HealthDashboard/
│       ├── HealthDashboardApp.swift
│       ├── Config/AppConstants.swift    # ← Set your URL and user ID here
│       ├── Models/                      # Data models
│       ├── Services/                    # MCP client, HealthKit, sync
│       ├── Components/                  # Reusable UI (charts, badges)
│       └── Views/                       # SwiftUI views (tabs, detail screens)
│
└── data/                      # Your health data exports (gitignored)
```

## MCP Tools

The MCP server exposes 60+ tools organised by category:

| Category | Tools | Description |
|----------|-------|-------------|
| **Biomarkers** | `list_biomarkers`, `get_biomarker_detail`, `get_measurements`, `add_measurement`, `create_biomarker` | CRUD for health metrics |
| **Overview** | `get_health_overview`, `get_flagged_biomarkers`, `get_health_scores` | Dashboard summaries |
| **Coaching** | `get_coaching_brief`, `add_goal`, `update_goal`, `add_action_item`, `add_coaching_note` | Goal tracking and coaching |
| **Phases** | `get_phases`, `add_phase`, `update_phase`, `get_checklist`, `toggle_checklist` | Training phase management with supplement/med checklists |
| **Nutrition** | `log_nutrition`, `get_nutrition`, `import_macrofactor_nutrition` | Daily macro tracking with TDEE calculation |
| **Lifting** | `get_lifting`, `import_macrofactor` | Strength training logs with e1RM tracking |
| **Analytics** | `detect_trends`, `compute_correlation`, `analyse_event_impact`, `get_rolling_averages` | Statistical analysis |
| **Apple Health** | `import_apple_health` | Bulk import from the iOS app |
| **Events** | `get_events`, `add_event` | Health event tracking (TRT, supplements, scans) |
| **Insights** | `generate_insights`, `update_overview` | AI-generated analysis |

## Tearing down

To remove all AWS resources:

```bash
cd aws-deploy
./teardown.sh
```

This deletes the S3 bucket, CloudFront distribution, Lambda functions, and IAM roles.

## Data providers

The `build_unified_data.py` script normalises blood test data from:

- **manual.co** - UK blood testing service
- **Bluecrest Health** - UK health screening
- **Medichecks** - UK blood testing

To add a new provider, add a mapping section to `build_unified_data.py` following the existing patterns. The script maps provider-specific biomarker names to canonical names and normalises units.

## Notes

- All health data is stored in S3 as JSON files (`bloodwork_data.json`, `events.json`, `nutrition.json`, `lifting.json`, `coaching.json`, `phases.json`)
- The MCP Lambda reads/writes these files directly - there is no database
- The web dashboard embeds a copy of `bloodwork_data.json` as a fallback, with live data fetched from S3 via the proxy Lambda
- The iOS app authenticates to the MCP Lambda using the bearer token from `.deploy-config`
