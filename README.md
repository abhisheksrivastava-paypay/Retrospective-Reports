# Sprint Retrospective Report Generator

Automated sprint retrospective report generation for Jira sprints, with LinearB metrics integration and Confluence publishing.

## Features

- **Jira Integration**: Fetches sprint data, issues, story points, and velocity metrics
- **LinearB Integration**: Pulls engineering metrics (Coding Time, Pickup Time, Review Time, Cycle Time)
- **Confluence Publishing**: Creates/updates rich HTML pages with charts and tables
- **Automated Charts**: Generates burndown, velocity, and LinearB metric charts using Matplotlib

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

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Set the following environment variables or Google Colab secrets:

### Required

| Variable | Description |
|----------|-------------|
| `JIRA_PAT` / `Jira_Token` | Jira Personal Access Token |
| `CONF_PAT` / `Confluence_Token` | Confluence Personal Access Token |

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
| `LINEARB_TOKEN` / `LinearB_Token` | - | LinearB API key |
| `LINEARB_TEAM_ID` | `89945` | LinearB team ID |

## Usage

```bash
# Run the script
python sprint_report.py

# The script will:
# 1. Connect to Jira and Confluence
# 2. List the 5 most recent closed sprints
# 3. Prompt you to select a sprint
# 4. Generate and publish the report to Confluence
```

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

