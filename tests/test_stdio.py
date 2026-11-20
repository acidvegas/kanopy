#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# tests/test_stdio.py

import json
import os
import subprocess
import sys
import unittest

from pathlib import Path

from kanopy import accounts
from kanopy import kanban

from .base import Base


class TestStdio(Base):
	'''MCP over stdin/stdout.'''

	def run_stdio(self, messages: list, token: str = '') -> list:
		'''
		Run python -m kanopy.stdio on the test database with these messages and return its replies.

		:param messages: JSON-RPC messages, or raw strings sent as they are
		:param token: KANOPY_TOKEN value
		'''

		env  = dict(os.environ, KANOPY_DATABASE=kanban.DATABASE, KANOPY_TOKEN=token, KANOPY_AGENT='claude')
		data = ''.join((m if isinstance(m, str) else json.dumps(m)) + '\n' for m in messages)
		out  = subprocess.run([sys.executable, '-m', 'kanopy.stdio'], input=data, capture_output=True, text=True, env=env, cwd=Path(__file__).parent.parent, timeout=60)

		return [json.loads(line) for line in out.stdout.splitlines()]


	def test_without_token(self):
		'''Without a token the handshake and tool list answer, without the board's instructions, and tool calls are refused.'''

		replies = self.run_stdio([
			{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
			{'jsonrpc': '2.0', 'method': 'notifications/initialized'},
			{'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
			{'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'list_projects', 'arguments': {}}},
			'not json',
		])
		self.assertEqual([r['id'] for r in replies], [1, 2, 3, None])
		self.assertNotIn('instructions', replies[0]['result'])
		self.assertIn('save_task', [t['name'] for t in replies[1]['result']['tools']])
		self.assertEqual((replies[2]['error']['code'], replies[3]['error']['code']), (-32001, -32700))


	def test_with_token(self):
		'''With a token the tools act as its user and agent, and a write bumps the revision.'''

		ann = self.user('ann')
		key = accounts.token_create(ann, 'claude')['token']
		was = kanban.revision()
		replies = self.run_stdio([{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'save_task', 'arguments': {'project': 'Work', 'title': 'from stdio'}}}], key)
		self.assertIn('[Work] from stdio', replies[0]['result']['content'][0]['text'])
		self.act(ann)
		self.assertEqual(kanban.tasks()[0]['title'], 'from stdio')
		self.assertGreater(kanban.revision(), was)



if __name__ == '__main__':
	unittest.main()
