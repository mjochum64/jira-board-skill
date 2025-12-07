#!/usr/bin/env python3
"""
Jira API utility for board organization.
Uses environment variables: JIRA_URL, JIRA_USERNAME, JIRA_API_TOKEN, JIRA_PROJECTS_FILTER

For Azure AD protected instances, uses session cookies from jira_auth.py.
"""

import os
import sys
import json
import argparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from pathlib import Path
import base64


# Cookie storage location (shared with jira_auth.py)
COOKIE_FILE = Path.home() / ".config" / "jira" / "session_cookies.json"


def get_session_cookies():
    """Load session cookies from file (for Azure AD auth)."""
    if not COOKIE_FILE.exists():
        return None
    try:
        with open(COOKIE_FILE) as f:
            data = json.load(f)
            cookies = data.get("cookies", [])
            if cookies:
                return "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    except Exception:
        pass
    return None


def get_auth_header():
    """Get auth header - tries Bearer token first (for PAT), then Basic Auth."""
    token = os.environ.get("JIRA_API_TOKEN")
    username = os.environ.get("JIRA_USERNAME")

    if not token:
        return None

    # If JIRA_AUTH_TYPE is set to "basic", use Basic Auth
    auth_type = os.environ.get("JIRA_AUTH_TYPE", "bearer").lower()

    if auth_type == "basic" and username:
        credentials = f"{username}:{token}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded}"

    # Default: Bearer token (for Personal Access Tokens)
    return f"Bearer {token}"


def get_jira_url():
    """Get Jira base URL."""
    url = os.environ.get("JIRA_URL")
    if not url:
        print("Error: JIRA_URL must be set", file=sys.stderr)
        sys.exit(1)
    return url.rstrip("/")


def get_projects_filter():
    """Get project filter (comma-separated project keys)."""
    return os.environ.get("JIRA_PROJECTS_FILTER", "")


def trigger_auto_login():
    """Trigger automatic browser login if needed."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Run: pip install playwright", file=sys.stderr)
        return False

    import time

    jira_url = get_jira_url()
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)

    print(f"Session expired or not available. Opening browser for login...")
    print("Complete the Azure AD login, then Jira login if prompted.\n")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()

            page.goto(jira_url)

            # Wait for successful login with status updates
            max_wait = 300  # 5 minutes
            start = time.time()
            logged_in = False
            last_status = None

            while time.time() - start < max_wait:
                try:
                    status = page.evaluate("""() => {
                        const host = window.location.hostname.toLowerCase();

                        // Still on Azure login?
                        if (host.includes('microsoftonline') || host.includes('login.microsoft')) {
                            return 'azure';
                        }

                        // Check for Jira login form elements
                        const hasLoginForm = document.querySelector('#login-form') !== null ||
                                             document.querySelector('input[name="os_username"]') !== null ||
                                             document.querySelector('input[name="password"]') !== null ||
                                             document.querySelector('.login-section') !== null ||
                                             document.querySelector('#login-container') !== null ||
                                             document.querySelector('form[action*="login"]') !== null;

                        if (hasLoginForm) {
                            return 'jira-login';
                        }

                        // Check for actual Jira dashboard/content
                        const hasContent = document.querySelector('#header') !== null ||
                                           document.querySelector('.aui-header') !== null ||
                                           document.querySelector('#jira') !== null ||
                                           document.querySelector('.ghx-board') !== null ||
                                           document.querySelector('#dashboard') !== null;

                        if (hasContent) {
                            return 'logged-in';
                        }

                        return 'waiting';
                    }""")

                    if status != last_status:
                        if status == 'azure':
                            print("Azure AD login in progress...")
                        elif status == 'jira-login':
                            print("Jira login page - please enter your credentials...")
                        elif status == 'logged-in':
                            print("Login successful!")
                            logged_in = True
                            break
                        last_status = status

                    time.sleep(1)

                except Exception:
                    time.sleep(1)

            if not logged_in:
                print("Login timeout")
                browser.close()
                return False

            # Wait for page to stabilize
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)

            cookies = context.cookies()
            # Filter cookies for Jira domain
            jira_host = get_jira_url().replace('https://', '').replace('http://', '').split('/')[0]
            jira_cookies = [c for c in cookies if jira_host in c.get('domain', '').lower()
                          or 'jira' in c.get('domain', '').lower()]

            from datetime import datetime
            cookie_data = {
                "saved_at": datetime.now().isoformat(),
                "jira_url": jira_url,
                "cookies": jira_cookies
            }

            with open(COOKIE_FILE, 'w') as f:
                json.dump(cookie_data, f, indent=2)

            print(f"Saved {len(jira_cookies)} cookies.\n")
            browser.close()
            return True
    except Exception as e:
        print(f"Login failed: {e}", file=sys.stderr)
        return False


def jira_request(endpoint, method="GET", data=None, _retry=True):
    """Make a Jira API request. Auto-triggers browser login if needed."""
    # Use API v2 for better compatibility with Jira Server/Data Center
    url = f"{get_jira_url()}/rest/api/2/{endpoint}"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    # Try session cookies first (for Azure AD), fallback to Basic Auth
    session_cookies = get_session_cookies()
    auth_header = get_auth_header()

    if session_cookies:
        headers["Cookie"] = session_cookies
    elif auth_header:
        headers["Authorization"] = auth_header
    else:
        # No auth available, try auto-login
        if _retry and trigger_auto_login():
            return jira_request(endpoint, method, data, _retry=False)
        print("Error: No authentication available.", file=sys.stderr)
        sys.exit(1)

    body = json.dumps(data).encode() if data else None
    req = Request(url, data=body, headers=headers, method=method)

    try:
        with urlopen(req) as response:
            if response.status == 204:
                return None
            return json.loads(response.read().decode())
    except HTTPError as e:
        # Check if redirected to login (Azure AD) or unauthorized
        if e.code == 302 or e.code == 401:
            if _retry:
                # Try auto-login and retry
                if trigger_auto_login():
                    return jira_request(endpoint, method, data, _retry=False)
            print("Authentication failed.", file=sys.stderr)
            sys.exit(1)
        error_body = e.read().decode() if e.fp else ""
        print(f"Error {e.code}: {error_body}", file=sys.stderr)
        sys.exit(1)
    except URLError as e:
        print(f"Connection error: {e.reason}", file=sys.stderr)
        sys.exit(1)


def jira_agile_request(endpoint, method="GET", data=None, _retry=True):
    """Make a Jira Agile API request. Auto-triggers browser login if needed."""
    url = f"{get_jira_url()}/rest/agile/1.0/{endpoint}"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    # Try session cookies first (for Azure AD), fallback to Basic Auth
    session_cookies = get_session_cookies()
    auth_header = get_auth_header()

    if session_cookies:
        headers["Cookie"] = session_cookies
    elif auth_header:
        headers["Authorization"] = auth_header
    else:
        # No auth available, try auto-login
        if _retry and trigger_auto_login():
            return jira_agile_request(endpoint, method, data, _retry=False)
        print("Error: No authentication available.", file=sys.stderr)
        sys.exit(1)

    body = json.dumps(data).encode() if data else None
    req = Request(url, data=body, headers=headers, method=method)

    try:
        with urlopen(req) as response:
            if response.status == 204:
                return None
            return json.loads(response.read().decode())
    except HTTPError as e:
        # Check if redirected to login (Azure AD) or unauthorized
        if e.code == 302 or e.code == 401:
            if _retry:
                if trigger_auto_login():
                    return jira_agile_request(endpoint, method, data, _retry=False)
            print("Authentication failed.", file=sys.stderr)
            sys.exit(1)
        error_body = e.read().decode() if e.fp else ""
        print(f"Error {e.code}: {error_body}", file=sys.stderr)
        sys.exit(1)
    except URLError as e:
        print(f"Connection error: {e.reason}", file=sys.stderr)
        sys.exit(1)


# === Issue Operations ===

def list_issues(project=None, status=None, assignee=None, sprint=None, jql=None, max_results=50):
    """List issues with optional filters."""
    if jql:
        query = jql
    else:
        conditions = []
        projects = project or get_projects_filter()
        if projects:
            project_list = ",".join(p.strip() for p in projects.split(","))
            conditions.append(f"project IN ({project_list})")
        if status:
            conditions.append(f'status = "{status}"')
        if assignee:
            if assignee.lower() == "me":
                conditions.append("assignee = currentUser()")
            else:
                conditions.append(f'assignee = "{assignee}"')
        if sprint:
            if sprint.lower() == "active":
                conditions.append("sprint in openSprints()")
            else:
                conditions.append(f'sprint = "{sprint}"')
        query = " AND ".join(conditions) if conditions else "ORDER BY created DESC"

    params = urlencode({"jql": query, "maxResults": max_results})
    result = jira_request(f"search?{params}")
    return result.get("issues", [])


def get_issue(issue_key):
    """Get a single issue by key."""
    return jira_request(f"issue/{issue_key}")


def create_issue(project, summary, issue_type="Task", description=None, assignee=None, priority=None, labels=None):
    """Create a new issue."""
    fields = {
        "project": {"key": project},
        "summary": summary,
        "issuetype": {"name": issue_type}
    }
    if description:
        fields["description"] = {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": description}]}]
        }
    if assignee:
        fields["assignee"] = {"accountId": assignee} if len(assignee) > 20 else {"name": assignee}
    if priority:
        fields["priority"] = {"name": priority}
    if labels:
        fields["labels"] = labels if isinstance(labels, list) else [labels]

    return jira_request("issue", method="POST", data={"fields": fields})


def update_issue(issue_key, summary=None, description=None, assignee=None, priority=None, labels=None):
    """Update an existing issue."""
    fields = {}
    if summary:
        fields["summary"] = summary
    if description:
        fields["description"] = {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": description}]}]
        }
    if assignee:
        fields["assignee"] = {"accountId": assignee} if len(assignee) > 20 else {"name": assignee}
    if priority:
        fields["priority"] = {"name": priority}
    if labels is not None:
        fields["labels"] = labels if isinstance(labels, list) else [labels]

    if fields:
        jira_request(f"issue/{issue_key}", method="PUT", data={"fields": fields})
    return get_issue(issue_key)


def transition_issue(issue_key, status):
    """Transition an issue to a new status."""
    transitions = jira_request(f"issue/{issue_key}/transitions")
    target = None
    for t in transitions.get("transitions", []):
        if t["name"].lower() == status.lower() or t["to"]["name"].lower() == status.lower():
            target = t["id"]
            break

    if not target:
        available = [t["name"] for t in transitions.get("transitions", [])]
        print(f"Error: Status '{status}' not found. Available: {available}", file=sys.stderr)
        sys.exit(1)

    jira_request(f"issue/{issue_key}/transitions", method="POST", data={"transition": {"id": target}})
    return get_issue(issue_key)


def assign_issue(issue_key, assignee):
    """Assign an issue to a user."""
    if assignee.lower() == "me":
        user = jira_request("myself")
        assignee_id = user["accountId"]
    elif len(assignee) > 20:
        assignee_id = assignee
    else:
        users = jira_request(f"user/search?query={assignee}")
        if not users:
            print(f"Error: User '{assignee}' not found", file=sys.stderr)
            sys.exit(1)
        assignee_id = users[0]["accountId"]

    jira_request(f"issue/{issue_key}/assignee", method="PUT", data={"accountId": assignee_id})
    return get_issue(issue_key)


def add_comment(issue_key, comment_text):
    """Add a comment to an issue."""
    body = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": comment_text}]}]
        }
    }
    return jira_request(f"issue/{issue_key}/comment", method="POST", data=body)


# === Board Operations ===

def list_boards(project=None):
    """List all boards, optionally filtered by project."""
    params = {}
    if project:
        params["projectKeyOrId"] = project
    query = urlencode(params) if params else ""
    endpoint = f"board?{query}" if query else "board"
    result = jira_agile_request(endpoint)
    return result.get("values", [])


def get_board(board_id):
    """Get board details."""
    return jira_agile_request(f"board/{board_id}")


def get_board_issues(board_id, sprint=None):
    """Get issues on a board."""
    if sprint and sprint.lower() == "active":
        sprints = jira_agile_request(f"board/{board_id}/sprint?state=active")
        if sprints.get("values"):
            sprint_id = sprints["values"][0]["id"]
            result = jira_agile_request(f"board/{board_id}/sprint/{sprint_id}/issue")
            return result.get("issues", [])
    result = jira_agile_request(f"board/{board_id}/issue")
    return result.get("issues", [])


# === Sprint Operations ===

def list_sprints(board_id, state=None):
    """List sprints for a board."""
    params = {"state": state} if state else {}
    query = urlencode(params) if params else ""
    endpoint = f"board/{board_id}/sprint?{query}" if query else f"board/{board_id}/sprint"
    result = jira_agile_request(endpoint)
    return result.get("values", [])


def get_sprint(sprint_id):
    """Get sprint details."""
    return jira_agile_request(f"sprint/{sprint_id}")


def create_sprint(board_id, name, start_date=None, end_date=None, goal=None):
    """Create a new sprint."""
    data = {"name": name, "originBoardId": board_id}
    if start_date:
        data["startDate"] = start_date
    if end_date:
        data["endDate"] = end_date
    if goal:
        data["goal"] = goal
    return jira_agile_request("sprint", method="POST", data=data)


def start_sprint(sprint_id, start_date=None, end_date=None):
    """Start a sprint."""
    data = {"state": "active"}
    if start_date:
        data["startDate"] = start_date
    if end_date:
        data["endDate"] = end_date
    return jira_agile_request(f"sprint/{sprint_id}", method="POST", data=data)


def close_sprint(sprint_id):
    """Close/complete a sprint."""
    return jira_agile_request(f"sprint/{sprint_id}", method="POST", data={"state": "closed"})


def move_to_sprint(sprint_id, issue_keys):
    """Move issues to a sprint."""
    if isinstance(issue_keys, str):
        issue_keys = [issue_keys]
    return jira_agile_request(f"sprint/{sprint_id}/issue", method="POST", data={"issues": issue_keys})


def get_sprint_issues(sprint_id):
    """Get all issues in a sprint."""
    result = jira_agile_request(f"sprint/{sprint_id}/issue")
    return result.get("issues", [])


# === Output Formatting ===

def format_issue(issue, verbose=False):
    """Format an issue for display."""
    fields = issue["fields"]
    key = issue["key"]
    summary = fields.get("summary", "")
    status = fields.get("status", {}).get("name", "Unknown")
    assignee = fields.get("assignee", {})
    assignee_name = assignee.get("displayName", "Unassigned") if assignee else "Unassigned"
    priority = fields.get("priority", {}).get("name", "None") if fields.get("priority") else "None"

    if verbose:
        issue_type = fields.get("issuetype", {}).get("name", "Unknown")
        labels = ", ".join(fields.get("labels", [])) or "None"
        return f"{key} [{status}] {summary}\n  Type: {issue_type} | Priority: {priority} | Assignee: {assignee_name}\n  Labels: {labels}"
    return f"{key} [{status}] {summary} (@{assignee_name})"


def format_sprint(sprint):
    """Format a sprint for display."""
    name = sprint.get("name", "Unknown")
    state = sprint.get("state", "unknown")
    goal = sprint.get("goal", "")
    start = sprint.get("startDate", "")[:10] if sprint.get("startDate") else "N/A"
    end = sprint.get("endDate", "")[:10] if sprint.get("endDate") else "N/A"
    return f"{sprint['id']}: {name} [{state}] ({start} - {end})" + (f"\n  Goal: {goal}" if goal else "")


def format_board(board):
    """Format a board for display."""
    return f"{board['id']}: {board['name']} ({board.get('type', 'unknown')})"


def main():
    parser = argparse.ArgumentParser(description="Jira Board Organization CLI")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Issues
    issues_parser = subparsers.add_parser("issues", help="List issues")
    issues_parser.add_argument("--project", "-p", help="Project key(s), comma-separated")
    issues_parser.add_argument("--status", "-s", help="Filter by status")
    issues_parser.add_argument("--assignee", "-a", help="Filter by assignee (use 'me' for yourself)")
    issues_parser.add_argument("--sprint", help="Filter by sprint (use 'active' for current)")
    issues_parser.add_argument("--jql", help="Custom JQL query")
    issues_parser.add_argument("--max", type=int, default=50, help="Max results")
    issues_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    # Get issue
    get_parser = subparsers.add_parser("get", help="Get issue details")
    get_parser.add_argument("issue_key", help="Issue key (e.g., PROJ-123)")

    # Create issue
    create_parser = subparsers.add_parser("create", help="Create an issue")
    create_parser.add_argument("project", help="Project key")
    create_parser.add_argument("summary", help="Issue summary")
    create_parser.add_argument("--type", "-t", default="Task", help="Issue type")
    create_parser.add_argument("--description", "-d", help="Description")
    create_parser.add_argument("--assignee", "-a", help="Assignee")
    create_parser.add_argument("--priority", "-p", help="Priority")
    create_parser.add_argument("--labels", "-l", nargs="+", help="Labels")

    # Update issue
    update_parser = subparsers.add_parser("update", help="Update an issue")
    update_parser.add_argument("issue_key", help="Issue key")
    update_parser.add_argument("--summary", "-s", help="New summary")
    update_parser.add_argument("--description", "-d", help="New description")
    update_parser.add_argument("--assignee", "-a", help="New assignee")
    update_parser.add_argument("--priority", "-p", help="New priority")
    update_parser.add_argument("--labels", "-l", nargs="+", help="New labels")

    # Transition issue
    transition_parser = subparsers.add_parser("transition", help="Change issue status")
    transition_parser.add_argument("issue_key", help="Issue key")
    transition_parser.add_argument("status", help="Target status")

    # Assign issue
    assign_parser = subparsers.add_parser("assign", help="Assign an issue")
    assign_parser.add_argument("issue_key", help="Issue key")
    assign_parser.add_argument("assignee", help="Assignee (use 'me' for yourself)")

    # Comment
    comment_parser = subparsers.add_parser("comment", help="Add a comment")
    comment_parser.add_argument("issue_key", help="Issue key")
    comment_parser.add_argument("text", help="Comment text")

    # Boards
    boards_parser = subparsers.add_parser("boards", help="List boards")
    boards_parser.add_argument("--project", "-p", help="Filter by project")

    # Board issues
    board_issues_parser = subparsers.add_parser("board-issues", help="List issues on a board")
    board_issues_parser.add_argument("board_id", type=int, help="Board ID")
    board_issues_parser.add_argument("--sprint", help="Filter by sprint (use 'active' for current)")
    board_issues_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    # Sprints
    sprints_parser = subparsers.add_parser("sprints", help="List sprints")
    sprints_parser.add_argument("board_id", type=int, help="Board ID")
    sprints_parser.add_argument("--state", choices=["active", "future", "closed"], help="Filter by state")

    # Create sprint
    create_sprint_parser = subparsers.add_parser("create-sprint", help="Create a sprint")
    create_sprint_parser.add_argument("board_id", type=int, help="Board ID")
    create_sprint_parser.add_argument("name", help="Sprint name")
    create_sprint_parser.add_argument("--start", help="Start date (ISO format)")
    create_sprint_parser.add_argument("--end", help="End date (ISO format)")
    create_sprint_parser.add_argument("--goal", help="Sprint goal")

    # Start sprint
    start_sprint_parser = subparsers.add_parser("start-sprint", help="Start a sprint")
    start_sprint_parser.add_argument("sprint_id", type=int, help="Sprint ID")
    start_sprint_parser.add_argument("--start", help="Start date (ISO format)")
    start_sprint_parser.add_argument("--end", help="End date (ISO format)")

    # Close sprint
    close_sprint_parser = subparsers.add_parser("close-sprint", help="Close a sprint")
    close_sprint_parser.add_argument("sprint_id", type=int, help="Sprint ID")

    # Move to sprint
    move_parser = subparsers.add_parser("move-to-sprint", help="Move issues to sprint")
    move_parser.add_argument("sprint_id", type=int, help="Sprint ID")
    move_parser.add_argument("issues", nargs="+", help="Issue keys")

    # Sprint issues
    sprint_issues_parser = subparsers.add_parser("sprint-issues", help="List issues in a sprint")
    sprint_issues_parser.add_argument("sprint_id", type=int, help="Sprint ID")
    sprint_issues_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Execute commands
    if args.command == "issues":
        issues = list_issues(args.project, args.status, args.assignee, args.sprint, args.jql, args.max)
        for issue in issues:
            print(format_issue(issue, args.verbose))

    elif args.command == "get":
        issue = get_issue(args.issue_key)
        print(json.dumps(issue, indent=2))

    elif args.command == "create":
        result = create_issue(args.project, args.summary, args.type, args.description, args.assignee, args.priority, args.labels)
        print(f"Created: {result['key']}")

    elif args.command == "update":
        result = update_issue(args.issue_key, args.summary, args.description, args.assignee, args.priority, args.labels)
        print(f"Updated: {format_issue(result)}")

    elif args.command == "transition":
        result = transition_issue(args.issue_key, args.status)
        print(f"Transitioned: {format_issue(result)}")

    elif args.command == "assign":
        result = assign_issue(args.issue_key, args.assignee)
        print(f"Assigned: {format_issue(result)}")

    elif args.command == "comment":
        add_comment(args.issue_key, args.text)
        print(f"Comment added to {args.issue_key}")

    elif args.command == "boards":
        boards = list_boards(args.project)
        for board in boards:
            print(format_board(board))

    elif args.command == "board-issues":
        issues = get_board_issues(args.board_id, args.sprint)
        for issue in issues:
            print(format_issue(issue, args.verbose))

    elif args.command == "sprints":
        sprints = list_sprints(args.board_id, args.state)
        for sprint in sprints:
            print(format_sprint(sprint))

    elif args.command == "create-sprint":
        result = create_sprint(args.board_id, args.name, args.start, args.end, args.goal)
        print(f"Created sprint: {format_sprint(result)}")

    elif args.command == "start-sprint":
        result = start_sprint(args.sprint_id, args.start, args.end)
        print(f"Started sprint: {format_sprint(result)}")

    elif args.command == "close-sprint":
        result = close_sprint(args.sprint_id)
        print(f"Closed sprint: {format_sprint(result)}")

    elif args.command == "move-to-sprint":
        move_to_sprint(args.sprint_id, args.issues)
        print(f"Moved {len(args.issues)} issue(s) to sprint {args.sprint_id}")

    elif args.command == "sprint-issues":
        issues = get_sprint_issues(args.sprint_id)
        for issue in issues:
            print(format_issue(issue, args.verbose))


if __name__ == "__main__":
    main()
