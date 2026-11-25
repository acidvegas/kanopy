#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# tests/test_twofactor.py

import base64
import hashlib
import json
import os
import time

import cbor2

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from kanopy import accounts
from kanopy import app
from kanopy import twofactor

from .base import PASSWORD
from .test_app import AppBase


def b64(data: bytes) -> str:
	'''Base64url without padding, as WebAuthn JSON uses.'''

	return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


class SoftKey:
	'''A software passkey for the test client's origin (http://localhost), so registration and sign-in run through the real WebAuthn checks.'''

	def __init__(self):
		self.key   = ec.generate_private_key(ec.SECP256R1())
		self.id    = os.urandom(16)
		self.count = 0
		self.rp    = hashlib.sha256(b'localhost').digest()


	def client_data(self, kind: str, challenge: str) -> bytes:
		return json.dumps({'type': kind, 'challenge': challenge, 'origin': 'http://localhost', 'crossOrigin': False}).encode()


	def create(self, options: dict) -> dict:
		'''Answer registration options like navigator.credentials.create().toJSON().'''

		nums = self.key.public_key().public_numbers()
		cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, 'big'), -3: nums.y.to_bytes(32, 'big')})
		auth = self.rp + bytes([0x45]) + (0).to_bytes(4, 'big') + bytes(16) + len(self.id).to_bytes(2, 'big') + self.id + cose
		return {'id': b64(self.id), 'rawId': b64(self.id), 'type': 'public-key', 'response': {'clientDataJSON': b64(self.client_data('webauthn.create', options['challenge'])), 'attestationObject': b64(cbor2.dumps({'fmt': 'none', 'attStmt': {}, 'authData': auth}))}}


	def get(self, options: dict, origin_ok: bool = True) -> dict:
		'''Answer sign-in options like navigator.credentials.get().toJSON().'''

		self.count += 1
		data = self.client_data('webauthn.get', options['challenge'])
		if not origin_ok:
			data = data.replace(b'http://localhost', b'https://evil.example')
		auth = self.rp + bytes([0x05]) + self.count.to_bytes(4, 'big')
		sig  = self.key.sign(auth + hashlib.sha256(data).digest(), ec.ECDSA(hashes.SHA256()))
		return {'id': b64(self.id), 'rawId': b64(self.id), 'type': 'public-key', 'response': {'clientDataJSON': b64(data), 'authenticatorData': b64(auth), 'signature': b64(sig)}}


class TestTwoFactor(AppBase):
	'''Authenticator codes, passkeys and recovery codes.'''

	def setUp(self):
		'''Sign ann in by cookie as well; her 2FA is what gets set up.'''

		super().setUp()
		twofactor.TICKETS.clear()
		self.me = app.app.test_client()
		self.me.set_cookie(app.COOKIE, accounts.login('ann', PASSWORD))


	def totp_on(self) -> tuple:
		'''Turn ann's authenticator app on; returns (secret, recovery codes).'''

		begin = self.me.post('/api/me/2fa/totp', json={'password': PASSWORD}).json
		done  = self.me.post('/api/me/2fa/totp/confirm', json={'code': twofactor.code_at(begin['secret'], int(time.time()) // twofactor.STEP)}).json
		return begin['secret'], done['recovery']


	def ticket(self) -> str:
		'''Sign ann in with her password; returns the second-factor ticket.'''

		r = app.app.test_client().post('/api/login', json={'username': 'ann', 'password': PASSWORD})
		self.assertIn('recovery', r.json['two_factor'])
		self.assertNotIn('Set-Cookie', r.headers)
		return r.json['ticket']


	def test_rfc6238_vector(self):
		'''The code matches RFC 6238's SHA-1 test vector (59 s, last 6 digits).'''

		secret = base64.b32encode(b'12345678901234567890').decode()
		self.assertEqual(twofactor.code_at(secret, 59 // 30), '287082')


	def test_totp_setup_and_sign_in(self):
		'''Setup needs the password and a matching code; sign-in then needs a code, which works once.'''

		self.assertEqual(self.me.post('/api/me/2fa/totp', json={'password': 'wrong'}).status_code, 403)
		begin = self.me.post('/api/me/2fa/totp', json={'password': PASSWORD}).json
		self.assertTrue(begin['uri'].startswith('otpauth://totp/kanopy%3Aann?secret='))
		code  = twofactor.code_at(begin['secret'], int(time.time()) // twofactor.STEP)
		wrong = f'{(int(code) + 500000) % 1000000:06d}'
		self.assertEqual(self.me.post('/api/me/2fa/totp/confirm', json={'code': wrong}).status_code, 400)
		self.assertFalse(self.me.get('/api/me/2fa').json['totp'])
		done = self.me.post('/api/me/2fa/totp/confirm', json={'code': code}).json
		self.assertEqual((done['totp'], len(done['recovery']), done['recovery_left']), (True, 10, 10))
		self.assertEqual(app.app.test_client().post('/api/login/2fa', json={'ticket': self.ticket(), 'code': code}).status_code, 403) # the setup code was already used
		step = int(time.time()) // twofactor.STEP + 1
		r    = app.app.test_client().post('/api/login/2fa', json={'ticket': self.ticket(), 'code': twofactor.code_at(begin['secret'], step)})
		self.assertEqual(r.status_code, 200)
		self.assertIn(app.COOKIE, r.headers['Set-Cookie'])


	def test_recovery_codes(self):
		'''A recovery code signs in once; regenerating replaces them all.'''

		secret, codes = self.totp_on()
		anon = app.app.test_client()
		self.assertEqual(anon.post('/api/login/2fa', json={'ticket': self.ticket(), 'code': codes[0].upper()}).status_code, 200)
		self.assertEqual(anon.post('/api/login/2fa', json={'ticket': self.ticket(), 'code': codes[0]}).status_code, 403)
		self.assertEqual(self.me.get('/api/me/2fa').json['recovery_left'], 9)
		fresh = self.me.post('/api/me/2fa/recovery', json={'password': PASSWORD}).json['recovery']
		self.assertEqual(anon.post('/api/login/2fa', json={'ticket': self.ticket(), 'code': codes[1]}).status_code, 403)
		self.assertEqual(anon.post('/api/login/2fa', json={'ticket': self.ticket(), 'code': fresh[1]}).status_code, 200)


	def test_ticket_limits(self):
		'''A ticket dies after five wrong codes, and a made-up ticket gets nothing.'''

		secret, codes = self.totp_on()
		ticket = self.ticket()
		for _ in range(twofactor.TICKET_TRIES):
			try:
				twofactor.ticket_finish(ticket, 'nope')
			except PermissionError:
				pass
		with self.assertRaises(PermissionError):
			twofactor.ticket_finish(ticket, codes[0])
		self.assertEqual(app.app.test_client().post('/api/login/2fa', json={'ticket': 'made-up', 'code': codes[0]}).status_code, 403)


	def test_turn_off_and_admin_reset(self):
		'''Turning the app off needs the password and drops the recovery codes; an admin can reset a locked-out user.'''

		self.totp_on()
		self.assertEqual(self.me.delete('/api/me/2fa/totp', json={'password': 'wrong'}).status_code, 403)
		off = self.me.delete('/api/me/2fa/totp', json={'password': PASSWORD}).json
		self.assertEqual((off['totp'], off['recovery_left']), (False, 0))
		self.assertEqual(app.app.test_client().post('/api/login', json={'username': 'ann', 'password': PASSWORD}).json, {'ok': True})
		self.totp_on()
		self.assertEqual(self.me.delete(f'/api/users/{self.root["id"]}/2fa').status_code, 403) # not an admin
		self.assertEqual(self.client.delete(f'/api/users/{self.ann["id"]}/2fa').json, {'ok': True})
		self.assertEqual(twofactor.methods(self.ann['id']), [])


	def test_tokens_cannot_touch_2fa(self):
		'''API tokens are refused on every 2FA route, and the MCP endpoint is unaffected by 2FA.'''

		self.totp_on()
		anon = app.app.test_client()
		self.assertEqual(anon.get('/api/me/2fa', headers={'X-Key': self.key}).status_code, 403)
		self.assertEqual(anon.delete('/api/me/2fa/totp', json={'password': PASSWORD}, headers={'X-Key': self.key}).status_code, 403)
		self.assertEqual(anon.get('/api/projects', headers={'X-Key': self.key}).status_code, 200)
		self.assertNotIn('error', self.rpc('tools/call', {'name': 'list_projects', 'arguments': {}}).json)


	def test_passkey_register_and_sign_in(self):
		'''A passkey registers through the real WebAuthn checks and signs in; a wrong origin or a removed key does not.'''

		key  = SoftKey()
		self.assertEqual(self.me.post('/api/me/2fa/passkeys/options', json={'password': 'wrong'}).status_code, 403)
		opts = self.me.post('/api/me/2fa/passkeys/options', json={'password': PASSWORD}).json
		self.assertEqual(opts['rp']['id'], 'localhost')
		done = self.me.post('/api/me/2fa/passkeys', json={'name': 'laptop', 'credential': key.create(opts)}).json
		self.assertEqual(([p['name'] for p in done['passkeys']], len(done['recovery'])), (['laptop'], 10))
		self.assertEqual(self.me.post('/api/me/2fa/passkeys', json={'name': 'again', 'credential': key.create(opts)}).status_code, 400) # the challenge is single-use

		anon   = app.app.test_client()
		ticket = self.ticket()
		bad    = anon.post('/api/login/2fa', json={'ticket': ticket, 'passkey': key.get(anon.post('/api/login/passkey', json={'ticket': ticket}).json, origin_ok=False)})
		self.assertEqual(bad.status_code, 403)
		r = anon.post('/api/login/2fa', json={'ticket': ticket, 'passkey': key.get(anon.post('/api/login/passkey', json={'ticket': ticket}).json)})
		self.assertEqual(r.status_code, 200)
		self.assertIsNotNone(self.me.get('/api/me/2fa').json['passkeys'][0]['used'])

		gone = self.me.delete(f'/api/me/2fa/passkeys/{done["passkeys"][0]["id"]}', json={'password': PASSWORD}).json
		self.assertEqual((gone['passkeys'], gone['recovery_left']), ([], 0))
		self.assertEqual(anon.post('/api/login', json={'username': 'ann', 'password': PASSWORD}).json, {'ok': True})
