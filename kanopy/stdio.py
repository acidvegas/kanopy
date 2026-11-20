#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# kanopy/stdio.py

'''
MCP over stdin/stdout for clients that launch a local command instead of connecting over HTTP: python -m kanopy.stdio.
It works on the same database as the web app. KANOPY_TOKEN is an API token to act as its user; without one only the handshake and the tool list answer.
KANOPY_AGENT names the agent in comments and history, like the X-Agent header.
'''

import json
import os
import re
import secrets
import sys

from . import accounts
from . import kanban
from . import mcp_server


def main():
	'''Answer one JSON-RPC message per line until stdin closes; only replies go to stdout.'''

	kanban.init()

	user    = accounts.user_by_token(os.environ.get('KANOPY_TOKEN', ''))
	agent   = re.sub(r'[^a-z0-9_-]', '', os.environ.get('KANOPY_AGENT', 'agent').lower())[:32] or 'agent'
	session = secrets.token_hex(3)

	for line in sys.stdin:
		if not line.strip():
			continue
		try:
			msg = json.loads(line)
		except ValueError:
			reply = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'not JSON'}}
		else:
			if not isinstance(msg, dict):
				reply = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'send one JSON-RPC request object'}}
			elif not user and msg.get('method') not in mcp_server.OPEN_METHODS:
				reply = {'jsonrpc': '2.0', 'id': msg.get('id'), 'error': {'code': -32001, 'message': 'set KANOPY_TOKEN to an API token to use the tools'}} if msg.get('id') is not None else None
			else:
				kanban.ACTOR.set(user)
				kanban.AGENT.set(agent)
				kanban.SESSION.set(session)
				reply  = mcp_server.handle(msg, f'{agent} ({user["username"]})' if user else agent, bool(user and user.get('read_only')), (user or {}).get('token_level', kanban.OWN), anonymous=not user)
				params = msg.get('params') if isinstance(msg.get('params'), dict) else {}
				if reply and msg.get('method') == 'tools/call' and not reply.get('result', {}).get('isError') and not mcp_server.reads_only(params.get('name'), params.get('arguments') if isinstance(params.get('arguments'), dict) else {}):
					kanban.revision_bump() # open pages reload, as after a write over HTTP
		if reply:
			sys.stdout.write(json.dumps(reply) + '\n')
			sys.stdout.flush()



if __name__ == '__main__':
	main()
