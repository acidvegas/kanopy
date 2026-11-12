# kanopy

A small self-hosted kanban board for projects, todos and ideas, built to be shared between people and their AI agents *(Claude Code, opencode, anything that speaks MCP)*.

![Board](.screens/board.png)

| Project board                    | Task page                  |
|----------------------------------|----------------------------|
| ![Project](.screens/project.png) | ![Task](.screens/task.png) |

| Calendar                           | Themes                         |
|------------------------------------|--------------------------------|
| ![Calendar](.screens/calendar.png) | ![Themes](.screens/themes.png) |

*Screenshots use invented demo data.*

## Features

- **Board:** To-Do, Doing and Done columns with drag and drop *(touch works too)*; a project's board includes its sub-projects
- **Projects:** tree addressed by path *(`AI/hermes`)* with open task counts and colors *(sub-projects get a random one)*; drag in the sidebar to reorder, drop onto another project to nest it, drop onto "All tasks" to make it top-level *(desktop only; on a phone, move a project by renaming its path)*
- **Project description:** markdown panel above the board, collapsible, edit in place; the project color and folder *(the local directory a repo project lives in, used by the agent session briefing)* are set in the same editor
- **Task page:** `#/task/123` opens a task as a full page *(wide description and comments, details on the side, previous/next within its column)*; the expand button in the side panel or the corner button on a card *(new tab)* opens it, search results and notifications link to it
- **Task panel:** title, project, status, priority, due date, reminder, `namespace:value` labels, markdown description with clickable checklists, links *(blocks, subtask, related, ...; type a number or title to pick the task)*, file attachments *(up to 25 MB each; drop files on the panel)* and comments; drag a card onto a sidebar project to move it
- **History:** every change to a task is logged with who made it, plus the agent name for changes over MCP; shown newest first in a collapsible section on the task panel and page *(the newest 200 events per task are kept; description edits are noted without the text)*
- **Bulk select:** the checkbox button on the board turns on selection; move the picked tasks to a status or project, add or remove a label, or delete them
- **Auto-archive:** done tasks leave the board after a number of days set on the Settings page *(default 30, 0 turns it off)*; they stay in search and reopening one brings it back
- **Markdown editor:** task descriptions, comments and project descriptions get Write/Preview tabs, a toolbar *(bold, italic, heading, code, link, lists, checklist, quote, task link)*, Ctrl+B/I/K, and Enter continues a list
- **Calendar:** month grid of due dates and reminders; drag to reschedule
- **Labels:** global *(shared with everyone, managed by the admin; a fresh install starts with a set of `type:` labels like bug, feature, docs, research, purchase, note)* or personal *(listed only for you, though people on a shared project see them on its tasks, faded)*; on the Settings page, grouped by namespace with counts; rename, merge *(rename onto an existing label)*, recolor, delete
- **Search:** every word must match across titles, descriptions and comments; filter by project *(sub-projects included)*, label, minimum priority, due date and status, choose which fields to search, and sort; filters alone list matching tasks
- **Notifications:** ntfy pushes at reminder time, on the morning a task is due, a daily overdue digest, and when an agent marks a task done; each one toggled on the Settings page *(the ntfy token is never shown back)*
- `#123` in a description or comment links to that task; the filter box matches `#id`, text, project and labels
- Stored in SQLite at `data/kanban.db`, attachments included; backed up daily to `data/backups`, newest 3 kept

## Accounts

- Everyone signs in with a username and password *(scrypt hashes, 30-day session cookie)*; the admin creates single-use invite links *(7 days)* on the Settings page and new people sign up from the link
- Projects are private to whoever created them; share one with users or teams as **viewer** *(read)*, **editor** *(work on tasks)* or **manager** *(also edit, archive and share)*; sub-projects come with it. Admins see everything
- **Teams:** anyone can create one; owners add members by username and can promote other owners
- **Assignees:** any number per task, shown on cards; `@me` in the filter box or "Assigned to me" in the sidebar, and an assignee filter on the search page
- **Notifications:** `@username` mentions *(with autocomplete)*, assignments and activity on your tasks land in the inbox *(bell)* and on your own ntfy topic; each kind toggles on your account
- Profile pictures: Settings, Account, Change picture *(cropped square and resized in the browser)*; shown wherever initials would be
- Themes: Settings, Theme; ten to pick from *(the purple default, Dracula, Nord, Gruvbox, Catppuccin Mocha, Tokyo Night, One Dark, Monokai, GitHub Dark, GitHub Light)*, remembered per browser in the `kb_theme` cookie; the logo and favicon take the theme's colors
- Labels: anyone can make personal labels; only the admin creates, renames or deletes global ones. Anyone who can edit a task can remove any label from it
- Create the first admin with `venv/bin/python -c "import kanban, accounts; kanban.init(); accounts.user_create('name', 'password', True)"`

## Keys

| Key   | Action                                          |
|-------|-------------------------------------------------|
| `/`   | Filter                                          |
| `n`   | New task *(new label on Settings)*              |
| `Esc` | Close the task panel or page, leave select mode |

## MCP

The app serves an MCP endpoint at `/mcp` *(streamable HTTP, JSON responses, stateless)* for Claude Code and Hermes. The `X-Agent` header tags comments with the agent's name.

| Tool            | Does                                                                                                                                                                         |
|-----------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `list_projects` | Project tree with open task counts                                                                                                                                           |
| `save_project`  | Rename/move, describe, set the folder, archive; new projects come from `save_task`                                                                                               |
| `list_tasks`    | Grouped by project; filter by project *(recursive)*, labels, text *(titles, descriptions and comments)*, status, priority, due; `changed` lists recent activity newest first |
| `get_task`      | One or more tasks: markdown description, relations, comment log; or the text of one attachment                                                                               |
| `save_task`     | Create or update any field, move, add/remove labels, set status, tick a checklist item, add a comment or text file; a new project path creates the project *(parents included)* with the task, so no project is ever empty; `ids` updates several tasks at once                      |
| `comment`       | Progress note                                                                                                                                                                |
| `link`          | Relate two tasks or remove the relation                                                                                                                                      |
| `labels`        | List global and your own labels by namespace, create a global `type:` value *(admin)*, delete a label                                                                         |
| `inbox`         | Unread notifications, optionally marking them read                                                                                                                           |
| `delete`        | A task, or an empty project                                                                                                                                                  |

Agent labels use two namespaces: `host:` *(any machine, created on demand as the token user's personal label)* and `type:` *(a fixed set of values)*; a new `host:` that squashes to an existing one *(host:web01 vs host:web-01)* is rejected. The instructions agents get on connect come from `instructions.txt` if you create one *(untracked)*, otherwise `instructions.example.txt`.

`/mcp` takes a personal API token *(Settings, API tokens)* in the `X-Key` header; the agent acts as that user and sees only what they can. The `X-Agent` header names the agent on its comments. `KANOPY_KEY` in `.env` is the old shared key; on start it is stored as the admin's token so existing agent configs keep working.

`GET /api/context?cwd=<dir>` returns a short plain-text briefing for a session: the project whose folder is the deepest one containing `dir` *(`~/code/myapp`)*, its doing and open tasks *(top 10)*, and the doing/comment/done rule. It is empty when no project matches. The hook and plugin below add it to each new session.

`/task <what>` files a task right away *(no proposal step)*; a bare `/task` files the work under discussion.

### Claude Code

1. Add the MCP server:
```sh
claude mcp add --transport http -s user kanopy http://HOST:PORT/mcp --header "X-Agent: claude" --header "X-Key: $TOKEN"
```

2. Add `hook.sh` as a `SessionStart` hook in `~/.claude/settings.json`. It reads `KANOPY_KEY`, `KANOPY_HOST` and `KANOPY_PORT` from kanopy's `.env` and prints the briefing for `$PWD`:
```json
{
	"hooks": {
		"SessionStart": [{ "hooks": [{ "type": "command", "command": "/path/to/kanopy/hook.sh" }] }]
	}
}
```

3. Link the `/task` command:
```sh
ln -s /path/to/kanopy/commands/task.md ~/.claude/commands/task.md
```

### opencode

1. Add the MCP server to `~/.config/opencode/opencode.json`, with your token saved in `~/.config/opencode/secrets/kanopy.token`:
```json
{
	"mcp": {
		"kanopy": {
			"type": "remote",
			"url": "http://HOST:PORT/mcp",
			"headers": { "X-Agent": "opencode", "X-Key": "{file:~/.config/opencode/secrets/kanopy.token}" }
		}
	}
}
```

2. Link the session briefing plugin. It reads the same token file and talks to `KANOPY_URL` *(default `http://127.0.0.1:8000`)*:
```sh
ln -s /path/to/kanopy/opencode/kanopy.ts ~/.config/opencode/plugins/kanopy.ts
```

3. Link the `/task` command:
```sh
ln -s /path/to/kanopy/commands/task.md ~/.config/opencode/command/task.md
```

## Setup

```sh
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env # host and port; KANOPY_KEY (optional) becomes the admin's API token
cp instructions.example.txt instructions.txt # optional: describe your own project layout for agents
sudo cp kanopy.service /etc/systemd/system/ # set YOUR_USER and /path/to/kanopy first
sudo systemctl enable --now kanopy
venv/bin/python -c "import kanban, accounts; kanban.init(); accounts.user_create('name', 'password', True)" # first admin
```

### Docker

```sh
cp .env.example .env
mkdir -p data # created first so it is owned by you, not root
docker compose up -d --build
docker compose exec kanopy python -c "import accounts; accounts.user_create('name', 'password', True)" # first admin
```

`data/` is bind-mounted, so the database, backups and attachments are the same files the venv setup uses. The container shares the host network and listens on `KANOPY_HOST:KANOPY_PORT` from `.env`. `instructions.txt` is copied in at build time; rebuild after changing it.

Host and port come from `.env` *(default 127.0.0.1:8000)*; notification links and the social preview follow them.. The frontend libraries *(marked, DOMPurify, SortableJS)* are vendored in `static/vendor`.
