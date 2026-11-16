#!/usr/bin/env python3
# kanopy - Developed by acidvegas in Python (https://github.com/acidvegas)
# kanopy/database.py

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

		if not self.postgres:
			cur = self.raw.execute(sql, params)
			return Cursor(cur, cur.lastrowid)

		sql, returning = translate(sql)
		cur            = self.raw.cursor()
		cur.execute(sql, tuple(params) if params else None)
		row  = cur.fetchone() if returning and cur.description else None # no row when ON CONFLICT DO NOTHING skipped the insert
		last = row[0] if row else None

		return Cursor(cur, last)


	def executemany(self, sql: str, rows: list) -> 'Cursor':
		'''
		Run one statement for each parameter row.

		:param sql: SQL written for SQLite
		:param rows: Parameter tuples
		'''

		if not self.postgres:
			return Cursor(self.raw.executemany(sql, rows), None)

		sql, returning = translate(sql)
		cur            = self.raw.cursor()
		cur.executemany(re.sub(r' RETURNING id$', '', sql) if returning else sql, list(rows))

		return Cursor(cur, None)


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

	def __init__(self, cur, lastrowid: int):
		self.cur       = cur
		self.lastrowid = lastrowid


	def __iter__(self):
		return iter(self.fetchall())


	@property
	def rowcount(self) -> int:
		return self.cur.rowcount


	def fetchall(self) -> list:
		return self.cur.fetchall() if self.cur.description else []


	def fetchone(self):
		return self.cur.fetchone() if self.cur.description else None


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
