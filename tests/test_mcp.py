#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# tests/test_mcp.py

import unittest

from datetime import datetime, timedelta, timezone

from kanopy import accounts
from kanopy import kanban
from kanopy import mcp_server

from .base import Base, ToolError


class TestHelpers(Base):
	'''Pure text helpers.'''

	def test_check_item(self):
		'''Checklist items tick by number or unique text, and untick.'''

		desc = '- [ ] one\n- [ ] two\ntext\n* [x] three'
		self.assertEqual(mcp_server.check_item(desc, '2', True), '- [ ] one\n- [x] two\ntext\n* [x] three')
		self.assertEqual(mcp_server.check_item(desc, 'THREE', False), '- [ ] one\n- [ ] two\ntext\n* [ ] three')
		for bad in ('4', '0', 't', 'missing'):
			with self.assertRaises(ValueError, msg=bad):
				mcp_server.check_item(desc, bad, True)


	def test_edit_description(self):
		'''Edits replace text that matches exactly once; append adds a paragraph.'''

		self.assertEqual(mcp_server.edit_description('a b c', [{'old': 'b', 'new': 'B'}, {'old': 'c', 'new': ''}], ''), 'a B ')
		self.assertEqual(mcp_server.edit_description('top\n', [], ' more '), 'top\n\nmore')
		self.assertEqual(mcp_server.edit_description('', [], 'first'), 'first')
		for edits in ([{'old': 'x', 'new': 'y'}], [{'old': 'a', 'new': 'y'}], [{'old': '', 'new': 'y'}]):
			with self.assertRaises(ValueError, msg=edits):
				mcp_server.edit_description('a a', edits, '')


	def test_norm_label(self):
		'''Labels must use an allowed namespace.'''

		self.assertEqual(mcp_server.norm_label(' Host:PI '), ('host:pi', 'host'))
		for bad in ('pi', 'color:red', 'host:pi!'):
			with self.assertRaises(ValueError, msg=bad):
				mcp_server.norm_label(bad)


	def test_project_id(self):
		'''Projects resolve by id or case-insensitive path.'''

		pid = kanban.project_save('AI/Hermes')['id']
		self.assertEqual(mcp_server.project_id('ai/hermes/'), pid)
		self.assertEqual(mcp_server.project_id(str(pid)), pid)
		with self.assertRaises(LookupError):
			mcp_server.project_id('nope')


class TestProtocol(Base):
	'''JSON-RPC handling.'''

	def test_initialize(self):
		'''initialize echoes the client protocol and sends the instructions.'''

		r = mcp_server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2099-01-01'}}, 'claude')['result']
		self.assertEqual(r['protocolVersion'], '2099-01-01')
		self.assertEqual(r['instructions'], mcp_server.INSTRUCTIONS)
		r = mcp_server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'}, 'claude')['result']
		self.assertEqual(r['protocolVersion'], mcp_server.PROTOCOL)


	def test_notifications_and_unknown(self):
		'''Notifications get no reply; unknown methods get -32601; ping is empty.'''

		self.assertIsNone(mcp_server.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, 'claude'))
		self.assertEqual(mcp_server.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'bogus'}, 'claude')['error']['code'], -32601)
		self.assertEqual(mcp_server.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'ping'}, 'claude')['result'], {})


	def test_tool_schemas(self):
		'''Every tool has a description, an object schema and required fields that exist.'''

		tools = mcp_server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, 'claude')['result']['tools']
		self.assertEqual({t['name'] for t in tools}, {t[0] for t in mcp_server.TOOLS})
		for tool in tools:
			schema = tool['inputSchema']
			self.assertTrue(tool['description'])
			self.assertEqual(schema['type'], 'object')
			self.assertFalse(schema['additionalProperties'])
			self.assertTrue(set(schema['required']) <= set(schema['properties']), tool['name'])
			for name, prop in schema['properties'].items():
				self.assertIn('type', prop, f'{tool["name"]}.{name}')


	def test_errors_are_tool_errors(self):
		'''Bad calls come back as isError text, not protocol errors.'''

		with self.assertRaises(ToolError):
			self.mcp('nope')
		with self.assertRaises(ToolError):
			self.mcp('get_task', {})
		with self.assertRaises(ToolError):
			self.mcp('get_task', {'ids': [12_345]})


class TestContext(Base):
	'''Session-start briefing.'''

	def test_deepest_folder_wins(self):
		'''The project whose folder is the deepest one containing cwd is chosen, doing tasks first.'''

		outer = kanban.project_save('Code', folder='/src')['id']
		inner = kanban.project_save('Code/app', folder='/src/app')['id']
		kanban.task_create({'project_id': outer, 'title': 'outer task'})
		kanban.task_create({'project_id': inner, 'title': 'todo one', 'priority': 5})
		kanban.task_create({'project_id': inner, 'title': 'doing one', 'status': 'doing'})
		kanban.task_create({'project_id': inner, 'title': 'finished', 'status': 'done'})
		text  = mcp_server.context('/src/app/lib')
		lines = text.splitlines()
		self.assertEqual(lines[0], 'kanopy project for this directory: Code/app (2 open)')
		self.assertIn('doing one', lines[1])
		self.assertIn('todo one', lines[2])
		self.assertNotIn('finished', text)
		self.assertTrue(mcp_server.context('/src/other').startswith('kanopy project for this directory: Code (3 open)')) # sub-project tasks included
		self.assertEqual(mcp_server.context('/elsewhere'), '')
		self.assertEqual(mcp_server.context('/src/application'), mcp_server.context('/src/x')) # /src/app is not a parent of /src/application


	def test_stale_doing_and_overflow(self):
		'''Doing tasks idle for days are flagged and more than 10 tasks are summarized.'''

		pid = kanban.project_save('P', folder='/p')['id']
		t   = kanban.task_create({'project_id': pid, 'title': 'stuck', 'status': 'doing'})
		with kanban.connect() as db:
			db.execute('UPDATE tasks SET updated = ? WHERE id = ?', ((datetime.now(timezone.utc) - timedelta(days=4)).isoformat(), t['id']))
		for i in range(11):
			kanban.task_create({'project_id': pid, 'title': f't{i}'})
		text = mcp_server.context('/p')
		self.assertIn('no update in 4d', text)
		self.assertIn('... 2 more (list_tasks)', text)


	def test_unread_count(self):
		'''The briefing mentions unread notifications.'''

		ann = self.user('ann')
		self.act(ann)
		pid = kanban.project_save('P', folder='/p')['id']
		t   = kanban.task_create({'project_id': pid, 'title': 'x'})
		with kanban.connect() as db:
			db.execute("INSERT INTO notifications (user_id, task_id, kind, actor, text, created) VALUES (?, ?, 'mention', 'bob', 'hi', ?)", (ann['id'], t['id'], kanban.now()))
		self.assertIn('1 unread notifications', mcp_server.context('/p'))


class TestTools(Base):
	'''Tool behaviour.'''

	def test_create_with_new_project(self):
		'''save_task with a new path creates the project, reusing existing parents whatever their case.'''

		kanban.project_save('AI')
		out = self.mcp('save_task', {'project': 'ai/hermes', 'title': 'first', 'add_labels': ['host:pi'], 'priority': 2})
		self.assertRegex(out, r'^#\d+ \[AI/hermes\] first  p2  host:pi$')
		self.assertEqual([p['path'] for p in kanban.projects() if p['path'].startswith('AI')], ['AI', 'AI/hermes'])


	def test_failed_create_removes_new_project(self):
		'''If the task cannot be saved, the projects made for it are removed again.'''

		with self.assertRaises(ToolError):
			self.mcp('save_task', {'project': 'New/Deep', 'title': 'x', 'priority': 9})
		self.assertFalse([p for p in kanban.projects() if p['path'].startswith('New')])


	def test_create_needs_project_and_title(self):
		'''Creating requires both project and title, and refuses edit/append.'''

		with self.assertRaises(ToolError):
			self.mcp('save_task', {'title': 'x'})
		with self.assertRaises(ToolError):
			self.mcp('save_task', {'project': 'Inbox'})
		with self.assertRaises(ToolError):
			self.mcp('save_task', {'project': 'Inbox', 'title': 'x', 'append': 'y'})
		with self.assertRaises(ToolError):
			self.mcp('save_task', {'project': '9999', 'title': 'x'})


	def test_stale_description_guard(self):
		'''Replacing a description needs a fresh read; edit and append do not.'''

		t = self.task(description='v1')
		with self.assertRaises(ToolError):
			self.mcp('save_task', {'id': t['id'], 'description': 'v2'})
		self.mcp('get_task', {'ids': [t['id']]})
		self.mcp('save_task', {'id': t['id'], 'description': 'v2'})
		self.mcp('save_task', {'id': t['id'], 'description': 'v3'}) # its own write counts as read
		kanban.task_update(t['id'], {'description': 'changed in the web UI'})
		with self.assertRaises(ToolError):
			self.mcp('save_task', {'id': t['id'], 'description': 'v4'})
		self.mcp('save_task', {'id': t['id'], 'append': 'more'}, agent='other')
		self.assertEqual(kanban.task(t['id'])['description'], 'changed in the web UI\n\nmore')
		with self.assertRaises(ToolError):
			self.mcp('save_task', {'id': t['id'], 'description': 'x'}, agent='third') # per agent
		self.mcp('get_task', {'ids': [t['id']]}) # two sessions of the same agent both read it
		kanban.SESSION.set('b')
		self.mcp('get_task', {'ids': [t['id']]})
		self.mcp('save_task', {'id': t['id'], 'description': 'from b'})
		kanban.SESSION.set(None)
		with self.assertRaises(ToolError):
			self.mcp('save_task', {'id': t['id'], 'description': 'stale from a'}) # per session


	def test_seen_is_capped(self):
		'''The read record keeps only the newest SEEN_MAX entries.'''

		old, mcp_server.SEEN_MAX = mcp_server.SEEN_MAX, 3
		try:
			mcp_server.SEEN.clear()
			ids = [self.task(title=f't{i}')['id'] for i in range(5)]
			for i in ids:
				self.mcp('get_task', {'ids': [i]})
			self.assertEqual([k[-1] for k in mcp_server.SEEN], ids[-3:])
		finally:
			mcp_server.SEEN_MAX = old


	def test_edit_append_check_together(self):
		'''edit, append and check apply in that order in one call.'''

		t = self.task(description='- [ ] one\n- [ ] two')
		out = self.mcp('save_task', {'id': t['id'], 'edit': [{'old': 'two', 'new': 'deux'}], 'append': '- [ ] three', 'check': 'three'})
		self.assertIn('☐1/3', out)
		self.assertEqual(kanban.task(t['id'])['description'], '- [ ] one\n- [ ] deux\n\n- [x] three')


	def test_labels_policy(self):
		'''type: values must exist; host: values are created but near-duplicates are refused.'''

		t = self.task()
		self.mcp('save_task', {'id': t['id'], 'add_labels': ['type:bug', 'host:web-01']})
		with self.assertRaises(ToolError):
			self.mcp('save_task', {'id': t['id'], 'add_labels': ['type:madeup']})
		with self.assertRaises(ToolError):
			self.mcp('save_task', {'id': t['id'], 'add_labels': ['host:web01']})
		self.mcp('save_task', {'id': t['id'], 'remove_labels': ['type:bug']})
		self.assertEqual(kanban.task(t['id'])['labels'], ['host:web-01'])


	def test_assign(self):
		'''assign and unassign take usernames with or without @.'''

		self.user('ann', admin=True)
		t = self.task()
		self.mcp('save_task', {'id': t['id'], 'assign': ['@Ann']})
		self.assertEqual(kanban.task(t['id'])['assignees'], ['ann'])
		self.mcp('save_task', {'id': t['id'], 'unassign': ['ann']})
		self.assertEqual(kanban.task(t['id'])['assignees'], [])


	def test_done_warns_unticked_and_pushes(self):
		'''Marking done with unticked items says so, and the agent-done push goes out.'''

		me  = self.user('me', admin=True)
		accounts.user_update(me, {'ntfy_topic': 'my-topic'})
		self.act(me)
		t   = self.task(description='- [x] a\n- [ ] b')
		out = self.mcp('save_task', {'id': t['id'], 'status': 'done', 'comment': 'finished'})
		self.assertIn('1/2 checklist items are unticked', out)
		self.assertEqual(self.pushes[0]['title'], f'claude finished #{t["id"]}')
		self.assertEqual(kanban.task(t['id'])['comments'][0]['author'], 'claude')
		self.assertEqual(self.pushes[0]['topic'], 'my-topic')


	def test_bulk_ids(self):
		'''ids applies one update to each task and reports per-task errors.'''

		a, b = self.task(), self.task()
		out  = self.mcp('save_task', {'ids': [a['id'], b['id'], 99_999], 'status': 'doing'})
		lines = out.splitlines()
		self.assertTrue(lines[0].startswith(f'#{a["id"]} ▶'))
		self.assertIn('#99999 error', lines[2])


	def test_attach_and_read_file(self):
		'''attach stores a text file; get_task file= reads it back, truncated or refused when binary.'''

		t = self.task()
		self.mcp('save_task', {'id': t['id'], 'attach': {'name': 'log.txt', 'text': 'line'}})
		fid = kanban.task(t['id'])['attachments'][0]['id']
		self.assertEqual(self.mcp('get_task', {'file': fid}), 'line')
		kanban.attachment_add(t['id'], 'b.bin', 'application/pdf', b'\x00\xff')
		self.assertIn('is binary', self.mcp('get_task', {'file': fid + 1}))
		old, mcp_server.MAX_TEXT = mcp_server.MAX_TEXT, 2
		try:
			self.assertTrue(self.mcp('get_task', {'file': fid}).startswith('li\n... truncated'))
		finally:
			mcp_server.MAX_TEXT = old


	def test_get_task_render(self):
		'''get_task shows the line, description, relations, files and the last 5 comments.'''

		a, b = self.task(title='a', description='body'), self.task(title='b')
		kanban.link_add(a['id'], 'subtask', b['id'])
		for i in range(7):
			kanban.comment_add(a['id'], 'ann', f'c{i}')
		out = self.mcp('get_task', {'ids': [a['id']]})
		self.assertIn('\nbody\n', out)
		self.assertIn(f'subtasks #{b["id"]} b', out)
		self.assertIn('comments (last 5 of 7, comments=true for all):', out)
		self.assertNotIn('c1', out)
		self.assertIn('c6', out)
		full = self.mcp('get_task', {'ids': [a['id']], 'comments': True})
		self.assertIn('c0', full)
		self.assertIn('\n\n---\n\n', self.mcp('get_task', {'ids': [a['id'], b['id']]}))


	def test_list_tasks(self):
		'''list_tasks groups by project and honours every filter.'''

		self.user('ann', admin=True)
		today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
		a = self.task('A', 'alpha', priority=4, labels=['host:pi'], due=today, assignees=['ann'])
		self.task('A/sub', 'beta', status='doing')
		self.task('B', 'gamma', status='done', description='hidden word')
		out = self.mcp('list_tasks')
		self.assertEqual([l for l in out.splitlines() if l.startswith('[')], ['[A]', '[A/sub]'])
		self.assertNotIn('gamma', out)
		self.assertIn('gamma', self.mcp('list_tasks', {'status': 'all'}))
		self.assertIn('word', self.mcp('list_tasks', {'text': 'word', 'status': 'all'}))
		self.assertIn('    description: hidden word', self.mcp('list_tasks', {'text': 'word', 'status': 'all'}))
		self.assertEqual(self.mcp('list_tasks', {'labels': ['host:pi']}).splitlines()[1].split()[0], f'#{a["id"]}')
		self.assertIn('alpha', self.mcp('list_tasks', {'priority_min': 3}))
		self.assertNotIn('beta', self.mcp('list_tasks', {'priority_min': 3}))
		self.assertIn('alpha', self.mcp('list_tasks', {'due': 'today'}))
		self.assertIn('alpha', self.mcp('list_tasks', {'assignee': 'ann'}))
		self.assertEqual(self.mcp('list_tasks', {'assignee': 'nobody'}), 'no matching tasks')
		self.assertIn('beta', self.mcp('list_tasks', {'status': 'doing'}))
		self.assertNotIn('beta', self.mcp('list_tasks', {'project': 'B', 'status': 'all'}))
		self.assertIn('... 1 more', self.mcp('list_tasks', {'limit': 1}))
		changed = self.mcp('list_tasks', {'changed': '1d'})
		self.assertIn('changed:', changed)
		self.assertIn('gamma', changed)
		for bad in ({'changed': 'yesterday'}, {'status': 'later'}):
			with self.assertRaises(ToolError):
				self.mcp('list_tasks', bad)

	def test_list_tasks_ready(self):
		'''ready lists todo tasks whose blockers are all done.'''

		free, stuck, blocker = self.task('A', 'free'), self.task('A', 'stuck'), self.task('A', 'blocker')
		self.task('A', 'busy', status='doing')
		kanban.link_add(stuck['id'], 'blocked', blocker['id'])
		ready = lambda: [l.split()[0] for l in self.mcp('list_tasks', {'ready': True}).splitlines() if l.startswith('#')]
		self.assertEqual(sorted(ready()), sorted([f'#{free["id"]}', f'#{blocker["id"]}']))
		kanban.task_update(blocker['id'], {'status': 'done'})
		self.assertIn(f'#{stuck["id"]}', ready())


	def test_list_tasks_team(self):
		'''team= lists tasks on projects shared with that team and their sub-projects, for members only.'''

		ann, bob = self.user('ann'), self.user('bob')
		self.act(ann)
		self.task('NT', 'one')
		self.task('NT/havoc', 'two')
		self.task('Other', 'three')
		accounts.team_create(ann, 'crew')
		accounts.team_member_add(ann, accounts.teams(ann)[0]['id'], 'bob')
		accounts.share_add(next(p['id'] for p in kanban.projects() if p['path'] == 'NT'), 'editor', team='crew')
		self.act(bob)
		out = self.mcp('list_tasks', {'team': 'CREW'})
		self.assertIn('one', out)
		self.assertIn('two', out)
		self.assertNotIn('three', out)
		self.act(self.user('cat'))
		with self.assertRaises(ToolError):
			self.mcp('list_tasks', {'team': 'crew'})


	def test_files_and_archived_markers(self):
		'''get_task lists files; list_tasks marks archived tasks and file counts.'''

		t = self.task(status='done')
		kanban.attachment_add(t['id'], 'a.txt', 'text/plain', b'x')
		self.assertIn('files: #', self.mcp('get_task', {'ids': [t['id']]}))
		self.assertIn('files:1', mcp_server.task_line(t['id']))
		with kanban.connect() as db:
			db.execute('UPDATE tasks SET archived = 1 WHERE id = ?', (t['id'],))
		self.assertIn('  archived  files:1', self.mcp('list_tasks', {'status': 'done'}))


	def test_changed_overflow_and_create_assign(self):
		'''changed lists cap at limit; create can assign.'''

		self.user('ann', admin=True)
		out = self.mcp('save_task', {'project': 'Work', 'title': 'a', 'assign': ['ann']})
		self.assertIn('@ann', out)
		self.task(title='b')
		self.assertIn('... 1 more', self.mcp('list_tasks', {'changed': '1d', 'limit': 1}))


	def test_claims(self):
		'''doing claims a task for the session; another session is refused unless it takes over; leaving doing releases it.'''

		self.user('ann', admin=True)
		t = self.task()
		kanban.AGENT.set('claude') # set by the /mcp route from X-Agent
		kanban.SESSION.set('aaa')
		self.assertIn('by:claude#aaa', self.mcp('save_task', {'id': t['id'], 'status': 'doing'}))
		self.mcp('save_task', {'id': t['id'], 'status': 'doing'}) # the same session again is fine
		kanban.SESSION.set('bbb')
		with self.assertRaises(ToolError) as err:
			self.mcp('save_task', {'id': t['id'], 'status': 'doing'})
		self.assertIn('already being worked on by claude #aaa', str(err.exception))
		self.mcp('save_task', {'id': t['id'], 'title': 'renamed'}) # other edits are allowed
		self.assertIn('by:claude#bbb', self.mcp('save_task', {'id': t['id'], 'status': 'doing', 'take': True}))
		self.assertEqual(kanban.task(t['id'])['history'][0]['field'], 'claim')
		self.assertIsNone(kanban.task_update(t['id'], {'status': 'todo'})['claimed_by'])
		kanban.SESSION.set('ccc')
		self.assertIn('by:claude#ccc', self.mcp('save_task', {'id': t['id'], 'status': 'doing'})) # released, so anyone can claim it


	def test_read_only_tools(self):
		'''A read-only token sees and runs only the tools that change nothing.'''

		t     = self.task()
		tools = {x['name'] for x in mcp_server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, 'claude', True)['result']['tools']}
		self.assertEqual(tools, {'list_projects', 'list_tasks', 'get_task', 'inbox', 'labels'})
		call = lambda name, args: mcp_server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}}, 'claude', True)['result']
		self.assertFalse(call('get_task', {'ids': [t['id']]}).get('isError'))
		self.assertFalse(call('labels', {}).get('isError'))
		for name, args in (('save_task', {'id': t['id'], 'title': 'x'}), ('comment', {'id': t['id'], 'text': 'x'}), ('delete', {'task': t['id']}), ('labels', {'create': 'type:x'}), ('inbox', {'read': True})):
			self.assertIn('read-only', call(name, args)['content'][0]['text'], name)
		self.assertEqual(kanban.task(t['id'])['title'], 'task')


	def test_line_format(self):
		'''Compact lines carry status, fields, checks, labels, assignees and relations.'''

		self.user('ann', admin=True)
		a = self.task(title='a', status='doing', priority=3, due='2030-01-02', remind_at='2030-01-01T09:00', repeat='weekly', description='- [x] x\n- [ ] y', labels=['host:pi'], assignees=['ann'])
		b = self.task(title='b')
		kanban.link_add(a['id'], 'blocked', b['id'])
		self.assertEqual(mcp_server.task_line(a['id']), f'#{a["id"]} ▶ [Work] a  by:system  p3  due:2030-01-02  remind:2030-01-01T09:00  repeat:weekly  ☐1/2  host:pi  @ann  blocked-by:#{b["id"]}')


	def test_projects_tools(self):
		'''list_projects shows paths and roles; save_project updates but never creates.'''

		self.task('A', 'x')
		self.assertIn('A  (id', self.mcp('list_projects'))
		with self.assertRaises(ToolError):
			self.mcp('save_project', {'path': 'New'})
		self.mcp('save_project', {'project': 'A', 'folder': '~/a', 'description': 'desc'})
		self.assertIn('~/a', self.mcp('list_projects'))
		self.mcp('save_project', {'project': 'A', 'path': 'Moved/A'})
		self.assertIn('Moved/A', self.mcp('list_projects'))


	def test_comment_link_labels_inbox(self):
		'''comment, link, labels and inbox tools.'''

		ann = self.user('ann', admin=True)
		self.act(ann)
		a, b = self.task(title='a'), self.task(title='b')
		self.assertEqual(self.mcp('comment', {'id': a['id'], 'text': 'note'}), f'commented on #{a["id"]}')
		self.assertIn(f'related:#{b["id"]}', self.mcp('link', {'id': a['id'], 'other': b['id'], 'kind': 'related'}))
		self.assertNotIn('related', self.mcp('link', {'id': a['id'], 'other': b['id'], 'kind': 'related', 'remove': True}))
		self.assertIn('type: ', self.mcp('labels'))
		self.assertIn('madeup', self.mcp('labels', {'create': 'type:madeup'}))
		self.assertNotIn('madeup', self.mcp('labels', {'delete': 'type:madeup'}))
		self.assertEqual(self.mcp('inbox'), 'no unread notifications')
		with kanban.connect() as db:
			db.execute("INSERT INTO notifications (user_id, task_id, kind, actor, text, created) VALUES (?, ?, 'mention', 'bob', 'bob mentioned you', ?)", (ann['id'], a['id'], kanban.now()))
		self.assertIn('bob mentioned you: a', self.mcp('inbox', {'read': True}))
		self.assertEqual(self.mcp('inbox'), 'no unread notifications')


	def test_delete_and_restore(self):
		'''delete trashes a task, restore brings it back, and empty projects can be deleted.'''

		t = self.task('A', 'x')
		self.assertIn('restore= within 30 days', self.mcp('delete', {'task': t['id']}))
		self.assertIn('x', self.mcp('delete', {'restore': t['id']}))
		with self.assertRaises(ToolError):
			self.mcp('delete', {'restore': t['id']})
		with self.assertRaises(ToolError):
			self.mcp('delete', {'project': 'A'})
		kanban.project_save('Empty')
		self.assertEqual(self.mcp('delete', {'project': 'Empty'}), 'deleted Empty')
		with self.assertRaises(ToolError):
			self.mcp('delete', {})


	def test_acts_as_token_user(self):
		'''Tools only see what the token's user can see.'''

		ann, bob = self.user('ann'), self.user('bob')
		self.act(ann)
		t = self.task('Ann', 'secret')
		self.act(bob)
		self.assertEqual(self.mcp('list_tasks', {'status': 'all'}), 'no matching tasks')
		with self.assertRaises(ToolError):
			self.mcp('get_task', {'ids': [t['id']]})
		with self.assertRaises(ToolError):
			self.mcp('save_task', {'id': t['id'], 'title': 'pwned'})
		self.act(ann)
		accounts.share_add(t['project_id'], 'viewer', 'bob')
		self.act(bob)
		self.assertIn('secret', self.mcp('get_task', {'ids': [t['id']]}))



if __name__ == '__main__':
	unittest.main()
