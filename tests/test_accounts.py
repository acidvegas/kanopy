#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# tests/test_accounts.py

import unittest

from kanopy import accounts
from kanopy import kanban
from kanopy import notify

from .base import Base, PASSWORD


class TestAuth(Base):
	'''Passwords, setup, sign-in and sessions.'''

	def test_password_hash(self):
		'''Hashes are salted and verify only the right password.'''

		a, b = accounts.password_hash('secret-1'), accounts.password_hash('secret-1')
		self.assertNotEqual(a, b)
		self.assertTrue(accounts.password_ok('secret-1', a))
		self.assertFalse(accounts.password_ok('secret-2', a))
		self.assertFalse(accounts.password_ok('anything', accounts.DUMMY_HASH))


	def test_setup_once(self):
		'''The first account is an admin and setup works only on an empty board.'''

		self.assertTrue(accounts.setup_needed())
		token = accounts.setup('Root', 'longpassword')
		self.assertFalse(accounts.setup_needed())
		user = accounts.user_by_session(token)
		self.assertEqual((user['username'], user['is_admin']), ('root', 1))
		with self.assertRaises(PermissionError):
			accounts.setup('again', 'longpassword')


	def test_validation(self):
		'''Usernames and passwords are checked.'''

		for bad in ('a', 'has space', 'x' * 33, 'bad!'):
			with self.assertRaises(ValueError, msg=bad):
				accounts.valid_username(bad)
		self.assertEqual(accounts.valid_username(' Ann_1 '), 'ann_1')
		with self.assertRaises(ValueError):
			accounts.valid_password('short')
		with self.assertRaises(ValueError):
			accounts.setup('root', 'short')


	def test_login_and_sessions(self):
		'''Sign-in opens a session; wrong passwords and unknown users fail alike; sessions expire and end.'''

		self.user('ann')
		token = accounts.login(' ANN ', PASSWORD)
		self.assertEqual(accounts.user_by_session(token)['username'], 'ann')
		for name, pw in (('ann', 'wrong'), ('ghost', PASSWORD), (None, None)):
			with self.assertRaises(PermissionError):
				accounts.login(name, pw)
		self.assertIsNone(accounts.user_by_session('') or accounts.user_by_session('bogus'))
		accounts.session_delete(token)
		self.assertIsNone(accounts.user_by_session(token))
		token = accounts.login('ann', PASSWORD)
		with kanban.connect() as db:
			db.execute("UPDATE sessions SET expires = '2000-01-01T00:00:00+00:00'")
		self.assertIsNone(accounts.user_by_session(token))


	def test_session_tokens_are_hashed(self):
		'''Raw session and API tokens are never stored.'''

		ann   = self.user('ann')
		token = accounts.login('ann', PASSWORD)
		key   = accounts.token_create(ann, 'claude')['token']
		with kanban.connect() as db:
			stored = [r[0] for r in db.execute('SELECT token FROM sessions UNION ALL SELECT token FROM tokens')]
		self.assertNotIn(token, stored)
		self.assertNotIn(key, stored)
		self.assertIn(accounts.digest(token), stored)


class TestInvites(Base):
	'''Invite links and sign-up.'''

	def setUp(self):
		'''An admin and an invite.'''

		super().setUp()
		self.admin  = self.user('root', admin=True)
		self.invite = accounts.invite_create(self.admin)


	def test_signup(self):
		'''An invite creates one non-admin account, then is used up.'''

		token = accounts.signup(self.invite['token'], 'Ann', PASSWORD)
		user  = accounts.user_by_session(token)
		self.assertEqual((user['username'], user['is_admin']), ('ann', 0))
		with self.assertRaises(LookupError):
			accounts.signup(self.invite['token'], 'bob', PASSWORD)
		self.assertEqual(accounts.invites(self.admin)[0]['used_by'], 'ann')


	def test_taken_username(self):
		'''A taken username does not use up the invite.'''

		with self.assertRaises(ValueError):
			accounts.signup(self.invite['token'], 'root', PASSWORD)
		accounts.invite_check(self.invite['token'])


	def test_expired_and_revoked(self):
		'''Expired or revoked invites are refused; only admins manage invites.'''

		with kanban.connect() as db:
			db.execute("UPDATE invites SET expires = '2000-01-01T00:00:00+00:00'")
		with self.assertRaises(LookupError):
			accounts.invite_check(self.invite['token'])
		other = accounts.invite_create(self.admin)
		accounts.invite_delete(self.admin, other['id'])
		with self.assertRaises(LookupError):
			accounts.invite_check(other['token'])
		with self.assertRaises(LookupError):
			accounts.invite_delete(self.admin, other['id'])
		ann = self.user('ann')
		for call in (lambda: accounts.invite_create(ann), lambda: accounts.invites(ann), lambda: accounts.invite_delete(ann, 1)):
			with self.assertRaises(PermissionError):
				call()


class TestProfile(Base):
	'''Profile, pictures, tokens and admin user management.'''

	def test_user_update(self):
		'''Password changes need the current password; topic and toggles are validated.'''

		ann = self.user('ann')
		with self.assertRaises(PermissionError):
			accounts.user_update(ann, {'password': 'newpassword', 'current_password': 'wrong'})
		accounts.user_update(ann, {'password': 'newpassword', 'current_password': PASSWORD})
		accounts.login('ann', 'newpassword')
		row = accounts.user_update(ann, {'ntfy_topic': ' my-topic ', 'notify_mention': False})
		self.assertEqual((row['ntfy_topic'], row['notify_mention']), ('my-topic', 0))
		with self.assertRaises(ValueError):
			accounts.user_update(ann, {'ntfy_topic': 'bad topic!'})


	def test_avatar(self):
		'''Pictures must be small images; empty data clears them.'''

		ann = self.user('ann')
		accounts.avatar_set(ann, b'png', 'image/png')
		self.assertEqual(accounts.avatar_get(ann['id']), (b'png', 'image/png'))
		with self.assertRaises(ValueError):
			accounts.avatar_set(ann, b'<svg>', 'image/svg+xml')
		with self.assertRaises(ValueError):
			accounts.avatar_set(ann, b'x' * (accounts.AVATAR_MAX + 1), 'image/png')
		self.assertIsNone(accounts.avatar_set(ann, b'', '')['avatar_at'])
		with self.assertRaises(LookupError):
			accounts.avatar_get(ann['id'])


	def test_api_tokens(self):
		'''Tokens authenticate, record use, list without secrets and revoke only by their owner.'''

		ann, bob = self.user('ann'), self.user('bob')
		tok      = accounts.token_create(ann, '  ')
		self.assertEqual(tok['name'], 'token')
		self.assertTrue(tok['token'].startswith('bk_'))
		self.assertEqual(accounts.user_by_token(tok['token'])['username'], 'ann')
		self.assertIsNotNone(accounts.tokens(ann)[0]['used'])
		self.assertNotIn('token', accounts.tokens(ann)[0])
		self.assertIsNone(accounts.user_by_token('bk_wrong'))
		with self.assertRaises(LookupError):
			accounts.token_delete(bob, tok['id'])
		accounts.token_delete(ann, tok['id'])
		self.assertIsNone(accounts.user_by_token(tok['token']))
		accounts.token_import(ann['id'], 'shared', 'legacy')
		accounts.token_import(ann['id'], 'shared', 'legacy')
		self.assertEqual(len(accounts.tokens(ann)), 1)


	def test_own_password_change_signs_out_other_sessions(self):
		'''Changing your own password keeps this session and your API tokens, and signs out every other session.'''

		ann   = self.user('ann')
		here  = accounts.login('ann', PASSWORD)
		there = accounts.login('ann', PASSWORD)
		key   = accounts.token_create(ann, 'claude')['token']
		accounts.user_update(ann, {'password': 'brandnewpassword', 'current_password': PASSWORD}, here)
		self.assertIsNotNone(accounts.user_by_session(here))
		self.assertIsNone(accounts.user_by_session(there))
		self.assertIsNotNone(accounts.user_by_token(key))


	def test_own_ntfy_server(self):
		'''Users set their own ntfy server and token; a blank token keeps the stored one, clear removes it, and the token is never returned.'''

		ann = self.user('ann')
		row = accounts.user_update(ann, {'ntfy_url': ' https://ntfy.example.com/ ', 'ntfy_token': 'tk_1'})
		self.assertEqual(row['ntfy_url'], 'https://ntfy.example.com')
		self.assertNotIn('ntfy_token', row)
		self.assertTrue(accounts.ntfy_token_set(ann['id']))
		accounts.user_update(ann, {'ntfy_token': ''})
		self.assertTrue(accounts.ntfy_token_set(ann['id']))
		accounts.user_update(ann, {'clear_ntfy_token': True})
		self.assertFalse(accounts.ntfy_token_set(ann['id']))
		with self.assertRaises(ValueError):
			accounts.user_update(ann, {'ntfy_url': 'ftp://nope'})


	def test_admin_reset_and_delete(self):
		'''Resetting a password signs the user out and revokes their API tokens; deleting a user hands their projects to the admin.'''

		admin, ann = self.user('root', admin=True), self.user('ann')
		token = accounts.login('ann', PASSWORD)
		key   = accounts.token_create(ann, 'claude')['token']
		accounts.user_reset(admin, ann['id'], 'resetpassword')
		self.assertIsNone(accounts.user_by_session(token))
		self.assertIsNone(accounts.user_by_token(key))
		self.act(ann)
		pid = kanban.project_save('Ann')['id']
		self.act(None)
		with self.assertRaises(PermissionError):
			accounts.user_delete(ann, admin['id'])
		with self.assertRaises(ValueError):
			accounts.user_delete(admin, admin['id'])
		accounts.user_delete(admin, ann['id'])
		with kanban.connect() as db:
			self.assertEqual(db.execute('SELECT owner_id FROM projects WHERE id = ?', (pid,)).fetchone()[0], admin['id'])
		with self.assertRaises(LookupError):
			accounts.user_delete(admin, ann['id'])
		with self.assertRaises(LookupError):
			accounts.user_reset(admin, 999, 'resetpassword')


class TestShares(Base):
	'''Project sharing.'''

	def test_share_list_and_remove(self):
		'''Shares list the owner, direct and inherited shares; re-sharing changes the role.'''

		ann, bob = self.user('ann'), self.user('bob')
		self.act(ann)
		parent = kanban.project_save('Ann')['id']
		child  = kanban.project_save('Ann/sub')['id']
		accounts.share_add(parent, 'viewer', '@bob')
		rows = accounts.share_add(parent, 'editor', 'bob')
		self.assertEqual([(r['kind'], r['name'], r['role']) for r in rows], [('owner', 'ann', 'owner'), ('user', 'bob', 'editor')])
		inherited = accounts.shares(child)
		self.assertEqual([(r['name'], r['inherited']) for r in inherited], [('ann', False), ('ann', True), ('bob', True)]) # ann made the sub-project too, so she owns it directly
		accounts.share_remove(rows[1]['id'])
		self.act(bob)
		with self.assertRaises(PermissionError):
			accounts.shares(parent)


	def test_share_errors(self):
		'''Bad roles, unknown users and teams, and missing targets are refused.'''

		pid = kanban.project_save('P')['id']
		for args in (('owner', 'x', None), ('viewer', 'ghost', None), ('viewer', None, 'ghosts'), ('viewer', None, None)):
			with self.assertRaises((ValueError, LookupError), msg=args):
				accounts.share_add(pid, *args)


class TestTeams(Base):
	'''Teams and their owners.'''

	def setUp(self):
		'''Ann owns team crew with Bob as a member.'''

		super().setUp()
		self.ann, self.bob = self.user('ann'), self.user('bob')
		accounts.team_create(self.ann, 'crew')
		self.team = accounts.teams(self.ann)[0]['id']
		accounts.team_member_add(self.ann, self.team, 'bob')


	def test_names(self):
		'''Team names are validated and unique.'''

		with self.assertRaises(ValueError):
			accounts.team_create(self.ann, 'crew')
		with self.assertRaises(ValueError):
			accounts.team_create(self.ann, 'x')


	def test_only_owners_manage(self):
		'''Members cannot add, remove, promote or delete; they can leave.'''

		for call in (lambda: accounts.team_member_add(self.bob, self.team, 'ann'), lambda: accounts.team_member_remove(self.bob, self.team, self.ann['id']), lambda: accounts.team_member_role(self.bob, self.team, self.bob['id'], True), lambda: accounts.team_delete(self.bob, self.team)):
			with self.assertRaises(PermissionError):
				call()
		accounts.team_member_remove(self.bob, self.team, self.bob['id'])
		self.assertEqual(accounts.teams(self.bob), [])


	def test_webhook(self):
		'''Only owners set the webhook and only they see it; it must be a public https URL, and empty clears it.'''

		with self.assertRaises(PermissionError):
			accounts.team_webhook(self.bob, self.team, 'https://chat.example.com/hook')
		for bad in ('ftp://x', 'not a url', 'https://' + 'x' * 500, 'http://chat.example.com/hook', 'https://127.0.0.1:3000/hook', 'https://100.64.0.1/hook'):
			with self.assertRaises(ValueError, msg=bad):
				accounts.team_webhook(self.ann, self.team, bad)
		real, notify.webhook_public = notify.webhook_public, lambda host: host == 'chat.example.com' # no DNS in tests
		try:
			self.assertEqual(accounts.team_webhook(self.ann, self.team, ' https://chat.example.com/hook ')[0]['webhook'], 'https://chat.example.com/hook')
		finally:
			notify.webhook_public = real
		self.assertEqual(accounts.teams(self.bob)[0]['webhook'], '')
		self.assertEqual(accounts.team_webhook(self.ann, self.team, '')[0]['webhook'], '')


	def test_last_owner(self):
		'''A team always keeps at least one owner.'''

		with self.assertRaises(ValueError):
			accounts.team_member_remove(self.ann, self.team, self.ann['id'])
		with self.assertRaises(ValueError):
			accounts.team_member_role(self.ann, self.team, self.ann['id'], False)
		accounts.team_member_role(self.ann, self.team, self.bob['id'], True)
		accounts.team_member_remove(self.ann, self.team, self.ann['id'])
		self.assertTrue(accounts.teams(self.bob)[0]['owner'])


	def test_unknown_members(self):
		'''Unknown users and non-members are reported.'''

		with self.assertRaises(LookupError):
			accounts.team_member_add(self.ann, self.team, 'ghost')
		cat = self.user('cat')
		with self.assertRaises(LookupError):
			accounts.team_member_remove(self.ann, self.team, cat['id'])
		with self.assertRaises(LookupError):
			accounts.team_member_role(self.ann, self.team, cat['id'], True)


	def test_admin_sees_all(self):
		'''Admins see and manage every team.'''

		root = self.user('root', admin=True)
		self.assertEqual([t['name'] for t in accounts.teams(root)], ['crew'])
		accounts.team_delete(root, self.team)
		self.assertEqual(accounts.teams(self.ann), [])


class TestNotifications(Base):
	'''Inbox.'''

	def test_hidden_after_losing_access(self):
		'''Notifications about tasks the user can no longer see are dropped; read marks them.'''

		ann, bob = self.user('ann'), self.user('bob')
		self.act(ann)
		t     = self.task('Ann', 'x')
		share = accounts.share_add(t['project_id'], 'viewer', 'bob')[1]['id']
		with kanban.connect() as db:
			for _ in range(2):
				db.execute("INSERT INTO notifications (user_id, task_id, kind, actor, text, created) VALUES (?, ?, 'mention', 'ann', 'hi', ?)", (bob['id'], t['id'], kanban.now()))
		self.assertEqual(accounts.notifications(bob)['unread'], 2)
		first = accounts.notifications(bob)['notifications'][0]['id']
		accounts.notifications_read(bob, [first])
		self.assertEqual(accounts.notifications(bob)['unread'], 1)
		accounts.share_remove(share)
		self.assertEqual(accounts.notifications(bob), {'notifications': [], 'unread': 0})



if __name__ == '__main__':
	unittest.main()
