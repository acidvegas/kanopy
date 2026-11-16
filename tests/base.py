#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# tests/base.py

import tempfile
import unittest

from pathlib import Path

from kanopy import accounts
from kanopy import app
from kanopy import kanban
from kanopy import mcp_server
from kanopy import notify


PASSWORD = 'password123'
HASH     = accounts.password_hash(PASSWORD) # scrypt is slow, so every test user shares one hash


class Base(unittest.TestCase):
	'''Fresh database in a temporary directory per test, with ntfy pushes recorded instead of sent.'''

	def setUp(self):
		'''Point kanban at an empty database and stub out network pushes.'''

		self.dir       = tempfile.TemporaryDirectory()
		self.old       = (kanban.DB, kanban.BACKUPS, notify.send)
		kanban.DB      = Path(self.dir.name) / 'kanban.db'
		kanban.BACKUPS = Path(self.dir.name) / 'backups'
		self.pushes    = []
		notify.send    = lambda title, message, task_id=None, priority=3, tags='', topic=None: self.pushes.append({'title': title, 'message': message, 'task_id': task_id, 'topic': topic})
		kanban.init()
		mcp_server.SEEN.clear()
		app.FAILS.clear()
		kanban.ACTOR.set(None)
		kanban.AGENT.set(None)


	def tearDown(self):
		'''Restore module state and remove the database.'''

		kanban.DB, kanban.BACKUPS, notify.send = self.old
		kanban.ACTOR.set(None)
		kanban.AGENT.set(None)
		self.dir.cleanup()


	def act(self, user: dict = None):
		'''
		Make every following call run as this user (None = the system, full access).

		:param user: User dict
		'''

		kanban.ACTOR.set(user)


	def mcp(self, name: str, args: dict = None, agent: str = 'claude') -> str:
		'''
		Call an MCP tool through the JSON-RPC handler and return its text; raises AssertionError text on tool errors.

		:param name: Tool name
		:param args: Tool arguments
		:param agent: Agent tag
		'''

		reply  = mcp_server.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': args or {}}}, agent)
		result = reply['result']
		text   = result['content'][0]['text']

		if result.get('isError'):
			raise ToolError(text)

		return text


	def task(self, project: str = 'Work', title: str = 'task', **fields) -> dict:
		'''
		Create a task (creating its project path when needed) as the current actor.

		:param project: Project path
		:param title: Task title
		'''

		pid = next((p['id'] for p in kanban.projects() if p['path'] == project), None) or kanban.project_save(project)['id']

		return kanban.task_create({'project_id': pid, 'title': title, **fields})


	def user(self, name: str, admin: bool = False) -> dict:
		'''
		Create a user and return them as the signed-in user dict.

		:param name: Username
		:param admin: Make them an admin
		'''

		with kanban.connect() as db:
			uid = db.execute('INSERT INTO users (username, password, is_admin, created) VALUES (?, ?, ?, ?)', (name, HASH, int(admin), kanban.now())).lastrowid
			return dict(db.execute(f'SELECT {accounts.PUBLIC} FROM users WHERE id = ?', (uid,)).fetchone())


class ToolError(Exception):
	'''An MCP tool returned isError.'''
