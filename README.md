<p align="center"><img src="static/icons/favicon.svg" width="96" alt=""></p>

<h1 align="center">kanopy</h1>

<p align="center">The self-hosted kanban board built for AI agents and the people they work with.</p>

<p align="center"><a href="https://acidvegas.github.io/kanopy">acidvegas.github.io/kanopy</a></p>

<p align="center">
	<a href="https://github.com/acidvegas/kanopy/releases/latest"><img src="https://img.shields.io/github/v/release/acidvegas/kanopy?color=a78bfa" alt="Release"></a>
	<a href="https://github.com/acidvegas/kanopy/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/acidvegas/kanopy/tests.yml?label=tests" alt="Tests"></a>
	<a href="https://github.com/acidvegas/kanopy/pkgs/container/kanopy"><img src="https://img.shields.io/badge/docker-ghcr.io-2496ed?logo=docker&logoColor=white" alt="Docker"></a>
	<img src="https://img.shields.io/badge/python-3.12%2B-3776ab?logo=python&logoColor=white" alt="Python 3.12+">
	<a href="LICENSE"><img src="https://img.shields.io/github/license/acidvegas/kanopy?color=f472b6" alt="License"></a>
</p>

<p align="center">
	<a href="#quick-start">Quick start</a> ·
	<a href="#why-kanopy">Why kanopy</a> ·
	<a href="#features">Features</a> ·
	<a href="#connecting-agents">Connecting agents</a> ·
	<a href="#mcp-tools">MCP tools</a> ·
	<a href="#configuration">Configuration</a>
</p>

![An agent filing, claiming and finishing a task](.screens/agent.gif)

kanopy keeps your projects, todos and ideas on one board, and lets the agents you work with read and update it the same way you do. Claude Code, opencode or anything else that speaks [MCP](https://modelcontextprotocol.io) can file tasks, claim them, move them through To-Do, Doing and Done, leave progress notes and pick up where the last session left off. Every change is logged with who made it, person or agent.

It is a single Python process with a SQLite database *(or PostgreSQL for bigger teams)* and a plain JavaScript frontend.

| Project board                    | Task page                  |
|----------------------------------|----------------------------|
| ![Project](.screens/project.png) | ![Task](.screens/task.png) |

| Calendar                           | Themes                         |
|------------------------------------|--------------------------------|
| ![Calendar](.screens/calendar.png) | ![Themes](.screens/themes.png) |

*Screenshots use invented demo data.*

## Quick start

With Docker, from the published image:

```sh
mkdir -p kanopy/data && cd kanopy
docker run -d --name kanopy -p 8000:8000 -e KANOPY_HOST=0.0.0.0 -v "$PWD/data:/app/data" --user "$(id -u):$(id -g)" ghcr.io/acidvegas/kanopy
```

Or with Docker Compose from a checkout:

```sh
git clone https://github.com/acidvegas/kanopy && cd kanopy
cp .env.example .env
mkdir -p data
docker compose up -d
```

Or with a virtual environment:

```sh
git clone https://github.com/acidvegas/kanopy && cd kanopy
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
venv/bin/python -m kanopy.app
```

Open `http://127.0.0.1:8000`. The first page asks you to create the admin account; everyone after that joins through an invite link.

To run it as a service, copy `kanopy.service` to `/etc/systemd/system/`, set `YOUR_USER` and `/path/to/kanopy`, then `sudo systemctl enable --now kanopy`.

## Why kanopy

kanopy was made for working with AI agents first. Before writing it we looked at the boards people already run: Vikunja, Planka and Jira. They are good boards built for people, and agents can reach all three today. Connecting an agent to a board is no longer the hard part. Working alongside several agents at once, without them clobbering each other or your edits, is.

|             | kanopy   | Vikunja                                                                        | Planka            | Jira                      |
|-------------|----------|--------------------------------------------------------------------------------|-------------------|---------------------------|
| Self-hosted | Yes      | Yes                                                                            | Yes               | Data Center only *(paid)* |
| MCP         | Built in | Built in since [2.7](https://vikunja.io/changelog/vikunja-2.7.0-was-released/) | Community servers | Atlassian's cloud server  |

What kanopy does for agents on top of that:

- **Every change names its agent.** Comments and history show the agent and the person whose token it used, like `claude (brandon)`, so you always know who did what.
- **Claims.** Moving a task to Doing claims it for that agent session. A second session that tries to start it is told who has it instead of doing the same work twice.
- **No lost edits.** An agent cannot replace a description it has not read, and `edit` and `append` change one line without resending the rest. If an agent does make a mess, the last 10 versions of a description can be restored, and deleted tasks sit in a 30-day trash.
- **Cheap on tokens.** Tools answer in compact text with one line per task instead of JSON. Fifty tasks come back in about 1,200 tokens.
- **Context at session start.** A Claude Code hook briefs each new session on the project for the current directory and its open tasks, flagging work left in Doing.
- **Rules and limits.** Agents get your working rules when they connect, can be given read-only tokens, and you get a push when one finishes a task.

## Features

### Board and projects

Projects form a tree addressed by path, like `Code/weatherbox`, and a project's board includes its sub-projects. Cards drag between To-Do, Doing and Done, onto another project in the sidebar, or onto a date in the month calendar. Each project has a markdown description panel and an **activity page** listing every change across it.

The Done column loads its newest 25 tasks with a button for more, done tasks archive themselves after a configurable number of days, and a bulk mode moves, labels or deletes many tasks at once. Deleted tasks stay in the trash on the Settings page for 30 days, with their comments, links and attachments, and can be restored.

### Tasks

A task carries a markdown description with clickable checklists, a priority, due date, reminder, labels, assignees, file attachments, comments and links to other tasks *(blocks, subtask, related, duplicates, precedes)*. It opens in a side panel or as a full page at `#/task/123`, and `#123` anywhere in text links to it. Long comment threads show the newest 30 first.

Tasks can **repeat** daily, weekly, monthly, yearly, every N days or on chosen weekdays. Marking one done creates the next occurrence with its checklist reset and its dates moved forward; upcoming ones show faded on the calendar.

Every task keeps a **history** of who changed what, including the agent's name for changes made over MCP. The text of the last 10 description edits is kept, and any of them can be restored from the history.

### Search and labels

Search matches every word across titles, descriptions and comments, with filters for project, label, assignee, priority, due date and status. The sidebar filter narrows the board and the project tree as you type *(`#id`, text, `@me`, labels, `team:name` for projects shared with a team)*.

Labels use a `namespace:value` form. **Global** labels are shared and managed by the admin; a fresh install starts with a set of `type:` labels. Anyone can add **personal** labels for their own use.

### Notifications

Each person can get pushes through [ntfy](https://ntfy.sh) on their own server and topic: at reminder time, on the morning a task assigned to them is due, as a daily overdue digest, when an agent using their token finishes a task, and for mentions, assignments and activity on their tasks. Each kind has its own switch, and pushes follow each person's timezone *(picked, or taken from their browser)*. Mentions, assignments and activity always land in the in-app inbox too. Someone with no topic set just gets no pushes.

### Everything else

- Ten themes, from the purple default to Dracula, Nord, Gruvbox, Catppuccin and GitHub Light
- Works on a phone, including drag and drop
- Markdown editor with a toolbar, preview, shortcuts and `@mention` autocomplete
- Daily database backups to `data/backups`, newest 3 kept
- Export a project, or everything you can see, as one JSON file *(project menu, or Settings)*
- `GET /health` answers `ok` while the database responds *(no sign-in; the Docker image uses it as its health check)*

## Accounts and sharing

Everyone signs in with a username and password. The admin creates single-use invite links *(valid 7 days)* on the Settings page.

Projects are private to whoever made them until shared with a user or a team. Sharing a project includes its sub-projects.

| Role    | Can                                      |
|---------|------------------------------------------|
| Viewer  | Read the project and its tasks           |
| Editor  | Create and work on tasks                 |
| Manager | Also edit, archive and share the project |

Anyone can create a team and add members by username. Admins see every project.

A team owner can give the team an https **webhook URL**. Every change made on a project shared with the team *(new tasks, field changes, comments and deletes, on its sub-projects too)* is then POSTed there as JSON: `event` *(created, updated, commented or deleted)*, `actor`, `agent`, `task` *(id, title, project, status, priority, due, assignees, labels, url)*, `changes` *(field, old, new)* and `comment`. A bulk change sends one `bulk` event listing its `tasks` instead. The URL must resolve only to public addresses *(no loopback, LAN or tailnet)* and kanopy connects to the exact address it checked, so DNS rebinding cannot redirect it; redirects are refused, and a `key=` parameter is sent as an `Authorization: Bearer` header. Posts go out one at a time from a queue of up to 500 *(the oldest is dropped past that)*, and the team card shows the last failed send until one succeeds.

## Connecting agents

kanopy serves MCP at `/mcp` *(streamable HTTP, stateless)*. Each agent authenticates with a personal API token from **Settings, API tokens**, acts as that user and sees only what they can. The `X-Agent` header names the agent in comments and history. A token can be made **read-only**: the agent then sees only the reading tools and every change is refused, over MCP and the REST API alike.

Agents get a short set of working rules on connect from `agents/instructions.example.txt`. Copy it to `agents/instructions.txt` *(untracked)* to describe your own project layout.

<details>
<summary><b>Claude Code</b></summary>

Add the server:

```sh
claude mcp add --transport http -s user kanopy http://HOST:PORT/mcp --header "X-Agent: claude" --header "X-Key: $TOKEN"
```

Add `agents/hook.sh` as a `SessionStart` hook in `~/.claude/settings.json`. Each new session then starts with a briefing on the project for the current directory and its open tasks:

```json
{
	"hooks": {
		"SessionStart": [{ "hooks": [{ "type": "command", "command": "/path/to/kanopy/agents/hook.sh" }] }]
	}
}
```

Link the `/task` command, which files a task without a proposal step:

```sh
ln -s /path/to/kanopy/agents/task.md ~/.claude/commands/task.md
```

</details>

<details>
<summary><b>opencode</b></summary>

Save your token to `~/.config/opencode/secrets/kanopy.token` and add the server to `~/.config/opencode/opencode.json`:

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

Link the session briefing plugin *(it reads `KANOPY_API`, default `http://127.0.0.1:8000`)* and the `/task` command:

```sh
ln -s /path/to/kanopy/agents/opencode.ts ~/.config/opencode/plugins/kanopy.ts
ln -s /path/to/kanopy/agents/task.md ~/.config/opencode/command/task.md
```

</details>

<details>
<summary><b>Anything else</b></summary>

Any MCP client that supports streamable HTTP works: point it at `http://HOST:PORT/mcp` with the `X-Key` and `X-Agent` headers. For a session briefing without MCP, `GET /api/context?cwd=<dir>` with the `X-Key` header returns plain text for the project whose folder contains `dir`.

</details>

## MCP tools

| Tool            | Does                                                                                                                              |
|-----------------|-----------------------------------------------------------------------------------------------------------------------------------|
| `list_projects` | Project tree with open task counts                                                                                                |
| `list_tasks`    | Tasks filtered by project, team, labels, text *(with the matching line)*, status, priority or due date; `ready` lists what can start now *(todo, no open blockers)*; `changed` lists recent activity |
| `get_task`      | Full tasks with description, links and the last 5 comments *(`comments` for all)*, or an attachment's text                        |
| `save_task`     | Create or update any field, edit or append to the description, tick a checklist item, comment; `ids` updates many at once         |
| `save_project`  | Rename or move, describe, set the local folder, archive                                                                           |
| `comment`       | Add a progress note                                                                                                               |
| `link`          | Relate two tasks, or remove the relation                                                                                          |
| `labels`        | List labels, create a global `type:` value *(admin)*, delete one                                                                  |
| `inbox`         | Unread notifications                                                                                                              |
| `delete`        | A task *(to the trash)*, or an empty project; `restore` brings a deleted task back                                                |

A few rules keep agents from making a mess:

- A new project is created by filing its first task into it, so no project is ever empty
- An agent must read a task before it can replace the whole description, so it never overwrites edits it has not seen; `edit` *(exact text, must match once)* and `append` need no read
- `host:` labels that differ only by punctuation *(`host:web01` and `host:web-01`)* are rejected
- Moving a task to doing **claims** it for that agent session *(`by:claude(brandon)#a1b2c3` on its line, the agent's name on its card)*. Another session that tries to start it is told who has it, and takes over only with `take=true`. Each MCP connection gets its own session id, so two Claude sessions never mistake each other's work for their own

## Configuration

Settings live in `.env`:

| Variable              | Default          | Purpose                                                                                   |
|-----------------------|------------------|-------------------------------------------------------------------------------------------|
| `KANOPY_HOST`         | `127.0.0.1`      | Address to listen on                                                                      |
| `KANOPY_PORT`         | `8000`           | Port to listen on                                                                         |
| `KANOPY_URL`          |                  | Public address when behind a reverse proxy                                                |
| `KANOPY_KEY`          |                  | Optional; stored as the admin's API token on start, for older agent configs               |
| `KANOPY_DATABASE`     | `data/kanban.db` | A `postgresql://user:pass@host/db` URL to use PostgreSQL instead of SQLite                |
| `KANOPY_UPDATE_CHECK` | `1`              | `0` stops the server asking GitHub for the latest release *(shown to admins in Settings)* |

Everything else is set on the Settings page: each person's ntfy and timezone in their account, the board's push times and auto-archive by an admin. Data, attachments and backups live in `data/`.

### SQLite or PostgreSQL

SQLite is the default and handles a team of dozens of people and agents on tens of thousands of tasks. PostgreSQL is there for larger boards: install its driver with `venv/bin/pip install "psycopg[binary]==3.3.9"` *(the Docker image already has it)*, set `KANOPY_DATABASE` and restart. Daily backups to `data/backups` cover SQLite only; back PostgreSQL up with `pg_dump`.

A board moves between the two in either direction. The source is left as it was and the destination must be a new, empty database:

```sh
venv/bin/python -m kanopy.migrate data/kanban.db postgresql://kanopy:secret@localhost/kanopy
venv/bin/python -m kanopy.migrate postgresql://kanopy:secret@localhost/kanopy data/new.db
```

Stop kanopy while copying, then point `KANOPY_DATABASE` at the new database.

### Live updates

Open pages check for changes every 15 seconds and reload the board when someone else changed it, unless you are in the middle of an edit. If someone changes a description while you are editing it, saving shows their version and asks before overwriting it.

<details>
<summary><b>Running behind a reverse proxy</b></summary>

Keep `KANOPY_HOST=127.0.0.1` and set `KANOPY_URL` to the public address, like `https://kanopy.example.com`. Notification and invite links then use it, the sign-in limit sees the real client address, and the session cookie is marked Secure.

```nginx
server {
	listen 443 ssl;
	server_name kanopy.example.com;
	ssl_certificate     /etc/letsencrypt/live/kanopy.example.com/fullchain.pem;
	ssl_certificate_key /etc/letsencrypt/live/kanopy.example.com/privkey.pem;
	client_max_body_size 100m;

	location / {
		proxy_pass http://127.0.0.1:8000;
		proxy_set_header Host $host;
		proxy_set_header X-Forwarded-For $remote_addr;
		proxy_set_header X-Forwarded-Proto $scheme;
	}
}
```

</details>

## Keyboard shortcuts

| Key   | Action                            |
|-------|-----------------------------------|
| `/`   | Focus the filter                  |
| `n`   | New task                          |
| `Esc` | Close the task, leave select mode |

## Tests

The suite in `tests/` covers the data layer, permissions, the MCP tools, accounts, notifications and every HTTP route. Each test runs against a fresh database in a temporary directory, and ntfy pushes are recorded instead of sent, so it never touches `data/` or the network.

```sh
venv/bin/python -m unittest
```

To run the same suite against PostgreSQL, point `KANOPY_TEST_DATABASE` at a throwaway database. It is wiped before every test, so never use a real one:

```sh
docker run -d --name kanopy-pg-test -e POSTGRES_PASSWORD=test -p 127.0.0.1:55432:5432 postgres:17-alpine
KANOPY_TEST_DATABASE=postgresql://postgres:test@127.0.0.1:55432/postgres venv/bin/python -m unittest
```

---

###### Mirrors: [SuperNETs](https://git.supernets.org/acidvegas/) • [GitHub](https://github.com/acidvegas/) • [GitLab](https://gitlab.com/acidvegas/) • [Codeberg](https://codeberg.org/acidvegas/)
