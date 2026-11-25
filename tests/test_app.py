#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# tests/test_app.py

import io
import json
import re
import threading
import time
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
		self.assertEqual(self.client.post('/api/projects', json={'path': 'Z'}, headers={'X-Key': 'bogus', 'Origin': 'https://evil.example'}).status_code, 401) # a bad key never falls back to the cookie


	def test_rate_limit_holds_under_parallel_guesses(self):
		'''Guesses fired at once still get only FAIL_LIMIT password checks.'''

		calls, real = [], accounts.login
		def slow_wrong(username, password):
			calls.append(1)
			time.sleep(0.2)
			raise PermissionError('wrong username or password')
		accounts.login = slow_wrong
		try:
			threads = [threading.Thread(target=lambda: app.app.test_client().post('/api/login', json={'username': 'ann', 'password': 'x'})) for _ in range(12)]
			for t in threads:
				t.start()
			for t in threads:
				t.join()
		finally:
			accounts.login = real
		self.assertEqual(len(calls), app.FAIL_LIMIT)


	def test_malformed_bodies_are_400(self):
		'''Bodies of the wrong shape or type get a clean 400 or tool error, never a 500.'''

		self.assertEqual(self.client.post('/api/tasks', json=[1]).status_code, 400)
		self.assertEqual(app.app.test_client().post('/api/login', json={'username': 'ann', 'password': 5}).status_code, 400)
		r = self.client.post('/mcp', json=[{'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}], headers={'X-Key': self.key})
		self.assertEqual((r.status_code, r.json['error']['code']), (400, -32600))
		self.assertTrue(self.rpc('tools/call', {'name': 'save_task', 'arguments': {'project': 'P', 'title': 't', 'add_labels': [5]}}).json['result']['isError'])
		self.assertTrue(self.rpc('tools/call', {'name': 'list_tasks', 'arguments': [1]}).json['result']['isError'])
		self.assertEqual(self.client.patch('/api/me', json={'ntfy_topic': 5}).status_code, 400)
		self.assertEqual(self.client.post('/api/tasks', json={'project_id': 1, 'title': 't', 'labels': [1]}).status_code, 400)
		self.assertEqual(self.client.post('/api/tasks', json={'project_id': [1], 'title': 't'}).status_code, 400)
		self.assertEqual(self.client.post('/api/tasks', json={'project_id': 1, 'title': 't', 'description': {'a': 1}}).status_code, 400)
		self.assertEqual(self.client.get('/api/tasks/99999999999999999999').status_code, 400)
		self.assertTrue(self.rpc('tools/call', {'name': 'get_task', 'arguments': {'ids': [10**30]}}).json['result']['isError'])
		self.assertTrue(self.rpc('tools/call', {'name': 'comment', 'arguments': {'id': 424242, 'text': ' '}}).json['result']['isError'])
		self.assertEqual(self.rpc('tools/call', {'name': 'save_task', 'arguments': {'ids': [1, 2], 'add_labels': [5]}}).json['result']['content'][0]['text'].count(' error: '), 2) # each id reports its own error


	def test_duplicate_is_409(self):
		'''A create that loses a race to the same name hits the unique rule and gets a 409, not a 500.'''

		def insert(user, name): # no name check, as when another request inserted it between the check and the insert
			with kanban.connect() as db:
				db.execute('INSERT INTO teams (name, created) VALUES (?, ?)', (name, kanban.now()))

		real, accounts.team_create = accounts.team_create, insert
		try:
			insert(None, 'crew')
			r = self.client.post('/api/teams', json={'name': 'crew'})
		finally:
			accounts.team_create = real
		self.assertEqual((r.status_code, r.json['error']), (409, 'that already exists'))


	def test_health(self):
		'''/health answers ok without signing in, and 503 when the database is unreachable.'''

		r = app.app.test_client().get('/health')
		self.assertEqual((r.status_code, r.get_data(as_text=True)), (200, 'ok'))
		real, kanban.connect = kanban.connect, lambda *a, **k: (_ for _ in ()).throw(OSError('down'))
		try:
			self.assertEqual(app.app.test_client().get('/health').status_code, 503)
		finally:
			kanban.connect = real


	def test_export_download(self):
		'''/api/export downloads JSON named after the project, or everything as kanopy-export.json.'''

		pid = self.client.post('/api/projects', json={'path': 'Code/My App'}).json['id']
		r   = self.client.get(f'/api/export?project={pid}')
		self.assertEqual((r.status_code, r.headers['Content-Disposition']), (200, 'attachment; filename="kanopy-code-my-app.json"'))
		self.assertEqual([p['path'] for p in r.json['projects']], ['Code/My App'])
		self.assertIn('kanopy-export.json', self.client.get('/api/export').headers['Content-Disposition'])
		self.assertEqual(app.app.test_client().get('/api/export').status_code, 401)


	def test_expired_fail_entries_dropped(self):
		'''Addresses whose failed sign-ins have all expired are forgotten on the next sign-in.'''

		app.FAILS['192.0.2.9'] = [time.monotonic() - app.FAIL_WINDOW - 1]
		app.app.test_client().post('/api/login', json={'username': 'ann', 'password': 'wrong'})
		self.assertNotIn('192.0.2.9', app.FAILS)


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


class TestOpenApi(unittest.TestCase):
	'''static/openapi.json matches the routes the app serves.'''

	def setUp(self):
		'''Load the spec and the app's routes as (path, method) pairs in OpenAPI form.'''

		with open(app.STATIC / 'openapi.json') as f:
			self.spec = json.load(f)
		self.documented = {(path, method.upper()) for path, ops in self.spec['paths'].items() for method in ops}
		self.served     = {(re.sub(r'<(?:\w+:)?(\w+)>', r'{\1}', rule.rule), method) for rule in app.app.url_map.iter_rules() if rule.rule.startswith(('/api/', '/mcp', '/health')) for method in rule.methods - {'HEAD', 'OPTIONS'}}


	def test_every_route_documented(self):
		'''Every route is in the spec, and the spec lists nothing the app does not serve.'''

		self.assertEqual(sorted(self.served - self.documented), [], 'routes missing from static/openapi.json')
		self.assertEqual(sorted(self.documented - self.served), [], 'static/openapi.json lists routes the app does not serve')


	def test_security_matches_auth(self):
		'''Routes marked cookie-only are the ones API tokens are refused on, and routes marked open are the ones that work signed out.'''

		for path, ops in self.spec['paths'].items():
			sample = re.sub(r'\{\w+\}', '1', path)
			for method, operation in ops.items():
				security = operation.get('security')
				with self.subTest(path=path, method=method):
					self.assertEqual(security == [{'session': []}], bool(app.TOKEN_DENY.match(sample)) and not sample.startswith(app.OPEN))
					if sample.startswith('/api/'):
						self.assertEqual(security == [], sample.startswith(app.OPEN))


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
		slim = c.get(f'/api/tasks?project={pid}&brief=1').json['tasks']
		self.assertEqual((len(slim), 'title' in slim[0], 'description' in slim[0]), (2, True, False))
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
		self.assertEqual(set(c.get(f'/api/users/{self.ann["id"]}/footprint').json), {'projects', 'tasks', 'shared', 'tags'})
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


	def test_test_push(self):
		'''Anyone can send a test push to their own topic; no topic or a failing push is a 400.'''

		self.assertEqual(self.client.post('/api/me/test').status_code, 400) # no topic yet
		self.client.patch('/api/me', json={'ntfy_topic': 'root-t'})
		self.assertEqual(self.client.post('/api/me/test').json, {'ok': True})
		self.assertEqual((self.pushes[-1]['title'], self.pushes[-1]['topic']), ('kanopy test', 'root-t'))

		def boom(*args, **kwargs):
			raise OSError('unreachable')

		notify.send = boom
		r = self.client.post('/api/me/test')
		self.assertEqual(r.status_code, 400)
		self.assertIn('unreachable', r.json['error'])


	def test_settings_admin_only(self):
		'''Board times need an admin, and the old board-wide ntfy keys are ignored.'''

		self.assertEqual(self.client.put('/api/settings', json={'due_time': '07:15', 'ntfy_topic': 't'}).status_code, 200)
		conf = self.client.get('/api/settings').json
		self.assertEqual(conf['due_time'], '07:15')
		self.assertNotIn('ntfy_topic', conf)
		self.assertEqual(app.app.test_client().put('/api/settings', json={}, headers={'X-Key': self.key}).status_code, 403)


	def test_version_check(self):
		'''Admins see the running version against the newest GitHub release; the answer is cached, failures stay quiet, and the check can be turned off.'''

		calls, reply = [], {'tag_name': 'v99.0.0'}
		class Resp(io.BytesIO):
			def __enter__(self): return self
			def __exit__(self, *a): pass
		def fake(req, timeout):
			calls.append(req.full_url)
			if isinstance(reply, Exception):
				raise reply
			return Resp(json.dumps(reply).encode())
		real, app.urllib.request.urlopen = app.urllib.request.urlopen, fake
		try:
			app.LATEST.update(tag=None, at=-1e9)
			v = self.client.get('/api/version').json
			self.assertEqual((v['current'], v['latest'], v['outdated']), (app.__version__, 'v99.0.0', True))
			self.client.get('/api/version')
			self.assertEqual(len(calls), 1) # cached
			app.LATEST.update(tag=None, at=-1e9)
			reply = {'tag_name': 'v' + app.__version__}
			self.assertFalse(self.client.get('/api/version').json['outdated'])
			app.LATEST.update(tag=None, at=-1e9)
			reply = OSError('offline')
			with self.assertLogs(level='WARNING'):
				self.assertEqual(self.client.get('/api/version').json['latest'], None)
			app.LATEST.update(tag=None, at=-1e9)
			app.UPDATE_CHECK = False
			before = len(calls)
			self.assertEqual(self.client.get('/api/version').json, {'current': app.__version__, 'latest': None, 'outdated': False, 'check': False})
			self.assertEqual(len(calls), before)
		finally:
			app.urllib.request.urlopen, app.UPDATE_CHECK = real, True
			app.LATEST.update(tag=None, at=-1e9)
		self.assertEqual(app.app.test_client().get('/api/version', headers={'X-Key': self.key}).status_code, 403)


	def test_revision(self):
		'''Writes bump the revision, reads and failed writes do not, and every response carries it.'''

		start = self.client.get('/api/revision').json['revision']
		r     = self.client.post('/api/projects', json={'path': 'R'})
		self.assertEqual(int(r.headers['X-Revision']), start + 1)
		self.assertEqual(int(r.headers['X-Revision-Was']), start)
		self.assertNotIn('X-Revision-Was', self.client.get('/api/projects').headers)
		self.client.post('/api/projects', json={'path': ''})
		self.assertEqual(self.client.get('/api/revision').json['revision'], start + 1)
		self.rpc('tools/call', {'name': 'list_tasks', 'arguments': {}})
		self.assertEqual(self.client.get('/api/revision').json['revision'], start + 1)
		self.rpc('tools/call', {'name': 'labels', 'arguments': {}})
		self.rpc('tools/call', {'name': 'inbox', 'arguments': {}})
		self.rpc('tools/call', {'name': 'save_task', 'arguments': {'id': 99999, 'title': 'x'}}) # fails: no such task
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
		'''Tool calls take API tokens only, never cookies; without a token only the handshake and the tool list answer, and the board's instructions stay private.'''

		call = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'list_projects', 'arguments': {}}}
		self.assertEqual(self.client.post('/mcp', json=call).status_code, 401)
		self.assertEqual(app.app.test_client().post('/mcp', json=dict(call, method='resources/list')).status_code, 401)
		self.assertEqual(self.rpc('ping', key='bk_wrong').status_code, 401)
		self.assertEqual(self.rpc('ping').json['result'], {})
		anon = app.app.test_client()
		init = anon.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}}).json['result']
		self.assertEqual((init['serverInfo']['name'], 'instructions' in init), ('kanopy', False))
		self.assertIn('instructions', self.rpc('initialize').json['result'])
		self.assertIn('save_task', [t['name'] for t in anon.post('/mcp', json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}).json['result']['tools']])
		self.assertEqual(anon.post('/mcp', json={'jsonrpc': '2.0', 'method': 'notifications/initialized'}).status_code, 202)


	def test_notification_is_202(self):
		'''JSON-RPC notifications get an empty 202.'''

		r = self.rpc('notifications/initialized', rid=None)
		self.assertEqual((r.status_code, r.data), (202, b''))


	def test_agent_tag(self):
		'''Comments from an agent are tagged with its token's user, admins included; the agent name is sanitized for history.'''

		self.act(self.ann)
		t = self.task('Ann', 'x')
		self.act(None)
		self.rpc('tools/call', {'name': 'comment', 'arguments': {'id': t['id'], 'text': 'hi'}}, agent='Clau de!!')
		task = kanban.task(t['id'])
		self.assertEqual(task['comments'][0]['author'], 'claude (ann)')
		self.assertEqual(task['history'][0]['agent'], 'claude')
		self.assertIsNone(kanban.AGENT.get())
		self.rpc('tools/call', {'name': 'comment', 'arguments': {'id': t['id'], 'text': 'hi'}}, key=accounts.token_create(self.root, 'claude')['token'])
		self.assertEqual(kanban.task(t['id'])['comments'][-1]['author'], 'claude (root)')


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

		ro   = accounts.token_create(self.ann, 'reader', 'viewer')['token']
		anon = app.app.test_client()
		self.assertEqual(anon.get('/api/projects', headers={'X-Key': ro}).status_code, 200)
		r = anon.post('/api/projects', json={'path': 'Nope'}, headers={'X-Key': ro})
		self.assertEqual((r.status_code, r.json['error']), (403, 'this API token is read-only'))
		out = self.rpc('tools/call', {'name': 'save_task', 'arguments': {'project': 'Ann', 'title': 'x'}}, key=ro).json['result']
		self.assertTrue(out['isError'])
		self.assertEqual(len(self.rpc('tools/list', key=ro).json['result']['tools']), 5)
		self.assertTrue(self.client.get('/api/tokens').json is not None)
		self.assertEqual(accounts.tokens(self.ann)[-1]['role'], 'viewer')


	def test_limited_token(self):
		'''A token limited to one project and the editor role sees and edits only that project, never above editor, never account endpoints, and cannot make top-level projects.'''

		self.act(self.ann)
		work  = kanban.project_save('Work')['id']
		kanban.project_save('Private')
		inner = self.task('Work/api', 'inner')
		self.act(None)
		key  = accounts.token_create(self.ann, 'scoped', 'editor', [work])['token']
		anon = app.app.test_client()
		self.assertEqual(sorted(p['path'] for p in anon.get('/api/projects', headers={'X-Key': key}).json['projects']), ['Work', 'Work/api'])
		self.assertEqual({p['role'] for p in anon.get('/api/projects', headers={'X-Key': key}).json['projects']}, {kanban.EDIT})
		self.assertIn('[Work/api] new', self.rpc('tools/call', {'name': 'save_task', 'arguments': {'project': 'Work/api', 'title': 'new'}}, key=key).json['result']['content'][0]['text'])
		self.assertTrue(self.rpc('tools/call', {'name': 'save_task', 'arguments': {'project': 'Private', 'title': 'x'}}, key=key).json['result']['isError'])
		self.assertTrue(self.rpc('tools/call', {'name': 'save_task', 'arguments': {'project': 'Elsewhere', 'title': 'x'}}, key=key).json['result']['isError'])
		self.assertEqual(anon.delete(f'/api/projects/{work}', headers={'X-Key': key}).status_code, 403) # owner-only, above the token's role
		self.assertNotIn('save_project', [t['name'] for t in self.rpc('tools/list', key=key).json['result']['tools']])
		r = anon.post('/api/tokens', json={'name': 'escape'}, headers={'X-Key': key})
		self.assertEqual(r.status_code, 403)
		self.assertEqual(anon.get('/api/me', headers={'X-Key': self.key}).status_code, 403) # even a full token stays off account endpoints
		self.assertEqual(self.rpc('tools/call', {'name': 'get_task', 'arguments': {'id': inner['id']}}, key=key).json['result'].get('isError'), None)
		self.assertEqual(accounts.tokens(self.ann)[-1]['projects'], ['Work'])


	def test_token_create_checks(self):
		'''Tokens take only known roles and projects the user can see; an admin's limited token is not an admin.'''

		with self.assertRaises(ValueError):
			accounts.token_create(self.ann, 'x', 'god')
		self.act(self.root)
		theirs = kanban.project_save('RootOnly')['id']
		self.act(None)
		with self.assertRaises(PermissionError):
			accounts.token_create(self.ann, 'x', 'editor', [theirs])
		key  = accounts.token_create(self.root, 'scoped', 'manager', [theirs])['token']
		user = accounts.user_by_token(key)
		self.assertEqual((user['is_admin'], user['token_level']), (0, kanban.MANAGE))
		self.act(user)
		self.assertEqual([p['path'] for p in kanban.projects()], ['RootOnly'])
		with self.assertRaises(PermissionError):
			kanban.need_admin()


	def test_tools_list_and_call(self):
		'''tools/list and tools/call work end to end as the token's user.'''

		self.assertIn('save_task', [t['name'] for t in self.rpc('tools/list').json['result']['tools']])
		out = self.rpc('tools/call', {'name': 'save_task', 'arguments': {'project': 'Ann', 'title': 'from mcp'}}).json['result']['content'][0]['text']
		self.assertIn('[Ann] from mcp', out)
		self.act(self.ann)
		self.assertEqual(kanban.projects()[0]['role'], kanban.OWN)



if __name__ == '__main__':
	unittest.main()
