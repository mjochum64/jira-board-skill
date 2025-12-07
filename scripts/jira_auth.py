#!/usr/bin/env python3
"""
Jira Authentication Helper for Azure AD protected instances.

Usage:
    # Initial login (opens browser for Azure AD auth)
    python jira_auth.py login

    # Test if session is valid
    python jira_auth.py test

    # Get cookies for curl/API usage
    python jira_auth.py cookies

    # Fetch a Jira issue
    python jira_auth.py issue DP-217
"""

import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Cookie storage location
COOKIE_FILE = Path.home() / ".config" / "jira" / "session_cookies.json"

def ensure_cookie_dir():
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)

def get_jira_url():
    url = os.environ.get("JIRA_URL")
    if not url:
        print("Error: JIRA_URL environment variable not set", file=sys.stderr)
        sys.exit(1)
    return url

def login():
    """Open browser for Azure AD login and save session cookies."""
    from playwright.sync_api import sync_playwright
    import time

    jira_url = get_jira_url()
    ensure_cookie_dir()

    print(f"Opening browser for Jira login: {jira_url}")
    print("Complete the Azure AD login, then Jira login if prompted.")
    print("The browser will close automatically after successful login.\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        # Navigate to Jira
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
                    elif status == 'waiting':
                        print("Waiting for page to load...")
                    last_status = status

                time.sleep(1)

            except Exception:
                time.sleep(1)

        if not logged_in:
            print("Login timeout", file=sys.stderr)
            browser.close()
            sys.exit(1)

        # Wait for page to stabilize
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)

        # Get all cookies
        cookies = context.cookies()

        # Filter cookies for Jira domain
        jira_host = jira_url.replace('https://', '').replace('http://', '').split('/')[0]
        jira_cookies = [c for c in cookies if jira_host in c.get('domain', '').lower()
                      or 'jira' in c.get('domain', '').lower()]

        # Save cookies with metadata
        cookie_data = {
            "saved_at": datetime.now().isoformat(),
            "jira_url": jira_url,
            "cookies": jira_cookies
        }

        with open(COOKIE_FILE, 'w') as f:
            json.dump(cookie_data, f, indent=2)

        print(f"\nSaved {len(jira_cookies)} cookies to {COOKIE_FILE}")
        browser.close()

def load_cookies():
    """Load saved cookies from file."""
    if not COOKIE_FILE.exists():
        print(f"No saved session found. Run 'jira_auth.py login' first.", file=sys.stderr)
        sys.exit(1)

    with open(COOKIE_FILE) as f:
        return json.load(f)

def get_cookie_header():
    """Return cookies formatted for HTTP header."""
    data = load_cookies()
    cookies = data.get("cookies", [])

    # Build cookie string
    cookie_parts = []
    for c in cookies:
        cookie_parts.append(f"{c['name']}={c['value']}")

    return "; ".join(cookie_parts)

def test_session():
    """Test if the saved session is still valid."""
    import urllib.request
    import urllib.error

    jira_url = get_jira_url()
    cookie_header = get_cookie_header()

    test_url = f"{jira_url}/rest/api/2/myself"

    req = urllib.request.Request(test_url)
    req.add_header("Cookie", cookie_header)
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            print(f"Session valid! Logged in as: {data.get('displayName', 'Unknown')}")
            return True
    except urllib.error.HTTPError as e:
        if e.code == 401 or e.code == 302:
            print("Session expired. Run 'jira_auth.py login' to re-authenticate.", file=sys.stderr)
        else:
            print(f"HTTP Error {e.code}: {e.reason}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return False

def fetch_issue(issue_key):
    """Fetch a Jira issue and display its status."""
    import urllib.request
    import urllib.error

    jira_url = get_jira_url()
    cookie_header = get_cookie_header()

    api_url = f"{jira_url}/rest/api/2/issue/{issue_key}"

    req = urllib.request.Request(api_url)
    req.add_header("Cookie", cookie_header)
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())
            fields = data.get("fields", {})

            # Extract key information
            status = fields.get("status", {}).get("name", "Unknown")
            summary = fields.get("summary", "No summary")
            issue_type = fields.get("issuetype", {}).get("name", "Unknown")
            priority = fields.get("priority", {}).get("name", "Unknown")
            assignee = fields.get("assignee", {})
            assignee_name = assignee.get("displayName", "Unassigned") if assignee else "Unassigned"
            reporter = fields.get("reporter", {}).get("displayName", "Unknown")
            created = fields.get("created", "")[:10]
            updated = fields.get("updated", "")[:10]

            print(f"\n{'='*60}")
            print(f"  {issue_key}: {summary}")
            print(f"{'='*60}")
            print(f"  Status:      {status}")
            print(f"  Type:        {issue_type}")
            print(f"  Priority:    {priority}")
            print(f"  Assignee:    {assignee_name}")
            print(f"  Reporter:    {reporter}")
            print(f"  Created:     {created}")
            print(f"  Updated:     {updated}")
            print(f"{'='*60}\n")

            # Print description if present
            description = fields.get("description")
            if description:
                print("Description:")
                print("-" * 40)
                # Truncate long descriptions
                if len(description) > 500:
                    print(description[:500] + "...")
                else:
                    print(description)
                print()

            # Return raw data for further processing
            return data

    except urllib.error.HTTPError as e:
        if e.code == 401 or e.code == 302:
            print("Session expired. Run 'jira_auth.py login' to re-authenticate.", file=sys.stderr)
        elif e.code == 404:
            print(f"Issue {issue_key} not found.", file=sys.stderr)
        else:
            print(f"HTTP Error {e.code}: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

def print_cookies():
    """Print cookies in a format suitable for curl."""
    cookie_header = get_cookie_header()
    print(cookie_header)

def main():
    parser = argparse.ArgumentParser(description="Jira Authentication Helper for Azure AD")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Login command
    subparsers.add_parser("login", help="Open browser for Azure AD login")

    # Test command
    subparsers.add_parser("test", help="Test if session is valid")

    # Cookies command
    subparsers.add_parser("cookies", help="Print cookies for curl/API usage")

    # Issue command
    issue_parser = subparsers.add_parser("issue", help="Fetch a Jira issue")
    issue_parser.add_argument("issue_key", help="Issue key (e.g., DP-217)")

    args = parser.parse_args()

    if args.command == "login":
        login()
    elif args.command == "test":
        if test_session():
            sys.exit(0)
        else:
            sys.exit(1)
    elif args.command == "cookies":
        print_cookies()
    elif args.command == "issue":
        fetch_issue(args.issue_key)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
