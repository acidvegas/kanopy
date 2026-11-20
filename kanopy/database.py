#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# kanopy/database.py

import contextlib
import queue
import re
import sqlite3


ID_TABLES = {'projects', 'tasks', 'attachments', 'labels', 'comments', 'users', 'tokens', 'invites', 'teams', 'shares', 'task_events', 'notifications', 'trash'} # tables with an id primary key, so an INSERT can report it
POOL_SIZE = 8 # idle PostgreSQL connections kept per database
POOLS     = {} # postgres url -> queue of idle connections


class Connection:
	'''
	One database connection with the sqlite3 interface kanban.py is written against: execute() with ? placeholders, rows readable by name or index, lastrowid, and use as a context manager that commits or rolls back.
	PostgreSQL connections are translated on the fly and go back to a pool when the object is dropped.
	'''

	def __init__(self, target: str):
		'''
		Open a connection.

		:param target: SQLite file path, or a postgresql:// URL
		'''

		self.target   = str(target)
		self.postgres = is_postgres(self.target)

		if self.postgres:
			try:
				self.raw = POOLS.setdefault(self.target, queue.Queue()).get_nowait()
			except queue.Empty:
				self.raw = pg_connect(self.target)
		else:
			self.raw = sqlite3.connect(self.target, timeout=10)
			self.raw.row_factory = sqlite3.Row
			self.raw.execute('PRAGMA foreign_keys = ON')
			self.raw.create_function('LOWER', 1, lambda v: v.lower() if isinstance(v, str) else v, deterministic=True) # SQLite's own LOWER only knows A-Z; search lowercases with Python


	def __del__(self):
		'''Return a PostgreSQL connection to the pool, or close it.'''

		raw = getattr(self, 'raw', None)

		if raw is None:
			return

		if self.postgres and not raw.closed:
			raw.rollback() # anything left uncommitted belongs to nobody now
			pool = POOLS.setdefault(self.target, queue.Queue())
			if pool.qsize() < POOL_SIZE:
				pool.put(raw)
				return

		raw.close()


	def __enter__(self):
		return self


	def __exit__(self, kind, value, trace):
		if kind:
			self.raw.rollback()
		else:
			self.raw.commit()

		return False


	def execute(self, sql: str, params: tuple = ()) -> 'Cursor':
		'''
		Run one statement.

		:param sql: SQL written for SQLite
		:param params: Positional parameters
		'''

		check(params)

		if not self.postgres:
			with duplicates():
				cur = self.raw.execute(sql, params)
			return Cursor(cur, cur.lastrowid, self)

		sql, returning = translate(sql)
		cur            = self.raw.cursor()
		with duplicates():
			cur.execute(sql, tuple(params) if params else None)
		row  = cur.fetchone() if returning and cur.description else None # no row when ON CONFLICT DO NOTHING skipped the insert
		last = row[0] if row else None

		return Cursor(cur, last, self)


	def executemany(self, sql: str, rows: list) -> 'Cursor':
		'''
		Run one statement for each parameter row.

		:param sql: SQL written for SQLite
		:param rows: Parameter tuples
		'''

		rows = list(rows)
		for row in rows:
			check(row)

		if not self.postgres:
			with duplicates():
				return Cursor(self.raw.executemany(sql, rows), None, self)

		sql, returning = translate(sql)
		cur            = self.raw.cursor()
		with duplicates():
			cur.executemany(re.sub(r' RETURNING id$', '', sql) if returning else sql, rows)

		return Cursor(cur, None, self)


	def executescript(self, script: str):
		'''
		Run a script of statements without parameters.

		:param script: SQL statements separated by semicolons
		'''

		if not self.postgres:
			return self.raw.executescript(script)

		self.raw.execute(script)


	def commit(self):
		'''Commit the open transaction.'''

		self.raw.commit()


class Cursor:
	'''The parts of a sqlite3 cursor kanban.py uses, for either backend.'''

	def __init__(self, cur, lastrowid: int, owner: Connection):
		self.cur       = cur
		self.lastrowid = lastrowid
		self.owner     = owner # keeps the connection open while its results are read


	def __iter__(self):
		return iter(self.fetchall())


	@property
	def rowcount(self) -> int:
		return self.cur.rowcount


	def fetchall(self) -> list:
		return self.cur.fetchall() if self.cur.description else []


	def fetchone(self):
		return self.cur.fetchone() if self.cur.description else None


	def fetchmany(self, size: int) -> list:
		return self.cur.fetchmany(size) if self.cur.description else []


class Row(tuple):
	'''A result row readable by column name or index, like sqlite3.Row.'''

	def __new__(cls, values: tuple, names: tuple):
		row       = super().__new__(cls, values)
		row.names = names
		return row


	def __getitem__(self, key):
		return super().__getitem__(self.names.index(key) if isinstance(key, str) else key)


	def keys(self) -> list:
		return list(self.names)


def columns(db: Connection, table: str) -> list:
	'''
	Return a table's column names.

	:param db: Open connection
	:param table: Table name
	'''

	if db.postgres:
		return [r[0] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ? ORDER BY ordinal_position", (table,))]

	return [r['name'] for r in db.execute(f'PRAGMA table_info({table})')]


def is_postgres(target: str) -> bool:
	'''
	Say whether a database target is a PostgreSQL URL.

	:param target: SQLite path or URL
	'''

	return str(target).startswith(('postgresql://', 'postgres://'))


def pg_connect(url: str):
	'''
	Open a new PostgreSQL connection that returns Row results.

	:param url: postgresql:// URL
	'''

	try:
		import psycopg
	except ImportError:
		raise ImportError('PostgreSQL needs the psycopg library (pip install "psycopg[binary]")')

	def rows(cursor):
		names = tuple(c.name for c in cursor.description or ())
		return lambda values: Row(values, names)

	return psycopg.connect(url, row_factory=rows)


def schema(script: str, postgres: bool) -> str:
	'''
	Return the schema script for a backend; the source is written for SQLite.

	:param script: CREATE statements in SQLite form
	:param postgres: Translate for PostgreSQL
	'''

	if not postgres:
		return script

	script = script.replace('INTEGER PRIMARY KEY,', 'INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,')
	script = re.sub(r'\bBLOB\b', 'BYTEA', script)
	script = re.sub(r'\bREAL\b', 'DOUBLE PRECISION', script)

	return script


class Duplicate(ValueError):
	'''A write hit a unique rule, like two people creating the same name at once.'''


@contextlib.contextmanager
def duplicates():
	'''Turn either backend's unique-constraint error into Duplicate.'''

	try:
		yield
	except sqlite3.IntegrityError as exc:
		if 'UNIQUE' not in str(exc):
			raise
		raise Duplicate('that already exists') from exc
	except Exception as exc:
		if type(exc).__name__ != 'UniqueViolation': # psycopg.errors.UniqueViolation, without importing psycopg for SQLite boards
			raise
		raise Duplicate('that already exists') from exc


def check(params):
	'''
	Raise TypeError for a value no column holds (a list or object from request JSON) and ValueError for a whole number past 64 bits, so bad input is a 400 on both backends instead of a database error.

	:param params: Positional parameters
	'''

	for value in params or ():
		if isinstance(value, int) and not -2**63 <= value < 2**63:
			raise ValueError('number out of range')
		if value is not None and not isinstance(value, (str, int, float, bytes, memoryview)):
			raise TypeError(f'expected text or a number, not {type(value).__name__}')


def translate(sql: str) -> tuple:
	'''
	Rewrite one SQLite statement for PostgreSQL; returns (sql, adds RETURNING id).

	:param sql: Statement with ? placeholders
	'''

	if sql.strip() == 'BEGIN IMMEDIATE': # the critical sections that took SQLite's write lock serialize on one advisory lock instead
		return 'SELECT pg_advisory_xact_lock(7243)', False

	ignore = bool(re.match(r'\s*INSERT OR IGNORE\b', sql))
	sql    = re.sub(r'^\s*INSERT OR IGNORE\b', 'INSERT', sql)
	sql    = sql.replace('%', '%%')
	sql    = re.sub(r'\bIS NOT \?', 'IS DISTINCT FROM ?', sql)
	sql    = re.sub(r'\bIS \?', 'IS NOT DISTINCT FROM ?', sql)
	sql    = sql.replace('?', '%s')

	if ignore:
		sql += ' ON CONFLICT DO NOTHING'

	table     = re.match(r'\s*INSERT INTO (\w+)', sql)
	returning = bool(table and table[1] in ID_TABLES)

	if returning:
		sql += ' RETURNING id'

	return sql, returning
