#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# mcp_server.py

import re

from datetime import datetime, timedelta, timezone
from pathlib import Path

import accounts
import kanban
import notify


INSTRUCTIONS = next(p for p in (Path(__file__).parent / 'instructions.txt', Path(__file__).parent / 'instructions.example.txt') if p.exists()).read_text().strip() # your own instructions.txt wins
NAMESPACES   = {'host': True, 'type': False} # namespace -> new values allowed without create
MAX_TEXT     = 20_000 # characters of an attachment returned to an agent
PROTOCOL     = '2025-06-18'
REL_NAMES    = (('blocking', 'blocks'), ('blocked', 'blocked-by'), ('parenttask', 'parent'), ('subtask', 'subtasks'), ('related', 'related'), ('precedes', 'precedes'), ('follows', 'follows'), ('duplicateof', 'duplicate-of'), ('duplicates', 'duplicates'))

STR  = {'type': 'string'}
INT  = {'type': 'integer'}
BOOL = {'type': 'boolean'}
LIST = {'type': 'array', 'items': STR}

TOOLS = [
	('list_projects', 'Project tree with open-task counts.', {}, []),
	('save_project', 'Create a project by path (parents too), or update the given project: move to path, description, folder, archived.', {
		'path'        : {**STR, 'description': 'like AI/hermes'},
		'project'     : {**STR, 'description': 'path or id to update; omit to create'},
		'description' : {**STR, 'description': 'one line'},
		'folder'      : {**STR, 'description': 'local repo dir; "" clears'},
		'archived'    : BOOL
	}, []),
	('list_tasks', 'Tasks as compact lines under [project] headers. Filters AND together.', {
		'project'      : {**STR, 'description': 'path or id, includes sub-projects'},
		'labels'       : {**LIST, 'description': 'must have all'},
		'text'         : {**STR, 'description': 'all words in title, description or comments'},
		'status'       : {**STR, 'description': 'open (default), todo, doing, done, all'},
		'priority_min' : {**INT, 'description': '1-5'},
		'due_before'   : {**STR, 'description': 'YYYY-MM-DD'},
		'due'          : {**STR, 'description': 'overdue, today, week, any, none'},
		'assignee'     : {**STR, 'description': 'username or me'},
		'changed'      : {**STR, 'description': 'since YYYY-MM-DD or Nd; newest first, any status'},
		'limit'        : {**INT, 'description': 'default 50'}
	}, []),
	('get_task', 'Full tasks: description, relations, comments. file= returns one attachment\'s text instead.', {'ids': {'type': 'array', 'items': INT}, 'file': {**INT, 'description': 'attachment id'}}, []),
	('save_task', 'Create (project + title) or update by id; only given fields change.', {
		'id'            : {**INT, 'description': 'omit to create'},
		'ids'           : {'type': 'array', 'items': INT, 'description': 'apply the same update to each'},
		'project'       : {**STR, 'description': 'path or id; moves on update'},
		'title'         : STR,
		'description'   : {**STR, 'description': 'markdown, replaces it all; steps as "- [ ] "'},
		'add_labels'    : {**LIST, 'description': 'like host:pi'},
		'remove_labels' : LIST,
		'assign'        : {**LIST, 'description': 'usernames'},
		'unassign'      : LIST,
		'priority'      : {**INT, 'description': '1-5, 0 clears'},
		'due'           : {**STR, 'description': 'YYYY-MM-DD, "" clears'},
		'remind_at'     : {**STR, 'description': 'YYYY-MM-DDTHH:MM local, "" clears'},
		'status'        : {**STR, 'description': 'todo, doing, done'},
		'check'         : {**STR, 'description': 'tick checklist item by number or unique text'},
		'checked'       : {**BOOL, 'description': 'false unticks'},
		'comment'       : {**STR, 'description': 'markdown note added with the change'},
		'attach'        : {'type': 'object', 'properties': {'name': STR, 'text': STR}, 'required': ['name', 'text'], 'description': 'text file to attach'}
	}, []),
	('comment', 'Add a markdown progress note to a task.', {'id': INT, 'text': STR}, ['id', 'text']),
	('link', 'Relate two tasks, or remove the relation.', {
		'id'     : INT,
		'other'  : INT,
		'kind'   : {**STR, 'description': 'id is <kind> other: blocked, blocking, subtask, parenttask, related, precedes, follows, duplicateof, duplicates'},
		'remove' : BOOL
	}, ['id', 'other', 'kind']),
	('labels', 'List labels. create: add a type: value (only when asked). delete: remove a label everywhere.', {'create': STR, 'delete': STR}, []),
	('inbox', 'Your unread notifications; read=true marks them all read.', {'read': BOOL}, []),
	('delete', 'Delete a task or an empty project. Confirm first.', {'task': INT, 'project': {**STR, 'description': 'path or id'}}, [])
]


def extras(task_id: int, args: dict, agent: str):
	'''
	Add args['comment'] (with notifications) and args['attach'] to a task, when given.

	:param task_id: Task id
	:param args: Tool arguments
	:param agent: Agent tag used as comment author
	'''

	if text := (args.get('comment') or '').strip():
		task = kanban.comment_add(task_id, agent, text)
		notify.task_event(task, task, text)

	if file := args.get('attach'):
		kanban.attachment_add(task_id, file['name'], 'text/plain', file['text'].encode())


def call(name: str, args: dict, agent: str) -> str:
	'''
	Run a tool and return its text output.

	:param name: Tool name
	:param args: Tool arguments
	:param agent: Agent tag used as comment author
	'''

	if name == 'list_projects':
		rows = kanban.projects()
		roles = {0: ', no access', 1: ', viewer', 2: ', editor', 3: ', manager', 4: ''}
		return '\n'.join(f'{p["path"]}  (id {p["id"]}, {p["open"]} open{", archived" if p["archived"] else ""}{roles[p["role"]]}){"  " + p["folder"] if p["folder"] else ""}' for p in rows)

	if name == 'save_project':
		pid = project_id(args['project']) if args.get('project') else None
		if not pid and not args.get('path'):
			raise ValueError('path is required to create a project')
		saved = kanban.project_save(args.get('path'), pid, args.get('description'), args.get('archived'), folder=args.get('folder'))
		return f'{"updated" if pid else "saved"} {project_path(saved["id"])} (id {saved["id"]})'

	if name == 'list_tasks':
		return list_tasks(args)

	if name == 'get_task':
		if args.get('file'):
			return read_file(int(args['file']))
		if not (args.get('ids') or args.get('id')):
			raise ValueError('give ids or file')
		return '\n\n---\n\n'.join(get_task(int(i)) for i in args.get('ids') or [args['id']])

	if name == 'save_task':
		return save_task(args, agent)

	if name == 'comment':
		extras(int(args['id']), {'comment': args.get('text', '')}, agent)
		return f'commented on #{args["id"]}'

	if name == 'link':
		action = kanban.link_remove if args.get('remove') else kanban.link_add
		action(int(args['id']), args['kind'], int(args['other']))
		return task_line(int(args['id']))

	if name == 'labels':
		return labels(args)

	if name == 'inbox':
		return inbox(args)

	if name == 'delete':
		if args.get('task'):
			title = kanban.task(int(args['task']))['title']
			kanban.task_delete(int(args['task']))
			return f'deleted #{args["task"]} {title}'
		if args.get('project'):
			pid  = project_id(args['project'])
			path = project_path(pid)
			kanban.project_delete(pid)
			return f'deleted {path}'
		raise ValueError('give task or project')

	raise LookupError(f'unknown tool {name}')


def check_item(description: str, item: str, checked: bool) -> str:
	'''
	Tick or untick one checklist item in a markdown description.

	:param description: Markdown description
	:param item: 1-based item number or unique text fragment
	:param checked: New state
	'''

	lines = description.split('\n')
	items = [i for i, line in enumerate(lines) if re.match(r'^\s*[-*] \[( |x|X)\] ', line)]

	if item.strip().isdigit():
		num = int(item)
		if not 1 <= num <= len(items):
			raise ValueError(f'checklist has {len(items)} items')
		hits = [items[num - 1]]
	else:
		hits = [i for i in items if item.lower() in lines[i].lower()]
		if len(hits) != 1:
			raise ValueError(f'"{item}" matches {len(hits)} checklist items, use the item number')

	lines[hits[0]] = re.sub(r'\[( |x|X)\]', '[x]' if checked else '[ ]', lines[hits[0]], count=1)

	return '\n'.join(lines)


def context(cwd: str) -> str:
	'''
	Session-start briefing for an agent: the project whose folder is the deepest one containing cwd, and its doing and open tasks.

	:param cwd: Agent's working directory
	'''

	best = None

	for p in kanban.projects():
		if not p['folder'] or p.get('role') == 0: # role 0: ancestor shown only for the tree
			continue
		path = Path(p['folder']).expanduser()
		if (Path(cwd) == path or path in Path(cwd).parents) and (not best or len(path.parts) > len(best[1].parts)):
			best = (p, path)

	if not best:
		return ''

	proj  = best[0]
	open_ = sorted((t for t in kanban.tasks(proj['id']) if t['status'] != 'done'), key=lambda t: (t['status'] != 'doing', -t['priority'], t['id']))

	with kanban.connect() as db:
		rels = links_map(db)

	now   = datetime.now(timezone.utc)
	idle  = lambda t: (now - datetime.fromisoformat(t['updated'])).days if t['status'] == 'doing' else 0 # days a doing task has gone without an update
	lines = [f'kanopy project for this directory: {proj["path"]} ({len(open_)} open)']
	if (user := kanban.ACTOR.get()) and (unread := accounts.notifications(user)['unread']):
		lines.append(f'{unread} unread notifications (inbox tool)')
	lines += [line(t, rels.get(t['id'], {}), project=t['project'] != proj['path']) + (f'  (doing, no update in {idle(t)}d: still being worked on?)' if idle(t) else '') for t in open_[:10]]

	if len(open_) > 10:
		lines.append(f'... {len(open_) - 10} more (list_tasks)')

	return '\n'.join(lines)


def get_task(task_id: int) -> str:
	'''
	Render a full task: line, description, relations and comments.

	:param task_id: Task id
	'''

	t   = kanban.task(task_id)
	out = [task_line(task_id)]

	if t['description'].strip():
		out += ['', t['description'].strip()]

	rels = {kind: label for kind, label in REL_NAMES}
	for link in t['links']:
		out.append(f'\n{rels.get(link["kind"], link["kind"])} #{link["id"]} {link["title"]}')

	if t['attachments']:
		out.append('\nfiles: ' + ', '.join(f'#{a["id"]} {a["name"]} ({a["size"] // 1024 or 1} KB)' for a in t['attachments']))

	if t['comments']:
		out.append('\ncomments:')
		out += [f'- {c["created"][:10]} [{c["author"]}] {c["body"]}' for c in t['comments']]

	return '\n'.join(out)


def handle(msg: dict, agent: str) -> dict:
	'''
	Answer one MCP JSON-RPC message; returns None for notifications.

	:param msg: Decoded JSON-RPC request
	:param agent: Agent tag from the X-Agent header
	'''

	method = msg.get('method', '')
	rid    = msg.get('id')

	if rid is None:
		return None

	if method == 'initialize':
		result = {'protocolVersion': (msg.get('params') or {}).get('protocolVersion') or PROTOCOL, 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'kanopy', 'version': '1.0.0'}, 'instructions': INSTRUCTIONS}
	elif method == 'ping':
		result = {}
	elif method == 'tools/list':
		result = {'tools': [{'name': n, 'description': d, 'inputSchema': {'type': 'object', 'properties': p, 'required': r, 'additionalProperties': False}} for n, d, p, r in TOOLS]}
	elif method == 'tools/call':
		params = msg.get('params') or {}
		try:
			result = {'content': [{'type': 'text', 'text': call(params.get('name'), params.get('arguments') or {}, agent)}]}
		except (LookupError, PermissionError, ValueError, TypeError) as exc:
			result = {'content': [{'type': 'text', 'text': f'error: {exc}'}], 'isError': True}
	else:
		return {'jsonrpc': '2.0', 'id': rid, 'error': {'code': -32601, 'message': f'method not found: {method}'}}

	return {'jsonrpc': '2.0', 'id': rid, 'result': result}


def inbox(args: dict) -> str:
	'''
	List the agent user's unread notifications, optionally marking them read.

	:param args: Tool arguments with optional read
	'''

	user = kanban.ACTOR.get()
	rows = [n for n in accounts.notifications(user)['notifications'] if not n['read']]

	if args.get('read'):
		accounts.notifications_read(user)

	return '\n'.join(f'- {n["created"][:16]} {n["text"]}' + (f': {n["title"]}' if n['title'] else '') for n in rows) or 'no unread notifications'


def labels(args: dict) -> str:
	'''
	Create or delete a label if asked, then list labels grouped by namespace.

	:param args: Tool arguments with optional create / delete
	'''

	if args.get('create'):
		kanban.label_create(norm_label(args['create'])[0])

	if args.get('delete'):
		kanban.label_delete(args['delete'])

	groups = {}
	for label in kanban.labels():
		ns, _, value = label['name'].partition(':')
		groups.setdefault(ns, []).append(value)

	return '\n'.join(f'{ns}: {", ".join(values)}' for ns, values in sorted(groups.items()))


def line(t: dict, rels: dict, project: bool = True) -> str:
	'''
	Render a task summary as one compact line.

	:param t: Task summary from kanban.tasks / kanban.task
	:param rels: Kind -> other task ids
	:param project: Include the [project] prefix
	'''

	out = f'#{t["id"]} ' + {'doing': '▶ ', 'done': '✓ '}.get(t['status'], '') + (f'[{t["project"]}] ' if project else '') + t['title']

	if t['archived']:
		out += '  archived'
	if t['files']:
		out += f'  files:{t["files"]}'

	if t['priority']:
		out += f'  p{t["priority"]}'
	if t['due']:
		out += f'  due:{t["due"][:10]}'
	if t['remind_at']:
		out += f'  remind:{t["remind_at"][:16]}'
	if t['checks'][1]:
		out += f'  ☐{t["checks"][0]}/{t["checks"][1]}'
	if t['labels']:
		out += '  ' + ','.join(t['labels'])
	if t['assignees']:
		out += '  ' + ' '.join(f'@{a}' for a in t['assignees'])

	for kind, label in REL_NAMES:
		if rels.get(kind):
			out += f'  {label}:' + ','.join(f'#{i}' for i in rels[kind])

	return out


def links_map(db) -> dict:
	'''
	Return task id -> {kind: [other ids]} for every link.

	:param db: Open database connection
	'''

	out = {}

	for row in db.execute('SELECT task_id, kind, other_id FROM links ORDER BY other_id'):
		out.setdefault(row['task_id'], {}).setdefault(row['kind'], []).append(row['other_id'])

	return out


def list_tasks(args: dict) -> str:
	'''
	Filter tasks and render them as compact lines.

	:param args: Tool arguments
	'''

	changed = (args.get('changed') or '').strip()
	status  = args.get('status') or ('all' if changed else 'open')
	cutoff  = None

	if changed:
		days = re.fullmatch(r'(\d+)d', changed)
		if not days and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', changed):
			raise ValueError('changed takes YYYY-MM-DD or a day count like 2d')
		cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days[1]))).isoformat() if days else changed

	if status not in ('open', 'todo', 'doing', 'done', 'all'):
		raise ValueError('status must be open, todo, doing, done or all')

	want  = [norm_label(l)[0] for l in args.get('labels') or []]
	text  = (args.get('text') or '').strip()
	due   = args.get('due') or ''
	rows  = kanban.tasks(project_id(args['project']) if args.get('project') else None, archived=bool(text) or status in ('done', 'all'))
	match = {r['id'] for r in kanban.search(text, due=due)} if text or due else None
	who   = (args.get('assignee') or '').strip().lower().lstrip('@')
	who   = kanban.ACTOR.get()['username'] if who == 'me' and kanban.ACTOR.get() else who

	out = []
	for t in rows:
		if status == 'open' and t['status'] == 'done' or status not in ('open', 'all') and t['status'] != status:
			continue
		if t['priority'] < (args.get('priority_min') or 0):
			continue
		if args.get('due_before') and not (t['due'] and t['due'][:10] < args['due_before']):
			continue
		if match is not None and t['id'] not in match:
			continue
		if not all(w in t['labels'] for w in want):
			continue
		if who and who not in t['assignees']:
			continue
		if cutoff and t['updated'] < cutoff:
			continue
		out.append(t)

	if not out:
		return 'no matching tasks'

	out.sort(key=lambda t: (t['project'].lower(), t['status'] == 'done', -t['priority'], t['id']))
	limit = args.get('limit') or 50

	with kanban.connect() as db:
		rels = links_map(db)

	if cutoff: # newest first, one flat list
		out.sort(key=lambda t: t['updated'], reverse=True)
		lines = [line(t, rels.get(t['id'], {})) + f'  changed:{t["updated"][:10]}' for t in out[:limit]]
		if len(out) > limit:
			lines.append(f'... {len(out) - limit} more (raise limit)')
		return '\n'.join(lines)

	lines, group = [], None
	for t in out[:limit]:
		if t['project'] != group:
			group = t['project']
			lines.append(f'[{group}]')
		lines.append(line(t, rels.get(t['id'], {}), project=False))

	if len(out) > limit:
		lines.append(f'... {len(out) - limit} more (raise limit)')

	return '\n'.join(lines)


def norm_label(name: str) -> tuple:
	'''
	Normalize a label and check its namespace; returns (name, namespace).

	:param name: Label like host:pi
	'''

	name = name.strip().lower()
	ns, _, value = name.partition(':')
	allowed = ', '.join(f'{n}:' for n in NAMESPACES)

	if not value:
		raise ValueError(f'label "{name}" needs a namespace: {allowed}')

	if ns not in NAMESPACES:
		raise ValueError(f'unknown namespace "{ns}:" in "{name}", allowed: {allowed}')

	if not kanban.LABEL_RE.match(name):
		raise ValueError(f'label "{name}" has invalid characters')

	return name, ns


def project_id(ref) -> int:
	'''
	Resolve a project path (case-insensitive) or id to an id.

	:param ref: Path like AI/hermes or a numeric id
	'''

	ref = str(ref).strip().strip('/')

	for p in kanban.projects():
		if str(p['id']) == ref or p['path'].lower() == ref.lower():
			return p['id']

	raise LookupError(f'project "{ref}" not found (list_projects shows paths)')


def project_path(pid: int) -> str:
	'''
	Return a project's path.

	:param pid: Project id
	'''

	with kanban.connect() as db:
		return kanban.paths(db)[pid]


def read_file(file_id: int) -> str:
	'''
	Return an attachment's text, capped at MAX_TEXT characters.

	:param file_id: Attachment id
	'''

	row  = kanban.attachment_get(file_id)
	data = bytes(row['data'])

	try:
		text = data.decode()
	except UnicodeDecodeError:
		text = '\x00'

	if '\x00' in text:
		return f'{row["name"]} is binary ({row["mime"]}, {row["size"] // 1024 or 1} KB); open it in the web UI'

	if len(text) > MAX_TEXT:
		return text[:MAX_TEXT] + f'\n... truncated, {len(text) // 1024} KB total'

	return text


def save_task(args: dict, agent: str) -> str:
	'''
	Create or update a task from tool arguments; pushes an ntfy note when an agent marks a task done.

	:param args: Tool arguments
	:param agent: Agent tag
	'''

	if args.get('ids'):
		out = []
		for task_id in args['ids']:
			try:
				out.append(save_task({**args, 'id': task_id, 'ids': None}, agent))
			except (LookupError, PermissionError, ValueError, TypeError) as exc:
				out.append(f'#{task_id} error: {exc}')
		return '\n'.join(out)

	fields = {key: args[key] for key in ('title', 'description', 'priority', 'due', 'remind_at', 'status') if key in args}

	if args.get('project'):
		fields['project_id'] = project_id(args['project'])

	add    = [norm_label(l)[0] for l in args.get('add_labels') or []]
	remove = {norm_label(l)[0] for l in args.get('remove_labels') or []}
	validate_labels(add)

	assign   = [n.strip().lower().lstrip('@') for n in args.get('assign') or []]
	unassign = {n.strip().lower().lstrip('@') for n in args.get('unassign') or []}

	if args.get('id'):
		t = kanban.task(int(args['id']))
		if add or remove:
			fields['labels'] = [l for l in t['labels'] if l not in remove] + add
		if assign or unassign:
			fields['assignees'] = [a for a in t['assignees'] if a not in unassign] + assign
		if args.get('check'):
			fields['description'] = check_item(fields.get('description', t['description']), str(args['check']), args.get('checked', True))
		saved = kanban.task_update(t['id'], fields)
		notify.task_event(t, saved)
		out = task_line(t['id'])
		if saved['status'] == 'done' and t['status'] != 'done':
			notify.agent_done(saved, agent)
			boxes = kanban.CHECK_RE.findall(saved['description'])
			if open_ := sum(c == ' ' for c in boxes):
				out += f'\n{open_}/{len(boxes)} checklist items are unticked: tick the finished ones (check=) and comment why the rest were skipped'
		extras(t['id'], args, agent)
		return out

	if not fields.get('project_id') or not (fields.get('title') or '').strip():
		raise ValueError('project and title are required to create a task')

	fields['labels'] = add
	if assign:
		fields['assignees'] = assign
	created = kanban.task_create(fields)
	notify.task_event(None, created)
	extras(created['id'], args, agent)

	return task_line(created['id'])


def task_line(task_id: int) -> str:
	'''
	Render one task as a compact line.

	:param task_id: Task id
	'''

	with kanban.connect() as db:
		rels = links_map(db).get(task_id, {})

	return line(kanban.task(task_id), rels)


def validate_labels(names: list):
	'''
	Enforce the label policy: type: values must already exist; new host: values must not squash-match an existing one.

	:param names: Normalized label names
	'''

	existing = {l['name'] for l in kanban.labels()}
	squash   = lambda s: re.sub(r'[-_. ]', '', s)

	for name in names:
		if name in existing:
			continue
		ns = name.split(':')[0]
		same_ns = sorted(e for e in existing if e.startswith(ns + ':'))
		if not NAMESPACES[ns]:
			raise ValueError(f'{name} does not exist; type: values are fixed (existing: {", ".join(same_ns)}); labels(create=...) only when the user asks')
		clash = [e for e in same_ns if squash(e) == squash(name)]
		if clash:
			raise ValueError(f'{name} looks like existing {clash[0]}, use that')
