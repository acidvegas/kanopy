#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# notify.py

import json
import logging
import re
import time
import urllib.request

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import kanban


BASE_URL   = '' # set by app.py from HOST and PORT
STALE_DAYS = 3 # a task in doing with no update for this many days shows in the digest
MENTION_RE = re.compile(r'(?<![\w@])@([a-z0-9_-]{2,32})')
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36'

DEFAULTS = {
	'ntfy_url'          : 'https://ntfy.sh',
	'ntfy_topic'        : '',
	'ntfy_token'        : '',
	'timezone'          : 'UTC',
	'notify_remind'     : '1',
	'notify_due'        : '1',
	'due_time'          : '09:00',
	'notify_digest'     : '1',
	'digest_time'       : '09:00',
	'notify_agent_done' : '1',
	'archive_days'      : '30'
}


def agent_done(task: dict, agent: str):
	'''
	Push that an agent finished a task, if that notification is on.

	:param task: Task dict from kanban.task
	:param agent: Agent tag
	'''

	if settings()['notify_agent_done'] == '1':
		try:
			send(f'{agent} finished #{task["id"]}', f'{task["title"]}\n{task["project"]}', task['id'], tags='white_check_mark')
		except Exception as exc:
			logging.warning('ntfy agent-done push failed: %s', exc)


def check():
	'''Send any due reminder, due-date and digest pushes that have not been sent yet.'''

	conf  = settings()
	board = bool(conf['ntfy_topic']) # board-wide pushes need the board topic; personal ones go to each user's topic
	now   = datetime.now(ZoneInfo(conf['timezone']))
	stamp = now.strftime('%Y-%m-%dT%H:%M')
	today = now.strftime('%Y-%m-%d')
	clock = now.strftime('%H:%M')
	floor = (now - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
	open_ = [t for t in kanban.tasks() if t['status'] != 'done']

	if board and conf['notify_remind'] == '1':
		for t in open_:
			if t['remind_at'] and floor < t['remind_at'][:16] <= stamp:
				once('remind', f'{t["id"]}:{t["remind_at"]}', lambda t=t: send(f'Reminder #{t["id"]}', f'{t["title"]}\n{t["project"]}', t['id'], priority=4, tags='bell'))

	if board and conf['notify_due'] == '1' and clock >= conf['due_time']:
		for t in open_:
			if t['due'] and t['due'][:10] == today:
				once('due', f'{t["id"]}:{today}', lambda t=t: send(f'Due today #{t["id"]}', f'{t["title"]}\n{t["project"]}', t['id'], tags='calendar'))

	stale = (now - timedelta(days=STALE_DAYS)).strftime('%Y-%m-%d')

	if board and conf['notify_digest'] == '1' and clock >= conf['digest_time']:
		title, body = digest(open_, today, stale)
		if title:
			once('digest', today, lambda: send(title, body, tags='warning'))

	with kanban.connect() as db:
		people = [dict(row) for row in db.execute("SELECT id, username, is_admin, ntfy_topic FROM users WHERE is_admin = 0 AND notify_due = 1 AND ntfy_topic != ''")]
		access = {person['id']: kanban.levels(db, person) for person in people}

	for person in people:
		mine  = [t for t in open_ if person['username'] in t['assignees'] and access[person['id']].get(t['project_id'])] # skip tasks they lost access to
		topic = person['ntfy_topic']
		for t in mine:
			if t['remind_at'] and floor < t['remind_at'][:16] <= stamp:
				once('remind', f'{person["id"]}:{t["id"]}:{t["remind_at"]}', lambda t=t: send(f'Reminder #{t["id"]}', f'{t["title"]}\n{t["project"]}', t['id'], priority=4, tags='bell', topic=topic))
			if clock >= conf['due_time'] and t['due'] and t['due'][:10] == today:
				once('due', f'{person["id"]}:{t["id"]}:{today}', lambda t=t: send(f'Due today #{t["id"]}', f'{t["title"]}\n{t["project"]}', t['id'], tags='calendar', topic=topic))
		title, body = digest(mine, today, stale)
		if title and clock >= conf['digest_time']:
			once('digest', f'{person["id"]}:{today}', lambda title=title, body=body: send(title, body, tags='warning', topic=topic))


def deliver(user: dict, kind: str, task: dict, text: str):
	'''
	Put a notification in a user's inbox and push it to their ntfy topic, if they want this kind and can see the task.

	:param user: Recipient row with prefs
	:param kind: mention, assign or activity
	:param task: Task dict
	:param text: Notification text
	'''

	actor = kanban.ACTOR.get()

	if not user[f'notify_{kind}'] or (actor and actor['id'] == user['id']):
		return

	with kanban.connect() as db:
		lv = kanban.levels(db, user)
		if lv is not None and not lv.get(task['project_id']):
			return
		db.execute('INSERT INTO notifications (user_id, task_id, kind, actor, text, created) VALUES (?, ?, ?, ?, ?, ?)', (user['id'], task['id'], kind, actor['username'] if actor else 'system', text, kanban.now()))

	if user['ntfy_topic']:
		try:
			send(text, f'{task["title"]}\n{task["project"]}', task['id'], tags={'mention': 'speech_balloon', 'assign': 'bust_in_silhouette', 'activity': 'memo'}[kind], topic=user['ntfy_topic'])
		except Exception as exc:
			logging.warning('ntfy push to %s failed: %s', user['username'], exc)


def digest(tasks: list, today: str, stale: str) -> tuple:
	'''
	Build the daily digest: overdue tasks, then tasks stuck in doing. Returns ('', '') when there is nothing to send.

	:param tasks: Open tasks to consider
	:param today: Today as YYYY-MM-DD
	:param stale: Tasks in doing last updated on or before this date are stuck
	'''

	late  = sorted((t for t in tasks if t['due'] and t['due'][:10] < today), key=lambda t: t['due'])
	stuck = sorted((t for t in tasks if t['status'] == 'doing' and t['updated'][:10] <= stale), key=lambda t: t['updated'])
	parts = ([f'{len(late)} overdue'] if late else []) + ([f'{len(stuck)} stuck in doing'] if stuck else [])
	lines = [f'#{t["id"]} {t["title"]} (due {t["due"][:10]})' for t in late] + [f'#{t["id"]} {t["title"]} (doing, no update since {t["updated"][:10]})' for t in stuck]

	return ', '.join(parts), '\n'.join(lines[:20])


def loop():
	'''Run check() every 30 seconds forever; meant for a daemon thread.'''

	while True:
		try:
			check()
		except Exception as exc:
			logging.warning('notify check failed: %s', exc)
		time.sleep(30)


def once(kind: str, key: str, push):
	'''
	Run push() unless this notification was already sent, then record it. A failed push is retried next pass.

	:param kind: remind, due or digest
	:param key: Unique key within the kind
	:param push: Callable that sends the notification
	'''

	with kanban.connect() as db:
		if db.execute('SELECT 1 FROM sent WHERE kind = ? AND key = ?', (kind, key)).fetchone():
			return

	push()

	with kanban.connect() as db:
		db.execute('INSERT OR IGNORE INTO sent (kind, key, at) VALUES (?, ?, ?)', (kind, key, kanban.now()))


def save(values: dict):
	'''
	Validate and store notification settings. An empty ntfy_token keeps the stored one; clear_token removes it.

	:param values: Settings from the UI
	'''

	clean = {}

	for key, value in values.items():
		if key not in DEFAULTS:
			continue
		value = str(value).strip()
		if key.startswith('notify_') and value not in ('0', '1'):
			raise ValueError(f'{key} must be 0 or 1')
		if key.endswith('_time') and not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', value):
			raise ValueError(f'{key} must look like HH:MM')
		if key == 'timezone':
			try:
				ZoneInfo(value)
			except Exception:
				raise ValueError(f'unknown timezone {value}')
		if key == 'archive_days' and not (value.isdigit() and int(value) <= 3650):
			raise ValueError('archive_days must be 0-3650 (0 turns it off)')
		if key == 'ntfy_url' and not re.match(r'^https?://', value):
			raise ValueError('ntfy_url must start with http:// or https://')
		if key == 'ntfy_token' and not value:
			continue
		clean[key] = value

	if values.get('clear_token'):
		clean['ntfy_token'] = None

	kanban.settings_save(clean)


def send(title: str, message: str, task_id: int = None, priority: int = 3, tags: str = '', topic: str = None):
	'''
	Publish one ntfy message; raises on failure.

	:param title: Notification title
	:param message: Body text
	:param task_id: Task to open when the notification is tapped
	:param priority: ntfy priority 1-5
	:param tags: ntfy tags (emoji shortcodes)
	:param topic: ntfy topic, default the board topic from settings
	'''

	conf    = settings()
	body    = {'topic': topic or conf['ntfy_topic'], 'title': title, 'message': message, 'priority': priority, 'tags': [tags] if tags else []}
	headers = {'Content-Type': 'application/json', 'User-Agent': USER_AGENT}

	if task_id:
		body['click'] = f'{BASE_URL}/#/task/{task_id}'
	if conf['ntfy_token']:
		headers['Authorization'] = f'Bearer {conf["ntfy_token"]}'

	req = urllib.request.Request(conf['ntfy_url'].rstrip('/'), data=json.dumps(body).encode('utf-8'), headers=headers, method='POST')

	with urllib.request.urlopen(req, timeout=10) as resp:
		resp.read()


def settings() -> dict:
	'''Return notification settings with defaults filled in.'''

	return {**DEFAULTS, **{k: v for k, v in kanban.setting_values().items() if k in DEFAULTS}}


def task_event(before: dict, after: dict, comment: str = ''):
	'''
	Fan out assignment, @mention and activity notifications for one task change made by the signed-in user.

	:param before: Task before the change, or None for a new task
	:param after: Task after the change
	:param comment: Body of a new comment, if the change was a comment
	'''

	actor = kanban.ACTOR.get()

	if not actor:
		return

	with kanban.connect() as db:
		people = {row['username']: dict(row) for row in db.execute('SELECT * FROM users')}

	who   = actor['username']
	tag   = f'#{after["id"]}'
	done  = set()
	old   = set(before['assignees']) if before else set()

	for name in sorted(set(after['assignees']) - old):
		if name in people:
			deliver(people[name], 'assign', after, f'{who} assigned you {tag}')
			done.add(name)

	text = comment
	if not before or before['description'] != after['description']:
		text += '\n' + after['description']
	seen = set(MENTION_RE.findall(before['description'])) if before and not comment else set()

	for name in sorted(set(MENTION_RE.findall(text)) - seen - done):
		if name in people:
			deliver(people[name], 'mention', after, f'{who} mentioned you in {tag}')
			done.add(name)

	what = f'{who} commented on {tag}' if comment else f'{who} moved {tag} to {after["status"]}' if before and before['status'] != after['status'] else ''

	if what:
		for name in after['assignees']:
			if name in people and name not in done:
				deliver(people[name], 'activity', after, what)
