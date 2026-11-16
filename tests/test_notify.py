#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# tests/test_notify.py

import json
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
		finally:
			notify.send, notify.urllib.request.urlopen = real, old_open
		req, timeout = sent[0]
		body = json.loads(req.data)
		self.assertEqual((req.full_url, req.get_method(), timeout), ('https://push.example', 'POST', 10))
		self.assertEqual(req.get_header('User-agent'), notify.USER_AGENT)
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
		'''A push that raises is not recorded, so the next pass retries it.'''

		def boom():
			raise OSError('down')

		with self.assertRaises(OSError):
			notify.once('due', 'k', boom)
		calls = []
		notify.once('due', 'k', lambda: calls.append(1))
		notify.once('due', 'k', lambda: calls.append(1))
		self.assertEqual(calls, [1])


	def test_agent_done_toggle(self):
		'''The agent-done push follows its setting.'''

		t = self.task()
		notify.agent_done(kanban.task(t['id']), 'claude')
		notify.save({'notify_agent_done': '0'})
		notify.agent_done(kanban.task(t['id']), 'claude')
		self.assertEqual(self.titles(), [f'claude finished #{t["id"]}'])


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



if __name__ == '__main__':
	unittest.main()
