#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# tests/test_notify.py

import http.server
import json
import queue
import threading
import unittest

from datetime import datetime, timedelta, timezone

from kanopy import accounts
from kanopy import kanban
from kanopy import notify

from .base import Base


class TestSettings(Base):
	'''Notification settings.'''

	def test_defaults_and_save(self):
		'''Unset keys fall back to defaults; unknown keys are ignored.'''

		self.assertEqual(notify.settings(), notify.DEFAULTS)
		notify.save({'ntfy_topic': ' board ', 'timezone': 'America/New_York', 'bogus': 'x'})
		conf = notify.settings()
		self.assertEqual((conf['ntfy_topic'], conf['timezone']), ('board', 'America/New_York'))
		self.assertNotIn('bogus', kanban.setting_values())


	def test_validation(self):
		'''Every setting is checked.'''

		for bad in ({'notify_due': 'yes'}, {'due_time': '25:00'}, {'digest_time': '9am'}, {'timezone': 'Mars/Base'}, {'archive_days': '-1'}, {'archive_days': '9999'}, {'ntfy_url': 'ftp://x'}):
			with self.assertRaises(ValueError, msg=bad):
				notify.save(bad)


	def test_token_kept_and_cleared(self):
		'''An empty token keeps the saved one; clear_token removes it.'''

		notify.save({'ntfy_token': 'tk_1'})
		notify.save({'ntfy_token': ''})
		self.assertEqual(notify.settings()['ntfy_token'], 'tk_1')
		notify.save({'clear_token': True})
		self.assertEqual(notify.settings()['ntfy_token'], '')


class TestPushes(Base):
	'''Scheduled pushes.'''

	def setUp(self):
		'''A board topic in UTC with every push due from midnight.'''

		super().setUp()
		notify.save({'ntfy_topic': 'board', 'timezone': 'UTC', 'due_time': '00:00', 'digest_time': '00:00'})
		self.now = datetime.now(timezone.utc)


	def titles(self) -> list:
		'''Titles of the pushes sent so far.'''

		return [p['title'] for p in self.pushes]


	def test_reminder_due_and_digest_once(self):
		'''Reminders, due-today and the digest each go out once.'''

		r = self.task(title='remind', remind_at=self.now.strftime('%Y-%m-%dT%H:%M'))
		d = self.task(title='due', due=self.now.strftime('%Y-%m-%d'))
		self.task(title='late', due='2000-01-01')
		self.task(title='ok', due='2000-01-01', status='done')
		notify.check()
		notify.check()
		self.assertEqual(sorted(self.titles()), sorted([f'Reminder #{r["id"]}', f'Due today #{d["id"]}', '1 overdue']))


	def test_failed_push_does_not_block_others(self):
		'''A push that fails is skipped for this pass and retried later, and the pushes after it still go out.'''

		a = self.task(title='a', due=self.now.strftime('%Y-%m-%d'))
		b = self.task(title='b', due=self.now.strftime('%Y-%m-%d'))
		record = notify.send
		def flaky(title, *args, **kwargs):
			if title == f'Due today #{a["id"]}':
				raise OSError('ntfy down')
			record(title, *args, **kwargs)
		notify.send = flaky
		notify.check()
		self.assertIn(f'Due today #{b["id"]}', self.titles())
		notify.send = record
		notify.check()
		self.assertIn(f'Due today #{a["id"]}', self.titles())


	def test_old_reminders_skipped(self):
		'''Reminders more than a day old and in the future are not sent.'''

		self.task(remind_at=(self.now - timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'))
		self.task(remind_at=(self.now + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M'))
		notify.check()
		self.assertEqual(self.titles(), [])


	def test_toggles_and_no_topic(self):
		'''Turned-off kinds and a missing board topic send nothing board-wide.'''

		self.task(due=self.now.strftime('%Y-%m-%d'))
		notify.save({'notify_due': '0'})
		notify.check()
		self.assertEqual(self.titles(), [])
		notify.save({'notify_due': '1', 'ntfy_topic': ''})
		notify.check()
		self.assertEqual(self.titles(), [])


	def test_admin_personal_pushes(self):
		'''Admins with their own topic get personal pushes too, unless that topic is the board topic.'''

		root = self.user('root', admin=True)
		accounts.user_update(root, {'ntfy_topic': 'root-topic'})
		t = self.task('Root', 'mine', due=self.now.strftime('%Y-%m-%d'), assignees=['root'])
		notify.check()
		self.assertIn((f'Due today #{t["id"]}', 'root-topic'), [(p['title'], p['topic']) for p in self.pushes])
		self.pushes.clear()
		accounts.user_update(root, {'ntfy_topic': 'board'})
		with kanban.connect() as db:
			db.execute('DELETE FROM sent')
		notify.check()
		self.assertEqual([p['topic'] for p in self.pushes if p['title'] == f'Due today #{t["id"]}'], [None]) # once, on the board topic


	def test_personal_pushes(self):
		'''Non-admins with a topic get pushes for tasks assigned to them that they can still see.'''

		notify.save({'ntfy_topic': ''})
		ann = self.user('ann')
		accounts.user_update(ann, {'ntfy_topic': 'ann-topic'})
		self.act(ann)
		t = self.task('Ann', 'mine', due=self.now.strftime('%Y-%m-%d'), assignees=['ann'])
		self.act(None)
		notify.check()
		self.assertEqual([(p['title'], p['topic']) for p in self.pushes], [(f'Due today #{t["id"]}', 'ann-topic')])


	def test_personal_server_and_token(self):
		'''Personal pushes use the user's own ntfy server and token, or ntfy.sh with no token, never the board's.'''

		notify.save({'ntfy_topic': 'board', 'ntfy_url': 'https://push.example.com', 'ntfy_token': 'board-secret'})
		ann, bob = self.user('ann'), self.user('bob')
		accounts.user_update(ann, {'ntfy_topic': 'ann-topic', 'ntfy_url': 'https://ntfy.ann.example/', 'ntfy_token': 'ann-secret'})
		accounts.user_update(bob, {'ntfy_topic': 'bob-topic'})
		self.act(ann)
		a = self.task('Ann', 'mine', due=self.now.strftime('%Y-%m-%d'), assignees=['ann'])
		self.act(bob)
		b = self.task('Bob', 'his', due=self.now.strftime('%Y-%m-%d'), assignees=['bob'])
		self.act(None)
		notify.check()
		got = {p['topic']: (p['server'], p['token']) for p in self.pushes if p['topic'] in ('ann-topic', 'bob-topic')}
		self.assertEqual(got, {'ann-topic': ('https://ntfy.ann.example', 'ann-secret'), 'bob-topic': ('https://ntfy.sh', '')})
		self.assertIn(f'Due today #{a["id"]}', [p['title'] for p in self.pushes if p['topic'] is None]) # the board topic still hears about everything
		self.assertIn(f'Due today #{b["id"]}', [p['title'] for p in self.pushes if p['topic'] is None])


	def test_personal_reminder_and_digest(self):
		'''Assigned users also get their own reminder and digest.'''

		notify.save({'ntfy_topic': ''})
		ann = self.user('ann')
		accounts.user_update(ann, {'ntfy_topic': 'ann-topic'})
		self.act(ann)
		t = self.task('Ann', 'mine', remind_at=self.now.strftime('%Y-%m-%dT%H:%M'), assignees=['ann'])
		self.task('Ann', 'late', due='2000-01-01', assignees=['ann'])
		self.act(None)
		notify.check()
		self.assertEqual(sorted(p['title'] for p in self.pushes), sorted([f'Reminder #{t["id"]}', '1 overdue']))
		self.assertEqual({p['topic'] for p in self.pushes}, {'ann-topic'})


	def test_send_request(self):
		'''send posts JSON to the ntfy server with a browser User-Agent, the bearer token and a click link.'''

		sent = []

		class Resp:
			def __enter__(self): return self
			def __exit__(self, *exc): return False
			def read(self): return b''

		def urlopen(req, timeout):
			sent.append((req, timeout))
			return Resp()

		real, notify.send = notify.send, self.old[3]
		old_open, notify.urllib.request.urlopen = notify.urllib.request.urlopen, urlopen
		try:
			notify.save({'ntfy_url': 'https://push.example/', 'ntfy_token': 'tk_9'})
			notify.send('title', 'body', 42, tags='bell')
			notify.send('mine', 'body', topic='ann-topic', server='https://ntfy.ann.example', token='')
		finally:
			notify.send, notify.urllib.request.urlopen = real, old_open
		req, timeout = sent[0]
		body = json.loads(req.data)
		self.assertEqual((req.full_url, req.get_method(), timeout), ('https://push.example', 'POST', 10))
		self.assertEqual(req.get_header('User-agent'), notify.USER_AGENT)
		own = sent[1][0]
		self.assertEqual((own.full_url, own.get_header('Authorization'), json.loads(own.data)['topic']), ('https://ntfy.ann.example', None, 'ann-topic')) # a personal push never carries the board's token
		self.assertEqual(req.get_header('Authorization'), 'Bearer tk_9')
		self.assertEqual((body['topic'], body['title'], body['tags'], body['click']), ('board', 'title', ['bell'], f'{notify.BASE_URL}/#/task/42'))
		self.assertNotIn('python', notify.USER_AGENT.lower())


	def test_digest_content(self):
		'''The digest lists overdue tasks then tasks stuck in doing.'''

		tasks = [{'id': 1, 'title': 'late', 'due': '2026-01-01', 'status': 'todo', 'updated': '2026-01-05'}, {'id': 2, 'title': 'stuck', 'due': None, 'status': 'doing', 'updated': '2026-01-01T00:00'}, {'id': 3, 'title': 'fine', 'due': None, 'status': 'doing', 'updated': '2026-01-09'}]
		title, body = notify.digest(tasks, '2026-01-10', '2026-01-07')
		self.assertEqual(title, '1 overdue, 1 stuck in doing')
		self.assertEqual(body, '#1 late (due 2026-01-01)\n#2 stuck (doing, no update since 2026-01-01)')
		self.assertEqual(notify.digest([], '2026-01-10', '2026-01-07'), ('', ''))


	def test_once_retries_failed_push(self):
		'''A push that raises is logged, not recorded, so the next pass retries it.'''

		def boom():
			raise OSError('down')

		with self.assertLogs(level='WARNING'):
			notify.once('due', 'k', boom)
		calls = []
		notify.once('due', 'k', lambda: calls.append(1))
		notify.once('due', 'k', lambda: calls.append(1))
		self.assertEqual(calls, [1])


	def test_sent_purge(self):
		'''Old sent-push records are forgotten, recent ones kept.'''

		notify.once('due', 'old', lambda: None)
		notify.once('due', 'new', lambda: None)
		with kanban.connect() as db:
			db.execute("UPDATE sent SET at = '2000-01-01T00:00:00+00:00' WHERE key = 'old'")
		notify.sent_purge()
		with kanban.connect() as db:
			self.assertEqual([r[0] for r in db.execute('SELECT key FROM sent')], ['new'])


	def test_agent_done_toggle(self):
		'''The agent-done push follows its setting.'''

		t = self.task()
		notify.agent_done(kanban.task(t['id']), 'claude')
		notify.save({'notify_agent_done': '0'})
		notify.agent_done(kanban.task(t['id']), 'claude')
		self.assertEqual(self.titles(), [f'claude finished #{t["id"]}'])


	def test_agent_done_needs_topic(self):
		'''With no board topic set, the agent-done push is skipped.'''

		notify.save({'ntfy_topic': ''})
		notify.agent_done(kanban.task(self.task()['id']), 'claude')
		self.assertEqual(self.titles(), [])


class TestTaskEvents(Base):
	'''Inbox fan-out for task changes.'''

	def setUp(self):
		'''Ann (admin) edits; Bob can see the project.'''

		super().setUp()
		self.ann, self.bob = self.user('ann', admin=True), self.user('bob')
		accounts.user_update(self.bob, {'ntfy_topic': 'bob-topic'})
		self.act(self.ann)
		self.t = self.task('Shared', 'x')
		accounts.share_add(self.t['project_id'], 'editor', 'bob')


	def inbox(self) -> list:
		'''Bob's notification texts, oldest first.'''

		return [n['text'] for n in reversed(accounts.notifications(self.bob)['notifications'])]


	def test_assign_mention_activity(self):
		'''Assigning, mentioning and commenting each notify once and push to the user topic.'''

		tag    = f'#{self.t["id"]}'
		before = kanban.task(self.t['id'])
		after  = kanban.task_update(self.t['id'], {'assignees': ['bob'], 'description': 'ping @bob'})
		notify.task_event(before, after)
		self.assertEqual(self.inbox(), [f'ann assigned you {tag}'])
		notify.task_event(after, kanban.comment_add(self.t['id'], 'ann', 'hey @bob'), 'hey @bob')
		self.assertEqual(self.inbox()[-1], f'ann mentioned you in {tag}')
		notify.task_event(after, kanban.comment_add(self.t['id'], 'ann', 'plain'), 'plain')
		self.assertEqual(self.inbox()[-1], f'ann commented on {tag}')
		moved = kanban.task_update(self.t['id'], {'status': 'doing'})
		notify.task_event(after, moved)
		self.assertEqual(self.inbox()[-1], f'ann moved {tag} to doing')
		self.assertEqual({p['topic'] for p in self.pushes}, {'bob-topic'})


	def test_existing_mention_not_repeated(self):
		'''Editing a description that already mentioned someone does not notify again.'''

		before = kanban.task_update(self.t['id'], {'description': '@bob'})
		after  = kanban.task_update(self.t['id'], {'description': '@bob more'})
		notify.task_event(before, after)
		self.assertEqual(self.inbox(), [])


	def test_push_failure_keeps_inbox(self):
		'''A failed ntfy push still leaves the inbox notification.'''

		def boom(*args, **kwargs):
			raise OSError('down')

		notify.send = boom
		before = kanban.task(self.t['id'])
		with self.assertLogs(level='WARNING'):
			notify.task_event(before, kanban.task_update(self.t['id'], {'assignees': ['bob']}))
		self.assertEqual(len(self.inbox()), 1)


	def test_no_access_no_notification(self):
		'''Users who cannot see the task are never notified.'''

		self.user('cat')
		before = kanban.task(self.t['id'])
		notify.task_event(before, kanban.task_update(self.t['id'], {'description': '@cat'}))
		cat = next(u for u in accounts.users() if u['username'] == 'cat')
		with kanban.connect() as db:
			self.assertEqual(db.execute('SELECT COUNT(*) FROM notifications WHERE user_id = ?', (cat['id'],)).fetchone()[0], 0)


	def test_self_prefs_and_access(self):
		'''No notifications for your own changes, turned-off kinds, or tasks you cannot see.'''

		self.act(self.bob)
		before = kanban.task(self.t['id'])
		notify.task_event(before, kanban.task_update(self.t['id'], {'assignees': ['bob']}))
		self.assertEqual(self.inbox(), [])
		self.act(self.ann)
		accounts.user_update(self.bob, {'notify_mention': False})
		before = kanban.task(self.t['id'])
		notify.task_event(before, kanban.task_update(self.t['id'], {'description': '@bob'}))
		self.assertEqual(self.inbox(), [])
		self.act(None)
		notify.task_event(None, kanban.task(self.t['id']))
		self.assertEqual(self.inbox(), [])



class TestWebhooks(Base):
	'''Team webhooks for changes on shared projects.'''

	def setUp(self):
		'''Ann shares Work with team crew, which has a webhook; Ann acts and posts are recorded.'''

		super().setUp()
		self.ann            = self.user('ann')
		self.hits           = []
		self.old_post       = notify.webhook_post
		notify.webhook_post = lambda urls, payload: self.hits.append((urls, payload))
		self.act(self.ann)
		accounts.team_create(self.ann, 'crew')
		team = accounts.teams(self.ann)[0]['id']
		real, notify.webhook_public = notify.webhook_public, lambda host: True # no DNS in tests
		accounts.team_webhook(self.ann, team, 'https://chat.example.com/hook')
		notify.webhook_public = real
		self.nt = kanban.project_save('Work')['id']
		accounts.share_add(self.nt, 'editor', team='crew')


	def tearDown(self):
		'''Restore the real poster.'''

		notify.webhook_post = self.old_post
		super().tearDown()


	def events(self) -> list:
		'''Event names posted so far.'''

		return [payload['event'] for urls, payload in self.hits]


	def test_lifecycle(self):
		'''Create, field changes, comments and deletes on a sub-project all post, with the changed fields.'''

		t = self.task('Work/api', 'scan')
		notify.task_event(None, t)
		before = kanban.task(t['id'])
		notify.task_event(before, kanban.task_update(t['id'], {'status': 'doing', 'description': 'plan'}))
		task = kanban.comment_add(t['id'], 'ann', 'looks good')
		notify.task_event(task, task, 'looks good')
		self.assertEqual(self.events(), ['created', 'updated', 'commented'])
		urls, payload = self.hits[1]
		self.assertEqual(urls, ['https://chat.example.com/hook'])
		self.assertEqual((payload['actor'], payload['task']['project'], payload['task']['status']), ('ann', 'Work/api', 'doing'))
		self.assertEqual(payload['changes'], [{'field': 'status', 'old': 'todo', 'new': 'doing'}, {'field': 'description', 'old': None, 'new': None}])
		self.assertTrue(payload['task']['url'].endswith(f'/#/task/{t["id"]}'))
		self.assertEqual(self.hits[2][1]['comment'], 'looks good')
		notify.webhook('deleted', None, kanban.task(t['id']))
		self.assertEqual(self.events()[-1], 'deleted')


	def test_post_pins_address_and_moves_key(self):
		'''The real poster connects to the address webhook_address checked without looking the host up again, keeps the hostname in the Host header, and sends key= as a Bearer header.'''

		got = queue.Queue()

		class Handler(http.server.BaseHTTPRequestHandler):
			def do_POST(self):
				got.put((self.path, self.headers.get('Host'), self.headers.get('Authorization'), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
				self.send_response(200)
				self.end_headers()

			def log_message(self, *args):
				pass

		server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
		threading.Thread(target=server.handle_request, daemon=True).start()
		lookups  = []
		statuses = queue.Queue()
		real     = (notify.webhook_address, notify.webhook_status)
		notify.webhook_address = lambda host: lookups.append(host) or '127.0.0.1' # hook.test never resolves, so the send must use this pinned address
		notify.webhook_status  = lambda url, error: statuses.put(error)
		try:
			self.old_post([f'http://hook.test:{server.server_port}/hook?channel=dev&key=secret123&topic=kanopy'], {'event': 'created', 'task': {'id': 1}})
			path, host, auth, body = got.get(timeout=5)
			self.assertEqual(statuses.get(timeout=5), '') # the sender is done before this test's database goes away
		finally:
			notify.webhook_address, notify.webhook_status = real
			server.server_close()
		self.assertEqual(lookups, ['hook.test'])
		self.assertEqual((path, host, auth, body['event']), ('/hook?channel=dev&topic=kanopy', f'hook.test:{server.server_port}', 'Bearer secret123', 'created'))


	def test_public_only_and_no_redirects(self):
		'''Loopback, LAN, tailnet, link-local and metadata addresses are refused, and a redirect is never followed.'''

		for host in ('127.0.0.1', '10.0.0.5', '192.168.1.1', '100.64.0.1', '169.254.169.254', '::1', 'fd00::1'):
			self.assertFalse(notify.webhook_public(host), host)
		self.assertTrue(notify.webhook_public('1.1.1.1'))
		with self.assertRaises(ValueError):
			notify.webhook_send('http://127.0.0.1:9/hook', {'event': 'created'})

		class Redirect(http.server.BaseHTTPRequestHandler):
			def do_POST(self):
				self.send_response(302)
				self.send_header('Location', 'http://example.com/steal')
				self.end_headers()

			def log_message(self, *args):
				pass

		server = http.server.HTTPServer(('127.0.0.1', 0), Redirect)
		threading.Thread(target=server.handle_request, daemon=True).start()
		real, notify.webhook_address = notify.webhook_address, lambda host: '127.0.0.1'
		try:
			with self.assertRaises(OSError):
				notify.webhook_send(f'http://hook.test:{server.server_port}/hook?key=secret123', {'event': 'created'})
		finally:
			notify.webhook_address = real
			server.server_close()


	def test_failure_shown_until_success(self):
		'''A failed send is recorded on the team for its owner; a later success or a new URL clears it.'''

		team = accounts.teams(self.ann)[0]['id']
		notify.webhook_status('https://chat.example.com/hook', 'HTTP 401 from chat.example.com')
		mine = accounts.teams(self.ann)[0]
		self.assertEqual(mine['webhook_error'], 'HTTP 401 from chat.example.com')
		self.assertIsNotNone(mine['webhook_failed'])
		notify.webhook_status('https://chat.example.com/hook', '')
		self.assertEqual(accounts.teams(self.ann)[0]['webhook_error'], '')
		notify.webhook_status('https://chat.example.com/hook', 'down')
		accounts.team_webhook(self.ann, team, '')
		self.assertEqual((accounts.teams(self.ann)[0]['webhook_error'], accounts.teams(self.ann)[0]['webhook_failed']), ('', None))


	def test_queue_drops_oldest_when_full(self):
		'''Past the queue limit the oldest post is dropped, with a warning, so a dead receiver cannot grow memory forever.'''

		class Alive:
			def is_alive(self):
				return True

		old = (notify.HOOK_QUEUE, notify.HOOK_SENDER)
		notify.HOOK_QUEUE, notify.HOOK_SENDER = queue.Queue(maxsize=2), Alive() # no sender drains it
		try:
			with self.assertLogs(level='WARNING'):
				for n in range(3):
					self.old_post(['https://chat.example.com/hook'], {'event': f'e{n}'})
			self.assertEqual([notify.HOOK_QUEUE.get_nowait()[1]['event'] for _ in range(2)], ['e1', 'e2'])
		finally:
			notify.HOOK_QUEUE, notify.HOOK_SENDER = old


	def test_mixed_answers_refused(self):
		'''A host is refused if any address it resolves to is private, even when another one is public.'''

		real = notify.socket.getaddrinfo
		notify.socket.getaddrinfo = lambda host, port, **kwargs: [(None, None, None, '', ('1.1.1.1', 0)), (None, None, None, '', ('127.0.0.1', 0))]
		try:
			with self.assertRaises(ValueError):
				notify.webhook_address('rebind.test')
		finally:
			notify.socket.getaddrinfo = real


	def test_bulk_is_one_summary(self):
		'''A bulk change posts one message per webhook listing the changed tasks, and skips tasks with no tracked change.'''

		a, b = self.task('Work', 'one'), self.task('Work/api', 'two')
		before = {t['id']: kanban.task(t['id']) for t in (a, b)}
		kanban.bulk([a['id'], b['id']], {'status': 'done'})
		notify.webhook_bulk('updated', before, {t: kanban.task(t) for t in before})
		self.assertEqual(len(self.hits), 1)
		urls, payload = self.hits[0]
		self.assertEqual((payload['event'], payload['action'], payload['fields'], [t['id'] for t in payload['tasks']]), ('bulk', 'updated', ['status'], [a['id'], b['id']]))
		notify.webhook_bulk('updated', {a['id']: kanban.task(a['id'])}, {a['id']: kanban.task(a['id'])})
		self.assertEqual(len(self.hits), 1)
		notify.webhook_bulk('deleted', before, {})
		self.assertEqual(self.hits[-1][1]['action'], 'deleted')


	def test_webhook_tasks(self):
		'''Only tasks under a project a team webhook covers need snapshots, and none when no team has a webhook.'''

		a, b = self.task('Work', 'one'), self.task('Elsewhere', 'two')
		self.assertEqual(notify.webhook_tasks([a['id'], b['id']]), [a['id']])
		with kanban.connect() as db:
			db.execute("UPDATE teams SET webhook = ''")
		self.assertEqual(notify.webhook_tasks([a['id'], b['id']]), [])


	def test_skipped(self):
		'''Unshared projects, changes with no tracked field, system changes and teams without a URL post nothing.'''

		other = self.task('Home', 'dishes')
		notify.task_event(None, other)
		t      = self.task('Work', 'scan')
		before = kanban.task(t['id'])
		notify.task_event(before, kanban.task(t['id']))
		self.act(None)
		notify.task_event(None, t)
		self.act(self.ann)
		accounts.team_webhook(self.ann, accounts.teams(self.ann)[0]['id'], '')
		notify.task_event(None, t)
		self.assertEqual(self.hits, [])



if __name__ == '__main__':
	unittest.main()
