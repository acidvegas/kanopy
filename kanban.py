#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# kanban.py

import contextvars
import random
import re
import sqlite3

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


DB       = Path(__file__).parent / 'data' / 'kanban.db'
BACKUPS  = DB.parent / 'backups'
KEEP     = 3 # backups kept
MAX_FILE = 25 * 1024 * 1024 # bytes per attachment
SAFE_MIME = ('image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/avif', 'application/pdf', 'text/plain') # attachment types kept as-is; anything else (SVG, HTML...) is stored as application/octet-stream
INVERSE  = {'blocking': 'blocked', 'blocked': 'blocking', 'subtask': 'parenttask', 'parenttask': 'subtask', 'related': 'related', 'duplicates': 'duplicateof', 'duplicateof': 'duplicates', 'precedes': 'follows', 'follows': 'precedes'}
LABEL_RE = re.compile(r'^[a-z]+:[a-z0-9][a-z0-9._-]*$')
STATUSES = ('todo', 'doing', 'done')
DEFAULT_COLUMNS = ({'id': None, 'title': 'To-Do', 'status': 'todo'}, {'id': None, 'title': 'Doing', 'status': 'doing'}, {'id': None, 'title': 'Done', 'status': 'done'}) # board columns for projects without their own
COLORS   = ('#a78bfa', '#f472b6', '#f87171', '#fb923c', '#fbbf24', '#4ade80', '#2dd4bf', '#22d3ee', '#60a5fa', '#94a3b8')
COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')
CHECK_RE = re.compile(r'^\s*[-*] \[( |x|X)\]', re.M)
ROLES    = {'viewer': 1, 'editor': 2, 'manager': 3} # project share roles; 4 = owner
VIEW, EDIT, MANAGE, OWN = 1, 2, 3, 4
ACTOR    = contextvars.ContextVar('actor', default=None) # signed-in user dict for this request; None = system (full access)
AGENT    = contextvars.ContextVar('agent', default=None) # X-Agent name when the change comes through MCP
HISTORY  = 200 # events kept per task

SCHEMA = '''
CREATE TABLE IF NOT EXISTS projects (
	id          INTEGER PRIMARY KEY,
	parent_id   INTEGER REFERENCES projects(id),
	title       TEXT    NOT NULL,
	description TEXT    NOT NULL DEFAULT '',
	archived    INTEGER NOT NULL DEFAULT 0,
	color       TEXT,
	position    REAL    NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tasks (
	id          INTEGER PRIMARY KEY,
	project_id  INTEGER NOT NULL REFERENCES projects(id),
	title       TEXT    NOT NULL,
	description TEXT    NOT NULL DEFAULT '',
	status      TEXT    NOT NULL DEFAULT 'todo',
	priority    INTEGER NOT NULL DEFAULT 0,
	due         TEXT,
	remind_at   TEXT,
	position    REAL    NOT NULL DEFAULT 0,
	created     TEXT    NOT NULL,
	updated     TEXT    NOT NULL,
	done_at     TEXT,
	archived    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS attachments (id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, name TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL, data BLOB NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings    (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sent        (kind TEXT NOT NULL, key TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (kind, key));
CREATE TABLE IF NOT EXISTS labels      (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, color TEXT);
CREATE TABLE IF NOT EXISTS task_labels (task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, label_id INTEGER NOT NULL REFERENCES labels(id) ON DELETE CASCADE, PRIMARY KEY (task_id, label_id));
CREATE TABLE IF NOT EXISTS comments    (id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, author TEXT NOT NULL DEFAULT '', body TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS links       (task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, kind TEXT NOT NULL, other_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, PRIMARY KEY (task_id, kind, other_id));
CREATE TABLE IF NOT EXISTS users          (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0, ntfy_topic TEXT NOT NULL DEFAULT '', notify_mention INTEGER NOT NULL DEFAULT 1, notify_assign INTEGER NOT NULL DEFAULT 1, notify_activity INTEGER NOT NULL DEFAULT 1, notify_due INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions       (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tokens         (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, name TEXT NOT NULL, token TEXT NOT NULL UNIQUE, created TEXT NOT NULL, used TEXT);
CREATE TABLE IF NOT EXISTS invites        (id INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE, created_by INTEGER REFERENCES users(id) ON DELETE SET NULL, created TEXT NOT NULL, expires TEXT NOT NULL, used_by INTEGER REFERENCES users(id) ON DELETE SET NULL, used TEXT);
CREATE TABLE IF NOT EXISTS teams          (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS team_members   (team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, owner INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (team_id, user_id));
CREATE TABLE IF NOT EXISTS shares         (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE, team_id INTEGER REFERENCES teams(id) ON DELETE CASCADE, role TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_assignees (task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, PRIMARY KEY (task_id, user_id));
CREATE TABLE IF NOT EXISTS columns        (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, title TEXT NOT NULL, status TEXT NOT NULL, position REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS task_events    (id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, at TEXT NOT NULL, actor TEXT NOT NULL, agent TEXT, field TEXT NOT NULL, old TEXT, new TEXT);
CREATE INDEX IF NOT EXISTS task_events_task ON task_events (task_id, id);
CREATE TABLE IF NOT EXISTS notifications  (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE, kind TEXT NOT NULL, actor TEXT NOT NULL, text TEXT NOT NULL, created TEXT NOT NULL, read INTEGER NOT NULL DEFAULT 0);
'''


def apply_fields(db: sqlite3.Connection, task_id: int, data: dict, log: bool = True):
	'''
	Write any task fields present in data inside the caller's transaction.

	:param db: Open database connection
	:param task_id: Task id
	:param data: Any of: title, description, project_id, status, priority, due, remind_at, labels, assignees (usernames), column_id (also sets the status)
	:param log: Record changes in the task history (off while a task is being created)
	'''

	row  = require(db, 'tasks', task_id)
	note = (lambda *args: record(db, task_id, *args)) if log else (lambda *args: None)

	if 'title' in data:
		if not (data['title'] or '').strip():
			raise ValueError('title is required')
		db.execute('UPDATE tasks SET title = ? WHERE id = ?', (data['title'].strip(), task_id))
		if data['title'].strip() != row['title']:
			note('title', row['title'], data['title'].strip())

	if 'description' in data:
		db.execute('UPDATE tasks SET description = ? WHERE id = ?', (data['description'] or '', task_id))
		if (data['description'] or '') != row['description']:
			note('description') # text not kept, so the table stays small

	if 'project_id' in data:
		require(db, 'projects', data['project_id'])
		need(db, data['project_id'], EDIT)
		db.execute('UPDATE tasks SET project_id = ? WHERE id = ?', (data['project_id'], task_id))
		if int(data['project_id']) != row['project_id']:
			names = paths(db)
			note('project', names[row['project_id']], names[int(data['project_id'])])

	if 'assignees' in data:
		pid = data.get('project_id') or row['project_id']
		ids = []
		for name in {n.strip().lower().lstrip('@') for n in data['assignees'] or [] if n.strip()}:
			person = db.execute('SELECT * FROM users WHERE username = ?', (name,)).fetchone()
			if not person:
				raise ValueError(f'no user named {name}')
			lv = levels(db, dict(person))
			if lv is not None and lv.get(pid, 0) < VIEW:
				raise ValueError(f'{name} has no access to this project; share it with them first')
			ids.append(person['id'])
		before = set(assignees_map(db, task_id).get(task_id, []))
		after  = {n.strip().lower().lstrip('@') for n in data['assignees'] or [] if n.strip()}
		db.execute('DELETE FROM task_assignees WHERE task_id = ?', (task_id,))
		db.executemany('INSERT INTO task_assignees VALUES (?, ?)', [(task_id, uid) for uid in ids])
		for name in sorted(after - before):
			note('assignee', None, name)
		for name in sorted(before - after):
			note('assignee', name, None)

	if 'priority' in data:
		if int(data['priority'] or 0) not in range(6):
			raise ValueError('priority must be 0-5')
		db.execute('UPDATE tasks SET priority = ? WHERE id = ?', (int(data['priority'] or 0), task_id))
		if int(data['priority'] or 0) != row['priority']:
			note('priority', str(row['priority']), str(int(data['priority'] or 0)))

	for key, fmt, shape in (('due', '%Y-%m-%d', 'YYYY-MM-DD'), ('remind_at', '%Y-%m-%dT%H:%M', 'YYYY-MM-DDTHH:MM')):
		if key in data:
			if data[key]:
				try:
					datetime.strptime(data[key], fmt)
				except (TypeError, ValueError):
					raise ValueError(f'{key} must look like {shape}') from None
			db.execute(f'UPDATE tasks SET {key} = ? WHERE id = ?', (data[key] or None, task_id))
			if (data[key] or None) != row[key]:
				note(key, row[key], data[key] or None)

	if 'labels' in data:
		before = {r['name'] for r in db.execute('SELECT l.name FROM labels l JOIN task_labels tl ON tl.label_id = l.id WHERE tl.task_id = ?', (task_id,))}
		set_labels(db, task_id, data['labels'] or [])
		after  = {name.strip().lower() for name in data['labels'] or [] if name.strip()}
		for name in sorted(after - before):
			note('label', None, name)
		for name in sorted(before - after):
			note('label', name, None)

	if data.get('status') and data['status'] != row['status']:
		set_status(db, task_id, data['status'], log)

	if 'column_id' in data:
		set_column(db, task_id, data['column_id'], log)

	touch(db, task_id)


def archive_done(days: int) -> int:
	'''
	Archive tasks that have been done for more than the given number of days; returns how many.

	:param days: Days a task stays on the board after it is done
	'''

	cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec='seconds')

	with connect() as db:
		return db.execute("UPDATE tasks SET archived = 1 WHERE status = 'done' AND archived = 0 AND done_at < ?", (cutoff,)).rowcount


def assignees_map(db: sqlite3.Connection, task_id: int = None) -> dict:
	'''
	Return task id -> assignee usernames, for one task or all.

	:param db: Open database connection
	:param task_id: Limit to one task
	'''

	out = {}

	for row in db.execute('SELECT a.task_id, u.username FROM task_assignees a JOIN users u ON u.id = a.user_id' + (' WHERE a.task_id = ?' if task_id else '') + ' ORDER BY u.username', (task_id,) if task_id else ()):
		out.setdefault(row['task_id'], []).append(row['username'])

	return out


def attachment_add(task_id: int, name: str, mime: str, data: bytes) -> dict:
	'''
	Attach a file to a task.

	:param task_id: Task id
	:param name: File name
	:param mime: MIME type
	:param data: File contents
	'''

	name = Path(name or '').name.strip() or 'file'

	if len(data) > MAX_FILE:
		raise ValueError(f'{name} is over {MAX_FILE // 1024 // 1024} MB')

	with connect() as db:
		need_task(db, task_id, EDIT)
		db.execute('INSERT INTO attachments (task_id, name, mime, size, data, created) VALUES (?, ?, ?, ?, ?, ?)', (task_id, name, mime if mime in SAFE_MIME else 'application/octet-stream', len(data), data, now()))
		record(db, task_id, 'attachment', None, name)
		touch(db, task_id)

	return task(task_id)


def attachment_delete(attachment_id: int) -> dict:
	'''
	Delete an attachment; returns its task.

	:param attachment_id: Attachment id
	'''

	with connect() as db:
		row = require(db, 'attachments', attachment_id)
		need_task(db, row['task_id'], EDIT)
		db.execute('DELETE FROM attachments WHERE id = ?', (attachment_id,))
		record(db, row['task_id'], 'attachment', row['name'], None)
		touch(db, row['task_id'])

	return task(row['task_id'])


def attachment_get(attachment_id: int) -> sqlite3.Row:
	'''
	Return an attachment row with its data.

	:param attachment_id: Attachment id
	'''

	with connect() as db:
		row = require(db, 'attachments', attachment_id)
		need_task(db, row['task_id'], VIEW)
		return row


def backup():
	'''Write today's backup of the database unless it exists, keeping only the newest KEEP backups.'''

	BACKUPS.mkdir(exist_ok=True)
	target = BACKUPS / f'kanban-{datetime.now().strftime("%Y-%m-%d")}.db'

	if not target.exists():
		part = target.with_suffix('.part')
		src  = sqlite3.connect(DB)
		dst  = sqlite3.connect(part)
		with dst:
			src.backup(dst)
		dst.close()
		src.close()
		part.rename(target)

	for old in sorted(BACKUPS.glob('kanban-*.db'))[:-KEEP]:
		old.unlink()
	for side in BACKUPS.glob('kanban-*.db-*'): # -shm/-wal files left when a backup was opened; backups are never written to
		side.unlink()


def board_columns(db: sqlite3.Connection, project_id: int) -> list:
	'''
	Return the board columns a project uses: its own, else the nearest ancestor's, else the defaults.

	:param db: Open database connection
	:param project_id: Project id
	'''

	while project_id:
		cols = [dict(row) for row in db.execute('SELECT id, title, status FROM columns WHERE project_id = ? ORDER BY position, id', (project_id,))]
		if cols:
			return cols
		project_id = db.execute('SELECT parent_id FROM projects WHERE id = ?', (project_id,)).fetchone()['parent_id']

	return [dict(col) for col in DEFAULT_COLUMNS]


def bulk(ids: list, data: dict) -> int:
	'''
	Apply one change to many tasks in a single transaction; returns how many changed.

	:param ids: Task ids
	:param data: One or more of: status, project_id, add_label, remove_label, delete (true)
	'''

	if not ids:
		raise ValueError('no tasks selected')

	with connect() as db:
		for task_id in ids:
			row = need_task(db, task_id, EDIT)
			if data.get('delete'):
				db.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
				continue
			fields = {key: data[key] for key in ('status', 'project_id') if data.get(key)}
			if data.get('add_label') or data.get('remove_label'):
				have = [r['name'] for r in db.execute('SELECT l.name FROM labels l JOIN task_labels tl ON tl.label_id = l.id WHERE tl.task_id = ?', (row['id'],))]
				gone = (data.get('remove_label') or '').strip().lower()
				fields['labels'] = [l for l in have if l != gone] + ([valid_label(data['add_label'])] if data.get('add_label') else [])
			if fields:
				apply_fields(db, task_id, fields)

	return len(ids)


def comment_add(task_id: int, author: str, body: str) -> dict:
	'''
	Add a comment to a task.

	:param task_id: Task id
	:param author: Comment author tag
	:param body: Markdown comment body
	'''

	if not body.strip():
		raise ValueError('comment is empty')

	with connect() as db:
		need_task(db, task_id, EDIT)
		db.execute('INSERT INTO comments (task_id, author, body, created) VALUES (?, ?, ?, ?)', (task_id, author, body.strip(), now()))
		record(db, task_id, 'comment')
		touch(db, task_id)

	return task(task_id)


def columns_save(project_id: int, columns: list):
	'''
	Replace a project's board columns (manager or owner). An empty list drops them, so the project inherits again.

	:param project_id: Project id
	:param columns: Ordered list of {id (existing column, optional), title, status}; every status needs at least one column
	'''

	cols = [{'id': col.get('id'), 'title': (col.get('title') or '').strip(), 'status': col.get('status')} for col in columns]

	for col in cols:
		if not col['title']:
			raise ValueError('every column needs a name')
		if col['status'] not in STATUSES:
			raise ValueError(f'column status must be one of: {", ".join(STATUSES)}')

	if cols and {col['status'] for col in cols} != set(STATUSES):
		raise ValueError('keep at least one column for each of To-Do, Doing and Done')

	with connect() as db:
		require(db, 'projects', project_id)
		need(db, project_id, MANAGE)
		have = {row['id'] for row in db.execute('SELECT id FROM columns WHERE project_id = ?', (project_id,))}
		keep = {int(col['id']) for col in cols if col['id']}
		if keep - have:
			raise ValueError('a column belongs to another project')
		for col_id in have - keep: # its tasks fall back to the first column of their status
			db.execute('DELETE FROM columns WHERE id = ?', (col_id,))
		for pos, col in enumerate(cols):
			if col['id']:
				old = db.execute('SELECT status FROM columns WHERE id = ?', (int(col['id']),)).fetchone()['status']
				db.execute('UPDATE columns SET title = ?, status = ?, position = ? WHERE id = ?', (col['title'], col['status'], pos, int(col['id'])))
				if old != col['status']: # its tasks keep their status and fall back to the first column of it
					db.execute('UPDATE tasks SET column_id = NULL WHERE column_id = ?', (int(col['id']),))
			else:
				db.execute('INSERT INTO columns (project_id, title, status, position) VALUES (?, ?, ?, ?)', (project_id, col['title'], col['status'], pos))


def connect() -> sqlite3.Connection:
	'''Open the database with foreign keys enforced and rows as mappings.'''

	db = sqlite3.connect(DB, timeout=10)
	db.row_factory = sqlite3.Row
	db.execute('PRAGMA foreign_keys = ON')

	return db


def init():
	'''Create the database and an Inbox project if there are no projects.'''

	DB.parent.mkdir(exist_ok=True)

	with connect() as db:
		db.execute('PRAGMA journal_mode = WAL')
		db.executescript(SCHEMA)
		for table, column, kind in (('projects', 'owner_id', 'INTEGER REFERENCES users(id)'), ('users', 'avatar', 'BLOB'), ('users', 'avatar_mime', 'TEXT'), ('users', 'avatar_at', 'TEXT'), ('projects', 'folder', "TEXT NOT NULL DEFAULT ''"), ('tasks', 'column_id', 'INTEGER REFERENCES columns(id) ON DELETE SET NULL')):
			if column not in [row['name'] for row in db.execute(f'PRAGMA table_info({table})')]:
				db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {kind}')
		if not db.execute('SELECT 1 FROM projects').fetchone():
			db.execute("INSERT INTO projects (title) VALUES ('Inbox')")


def label_create(name: str, color: str = None):
	'''
	Create a label; without a color it takes its namespace's color.

	:param name: Label like host:pi
	:param color: #rrggbb or None
	'''

	name = valid_label(name)

	if color and not COLOR_RE.match(color):
		raise ValueError('color must look like #rrggbb')

	with connect() as db:
		if db.execute('SELECT 1 FROM labels WHERE name = ?', (name,)).fetchone():
			raise ValueError(f'label {name} already exists')
		db.execute('INSERT INTO labels (name, color) VALUES (?, COALESCE(?, (SELECT color FROM labels WHERE name LIKE ? AND color IS NOT NULL LIMIT 1)))', (name, color or None, name.split(':')[0] + ':%'))


def label_delete(name: str):
	'''
	Delete a label and remove it from every task.

	:param name: Label name
	'''

	need_admin()

	with connect() as db:
		if not db.execute('DELETE FROM labels WHERE name = ?', (name.strip().lower(),)).rowcount:
			raise LookupError(f'label {name} not found')


def label_update(name: str, new_name: str = None, color: str = None):
	'''
	Recolor and/or rename a label. Renaming onto an existing label merges them.

	:param name: Current label name
	:param new_name: New name, or an existing label to merge into
	:param color: #rrggbb, or an empty string to clear
	'''

	need_admin()

	with connect() as db:
		row = db.execute('SELECT id FROM labels WHERE name = ?', (name.strip().lower(),)).fetchone()

		if not row:
			raise LookupError(f'label {name} not found')

		if color is not None:
			if color and not COLOR_RE.match(color):
				raise ValueError('color must look like #rrggbb')
			db.execute('UPDATE labels SET color = ? WHERE id = ?', (color.lower() or None, row['id']))

		if new_name and valid_label(new_name) != name.strip().lower():
			new_name = valid_label(new_name)
			target   = db.execute('SELECT id FROM labels WHERE name = ?', (new_name,)).fetchone()
			if target:
				db.execute('INSERT OR IGNORE INTO task_labels SELECT task_id, ? FROM task_labels WHERE label_id = ?', (target['id'], row['id']))
				db.execute('DELETE FROM labels WHERE id = ?', (row['id'],))
			else:
				db.execute('UPDATE labels SET name = ? WHERE id = ?', (new_name, row['id']))


def labels() -> list:
	'''Return every label with its color and the number of tasks using it that the signed-in user can see.'''

	with connect() as db:
		lv  = levels(db)
		out = {row['id']: {'name': row['name'], 'color': row['color'], 'count': 0} for row in db.execute('SELECT id, name, color FROM labels ORDER BY name')}
		for row in db.execute('SELECT tl.label_id, t.project_id FROM task_labels tl JOIN tasks t ON t.id = tl.task_id'):
			if lv is None or lv.get(row['project_id']):
				out[row['label_id']]['count'] += 1

	return list(out.values())


def link_add(task_id: int, kind: str, other_id: int) -> dict:
	'''
	Link two tasks, storing both directions.

	:param task_id: Task id
	:param kind: Relation kind from INVERSE
	:param other_id: The other task id
	'''

	if kind not in INVERSE:
		raise ValueError(f'kind must be one of: {", ".join(INVERSE)}')

	if task_id == other_id:
		raise ValueError('cannot link a task to itself')

	with connect() as db:
		need_task(db, task_id, EDIT)
		need_task(db, other_id, VIEW)
		if db.execute('INSERT OR IGNORE INTO links VALUES (?, ?, ?)', (task_id, kind, other_id)).rowcount:
			record(db, task_id, 'link', None, f'{kind}:{other_id}')
		if db.execute('INSERT OR IGNORE INTO links VALUES (?, ?, ?)', (other_id, INVERSE[kind], task_id)).rowcount:
			record(db, other_id, 'link', None, f'{INVERSE[kind]}:{task_id}')
		touch(db, task_id)

	return task(task_id)


def link_remove(task_id: int, kind: str, other_id: int) -> dict:
	'''
	Remove a link between two tasks in both directions.

	:param task_id: Task id
	:param kind: Relation kind
	:param other_id: The other task id
	'''

	with connect() as db:
		need_task(db, task_id, EDIT)
		if db.execute('DELETE FROM links WHERE task_id = ? AND kind = ? AND other_id = ?', (task_id, kind, other_id)).rowcount:
			record(db, task_id, 'link', f'{kind}:{other_id}', None)
		if db.execute('DELETE FROM links WHERE task_id = ? AND kind = ? AND other_id = ?', (other_id, INVERSE.get(kind, kind), task_id)).rowcount:
			record(db, other_id, 'link', f'{INVERSE.get(kind, kind)}:{task_id}', None)
		touch(db, task_id)

	return task(task_id)


def levels(db: sqlite3.Connection, user: dict = None) -> dict:
	'''
	Return project id -> access level (1 viewer, 2 editor, 3 manager, 4 owner) for a user, or None when they can see everything (admin or system). Ownership and shares carry down to sub-projects.

	:param db: Open database connection
	:param user: User dict; defaults to the signed-in user
	'''

	user = user or ACTOR.get()

	if user is None or user['is_admin']:
		return None

	rows   = db.execute('SELECT id, parent_id, owner_id FROM projects').fetchall()
	parent = {row['id']: row['parent_id'] for row in rows}
	direct = {row['id']: OWN for row in rows if row['owner_id'] == user['id']}

	for row in db.execute('SELECT project_id, role FROM shares WHERE user_id = ? OR team_id IN (SELECT team_id FROM team_members WHERE user_id = ?)', (user['id'], user['id'])):
		direct[row['project_id']] = max(direct.get(row['project_id'], 0), ROLES[row['role']])

	out = {}
	for pid in parent:
		level, node = 0, pid
		while node:
			level = max(level, direct.get(node, 0))
			node  = parent[node]
		if level:
			out[pid] = level

	return out


def need(db: sqlite3.Connection, project_id: int, level: int):
	'''
	Raise PermissionError unless the signed-in user has at least this level on a project.

	:param db: Open database connection
	:param project_id: Project id
	:param level: VIEW, EDIT, MANAGE or OWN
	'''

	lv = levels(db)

	if lv is not None and lv.get(project_id, 0) < level:
		raise PermissionError('you do not have access to that project' if not lv.get(project_id) else 'your role on this project does not allow that')


def need_admin():
	'''Raise PermissionError unless the signed-in user is an admin (or the system itself, with no user).'''

	user = ACTOR.get()

	if user is not None and not user['is_admin']:
		raise PermissionError('admin only')


def need_task(db: sqlite3.Connection, task_id: int, level: int) -> sqlite3.Row:
	'''
	Return a task row after checking the signed-in user's level on its project.

	:param db: Open database connection
	:param task_id: Task id
	:param level: VIEW, EDIT, MANAGE or OWN
	'''

	row = require(db, 'tasks', task_id)
	need(db, row['project_id'], level)

	return row


def next_position(db: sqlite3.Connection, parent_id: int) -> float:
	'''
	Return a position that puts a new project after its siblings.

	:param db: Open database connection
	:param parent_id: Parent project id, or None for top-level
	'''

	return db.execute('SELECT COALESCE(MAX(position), 0) + 1 FROM projects WHERE parent_id IS ?', (parent_id,)).fetchone()[0]


def now() -> str:
	'''Return the current UTC time as an ISO string.'''

	return datetime.now(timezone.utc).isoformat(timespec='seconds')


def paths(db: sqlite3.Connection) -> dict:
	'''
	Return a mapping of project id to its full slash path.

	:param db: Open database connection
	'''

	rows = {row['id']: row for row in db.execute('SELECT id, parent_id, title FROM projects')}
	out  = {}

	def path(pid: int) -> str:
		if pid not in out:
			row = rows[pid]
			out[pid] = (path(row['parent_id']) + '/' if row['parent_id'] else '') + row['title']
		return out[pid]

	for pid in rows:
		path(pid)

	return out


def pick_color(db: sqlite3.Connection, parent_id: int) -> str:
	'''
	Pick a random color for a new sub-project, avoiding its parent's and siblings' colors when possible. Top-level projects get none.

	:param db: Open database connection
	:param parent_id: Parent project id, or None for a top-level project
	'''

	if not parent_id:
		return None

	used    = {row['color'] for row in db.execute('SELECT color FROM projects WHERE parent_id = ? OR id = ?', (parent_id, parent_id))}
	choices = [color for color in COLORS if color not in used] or list(COLORS)

	return random.choice(choices)


def project_delete(project_id: int):
	'''
	Delete a project that has no tasks and no sub-projects.

	:param project_id: Project id
	'''

	with connect() as db:
		require(db, 'projects', project_id)
		need(db, project_id, OWN)
		if db.execute('SELECT 1 FROM tasks WHERE project_id = ?', (project_id,)).fetchone():
			raise ValueError('project still has tasks')
		if db.execute('SELECT 1 FROM projects WHERE parent_id = ?', (project_id,)).fetchone():
			raise ValueError('project still has sub-projects')
		db.execute('DELETE FROM projects WHERE id = ?', (project_id,))


def project_order(parent_id: int, ids: list):
	'''
	Put projects under one parent in the given order, moving any that came from elsewhere.

	:param parent_id: New parent project id, or None for top-level
	:param ids: Every child project id under that parent, top to bottom
	'''

	with connect() as db:
		db.execute('BEGIN IMMEDIATE') # the loop check and the move happen under one write lock
		if parent_id:
			require(db, 'projects', parent_id)
			need(db, parent_id, MANAGE)

		lv = levels(db)
		for pos, pid in enumerate(ids):
			row = require(db, 'projects', pid)
			if lv is not None and lv.get(pid, 0) < MANAGE:
				if row['parent_id'] != parent_id:
					raise PermissionError(f'you cannot move {row["title"]}')
				continue
			ancestor = parent_id
			while ancestor:
				if ancestor == pid:
					raise ValueError('cannot move a project into itself')
				ancestor = db.execute('SELECT parent_id FROM projects WHERE id = ?', (ancestor,)).fetchone()['parent_id']
			if row['parent_id'] != parent_id and db.execute('SELECT 1 FROM projects WHERE title = ? AND parent_id IS ? AND id != ?', (row['title'], parent_id, pid)).fetchone():
				raise ValueError(f'a project named {row["title"]} already exists there')
			db.execute('UPDATE projects SET parent_id = ?, position = ? WHERE id = ?', (parent_id, pos, pid))


def project_save(path: str, project_id: int = None, description: str = None, archived: bool = None, color: str = None, folder: str = None) -> dict:
	'''
	Create a project by path, or rename/move an existing one to a new path. Missing parents are created.

	:param path: Slash path like Code/AI/hermes
	:param project_id: Existing project to rename/move, or None to create
	:param description: New description
	:param archived: New archived state
	:param color: Sidebar color as #rrggbb, or an empty string to clear it
	:param folder: Local directory the project lives in (~/code/...), or an empty string to clear it
	'''

	parts = [part.strip() for part in (path or '').split('/') if part.strip()]

	actor = ACTOR.get()
	owner = actor['id'] if actor else None

	with connect() as db:
		db.execute('BEGIN IMMEDIATE') # the loop check and the move happen under one write lock
		if project_id:
			require(db, 'projects', project_id)
			need(db, project_id, MANAGE)

		if parts:
			parent = None
			for part in parts[:-1]:
				row = db.execute('SELECT id FROM projects WHERE title = ? AND parent_id IS ?', (part, parent)).fetchone()
				if not row and parent:
					need(db, parent, MANAGE)
				parent = row['id'] if row else db.execute('INSERT INTO projects (title, parent_id, color, position, owner_id) VALUES (?, ?, ?, ?, ?)', (part, parent, pick_color(db, parent), next_position(db, parent), owner)).lastrowid

			clash = db.execute('SELECT id FROM projects WHERE title = ? AND parent_id IS ?', (parts[-1], parent)).fetchone()
			if clash and clash['id'] != project_id:
				raise ValueError('a project with that path already exists')

			if project_id:
				ancestor = parent
				while ancestor:
					if ancestor == project_id:
						raise ValueError('cannot move a project into itself')
					ancestor = db.execute('SELECT parent_id FROM projects WHERE id = ?', (ancestor,)).fetchone()['parent_id']
				if parent and parent != db.execute('SELECT parent_id FROM projects WHERE id = ?', (project_id,)).fetchone()['parent_id']:
					need(db, parent, MANAGE)
				db.execute('UPDATE projects SET title = ?, parent_id = ? WHERE id = ?', (parts[-1], parent, project_id))
			else:
				if parent:
					need(db, parent, MANAGE)
				project_id = db.execute('INSERT INTO projects (title, parent_id, color, position, owner_id) VALUES (?, ?, ?, ?, ?)', (parts[-1], parent, pick_color(db, parent), next_position(db, parent), owner)).lastrowid
		elif not project_id:
			raise ValueError('path is required')

		if description is not None:
			db.execute('UPDATE projects SET description = ? WHERE id = ?', (description.strip(), project_id))

		if archived is not None:
			db.execute('UPDATE projects SET archived = ? WHERE id = ?', (int(archived), project_id))

		if color is not None:
			if color and not COLOR_RE.match(color):
				raise ValueError('color must look like #rrggbb')
			db.execute('UPDATE projects SET color = ? WHERE id = ?', (color.lower() or None, project_id))

		if folder is not None:
			folder = folder.strip().rstrip('/')
			if folder and folder[0] not in '~/':
				raise ValueError('folder must be an absolute path or start with ~')
			db.execute('UPDATE projects SET folder = ? WHERE id = ?', (folder, project_id))

	return {'id': project_id}


def projects() -> list:
	'''
	Return the projects the signed-in user can see with path, open (todo + doing) task count, role (1 viewer .. 4 owner; 0 = a parent shown only for the tree) and own board columns (empty = inherited or default), in tree order (siblings by position, then title).
	'''

	with connect() as db:
		names  = paths(db)
		lv     = levels(db)
		counts = dict(db.execute("SELECT project_id, COUNT(*) FROM tasks WHERE status != 'done' AND archived = 0 GROUP BY project_id").fetchall())
		cols   = {}
		for col in db.execute('SELECT id, project_id, title, status FROM columns ORDER BY position, id'):
			cols.setdefault(col['project_id'], []).append({'id': col['id'], 'title': col['title'], 'status': col['status']})
		rows   = [dict(row, path=names[row['id']], open=counts.get(row['id'], 0), role=OWN if lv is None else lv.get(row['id'], 0), columns=cols.get(row['id'], [])) for row in db.execute('SELECT * FROM projects')]

	if lv is not None:
		parent = {row['id']: row['parent_id'] for row in rows}
		keep   = set()
		for pid in lv:
			while pid and pid not in keep:
				keep.add(pid)
				pid = parent[pid]
		rows = [dict(row, open=row['open'] if row['role'] else 0) for row in rows if row['id'] in keep]

	kids = {}
	for row in rows:
		kids.setdefault(row['parent_id'], []).append(row)

	out = []
	def walk(parent):
		for row in sorted(kids.get(parent, []), key=lambda r: (r['position'], r['title'].lower())):
			out.append(row)
			walk(row['id'])
	walk(None)

	return out


def record(db: sqlite3.Connection, task_id: int, field: str, old: str = None, new: str = None):
	'''
	Add an event to a task's history as the signed-in user (and MCP agent), keeping only the newest HISTORY events.

	:param db: Open database connection
	:param task_id: Task id
	:param field: What changed: created, title, description, status, column, priority, due, remind_at, project, label, assignee, link, attachment, comment
	:param old: Value before, or None
	:param new: Value after, or None
	'''

	actor = ACTOR.get()

	db.execute('INSERT INTO task_events (task_id, at, actor, agent, field, old, new) VALUES (?, ?, ?, ?, ?, ?, ?)', (task_id, now(), actor['username'] if actor else 'system', AGENT.get(), field, old, new))
	db.execute('DELETE FROM task_events WHERE task_id = ? AND id NOT IN (SELECT id FROM task_events WHERE task_id = ? ORDER BY id DESC LIMIT ?)', (task_id, task_id, HISTORY))


def reorder(status: str, ids: list, column_id: int = None):
	'''
	Put tasks into a status column in the given order.

	:param status: Target status column
	:param ids: The visible task ids in that column, top to bottom; cards not listed (filtered out, other projects) keep their place
	:param column_id: Custom board column the tasks were dropped in, or None for a default column
	'''

	if status not in STATUSES:
		raise ValueError(f'status must be one of: {", ".join(STATUSES)}')

	with connect() as db:
		rows   = {task_id: need_task(db, task_id, EDIT) for task_id in ids}
		column = [row['id'] for row in db.execute('SELECT id FROM tasks WHERE status = ? AND archived = 0 ORDER BY position, id', (status,))]
		queue  = list(ids)
		order  = []
		last   = 0
		for task_id in column:
			if task_id in rows:
				order.append(queue.pop(0))
				last = len(order)
			else:
				order.append(task_id)
		order[last:last] = queue # cards new to this column go right after the last visible one

		for pos, task_id in enumerate(order):
			db.execute('UPDATE tasks SET position = ? WHERE id = ?', (pos, task_id))
		for task_id, row in rows.items():
			if column_id and row['column_id'] != column_id: # also sets the column's status
				set_column(db, task_id, column_id)
				touch(db, task_id)
			elif row['status'] != status:
				set_status(db, task_id, status)
				touch(db, task_id)


def require(db: sqlite3.Connection, table: str, row_id: int) -> sqlite3.Row:
	'''
	Return a row by id or raise LookupError.

	:param db: Open database connection
	:param table: Table name
	:param row_id: Row id
	'''

	row = db.execute(f'SELECT * FROM {table} WHERE id = ?', (row_id,)).fetchone()

	if not row:
		raise LookupError(f'{table[:-1]} {row_id} not found')

	return row


def search(query: str, project: int = None, statuses: list = None, label: str = None, priority: int = 0, due: str = '', fields: list = None, sort: str = 'updated', assignee: str = None) -> list:
	'''
	Find tasks whose chosen fields contain every word of the query (case-insensitive), narrowed by filters. An empty query lists every task the filters allow.

	:param query: Search words
	:param project: Project id; its sub-projects are included
	:param statuses: Statuses to include, default all
	:param label: Label every result must have
	:param priority: Minimum priority
	:param due: overdue, today, week, any (has a due date), none (no due date), or empty for no filter
	:param fields: Any of title, description, comments; default all three
	:param sort: updated, created, priority, due or id
	:param assignee: Username every result must be assigned to
	'''

	words  = [w.lower() for w in query.split() if w.strip()]
	fields = [f for f in (fields or ('title', 'description', 'comments')) if f in ('title', 'description', 'comments')] or ['title', 'description', 'comments']

	if due not in ('', 'overdue', 'today', 'week', 'any', 'none'):
		raise ValueError('due must be overdue, today, week, any or none')

	if sort not in ('updated', 'created', 'priority', 'due', 'id'):
		raise ValueError('sort must be updated, created, priority, due or id')

	if not words and not (project or statuses or label or priority or due or assignee):
		return []

	local = datetime.now(ZoneInfo(setting_values().get('timezone') or 'UTC'))
	today = local.strftime('%Y-%m-%d')
	week  = (local + timedelta(days=7)).strftime('%Y-%m-%d')

	with connect() as db:
		names = paths(db)
		scope = None
		if project:
			require(db, 'projects', project)
			prefix = names[project] + '/'
			scope  = {pid for pid, path in names.items() if pid == project or path.startswith(prefix)}
		notes = {}
		if 'comments' in fields:
			for row in db.execute('SELECT task_id, body FROM comments ORDER BY id'):
				notes.setdefault(row['task_id'], []).append(row['body'])
		tags = {}
		for row in db.execute('SELECT tl.task_id, l.name FROM task_labels tl JOIN labels l ON l.id = tl.label_id ORDER BY l.name'):
			tags.setdefault(row['task_id'], []).append(row['name'])
		people = assignees_map(db)
		lv     = levels(db)
		rows   = [row for row in db.execute('SELECT id, project_id, title, description, status, priority, due, created, updated, archived FROM tasks') if lv is None or lv.get(row['project_id'])]

	out = []
	for row in rows:
		d = (row['due'] or '')[:10]
		if scope is not None and row['project_id'] not in scope:
			continue
		if statuses and row['status'] not in statuses:
			continue
		if label and label not in tags.get(row['id'], []):
			continue
		if assignee and assignee not in people.get(row['id'], []):
			continue
		if row['priority'] < (priority or 0):
			continue
		if (due == 'overdue' and not (d and d < today and row['status'] != 'done')) or (due == 'today' and d != today) or (due == 'week' and not (d and today <= d <= week)) or (due == 'any' and not d) or (due == 'none' and d):
			continue
		texts = [('title', row['title'])] * ('title' in fields) + [('description', row['description'])] * ('description' in fields) + [('comment', n) for n in notes.get(row['id'], [])]
		if not all(any(w in text.lower() for _, text in texts) for w in words):
			continue
		where, snippet = 'title', ''
		if words and not ('title' in fields and any(w in row['title'].lower() for w in words)):
			for kind, text in texts[1 if 'title' in fields else 0:]:
				hit = next((line.strip() for line in text.split('\n') if any(w in line.lower() for w in words)), None)
				if hit:
					where, snippet = kind, hit[:220]
					break
		out.append({'id': row['id'], 'title': row['title'], 'project': names[row['project_id']], 'project_id': row['project_id'], 'status': row['status'], 'priority': row['priority'], 'due': d or None, 'labels': tags.get(row['id'], []), 'assignees': people.get(row['id'], []), 'archived': row['archived'], 'where': where, 'snippet': snippet, 'created': row['created'], 'updated': row['updated']})

	if sort == 'priority':
		out.sort(key=lambda r: -r['priority'])
	elif sort == 'due':
		out.sort(key=lambda r: (r['due'] is None, r['due'] or ''))
	elif sort == 'id':
		out.sort(key=lambda r: r['id'], reverse=True)
	else:
		out.sort(key=lambda r: r[sort], reverse=True)
		out.sort(key=lambda r: r['status'] == 'done')

	return out


def set_labels(db: sqlite3.Connection, task_id: int, names: list):
	'''
	Replace a task's labels, creating any new ones.

	:param db: Open database connection
	:param task_id: Task id
	:param names: Label names like host:pi
	'''

	names = sorted({name.strip().lower() for name in names if name.strip()})

	for name in names:
		if not LABEL_RE.match(name):
			raise ValueError(f'label "{name}" must look like namespace:value')

	db.execute('DELETE FROM task_labels WHERE task_id = ?', (task_id,))

	for name in names:
		db.execute('INSERT OR IGNORE INTO labels (name, color) VALUES (?, (SELECT color FROM labels WHERE name LIKE ? AND color IS NOT NULL LIMIT 1))', (name, name.split(':')[0] + ':%'))
		db.execute('INSERT INTO task_labels SELECT ?, id FROM labels WHERE name = ?', (task_id, name))


def set_column(db: sqlite3.Connection, task_id: int, column_id: int, log: bool = True):
	'''
	Put a task in a board column of its project (or an ancestor's), taking that column's status; None goes back to the first column of its status.

	:param db: Open database connection
	:param task_id: Task id
	:param column_id: Column id, or None
	:param log: Record the change in the task history
	'''

	row = db.execute('SELECT t.project_id, t.column_id, c.title FROM tasks t LEFT JOIN columns c ON c.id = t.column_id WHERE t.id = ?', (task_id,)).fetchone()

	if not column_id:
		db.execute('UPDATE tasks SET column_id = NULL WHERE id = ?', (task_id,))
		return

	col  = require(db, 'columns', int(column_id))
	pid  = row['project_id']
	line = set()
	while pid:
		line.add(pid)
		pid = db.execute('SELECT parent_id FROM projects WHERE id = ?', (pid,)).fetchone()['parent_id']
	if col['project_id'] not in line:
		raise ValueError('that column is not on this task\'s board')

	set_status(db, task_id, col['status'], log)
	db.execute('UPDATE tasks SET column_id = ? WHERE id = ?', (col['id'], task_id))

	if log and row['column_id'] != col['id']:
		record(db, task_id, 'column', row['title'], col['title'])


def set_status(db: sqlite3.Connection, task_id: int, status: str, log: bool = True):
	'''
	Change a task's status, moving it to the bottom of the new column and tracking done_at.

	:param db: Open database connection
	:param task_id: Task id
	:param status: New status
	:param log: Record the change in the task history
	'''

	if status not in STATUSES:
		raise ValueError(f'status must be one of: {", ".join(STATUSES)}')

	old = db.execute('SELECT status FROM tasks WHERE id = ?', (task_id,)).fetchone()['status']
	if log and old != status:
		record(db, task_id, 'status', old, status)
	if old != status: # a column of the old status no longer fits; the board shows the first column of the new one
		db.execute('UPDATE tasks SET column_id = NULL WHERE id = ? AND column_id IN (SELECT id FROM columns WHERE status != ?)', (task_id, status))

	bottom = db.execute('SELECT COALESCE(MAX(position), 0) + 1 FROM tasks WHERE status = ?', (status,)).fetchone()[0]
	db.execute('UPDATE tasks SET status = ?, position = ?, done_at = ?, archived = 0 WHERE id = ?', (status, bottom, now() if status == 'done' else None, task_id))


def setting_values() -> dict:
	'''Return every stored setting as a dict.'''

	with connect() as db:
		return dict(db.execute('SELECT key, value FROM settings').fetchall())


def settings_save(values: dict):
	'''
	Store settings; a value of None deletes the key.

	:param values: Setting key to string value
	'''

	with connect() as db:
		for key, value in values.items():
			if value is None:
				db.execute('DELETE FROM settings WHERE key = ?', (key,))
			else:
				db.execute('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value', (key, str(value)))


def summary(row: sqlite3.Row, names: dict, tags: dict, notes: dict, files: dict, people: dict, cols: dict) -> dict:
	'''
	Build the card-sized view of a task.

	:param row: Task row
	:param names: Project id to path mapping
	:param tags: Task id to label list mapping
	:param notes: Task id to comment count mapping
	:param files: Task id to attachment count mapping
	:param people: Task id to assignee usernames mapping
	:param cols: Column id to title mapping
	'''

	checks = CHECK_RE.findall(row['description'])
	data   = {key: row[key] for key in ('id', 'project_id', 'column_id', 'title', 'status', 'priority', 'due', 'remind_at', 'position', 'done_at', 'updated', 'archived')}

	return dict(data, project=names[row['project_id']], labels=tags.get(row['id'], []), checks=[sum(c != ' ' for c in checks), len(checks)], comments=notes.get(row['id'], 0), files=files.get(row['id'], 0), assignees=people.get(row['id'], []), column=cols.get(row['column_id']))


def task(task_id: int) -> dict:
	'''
	Return a task with its description, labels, links and comments.

	:param task_id: Task id
	'''

	with connect() as db:
		row   = need_task(db, task_id, VIEW)
		lv    = levels(db)
		names = paths(db)
		tags  = [r['name'] for r in db.execute('SELECT l.name FROM labels l JOIN task_labels tl ON tl.label_id = l.id WHERE tl.task_id = ? ORDER BY l.name', (task_id,))]
		data  = summary(row, names, {task_id: tags}, {}, {}, assignees_map(db, task_id), dict(db.execute('SELECT id, title FROM columns').fetchall()))
		data['role']        = OWN if lv is None else lv.get(row['project_id'], 0)
		data['description'] = row['description']
		data['created']     = row['created']
		data['attachments'] = [dict(r) for r in db.execute('SELECT id, name, mime, size, created FROM attachments WHERE task_id = ? ORDER BY id', (task_id,))]
		data['links']       = [{k: r[k] for k in ('kind', 'id', 'title', 'status')} for r in db.execute('SELECT k.kind, t.id, t.title, t.status, t.project_id FROM links k JOIN tasks t ON t.id = k.other_id WHERE k.task_id = ? ORDER BY k.kind, t.id', (task_id,)) if lv is None or lv.get(r['project_id'])] # hide tasks the user cannot see
		data['comments']    = [dict(r) for r in db.execute('SELECT id, author, body, created FROM comments WHERE task_id = ? ORDER BY id', (task_id,))]
		data['history']     = [dict(r) for r in db.execute('SELECT at, actor, agent, field, old, new FROM task_events WHERE task_id = ? ORDER BY id DESC', (task_id,))]

	return data


def task_create(data: dict) -> dict:
	'''
	Create a task.

	:param data: Fields: project_id, title, and optionally description, status, priority, due, remind_at, labels
	'''

	title = (data.get('title') or '').strip()

	if not title:
		raise ValueError('title is required')

	with connect() as db:
		require(db, 'projects', data.get('project_id'))
		need(db, data['project_id'], EDIT)
		stamp   = now()
		task_id = db.execute('INSERT INTO tasks (project_id, title, created, updated) VALUES (?, ?, ?, ?)', (data['project_id'], title, stamp, stamp)).lastrowid
		set_status(db, task_id, data.get('status') or 'todo', False)
		apply_fields(db, task_id, {key: value for key, value in data.items() if key not in ('project_id', 'title', 'status')}, False)
		record(db, task_id, 'created', None, title)

	return task(task_id)


def task_delete(task_id: int):
	'''
	Delete a task with its comments, labels and links.

	:param task_id: Task id
	'''

	with connect() as db:
		need_task(db, task_id, EDIT)
		db.execute('DELETE FROM tasks WHERE id = ?', (task_id,))


def task_update(task_id: int, data: dict) -> dict:
	'''
	Update any task fields that are present in data.

	:param task_id: Task id
	:param data: Any of: title, description, project_id, status, priority, due, remind_at, labels, assignees
	'''

	with connect() as db:
		need_task(db, task_id, EDIT)
		apply_fields(db, task_id, data)

	return task(task_id)


def tasks(project_id: int = None, archived: bool = False) -> list:
	'''
	Return card summaries for every task, or for one project and its sub-projects.

	:param project_id: Project id, or None for all tasks
	:param archived: Include archived tasks
	'''

	with connect() as db:
		names = paths(db)
		scope = None

		lv = levels(db)
		if project_id:
			require(db, 'projects', project_id)
			need(db, project_id, VIEW)
			prefix = names[project_id] + '/'
			scope  = {pid for pid, path in names.items() if pid == project_id or path.startswith(prefix)}

		tags = {}
		for row in db.execute('SELECT tl.task_id, l.name FROM task_labels tl JOIN labels l ON l.id = tl.label_id ORDER BY l.name'):
			tags.setdefault(row['task_id'], []).append(row['name'])

		notes = dict(db.execute('SELECT task_id, COUNT(*) FROM comments GROUP BY task_id').fetchall())
		files = dict(db.execute('SELECT task_id, COUNT(*) FROM attachments GROUP BY task_id').fetchall())
		people = assignees_map(db)
		cols   = dict(db.execute('SELECT id, title FROM columns').fetchall())
		rows  = db.execute('SELECT * FROM tasks' + ('' if archived else ' WHERE archived = 0') + ' ORDER BY position, id').fetchall()

	return [summary(row, names, tags, notes, files, people, cols) for row in rows if (scope is None or row['project_id'] in scope) and (lv is None or lv.get(row['project_id']))]


def touch(db: sqlite3.Connection, task_id: int):
	'''
	Bump a task's updated time.

	:param db: Open database connection
	:param task_id: Task id
	'''

	db.execute('UPDATE tasks SET updated = ? WHERE id = ?', (now(), task_id))


def valid_label(name: str) -> str:
	'''
	Normalize a label name and check it looks like namespace:value.

	:param name: Label name
	'''

	name = (name or '').strip().lower()

	if not LABEL_RE.match(name):
		raise ValueError(f'label "{name}" must look like namespace:value')

	return name
