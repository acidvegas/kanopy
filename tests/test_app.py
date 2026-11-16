#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# tests/test_app.py

import io
import unittest

from kanopy import accounts
from kanopy import app
from kanopy import kanban
from kanopy import notify

from .base import Base, PASSWORD


class AppBase(Base):
	'''Flask test client with an admin signed in by cookie and a non-admin API token.'''

	def setUp(self):
		'''Create root (admin, cookie session) and ann (API token).'''

		super().setUp()
		self.client = app.app.test_client()
		self.root   = self.user('root', admin=True)
		self.ann    = self.user('ann')
		self.key    = accounts.token_create(self.ann, 'claude')['token']
		self.client.set_cookie(app.COOKIE, accounts.login('root', PASSWORD))


	def rpc(self, method: str, params: dict = None, key: str = None, agent: str = 'claude', rid: int = 1):
		'''
		POST one JSON-RPC message to /mcp with an API token.

		:param method: JSON-RPC method
		:param params: Params
		:param key: API token, default ann's
		:param agent: X-Agent header
		:param rid: Request id, None for a notification
		'''

		body = {'jsonrpc': '2.0', 'method': method, 'params': params or {}}
		if rid is not None:
			body['id'] = rid

		return self.client.post('/mcp', json=body, headers={'X-Key': key or self.key, 'X-Agent': agent})


class TestAuthLayer(AppBase):
	'''Request authentication and cross-site protection.'''

	def test_signed_out(self):
		'''API calls need a user; OPEN paths and static files do not.'''

		anon = app.app.test_client()
		self.assertEqual(anon.get('/api/projects').status_code, 401)
		self.assertEqual(anon.get('/api/setup').json, {'needed': False})
		self.assertEqual(anon.get('/').status_code, 200)
		with anon.get('/style.css') as r:
			self.assertEqual(r.status_code, 200)
		self.assertEqual(anon.get('/api/invites/check/bogus').status_code, 404)


	def test_index_base_url(self):
		'''The page has {{BASE}} replaced with the public address.'''

		html = self.client.get('/').get_data(as_text=True)
		self.assertNotIn('{{BASE}}', html)
		self.assertIn(notify.BASE_URL + '/og.png', html)


	def test_cross_site_writes_refused(self):
		'''Cookie-signed writes from another origin, or that are not JSON, are refused; API tokens skip the check.'''

		self.assertEqual(self.client.post('/api/projects', json={'path': 'X'}, headers={'Origin': 'https://evil.example'}).status_code, 403)
		self.assertEqual(self.client.post('/api/projects', json={'path': 'X'}, headers={'Sec-Fetch-Site': 'cross-site'}).status_code, 403)
		self.assertEqual(self.client.post('/api/projects', data='path=X', content_type='application/x-www-form-urlencoded').status_code, 415)
		self.assertEqual(self.client.post('/api/projects', json={'path': 'X'}, headers={'Origin': 'http://localhost'}).status_code, 200)
		anon = app.app.test_client()
		self.assertEqual(anon.post('/api/projects', json={'path': 'Y'}, headers={'X-Key': self.key, 'Origin': 'https://evil.example'}).status_code, 200)


	def test_login_cookie_and_rate_limit(self):
		'''Sign-in sets an HttpOnly SameSite cookie; five failures lock the address out.'''

		anon = app.app.test_client()
		r    = anon.post('/api/login', json={'username': 'ann', 'password': PASSWORD})
		self.assertEqual(r.status_code, 200)
		cookie = r.headers['Set-Cookie']
		self.assertIn('HttpOnly', cookie)
		self.assertIn('SameSite=Lax', cookie)
		self.assertEqual(anon.get('/api/me').json['username'], 'ann')
		anon.post('/api/logout')
		self.assertEqual(anon.get('/api/me').status_code, 401)
		for _ in range(app.FAIL_LIMIT):
			self.assertEqual(anon.post('/api/login', json={'username': 'ann', 'password': 'wrong'}).status_code, 403)
		r = anon.post('/api/login', json={'username': 'ann', 'password': PASSWORD})
		self.assertEqual(r.status_code, 403)
		self.assertIn('too many', r.json['error'])


	def test_error_codes(self):
		'''Lookup errors are 404, permission errors 403, validation errors 400.'''

		self.assertEqual(self.client.get('/api/tasks/99999').status_code, 404)
		self.assertEqual(self.client.post('/api/projects', json={'path': ''}).status_code, 400)
		self.assertEqual(app.app.test_client().get('/api/settings', headers={'X-Key': self.key}).status_code, 403)


	def test_actor_cleared_after_request(self):
		'''The signed-in user does not leak past the request.'''

		self.client.get('/api/me')
		self.assertIsNone(kanban.ACTOR.get())


class TestSetup(Base):
	'''First-run page.'''

	def test_setup_imports_shared_key(self):
		'''Setup on an empty board makes the admin, signs in and imports KANOPY_KEY as their token.'''

		old, app.KEY = app.KEY, 'legacy-key'
		try:
			c = app.app.test_client()
			self.assertEqual(c.get('/api/setup').json, {'needed': True})
			self.assertEqual(c.post('/api/setup', json={'username': 'root', 'password': PASSWORD}).status_code, 200)
			self.assertTrue(c.get('/api/me').json['is_admin'])
			self.assertEqual(accounts.user_by_token('legacy-key')['username'], 'root')
			self.assertEqual(c.post('/api/setup', json={'username': 'two', 'password': PASSWORD}).status_code, 403)
		finally:
			app.KEY = old


class TestRoutes(AppBase):
	'''REST endpoints used by the web UI.'''

	def test_task_lifecycle(self):
		'''Create, update, comment, attach, link, bulk, reorder, trash and restore over HTTP.'''

		c   = self.client
		pid = c.post('/api/projects', json={'path': 'Web/app', 'folder': '~/app'}).json['id']
		t   = c.post('/api/tasks', json={'project_id': pid, 'title': 'one'}).json
		u   = c.patch(f'/api/tasks/{t["id"]}', json={'priority': 3, 'labels': ['host:pi']}).json
		self.assertEqual((u['priority'], u['labels']), (3, ['host:pi']))
		self.assertEqual(c.post(f'/api/tasks/{t["id"]}/comments', json={'body': 'hi'}).json['comments'][0]['author'], 'root')
		up = c.post(f'/api/tasks/{t["id"]}/attachments', data={'file': (io.BytesIO(b'data'), 'a.txt', 'text/plain')}, content_type='multipart/form-data').json
		aid = up['attachments'][0]['id']
		r = c.get(f'/api/attachments/{aid}')
		self.assertEqual((r.data, r.headers['X-Content-Type-Options']), (b'data', 'nosniff'))
		self.assertTrue(r.headers['Content-Disposition'].startswith('attachment;'))
		two = c.post('/api/tasks', json={'project_id': pid, 'title': 'two'}).json
		self.assertEqual(c.post(f'/api/tasks/{t["id"]}/links', json={'kind': 'related', 'other': two['id']}).json['links'][0]['id'], two['id'])
		self.assertEqual(c.delete(f'/api/tasks/{t["id"]}/links?kind=related&other={two["id"]}').json['links'], [])
		self.assertEqual(c.post('/api/reorder', json={'status': 'doing', 'ids': [two['id']]}).json, {'ok': True})
		self.assertEqual(c.post('/api/tasks/bulk', json={'ids': [two['id']], 'status': 'done'}).json, {'changed': 1})
		self.assertEqual(len(c.get(f'/api/tasks?project={pid}').json['tasks']), 2)
		found = c.get('/api/search?q=one').json
		self.assertEqual((found['results'][0]['id'], found['total']), (t['id'], 1))
		capped = c.get(f'/api/tasks?project={pid}&done_limit=0').json
		self.assertEqual((len(capped['tasks']), capped['done_total']), (1, 1))
		self.assertTrue(c.get(f'/api/projects/{pid}/activity').json['events'])
		self.assertEqual(c.delete(f'/api/tasks/{t["id"]}').json, {'ok': True})
		trash = c.get('/api/trash').json
		self.assertEqual((trash['days'], trash['trash'][0]['task_id']), (kanban.TRASH_DAYS, t['id']))
		back = c.post(f'/api/trash/{trash["trash"][0]["id"]}/restore').json
		self.assertEqual(back['id'], t['id'])
		self.assertEqual(c.delete(f'/api/attachments/{back["attachments"][0]["id"]}').json['attachments'], [])


	def test_projects_labels_repeats(self):
		'''Project, label and repeat endpoints.'''

		c   = self.client
		pid = c.post('/api/projects', json={'path': 'P'}).json['id']
		self.assertEqual(c.patch(f'/api/projects/{pid}', json={'description': 'd'}).json['id'], pid)
		self.assertIn('P', [p['path'] for p in c.get('/api/projects').json['projects']])
		self.assertEqual(c.post('/api/projects/order', json={'parent_id': None, 'ids': [pid]}).json, {'ok': True})
		self.assertEqual(c.post('/api/labels', json={'name': 'host:x', 'global': True}).json, {'ok': True})
		self.assertEqual(c.patch('/api/labels', json={'name': 'host:x', 'color': '#112233'}).json, {'ok': True})
		self.assertIn('host:x', [l['name'] for l in c.get('/api/labels').json['labels']])
		self.assertEqual(c.delete('/api/labels?name=host:x').json, {'ok': True})
		self.assertEqual(c.get('/api/repeats?from=2026-01-01&to=2026-01-31').json, {'dates': {}})
		self.assertEqual(c.delete(f'/api/projects/{pid}').json, {'ok': True})


	def test_accounts_endpoints(self):
		'''Invites, sign-up, teams, shares, tokens, profile and users over HTTP.'''

		c   = self.client
		inv = c.post('/api/invites').json
		self.assertIn('/#/invite/', inv['url'])
		anon = app.app.test_client()
		self.assertEqual(anon.get(f'/api/invites/check/{inv["token"]}').json, {'ok': True})
		self.assertEqual(anon.post('/api/signup', json={'token': inv['token'], 'username': 'cat', 'password': PASSWORD}).status_code, 200)
		self.assertEqual(anon.get('/api/me').json['username'], 'cat')
		self.assertEqual(c.get('/api/invites').json['invites'][0]['used_by'], 'cat')
		self.assertEqual(c.post('/api/teams', json={'name': 'crew'}).json['teams'][0]['name'], 'crew')
		team = c.get('/api/teams').json['teams'][0]['id']
		self.assertEqual(len(c.post(f'/api/teams/{team}/members', json={'username': 'ann'}).json['teams'][0]['members']), 2)
		self.assertEqual(c.patch(f'/api/teams/{team}/members/{self.ann["id"]}', json={'owner': True}).status_code, 200)
		self.assertEqual(c.delete(f'/api/teams/{team}/members/{self.ann["id"]}').status_code, 200)
		self.assertEqual(c.delete(f'/api/teams/{team}').json, {'teams': []})
		pid   = c.post('/api/projects', json={'path': 'S'}).json['id']
		share = c.post(f'/api/projects/{pid}/shares', json={'username': 'ann', 'role': 'viewer'}).json['shares'][-1]
		self.assertEqual(c.get(f'/api/projects/{pid}/shares').json['shares'][-1]['name'], 'ann')
		self.assertEqual(c.delete(f'/api/shares/{share["id"]}').status_code, 200)
		tok = c.post('/api/tokens', json={'name': 'x'}).json
		self.assertEqual(c.get('/api/tokens').json['tokens'][0]['name'], 'x')
		self.assertEqual(c.delete(f'/api/tokens/{tok["id"]}').json, {'ok': True})
		self.assertEqual(c.patch('/api/me', json={'ntfy_topic': 'root-t'}).json['ntfy_topic'], 'root-t')
		r = c.put('/api/me/avatar', data={'file': (io.BytesIO(b'img'), 'a.png', 'image/png')}, content_type='multipart/form-data')
		self.assertIsNotNone(r.json['avatar_at'])
		self.assertEqual(c.get(f'/api/users/{self.root["id"]}/avatar').data, b'img')
		self.assertIsNone(c.delete('/api/me/avatar').json['avatar_at'])
		self.assertEqual(c.post(f'/api/users/{self.ann["id"]}/password', json={'password': 'resetpassword'}).json, {'ok': True})
		self.assertIn('ann', [u['username'] for u in c.get('/api/users').json['users']])
		self.assertNotIn('ann', [u['username'] for u in c.delete(f'/api/users/{self.ann["id"]}').json['users']])
		self.assertEqual(c.get('/api/notifications').json, {'notifications': [], 'unread': 0})
		self.assertEqual(c.post('/api/notifications/read', json={}).json['unread'], 0)


	def test_bad_uploads_and_invite_revoke(self):
		'''Uploads without a file are 400; invites revoke over HTTP.'''

		t = self.task()
		self.assertEqual(self.client.post(f'/api/tasks/{t["id"]}/attachments', data={}, content_type='multipart/form-data').status_code, 400)
		self.assertEqual(self.client.put('/api/me/avatar', data={}, content_type='multipart/form-data').status_code, 400)
		inv = self.client.post('/api/invites').json
		self.assertEqual(self.client.delete(f'/api/invites/{inv["id"]}').json, {'ok': True})


	def test_settings_test_failure(self):
		'''A failing test push is reported as a 400.'''

		def boom(*args, **kwargs):
			raise OSError('unreachable')

		notify.send = boom
		r = self.client.post('/api/settings/test')
		self.assertEqual(r.status_code, 400)
		self.assertIn('unreachable', r.json['error'])


	def test_settings_admin_only(self):
		'''Settings never return the ntfy token and need an admin.'''

		self.assertEqual(self.client.put('/api/settings', json={'ntfy_token': 'secret', 'ntfy_topic': 't'}).status_code, 200)
		conf = self.client.get('/api/settings').json
		self.assertEqual((conf['ntfy_token'], conf['token_set'], conf['ntfy_topic']), ('', True, 't'))
		self.assertEqual(self.client.post('/api/settings/test').json, {'ok': True})
		self.assertEqual(self.pushes[-1]['title'], 'kanopy test')
		self.assertEqual(app.app.test_client().put('/api/settings', json={}, headers={'X-Key': self.key}).status_code, 403)


	def test_revision(self):
		'''Writes bump the revision, reads and failed writes do not, and every response carries it.'''

		start = self.client.get('/api/revision').json['revision']
		r     = self.client.post('/api/projects', json={'path': 'R'})
		self.assertEqual(int(r.headers['X-Revision']), start + 1)
		self.client.get('/api/projects')
		self.client.post('/api/projects', json={'path': ''})
		self.assertEqual(self.client.get('/api/revision').json['revision'], start + 1)
		self.rpc('tools/call', {'name': 'list_tasks', 'arguments': {}})
		self.assertEqual(self.client.get('/api/revision').json['revision'], start + 1)
		self.rpc('tools/call', {'name': 'save_task', 'arguments': {'project': 'Ann', 'title': 'x'}})
		self.assertEqual(self.client.get('/api/revision').json['revision'], start + 2)


	def test_description_clash(self):
		'''A web save whose starting text is out of date is refused with the newer text; a matching one saves.'''

		t = self.task(description='v1')
		kanban.task_update(t['id'], {'description': 'v2 from an agent'})
		r = self.client.patch(f'/api/tasks/{t["id"]}', json={'description': 'mine', 'description_was': 'v1'})
		self.assertEqual((r.status_code, r.json['description']), (409, 'v2 from an agent'))
		self.assertEqual(kanban.task(t['id'])['description'], 'v2 from an agent')
		r = self.client.patch(f'/api/tasks/{t["id"]}', json={'description': 'mine', 'description_was': 'v2 from an agent'})
		self.assertEqual(r.json['description'], 'mine')
		self.assertEqual(self.client.patch(f'/api/tasks/{t["id"]}', json={'description': 'forced'}).json['description'], 'forced')


	def test_context(self):
		'''The session hook endpoint returns the briefing as plain text.'''

		self.client.post('/api/projects', json={'path': 'Here', 'folder': '/here'})
		r = self.client.get('/api/context?cwd=/here/sub')
		self.assertEqual(r.mimetype, 'text/plain')
		self.assertTrue(r.get_data(as_text=True).startswith('kanopy project for this directory: Here'))


class TestMcpEndpoint(AppBase):
	'''MCP over HTTP.'''

	def test_needs_token(self):
		'''The MCP endpoint takes API tokens only, never cookies.'''

		self.assertEqual(self.client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}).status_code, 401)
		self.assertEqual(self.rpc('ping', key='bk_wrong').status_code, 401)
		self.assertEqual(self.rpc('ping').json['result'], {})


	def test_notification_is_202(self):
		'''JSON-RPC notifications get an empty 202.'''

		r = self.rpc('notifications/initialized', rid=None)
		self.assertEqual((r.status_code, r.data), (202, b''))


	def test_agent_tag(self):
		'''Comments from a non-admin's agent are tagged with the user; the agent name is sanitized for history.'''

		self.act(self.ann)
		t = self.task('Ann', 'x')
		self.act(None)
		self.rpc('tools/call', {'name': 'comment', 'arguments': {'id': t['id'], 'text': 'hi'}}, agent='Clau de!!')
		task = kanban.task(t['id'])
		self.assertEqual(task['comments'][0]['author'], 'claude (ann)')
		self.assertEqual(task['history'][0]['agent'], 'claude')
		self.assertIsNone(kanban.AGENT.get())


	def test_session_ids(self):
		'''initialize hands out a session id that later requests send back, so two sessions claim as different workers.'''

		sid = self.rpc('initialize', {'protocolVersion': '2025-06-18'}).headers['Mcp-Session-Id']
		self.assertRegex(sid, r'^[0-9a-f]{6}$')
		self.act(self.ann)
		t = self.task('Ann', 'x')
		self.act(None)
		call = lambda s, take=False: self.client.post('/mcp', json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'save_task', 'arguments': {'id': t['id'], 'status': 'doing', 'take': take}}}, headers={'X-Key': self.key, 'X-Agent': 'claude', 'Mcp-Session-Id': s}).json['result']
		self.assertIn(f'by:claude(ann)#{sid}', call(sid)['content'][0]['text'])
		self.assertTrue(call('other1')['isError'])
		self.assertEqual(kanban.task(t['id'])['claimed_by'], f'claude (ann) #{sid}')


	def test_read_only_token(self):
		'''A read-only token can read over REST and MCP but every write is refused.'''

		ro   = accounts.token_create(self.ann, 'reader', read_only=True)['token']
		anon = app.app.test_client()
		self.assertEqual(anon.get('/api/projects', headers={'X-Key': ro}).status_code, 200)
		r = anon.post('/api/projects', json={'path': 'Nope'}, headers={'X-Key': ro})
		self.assertEqual((r.status_code, r.json['error']), (403, 'this API token is read-only'))
		out = self.rpc('tools/call', {'name': 'save_task', 'arguments': {'project': 'Ann', 'title': 'x'}}, key=ro).json['result']
		self.assertTrue(out['isError'])
		self.assertEqual(len(self.rpc('tools/list', key=ro).json['result']['tools']), 5)
		self.assertTrue(self.client.get('/api/tokens').json is not None)
		self.assertEqual(accounts.tokens(self.ann)[-1]['read_only'], 1)


	def test_tools_list_and_call(self):
		'''tools/list and tools/call work end to end as the token's user.'''

		self.assertIn('save_task', [t['name'] for t in self.rpc('tools/list').json['result']['tools']])
		out = self.rpc('tools/call', {'name': 'save_task', 'arguments': {'project': 'Ann', 'title': 'from mcp'}}).json['result']['content'][0]['text']
		self.assertIn('[Ann] from mcp', out)
		self.act(self.ann)
		self.assertEqual(kanban.projects()[0]['role'], kanban.OWN)



if __name__ == '__main__':
	unittest.main()
