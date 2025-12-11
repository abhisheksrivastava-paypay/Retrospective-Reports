"""

SCRIPT: GV/GVRE Sprint Report → Confluence

VERSION: 2025-Nov-10r1  (with UX updates applied on 2025-11-10)

"""



# ===================== IMPORTS =====================

import os, sys, re, io, time, json, datetime

from zoneinfo import ZoneInfo

import requests

from requests.exceptions import HTTPError, Timeout, ConnectionError



# Matplotlib: one chart per fig; explicit colors only where asked

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

import matplotlib.dates as mdates  # (used by date formatting helpers if needed)



# ===================== VERSION =====================

SCRIPT_VERSION = "2025-Nov-10r1"



# ===================== CONFIG / SECRETS =====================

def _get_secret(colab_key: str, env_key: str) -> str:

    """Prefer Colab secrets; fallback to env."""

    try:

        from google.colab import userdata  # type: ignore

        v = userdata.get(colab_key)

        if v: return v

    except Exception:

        pass

    return os.getenv(env_key, "")



# Bases

JIRA_BASE = os.getenv("JIRA_BASE", "https://paypay-corp.rickcloud.jp/jira").rstrip("/")

CONF_BASE = os.getenv("CONF_BASE", "https://paypay-corp.rickcloud.jp/wiki").rstrip("/")



# Tokens (baseline secret names kept)

JIRA_PAT  = _get_secret("Jira_Token", "JIRA_PAT")

CONF_PAT  = _get_secret("Confluence_Token", "CONF_PAT")

LINEARB_TOKEN = _get_secret("LinearB_Token", "LINEARB_TOKEN")



if not JIRA_PAT or not CONF_PAT:

    print("Missing Jira/Confluence tokens (Jira_Token / Confluence_Token). Set Colab Secrets or env.", file=sys.stderr)

    sys.exit(1)



# Confluence settings

SPACE_KEY = os.getenv("SPACE_KEY", "ProductDevDiv")

PARENT_ID = os.getenv("PARENT_ID", "656711904")  # Retrospectives parent in your space



# Jira board / sprint naming

BOARD_NAME         = os.getenv("BOARD_NAME", "GVRE Board")   # resolve by exact name

SPRINT_NAME_PREFIX = os.getenv("SPRINT_NAME_PREFIX", "GVRE") # list closed sprints starting with this



# Tech-debt epic and story types

TD_EPIC_KEY = os.getenv("TD_EPIC_KEY", "GV-2398")

STORY_TYPES = [s.strip() for s in os.getenv(

    "STORY_TYPES", "Story,Task,Spike,Enabler Story,Technical Story,User Story"

).split(",") if s.strip()]



# >>> PATCH: Retro Action Items epic key

RETRO_EPIC_KEY = os.getenv("RETRO_EPIC_KEY", "GV-2527")



# Quality/audit filters

AUDIT_ISSUE_TYPES = {"story", "task"}

AUDIT_EXCLUDED_STATUS_NAMES = {"cancelled", "canceled", "not needed"}  # case-insensitive



# UX

SUMMARY_MAX_CHARS = int(os.getenv("SUMMARY_MAX_CHARS", "120"))

EXCLUDE_ASSIGNEE_REGEX = os.getenv(

    "EXCLUDE_ASSIGNEE_REGEX",

    r"(automation|bot|svc|service|ci|pipeline|system)"

)

DEFAULT_LABELS = ["retro", "board-gvre"]



# Requests

TIMEOUT_SECS = 60

RETRY_MAX_TRIES = 4

RETRY_BASE_DELAY = 0.8



# Headers

J_HEADERS = {"Authorization": f"Bearer {JIRA_PAT}", "Accept": "application/json"}

C_HEADERS = {"Authorization": f"Bearer {CONF_PAT}", "Accept": "application/json"}



# Jira fields discovered at runtime

EPIC_LINK_FIELD_ID = None

STORY_POINTS_FIELD_ID = None

STORY_POINTS_FIELD_IDS = []  # all numeric SP fields we find

SP_CLAUSE_TOKENS = []        # cf[12345] tokens (we will NOT use these in server-side JQL to avoid 400s)



# LinearB

LINEARB_BASE = os.getenv("LINEARB_BASE", "https://public-api.linearb.io").rstrip("/")

LINEARB_TEAM_ID = int(os.getenv("LINEARB_TEAM_ID", "89945"))  # Gift Voucher Reward Engine

# Reference IDs: Gift Voucher — 44360; Gift Voucher Reward Engine — 89945



# Attachment filenames

BURNDOWN_FN = "burndown.png"

VELOCITY_FN = "velocity.png"

CODING_FN   = "coding_p50.png"

PICKUP_FN   = "pickup_p50.png"

REVIEW_FN   = "review_p50.png"

CYCLE_FN    = "cycle_p50.png"



# ===================== HTTP HELPERS =====================

def _sleep_backoff(i): time.sleep(min(RETRY_BASE_DELAY * (2**(i-1)), 6.0))



def request_with_retries(method, url, headers=None, params=None, data=None, json_payload=None,

                         timeout=TIMEOUT_SECS, expect_json=True):

    last = None; body = None

    for i in range(1, RETRY_MAX_TRIES+1):

        try:

            r = requests.request(method.upper(), url, headers=headers or {}, params=params or {},

                                 data=data, json=json_payload, timeout=timeout)

            try: body = r.text

            except Exception: body = None

            r.raise_for_status()

            return r.json() if expect_json else r

        except (HTTPError, Timeout, ConnectionError, RuntimeError, ValueError) as e:

            last = e

            msg = f"[warn] {method.upper()} {url} failed (attempt {i}/{RETRY_MAX_TRIES}): {e}"

            if body: msg += f"\nbody: {body[:800]}"

            print(msg, file=sys.stderr)

            if i < RETRY_MAX_TRIES:

                _sleep_backoff(i)

    raise RuntimeError(f"Request failed after retries: {method.upper()} {url} :: {last}")



def get_json(url, headers=None, params=None):

    return request_with_retries("GET", url, headers=headers or {}, params=params, expect_json=True)



def post_json(url, headers=None, payload=None):

    return request_with_retries("POST", url, headers={**(headers or {}), "Content-Type":"application/json"},

                                json_payload=payload or {}, expect_json=True)



def put_json(url, headers=None, payload=None):

    return request_with_retries("PUT", url, headers={**(headers or {}), "Content-Type":"application/json"},

                                json_payload=payload or {}, expect_json=True)



# ===================== UTILS =====================

def escape_html(s):

    return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")



def parse_jira_date(s):

    if not s: return None

    try:

        if isinstance(s, str) and s.endswith("+0000"):

            s = s[:-5] + "+00:00"

        return datetime.datetime.fromisoformat(s)

    except Exception:

        pass

    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y/%m/%d %H:%M"):

        try: return datetime.datetime.strptime(s, fmt)

        except Exception: continue

    return None



def to_ist_str(dt_str):

    dt = parse_jira_date(dt_str)

    if not dt: return "-"

    try:

        return dt.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %H:%M IST")

    except Exception:

        return "-"



def safe_float(x, default=0.0):

    try:

        if x is None: return default

        return float(x)

    except Exception:

        return default



def truncate_text(text, max_chars=SUMMARY_MAX_CHARS):

    t = (text or "").strip()

    if len(t) <= max_chars: return t

    cut = t[:max_chars].rsplit(" ", 1)[0]

    return (cut or t[:max_chars]).rstrip() + "…"



def minutes_to_dhm(total_minutes):

    if total_minutes is None: return "-"

    try: m = int(round(float(total_minutes)))

    except Exception: return "-"

    d, rem = divmod(m, 1440); h, mm = divmod(rem, 60)

    parts = []

    if d: parts.append(f"{d}d")

    if h or d: parts.append(f"{h}h")

    parts.append(f"{mm}m")

    return " ".join(parts)



def working_days_inclusive(start_dt, end_dt) -> int:

    """

    Working days (Mon–Fri) inclusive of both start and end dates.

    Accepts datetime or date; returns 0 if invalid or end < start.

    """

    if not start_dt or not end_dt:

        return 0

    s = start_dt.date() if isinstance(start_dt, datetime.datetime) else start_dt

    e = end_dt.date() if isinstance(end_dt, datetime.datetime) else end_dt

    if e < s:

        return 0

    d, cnt = s, 0

    one = datetime.timedelta(days=1)

    while d <= e:

        if d.weekday() < 5:

            cnt += 1

        d += one

    return cnt



def working_days_since(created_dt, today_date=None) -> int:

    """

    Working days (Mon–Fri) since creation, EXCLUDING the creation day, up to 'today_date'.

    """

    if not created_dt:

        return 0

    c = created_dt.date() if isinstance(created_dt, datetime.datetime) else created_dt

    if today_date is None:

        try:

            today_date = datetime.datetime.now(ZoneInfo("Asia/Kolkata")).date()

        except Exception:

            today_date = datetime.date.today()

    if today_date <= c:

        return 0

    d, cnt = c + datetime.timedelta(days=1), 0

    one = datetime.timedelta(days=1)

    while d <= today_date:

        if d.weekday() < 5:

            cnt += 1

        d += one

    return cnt



# ===================== FIELD DISCOVERY =====================

def discover_field_ids():

    """Find Epic Link and Story Points fields. Keep all SP variants and cf[...] tokens for logging."""

    global EPIC_LINK_FIELD_ID, STORY_POINTS_FIELD_ID, STORY_POINTS_FIELD_IDS, SP_CLAUSE_TOKENS

    fields = get_json(f"{JIRA_BASE}/rest/api/2/field", J_HEADERS)



    EPIC_LINK_FIELD_ID = next((f["id"] for f in fields if (f.get("name") or "").strip().lower() == "epic link"), None)



    cands = [f for f in fields

             if "story point" in (f.get("name","").lower())

             and (f.get("schema") or {}).get("type") == "number"

             and f.get("id","").startswith("customfield_")]



    pref = None

    for nm in ("Story Points", "Story Point"):

        for f in cands:

            if (f.get("name") or "").strip().lower() == nm.lower():

                pref = f; break

        if pref: break

    STORY_POINTS_FIELD_ID = (pref or (cands[0] if cands else {})).get("id")



    STORY_POINTS_FIELD_IDS = []

    seen = set()

    if STORY_POINTS_FIELD_ID:

        STORY_POINTS_FIELD_IDS.append(STORY_POINTS_FIELD_ID); seen.add(STORY_POINTS_FIELD_ID)

    for f in cands:

        fid = f["id"]

        if fid not in seen:

            STORY_POINTS_FIELD_IDS.append(fid); seen.add(fid)



    SP_CLAUSE_TOKENS = []

    for f in cands:

        cid = (f.get("schema") or {}).get("customId")

        if cid is not None:

            SP_CLAUSE_TOKENS.append(f"cf[{cid}]")



    # Log for future reference (helps when regenerating)

    print("[info] Discovered fields:",

          "\n  Epic Link:", EPIC_LINK_FIELD_ID,

          "\n  Primary SP field:", STORY_POINTS_FIELD_ID,

          "\n  All SP fields:", STORY_POINTS_FIELD_IDS,

          "\n  SP cf[...] tokens:", SP_CLAUSE_TOKENS)



    if not EPIC_LINK_FIELD_ID:

        print("[fatal] Could not resolve Epic Link field ID.", file=sys.stderr); sys.exit(2)

    if not STORY_POINTS_FIELD_IDS:

        print("[fatal] Could not resolve Story Points fields.", file=sys.stderr); sys.exit(2)



# ===================== BOARD / SPRINTS =====================

def list_scrum_boards():

    boards, start = [], 0

    while True:

        data = get_json(f"{JIRA_BASE}/rest/agile/1.0/board", J_HEADERS,

                        {"type":"scrum","startAt":start,"maxResults":50})

        vals = data.get("values", []) or []

        boards.extend(vals)

        if not vals or len(vals) < 50: break

        start += len(vals)

    return boards



def find_board_by_name(name):

    boards = list_scrum_boards()

    exact = [b for b in boards if (b.get("name") or "").strip() == name.strip()]

    if exact: return exact[0]

    ci = [b for b in boards if name.lower() in (b.get("name","").lower())]

    if len(ci) == 1: return ci[0]

    if ci:

        print("Multiple boards matched. Refine BOARD_NAME:", file=sys.stderr)

        for b in ci: print(f"- {b.get('name')} (ID {b.get('id')})", file=sys.stderr)

        sys.exit(2)

    print(f'Board "{name}" not found.', file=sys.stderr); sys.exit(2)



def list_all_closed_sprints(board_id):

    seen, sprints, start = set(), [], 0

    while True:

        data = get_json(f"{JIRA_BASE}/rest/agile/1.0/board/{board_id}/sprint", J_HEADERS,

                        {"state":"closed","startAt":start,"maxResults":50})

        vals = data.get("values", []) or []

        for s in vals:

            sid = s.get("id")

            if sid in seen: continue

            seen.add(sid); sprints.append(s)

        if not vals or len(vals) < 50: break

        start += len(vals)

    return sprints



def sprint_report(board_id, sprint_id):

    return get_json(f"{JIRA_BASE}/rest/greenhopper/1.0/rapid/charts/sprintreport",

                    J_HEADERS, {"rapidViewId": board_id, "sprintId": sprint_id})



def velocity_json(board_id):

    return get_json(f"{JIRA_BASE}/rest/greenhopper/1.0/rapid/charts/velocity",

                    J_HEADERS, {"rapidViewId": board_id})



# ===================== ISSUE HELPERS =====================

_ISSUE_FIELDS_CACHE = {}



def get_issue_fields(issue_key, fields):

    url = f"{JIRA_BASE}/rest/api/2/issue/{issue_key}"

    params = {"fields": ",".join(fields)} if fields else None

    try:

        return (get_json(url, J_HEADERS, params).get("fields") or {})

    except Exception as e:

        print(f"[warn] Per-issue fetch failed for {issue_key}: {e}", file=sys.stderr)

        return {}



def _get_issue_fields_multi(issue_key: str, fields: list[str]) -> dict:

    if issue_key in _ISSUE_FIELDS_CACHE:

        return _ISSUE_FIELDS_CACHE[issue_key]

    _ISSUE_FIELDS_CACHE[issue_key] = get_issue_fields(issue_key, fields) or {}

    return _ISSUE_FIELDS_CACHE[issue_key]



def _status_category_key(issue):

    try: return ((issue.get("fields") or {}).get("status") or {}).get("statusCategory", {}).get("key")

    except Exception: return None



def _issuetype_name(issue):

    try: return ((issue.get("fields") or {}).get("issuetype") or {}).get("name")

    except Exception: return None



def _field_sp_from_fields(fields_obj: dict):

    for fid in STORY_POINTS_FIELD_IDS:

        if fid in fields_obj and fields_obj[fid] is not None:

            try: return float(fields_obj[fid])

            except Exception: pass

    return None



# ===================== SPRINT MATH =====================

def build_sp_map_from_report(report):

    """Map issueKey → SP from the sprint report's estimateStatistic."""

    contents = report.get("contents", {}) or {}

    completed = contents.get("completedIssues", []) or []

    not_completed = contents.get("issuesNotCompletedInCurrentSprint", []) or []

    def sp(est):

        try:

            v = (est or {}).get("statFieldValue") or {}

            if isinstance(v.get("value"), (int,float)): return float(v["value"])

            if v.get("text"): return float(v["text"])

        except Exception: pass

        return 0.0

    mp = {}

    for it in completed + not_completed:

        try: mp[it.get("key")] = sp(it.get("estimateStatistic"))

        except Exception: continue

    return mp



def get_committed_completed_from_velocity(board_id, sprint_id):

    """Exact numbers Jira shows on the Velocity chart for that sprint."""

    try:

        vj = velocity_json(board_id)

        entries = vj.get("velocityStatEntries", {}) or {}

        entry = entries.get(str(sprint_id)) or entries.get(int(sprint_id))  # keys are strings

        if entry:

            est = float((entry.get("estimated") or {}).get("value") or 0)

            comp = float((entry.get("completed") or {}).get("value") or 0)

            return est, comp

    except Exception as e:

        print("[warn] Velocity endpoint read failed; fallback to sprint report math:", e, file=sys.stderr)

    return None, None



def compute_committed_completed_from_report(report):

    """Fallback computation if Velocity endpoint is unavailable."""

    contents = report.get("contents", {}) or {}

    completed = contents.get("completedIssues", []) or []

    not_completed = contents.get("issuesNotCompletedInCurrentSprint", []) or []

    added_mid = set((contents.get("issueKeysAddedDuringSprint") or {}).keys())



    def sp(est):

        try:

            v = (est or {}).get("statFieldValue") or {}

            if isinstance(v.get("value"), (int,float)): return float(v["value"])

            if v.get("text"): return float(v["text"])

        except Exception: pass

        return 0.0



    completed_sp = sum(sp(it.get("estimateStatistic")) for it in completed)



    committed_sp = 0.0

    for it in completed:

        if it.get("key") not in added_mid:

            committed_sp += sp(it.get("estimateStatistic"))

    for it in not_completed:

        if it.get("key") not in added_mid:

            committed_sp += sp(it.get("estimateStatistic"))



    scope_change_count = len(added_mid)

    carry_over_count   = len(not_completed)

    return committed_sp, completed_sp, scope_change_count, carry_over_count



def agile_sprint_issues_paginated(sprint_id, jql_fragment, fields, max_pages=50, page_size=100):

    """

    Use agile endpoint for sprint issues. If server-side JQL fails (e.g., due to custom fields),

    fetch without JQL and filter client-side.

    """

    base_url = f"{JIRA_BASE}/rest/agile/1.0/sprint/{sprint_id}/issue"

    params_common = {"maxResults": page_size}

    if fields: params_common["fields"] = ",".join(fields)



    issues, start_at = [], 0

    used_server_filter = True

    try:

        for _ in range(max_pages):

            params = dict(params_common, startAt=start_at)

            if jql_fragment: params["jql"] = jql_fragment

            data = get_json(base_url, J_HEADERS, params)

            chunk = (data.get("issues") or [])

            issues.extend(chunk)

            if len(chunk) < page_size: break

            start_at += page_size

        return issues, used_server_filter

    except Exception as e:

        print(f"[warn] Agile JQL failed: {e}. Falling back to client-side filter.", file=sys.stderr)



    used_server_filter = False

    issues, start_at = [], 0

    for _ in range(max_pages):

        params = dict(params_common, startAt=start_at)

        data = get_json(base_url, J_HEADERS, params)

        chunk = (data.get("issues") or [])

        issues.extend(chunk)

        if len(chunk) < page_size: break

        start_at += page_size

    return issues, used_server_filter



# ===================== DATA BUILDERS (tables) =====================

def _is_done(fields: dict) -> bool:

    try:

        return ((fields.get("status") or {}).get("statusCategory") or {}).get("key") == "done"

    except Exception:

        return False



def carry_over_items_with_sp(report, sprint_id):

    """Issues not completed in the current sprint, with SP and Status."""

    contents = (report or {}).get("contents", {}) or {}

    not_completed = contents.get("issuesNotCompletedInCurrentSprint", []) or []

    keys = {it.get("key") for it in not_completed if it.get("key")}

    if not keys:

        return []

    # Fetch once and filter

    fields = ["summary", "issuetype", "status"] + STORY_POINTS_FIELD_IDS

    issues, _ = agile_sprint_issues_paginated(sprint_id, None, fields, max_pages=200, page_size=200)

    sp_map = build_sp_map_from_report(report)

    rows = []

    for it in issues:

        key = it.get("key")

        if key not in keys:

            continue

        f = it.get("fields") or {}

        name = f.get("summary") or ""

        sp = _field_sp_from_fields(f)

        if sp is None:

            sp = float(sp_map.get(key, 0.0))

        status_name = ((f.get("status") or {}).get("name") or "").strip()

        rows.append((key, name, safe_float(sp, 0.0), status_name))

    rows.sort(key=lambda r: (r[2] or 0.0), reverse=True)

    return rows



def top5_completed_stories(report, sprint_id):

    """Top 5 completed Story/Task by SP with Status (server filter Done → client filter by type)."""

    sp_map = build_sp_map_from_report(report)

    allowed_types = {"story", "task"}

    jql_frag = 'statusCategory = Done'

    fields = ["summary", STORY_POINTS_FIELD_ID, "issuetype", "status"]

    issues, used_server_filter = agile_sprint_issues_paginated(sprint_id, jql_frag, fields)

    rows = []

    for it in issues:

        f = it.get("fields") or {}

        if not used_server_filter and not _is_done(f):

            continue

        itype = (f.get("issuetype") or {}).get("name","").strip().lower()

        if itype not in allowed_types:

            continue

        key = it.get("key") or ""

        name = f.get("summary","")

        sp = _field_sp_from_fields(f)

        if sp is None: sp = float(sp_map.get(key, 0.0))

        status_name = ((f.get("status") or {}).get("name") or "").strip()

        rows.append((key, name, sp or 0.0, status_name))

    rows.sort(key=lambda r: r[2], reverse=True)

    return rows[:5]



def completed_tech_debts_with_sp(report, sprint_id):

    """Completed items under TD epic; EXCLUDE Cancelled/Canceled/Not Needed; include Status."""

    sp_map = build_sp_map_from_report(report)

    jql_frag = 'statusCategory = Done'

    fields = ["summary", STORY_POINTS_FIELD_ID, "issuetype", "status", EPIC_LINK_FIELD_ID]

    issues, used_server_filter = agile_sprint_issues_paginated(sprint_id, jql_frag, fields)

    rows = []

    for it in issues:

        f = it.get("fields") or {}

        if not used_server_filter and not _is_done(f):

            continue

        sname = (f.get("status") or {}).get("name","").strip().lower()

        if sname in AUDIT_EXCLUDED_STATUS_NAMES:

            continue

        epic_val = f.get(EPIC_LINK_FIELD_ID)

        epic_key = (epic_val.get("key") if isinstance(epic_val, dict) else epic_val)

        if epic_key != TD_EPIC_KEY:

            continue

        key = it.get("key","")

        name = f.get("summary","")

        sp = _field_sp_from_fields(f)

        if sp is None: sp = float(sp_map.get(key, 0.0))

        status_name = ((f.get("status") or {}).get("name") or "").strip()

        rows.append((key, name, safe_float(sp, 0.0), status_name))

    rows.sort(key=lambda r: (r[2] or 0.0), reverse=True)

    return rows



# --- Audit: Stories/Tasks without Epic Link (kept) ---

def stories_tasks_without_epic(sprint_id):

    excluded = ",".join(f'"{s.title()}"' for s in AUDIT_EXCLUDED_STATUS_NAMES)

    jql = f'status not in ({excluded})' if excluded else None  # status filter server-side; rest client-side

    fields = ["summary", "issuetype", "status", EPIC_LINK_FIELD_ID, "assignee"]

    issues, _ = agile_sprint_issues_paginated(sprint_id, jql, fields, max_pages=200, page_size=200)

    rows = []

    for it in issues:

        f = (it.get("fields") or {})

        itype = (f.get("issuetype") or {}).get("name","").lower()

        if itype not in AUDIT_ISSUE_TYPES:

            continue

        epic_val = f.get(EPIC_LINK_FIELD_ID, "__MISSING__")

        if epic_val in ("__MISSING__", None):

            assignee = (f.get("assignee") or {}).get("displayName") or "-"

            rows.append((it.get("key") or "", f.get("summary") or "", assignee))

    return rows



# --- Audit: Stories/Tasks without SP (kept) ---

def stories_tasks_without_sp(sprint_id):

    """Tickets where SP is missing OR SP == 0, excluding Cancelled/Canceled/Not Needed."""

    excluded = ",".join(f'"{s.title()}"' for s in AUDIT_EXCLUDED_STATUS_NAMES)

    jql = f'status not in ({excluded})' if excluded else None

    fields = ["summary", "issuetype", "status", "assignee"] + STORY_POINTS_FIELD_IDS

    issues, _ = agile_sprint_issues_paginated(sprint_id, jql, fields, max_pages=200, page_size=200)



    rows = []

    for it in issues:

        f = (it.get("fields") or {})

        itype = (f.get("issuetype") or {}).get("name","").lower()

        if itype not in AUDIT_ISSUE_TYPES:

            continue

        sp_vals = []

        for fid in STORY_POINTS_FIELD_IDS:

            if fid in f and f[fid] is not None:

                try: sp_vals.append(float(f[fid]))

                except Exception: pass

        has_any = len(sp_vals) > 0

        sp_sum = sum(sp_vals) if has_any else 0.0

        if (not has_any) or (sp_sum == 0.0):

            assignee = (f.get("assignee") or {}).get("displayName") or "-"

            rows.append((it.get("key") or "", f.get("summary") or "", assignee))

    return rows



# >>> PATCH: Previous Retro Action Items fetcher — now adds Ageing for OPEN items

def fetch_epic_action_items(epic_key: str):

    """

    Returns:

      open_rows: [(key, summary, assignee, ageing_str, status), ...]  # sorted by Ageing desc

      done_rows: [(key, summary, assignee, status), ...]

    Pulls all Story/Task under the epic using Agile API and includes their Sub-tasks.

    Parents are filtered to Story/Task-family issue types; Sub-tasks are always included.

    Ageing = working days since creation (creation day excluded) up to "today".

    """

    fields = ["status", "assignee", "issuetype", "subtasks", "summary", "created"]

    # 1) Get Story/Task items under the epic (Agile endpoint; handles pagination)

    issues = []

    start_at = 0

    while True:

        try:

            data = get_json(

                f"{JIRA_BASE}/rest/agile/1.0/epic/{epic_key}/issue",

                J_HEADERS,

                {"startAt": start_at, "maxResults": 100, "fields": ",".join(fields)}

            )

            chunk = data.get("issues") or []

            issues.extend(chunk)

            if len(chunk) < 100:

                break

            start_at += 100

        except Exception as e:

            print(f"[warn] Epic issues via agile endpoint failed: {e}", file=sys.stderr)

            break



    # 2) Fallback via Search API if Agile returned nothing (rare)

    if not issues:

        try:

            jql = f'"Epic Link" = {epic_key}'

            start_at = 0

            while True:

                data = get_json(

                    f"{JIRA_BASE}/rest/api/2/search",

                    J_HEADERS,

                    {"jql": jql, "startAt": start_at, "maxResults": 100, "fields": ",".join(fields + ["parent"])}

                )

                chunk = data.get("issues") or []

                issues.extend(chunk)

                if len(chunk) < 100:

                    break

                start_at += 100

        except Exception as e:

            print(f"[warn] Fallback JQL epic search failed: {e}", file=sys.stderr)



    def _pull_common(it_fields):

        summary = it_fields.get("summary") or "-"

        assignee = (it_fields.get("assignee") or {}).get("displayName") or "-"

        status   = (it_fields.get("status") or {}).get("name") or "-"

        status_cat = ((it_fields.get("status") or {}).get("statusCategory") or {}).get("key")

        created_dt = parse_jira_date(it_fields.get("created"))

        age = working_days_since(created_dt)

        return summary, assignee, status, status_cat, age



    # Allow only story/task family for parents

    allowed_parent_types = {t.lower() for t in STORY_TYPES} | {"story", "task"}



    open_tmp, done_rows = [], []

    subtask_keys = []



    # Parent Story/Task rows

    for it in issues:

        key = it.get("key") or ""

        f = it.get("fields") or {}

        itype = ((f.get("issuetype") or {}).get("name") or "").strip().lower()

        if itype not in allowed_parent_types:

            continue

        summary, assignee, status, status_cat, age = _pull_common(f)

        if status_cat == "done":

            done_rows.append((key, summary, assignee, status))

        else:

            open_tmp.append((age, (key, summary, assignee, f"{age} days", status)))

        for st in f.get("subtasks") or []:

            skey = st.get("key") or st.get("id")

            if skey:

                subtask_keys.append(skey)



    # Sub-task rows (fetch fields individually)

    for sk in subtask_keys:

        sf = get_issue_fields(sk, ["status", "assignee", "summary", "created"])

        summary, assignee, status, status_cat, age = _pull_common(sf or {})

        if status_cat == "done":

            done_rows.append((sk, summary, assignee, status))

        else:

            open_tmp.append((age, (sk, summary, assignee, f"{age} days", status)))



    # Sort: OPEN by Ageing desc; DONE stable

    open_tmp.sort(key=lambda r: r[0], reverse=True)

    open_rows = [row for _, row in open_tmp]



    # For readability in Done: by status, then assignee, then key

    done_rows.sort(key=lambda r: ((r[3] or ""), (r[2] or ""), (r[0] or "")))

    return open_rows, done_rows





# ===================== CONFLUENCE (attachments hardened) =====================

def _conf_url(path): return f"{CONF_BASE}{path}"



def find_existing_page(title):

    try:

        data = get_json(_conf_url("/rest/api/content"), C_HEADERS,

                        {"spaceKey": SPACE_KEY, "title": title, "expand": "version"})

        results = data.get("results", []) or []

        return results[0] if results else None

    except Exception as e:

        print(f"[warn] find_existing_page failed: {e}", file=sys.stderr)

        return None



def create_page(title, storage_html):

    payload = {"type": "page", "title": title, "space": {"key": SPACE_KEY},

               "body": {"storage": {"value": storage_html, "representation": "storage"}}}

    if PARENT_ID: payload["ancestors"] = [{"id": PARENT_ID}]

    return post_json(_conf_url("/rest/api/content"), C_HEADERS, payload)



def update_page(page_id, title, storage_html, version):

    payload = {"id": page_id, "type": "page", "title": title,

               "version": {"number": (version or 0) + 1},

               "body": {"storage": {"value": storage_html, "representation": "storage"}}}

    if PARENT_ID: payload["ancestors"] = [{"id": PARENT_ID}]

    return put_json(_conf_url(f"/rest/api/content/{page_id}"), C_HEADERS, payload)



def post_labels(page_id, labels):

    if not labels: return

    url = _conf_url(f"/rest/api/content/{page_id}/label")

    body = [{"prefix":"global","name":lbl} for lbl in labels]

    last = None

    for i in range(1, RETRY_MAX_TRIES+1):

        try:

            r = requests.post(url, headers={**C_HEADERS, "Content-Type":"application/json"},

                              data=json.dumps(body), timeout=60)

            r.raise_for_status()

            return

        except Exception as e:

            last = e

            print(f"[warn] adding labels failed (attempt {i}/{RETRY_MAX_TRIES}): {e}", file=sys.stderr)

            if i < RETRY_MAX_TRIES:

                _sleep_backoff(i)

    raise RuntimeError(f"Labeling failed for {page_id}: {last}")



def list_attachments(page_id):

    url = _conf_url(f"/rest/api/content/{page_id}/child/attachment")

    r = requests.get(url, headers=C_HEADERS, timeout=60)

    r.raise_for_status()

    data = r.json()

    return {att["title"]: att for att in data.get("results", [])}



def upload_or_update_attachment(page_id, filename, file_bytes, content_type="image/png"):

    existing = list_attachments(page_id)

    att = existing.get(filename)

    headers = {**C_HEADERS, "X-Atlassian-Token": "nocheck"}

    if att is None:

        url = _conf_url(f"/rest/api/content/{page_id}/child/attachment")

    else:

        att_id = att["id"]

        url = _conf_url(f"/rest/api/content/{page_id}/child/attachment/{att_id}/data")

    files = {"file": (filename, io.BytesIO(file_bytes), content_type)}

    last = None

    for i in range(1, RETRY_MAX_TRIES+1):

        try:

            r = requests.post(url, headers=headers, files=files, timeout=120)

            r.raise_for_status()

            data = r.json()

            return (data["results"][0] if isinstance(data, dict) and "results" in data else data)

        except Exception as e:

            last = e

            print(f"[warn] attach {filename} failed (attempt {i}/{RETRY_MAX_TRIES}): {e}", file=sys.stderr)

            if i < RETRY_MAX_TRIES:

                _sleep_backoff(i)

    raise RuntimeError(f"Attachment upload failed for {filename}: {last}")



def image_block(filename, title=None, width=None):

    w = f' ac:width="{int(width)}"' if width else ""

    t = f' ac:title="{escape_html(title)}"' if title else ""

    return (

        f'<ac:image{w}{t}>'

        f'  <ri:attachment ri:filename="{escape_html(filename)}" />'

        f'</ac:image>'

    )



# ===================== CHART HELPERS =====================

def fig_to_png_bytes():

    buf = io.BytesIO()

    # Extra bottom margin so long, rotated sprint names aren't clipped in PNGs.

    plt.tight_layout(rect=[0, 0.18, 1, 1])

    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")

    plt.close()

    return buf.getvalue()



def _format_dates_axis(ax, xs_dates, rotate_deg=30):

    """Format x-axis ticks as 25-Sep and show ALL dates passed in."""

    if not xs_dates: return

    ax.set_xticks(xs_dates)

    labels = [d.strftime("%d-%b") for d in xs_dates]  # e.g., 25-Sep

    ax.set_xticklabels(labels, rotation=rotate_deg, ha="right")



# ===================== JIRA CHARTS =====================

def render_burndown_png_bytes(report_json, sprint_id, committed_sp, board_id):

    """

    Jira-like burndown with dashed Ideal and stepped Actual remaining SP.

    Actual is computed from issues' resolution dates and SP values discovered at runtime.

    Also shades weekends with light grey bands behind the lines.



    CHANGE: X-axis end now uses sprint.completeDate (actual completion) when present,

    falling back to sprint.endDate.

    """

    sprint = (report_json or {}).get("sprint", {}) or {}

    start_dt = parse_jira_date(sprint.get("startDate")) or datetime.datetime.now(datetime.timezone.utc)

    # >>> Use actual completion if present

    end_dt   = parse_jira_date(sprint.get("completeDate") or sprint.get("endDate")) \

               or (start_dt + datetime.timedelta(days=14))



    # Build daily buckets (dates only)

    days = []

    d = start_dt.date()

    while d <= end_dt.date():

        days.append(d)

        d += datetime.timedelta(days=1)



    # Fetch all sprint issues once (resolutiondate + SP fields)

    fields = ["resolutiondate"] + STORY_POINTS_FIELD_IDS + ["issuetype","status","summary"]

    issues, _ = agile_sprint_issues_paginated(sprint_id, None, fields, max_pages=300, page_size=200)



    # Sum SP completed per day (based on resolution date)

    done_by_day = {day: 0.0 for day in days}

    for it in issues:

        f = it.get("fields") or {}

        rd = f.get("resolutiondate")

        if not rd: continue

        rdt = parse_jira_date(rd)

        if not rdt: continue

        day = rdt.date()

        if day < days[0] or day > days[-1]: continue

        sp = _field_sp_from_fields(f)

        if sp is None: sp = 0.0

        done_by_day[day] += float(sp)



    # Cumulative done → remaining from committed

    remaining = []

    cum = 0.0

    for day in days:

        cum += done_by_day.get(day, 0.0)

        remaining.append(max(committed_sp - cum, 0.0))



    # Ideal line (straight descent)

    ideal = []

    total_days = max(1, (days[-1] - days[0]).days)

    for i, _day in enumerate(days):

        frac = i / total_days

        ideal.append(committed_sp * (1.0 - frac))



    # Plot — larger (like LinearB) with extra height so X labels are readable

    plt.figure(figsize=(16, 6))

    ax = plt.gca()



    # Shade weekends with light grey vertical spans

    for i, day in enumerate(days):

        if day.weekday() >= 5:  # 5=Sat, 6=Sun

            next_day = day + datetime.timedelta(days=1)

            ax.axvspan(day, next_day, color="#F4F5F7", alpha=0.6, zorder=0)



    # Ideal (grey dashed) & Actual (blue step)

    ax.plot(days, ideal, label="Ideal", linestyle="--", linewidth=2.0, color="#7A869A")

    ax.step(days, remaining, where="post", label="Actual", linewidth=2.2, color="#0052CC")



    ax.set_title("Sprint Burndown")

    ax.set_ylabel("Remaining SP")

    _format_dates_axis(ax, days, rotate_deg=30)

    ax.set_ylim(bottom=0)

    ax.legend()



    # Prevent cropped tick labels in PNG

    plt.gcf().subplots_adjust(bottom=0.30)

    return fig_to_png_bytes()





def render_velocity_png_bytes(board_id, board_name, prefix, highlight_sprint_id=None, max_sprints=8, sprint_ids=None):

    """

    Velocity with Jira-like look:

      - Committed (grey) vs Completed (green) bars

      - Average line

      - Values above bars + % labels

      - Highlight band for selected sprint

      - **X-axis shows sprint names** (with robust fallback if Velocity endpoint omits them)



    [A‑2] Fallback: when a sprint in the explicit window is missing from Velocity API,

          compute Committed/Completed from the Sprint Report so the window stays complete.

    """

    vj = velocity_json(board_id)

    entries = vj.get("velocityStatEntries", {}) or {}



    rows_all = []

    for sid, v in entries.items():

        sp = v.get("sprint", {}) or {}

        name  = sp.get("name", "")  # may be empty on some Jira versions

        start = sp.get("startDate") or sp.get("endDate") or ""

        est = float((v.get("estimated") or {}).get("value") or 0)

        comp = float((v.get("completed") or {}).get("value") or 0)

        rows_all.append((str(sid), name, est, comp, start))



    # Select rows (explicit ids preferred; else prefix/max_sprints)

    if sprint_ids:

        rows = []

        by_id = {r[0]: r for r in rows_all}

        for wanted in map(str, sprint_ids):

            if wanted in by_id:

                rows.append(by_id[wanted])

            else:

                # ----- [A-2] FALLBACK to Sprint Report -----

                try:

                    rep = sprint_report(board_id, int(wanted))

                    spmeta = (rep.get("sprint") or {})

                    name   = spmeta.get("name") or f"Sprint {wanted}"

                    start  = spmeta.get("startDate") or spmeta.get("endDate") or ""

                    committed, completed, *_ = compute_committed_completed_from_report(rep)

                    rows.append((wanted, name, float(committed or 0), float(completed or 0), start))

                except Exception as e:

                    print(f"[warn] velocity fallback failed for sprint {wanted}: {e}", file=sys.stderr)

    else:

        rows = [r for r in rows_all if (not prefix) or (r[1].startswith(prefix))]

        if not rows:

            rows = rows_all

        rows.sort(key=lambda r: r[4], reverse=True)

        rows = rows[:max_sprints]

        rows.reverse()



    # --- Backfill sprint names if missing ---

    for i, (sid, nm, est, comp, start) in enumerate(rows):

        if not (nm or "").strip():

            try:

                meta = get_json(f"{JIRA_BASE}/rest/agile/1.0/sprint/{sid}", J_HEADERS)

                nm = meta.get("name") or nm

            except Exception:

                pass

        if not (nm or "").strip():

            nm = f"Sprint {sid}"

        rows[i] = (sid, nm, est, comp, start)



    names = [r[1] for r in rows]  # guaranteed non-empty now

    ests  = [r[2] for r in rows]

    comps = [r[3] for r in rows]



    plt.figure(figsize=(18, 7))

    ax = plt.gca()

    if not names:

        ax.set_title(f"Velocity — {board_name}")

        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)

        return fig_to_png_bytes()



    x = list(range(len(names)))

    committed_color = "#7A869A"  # Atlassian grey

    completed_color = "#36B37E"  # Atlassian green



    # Highlight band for selected sprint

    selected_idx = None

    if highlight_sprint_id is not None:

        sid_str = str(highlight_sprint_id)

        for idx, r in enumerate(rows):

            if r[0] == sid_str:

                selected_idx = idx

                ax.axvspan(idx - 0.5, idx + 0.5, color="#DEEBFF", alpha=0.4, zorder=0)

                break



    # Bars

    ax.bar([i-0.2 for i in x], ests,  width=0.4, label="Committed", color=committed_color)

    ax.bar([i+0.2 for i in x], comps, width=0.4, label="Completed", color=completed_color)



    # Numbers above bars

    y_top = max([0.0] + ests + comps)

    for i, (e, c) in enumerate(zip(ests, comps)):

        ax.text(i-0.2, e, f"{e:.1f}", ha="center", va="bottom", fontsize=9)

        ax.text(i+0.2, c, f"{c:.1f}", ha="center", va="bottom", fontsize=9)



    # % labels

    for i, (e, c) in enumerate(zip(ests, comps)):

        pct = (c / e * 100.0) if e > 0 else None

        label = f"{pct:.0f}%" if pct is not None else "—"

        y = max(e, c) + max(1.0, y_top * 0.04)

        ax.text(i, y, label, ha="center", va="bottom", fontsize=10, fontweight="bold")



    # Average line

    if comps:

        avg = sum(comps) / len(comps)

        ax.axhline(avg, linestyle="-", linewidth=1.8)

        ax.text(len(x)-0.05, avg, f"Average: {avg:.1f}", va="bottom", ha="right", fontsize=9)



    ax.set_title(f"Velocity — {board_name}")

    ax.set_ylabel("Story Points")



    # Force tick labels to sprint names (works on old/new Matplotlib)

    try:

        ax.set_xticks(x, labels=names)

    except TypeError:

        ax.set_xticks(x)

        ax.set_xticklabels(names)

    ax.tick_params(axis="x", labelrotation=30, labelsize=10, pad=10)

    for lbl in ax.get_xticklabels():

        lbl.set_ha("right")



    # Top label for selected sprint

    if selected_idx is not None:

        top_label_y = y_top * 1.20 + 2.0

        ax.text(selected_idx, top_label_y, "Sprint under review (this report)", ha="center", va="bottom", fontsize=10)



    # Headroom and margins

    ax.set_ylim(bottom=0, top=max(ax.get_ylim()[1], y_top * 1.35 + 2.0))

    ax.legend()

    plt.gcf().subplots_adjust(bottom=0.36)  # space for rotated labels



    return fig_to_png_bytes()







# ===================== LinearB measurements =====================

def _lb_headers():

    if not LINEARB_TOKEN:

        raise RuntimeError("LinearB token missing (LinearB_Token).")

    return {"x-api-key": LINEARB_TOKEN, "Content-Type": "application/json", "Accept": "application/json"}



def linearb_measurements_daily(team_id: int, start_date: str, end_date: str) -> list[dict]:

    """

    Returns a list of dicts:

      {"date": "YYYY-MM-DD", "coding_min":..., "pickup_min":..., "review_min":..., "cycle_min":...}

    Uses v2 measurements POST with roll_up=1d. Robust to 204/no-data.

    """

    url = f"{LINEARB_BASE}/api/v2/measurements"

    body = {

        "group_by": "team",

        "team_ids": [int(team_id)],

        "roll_up": "1d",

        "requested_metrics": [

            {"name": "branch.time_to_pr", "agg": "p50"},

            {"name": "branch.time_to_review", "agg": "p50"},

            {"name": "branch.review_time", "agg": "p50"},

            {"name": "branch.computed.cycle_time", "agg": "p50"}

        ],

        "time_ranges": [{"after": start_date, "before": end_date}],

        "return_no_data": True,

        "limit": 1000

    }

    last = None

    for i in range(1, RETRY_MAX_TRIES+1):

        try:

            r = requests.post(url, headers=_lb_headers(), json=body, timeout=TIMEOUT_SECS)

            if r.status_code == 204:

                return []

            r.raise_for_status()

            data = r.json()

            rows = []

            for slice_ in data:

                after = slice_.get("after")

                row = {

                    "date": (after or "").split("T")[0],

                    "coding_min": None, "pickup_min": None, "review_min": None, "cycle_min": None

                }

                for m in slice_.get("metrics", []):

                    for k, v in m.items():

                        if k == "branch.time_to_pr:p50":              row["coding_min"] = v

                        elif k == "branch.time_to_review:p50":        row["pickup_min"] = v

                        elif k == "branch.review_time:p50":           row["review_min"] = v

                        elif k == "branch.computed.cycle_time:p50":   row["cycle_min"]  = v

                rows.append(row)

            return rows

        except Exception as e:

            last = e

            if i < RETRY_MAX_TRIES:

                print(f"[warn] LinearB measurements failed (attempt {i}/{RETRY_MAX_TRIES}): {e}", file=sys.stderr)

                _sleep_backoff(i)

    raise RuntimeError(f"LinearB measurements failed after retries: {last}")



def _daterange_inclusive(start_date: str, end_date: str) -> list[datetime.date]:

    """Inclusive list of dates from YYYY-MM-DD to YYYY-MM-DD."""

    start = datetime.date.fromisoformat(start_date)

    end = datetime.date.fromisoformat(end_date)

    days = []

    d = start

    while d <= end:

        days.append(d)

        d += datetime.timedelta(days=1)

    return days



def _lb_timeseries_complete(points: list[dict], start_date: str, end_date: str, key: str):

    """

    Build a complete series for all sprint days:

      - X covers every day from start_date to end_date (inclusive)

      - Y is hours; missing days are ZERO (zero-fill), per request

    """

    all_days = _daterange_inclusive(start_date, end_date)

    by_date_hours = {}

    for row in points:

        d = row.get("date")

        if not d:

            continue

        try:

            dt = datetime.date.fromisoformat(d)

        except Exception:

            continue

        v_min = row.get(key)

        by_date_hours[dt] = (float(v_min) / 60.0) if v_min is not None else 0.0



    ys = [(by_date_hours.get(day, 0.0)) for day in all_days]  # zeros for missing

    return all_days, ys



def render_linearb_series_png_bytes(xs, ys, title, ylabel):

    """

    Wide figure; dd-Mon ticks (30°); labels inside; zero-filled series.

    Annotations use dynamic offset and headroom so they stay within axes.

    Weekends are shaded (light grey) like Jira burndown.

    """

    plt.figure(figsize=(16, 4.2))  # wide → all dates visible

    ax = plt.gca()



    if not xs:

        ax.set_title(title)

        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)

        return fig_to_png_bytes()



    # Shade weekends with light grey vertical spans (per request #5)

    for day in xs:

        if isinstance(day, datetime.date) and day.weekday() >= 5:

            next_day = day + datetime.timedelta(days=1)

            ax.axvspan(day, next_day, color="#F4F5F7", alpha=0.6, zorder=0)



    ax.plot(xs, ys, marker="o", linewidth=2.0)



    # Determine headroom and keep labels inside the frame

    ymax = max(ys) if ys else 0.0

    pad  = max(0.5, ymax * 0.15)  # headroom for labels

    ax.set_ylim(0, ymax + pad)



    for x, y in zip(xs, ys):

        label = minutes_to_dhm(y * 60.0)

        # If near the top, place label slightly below the point

        top = ax.get_ylim()[1]

        offset = -10 if y >= (top * 0.95) else 6

        va = "top" if offset < 0 else "bottom"

        ax.annotate(label, (x, y), textcoords="offset points", xytext=(0, offset),

                    ha="center", va=va, fontsize=9, clip_on=True)



    ax.set_title(title)

    ax.set_ylabel(ylabel)

    _format_dates_axis(ax, xs, rotate_deg=30)  # dd-Mon

    ax.margins(x=0.01)

    return fig_to_png_bytes()



# ===================== HTML / UX =====================

h2_css = 'style="margin-top:8px;margin-bottom:6px;"'

TABLE_STYLE = "width:100%; font-size:12.5px; border-collapse:collapse;"

TH_STYLE = "text-align:left; padding:4px 6px; border-bottom:1px solid #DFE1E6;"

TD_STYLE = "padding:4px 6px; vertical-align:top;"



# Status palette (darker tones as requested)

ATL_GREEN = "#006644"      # Done (dark green)

ATL_AMBER = "#FF8B00"      # In Progress (amber/orange)

ATL_GREY  = "#42526E"      # To Do (dark grey)

ATL_BLUE  = "#0052CC"      # Reviewing (dark blue)

ATL_LIGHT_GREEN = "#57D9A3" # Not Needed (light green)



def panel(title: str, inner_html: str):

    return f"""

<ac:structured-macro ac:name="panel">

  <ac:parameter ac:name="title">{escape_html(title)}</ac:parameter>

  <ac:parameter ac:name="borderColor">#DFE1E6</ac:parameter>

  <ac:rich-text-body>

    <div style="margin-bottom:6px">{inner_html}</div>

  </ac:rich-text-body>

</ac:structured-macro>

"""



# >>> PATCH: Add Retro Action Items Epic link to Quick Links (unchanged)

def make_links_block(jira_base, board, sprint_id, epic_key):

    board_url  = f"{jira_base}/secure/RapidBoard.jspa?rapidView={board['id']}"

    sprint_rep = f"{jira_base}/secure/RapidBoard.jspa?rapidView={board['id']}&view=reporting&chart=sprintRetrospective&sprint={sprint_id}"

    epic_url   = f"{jira_base}/browse/{epic_key}"

    retro_epic_url = f"{jira_base}/browse/{RETRO_EPIC_KEY}"



    return f"""

<ul style="margin-top:4px;margin-bottom:4px;">

  <li><a href="{escape_html(sprint_rep)}" target="_blank" rel="noopener">Sprint Report in Jira</a></li>

  <li><a href="{escape_html(board_url)}" target="_blank" rel="noopener">Board: {escape_html(board.get('name',''))}</a></li>

  <li><a href="{escape_html(epic_url)}" target="_blank" rel="noopener">Tech Debt Epic: {escape_html(epic_key)}</a></li>

  <li><a href="{escape_html(retro_epic_url)}" target="_blank" rel="noopener">Retro Action Items Epic: {escape_html(RETRO_EPIC_KEY)}</a></li>

</ul>

"""



def _status_colorized_html(status_name: str, mapping: dict) -> str:

    """Return HTML for a status with optional color mapping (case-insensitive keys)."""

    sraw = (status_name or "-")

    key = sraw.strip().lower()

    # allow a few common aliases to map to the requested buckets

    alias = {

        "in review": "reviewing",

        "review": "reviewing",

        "todo": "to do"

    }.get(key, key)

    color = mapping.get(alias)

    text = escape_html(truncate_text(sraw, SUMMARY_MAX_CHARS))

    return f'<span style="color:{color};">{text}</span>' if color else text



def make_summary_table(committed_sp, completed_sp, scope_change_count, carry_over_count,

                       planned_start_ist, planned_end_ist, actual_start_ist, actual_end_ist,

                       planned_wd, actual_wd):

    committed_sp = safe_float(committed_sp, 0.0)

    completed_sp = safe_float(completed_sp, 0.0)

    pct = (completed_sp/committed_sp*100.0) if committed_sp > 0 else 0.0

    pct_text = f"{pct:.1f}%"

    color = "#1f7a1f" if pct >= 90.0 else ("#d47500" if pct >= 75.0 else "#c62828")

    badge_css = "display:inline-block;padding:2px 8px;border-radius:12px;font-size:12px;margin-right:6px;"

    scope_badge = f'<span style="{badge_css}background:#e3f2fd;color:#0d47a1;">Scope Change: {int(scope_change_count or 0)}</span>'

    carry_badge = f'<span style="{badge_css}background:#f3e5f5;color:#4a148c;">Carry-over: {int(carry_over_count or 0)}</span>'

    html = f"""

<table style="{TABLE_STYLE}">

  <thead><tr><th style="{TH_STYLE}">Metric</th><th style="{TH_STYLE}">Value</th></tr></thead>

  <tbody>

    <tr><td style="{TD_STYLE}"><strong>Committed SP</strong></td><td style="{TD_STYLE}"><strong>{committed_sp:.1f}</strong></td></tr>

    <tr><td style="{TD_STYLE}"><strong>Completed SP</strong></td><td style="{TD_STYLE}"><strong>{completed_sp:.1f}</strong></td></tr>

    <tr><td style="{TD_STYLE}">% Complete</td><td style="{TD_STYLE}"><span style="color:{color}; font-weight:bold;">{pct_text}</span></td></tr>

    <tr><td style="{TD_STYLE}">Planned Start (IST)</td><td style="{TD_STYLE}">{escape_html(planned_start_ist)}</td></tr>

    <tr><td style="{TD_STYLE}">Planned End (IST)</td><td style="{TD_STYLE}">{escape_html(planned_end_ist)}</td></tr>

    <tr><td style="{TD_STYLE}">Planned Duration (working days)</td><td style="{TD_STYLE}">{int(planned_wd)}</td></tr>

    <tr><td style="{TD_STYLE}">Actual Start (IST)</td><td style="{TD_STYLE}">{escape_html(actual_start_ist)}</td></tr>

    <tr><td style="{TD_STYLE}">Actual End (IST)</td><td style="{TD_STYLE}">{escape_html(actual_end_ist)}</td></tr>

    <tr><td style="{TD_STYLE}">Actual Duration (working days)</td><td style="{TD_STYLE}">{int(actual_wd)}</td></tr>

  </tbody>

</table>

<div style="margin-top:6px">{scope_badge}{carry_badge}</div>

<p style="font-size: 0.9em; opacity: 0.85; margin-top: 6px;">

  <strong>Legend:</strong>

  <span style="color:#1f7a1f; font-weight:bold;">Green &ge; 90%</span> &nbsp;|&nbsp;

  <span style="color:#d47500; font-weight:bold;">Orange &ge;75% &lt; 90%</span> &nbsp;|&nbsp;

  <span style="color:#c62828; font-weight:bold;">Red &lt; 75%</span>

</p>

"""

    return html



def make_sp_per_person_table(unique_names, committed_sp, completed_sp):

    names = unique_names or []

    count = len(names)

    committed_sp = safe_float(committed_sp, 0.0)

    completed_sp = safe_float(completed_sp, 0.0)

    committed_per = f"{(committed_sp / count):.2f}" if count else "—"

    completed_per = f"{(completed_sp / count):.2f}" if count else "—"

    return f"""

<table style="{TABLE_STYLE}">

  <thead><tr><th style="{TH_STYLE}">Metric</th><th style="{TH_STYLE}">Value</th></tr></thead>

  <tbody>

    <tr><td style="{TD_STYLE}">Committed SP / Person</td><td style="{TD_STYLE}">{committed_per}</td></tr>

    <tr><td style="{TD_STYLE}">Completed SP / Person</td><td style="{TD_STYLE}">{completed_per}</td></tr>

  </tbody>

</table>

"""



def make_team_table_two_cols(unique_names):

    names = list(unique_names or [])

    if len(names) % 2 == 1:

        names.append("")

    rows_html = []

    if not names:

        rows_html.append(f"<tr><td style='{TD_STYLE}'>-</td><td style='{TD_STYLE}'></td></tr>")

    else:

        for i in range(0, len(names), 2):

            left = escape_html(names[i] or "")

            right = escape_html(names[i+1] or "")

            rows_html.append(f"<tr><td style='{TD_STYLE}'>{left}</td><td style='{TD_STYLE}'>{right}</td></tr>")

    return f"""

<table style="{TABLE_STYLE}">

  <thead><tr><th style="{TH_STYLE}">Name</th><th style="{TH_STYLE}">Name</th></tr></thead>

  <tbody>

    {''.join(rows_html)}

  </tbody>

</table>

"""



def make_table_with_total(items, headers, jira_base):

    """(Legacy 3-column builder) Key | Summary | SP"""

    count = len(items or [])

    colgroup = """

<colgroup>

  <col style="width:18%;"/>

  <col style="width:62%;"/>

  <col style="width:20%;"/>

</colgroup>

"""

    thead = "".join(f"<th style='{TH_STYLE}'>{escape_html(h)}</th>" for h in headers)

    rows_html, total = [], 0.0

    for idx, (key, summary, sp) in enumerate(items or []):

        link = f'<a href="{jira_base}/browse/{escape_html(key)}" target="_blank" rel="noopener" style="white-space:nowrap; text-decoration:none;">{escape_html(key)}</a>'

        sp_num = None if (sp is None or sp == "") else safe_float(sp, 0.0)

        sp_text = "-" if (sp_num is None) else f"{sp_num:.1f}"

        if isinstance(sp_num,(int,float)): total += float(sp_num)

        short = truncate_text(summary, SUMMARY_MAX_CHARS)

        zebra = "#FAFBFC" if (idx % 2 == 0) else "#FFFFFF"

        rows_html.append(

            f"<tr style='background:{zebra};'>"

            f"<td style='{TD_STYLE} white-space:nowrap'>{link}</td>"

            f"<td style='{TD_STYLE}'>{escape_html(short)}</td>"

            f"<td style='{TD_STYLE} text-align:right'>{sp_text}</td>"

            f"</tr>"

        )

    if not rows_html:

        rows_html = [f"<tr><td style='{TD_STYLE}' colspan='3'>None</td></tr>"]

    body = "\n".join(rows_html)

    tfoot = f"""

  <tfoot>

    <tr>

      <td colspan="2" style="{TD_STYLE}"><strong>Items:</strong> {count}</td>

      <td style="{TD_STYLE} text-align:right"><strong>Total SP:</strong> {total:.1f}</td>

    </tr>

  </tfoot>"""

    return f"<table style='{TABLE_STYLE}'>{colgroup}<thead><tr>{thead}</tr></thead><tbody>{body}</tbody>{tfoot}</table>"



def make_table_with_total_and_status(items, headers, jira_base, status_color_map: dict):

    """

    4-column builder: Key | Summary | SP | Status (colored per mapping).

    items: [(key, summary, sp, status), ...]

    """

    count = len(items or [])

    colgroup = """

<colgroup>

  <col style="width:16%;"/>

  <col style="width:54%;"/>

  <col style="width:18%;"/>

  <col style="width:12%;"/>

</colgroup>

"""

    thead = "".join(f"<th style='{TH_STYLE}'>{escape_html(h)}</th>" for h in headers)

    rows_html, total = [], 0.0

    for idx, row in enumerate(items or []):

        key, summary, sp, status = (row + (None,))[:4]

        link = f'<a href="{jira_base}/browse/{escape_html(key)}" target="_blank" rel="noopener" style="white-space:nowrap; text-decoration:none;">{escape_html(key)}</a>'

        sp_num = None if (sp is None or sp == "") else safe_float(sp, 0.0)

        sp_text = "-" if (sp_num is None) else f"{sp_num:.1f}"

        if isinstance(sp_num,(int,float)): total += float(sp_num)

        short = truncate_text(summary or "", SUMMARY_MAX_CHARS)

        status_html = _status_colorized_html(status or "-", status_color_map)

        zebra = "#FAFBFC" if (idx % 2 == 0) else "#FFFFFF"

        rows_html.append(

            f"<tr style='background:{zebra};'>"

            f"<td style='{TD_STYLE} white-space:nowrap'>{link}</td>"

            f"<td style='{TD_STYLE}'>{escape_html(short)}</td>"

            f"<td style='{TD_STYLE} text-align:right'>{sp_text}</td>"

            f"<td style='{TD_STYLE}'>{status_html}</td>"

            f"</tr>"

        )

    if not rows_html:

        rows_html = [f"<tr><td style='{TD_STYLE}' colspan='4'>None</td></tr>"]

    body = "\n".join(rows_html)

    tfoot = f"""

  <tfoot>

    <tr>

      <td colspan="3" style="{TD_STYLE}"><strong>Items:</strong> {count}</td>

      <td style="{TD_STYLE} text-align:right"><strong>Total SP:</strong> {total:.1f}</td>

    </tr>

  </tfoot>"""

    return f"<table style='{TABLE_STYLE}'>{colgroup}<thead><tr>{thead}</tr></thead><tbody>{body}</tbody>{tfoot}</table>"



def make_table_simple(items, headers, jira_base):

    """Generic simple table (no totals). Colors are not applied here."""

    count = len(items or [])

    thead = "".join(f"<th style='{TH_STYLE}'>{escape_html(h)}</th>" for h in headers)

    rows_html = []

    for idx, row in enumerate(items or []):

        key = row[0] or "-"

        other_cols = list(row[1:])

        zebra = "#FAFBFC" if (idx % 2 == 0) else "#FFFFFF"

        link_html = f'<a href="{jira_base}/browse/{escape_html(key)}" target="_blank" rel="noopener" style="white-space:nowrap; text-decoration:none;">{escape_html(key)}</a>'

        cells = [f"<td style='{TD_STYLE} white-space:nowrap'>{link_html}</td>"]

        for col in other_cols:

            cells.append(f"<td style='{TD_STYLE}'>{escape_html(truncate_text(str(col) if col else '-', SUMMARY_MAX_CHARS))}</td>")

        rows_html.append("<tr style='background:%s;'>%s</tr>" % (zebra, "".join(cells)))

    if not rows_html:

        rows_html = [f"<tr><td style='{TD_STYLE}' colspan='{len(headers)}'>None</td></tr>"]

    body = "\n".join(rows_html)

    tfoot = f"""

  <tfoot>

    <tr>

      <td colspan="{len(headers)}" style="{TD_STYLE}"><strong>Items:</strong> {count}</td>

    </tr>

  </tfoot>"""

    return f"<table style='{TABLE_STYLE}'><thead><tr>{thead}</tr></thead><tbody>{body}</tbody>{tfoot}</table>"



def make_table_simple_statuscolored(items, headers, jira_base, status_color_map: dict):

    """

    Simple table variant that color-codes the **last** column (Status) using status_color_map.

    items: e.g., [(key, summary, assignee, status), ...] OR with extra columns before Status.

    """

    count = len(items or [])

    thead = "".join(f"<th style='{TH_STYLE}'>{escape_html(h)}</th>" for h in headers)

    rows_html = []

    has_status = any(h.strip().lower() == "status" for h in headers)

    hdrs_lower = [h.strip().lower() for h in headers]

    status_pos_in_headers = hdrs_lower.index("status") if has_status else -1

    for idx, row in enumerate(items or []):

        key = row[0] or "-"

        other_cols = list(row[1:])

        zebra = "#FAFBFC" if (idx % 2 == 0) else "#FFFFFF"

        link_html = f'<a href="{jira_base}/browse/{escape_html(key)}" target="_blank" rel="noopener" style="white-space:nowrap; text-decoration:none;">{escape_html(key)}</a>'

        cells = [f"<td style='{TD_STYLE} white-space:nowrap'>{link_html}</td>"]

        # For each remaining displayed column, colorize if it aligns with 'Status'

        for j, col in enumerate(other_cols):

            text_html = escape_html(truncate_text(str(col) if col else '-', SUMMARY_MAX_CHARS))

            # Column j in other_cols corresponds to header index j+1

            if has_status and (j + 1) == status_pos_in_headers:

                text_html = _status_colorized_html(str(col) if col else '-', status_color_map)

            cells.append(f"<td style='{TD_STYLE}'>{text_html}</td>")

        rows_html.append("<tr style='background:%s;'>%s</tr>" % (zebra, "".join(cells)))

    if not rows_html:

        rows_html = [f"<tr><td style='{TD_STYLE}' colspan='{len(headers)}'>None</td></tr>"]

    body = "\n".join(rows_html)

    tfoot = f"""

  <tfoot>

    <tr>

      <td colspan="{len(headers)}" style="{TD_STYLE}"><strong>Items:</strong> {count}</td>

    </tr>

  </tfoot>"""

    return f"<table style='{TABLE_STYLE}'><thead><tr>{thead}</tr></thead><tbody>{body}</tbody>{tfoot}</table>"



# >>> PATCH: Previous Retro Action Items renderer — adds Ageing and status coloring

def make_prev_retro_section_html(jira_base, open_rows, done_rows):

    """

    Build the 'Previous Retro Action Items' section with two horizontally-stacked tables.

      - Left: To Do / In Progress / Reviewing (non-done): columns Ticket, Summary, Assignee, Ageing, Status.

               Status colors: Done=dark green; In Progress=amber; Reviewing=dark blue; To Do=dark grey; Not Needed=light green.

               Sorted by Ageing desc (oldest first).

      - Right: Done — columns Ticket, Summary, Assignee, Status (Status dark green).

    """

    open_map = {

        "done": ATL_GREEN,

        "in progress": ATL_AMBER,

        "reviewing": ATL_BLUE,

        "to do": ATL_GREY,

        "not needed": ATL_LIGHT_GREEN

    }

    done_map = {"done": ATL_GREEN}

    left_table  = make_table_simple_statuscolored(open_rows, ["Ticket", "Summary", "Assignee", "Ageing", "Status"], jira_base, open_map)

    right_table = make_table_simple_statuscolored(done_rows, ["Ticket", "Summary", "Assignee", "Status"], jira_base, done_map)

    return f"""

<h2 {h2_css}>Previous Retro Action Items</h2>

<ac:layout>

  <ac:layout-section ac:type="two_equal">

    <ac:layout-cell>{panel("To Do / In Progress / Reviewing", left_table)}</ac:layout-cell>

    <ac:layout-cell>{panel("Done", right_table)}</ac:layout-cell>

  </ac:layout-section>

</ac:layout>

"""





def make_storage_html(jira_base, board, sprint, committed_sp, completed_sp,

                      scope_change_count, carry_over_count,

                      planned_start_ist, planned_end_ist, actual_start_ist, actual_end_ist,

                      planned_wd, actual_wd,

                      top5_rows, techdebt_rows, team_names,

                      no_epic_rows, no_sp_rows, carry_rows,

                      prev_retro_html="",                      # inserted just below Quality Checks

                      charts_html=""):



    toc_block = """

<ac:layout>

  <ac:layout-section ac:type="single">

    <ac:layout-cell>

      <ac:structured-macro ac:name="panel">

        <ac:parameter ac:name="title">Contents</ac:parameter>

        <ac:parameter ac:name="borderColor">#DFE1E6</ac:parameter>

        <ac:rich-text-body>

          <ac:structured-macro ac:name="toc">

            <ac:parameter ac:name="minLevel">2</ac:parameter>

            <ac:parameter ac:name="maxLevel">3</ac:parameter>

          </ac:structured-macro>

        </ac:rich-text-body>

      </ac:structured-macro>

      <p style="margin:10px 0;"></p>

    </ac:layout-cell>

  </ac:layout-section>

</ac:layout>

"""



    summary_tbl_html = make_summary_table(

        committed_sp, completed_sp, scope_change_count, carry_over_count,

        planned_start_ist, planned_end_ist, actual_start_ist, actual_end_ist,

        planned_wd, actual_wd

    )

    sp_per_person_html = make_sp_per_person_table(team_names, committed_sp, completed_sp)

    team_table_html    = make_team_table_two_cols(team_names)

    team_note_html = '<p style="font-size:11px; color:#6B778C; font-style:italic; margin-top:6px;">Team members list includes all members from GVRE or other teams who have a Jira ticket in the sprint</p>'

    team_title = f"Team Members ({len(team_names)})"



    header_three_col = f"""

<h2 {h2_css}>Overview</h2>

<ac:layout>

  <ac:layout-section ac:type="three_equal">

    <ac:layout-cell>{panel("Summary", summary_tbl_html)}</ac:layout-cell>

    <ac:layout-cell>{panel("SP per Person (Avg)", sp_per_person_html)}</ac:layout-cell>

    <ac:layout-cell>{panel(team_title, team_table_html + team_note_html)}</ac:layout-cell>

  </ac:layout-section>

</ac:layout>

"""



    # Completed Work tables (include Status coloring)

    stories_table_html   = make_table_with_total_and_status(

        top5_rows, ["Story/Task", "Summary", "SP", "Status"], jira_base,

        status_color_map={"done": ATL_GREEN}

    )

    techdebts_table_html = make_table_with_total_and_status(

        techdebt_rows, ["Tech Debt", "Summary", "SP", "Status"], jira_base,

        status_color_map={"done": ATL_GREEN}

    )



    main_tables_cols = f"""

<h2 {h2_css}>Completed Work</h2>

<ac:layout>

  <ac:layout-section ac:type="two_equal">

    <ac:layout-cell>{panel("Top 5 Completed Stories / Tasks (by SP)", stories_table_html)}</ac:layout-cell>

    <ac:layout-cell>{panel("Completed Tech Debts", techdebts_table_html)}</ac:layout-cell>

  </ac:layout-section>

</ac:layout>

"""



    no_epic_table = make_table_simple(no_epic_rows, ["Story/Task (no Epic)", "Summary", "Assignee"], jira_base)

    no_sp_table   = make_table_simple(no_sp_rows,   ["Story/Task (no SP or SP = 0)", "Summary", "Assignee"], jira_base)



    carry_table   = make_table_with_total_and_status(

        carry_rows, ["Issue", "Summary", "SP", "Status"], jira_base,

        status_color_map={"in progress": ATL_AMBER, "to do": ATL_GREY, "reviewing": ATL_BLUE, "not needed": ATL_LIGHT_GREEN}

    )



    audit_tables_cols = f"""

<h2 {h2_css}>Quality Checks</h2>

<ac:layout>

  <ac:layout-section ac:type="single">

    <ac:layout-cell>{panel("Carry-over / Spill-over (Not Completed in Sprint)", carry_table)}</ac:layout-cell>

  </ac:layout-section>

</ac:layout>

<ac:layout>

  <ac:layout-section ac:type="two_equal">

    <ac:layout-cell>{panel("Stories/Tasks without Epic Link", no_epic_table)}</ac:layout-cell>

    <ac:layout-cell>{panel("Stories/Tasks without Story Points", no_sp_table)}</ac:layout-cell>

  </ac:layout-section>

</ac:layout>

"""



    links_block = make_links_block(jira_base, board, sprint["id"], TD_EPIC_KEY)

    quick_links_panel = panel("Quick Links", links_block)



    charts_section = f"""

<h2 {h2_css}>Charts</h2>

{charts_html or "<p>No charts available.</p>"}

<hr style="margin-top:10px;margin-bottom:10px;"/>

<p style="color:#6B778C; font-size: 11px;">Generated by GVRE Sprint Report v{SCRIPT_VERSION}</p>

"""



    return f"""

{toc_block}

{header_three_col}

<hr/>

{main_tables_cols}

<hr/>

{audit_tables_cols}

{prev_retro_html}                      <!-- inserted just below Quality Checks -->

<hr/>

{charts_section}

{quick_links_panel}

"""



# ===================== SPRINT PICKER =====================

# >>> PATCH: exclude "QA" sprints from the closed list (unchanged)

def list_all_closed_prefix_sorted(board_id, prefix):

    all_closed = list_all_closed_sprints(board_id)

    enriched = []

    seen = set()

    for s in all_closed:

        name = (s.get("name") or "")

        if prefix and not name.startswith(prefix):

            continue

        if "QA" in name.upper():

            continue

        sid = s.get("id")

        if sid in seen:

            continue

        seen.add(sid)

        try:

            rep = sprint_report(board_id, sid)

            meta = rep.get("sprint", {}) or {}

            craw = meta.get("completeDate") or meta.get("endDate") or s.get("endDate") or s.get("completeDate")

            dt = parse_jira_date(craw)

        except Exception as e:

            print(f"[warn] sprint_report enrich failed for sprint {sid}: {e}", file=sys.stderr)

            craw, dt = None, None

        s["_complete_raw"] = craw

        s["_complete_dt"]  = dt

        enriched.append(s)

    enriched.sort(key=lambda x: ((x["_complete_dt"] is not None), x["_complete_dt"], x.get("id", 0)), reverse=True)

    return enriched





def pick_from_list(items, how_many=5, label_key="name"):

    shown = items[:how_many] if items else []

    if not shown:

        print(f'No closed sprints found starting with "{SPRINT_NAME_PREFIX}".', file=sys.stderr)

        sys.exit(2)

    print("\n== Pick a closed sprint (most recent first) ==")

    for i, it in enumerate(shown, 1):

        label = it.get(label_key) or it.get("name") or "(no name)"

        cd = it.get("_complete_raw") or it.get("completeDate") or it.get("endDate")

        print(f"{i:2d}. {label} (ID {it.get('id')}), complete/end: {cd}")

    while True:

        try:

            raw = input(f"Pick a number (1-{len(shown)}) [default=1]: ").strip() or "1"

            idx = int(raw)

            if 1 <= idx <= len(shown): return shown[idx-1]

        except Exception:

            pass

        print("Invalid selection. Try again.")



# ===================== MAIN =====================

def main():

    # Connectivity checks

    try:

        me = get_json(f"{JIRA_BASE}/rest/api/2/myself", J_HEADERS)

        print(f"Jira OK — hello {me.get('displayName','(unknown)')}")

    except Exception as e:

        print("Jira connectivity failed:", e, file=sys.stderr); sys.exit(2)

    try:

        _me2 = get_json(f"{CONF_BASE}/rest/api/user/current", C_HEADERS)

        print("Confluence OK")

    except Exception as e:

        print("Confluence connectivity failed:", e, file=sys.stderr); sys.exit(2)



    discover_field_ids()



    print(f'\nResolving board named: "{BOARD_NAME}" …')

    board = find_board_by_name(BOARD_NAME)

    print(f'Using board: {board.get("name")} (ID {board.get("id")})')



    print(f'\nFinding closed sprints with name starting "{SPRINT_NAME_PREFIX}" …')

    ordered = list_all_closed_prefix_sorted(board["id"], SPRINT_NAME_PREFIX)

    recent5 = ordered[:5]                              # ← same set we will chart

    sprint = pick_from_list(recent5, how_many=5, label_key="name")

    if not sprint:

        print("[fatal] No sprint selected.", file=sys.stderr); sys.exit(2)



    print("\nDownloading sprint report …")

    report = sprint_report(board["id"], sprint["id"])



    # Committed/Completed from Velocity (exact), else fallback

    est_v, comp_v = get_committed_completed_from_velocity(board["id"], sprint["id"])

    if est_v is not None and comp_v is not None:

        committed_sp, completed_sp = est_v, comp_v

        _, _, scope_change_count, carry_over_count = compute_committed_completed_from_report(report)

    else:

        committed_sp, completed_sp, scope_change_count, carry_over_count = compute_committed_completed_from_report(report)



    # Sprint meta → planned/actual dates & working-day durations

    sprint_meta = report.get("sprint", {}) or {}

    planned_start_dt = parse_jira_date(sprint_meta.get("startDate"))

    planned_end_dt   = parse_jira_date(sprint_meta.get("endDate"))

    actual_start_dt  = parse_jira_date(sprint_meta.get("activatedDate") or sprint_meta.get("startDate"))

    actual_end_dt    = parse_jira_date(sprint_meta.get("completeDate") or sprint_meta.get("endDate"))



    planned_start_ist = to_ist_str(sprint_meta.get("startDate"))

    planned_end_ist   = to_ist_str(sprint_meta.get("endDate"))

    actual_start_ist  = to_ist_str(sprint_meta.get("activatedDate") or sprint_meta.get("startDate"))

    actual_end_ist    = to_ist_str(sprint_meta.get("completeDate") or sprint_meta.get("endDate"))



    planned_wd = working_days_inclusive(planned_start_dt, planned_end_dt)

    actual_wd  = working_days_inclusive(actual_start_dt, actual_end_dt)



    # Sections: tables

    top5      = top5_completed_stories(report, sprint["id"])       # includes Status

    techdebts = completed_tech_debts_with_sp(report, sprint["id"]) # includes Status



    # Team members (unique, bots filtered)

    def unique_assignees_from_sprint(sprint_id):

        regex = re.compile(EXCLUDE_ASSIGNEE_REGEX, re.I) if EXCLUDE_ASSIGNEE_REGEX else None

        issues, _ = agile_sprint_issues_paginated(sprint_id, None, ["assignee"], max_pages=200, page_size=200)

        seen, names = set(), []

        for it in issues:

            person = (it.get("fields") or {}).get("assignee")

            if not person: continue

            acc_id = person.get("accountId") or person.get("name") or person.get("emailAddress")

            display = person.get("displayName")

            email = person.get("emailAddress") or ""

            if not acc_id or not display: continue

            if regex and (regex.search(display) or (email and regex.search(email))): continue

            if acc_id in seen: continue

            seen.add(acc_id); names.append(display)

        return sorted(names)



    team_names = unique_assignees_from_sprint(sprint["id"])



    # Audits

    no_epic_rows = stories_tasks_without_epic(sprint["id"])

    no_sp_rows   = stories_tasks_without_sp(sprint["id"])

    carry_rows   = carry_over_items_with_sp(report, sprint["id"])   # includes Status



    # Previous Retro Action Items (GV-2527) with Ageing

    try:

        retro_open_rows, retro_done_rows = fetch_epic_action_items(RETRO_EPIC_KEY)

    except Exception as e:

        print(f"[warn] Retro epic fetch failed: {e}", file=sys.stderr)

        retro_open_rows, retro_done_rows = [], []

    prev_retro_html = make_prev_retro_section_html(JIRA_BASE, retro_open_rows, retro_done_rows)



    # 1) INITIAL PAGE (no charts yet)

    page_title = sprint.get("name","Sprint " + str(sprint.get("id")))

    storage_temp = make_storage_html(

        JIRA_BASE, board, sprint,

        committed_sp, completed_sp,

        scope_change_count, carry_over_count,

        planned_start_ist, planned_end_ist, actual_start_ist, actual_end_ist,

        planned_wd, actual_wd,

        top5, techdebts, team_names,

        no_epic_rows, no_sp_rows, carry_rows,

        prev_retro_html=prev_retro_html,

        charts_html=""

    )

    existing = find_existing_page(page_title)

    if existing:

        page_id = existing["id"]

        version = (existing.get("version") or {}).get("number", 1)

        update_page(page_id, page_title, storage_temp, version)

        version += 1

    else:

        resp = create_page(page_title, storage_temp)

        page_id = resp["id"]; version = 1



    # 2) RENDER & ATTACH CHARTS

    try:

        print("Rendering Jira charts …")

        # Burndown

        burndown_bytes = render_burndown_png_bytes(report, sprint["id"], committed_sp, board["id"])

        upload_or_update_attachment(page_id, BURNDOWN_FN, burndown_bytes, "image/png")



        # Velocity — show the SAME recent 5 sprints as in the picker (left→right oldest→newest)

        sprint_ids_for_chart = [s["id"] for s in reversed(recent5)]

        velocity_bytes = render_velocity_png_bytes(

            board["id"],

            board.get("name","Board"),

            SPRINT_NAME_PREFIX,

            highlight_sprint_id=sprint["id"],

            sprint_ids=sprint_ids_for_chart,

            max_sprints=5

        )

        upload_or_update_attachment(page_id, VELOCITY_FN, velocity_bytes, "image/png")



        # LinearB

        lb_pngs = {}

        s_start = parse_jira_date(sprint_meta.get("startDate")) or datetime.datetime.now()

        s_end   = parse_jira_date(sprint_meta.get("endDate"))   or s_start

        start_date = s_start.date().isoformat()

        end_date   = s_end.date().isoformat()



        rows = []

        if LINEARB_TOKEN:

            try:

                rows = linearb_measurements_daily(LINEARB_TEAM_ID, start_date, end_date)



                # ----- [A-1] PR-only Cycle (P50) proxy = Coding + Pickup + Review (all p50, hours) -----

                xs_coding,  hours_coding  = _lb_timeseries_complete(rows, start_date, end_date, "coding_min")

                xs_pickup,  hours_pickup  = _lb_timeseries_complete(rows, start_date, end_date, "pickup_min")

                xs_review,  hours_review  = _lb_timeseries_complete(rows, start_date, end_date, "review_min")



                lb_pngs[CODING_FN] = render_linearb_series_png_bytes(xs_coding, hours_coding, "Coding Time (P50)", "Duration (hours)")

                lb_pngs[PICKUP_FN] = render_linearb_series_png_bytes(xs_pickup, hours_pickup, "Pickup Time (P50)", "Duration (hours)")

                lb_pngs[REVIEW_FN] = render_linearb_series_png_bytes(xs_review, hours_review, "Review Time (P50)", "Duration (hours)")



                hours_cycle_proxy = [a + b + c for a, b, c in zip(hours_coding, hours_pickup, hours_review)]

                lb_pngs[CYCLE_FN] = render_linearb_series_png_bytes(xs_coding, hours_cycle_proxy, "Cycle Time (P50)", "Duration (hours)")

                # ------------------------------------------------------------------------------



            except Exception as e:

                print(f"[warn] LinearB measurements unavailable: {e}", file=sys.stderr)

        else:

            print("[warn] LinearB token missing; skipping LinearB charts.", file=sys.stderr)



        for fn, b in lb_pngs.items():

            upload_or_update_attachment(page_id, fn, b, "image/png")



        # Verify presence

        needed = {BURNDOWN_FN, VELOCITY_FN} | set(lb_pngs.keys())

        present = set(list_attachments(page_id).keys())

        missing = needed - present

        if missing:

            raise RuntimeError(f"Attachments missing on page {page_id}: {sorted(missing)}")



        # 3) CHART HTML block — stacked for readability

        charts_html = f"""

<ac:layout>

  <ac:layout-section ac:type="single">

    <ac:layout-cell>{panel("Sprint Burndown", image_block(BURNDOWN_FN, "Sprint Burndown", width=1100))}</ac:layout-cell>

  </ac:layout-section>

</ac:layout>

<ac:layout>

  <ac:layout-section ac:type="single">

    <ac:layout-cell>{panel("Velocity (Recent Sprints)", image_block(VELOCITY_FN, "Velocity", width=1100))}</ac:layout-cell>

  </ac:layout-section>

</ac:layout>

"""

        # LinearB charts — one per row (vertical)

        for title, fn in [("Coding Time (P50)", CODING_FN),

                          ("Pickup Time (P50)", PICKUP_FN),

                          ("Review Time (P50)", REVIEW_FN),

                          ("Cycle Time (P50)",  CYCLE_FN)]:

            if fn in present:

                charts_html += f"""

<ac:layout>

  <ac:layout-section ac:type="single">

    <ac:layout-cell>{panel(title, image_block(fn, title, width=1100))}</ac:layout-cell>

  </ac:layout-section>

</ac:layout>

"""



        # 4) FINAL PAGE UPDATE (with charts)

        carry_rows = carry_over_items_with_sp(report, sprint["id"])  # ensure fresh

        storage_final = make_storage_html(

            JIRA_BASE, board, sprint,

            committed_sp, completed_sp,

            scope_change_count, carry_over_count,

            planned_start_ist, planned_end_ist, actual_start_ist, actual_end_ist,

            planned_wd, actual_wd,

            top5, techdebts, team_names,

            no_epic_rows, no_sp_rows, carry_rows,

            prev_retro_html=prev_retro_html,

            charts_html=charts_html

        )

        update_page(page_id, page_title, storage_final, version)



    except Exception as e:

        print(f"[warn] Chart block failed unexpectedly: {e}", file=sys.stderr)



    # Labels

    labels = list(DEFAULT_LABELS) + [f"sprint-{sprint['id']}"]

    try: post_labels(page_id, labels)

    except Exception as e: print(f"[warn] Labeling failed: {e}", file=sys.stderr)



    print("Done. Page ID:", page_id, "| Version:", SCRIPT_VERSION)



# ------------------ MAIN ------------------

if __name__ == "__main__":

    main()
