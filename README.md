# Sprint Retrospective Report Generator

Automated sprint retrospective report generation for Jira sprints, with LinearB metrics integration and Confluence publishing.

## Features

- **Jira Integration**: Fetches sprint data, issues, story points, and velocity metrics
- **LinearB Integration**: Pulls engineering metrics (Coding Time, Pickup Time, Review Time, Cycle Time)
- **Confluence Publishing**: Creates/updates rich HTML pages with charts and tables
- **Automated Charts**: Generates burndown, velocity, and LinearB metric charts using Matplotlib
- **GitHub Actions**: Automated workflow with manual or scheduled triggers

## Report Contents

The generated report includes:

| Section | Description |
|---------|-------------|
| **Overview** | Committed vs Completed SP, % Complete, Planned/Actual dates, Working days |
| **SP per Person** | Average story points per team member |
| **Team Members** | List of all sprint contributors (bots filtered) |
| **Top 5 Completed** | Highest SP completed stories/tasks |
| **Tech Debts** | Completed items under the Tech Debt epic |
| **Quality Checks** | Carry-over items, Stories without Epic, Stories without SP |
| **Retro Action Items** | Previous retrospective action items with ageing |
| **Charts** | Burndown, Velocity, Coding/Pickup/Review/Cycle Time charts |

## Prerequisites

- Python 3.9+ (for `zoneinfo` support)
- Jira Server/Data Center with Agile boards
- Confluence Server/Data Center
- LinearB API access (optional)

---

## 🚀 GitHub Actions Setup

### Step 1: Add Required Secrets

Go to **Settings → Secrets and variables → Actions → Secrets** and add:

| Secret Name | Required | Description |
|-------------|----------|-------------|
| `JIRA_PAT` | ✅ Yes | Jira Personal Access Token |
| `CONFLUENCE_API_KEY` | ✅ Yes | Confluence Personal Access Token |
| `LINEARB_TOKEN` | ❌ Optional | LinearB API key (skip if not using LinearB) |

### Step 2: Add Repository Variables

Go to **Settings → Secrets and variables → Actions → Variables** and add:

| Variable Name | Default | Description |
|---------------|---------|-------------|
| `JIRA_BASE` | `https://paypay-corp.rickcloud.jp/jira` | Jira Server base URL |
| `CONF_BASE` | `https://paypay-corp.rickcloud.jp/wiki` | Confluence Server base URL |
| `SPACE_KEY` | `ProductDevDiv` | Confluence space key |
| `PARENT_ID` | `656711904` | Confluence parent page ID |
| `BOARD_NAME` | `GVRE Board` | Jira Scrum board name |
| `SPRINT_NAME_PREFIX` | `GVRE` | Filter sprints by this prefix |
| `TD_EPIC_KEY` | `GV-2398` | Tech Debt epic key |
| `RETRO_EPIC_KEY` | `GV-2527` | Retro Action Items epic key |
| `LINEARB_TEAM_ID` | `89945` | LinearB team ID |

### Step 3: Run the Workflow

1. Go to **Actions** tab
2. Select **"Generate Sprint Retrospective Report"**
3. Click **"Run workflow"**
4. Choose options:
   - **Sprint selection**: Pick from the 5 most recent, or specify a Sprint ID
   - **Board name**: Override the default board (optional)
   - **Sprint prefix**: Override the sprint filter (optional)
5. Click **"Run workflow"**

### Workflow Options

| Option | Description |
|--------|-------------|
| Latest (most recent closed sprint) | Generates report for the most recently completed sprint |
| Second/Third/Fourth/Fifth most recent | Select older sprints |
| Specific Sprint ID | Enter the exact Jira sprint ID |

### Scheduled Runs (Optional)

To automatically generate reports, uncomment the schedule in `.github/workflows/generate-sprint-report.yml`:

```yaml
schedule:
  - cron: '30 3 * * 1'  # Every Monday at 9:00 AM IST (3:30 AM UTC)
```

---

## 💻 Local Installation

```bash
pip install -r requirements.txt
```

## Configuration (Local/Colab)

Set the following environment variables or Google Colab secrets:

### Required

| Variable | Description |
|----------|-------------|
| `JIRA_PAT` / `JIRA_API_KEY` | Jira Personal Access Token |
| `CONFLUENCE_API_KEY` / `CONF_PAT` | Confluence Personal Access Token |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `JIRA_BASE` | `https://paypay-corp.rickcloud.jp/jira` | Jira base URL |
| `CONF_BASE` | `https://paypay-corp.rickcloud.jp/wiki` | Confluence base URL |
| `SPACE_KEY` | `ProductDevDiv` | Confluence space key |
| `PARENT_ID` | `656711904` | Parent page ID for reports |
| `BOARD_NAME` | `GVRE Board` | Jira board name |
| `SPRINT_NAME_PREFIX` | `GVRE` | Sprint name prefix filter |
| `TD_EPIC_KEY` | `GV-2398` | Tech Debt epic key |
| `RETRO_EPIC_KEY` | `GV-2527` | Retro Action Items epic key |
| `LINEARB_TOKEN` / `LINEARB_API_KEY` | - | LinearB API key |
| `LINEARB_TEAM_ID` | `89945` | LinearB team ID |

### CI Mode Variables

| Variable | Description |
|----------|-------------|
| `CI` | Set to `true` to enable non-interactive mode |
| `SPRINT_INDEX` | 1-5, selects from the N most recent closed sprints |
| `SPRINT_ID` | Specific sprint ID (overrides SPRINT_INDEX) |

## Usage

### Interactive Mode (Local)

```bash
python sprint_report.py

# The script will:
# 1. Connect to Jira and Confluence
# 2. List the 5 most recent closed sprints
# 3. Prompt you to select a sprint
# 4. Generate and publish the report to Confluence
```

### Non-Interactive Mode (CI/CD)

```bash
# Run with environment variables
export CI=true
export SPRINT_INDEX=1  # Most recent sprint
python sprint_report.py

# Or with specific sprint ID
export CI=true
export SPRINT_ID=12345
python sprint_report.py
```

---

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Jira Server   │────▶│  Sprint Report  │────▶│   Confluence    │
│   (Agile API)   │     │    Generator    │     │    (REST API)   │
└─────────────────┘     └────────┬────────┘     └─────────────────┘
                                 │
                        ┌────────▼────────┐
                        │    LinearB      │
                        │    (v2 API)     │
                        └─────────────────┘
```

## Key Components

1. **HTTP Layer**: Retry-enabled requests with exponential backoff
2. **Field Discovery**: Automatic Jira custom field resolution
3. **Chart Generation**: Matplotlib-based chart rendering
4. **HTML Builder**: Confluence storage format generation
5. **Attachment Handler**: Robust chart upload with retry logic
6. **CI Mode**: Non-interactive operation for GitHub Actions

## Status Color Legend

| Status | Color |
|--------|-------|
| Done | Dark Green (#006644) |
| In Progress | Amber (#FF8B00) |
| To Do | Dark Grey (#42526E) |
| Reviewing | Dark Blue (#0052CC) |
| Not Needed | Light Green (#57D9A3) |

## Version

Current: `2025-Nov-10r1`

## License

Internal use only.
