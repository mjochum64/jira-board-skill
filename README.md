# Jira Board Skill for Claude Code

A Claude Code skill for managing Jira boards, issues, and sprints via the Jira REST API. Supports both Jira Cloud and Jira Server/Data Center, including instances behind Azure AD authentication.

## Features

- List, create, update, and transition Jira issues
- Manage sprints and boards
- Query issues with JQL
- **Bearer Token (PAT) authentication** - Default for Jira Data Center
- **Basic Auth** - Optional for Jira Cloud
- **Azure AD Support**: Automatic browser-based login for Jira instances behind Azure AD/SSO

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/jira-board-skill.git ~/.claude/skills/jira-board
```

### 2. Configure environment variables

Add these to your shell profile (`~/.bashrc`, `~/.zshrc`, etc.):

```bash
export JIRA_URL="https://your-company.atlassian.net"  # or your Jira Server URL
export JIRA_USERNAME="your-email@company.com"         # Optional for Bearer auth
export JIRA_API_TOKEN="your-api-token-or-pat"

# Optional: Default project filter
export JIRA_PROJECTS_FILTER="PROJ,DEV"

# Optional: Authentication type (default: "bearer")
# Use "basic" for Jira Cloud with API tokens
export JIRA_AUTH_TYPE="bearer"
```

**Getting an API token:**
- **Jira Data Center 8.14+**: Profile → Personal Access Tokens (use Bearer auth - default)
- **Jira Cloud**: https://id.atlassian.com/manage-profile/security/api-tokens (set `JIRA_AUTH_TYPE=basic`)

## Usage

### Basic Commands

```bash
# Get issue details
python ~/.claude/skills/jira-board/scripts/jira_api.py get PROJ-123

# List issues
python ~/.claude/skills/jira-board/scripts/jira_api.py issues --project PROJ

# List my issues in active sprint
python ~/.claude/skills/jira-board/scripts/jira_api.py issues --assignee me --sprint active

# Create an issue
python ~/.claude/skills/jira-board/scripts/jira_api.py create PROJ "Fix login bug" --type Bug

# Transition issue
python ~/.claude/skills/jira-board/scripts/jira_api.py transition PROJ-123 "In Progress"

# Add comment
python ~/.claude/skills/jira-board/scripts/jira_api.py comment PROJ-123 "Working on this"
```

### With Claude Code

Once installed, Claude Code will automatically use this skill when you ask about Jira:

```
> What's the status of PROJ-123?
> Show me my open issues in the current sprint
> Create a bug ticket for the login issue
```

## Azure AD / SSO Support (Optional)

> **Note:** This section only applies if your Jira instance is behind Azure AD, SAML, or similar SSO. If you have direct access to Jira (e.g., via VPN or Jira Cloud with API token), you can skip this section entirely.

### Install Playwright (only needed for SSO)

```bash
pip install playwright
playwright install chromium
```

### How it works

If your Jira instance is behind Azure AD (or similar SSO), the skill will automatically:

1. Detect when authentication is needed
2. Open a browser window for you to log in
3. Save the session cookies for future requests
4. Automatically refresh when the session expires

### Manual Login

You can also trigger the login manually:

```bash
python ~/.claude/skills/jira-board/scripts/jira_auth.py login
```

### Session Management

```bash
# Test if session is valid
python ~/.claude/skills/jira-board/scripts/jira_auth.py test

# View saved cookies (for debugging)
python ~/.claude/skills/jira-board/scripts/jira_auth.py cookies
```

Session cookies are stored in `~/.config/jira/session_cookies.json`.

## Command Reference

### Issue Operations

| Command | Description |
|---------|-------------|
| `get ISSUE` | Get issue details |
| `issues [options]` | List issues with filters |
| `create PROJECT "Summary"` | Create new issue |
| `update ISSUE [options]` | Update issue fields |
| `transition ISSUE "Status"` | Change issue status |
| `assign ISSUE username` | Assign issue (use `me` for yourself) |
| `comment ISSUE "text"` | Add comment to issue |

### Board Operations

| Command | Description |
|---------|-------------|
| `boards` | List all boards |
| `board-issues BOARD_ID` | List issues on board |

### Sprint Operations

| Command | Description |
|---------|-------------|
| `sprints BOARD_ID` | List sprints |
| `sprint-issues SPRINT_ID` | List issues in sprint |
| `create-sprint BOARD_ID "Name"` | Create new sprint |
| `start-sprint SPRINT_ID` | Start a sprint |
| `close-sprint SPRINT_ID` | Close a sprint |
| `move-to-sprint SPRINT_ID ISSUE...` | Move issues to sprint |

### Common Options

| Option | Description |
|--------|-------------|
| `--project, -p` | Filter by project key(s) |
| `--status, -s` | Filter by status |
| `--assignee, -a` | Filter by assignee (`me` for yourself) |
| `--sprint` | Filter by sprint (`active` for current) |
| `--jql` | Custom JQL query |
| `--verbose, -v` | Show detailed output |

## JQL Examples

```bash
# High priority bugs
python jira_api.py issues --jql 'priority = High AND type = Bug'

# Recently updated
python jira_api.py issues --jql 'updated >= -7d ORDER BY updated DESC'

# Unassigned issues
python jira_api.py issues --jql 'project = PROJ AND assignee IS EMPTY'

# Issues in current sprint
python jira_api.py issues --jql 'sprint in openSprints()'
```

## Troubleshooting

### "Session expired" errors

Run the login command to refresh your session:
```bash
python ~/.claude/skills/jira-board/scripts/jira_auth.py login
```

### "403 Forbidden" or "AUTHENTICATION_DENIED"

This usually means:
1. **Wrong auth type**: Jira Data Center typically requires Bearer tokens (PAT), not Basic Auth. Ensure `JIRA_AUTH_TYPE` is not set or set to `bearer`.
2. **CAPTCHA challenge**: Too many failed login attempts triggered a CAPTCHA. Log in via browser once to reset.
3. **Basic Auth disabled**: Your Jira admin may have disabled Basic Auth. Use a Personal Access Token instead.

### "Connection error"

Check that:
1. `JIRA_URL` is set correctly
2. You can reach the Jira instance from your network
3. VPN is connected (if required)

### Browser doesn't open for login (SSO only)

If you're behind Azure AD/SSO, ensure Playwright and Chromium are installed:
```bash
pip install playwright
playwright install chromium
```

## Security Notes

- API tokens and session cookies are stored locally and never transmitted except to your Jira instance
- Session cookies are stored in `~/.config/jira/` with user-only permissions
- Never commit your API tokens or session cookies to version control

## License

MIT

## Contributing

Contributions welcome! Please open an issue or pull request.
