#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# app.py

import logging
import os
import re
import threading
import time
import urllib.parse

from pathlib import Path

try:
	from flask import Flask, Response, g, request, send_from_directory
except ImportError:
	raise ImportError('missing flask library (pip install flask)')

try:
	from werkzeug.middleware.proxy_fix import ProxyFix
except ImportError:
	raise ImportError('missing werkzeug library (pip install flask)')

try:
	from waitress import serve
except ImportError:
	raise ImportError('missing waitress library (pip install waitress)')

import accounts
import kanban
import mcp_server
import notify


COOKIE = 'kb_session'
FAILS       = {} # client ip -> monotonic times of recent failed sign-ins
FAILS_LOCK  = threading.Lock()
FAIL_LIMIT  = 5
FAIL_WINDOW = 900 # seconds
HOST   = os.environ.get('KANOPY_HOST', '127.0.0.1')
KEY    = os.environ.get('KANOPY_KEY', '') # old shared key, imported as the admin's API token
OPEN   = ('/api/login', '/api/logout', '/api/setup', '/api/signup', '/api/invites/check') # API paths that work signed out
PORT   = int(os.environ.get('KANOPY_PORT', '8000'))
STATIC = Path(__file__).parent / 'static'
URL    = os.environ.get('KANOPY_URL', '').rstrip('/') # public address when behind a reverse proxy

notify.BASE_URL = URL or f'http://{HOST}:{PORT}'

app = Flask(__name__, static_folder=None)
app.config['MAX_CONTENT_LENGTH'] = 4 * kanban.MAX_FILE

if URL:
	app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1) # the proxy's X-Forwarded-For/-Proto give the real client ip (sign-in limit) and https (Secure cookie)


@app.get('/api/invites')
def api_invites() -> dict:
	'''List invites (admin).'''

	return {'invites': accounts.invites(g.user)}


@app.get('/api/invites/check/<token>')
def api_invite_check(token: str) -> dict:
	'''Say whether an invite link can still be used.'''

	accounts.invite_check(token)

	return {'ok': True}


@app.post('/api/invites')
def api_invite_create() -> dict:
	'''Create a single-use invite link (admin).'''

	invite = accounts.invite_create(g.user)

	return dict(invite, url=f'{notify.BASE_URL}/#/invite/{invite["token"]}')


@app.delete('/api/invites/<int:invite_id>')
def api_invite_delete(invite_id: int) -> dict:
	'''Revoke an unused invite (admin).'''

	accounts.invite_delete(g.user, invite_id)

	return {'ok': True}


@app.post('/api/login')
def api_login() -> Response:
	'''Sign in and set the session cookie.'''

	body = request.get_json(force=True) or {}
	ip   = request.remote_addr or ''
	now  = time.monotonic()

	with FAILS_LOCK:
		FAILS[ip] = [t for t in FAILS.get(ip, []) if now - t < FAIL_WINDOW]
		if len(FAILS[ip]) >= FAIL_LIMIT:
			raise PermissionError('too many failed sign-ins, try again in a few minutes')

	try:
		token = accounts.login(body.get('username'), body.get('password'))
	except PermissionError:
		with FAILS_LOCK:
			FAILS[ip].append(now)
		raise

	return session_response(token)


@app.post('/api/logout')
def api_logout() -> Response:
	'''End the session and clear the cookie.'''

	accounts.session_delete(request.cookies.get(COOKIE))
	resp = Response('{"ok": true}', mimetype='application/json')
	resp.delete_cookie(COOKIE)

	return resp


@app.get('/api/me')
def api_me() -> dict:
	'''Return the signed-in user with their unread notification count.'''

	return dict(g.user, unread=accounts.notifications(g.user)['unread'])


@app.put('/api/me/avatar')
def api_me_avatar() -> dict:
	'''Set the signed-in user's picture from an uploaded image (field "file").'''

	f = request.files.get('file')

	if not f:
		raise ValueError('no image uploaded')

	return accounts.avatar_set(g.user, f.read(), f.mimetype)


@app.delete('/api/me/avatar')
def api_me_avatar_delete() -> dict:
	'''Remove the signed-in user's picture.'''

	return accounts.avatar_set(g.user, b'', '')


@app.patch('/api/me')
def api_me_update() -> dict:
	'''Update the signed-in user's password, ntfy topic or notification toggles.'''

	return accounts.user_update(g.user, request.get_json(force=True) or {})


@app.get('/api/notifications')
def api_notifications() -> dict:
	'''Return the signed-in user's inbox.'''

	return accounts.notifications(g.user)


@app.post('/api/notifications/read')
def api_notifications_read() -> dict:
	'''Mark notifications read (all when no ids are given).'''

	accounts.notifications_read(g.user, (request.get_json(force=True) or {}).get('ids'))

	return accounts.notifications(g.user)


@app.get('/api/projects/<int:project_id>/shares')
def api_shares(project_id: int) -> dict:
	'''List who a project is shared with.'''

	return {'shares': accounts.shares(project_id)}


@app.post('/api/projects/<int:project_id>/shares')
def api_share_add(project_id: int) -> dict:
	'''Share a project with a user or team.'''

	body = request.get_json(force=True) or {}

	return {'shares': accounts.share_add(project_id, body.get('role', 'viewer'), body.get('username'), body.get('team'))}


@app.delete('/api/shares/<int:share_id>')
def api_share_remove(share_id: int) -> dict:
	'''Remove a share.'''

	return {'shares': accounts.share_remove(share_id)}


@app.get('/api/setup')
def api_setup_needed() -> dict:
	'''Say whether the first admin still has to be created.'''

	return {'needed': accounts.setup_needed()}


@app.post('/api/setup')
def api_setup() -> Response:
	'''Create the first admin on an empty board and sign in.'''

	body  = request.get_json(force=True) or {}
	token = accounts.setup(body.get('username'), body.get('password'))

	if KEY:
		accounts.token_import(accounts.user_by_session(token)['id'], 'shared key', KEY)

	return session_response(token)


@app.post('/api/signup')
def api_signup() -> Response:
	'''Create an account from an invite link and sign in.'''

	body = request.get_json(force=True) or {}

	return session_response(accounts.signup(body.get('token', ''), body.get('username'), body.get('password')))


@app.get('/api/teams')
def api_teams() -> dict:
	'''List the signed-in user's teams.'''

	return {'teams': accounts.teams(g.user)}


@app.post('/api/teams')
def api_team_create() -> dict:
	'''Create a team.'''

	return {'teams': accounts.team_create(g.user, (request.get_json(force=True) or {}).get('name'))}


@app.delete('/api/teams/<int:team_id>')
def api_team_delete(team_id: int) -> dict:
	'''Delete a team.'''

	return {'teams': accounts.team_delete(g.user, team_id)}


@app.post('/api/teams/<int:team_id>/members')
def api_team_member_add(team_id: int) -> dict:
	'''Add a member by username.'''

	return {'teams': accounts.team_member_add(g.user, team_id, (request.get_json(force=True) or {}).get('username'))}


@app.delete('/api/teams/<int:team_id>/members/<int:user_id>')
def api_team_member_remove(team_id: int, user_id: int) -> dict:
	'''Remove a member, or leave.'''

	return {'teams': accounts.team_member_remove(g.user, team_id, user_id)}


@app.patch('/api/teams/<int:team_id>/members/<int:user_id>')
def api_team_member_role(team_id: int, user_id: int) -> dict:
	'''Promote or demote a team owner.'''

	return {'teams': accounts.team_member_role(g.user, team_id, user_id, (request.get_json(force=True) or {}).get('owner'))}


@app.get('/api/tokens')
def api_tokens() -> dict:
	'''List the signed-in user's API tokens.'''

	return {'tokens': accounts.tokens(g.user)}


@app.post('/api/tokens')
def api_token_create() -> dict:
	'''Create an API token; the secret is only shown in this response.'''

	return accounts.token_create(g.user, (request.get_json(force=True) or {}).get('name'))


@app.delete('/api/tokens/<int:token_id>')
def api_token_delete(token_id: int) -> dict:
	'''Revoke an API token.'''

	accounts.token_delete(g.user, token_id)

	return {'ok': True}


@app.get('/api/users')
def api_users() -> dict:
	'''List users (for assigning, sharing and mentions).'''

	return {'users': accounts.users()}


@app.get('/api/users/<int:user_id>/avatar')
def api_user_avatar(user_id: int) -> Response:
	'''Return a user's picture.'''

	data, mime = accounts.avatar_get(user_id)

	return Response(data, mimetype=mime, headers={'Cache-Control': 'private, max-age=86400', 'X-Content-Type-Options': 'nosniff'})


@app.delete('/api/users/<int:user_id>')
def api_user_delete(user_id: int) -> dict:
	'''Delete a user (admin).'''

	accounts.user_delete(g.user, user_id)

	return {'users': accounts.users()}


@app.post('/api/users/<int:user_id>/password')
def api_user_reset(user_id: int) -> dict:
	'''Set a user's password (admin).'''

	accounts.user_reset(g.user, user_id, (request.get_json(force=True) or {}).get('password', ''))

	return {'ok': True}


@app.post('/api/tasks/<int:task_id>/attachments')
def api_attachment_add(task_id: int) -> dict:
	'''Attach one or more uploaded files to a task.'''

	files = request.files.getlist('file')

	if not files:
		raise ValueError('no file uploaded')

	for f in files:
		kanban.attachment_add(task_id, f.filename, f.mimetype, f.read())

	return kanban.task(task_id)


@app.delete('/api/attachments/<int:attachment_id>')
def api_attachment_delete(attachment_id: int) -> dict:
	'''Delete an attachment.'''

	return kanban.attachment_delete(attachment_id)


@app.get('/api/attachments/<int:attachment_id>')
def api_attachment_get(attachment_id: int) -> Response:
	'''Return an attachment's contents.'''

	row = kanban.attachment_get(attachment_id)

	return Response(row['data'], mimetype=row['mime'], headers={'Content-Disposition': f"attachment; filename*=UTF-8''{urllib.parse.quote(row['name'])}", 'X-Content-Type-Options': 'nosniff'})


@app.post('/api/tasks/bulk')
def api_bulk() -> dict:
	'''Apply one change to many tasks.'''

	body = request.get_json(force=True) or {}

	return {'changed': kanban.bulk([int(i) for i in body.get('ids', [])], body)}


@app.post('/api/tasks/<int:task_id>/comments')
def api_comment(task_id: int) -> dict:
	'''Add a comment to a task from the web UI.'''

	body = (request.get_json(force=True) or {}).get('body', '')
	task = kanban.comment_add(task_id, g.user['username'], body)
	notify.task_event(task, task, body)

	return task


@app.get('/api/context')
def api_context() -> Response:
	'''Plain-text briefing for an agent session: the project mapped to ?cwd= and its open tasks, empty when none matches.'''

	return Response(mcp_server.context(request.args.get('cwd', '')), mimetype='text/plain')


@app.post('/api/labels')
def api_label_create() -> dict:
	'''Create a personal label, or a global one when the body has global (admin only).'''

	body = request.get_json(force=True) or {}
	kanban.label_create(body.get('name', ''), body.get('color'), bool(body.get('global')))

	return {'ok': True}


@app.delete('/api/labels')
def api_label_delete() -> dict:
	'''Delete a label everywhere.'''

	kanban.label_delete(request.args.get('name', ''))

	return {'ok': True}


@app.patch('/api/labels')
def api_label_update() -> dict:
	'''Rename (merging into an existing label) and/or recolor a label.'''

	body = request.get_json(force=True) or {}
	kanban.label_update(body.get('name', ''), body.get('new_name'), body.get('color'))

	return {'ok': True}


@app.get('/api/labels')
def api_labels() -> dict:
	'''Return the global labels and the user's own, with usage counts.'''

	return {'labels': kanban.labels()}


@app.post('/api/tasks/<int:task_id>/links')
def api_link_add(task_id: int) -> dict:
	'''Link a task to another task.'''

	body = request.get_json(force=True) or {}

	return kanban.link_add(task_id, body.get('kind'), int(body.get('other') or 0))


@app.delete('/api/tasks/<int:task_id>/links')
def api_link_remove(task_id: int) -> dict:
	'''Remove a link between two tasks.'''

	return kanban.link_remove(task_id, request.args.get('kind'), int(request.args.get('other') or 0))


@app.get('/api/projects/<int:project_id>/activity')
def api_project_activity(project_id: int) -> dict:
	'''Return task history across a project and its sub-projects, newest first, a page at a time.'''

	return {'events': kanban.activity(project_id, request.args.get('before', 0, type=int))}


@app.post('/api/projects')
def api_project_create() -> dict:
	'''Create a project by path.'''

	body = request.get_json(force=True) or {}

	return kanban.project_save(body.get('path'), description=body.get('description'), folder=body.get('folder'))


@app.delete('/api/projects/<int:project_id>')
def api_project_delete(project_id: int) -> dict:
	'''Delete an empty project.'''

	kanban.project_delete(project_id)

	return {'ok': True}


@app.post('/api/projects/order')
def api_project_order() -> dict:
	'''Set the parent and order of a group of sibling projects.'''

	body = request.get_json(force=True) or {}
	kanban.project_order(body.get('parent_id'), [int(i) for i in body.get('ids', [])])

	return {'ok': True}


@app.patch('/api/projects/<int:project_id>')
def api_project_update(project_id: int) -> dict:
	'''Rename/move, describe, archive or color a project.'''

	body = request.get_json(force=True) or {}

	return kanban.project_save(body.get('path'), project_id, body.get('description'), body.get('archived'), body.get('color'), body.get('folder'))


@app.get('/api/projects')
def api_projects() -> dict:
	'''Return every project with paths and open counts.'''

	return {'projects': kanban.projects()}


@app.post('/api/reorder')
def api_reorder() -> dict:
	'''Set the status and order of the tasks in one column.'''

	body = request.get_json(force=True) or {}
	kanban.reorder(body.get('status'), [int(i) for i in body.get('ids', [])])

	return {'ok': True}


@app.get('/api/repeats')
def api_repeats() -> dict:
	'''Return future dates of repeating tasks between from and to (YYYY-MM-DD), for the calendar.'''

	return {'dates': kanban.repeat_dates(request.args.get('from', ''), request.args.get('to', ''))}


@app.get('/api/search')
def api_search() -> dict:
	'''Search task titles, descriptions and comments.'''

	args     = request.args
	statuses = [v for v in args.get('status', '').split(',') if v]
	fields   = [v for v in args.get('fields', '').split(',') if v]

	return {'results': kanban.search(args.get('q', ''), args.get('project', type=int), statuses, args.get('label') or None, args.get('priority', 0, type=int), args.get('due', ''), fields, args.get('sort', 'updated'), args.get('assignee') or None)[:300]}


@app.get('/api/settings')
def api_settings() -> dict:
	'''Return notification settings; the ntfy token is never sent back, only whether one is set.'''

	accounts.need_admin(g.user)
	conf = notify.settings()

	return dict(conf, ntfy_token='', token_set=bool(conf['ntfy_token']))


@app.put('/api/settings')
def api_settings_save() -> dict:
	'''Save notification settings.'''

	accounts.need_admin(g.user)
	notify.save(request.get_json(force=True) or {})
	housekeep()

	return api_settings()


@app.post('/api/settings/test')
def api_settings_test() -> dict:
	'''Send a test notification with the saved settings.'''

	accounts.need_admin(g.user)

	try:
		notify.send('kanopy test', 'Notifications are working.', tags='tada')
	except Exception as exc:
		raise ValueError(f'ntfy push failed: {exc}')

	return {'ok': True}


@app.get('/api/tasks/<int:task_id>')
def api_task(task_id: int) -> dict:
	'''Return a full task.'''

	return kanban.task(task_id)


@app.post('/api/tasks')
def api_task_create() -> dict:
	'''Create a task.'''

	task = kanban.task_create(request.get_json(force=True) or {})
	notify.task_event(None, task)

	return task


@app.delete('/api/tasks/<int:task_id>')
def api_task_delete(task_id: int) -> dict:
	'''Delete a task.'''

	kanban.task_delete(task_id)

	return {'ok': True}


@app.patch('/api/tasks/<int:task_id>')
def api_task_update(task_id: int) -> dict:
	'''Update task fields.'''

	before = kanban.task(task_id)
	after  = kanban.task_update(task_id, request.get_json(force=True) or {})
	notify.task_event(before, after)

	return after


@app.get('/api/tasks')
def api_tasks() -> dict:
	'''Return task cards for all projects or one project subtree.'''

	return {'tasks': kanban.tasks(request.args.get('project', type=int))}


@app.before_request
def auth():
	'''Identify the user from an API token (X-Key header) or the session cookie; the MCP endpoint takes tokens only. Everything under /api except OPEN needs a user.'''

	g.user = None
	path   = request.path

	if not (path.startswith('/api/') or path == '/mcp'):
		return

	if path.startswith('/api/') and request.method not in ('GET', 'HEAD', 'OPTIONS') and not request.headers.get('X-Key'): # cookie-signed writes only
		origin = request.headers.get('Origin')
		if (origin and origin != request.host_url.rstrip('/')) or request.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'):
			return {'error': 'cross-site request refused'}, 403
		if request.content_length and request.mimetype not in ('application/json', 'multipart/form-data'):
			return {'error': 'requests must send JSON'}, 415

	g.user = accounts.user_by_token(request.headers.get('X-Key', '')) or (None if path == '/mcp' else accounts.user_by_session(request.cookies.get(COOKIE)))

	if not g.user and not path.startswith(OPEN):
		return {'error': 'sign in first'}, 401

	kanban.ACTOR.set(g.user)


@app.teardown_request
def auth_end(exc: Exception = None):
	'''Forget the signed-in user and agent once the request is done.'''

	kanban.ACTOR.set(None)
	kanban.AGENT.set(None)


def housekeep():
	'''Write the daily database backup and archive tasks done longer than the archive_days setting.'''

	kanban.backup()
	days = int(notify.settings()['archive_days'])

	if days:
		kanban.archive_done(days)


def housekeep_loop():
	'''Run housekeep() every hour forever; meant for a daemon thread.'''

	while True:
		try:
			housekeep()
		except Exception as exc:
			logging.warning('housekeeping failed: %s', exc)
		time.sleep(3600)


@app.errorhandler(LookupError)
@app.errorhandler(PermissionError)
@app.errorhandler(ValueError)
def error(exc: Exception) -> tuple:
	'''Return lookup errors as 404, permission errors as 403 and validation errors as 400.'''

	return {'error': str(exc)}, 404 if isinstance(exc, LookupError) else 403 if isinstance(exc, PermissionError) else 400


@app.get('/')
def index():
	'''Serve the single page app.'''

	return (STATIC / 'index.html').read_text().replace('{{BASE}}', notify.BASE_URL)


@app.post('/mcp')
def mcp() -> tuple:
	'''MCP endpoint (streamable HTTP, JSON responses, stateless). The X-Agent header tags comments.'''

	agent = re.sub(r'[^a-z0-9_-]', '', request.headers.get('X-Agent', 'agent').lower())[:32] or 'agent'
	kanban.AGENT.set(agent)
	agent = agent if g.user['is_admin'] else f'{agent} ({g.user["username"]})'
	reply = mcp_server.handle(request.get_json(force=True) or {}, agent)

	return (reply, 200) if reply else ('', 202)


def session_response(token: str) -> Response:
	'''
	Return {ok: true} with the session cookie set.

	:param token: Raw session token
	'''

	resp = Response('{"ok": true}', mimetype='application/json')
	resp.set_cookie(COOKIE, token, max_age=accounts.SESSION_DAYS * 86400, httponly=True, samesite='Lax', secure=request.is_secure)

	return resp


@app.get('/<path:name>')
def static_file(name: str):
	'''Serve a static asset.'''

	return send_from_directory(STATIC, name)



if __name__ == '__main__':
	kanban.init()
	admin = next((u for u in accounts.users() if u['is_admin']), None)
	if KEY and admin:
		accounts.token_import(admin['id'], 'shared key', KEY)
	threading.Thread(target=notify.loop, daemon=True).start()
	threading.Thread(target=housekeep_loop, daemon=True).start()
	serve(app, host=HOST, port=PORT)
