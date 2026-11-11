#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# accounts.py

import hashlib
import hmac
import re
import secrets

from datetime import datetime, timedelta, timezone

import kanban


INVITE_DAYS  = 7
SESSION_DAYS = 30
USERNAME_RE  = re.compile(r'^[a-z0-9_-]{2,32}$')
PREFS        = ('notify_mention', 'notify_assign', 'notify_activity', 'notify_due')
PUBLIC       = 'id, username, is_admin, ntfy_topic, notify_mention, notify_assign, notify_activity, notify_due, created, avatar_at'
AVATAR_MAX   = 512 * 1024 # bytes; the browser resizes before upload
AVATAR_MIME  = ('image/png', 'image/jpeg', 'image/webp', 'image/gif')
DUMMY_HASH   = 'scrypt$16384$8$1$' + '00' * 16 + '$' + '00' * 32 # checked against for unknown usernames so a miss costs as long as a hit


def avatar_get(user_id: int) -> tuple:
	'''
	Return a user's picture as (bytes, mime) or raise LookupError.

	:param user_id: User id
	'''

	with kanban.connect() as db:
		row = db.execute('SELECT avatar, avatar_mime FROM users WHERE id = ?', (user_id,)).fetchone()

	if not row or not row['avatar']:
		raise LookupError('no picture')

	return row['avatar'], row['avatar_mime']


def avatar_set(user: dict, data: bytes, mime: str) -> dict:
	'''
	Set or clear (empty data) the user's own picture.

	:param user: User dict
	:param data: Image bytes
	:param mime: Image MIME type
	'''

	if data and mime not in AVATAR_MIME:
		raise ValueError('pictures must be PNG, JPEG, WebP or GIF')

	if len(data) > AVATAR_MAX:
		raise ValueError('picture is too large')

	with kanban.connect() as db:
		db.execute('UPDATE users SET avatar = ?, avatar_mime = ?, avatar_at = ? WHERE id = ?', (data or None, mime if data else None, kanban.now() if data else None, user['id']))
		row = db.execute(f'SELECT {PUBLIC} FROM users WHERE id = ?', (user['id'],)).fetchone()

	return dict(row)


def digest(token: str) -> str:
	'''
	Hash a random token for storage.

	:param token: Raw token
	'''

	return hashlib.sha256(token.encode()).hexdigest()


def expires(days: int) -> str:
	'''
	Return an ISO time some days from now.

	:param days: Days ahead
	'''

	return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec='seconds')


def invite_check(token: str) -> dict:
	'''
	Return a usable invite or raise LookupError.

	:param token: Raw invite token
	'''

	with kanban.connect() as db:
		row = db.execute('SELECT * FROM invites WHERE token = ? AND used IS NULL AND expires > ?', (digest(token), kanban.now())).fetchone()

	if not row:
		raise LookupError('this invite link is invalid, used or expired')

	return dict(row)


def invite_create(user: dict) -> dict:
	'''
	Create a single-use invite link token (admin only); the raw token is only returned here.

	:param user: Admin creating it
	'''

	need_admin(user)
	token = secrets.token_urlsafe(24)

	with kanban.connect() as db:
		invite_id = db.execute('INSERT INTO invites (token, created_by, created, expires) VALUES (?, ?, ?, ?)', (digest(token), user['id'], kanban.now(), expires(INVITE_DAYS))).lastrowid

	return {'id': invite_id, 'token': token}


def invite_delete(user: dict, invite_id: int):
	'''
	Revoke an unused invite (admin only).

	:param user: Admin
	:param invite_id: Invite id
	'''

	need_admin(user)

	with kanban.connect() as db:
		if not db.execute('DELETE FROM invites WHERE id = ? AND used IS NULL', (invite_id,)).rowcount:
			raise LookupError('invite not found or already used')


def invites(user: dict) -> list:
	'''
	List invites, newest first (admin only).

	:param user: Admin
	'''

	need_admin(user)

	with kanban.connect() as db:
		return [dict(row) for row in db.execute('SELECT i.id, i.created, i.expires, i.used, u.username AS used_by FROM invites i LEFT JOIN users u ON u.id = i.used_by ORDER BY i.id DESC LIMIT 50')]


def login(username: str, password: str) -> str:
	'''
	Check credentials and open a session; returns the raw session token.

	:param username: Username
	:param password: Password
	'''

	with kanban.connect() as db:
		row = db.execute('SELECT * FROM users WHERE username = ?', ((username or '').strip().lower(),)).fetchone()

	if not password_ok(password or '', row['password'] if row else DUMMY_HASH) or not row: # hash even for unknown users so timing does not reveal them
		raise PermissionError('wrong username or password')

	return session_create(row['id'])


def need_admin(user: dict):
	'''
	Raise PermissionError unless the user is an admin.

	:param user: User dict
	'''

	if not user or not user['is_admin']:
		raise PermissionError('admin only')


def notifications(user: dict) -> dict:
	'''
	Return the user's newest notifications and unread count.

	:param user: User dict
	'''

	with kanban.connect() as db:
		lv   = kanban.levels(db, user)
		rows = [dict(row) for row in db.execute('SELECT n.*, t.title, t.project_id FROM notifications n LEFT JOIN tasks t ON t.id = n.task_id WHERE n.user_id = ? ORDER BY n.id DESC', (user['id'],))]

	rows = [row for row in rows if lv is None or row['task_id'] is None or lv.get(row.pop('project_id'))] # drop ones about tasks they can no longer see

	return {'notifications': rows[:100], 'unread': sum(not row['read'] for row in rows)}


def notifications_read(user: dict, ids: list = None):
	'''
	Mark notifications read; all of them when ids is empty.

	:param user: User dict
	:param ids: Notification ids
	'''

	with kanban.connect() as db:
		if ids:
			db.executemany('UPDATE notifications SET read = 1 WHERE id = ? AND user_id = ?', [(int(i), user['id']) for i in ids])
		else:
			db.execute('UPDATE notifications SET read = 1 WHERE user_id = ?', (user['id'],))


def password_hash(password: str) -> str:
	'''
	Hash a password with scrypt.

	:param password: Plain password
	'''

	salt = secrets.token_bytes(16)
	key  = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)

	return f'scrypt$16384$8$1${salt.hex()}${key.hex()}'


def password_ok(password: str, stored: str) -> bool:
	'''
	Check a password against a stored scrypt hash.

	:param password: Plain password
	:param stored: Stored hash from password_hash
	'''

	_, n, r, p, salt, key = stored.split('$')
	test = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(key) // 2)

	return hmac.compare_digest(test.hex(), key)


def session_create(user_id: int) -> str:
	'''
	Open a session; returns the raw token for the cookie.

	:param user_id: User id
	'''

	token = secrets.token_urlsafe(32)

	with kanban.connect() as db:
		db.execute('DELETE FROM sessions WHERE expires < ?', (kanban.now(),))
		db.execute('INSERT INTO sessions VALUES (?, ?, ?)', (digest(token), user_id, expires(SESSION_DAYS)))

	return token


def session_delete(token: str):
	'''
	End a session.

	:param token: Raw session token
	'''

	with kanban.connect() as db:
		db.execute('DELETE FROM sessions WHERE token = ?', (digest(token or ''),))


def share_add(project_id: int, role: str, username: str = None, team: str = None) -> list:
	'''
	Share a project with a user or a team (manager or owner only); re-sharing changes the role.

	:param project_id: Project id
	:param role: viewer, editor or manager
	:param username: User to share with
	:param team: Team name to share with
	'''

	if role not in kanban.ROLES:
		raise ValueError('role must be viewer, editor or manager')

	with kanban.connect() as db:
		kanban.require(db, 'projects', project_id)
		kanban.need(db, project_id, kanban.MANAGE)
		if username:
			row = db.execute('SELECT id FROM users WHERE username = ?', (username.strip().lower().lstrip('@'),)).fetchone()
			if not row:
				raise LookupError(f'no user named {username}')
			db.execute('DELETE FROM shares WHERE project_id = ? AND user_id = ?', (project_id, row['id']))
			db.execute('INSERT INTO shares (project_id, user_id, role) VALUES (?, ?, ?)', (project_id, row['id'], role))
		elif team:
			row = db.execute('SELECT id FROM teams WHERE name = ?', (team.strip(),)).fetchone()
			if not row:
				raise LookupError(f'no team named {team}')
			db.execute('DELETE FROM shares WHERE project_id = ? AND team_id = ?', (project_id, row['id']))
			db.execute('INSERT INTO shares (project_id, team_id, role) VALUES (?, ?, ?)', (project_id, row['id'], role))
		else:
			raise ValueError('give a username or a team')

	return shares(project_id)


def share_remove(share_id: int) -> list:
	'''
	Remove a share (manager or owner of the project only).

	:param share_id: Share id
	'''

	with kanban.connect() as db:
		row = kanban.require(db, 'shares', share_id)
		kanban.need(db, row['project_id'], kanban.MANAGE)
		db.execute('DELETE FROM shares WHERE id = ?', (share_id,))

	return shares(row['project_id'])


def shares(project_id: int) -> list:
	'''
	List who a project is shared with, plus its owner and shares inherited from parents.

	:param project_id: Project id
	'''

	with kanban.connect() as db:
		kanban.require(db, 'projects', project_id)
		kanban.need(db, project_id, kanban.VIEW)
		names = kanban.paths(db)
		chain = []
		node  = project_id
		while node:
			chain.append(node)
			node = db.execute('SELECT parent_id FROM projects WHERE id = ?', (node,)).fetchone()['parent_id']
		out = []
		for pid in chain:
			owner = db.execute('SELECT u.username FROM projects p JOIN users u ON u.id = p.owner_id WHERE p.id = ?', (pid,)).fetchone()
			if owner:
				out.append({'id': None, 'kind': 'owner', 'name': owner['username'], 'role': 'owner', 'project': names[pid], 'inherited': pid != project_id})
			for row in db.execute('SELECT s.id, s.role, u.username, t.name AS team FROM shares s LEFT JOIN users u ON u.id = s.user_id LEFT JOIN teams t ON t.id = s.team_id WHERE s.project_id = ? ORDER BY s.id', (pid,)):
				out.append({'id': row['id'], 'kind': 'team' if row['team'] else 'user', 'name': row['team'] or row['username'], 'role': row['role'], 'project': names[pid], 'inherited': pid != project_id})

	return out


def signup(token: str, username: str, password: str) -> str:
	'''
	Create an account from an invite and sign in; returns the raw session token.

	:param token: Raw invite token
	:param username: New username
	:param password: New password
	'''

	invite   = invite_check(token)
	username = valid_username(username)
	valid_password(password)

	with kanban.connect() as db:
		if db.execute('SELECT 1 FROM users WHERE username = ?', (username,)).fetchone():
			raise ValueError('that username is taken')
		user_id = db.execute('INSERT INTO users (username, password, created) VALUES (?, ?, ?)', (username, password_hash(password), kanban.now())).lastrowid
		if not db.execute('UPDATE invites SET used = ?, used_by = ? WHERE id = ? AND used IS NULL', (kanban.now(), user_id, invite['id'])).rowcount:
			raise LookupError('this invite link was just used')

	return session_create(user_id)


def team_create(user: dict, name: str) -> list:
	'''
	Create a team owned by the user.

	:param user: Creator
	:param name: Team name
	'''

	name = (name or '').strip()

	if not re.fullmatch(r'[\w .-]{2,40}', name):
		raise ValueError('team names are 2-40 letters, digits, spaces, dots, dashes or underscores')

	with kanban.connect() as db:
		if db.execute('SELECT 1 FROM teams WHERE name = ?', (name,)).fetchone():
			raise ValueError('that team name is taken')
		team_id = db.execute('INSERT INTO teams (name, created) VALUES (?, ?)', (name, kanban.now())).lastrowid
		db.execute('INSERT INTO team_members VALUES (?, ?, 1)', (team_id, user['id']))

	return teams(user)


def team_delete(user: dict, team_id: int) -> list:
	'''
	Delete a team (team owner or admin).

	:param user: Acting user
	:param team_id: Team id
	'''

	with kanban.connect() as db:
		team_owner(db, user, team_id)
		db.execute('DELETE FROM teams WHERE id = ?', (team_id,))

	return teams(user)


def team_member_add(user: dict, team_id: int, username: str) -> list:
	'''
	Add a user to a team by username (team owner or admin).

	:param user: Acting user
	:param team_id: Team id
	:param username: User to add
	'''

	with kanban.connect() as db:
		team_owner(db, user, team_id)
		row = db.execute('SELECT id FROM users WHERE username = ?', ((username or '').strip().lower().lstrip('@'),)).fetchone()
		if not row:
			raise LookupError(f'no user named {username}')
		db.execute('INSERT OR IGNORE INTO team_members VALUES (?, ?, 0)', (team_id, row['id']))

	return teams(user)


def team_member_remove(user: dict, team_id: int, user_id: int) -> list:
	'''
	Remove a member (team owner or admin), or leave a team (anyone, themselves). The last owner cannot leave.

	:param user: Acting user
	:param team_id: Team id
	:param user_id: Member to remove
	'''

	with kanban.connect() as db:
		if user_id != user['id']:
			team_owner(db, user, team_id)
		member = db.execute('SELECT owner FROM team_members WHERE team_id = ? AND user_id = ?', (team_id, user_id)).fetchone()
		if not member:
			raise LookupError('not a member of that team')
		if member['owner'] and db.execute('SELECT COUNT(*) FROM team_members WHERE team_id = ? AND owner = 1', (team_id,)).fetchone()[0] == 1:
			raise ValueError('the last owner cannot leave; promote someone or delete the team')
		db.execute('DELETE FROM team_members WHERE team_id = ? AND user_id = ?', (team_id, user_id))

	return teams(user)


def team_member_role(user: dict, team_id: int, user_id: int, owner: bool) -> list:
	'''
	Promote a member to team owner or demote them (team owner or admin).

	:param user: Acting user
	:param team_id: Team id
	:param user_id: Member
	:param owner: New owner flag
	'''

	with kanban.connect() as db:
		team_owner(db, user, team_id)
		if not owner and db.execute('SELECT COUNT(*) FROM team_members WHERE team_id = ? AND owner = 1 AND user_id != ?', (team_id, user_id)).fetchone()[0] == 0:
			raise ValueError('a team needs at least one owner')
		if not db.execute('UPDATE team_members SET owner = ? WHERE team_id = ? AND user_id = ?', (int(bool(owner)), team_id, user_id)).rowcount:
			raise LookupError('not a member of that team')

	return teams(user)


def team_owner(db, user: dict, team_id: int):
	'''
	Raise unless the user owns the team or is an admin.

	:param db: Open database connection
	:param user: Acting user
	:param team_id: Team id
	'''

	kanban.require(db, 'teams', team_id)

	if not user['is_admin'] and not db.execute('SELECT 1 FROM team_members WHERE team_id = ? AND user_id = ? AND owner = 1', (team_id, user['id'])).fetchone():
		raise PermissionError('only a team owner can do that')


def teams(user: dict) -> list:
	'''
	Return the teams the user belongs to (admins see all) with their members.

	:param user: User dict
	'''

	with kanban.connect() as db:
		query = 'SELECT * FROM teams ORDER BY name' if user['is_admin'] else 'SELECT t.* FROM teams t JOIN team_members m ON m.team_id = t.id WHERE m.user_id = ? ORDER BY t.name'
		rows  = db.execute(query, () if user['is_admin'] else (user['id'],)).fetchall()
		out   = []
		for team in rows:
			members = [dict(row) for row in db.execute('SELECT u.id, u.username, m.owner FROM team_members m JOIN users u ON u.id = m.user_id WHERE m.team_id = ? ORDER BY m.owner DESC, u.username', (team['id'],))]
			mine    = any(m['id'] == user['id'] and m['owner'] for m in members)
			out.append({'id': team['id'], 'name': team['name'], 'members': members, 'owner': mine or bool(user['is_admin'])})

	return out


def token_create(user: dict, name: str) -> dict:
	'''
	Create an API token for agents; the raw token is only returned here.

	:param user: Token owner
	:param name: Label like claude or hermes
	'''

	name  = (name or '').strip()[:40] or 'token'
	token = 'bk_' + secrets.token_urlsafe(32)

	with kanban.connect() as db:
		token_id = db.execute('INSERT INTO tokens (user_id, name, token, created) VALUES (?, ?, ?, ?)', (user['id'], name, digest(token), kanban.now())).lastrowid

	return {'id': token_id, 'name': name, 'token': token}


def token_delete(user: dict, token_id: int):
	'''
	Revoke one of the user's API tokens.

	:param user: Token owner
	:param token_id: Token id
	'''

	with kanban.connect() as db:
		if not db.execute('DELETE FROM tokens WHERE id = ? AND user_id = ?', (token_id, user['id'])).rowcount:
			raise LookupError('token not found')


def token_import(user_id: int, name: str, token: str):
	'''
	Store an existing secret (the old shared key) as a user's API token, once.

	:param user_id: Token owner
	:param name: Label
	:param token: Raw token
	'''

	with kanban.connect() as db:
		db.execute('INSERT OR IGNORE INTO tokens (user_id, name, token, created) VALUES (?, ?, ?, ?)', (user_id, name, digest(token), kanban.now()))


def tokens(user: dict) -> list:
	'''
	List the user's API tokens (never the secrets).

	:param user: Token owner
	'''

	with kanban.connect() as db:
		return [dict(row) for row in db.execute('SELECT id, name, created, used FROM tokens WHERE user_id = ? ORDER BY id', (user['id'],))]


def user_by_session(token: str) -> dict:
	'''
	Return the user for a session cookie, or None.

	:param token: Raw session token
	'''

	if not token:
		return None

	with kanban.connect() as db:
		row = db.execute(f'SELECT {", ".join("u." + c.strip() for c in PUBLIC.split(","))} FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ? AND s.expires > ?', (digest(token), kanban.now())).fetchone()

	return dict(row) if row else None


def user_by_token(token: str) -> dict:
	'''
	Return the user for an API token, or None; records when it was last used.

	:param token: Raw API token
	'''

	if not token:
		return None

	with kanban.connect() as db:
		row = db.execute('SELECT id, user_id FROM tokens WHERE token = ?', (digest(token),)).fetchone()
		if not row:
			return None
		db.execute('UPDATE tokens SET used = ? WHERE id = ?', (kanban.now(), row['id']))
		user = db.execute(f'SELECT {PUBLIC} FROM users WHERE id = ?', (row['user_id'],)).fetchone()

	return dict(user) if user else None


def user_create(username: str, password: str, is_admin: bool = False) -> int:
	'''
	Create a user directly (setup and admin use).

	:param username: Username
	:param password: Password
	:param is_admin: Admin flag
	'''

	username = valid_username(username)
	valid_password(password)

	with kanban.connect() as db:
		if db.execute('SELECT 1 FROM users WHERE username = ?', (username,)).fetchone():
			raise ValueError('that username is taken')
		return db.execute('INSERT INTO users (username, password, is_admin, created) VALUES (?, ?, ?, ?)', (username, password_hash(password), int(is_admin), kanban.now())).lastrowid


def user_delete(user: dict, user_id: int):
	'''
	Delete a user (admin only); their projects move to the admin.

	:param user: Admin
	:param user_id: User to delete
	'''

	need_admin(user)

	if user_id == user['id']:
		raise ValueError('you cannot delete yourself')

	with kanban.connect() as db:
		db.execute('UPDATE projects SET owner_id = ? WHERE owner_id = ?', (user['id'], user_id))
		if not db.execute('DELETE FROM users WHERE id = ?', (user_id,)).rowcount:
			raise LookupError('user not found')


def user_reset(user: dict, user_id: int, password: str):
	'''
	Set another user's password (admin only) and sign them out everywhere.

	:param user: Admin
	:param user_id: User id
	:param password: New password
	'''

	need_admin(user)
	valid_password(password)

	with kanban.connect() as db:
		if not db.execute('UPDATE users SET password = ? WHERE id = ?', (password_hash(password), user_id)).rowcount:
			raise LookupError('user not found')
		db.execute('DELETE FROM sessions WHERE user_id = ?', (user_id,))


def user_update(user: dict, data: dict) -> dict:
	'''
	Update the user's own profile: password (needs the current one), ntfy topic, notification toggles.

	:param user: User dict
	:param data: Any of: password + current_password, ntfy_topic, notify_* flags
	'''

	with kanban.connect() as db:
		if data.get('password'):
			row = db.execute('SELECT password FROM users WHERE id = ?', (user['id'],)).fetchone()
			if not password_ok(data.get('current_password') or '', row['password']):
				raise PermissionError('current password is wrong')
			valid_password(data['password'])
			db.execute('UPDATE users SET password = ? WHERE id = ?', (password_hash(data['password']), user['id']))
		if 'ntfy_topic' in data:
			topic = (data['ntfy_topic'] or '').strip()
			if topic and not re.fullmatch(r'[\w-]{1,64}', topic):
				raise ValueError('ntfy topics are letters, digits, dashes and underscores')
			db.execute('UPDATE users SET ntfy_topic = ? WHERE id = ?', (topic, user['id']))
		for key in PREFS:
			if key in data:
				db.execute(f'UPDATE users SET {key} = ? WHERE id = ?', (int(bool(data[key])), user['id']))
		row = db.execute(f'SELECT {PUBLIC} FROM users WHERE id = ?', (user['id'],)).fetchone()

	return dict(row)


def users() -> list:
	'''Return every user's id, username, admin flag, created time and picture timestamp.'''

	with kanban.connect() as db:
		return [dict(row) for row in db.execute('SELECT id, username, is_admin, created, avatar_at FROM users ORDER BY username')]


def valid_password(password: str):
	'''
	Require a password of at least 8 characters.

	:param password: Password
	'''

	if len(password or '') < 8:
		raise ValueError('passwords need at least 8 characters')


def valid_username(username: str) -> str:
	'''
	Normalize and check a username.

	:param username: Username
	'''

	username = (username or '').strip().lower()

	if not USERNAME_RE.match(username):
		raise ValueError('usernames are 2-32 lowercase letters, digits, dashes or underscores')

	return username
