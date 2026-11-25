#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# kanopy/twofactor.py

'''
Two-factor sign-in: authenticator-app codes (TOTP), passkeys (WebAuthn) and one-time recovery codes.
The password always comes first; a second factor is asked for only when the user turned one on. Every change here needs the current password.
'''

import base64
import hashlib
import hmac
import json
import secrets
import struct
import threading
import time
import urllib.parse

try:
	import webauthn
	from webauthn.helpers.structs import PublicKeyCredentialDescriptor
except ImportError:
	raise ImportError('missing webauthn library (pip install webauthn)')

from . import accounts
from . import kanban


RECOVERY_COUNT = 10
STEP           = 30  # seconds per authenticator code
TICKET_SECONDS = 300 # a signed-in-by-password ticket waits this long for its second factor
TICKET_TRIES   = 5   # wrong second factors before the ticket is dropped
TICKETS        = {}  # digest of ticket -> {'user': id, 'expires': monotonic, 'tries': n, 'challenge': bytes or None}
CHALLENGES     = {}  # user id -> (challenge, expires) for adding a passkey
LOCK           = threading.Lock()


def code_at(secret: str, counter: int) -> str:
	'''
	Return the 6-digit authenticator code for a base32 secret at a time step (RFC 6238, SHA-1).

	:param secret: Base32 secret
	:param counter: Time step number
	'''

	mac    = hmac.new(base64.b32decode(secret), struct.pack('>Q', counter), hashlib.sha1).digest()
	offset = mac[-1] & 15

	return f'{(struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7fffffff) % 1000000:06d}'


def code_match(secret: str, code: str, last: int) -> int:
	'''
	Return the time step a code matches (one step of clock drift either way), or 0. A step at or before last was already used.

	:param secret: Base32 secret
	:param code: Code as typed
	:param last: Last time step used
	'''

	code = ''.join(ch for ch in str(code or '') if ch.isdigit())
	now  = int(time.time()) // STEP

	if len(code) != 6:
		return 0

	return next((step for step in (now - 1, now, now + 1) if step > last and hmac.compare_digest(code_at(secret, step), code)), 0)


def need_password(db, user: dict, password: str):
	'''
	Raise PermissionError unless password is the user's current one.

	:param db: Open database connection
	:param user: User dict
	:param password: Password as typed
	'''

	row = db.execute('SELECT password FROM users WHERE id = ?', (user['id'],)).fetchone()

	if not accounts.password_ok(password or '', row['password']):
		raise PermissionError('current password is wrong')


def methods(user_id: int) -> list:
	'''
	Return the second factors a user can sign in with: any of totp, passkey and recovery. Empty means no second factor.

	:param user_id: User id
	'''

	with kanban.connect() as db:
		row  = db.execute("SELECT totp_secret != '' AS totp, (SELECT COUNT(*) FROM passkeys WHERE user_id = ?) AS n_keys, (SELECT COUNT(*) FROM recovery_codes WHERE user_id = ? AND used IS NULL) AS n_codes FROM users WHERE id = ?", (user_id, user_id, user_id)).fetchone()

	if not row or not (row['totp'] or row['n_keys']):
		return []

	return [name for name, on in (('totp', row['totp']), ('passkey', row['n_keys']), ('recovery', row['n_codes'])) if on]


def recovery_new(db, user_id: int) -> list:
	'''
	Replace a user's recovery codes with fresh ones; returns them, the only time they are shown.

	:param db: Open database connection
	:param user_id: User id
	'''

	codes = ['-'.join(secrets.token_hex(3)[i:i + 3] for i in (0, 3)) for _ in range(RECOVERY_COUNT)] # like 3fa-91c
	db.execute('DELETE FROM recovery_codes WHERE user_id = ?', (user_id,))
	db.executemany('INSERT INTO recovery_codes (user_id, code) VALUES (?, ?)', [(user_id, accounts.digest(code)) for code in codes])

	return codes


def status(user: dict) -> dict:
	'''
	Return a user's 2FA state: authenticator on, their passkeys, and unused recovery codes left.

	:param user: User dict
	'''

	with kanban.connect() as db:
		totp = db.execute("SELECT totp_secret != '' FROM users WHERE id = ?", (user['id'],)).fetchone()[0]
		keys = [dict(row) for row in db.execute('SELECT id, name, created, used FROM passkeys WHERE user_id = ? ORDER BY id', (user['id'],))]
		left = db.execute('SELECT COUNT(*) FROM recovery_codes WHERE user_id = ? AND used IS NULL', (user['id'],)).fetchone()[0]

	return {'totp': bool(totp), 'passkeys': keys, 'recovery_left': left}


def off_if_empty(db, user_id: int):
	'''
	Drop the recovery codes once a user has no second factor left, so they are not asked for one.

	:param db: Open database connection
	:param user_id: User id
	'''

	if not db.execute("SELECT 1 FROM users WHERE id = ? AND totp_secret != ''", (user_id,)).fetchone() and not db.execute('SELECT 1 FROM passkeys WHERE user_id = ?', (user_id,)).fetchone():
		db.execute('DELETE FROM recovery_codes WHERE user_id = ?', (user_id,))


def totp_begin(user: dict, password: str) -> dict:
	'''
	Start setting up an authenticator app: a new secret waits until a code from it is confirmed.

	:param user: User dict
	:param password: Current password
	'''

	secret = base64.b32encode(secrets.token_bytes(20)).decode()

	with kanban.connect() as db:
		need_password(db, user, password)
		db.execute('UPDATE users SET totp_pending = ? WHERE id = ?', (secret, user['id']))

	label = urllib.parse.quote(f'kanopy:{user["username"]}')

	return {'secret': secret, 'uri': f'otpauth://totp/{label}?secret={secret}&issuer=kanopy&digits=6&period={STEP}'}


def totp_confirm(user: dict, code: str) -> dict:
	'''
	Turn the authenticator app on once it shows a matching code; returns the status, plus recovery codes when this is the first second factor.

	:param user: User dict
	:param code: Code from the app
	'''

	with kanban.connect() as db:
		row = db.execute('SELECT totp_pending FROM users WHERE id = ?', (user['id'],)).fetchone()
		if not row['totp_pending']:
			raise ValueError('start setting up the authenticator app first')
		step = code_match(row['totp_pending'], code, 0)
		if not step:
			raise ValueError('that code does not match; check the time on your device and try the next one')
		first = not methods(user['id'])
		db.execute("UPDATE users SET totp_secret = totp_pending, totp_pending = '', totp_last = ? WHERE id = ?", (step, user['id']))
		codes = recovery_new(db, user['id']) if first else None

	return dict(status(user), recovery=codes)


def totp_disable(user: dict, password: str) -> dict:
	'''
	Turn the authenticator app off.

	:param user: User dict
	:param password: Current password
	'''

	with kanban.connect() as db:
		need_password(db, user, password)
		db.execute("UPDATE users SET totp_secret = '', totp_pending = '', totp_last = 0 WHERE id = ?", (user['id'],))
		off_if_empty(db, user['id'])

	return status(user)


def recovery_regenerate(user: dict, password: str) -> dict:
	'''
	Replace the recovery codes; returns the new ones.

	:param user: User dict
	:param password: Current password
	'''

	if not methods(user['id']):
		raise ValueError('turn on an authenticator app or a passkey first')

	with kanban.connect() as db:
		need_password(db, user, password)
		codes = recovery_new(db, user['id'])

	return dict(status(user), recovery=codes)


def passkey_options(user: dict, password: str, rp_id: str) -> dict:
	'''
	Return the browser options for adding a passkey.

	:param user: User dict
	:param password: Current password
	:param rp_id: The site's host name, which the passkey is bound to
	'''

	with kanban.connect() as db:
		need_password(db, user, password)
		have = [row['credential_id'] for row in db.execute('SELECT credential_id FROM passkeys WHERE user_id = ?', (user['id'],))]

	options = webauthn.generate_registration_options(rp_id=rp_id, rp_name='kanopy', user_name=user['username'], user_id=str(user['id']).encode(), exclude_credentials=[PublicKeyCredentialDescriptor(id=webauthn.base64url_to_bytes(c)) for c in have])

	with LOCK:
		CHALLENGES[user['id']] = (options.challenge, time.monotonic() + TICKET_SECONDS)

	return json.loads(webauthn.options_to_json(options))


def passkey_add(user: dict, name: str, credential: dict, rp_id: str, origin: str) -> dict:
	'''
	Store a passkey the browser just made; returns the status, plus recovery codes when this is the first second factor.

	:param user: User dict
	:param name: Label like "laptop"
	:param credential: The browser's credential, as JSON
	:param rp_id: The site's host name
	:param origin: The page's origin, like https://kanopy.example.com
	'''

	with LOCK:
		challenge, until = CHALLENGES.pop(user['id'], (None, 0))

	if not challenge or until < time.monotonic():
		raise ValueError('the passkey request expired; start again')

	try:
		done = webauthn.verify_registration_response(credential=credential, expected_challenge=challenge, expected_rp_id=rp_id, expected_origin=origin)
	except Exception as exc:
		raise ValueError(f'the passkey could not be verified: {exc}') from None

	with kanban.connect() as db:
		first = not methods(user['id'])
		db.execute('INSERT INTO passkeys (user_id, name, credential_id, public_key, sign_count, created) VALUES (?, ?, ?, ?, ?, ?)', (user['id'], (name or '').strip()[:40] or 'passkey', base64.urlsafe_b64encode(done.credential_id).rstrip(b'=').decode(), done.credential_public_key, done.sign_count, kanban.now()))
		codes = recovery_new(db, user['id']) if first else None

	return dict(status(user), recovery=codes)


def passkey_remove(user: dict, passkey_id: int, password: str) -> dict:
	'''
	Remove one of the user's passkeys.

	:param user: User dict
	:param passkey_id: Passkey id
	:param password: Current password
	'''

	with kanban.connect() as db:
		need_password(db, user, password)
		if not db.execute('DELETE FROM passkeys WHERE id = ? AND user_id = ?', (passkey_id, user['id'])).rowcount:
			raise LookupError('passkey not found')
		off_if_empty(db, user['id'])

	return status(user)


def reset(admin: dict, user_id: int):
	'''
	Turn off every second factor of a locked-out user (admin only).

	:param admin: Admin user
	:param user_id: User id
	'''

	accounts.need_admin(admin)

	with kanban.connect() as db:
		if not db.execute("UPDATE users SET totp_secret = '', totp_pending = '', totp_last = 0 WHERE id = ?", (user_id,)).rowcount:
			raise LookupError('user not found')
		db.execute('DELETE FROM passkeys WHERE user_id = ?', (user_id,))
		db.execute('DELETE FROM recovery_codes WHERE user_id = ?', (user_id,))


def ticket_new(user_id: int) -> str:
	'''
	Return a short-lived ticket for a user whose password was right and who still owes a second factor.

	:param user_id: User id
	'''

	ticket = secrets.token_urlsafe(32)
	now    = time.monotonic()

	with LOCK:
		for key in [key for key, t in TICKETS.items() if t['expires'] < now]:
			del TICKETS[key]
		TICKETS[accounts.digest(ticket)] = {'user': user_id, 'expires': now + TICKET_SECONDS, 'tries': 0, 'challenge': None}

	return ticket


def ticket_get(ticket: str) -> dict:
	'''
	Return a live ticket's entry, or raise PermissionError.

	:param ticket: Raw ticket
	'''

	entry = TICKETS.get(accounts.digest(str(ticket or '')))

	if not entry or entry['expires'] < time.monotonic():
		raise PermissionError('the sign-in expired; enter your password again')

	return entry


def ticket_passkey(ticket: str, rp_id: str) -> dict:
	'''
	Return the browser options for signing in with one of the ticket's user's passkeys.

	:param ticket: Raw ticket
	:param rp_id: The site's host name
	'''

	with LOCK:
		entry = ticket_get(ticket)

	with kanban.connect() as db:
		have = [row['credential_id'] for row in db.execute('SELECT credential_id FROM passkeys WHERE user_id = ?', (entry['user'],))]

	if not have:
		raise ValueError('no passkeys on this account')

	options = webauthn.generate_authentication_options(rp_id=rp_id, allow_credentials=[PublicKeyCredentialDescriptor(id=webauthn.base64url_to_bytes(c)) for c in have])

	with LOCK:
		entry['challenge'] = options.challenge

	return json.loads(webauthn.options_to_json(options))


def ticket_finish(ticket: str, code: str = None, passkey: dict = None, rp_id: str = None, origin: str = None) -> int:
	'''
	Check the second factor for a ticket; returns the user id to open a session for. A wrong one counts against the ticket.

	:param ticket: Raw ticket
	:param code: Authenticator code or recovery code
	:param passkey: The browser's passkey assertion, as JSON
	:param rp_id: The site's host name (passkeys)
	:param origin: The page's origin (passkeys)
	'''

	with LOCK:
		entry = ticket_get(ticket)
		if entry['tries'] >= TICKET_TRIES:
			TICKETS.pop(accounts.digest(ticket), None)
			raise PermissionError('too many wrong codes; enter your password again')
		entry['tries'] += 1

	user_id = entry['user']
	ok      = False

	with kanban.connect() as db:
		if passkey:
			ok = passkey_check(db, user_id, passkey, entry['challenge'], rp_id, origin)
		elif code:
			row  = db.execute('SELECT totp_secret, totp_last FROM users WHERE id = ?', (user_id,)).fetchone()
			step = code_match(row['totp_secret'], code, row['totp_last']) if row['totp_secret'] else 0
			if step:
				ok = bool(db.execute('UPDATE users SET totp_last = ? WHERE id = ? AND totp_last < ?', (step, user_id, step)).rowcount) # a code works once, even if two requests race
			else:
				clean = str(code).strip().lower()
				ok    = bool(db.execute('UPDATE recovery_codes SET used = ? WHERE user_id = ? AND code = ? AND used IS NULL', (kanban.now(), user_id, accounts.digest(clean))).rowcount)

	if not ok:
		raise PermissionError('that code is not right' if not passkey else 'the passkey was not accepted')

	with LOCK:
		TICKETS.pop(accounts.digest(ticket), None)

	return user_id


def passkey_check(db, user_id: int, credential: dict, challenge: bytes, rp_id: str, origin: str) -> bool:
	'''
	Verify a passkey assertion against the user's stored passkeys, and record its use.

	:param db: Open database connection
	:param user_id: User id
	:param credential: The browser's assertion, as JSON
	:param challenge: The challenge the ticket handed out
	:param rp_id: The site's host name
	:param origin: The page's origin
	'''

	if not challenge or not isinstance(credential, dict):
		return False

	row = db.execute('SELECT id, public_key, sign_count FROM passkeys WHERE user_id = ? AND credential_id = ?', (user_id, str(credential.get('id', '')))).fetchone()

	if not row:
		return False

	try:
		done = webauthn.verify_authentication_response(credential=credential, expected_challenge=challenge, expected_rp_id=rp_id, expected_origin=origin, credential_public_key=bytes(row['public_key']), credential_current_sign_count=row['sign_count'])
	except Exception:
		return False

	db.execute('UPDATE passkeys SET sign_count = ?, used = ? WHERE id = ?', (done.new_sign_count, kanban.now(), row['id']))

	return True
