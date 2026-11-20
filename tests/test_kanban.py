#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# tests/test_kanban.py

import unittest

from datetime import date, datetime, timedelta, timezone

from kanopy import accounts
from kanopy import kanban

from .base import Base, POSTGRES


class TestAttachments(Base):
	'''File attachments.'''

	def test_add_get_delete(self):
		'''An attachment round-trips its bytes and is logged in history.'''

		t = self.task()
		t = kanban.attachment_add(t['id'], 'notes.txt', 'text/plain', b'hello')
		row = kanban.attachment_get(t['attachments'][0]['id'])
		self.assertEqual(bytes(row['data']), b'hello')
		self.assertEqual(row['mime'], 'text/plain')
		t = kanban.attachment_delete(row['id'])
		self.assertEqual(t['attachments'], [])
		self.assertEqual([h['field'] for h in t['history']][:2], ['attachment', 'attachment'])


	def test_unsafe_mime_is_neutered(self):
		'''HTML, SVG and other active types are stored as application/octet-stream.'''

		t = self.task()
		for mime in ('text/html', 'image/svg+xml', 'application/javascript'):
			t = kanban.attachment_add(t['id'], 'x', mime, b'<script>')
		self.assertEqual({a['mime'] for a in t['attachments']}, {'application/octet-stream'})


	def test_name_is_stripped_to_basename(self):
		'''A path in the file name is reduced to its last part.'''

		t = kanban.attachment_add(self.task()['id'], '../../etc/passwd', 'text/plain', b'x')
		self.assertEqual(t['attachments'][0]['name'], 'passwd')


	def test_size_limit(self):
		'''Files over MAX_FILE are refused.'''

		old, kanban.MAX_FILE = kanban.MAX_FILE, 4
		try:
			with self.assertRaises(ValueError):
				kanban.attachment_add(self.task()['id'], 'big', 'text/plain', b'12345')
		finally:
			kanban.MAX_FILE = old


class TestBackup(Base):
	'''Daily database backups.'''

	@unittest.skipIf(POSTGRES, 'PostgreSQL is backed up with its own tools')
	def test_backup_written_and_pruned(self):
		'''Today's backup is written once and only the newest KEEP are kept.'''

		kanban.BACKUPS.mkdir()
		for day in range(1, 5):
			(kanban.BACKUPS / f'kanban-2000-01-0{day}.db').write_bytes(b'')
		(kanban.BACKUPS / 'kanban-2000-01-01.db-wal').write_bytes(b'')
		kanban.backup()
		names = sorted(p.name for p in kanban.BACKUPS.iterdir())
		self.assertEqual(len(names), kanban.KEEP)
		self.assertIn(f'kanban-{datetime.now().strftime("%Y-%m-%d")}.db', names)
		self.assertFalse(any('-wal' in n for n in names))


class TestBulkAndReorder(Base):
	'''Bulk changes and column ordering.'''

	def test_bulk_status_labels_delete(self):
		'''Bulk moves, labels and trashes many tasks in one go.'''

		a, b = self.task(title='a'), self.task(title='b')
		self.assertEqual(kanban.bulk([a['id'], b['id']], {'status': 'doing', 'add_label': 'host:pi'}), 2)
		self.assertEqual({kanban.task(i)['status'] for i in (a['id'], b['id'])}, {'doing'})
		self.assertEqual(kanban.task(a['id'])['labels'], ['host:pi'])
		kanban.bulk([a['id']], {'remove_label': 'host:pi'})
		self.assertEqual(kanban.task(a['id'])['labels'], [])
		kanban.bulk([a['id'], b['id']], {'delete': True})
		self.assertEqual(len(kanban.trash_list()), 2)
		with self.assertRaises(ValueError):
			kanban.bulk([], {'status': 'done'})


	def test_done_limit(self):
		'''done_limit keeps every open task and only the newest finished ones.'''

		open_ = self.task(title='open')
		done  = [self.task(title=f'd{i}') for i in range(3)]
		for i, t in enumerate(done):
			kanban.task_update(t['id'], {'status': 'done'})
			with kanban.connect() as db:
				db.execute('UPDATE tasks SET done_at = ? WHERE id = ?', (f'2030-01-0{i + 1}', t['id']))
		got = {t['id'] for t in kanban.tasks(done_limit=2)}
		self.assertEqual(got, {open_['id'], done[2]['id'], done[1]['id']})
		self.assertEqual(len(kanban.tasks(done_limit=0)), 1)


	def test_reorder(self):
		'''Reorder sets column order and moves cards into the column.'''

		a, b, c = (self.task(title=n) for n in 'abc')
		kanban.reorder('doing', [c['id'], a['id']])
		doing = [t['id'] for t in kanban.tasks() if t['status'] == 'doing']
		self.assertEqual(doing, [c['id'], a['id']])
		self.assertEqual(kanban.task(b['id'])['status'], 'todo')
		with self.assertRaises(ValueError):
			kanban.reorder('nope', [a['id']])


	def test_reorder_within_column_keeps_hidden_cards(self):
		'''Cards not in the visible list (filtered out, other projects) keep their slot.'''

		a, b, c = (self.task(title=n, status='doing') for n in 'abc')
		kanban.reorder('doing', [c['id'], a['id']])
		self.assertEqual([t['id'] for t in kanban.tasks() if t['status'] == 'doing'], [c['id'], b['id'], a['id']])


	def test_reorder_around_view_only_cards(self):
		'''View-only cards in a column stay put instead of blocking the reorder, and cannot be dragged to another column.'''

		ann, bob = self.user('ann'), self.user('bob')
		self.act(ann)
		v = self.task('Seen', 'v')
		a, b = self.task('Shared', 'a'), self.task('Shared', 'b')
		accounts.share_add(v['project_id'], 'viewer', 'bob')
		accounts.share_add(a['project_id'], 'editor', 'bob')
		self.act(bob)
		kanban.reorder('todo', [b['id'], v['id'], a['id']])
		self.assertEqual([t['id'] for t in kanban.tasks() if t['status'] == 'todo'], [v['id'], b['id'], a['id']])
		with self.assertRaises(PermissionError):
			kanban.reorder('doing', [v['id']])


	def test_reorder_into_filled_column(self):
		'''A card dropped at the top of a column that already has cards stays at the top.'''

		a, b = (self.task(title=n, status='doing') for n in 'ab')
		c = self.task(title='c')
		kanban.reorder('doing', [c['id'], a['id'], b['id']])
		self.assertEqual([t['id'] for t in kanban.tasks() if t['status'] == 'doing'], [c['id'], a['id'], b['id']])


class TestHistory(Base):
	'''Task history and project activity.'''

	def test_changes_are_recorded_with_actor_and_agent(self):
		'''Each field change is an event naming the user and the MCP agent.'''

		u = self.user('ann', admin=True)
		self.act(u)
		kanban.AGENT.set('claude')
		t = self.task()
		kanban.task_update(t['id'], {'title': 'renamed', 'priority': 3, 'due': '2030-01-02', 'status': 'doing'})
		fields = {h['field'] for h in kanban.task(t['id'])['history']}
		self.assertTrue({'created', 'title', 'priority', 'due', 'status'} <= fields)
		self.assertEqual({(h['actor'], h['agent']) for h in kanban.task(t['id'])['history']}, {('ann', 'claude')})


	def test_unchanged_values_are_not_recorded(self):
		'''Saving a field with its current value adds no event.'''

		t = self.task(priority=2)
		before = len(kanban.task(t['id'])['history'])
		kanban.task_update(t['id'], {'priority': 2, 'title': 'task'})
		self.assertEqual(len(kanban.task(t['id'])['history']), before)


	def test_description_versions(self):
		'''Each description change keeps the text it replaced, for the newest VERSIONS changes only.'''

		old, kanban.VERSIONS = kanban.VERSIONS, 2
		try:
			t = self.task(description='v0')
			for v in range(1, 5):
				kanban.task_update(t['id'], {'description': f'v{v}'})
			kanban.task_update(t['id'], {'title': 'renamed'})
			olds = [h['old'] for h in kanban.task(t['id'])['history'] if h['field'] == 'description']
			self.assertEqual(olds, ['v3', 'v2', None, None])
			self.assertTrue(all(h['id'] for h in kanban.task(t['id'])['history']))
		finally:
			kanban.VERSIONS = old


	def test_history_is_capped(self):
		'''Only the newest HISTORY events are kept per task.'''

		old, kanban.HISTORY = kanban.HISTORY, 3
		try:
			t = self.task()
			for p in range(1, 6):
				kanban.task_update(t['id'], {'priority': p})
			hist = kanban.task(t['id'])['history']
			self.assertEqual(len(hist), 3)
			self.assertEqual(hist[0]['new'], '5')
		finally:
			kanban.HISTORY = old


	def test_activity_covers_subprojects_and_pages(self):
		'''Project activity includes sub-projects and pages with before.'''

		a = self.task('Work', 'a')
		b = self.task('Work/sub', 'b')
		self.task('Other', 'c')
		pid    = next(p['id'] for p in kanban.projects() if p['path'] == 'Work')
		events = kanban.activity(pid)
		self.assertEqual({e['task_id'] for e in events}, {a['id'], b['id']})
		older = kanban.activity(pid, before=events[0]['id'])
		self.assertEqual(len(older), len(events) - 1)


class TestLabels(Base):
	'''Global and personal labels.'''

	def test_new_label_on_task_is_personal(self):
		'''A label typed onto a task by a user becomes that user's personal label.'''

		ann, bob = self.user('ann'), self.user('bob')
		self.act(ann)
		self.task(labels=['host:pi'])
		self.assertEqual([(l['name'], l['global']) for l in kanban.labels() if l['name'] == 'host:pi'], [('host:pi', False)])
		self.act(bob)
		self.assertNotIn('host:pi', [l['name'] for l in kanban.labels()])


	def test_global_label_absorbs_personal(self):
		'''Creating a global label folds same-named personal labels into it.'''

		ann = self.user('ann')
		self.act(ann)
		t = self.task(labels=['host:pi'])
		self.act(None)
		kanban.label_create('host:pi', glob=True)
		self.act(ann)
		self.assertEqual([(l['name'], l['global'], l['count']) for l in kanban.labels() if l['name'] == 'host:pi'], [('host:pi', True, 1)])
		self.assertEqual(kanban.task(t['id'])['labels'], ['host:pi'])


	def test_global_needs_admin(self):
		'''Only admins create, rename or delete global labels.'''

		self.act(self.user('ann'))
		with self.assertRaises(PermissionError):
			kanban.label_create('type:new', glob=True)
		with self.assertRaises(PermissionError):
			kanban.label_delete('type:bug')
		with self.assertRaises(PermissionError):
			kanban.label_update('type:bug', color='#000000')


	def test_rename_merges_and_recolor(self):
		'''Renaming onto an existing label merges the tasks; colors must be #rrggbb.'''

		t1 = self.task(labels=['host:a'])
		t2 = self.task(labels=['host:b'])
		kanban.label_update('host:a', new_name='host:b')
		self.assertEqual(kanban.task(t1['id'])['labels'], ['host:b'])
		self.assertEqual(kanban.task(t2['id'])['labels'], ['host:b'])
		kanban.label_update('host:b', color='#ABCDEF')
		self.assertEqual(next(l['color'] for l in kanban.labels() if l['name'] == 'host:b'), '#abcdef')
		with self.assertRaises(ValueError):
			kanban.label_update('host:b', color='red')


	def test_personal_rename_and_errors(self):
		'''A personal label renames in place; unknown labels and bad colors are refused.'''

		self.act(self.user('ann'))
		t = self.task(labels=['host:old'])
		kanban.label_update('host:old', new_name='host:new')
		self.assertEqual(kanban.task(t['id'])['labels'], ['host:new'])
		with self.assertRaises(LookupError):
			kanban.label_update('host:ghost', color='#000000')
		with self.assertRaises(ValueError):
			kanban.label_create('host:x', color='nope')


	def test_delete_removes_from_tasks(self):
		'''Deleting a label removes it from every task.'''

		t = self.task(labels=['host:pi'])
		kanban.label_delete('host:pi')
		self.assertEqual(kanban.task(t['id'])['labels'], [])


	def test_invalid_names(self):
		'''Labels must look like namespace:value.'''

		for bad in ('nocolon', 'Host:PI!', ':x', 'host:'):
			with self.assertRaises(ValueError):
				kanban.valid_label(bad)
		self.assertEqual(kanban.valid_label(' Host:Pi '), 'host:pi')


	def test_duplicate_create(self):
		'''Creating an existing label fails.'''

		with self.assertRaises(ValueError):
			kanban.label_create('type:bug')


	def test_fresh_install_types(self):
		'''A fresh database has the default type: labels as globals.'''

		names = {l['name'] for l in kanban.labels() if l['global']}
		self.assertEqual(names, {'type:' + t for t in kanban.TYPES})


class TestLinks(Base):
	'''Task relations.'''

	def test_link_both_directions(self):
		'''A link is stored with its inverse on the other task, and removed from both.'''

		a, b = self.task(title='a'), self.task(title='b')
		kanban.link_add(a['id'], 'blocked', b['id'])
		self.assertEqual([(l['kind'], l['id']) for l in kanban.task(a['id'])['links']], [('blocked', b['id'])])
		self.assertEqual([(l['kind'], l['id']) for l in kanban.task(b['id'])['links']], [('blocking', a['id'])])
		kanban.link_remove(b['id'], 'blocking', a['id'])
		self.assertEqual(kanban.task(a['id'])['links'], [])
		self.assertEqual(kanban.task(b['id'])['links'], [])


	def test_every_kind_has_an_inverse(self):
		'''INVERSE is symmetric.'''

		for kind, inverse in kanban.INVERSE.items():
			self.assertEqual(kanban.INVERSE[inverse], kind)


	def test_bad_links(self):
		'''Self links and unknown kinds are refused.'''

		a, b = self.task(), self.task()
		with self.assertRaises(ValueError):
			kanban.link_add(a['id'], 'blocked', a['id'])
		with self.assertRaises(ValueError):
			kanban.link_add(a['id'], 'likes', b['id'])


	def test_hidden_links(self):
		'''Links to tasks in projects the user cannot see are hidden.'''

		ann, bob = self.user('ann'), self.user('bob')
		self.act(ann)
		mine = self.task('Ann', 'mine')
		self.act(bob)
		theirs = self.task('Bob', 'theirs')
		self.act(None)
		kanban.link_add(theirs['id'], 'related', mine['id'])
		self.act(bob)
		self.assertEqual(kanban.task(theirs['id'])['links'], [])


class TestPermissions(Base):
	'''Ownership, shares, teams and roles.'''

	def setUp(self):
		'''Ann owns Ann/sub with a task; Bob starts with no access.'''

		super().setUp()
		self.ann, self.bob = self.user('ann'), self.user('bob')
		self.act(self.ann)
		self.t   = self.task('Ann/sub', 'secret')
		self.pid = next(p['id'] for p in kanban.projects() if p['path'] == 'Ann')
		self.sub = next(p['id'] for p in kanban.projects() if p['path'] == 'Ann/sub')


	def test_no_access(self):
		'''A user sees nothing of a project not shared with them.'''

		self.act(self.bob)
		self.assertEqual(kanban.projects(), [])
		self.assertEqual(kanban.tasks(), [])
		self.assertEqual(kanban.search('secret'), [])
		with self.assertRaises(PermissionError):
			kanban.task(self.t['id'])
		with self.assertRaises(PermissionError):
			kanban.tasks(self.pid)


	def test_viewer(self):
		'''A viewer reads but cannot change tasks.'''

		accounts.share_add(self.pid, 'viewer', 'bob')
		self.act(self.bob)
		self.assertEqual(kanban.task(self.t['id'])['title'], 'secret')
		for write in (lambda: kanban.task_update(self.t['id'], {'title': 'x'}), lambda: kanban.comment_add(self.t['id'], 'bob', 'hi'), lambda: kanban.task_delete(self.t['id']), lambda: kanban.task_create({'project_id': self.sub, 'title': 'x'})):
			with self.assertRaises(PermissionError):
				write()


	def test_share_inherits_to_subprojects(self):
		'''A share on a parent covers its sub-projects.'''

		accounts.share_add(self.pid, 'editor', 'bob')
		self.act(self.bob)
		self.assertEqual(kanban.task_update(self.t['id'], {'title': 'edited'})['title'], 'edited')
		self.assertEqual(kanban.levels(kanban.connect())[self.sub], kanban.EDIT)


	def test_editor_cannot_manage(self):
		'''An editor cannot rename, share or delete the project.'''

		accounts.share_add(self.pid, 'editor', 'bob')
		self.act(self.bob)
		with self.assertRaises(PermissionError):
			kanban.project_save('Renamed', self.pid)
		with self.assertRaises(PermissionError):
			accounts.share_add(self.pid, 'viewer', 'ann')


	def test_manager_cannot_delete(self):
		'''Managers edit and share, but only the owner deletes.'''

		accounts.share_add(self.pid, 'manager', 'bob')
		self.act(self.bob)
		kanban.project_save('Ann', self.pid, description='by bob')
		self.act(self.ann)
		empty = kanban.project_save('Ann/empty')['id']
		self.act(self.bob)
		with self.assertRaises(PermissionError):
			kanban.project_delete(empty)


	def test_team_share(self):
		'''Sharing with a team gives its members access.'''

		accounts.team_create(self.ann, 'crew')
		team = accounts.teams(self.ann)[0]['id']
		accounts.team_member_add(self.ann, team, 'bob')
		accounts.share_add(self.pid, 'viewer', team='crew')
		self.assertEqual(accounts.teams(self.ann)[0]['projects'], [self.pid])
		self.act(self.bob)
		self.assertEqual(kanban.task(self.t['id'])['title'], 'secret')


	def test_tree_shows_parent_without_access(self):
		'''Sharing only a sub-project shows its parent in the tree with role 0 and no counts.'''

		accounts.share_add(self.sub, 'viewer', 'bob')
		self.act(self.bob)
		rows = {p['path']: p for p in kanban.projects()}
		self.assertEqual(rows['Ann']['role'], 0)
		self.assertEqual(rows['Ann']['open'], 0)
		self.assertEqual(rows['Ann/sub']['role'], kanban.VIEW)


	def test_assignee_needs_access(self):
		'''Assigning someone without access to the project is refused.'''

		with self.assertRaises(ValueError):
			kanban.task_update(self.t['id'], {'assignees': ['bob']})
		with self.assertRaises(ValueError):
			kanban.task_update(self.t['id'], {'assignees': ['nobody']})
		accounts.share_add(self.pid, 'viewer', 'bob')
		self.assertEqual(kanban.task_update(self.t['id'], {'assignees': ['@Bob']})['assignees'], ['bob'])


	def test_admin_sees_everything(self):
		'''Admins and the system see every project.'''

		self.act(self.user('root', admin=True))
		self.assertIn('Ann/sub', [p['path'] for p in kanban.projects()])
		self.act(None)
		self.assertIsNone(kanban.levels(kanban.connect()))


class TestProjects(Base):
	'''Project tree.'''

	def test_create_path_makes_parents(self):
		'''A slash path creates any missing parents.'''

		kanban.project_save('A/B/C')
		self.assertEqual([p['path'] for p in kanban.projects() if p['path'].startswith('A')], ['A', 'A/B', 'A/B/C'])


	def test_rename_and_move(self):
		'''Saving a new path renames and re-parents a project, keeping its children.'''

		pid = kanban.project_save('A/B')['id']
		kanban.project_save('A/B/C')
		kanban.project_save('X/Y', pid)
		paths = {p['path'] for p in kanban.projects()}
		self.assertTrue({'X/Y', 'X/Y/C'} <= paths)
		self.assertNotIn('A/B', paths)


	def test_cannot_move_into_itself(self):
		'''A project cannot become its own descendant.'''

		pid = kanban.project_save('A')['id']
		kanban.project_save('A/B')
		with self.assertRaises(ValueError):
			kanban.project_save('A/B/A', pid)
		child = next(p['id'] for p in kanban.projects() if p['path'] == 'A/B')
		with self.assertRaises(ValueError):
			kanban.project_order(child, [pid])


	def test_path_clash(self):
		'''Two projects cannot share a path.'''

		kanban.project_save('A')
		with self.assertRaises(ValueError):
			kanban.project_save('A')
		pid = kanban.project_save('B')['id']
		with self.assertRaises(ValueError):
			kanban.project_save('A', pid)


	def test_fields(self):
		'''Description, archived, color and folder are stored and validated.'''

		pid = kanban.project_save('A', description='  desc  ', folder='~/code/a/')['id']
		kanban.project_save(None, pid, archived=True, color='#AABBCC')
		row = next(p for p in kanban.projects() if p['id'] == pid)
		self.assertEqual((row['description'], row['folder'], row['archived'], row['color']), ('desc', '~/code/a', 1, '#aabbcc'))
		with self.assertRaises(ValueError):
			kanban.project_save(None, pid, folder='relative/path')
		with self.assertRaises(ValueError):
			kanban.project_save(None, pid, color='blue')
		with self.assertRaises(ValueError):
			kanban.project_save('')


	def test_delete_rules(self):
		'''Only an empty project with no sub-projects can be deleted.'''

		self.task('A', 'x')
		kanban.project_save('B/C')
		a = next(p['id'] for p in kanban.projects() if p['path'] == 'A')
		b = next(p['id'] for p in kanban.projects() if p['path'] == 'B')
		with self.assertRaises(ValueError):
			kanban.project_delete(a)
		with self.assertRaises(ValueError):
			kanban.project_delete(b)
		c = next(p['id'] for p in kanban.projects() if p['path'] == 'B/C')
		kanban.project_delete(c)
		self.assertNotIn('B/C', [p['path'] for p in kanban.projects()])


	def test_order(self):
		'''project_order sets sibling order and moves projects under a parent.'''

		a, b = kanban.project_save('A')['id'], kanban.project_save('B')['id']
		p = kanban.project_save('P')['id']
		kanban.project_order(p, [b, a])
		self.assertEqual([r['path'] for r in kanban.projects() if r['path'].startswith('P/')], ['P/B', 'P/A'])


	def test_order_rules(self):
		'''Moving needs manager rights and a free name under the new parent.'''

		kanban.project_save('P/A')
		other = kanban.project_save('A')['id']
		p     = next(r['id'] for r in kanban.projects() if r['path'] == 'P')
		with self.assertRaises(ValueError):
			kanban.project_order(p, [other])
		ann = self.user('ann')
		self.act(ann)
		mine = kanban.project_save('Mine')['id']
		with self.assertRaises(PermissionError):
			kanban.project_order(mine, [other])
		kanban.project_order(None, [mine, other]) # projects she cannot manage keep their place


	def test_open_counts(self):
		'''open counts todo and doing tasks that are not archived.'''

		self.task('A', 'one')
		self.task('A', 'two', status='doing')
		self.task('A', 'three', status='done')
		self.assertEqual(next(p['open'] for p in kanban.projects() if p['path'] == 'A'), 2)


class TestRepeat(Base):
	'''Repeating tasks.'''

	def test_valid_repeat(self):
		'''Repeat rules normalize to a canonical form.'''

		cases = {'daily': 'daily', 'day': 'daily', 'every week': 'weekly', '1 month': 'monthly', 'every 2 weeks': 'every 2 weeks', '3 days': 'every 3 days', 'weekdays': 'mon,tue,wed,thu,fri', 'fri mon': 'mon,fri', 'Monday, wednesday': 'mon,wed', '': None}
		for text, want in cases.items():
			self.assertEqual(kanban.valid_repeat(text), want, text)
		for bad in ('0 days', '400 days', 'fortnightly', 'mo', 'every'):
			with self.assertRaises(ValueError, msg=bad):
				kanban.valid_repeat(bad)


	def test_next_month_end(self):
		'''Monthly from the 31st lands on the last day of short months and back on the 31st after.'''

		base = date(2026, 1, 31)
		self.assertEqual(kanban.repeat_next('monthly', base, base), date(2026, 2, 28))
		self.assertEqual(kanban.repeat_next('monthly', base, date(2026, 2, 28)), date(2026, 3, 31))
		self.assertEqual(kanban.repeat_next('yearly', date(2028, 2, 29), date(2028, 2, 29)), date(2029, 2, 28))


	def test_next_never_today_or_past(self):
		'''An overdue repeat skips to the first date after today.'''

		self.assertEqual(kanban.repeat_next('weekly', date(2026, 1, 1), date(2026, 1, 20)), date(2026, 1, 22))
		self.assertEqual(kanban.repeat_next('every 3 days', date(2026, 1, 1), date(2026, 1, 1)), date(2026, 1, 4))
		self.assertEqual(kanban.repeat_next('daily', date(2026, 1, 1), date(2026, 1, 5)), date(2026, 1, 6))


	def test_next_weekdays(self):
		'''Day lists pick the next listed weekday after today.'''

		friday = date(2026, 11, 20)
		self.assertEqual(friday.weekday(), 4)
		self.assertEqual(kanban.repeat_next('mon,tue,wed,thu,fri', friday, friday), date(2026, 11, 23))
		self.assertEqual(kanban.repeat_next('wed', friday, friday), date(2026, 11, 25))


	def test_done_spawns_next(self):
		'''Marking a repeating task done makes an unticked todo copy with moved dates, labels, assignees and a precedes link.'''

		ann = self.user('ann', admin=True)
		due = (date.today() - timedelta(days=1)).isoformat()
		t   = self.task(description='- [x] step\n- [ ] other', due=due, remind_at=due + 'T08:30', repeat='daily', labels=['host:pi'], assignees=['ann'])
		kanban.task_update(t['id'], {'status': 'done'})
		old = kanban.task(t['id'])
		self.assertIsNone(old['repeat'])
		nxt = next(l['id'] for l in old['links'] if l['kind'] == 'precedes')
		new = kanban.task(nxt)
		self.assertEqual((new['status'], new['repeat'], new['labels'], new['assignees']), ('todo', 'daily', ['host:pi'], ['ann']))
		self.assertEqual(new['description'], '- [ ] step\n- [ ] other')
		self.assertGreater(new['due'], date.today().isoformat() if new['due'] else '')
		self.assertEqual(new['remind_at'], new['due'] + 'T08:30')
		self.assertTrue(ann)


	def test_dates_stored_padded(self):
		'''Unpadded due and reminder dates are stored zero-padded, so a repeating task can still be completed.'''

		t = self.task(due='2027-1-5', remind_at='2027-1-5T9:5', repeat='weekly')
		got = kanban.task(t['id'])
		self.assertEqual((got['due'], got['remind_at']), ('2027-01-05', '2027-01-05T09:05'))
		kanban.task_update(t['id'], {'status': 'done'})
		self.assertEqual(kanban.task(t['id'])['status'], 'done')


	def test_monthly_repeat_keeps_its_day(self):
		'''A monthly task due on the 31st lands on the 28th in February and back on the 31st in March.'''

		t = self.task(title='rent', due='2027-01-31', repeat='monthly')
		dues = []
		for _ in range(2):
			kanban.task_update(t['id'], {'status': 'done'})
			t = next(x for x in kanban.tasks() if x['title'] == 'rent' and x['status'] == 'todo')
			dues.append(t['due'])
		self.assertEqual(dues, ['2027-02-28', '2027-03-31'])
		self.assertEqual(kanban.repeat_dates('2027-04-01', '2027-05-31')[t['id']], ['2027-04-30', '2027-05-31'])


	def test_overdue_repeat_projects_from_today(self):
		'''An overdue repeating task shows its upcoming copies only after today, not on the days it already missed.'''

		today = datetime.now(timezone.utc).date()
		t = self.task(title='water', due=(today - timedelta(days=10)).isoformat(), repeat='daily')
		got = kanban.repeat_dates((today - timedelta(days=15)).isoformat(), (today + timedelta(days=2)).isoformat())[t['id']]
		self.assertEqual(got, [(today + timedelta(days=1)).isoformat(), (today + timedelta(days=2)).isoformat()])
		self.assertEqual(kanban.repeat_next('daily', date(2000, 1, 1), today), today + timedelta(days=1)) # years overdue, still instant


	def test_repeat_dates(self):
		'''Calendar projections list upcoming dates inside the window only.'''

		start = date.today() + timedelta(days=1)
		t     = self.task(due=date.today().isoformat(), repeat='daily')
		dates = kanban.repeat_dates(start.isoformat(), (start + timedelta(days=2)).isoformat())
		self.assertEqual(len(dates[t['id']]), 3)
		with self.assertRaises(ValueError):
			kanban.repeat_dates('2026-01-01', '2026-06-01')
		with self.assertRaises(ValueError):
			kanban.repeat_dates('bad', '2026-01-01')
		with self.assertRaises(ValueError):
			kanban.repeat_dates('9999-12-01', '9999-12-31') # far-off windows are refused rather than computed
		later = date.today() + timedelta(days=4 * 365)
		self.assertEqual(len(kanban.repeat_dates(later.isoformat(), (later + timedelta(days=2)).isoformat())[t['id']]), 3)


class TestSearch(Base):
	'''Task search.'''

	def test_non_ascii_case(self):
		'''Search ignores case for non-English letters too.'''

		t = self.task(title='Ärger mit Öl')
		self.assertEqual([h['id'] for h in kanban.search('ärger')], [t['id']])
		self.assertEqual([h['id'] for h in kanban.search('ÖL')], [t['id']])


	def test_words_and_snippets(self):
		'''Every word must appear somewhere; the snippet shows where it matched.'''

		t = self.task(title='alpha', description='first line\nbeta gamma here')
		kanban.comment_add(t['id'], 'x', 'delta in a comment')
		self.task(title='alpha only')
		hits = kanban.search('ALPHA beta')
		self.assertEqual([h['id'] for h in hits], [t['id']])
		self.assertEqual(hits[0]['where'], 'title') # any word in the title shows the title
		hits = kanban.search('gamma BETA')
		self.assertEqual((hits[0]['where'], hits[0]['snippet']), ('description', 'beta gamma here'))
		self.assertEqual(kanban.search('delta')[0]['where'], 'comment')
		self.assertEqual(kanban.search('alpha', fields=['title'])[0]['snippet'], '')
		self.assertEqual(kanban.search('delta', fields=['title']), [])


	def test_sorts(self):
		'''Results sort by priority, due date, id or updated time, done last.'''

		a = self.task(title='x a', priority=1, due='2030-01-02')
		b = self.task(title='x b', priority=5)
		c = self.task(title='x c', due='2030-01-01', status='done')
		self.assertEqual([h['id'] for h in kanban.search('x', sort='priority')][0], b['id'])
		self.assertEqual([h['id'] for h in kanban.search('x', sort='due')], [c['id'], a['id'], b['id']])
		self.assertEqual([h['id'] for h in kanban.search('x', sort='id')], [c['id'], b['id'], a['id']])
		self.assertEqual(kanban.search('x')[-1]['id'], c['id'])
		results, total = kanban.search('x', sort='id', limit=2)
		self.assertEqual(([h['id'] for h in results], total), ([c['id'], b['id']], 3))


	def test_filters(self):
		'''Project, status, label, priority, due and assignee filters narrow results.'''

		self.user('ann')
		today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
		a = self.task('A', 'one', priority=4, labels=['host:pi'], due=today)
		b = self.task('A/sub', 'two', status='done')
		self.task('B', 'three', due='2000-01-01')
		pid = next(p['id'] for p in kanban.projects() if p['path'] == 'A')
		self.assertEqual({h['id'] for h in kanban.search('', project=pid)}, {a['id'], b['id']})
		self.assertEqual([h['id'] for h in kanban.search('', statuses=['done'])], [b['id']])
		self.assertEqual([h['id'] for h in kanban.search('', label='host:pi')], [a['id']])
		self.assertEqual([h['id'] for h in kanban.search('', priority=3)], [a['id']])
		self.assertEqual([h['id'] for h in kanban.search('', due='today')], [a['id']])
		self.assertEqual([h['title'] for h in kanban.search('', due='overdue')], ['three'])
		self.assertEqual(kanban.search(''), [])
		with self.assertRaises(ValueError):
			kanban.search('x', due='someday')
		with self.assertRaises(ValueError):
			kanban.search('x', sort='random')


class TestTasks(Base):
	'''Task fields and status.'''

	def test_create_defaults(self):
		'''A new task is todo with empty fields.'''

		t = self.task()
		self.assertEqual((t['status'], t['priority'], t['labels'], t['description'], t['checks']), ('todo', 0, [], '', [0, 0]))


	def test_validation(self):
		'''Titles, priorities, dates and statuses are validated.'''

		t = self.task()
		for bad in ({'title': '  '}, {'priority': 6}, {'due': '01/02/2026'}, {'remind_at': '2026-01-01'}, {'status': 'later'}, {'repeat': 'sometimes'}, {'labels': ['bad']}):
			with self.assertRaises(ValueError, msg=bad):
				kanban.task_update(t['id'], bad)
		with self.assertRaises(ValueError):
			kanban.task_create({'project_id': t['project_id'], 'title': ''})
		with self.assertRaises(LookupError):
			kanban.task(99_999)


	def test_clear_fields(self):
		'''Empty values clear due, reminder and priority.'''

		t = self.task(due='2030-01-01', remind_at='2030-01-01T09:00', priority=3)
		t = kanban.task_update(t['id'], {'due': '', 'remind_at': '', 'priority': 0})
		self.assertEqual((t['due'], t['remind_at'], t['priority']), (None, None, 0))


	def test_done_at_and_unarchive(self):
		'''done sets done_at; leaving done clears it; any status change unarchives.'''

		t = self.task()
		t = kanban.task_update(t['id'], {'status': 'done'})
		self.assertIsNotNone(t['done_at'])
		with kanban.connect() as db:
			db.execute('UPDATE tasks SET archived = 1 WHERE id = ?', (t['id'],))
		t = kanban.task_update(t['id'], {'status': 'todo'})
		self.assertEqual((t['done_at'], t['archived']), (None, 0))


	def test_checklist_counts(self):
		'''checks counts ticked and total checklist items.'''

		t = self.task(description='- [x] a\n- [ ] b\n* [X] c\nnot - [ ] d')
		self.assertEqual(t['checks'], [2, 3])


	def test_move_project_logged(self):
		'''Moving a task records the old and new project path.'''

		t   = self.task('A', 'x')
		pid = kanban.project_save('B')['id']
		t   = kanban.task_update(t['id'], {'project_id': pid})
		self.assertEqual(t['project'], 'B')
		self.assertEqual((t['history'][0]['old'], t['history'][0]['new']), ('A', 'B'))


	def test_archive_done(self):
		'''Tasks done longer than the cutoff are archived and leave the board.'''

		t = self.task(status='done')
		with kanban.connect() as db:
			db.execute("UPDATE tasks SET done_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (t['id'],))
		self.assertEqual(kanban.archive_done(30), 1)
		self.assertEqual(kanban.tasks(), [])
		self.assertEqual(len(kanban.tasks(archived=True)), 1)


	def test_comment(self):
		'''Comments are stored trimmed; empty ones are refused.'''

		t = kanban.comment_add(self.task()['id'], 'ann', '  hi  ')
		self.assertEqual(t['comments'][0]['body'], 'hi')
		with self.assertRaises(ValueError):
			kanban.comment_add(t['id'], 'ann', '   ')


class TestTrash(Base):
	'''Deleted tasks.'''

	def test_round_trip(self):
		'''Delete then restore brings back the task with everything attached under its old id.'''

		self.user('ann', admin=True)
		other = self.task(title='other')
		t     = self.task(title='doomed', description='desc', labels=['host:pi'], priority=2)
		kanban.task_update(t['id'], {'assignees': ['ann']})
		kanban.comment_add(t['id'], 'ann', 'note')
		kanban.attachment_add(t['id'], 'f.bin', 'application/pdf', b'\x00\x01')
		kanban.link_add(t['id'], 'blocked', other['id'])
		kanban.task_delete(t['id'])
		with self.assertRaises(LookupError):
			kanban.task(t['id'])
		self.assertEqual(kanban.task(other['id'])['links'], [])
		entry = kanban.trash_list()[0]
		self.assertEqual((entry['task_id'], entry['title'], entry['project']), (t['id'], 'doomed', 'Work'))
		r = kanban.trash_restore(entry['id'])
		self.assertEqual((r['id'], r['title'], r['description'], r['labels'], r['priority'], r['assignees']), (t['id'], 'doomed', 'desc', ['host:pi'], 2, ['ann']))
		self.assertEqual([c['body'] for c in r['comments']], ['note'])
		self.assertEqual(bytes(kanban.attachment_get(r['attachments'][0]['id'])['data']), b'\x00\x01')
		self.assertEqual([(l['kind'], l['id']) for l in r['links']], [('blocked', other['id'])])
		self.assertEqual([(l['kind'], l['id']) for l in kanban.task(other['id'])['links']], [('blocking', t['id'])])
		self.assertEqual(r['history'][0]['field'], 'restored')
		self.assertEqual(kanban.trash_list(), [])


	def test_taken_id_gets_new_one(self):
		'''If the old id was reused, the task comes back under a new id.'''

		t = self.task(title='x')
		kanban.task_delete(t['id'])
		with kanban.connect() as db:
			db.execute("INSERT INTO tasks (id, project_id, title, created, updated) VALUES (?, ?, 'taker', '', '')", (t['id'], t['project_id']))
		r = kanban.trash_restore(kanban.trash_list()[0]['id'])
		self.assertNotEqual(r['id'], t['id'])
		self.assertEqual(r['title'], 'x')


	def test_links_to_gone_tasks_dropped(self):
		'''Links to tasks deleted meanwhile are not restored.'''

		a, b = self.task(title='a'), self.task(title='b')
		kanban.link_add(a['id'], 'related', b['id'])
		kanban.task_delete(a['id'])
		kanban.task_delete(b['id'])
		entry = next(e for e in kanban.trash_list() if e['task_id'] == a['id'])
		self.assertEqual(kanban.trash_restore(entry['id'])['links'], [])


	def test_links_to_reused_ids_dropped(self):
		'''A link whose task id was reused by a newer task (SQLite reuses ids) is not restored, nor one that would point at itself.'''

		a, b = self.task(title='a'), self.task(title='b')
		kanban.link_add(a['id'], 'blocked', b['id'])
		kanban.task_delete(b['id'])
		c = self.task(title='c')
		with kanban.connect() as db:
			db.execute("UPDATE tasks SET created = '2000-01-01T00:00:00+00:00' WHERE id = ?", (c['id'],)) # tell it apart from b within the same second
		kanban.task_delete(a['id'])
		entry = next(e for e in kanban.trash_list() if e['task_id'] == a['id'])
		self.assertEqual(kanban.trash_restore(entry['id'])['links'], [])
		x, y = self.task(title='x'), self.task(title='y')
		kanban.link_add(x['id'], 'blocked', y['id'])
		kanban.task_delete(x['id'])
		kanban.task_delete(y['id'])
		self.task(title='z') # takes x's old id, so x comes back under y's
		restored = kanban.trash_restore(next(e for e in kanban.trash_list() if e['task_id'] == x['id'])['id'])
		self.assertEqual(restored['links'], [])


	def test_project_gone(self):
		'''A task whose project was deleted cannot be restored.'''

		t = self.task('Gone', 'x')
		kanban.task_delete(t['id'])
		kanban.project_delete(t['project_id'])
		self.assertEqual(kanban.trash_list()[0]['project'], 'a deleted project')
		with self.assertRaises(LookupError):
			kanban.trash_restore(kanban.trash_list()[0]['id'])


	def test_permissions(self):
		'''Only editors of the project see and restore its trash.'''

		ann, bob = self.user('ann'), self.user('bob')
		self.act(ann)
		t = self.task('Ann', 'x')
		kanban.task_delete(t['id'])
		entry = kanban.trash_list()[0]['id']
		self.act(bob)
		self.assertEqual(kanban.trash_list(), [])
		with self.assertRaises(PermissionError):
			kanban.trash_restore(entry)
		self.act(ann)
		accounts.share_add(t['project_id'], 'viewer', 'bob')
		self.act(bob)
		self.assertEqual(kanban.trash_list(), [])
		self.act(ann)
		accounts.share_add(t['project_id'], 'editor', 'bob')
		self.act(bob)
		self.assertEqual(len(kanban.trash_list()), 1)


	def test_deleted_by_names_agent(self):
		'''The trash records who deleted the task and through which agent.'''

		self.act(self.user('ann'))
		kanban.AGENT.set('claude')
		kanban.task_delete(self.task('Ann', 'x')['id'])
		self.assertEqual(kanban.trash_list()[0]['deleted_by'], 'ann (claude)')


	def test_purge(self):
		'''Entries older than the cutoff are removed for good.'''

		kanban.task_delete(self.task()['id'])
		kanban.task_delete(self.task()['id'])
		with kanban.connect() as db:
			db.execute("UPDATE trash SET deleted = '2000-01-01T00:00:00+00:00' WHERE id = (SELECT MIN(id) FROM trash)")
		self.assertEqual(kanban.trash_purge(), 1)
		self.assertEqual(len(kanban.trash_list()), 1)



if __name__ == '__main__':
	unittest.main()
